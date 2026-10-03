"""启动入口与报告流程。在new目录运行：python -B main.py --help。"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime
from pathlib import Path

sys.dont_write_bytecode = True
if sys.version_info < (3, 11):
    raise SystemExit("本项目需要 Python 3.11+（本机验证 3.14.6）。")

import pymupdf

from materials import (ROOT, TZ, Run, fetch, import_legacy, load_materials, register,
                       verify_registry, write_csv, write_json)
from extract import extract_material
from finance import analyze, check_claim, decimal
from llm_check import LLMClient, LLMError, check_text

def extract_selected(root: Path, run: Run, code=None, year=None, render=False):
    """code/year 既可传单值（CLI），也可传集合（测试批量圈定）。"""
    registry_path = root / "data" / "agent" / "materials.jsonl"
    if registry_path.exists():
        run.read(registry_path)

    def match(m):
        ok_code = (code is None if code is None else
                   m["company_code"] == code if isinstance(code, str) else
                   m["company_code"] in code)
        ok_year = (year is None if year is None else
                   m["report_year"] == year if isinstance(year, int) else
                   m["report_year"] in year)
        return ok_code and ok_year

    materials = [m for m in load_materials(root) if match(m)]
    if not materials:
        raise ValueError("没有匹配的已登记材料，请先 import-existing 或 fetch")
    facts, failures = [], []
    for material in materials:
        try:
            found = extract_material(root, material, run)
            facts.extend(found)
            if render:
                with pymupdf.open(root / material["local_file"]) as doc:
                    for page in sorted({f["page"] for f in found}):
                        name = f"{material['company_code']}_{material['report_year']}_{material['sha256'][:8]}_p{page}.png"
                        doc[page - 1].get_pixmap(matrix=pymupdf.Matrix(1.6, 1.6)).save(run.output(name))
        except Exception as exc:
            failure = {"document_id": material["document_id"],
                       "error_type": type(exc).__name__, "message": str(exc)}
            failures.append(failure)
            run.event("extraction_failed", **failure)
    write_json(run.output("evidence.json"), facts)
    write_json(run.output("failures.json"), failures)
    write_csv(run.output("evidence.csv"), facts, [
        "evidence_id", "company_code", "company_name", "report_year", "period_year",
        "period_start", "period_end", "period_kind", "metric", "metric_name", "value",
        "unit", "normalized_value", "normalized_unit", "currency", "scope", "adjustment",
        "page", "source_file", "source_sha256", "announcement_id", "source_url",
    ])
    return materials, facts, failures


def report_markdown(analysis: dict, materials: list[dict], checks: list[dict] | None = None) -> str:
    lines = ["# 年度财务证据与分析", "", analysis["basis"], "", analysis["limits"], "",
             "金额单位为人民币元；PDF页码从文件第一页开始计数，可能与印刷页码不同。", "",
             "同比核对按年报披露的小数位数使用 ROUND_HALF_UP；一致只表示报告内部算术一致。", "",
             "适用范围：当前已登记材料中的4类指标；24条固定参考答案尚待团队人工复签，"
             "新公司、新措辞和新版式的泛化能力尚未独立验证。", "",
             "| 公司 | 报告年度 | 指标 | 本年 | 同报告上年 | 基期状态 | 复算同比（核对精度） | 年报披露同比 | 核对结果 | PDF页 |",
             "|---|---:|---|---:|---:|---|---|---|---|---:|"]
    for row in analysis["rows"]:
        calc = row["yoy"]
        rate = format(decimal(calc["value"]), ".4f") + "%" if calc["status"] == "ok" else calc["status"]
        check = row.get("reported_yoy_check")
        disclosed = row.get("reported_yoy")
        reported = str(disclosed["value"]) + "%" if disclosed else "未提取到"
        outcome = "未核对（缺少披露值）" if calc["status"] == "ok" else f"未核对（{calc['status']}）"
        if check:
            rate = str(check["calculated_rounded"]) + "%"
            reported = str(check["reported"]) + "%"
            outcome = {"match": "一致", "mismatch": "不一致，需复核"}.get(check["status"], "未核对")
        lines.append(f"| {row['company_name']} | {row['report_year']} | {row['metric_name']} | "
                     f"{row['current']} | {row['previous']} | {row['previous_adjustment']} | "
                     f"{rate} | {reported} | {outcome} | {row['page']} |")
    lines += ["", "## 辅助分析", ""]
    for signal in analysis["signals"]:
        lines += [f"- {signal['document_id'].split('_')[0]}：{signal['description']} "
                  f"{signal.get('value', '')}{signal.get('unit', '')}。{signal['interpretation']}"]
    if not analysis["signals"]:
        lines += ["没有产生可确认的辅助分析项。"]
    if checks is not None:
        lines += ["", "## 结构化样例核对", "",
                  "这些待核查字段已在样例JSON中人工填写。程序尚未自动理解自然语言，没有接入大模型。", "",
                  "| 原句 | 结果 | 原因或建议 |", "|---|---|---|"]
        for check in checks:
            lines.append(f"| {check['original_sentence']} | {check['status']} | "
                         f"{check.get('suggestion') or check.get('reason', '')} |")
    lines += ["", "## 来源与待复核事项", ""]
    for material in materials:
        lines += [f"- {material['company_name']} {material['report_year']}年报："
                  f"[原始PDF](../{material['local_file']})；公告ID {material['announcement_id']}；"
                  f"SHA-256 {material['sha256']}。",
                  f"  披露日期状态：{material['disclosure_date_status']}；"
                  f"版本策略：{material['version_policy']}；需复核最新有效版本："
                  f"{'是' if material['needs_version_review'] else '否（仅指本次候选未发现冲突）'}。"]
    lines += ["", "精确单元格、年份表头、单位币种依据、作用域依据及计算操作数见同目录JSON和events.jsonl。", ""]
    return "\n".join(lines)


def evaluate_gold(facts: list[dict], gold: dict) -> dict:
    results = []
    keys = ("company_code", "report_year", "period_year", "metric", "adjustment")
    fingerprints = {(s["company_code"], s["report_year"]): s["sha256"]
                    for s in gold["source_pages"]}
    for expected in gold["records"]:
        matches = [f for f in facts if all(f[k] == expected[k] for k in keys)]
        errors = {}
        if len(matches) != 1:
            errors["record_count"] = {"expected": 1, "actual": len(matches)}
        else:
            actual = matches[0]
            if actual["source_sha256"] != fingerprints[(expected["company_code"], expected["report_year"])]:
                errors["source_sha256"] = {"expected": "参考答案对应原始文件", "actual": actual["source_sha256"]}
            for field, value in expected.items():
                if field in keys:
                    continue
                equal = (decimal(actual.get(field)) == decimal(value)) if field == "value" else actual.get(field) == value
                if not equal:
                    errors[field] = {"expected": value, "actual": actual.get(field)}
        results.append({"key": {k: expected[k] for k in keys},
                        "status": "FAIL" if errors else "PASS", "errors": errors})
    return {"reference_status": gold["review_status"], "checked": len(results),
            "passed": sum(r["status"] == "PASS" for r in results), "results": results}


def parser() -> argparse.ArgumentParser:
    app = argparse.ArgumentParser(description="第一条线：材料登记 → 财务证据 → 确定性分析")
    commands = app.add_subparsers(dest="command", required=True)
    importer = commands.add_parser("import-existing", help="复制并验证旧项目9份材料，不修改旧工程")
    importer.add_argument("--source", type=Path, required=True)
    downloader = commands.add_parser("fetch", help="真实下载一个公司、一个年份")
    downloader.add_argument("--code", required=True)
    downloader.add_argument("--year", type=int, required=True)
    downloader.add_argument("--as-of", type=date.fromisoformat, default=datetime.now(TZ).date())
    downloader.add_argument("--policy", choices=["first", "latest"], default="first")
    downloader.add_argument("--proxy", help="可选本地代理；不关闭TLS证书验证")
    commands.add_parser("verify", help="验证来源清单、PDF指纹与公司年份")
    for name in ["extract", "analyze"]:
        command = commands.add_parser(name)
        command.add_argument("--code")
        command.add_argument("--year", type=int)
        command.add_argument("--render", action="store_true", help="渲染证据所在页供复核")
    demo = commands.add_parser("demo", help="贵州茅台2024单报告示例（8条结构化陈述）")
    demo.add_argument("--render", action="store_true")
    live = commands.add_parser("live", help="决赛现场一键：登记/抽取/分析/出报告")
    live.add_argument("--pdf", type=Path, help="现场新材料 PDF；需同时给 --code --name --year")
    live.add_argument("--code", help="证券代码（已登记材料可只给 code/year）")
    live.add_argument("--name", help="公司简称（仅 --pdf 时必填）")
    live.add_argument("--year", type=int, help="报告年度（--pdf 时必填）")
    live.add_argument("--render", action="store_true", help="渲染证据所在页 PNG")
    commands.add_parser("check-gold", help="比较固定参考答案；不自动生成或修改参考答案")
    checker = commands.add_parser("check-text", help="模型拆解自然语言草稿，再由Python规则逐项核查")
    checker.add_argument("--file", type=Path, required=True, help="UTF-8纯草稿文件，不要选含凭证的接入说明")
    checker.add_argument("--base-url", help="兼容Chat Completions的HTTPS Base URL；也可设置LLM_BASE_URL")
    checker.add_argument("--model", help="模型名；也可设置LLM_MODEL")
    checker.add_argument("--format", choices=["json_schema", "json_object"], help="默认读取LLM_FORMAT，否则使用json_schema")
    checker.add_argument("--ask-key", action="store_true", help="交互输入临时密钥，不回显、不保存；否则读取LLM_API_KEY")
    return app


def main() -> int:
    args = parser().parse_args()
    parameters = {k: str(v) if isinstance(v, (Path, date)) else v
                  for k, v in vars(args).items() if k != "proxy"}
    parameters["proxy_configured"] = bool(getattr(args, "proxy", None))
    client = None
    if args.command == "check-text":
        try:
            client = LLMClient.from_environment(base_url=args.base_url, model=args.model,
                                                mode=args.format, ask_key=args.ask_key)
        except LLMError as exc:
            print(f"配置未就绪：{exc}")
            return 1
        # 不将环境变量或认证信息序列化到run.json。
        parameters = {"command": "check-text", "file": str(args.file), "model": client.model,
                      "provider_host": client.host, "response_format": client.mode}
    run = Run(ROOT, args.command, parameters)
    print(f"运行记录：{run.folder}", flush=True)
    try:
        if args.command == "import-existing":
            records, failures = import_legacy(ROOT, args.source, run)
            write_json(run.output("failures.json"), failures)
            run.finish(status="partial_failure" if failures else "ok",
                       imported=len(records), failures=len(failures))
            print(f"材料：成功 {len(records)}，失败 {len(failures)}")
            return int(bool(failures))
        if args.command == "fetch":
            record = fetch(ROOT, run, args.code, args.year, args.policy, args.as_of, args.proxy)
            write_json(run.output("selected_material.json"), record)
            run.finish(status="ok", document_id=record["document_id"],
                       needs_version_review=record["needs_version_review"])
            print(f"已保存：{record['local_file']}")
            return 0
        if args.command == "verify":
            results = verify_registry(ROOT, run)
            write_json(run.output("verification.json"), results)
            passed = sum(r["status"] == "PASS" for r in results)
            failed = passed != len(results) or not results
            run.finish(status="failed" if failed else "ok", checked=len(results), passed=passed)
            print(f"材料核验：{passed}/{len(results)}")
            return int(failed)
        if args.command == "live" and args.pdf:
            if not (args.code and args.name and args.year):
                raise ValueError("现场新材料必须同时给 --pdf --code --name --year")
            blob = args.pdf.read_bytes()
            stamp = datetime.now(TZ).strftime("%Y%m%d%H%M%S")
            record = register(ROOT, blob, {
                "company_code": args.code, "company_name": args.name,
                "report_year": int(args.year), "announcement_id": stamp,
                "title": f"{args.name}{args.year}年年度报告（现场登记）",
                "disclosed_at": datetime.now(TZ).date().isoformat(),
                "disclosure_date_status": "onsite_unverified",
                "source_url": f"onsite://{args.pdf.name}",
                "version_policy": "first", "license_status": "public_disclosure",
            }, run)
            print(f"已登记现场材料：{record['document_id']}")
        code = "600519" if args.command == "demo" else getattr(args, "code", None)
        year = 2024 if args.command == "demo" else getattr(args, "year", None)
        if args.command == "live":
            code = args.code
            year = args.year
        materials, facts, failures = extract_selected(ROOT, run, code, year, getattr(args, "render", False))
        if args.command == "check-text":
            if failures:
                raise LLMError("材料抽取不完整，本次未调用模型；请先检查failures.json")
            bundle = check_text(args.file, facts, run, client)
            run.finish(status="ok", materials=len(materials), evidence_count=len(facts),
                       checks=len(bundle["checks"]), counts=bundle["counts"])
            print(f"已完成 {len(bundle['checks'])} 项草稿核查；报告：{run.folder / 'text_report.md'}")
            return 0
        if args.command == "check-gold":
            gold_path = ROOT / "data" / "agent" / "samples.json"
            gold = json.loads(run.read(gold_path).decode("utf-8"))["reference_gold"]
            evaluation = evaluate_gold(facts, gold)
            write_json(run.output("gold_evaluation.json"), evaluation)
            failed = bool(failures) or evaluation["passed"] != evaluation["checked"]
            run.finish(status="failed" if failed else "ok", checked=evaluation["checked"],
                       passed=evaluation["passed"], failures=len(failures))
            print(f"固定参考答案：{evaluation['passed']}/{evaluation['checked']}")
            return int(failed)
        checks = None
        if args.command in {"analyze", "demo"}:
            analysis = analyze(facts, run)
            write_json(run.output("analysis.json"), analysis)
            if args.command == "demo":
                sample = json.loads(run.read(ROOT / "data" / "agent" / "samples.json").decode("utf-8"))
                checks = [check_claim(claim, facts) for claim in sample["claims"]]
                write_json(run.output("sample_checks.json"), checks)
                for check in checks:
                    run.event("structured_sample_check", **check)
            run.output("demo_report.md" if args.command == "demo" else "report.md").write_text(
                report_markdown(analysis, materials, checks), encoding="utf-8")
        if args.command == "live":
            analysis = analyze(facts, run)
            write_json(run.output("analysis.json"), analysis)
            issues = [f for f in facts if f.get("issues")]
            report = report_markdown(analysis, materials, None)
            report += "\n\n## 现场运行摘要\n\n"
            report += f"- 材料 {len(materials)} 份；证据 **{len(facts)}** 条；待复核字段 {len(issues)} 条。\n"
            report += f"- 运行目录：`{run.folder}`（含 run.json / evidence.json / events.jsonl）。\n"
            report += "- 下一步：`python scripts/site_build.py` 打包审计台；草稿核查用 `check-text`。\n"
            run.output("live_report.md").write_text(report, encoding="utf-8")
            print(f"现场报告：{run.folder / 'live_report.md'}（证据 {len(facts)} 条，待复核 {len(issues)}）")
        run.finish(status="partial_failure" if failures else "ok",
                   materials=len(materials), evidence_count=len(facts), failures=len(failures),
                   records_needing_review=sum(bool(f["issues"]) for f in facts))
        print(f"证据 {len(facts)} 条；失败 {len(failures)} 份；待复核字段 {sum(bool(f['issues']) for f in facts)} 条")
        return int(bool(failures))
    except Exception as exc:
        message = str(exc)
        if args.command == "check-text":
            message = str(exc) if isinstance(exc, LLMError) else "草稿读取或核查流程异常，请检查文件和运行环境"
            write_json(run.output("text_checks.json"), {
                "run_id": run.id, "status": "failed", "reason": message, "checks": []})
            run.output("text_report.md").write_text(
                f"# 草稿核查未完成\n\n运行：{run.id}\n\n{message}\n\n本次未生成财务判定。\n", encoding="utf-8")
        run.event("command_failed", error_type=type(exc).__name__, message=message)
        write_json(run.output("failures.json"), [{"error_type": type(exc).__name__, "message": message}])
        run.finish(status="failed", error_type=type(exc).__name__, message=message)
        print(f"失败：{type(exc).__name__}: {message}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
