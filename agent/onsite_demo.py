"""P4 现场可运行性：自检、离线 CLI 演示与 Web 启动。"""
from __future__ import annotations

import json
import subprocess
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Callable, Sequence


ROOT = Path(__file__).resolve().parents[1]

REQUIRED_PATHS = (
    "agent/main.py",
    "agent/webui.py",
    "data/agent/materials.jsonl",
    "data/agent/samples.json",
    "data/demo/audit_claims.json",
    "data/demo/sources.json",
    "docs/运行说明.md",
    "docs/第三方与来源清单.md",
    "requirements.txt",
    "web/index.html",
)

CLAIM_OUTCOME_MAP = {
    "证据支持": "supported",
    "确认错误": "wrong_value",
    "证据不足": "missing_evidence",
    "口径冲突／需人工复核": "scope_conflict",
}

REQUIRED_CLAIM_OUTCOMES = {
    "supported",
    "wrong_value",
    "missing_evidence",
    "scope_conflict",
}

FAILURE_PLAYBOOK = (
    {
        "id": "network_failure",
        "title": "现场网络失败",
        "trigger": "断开网络或把 LLM_BASE_URL 指向不可达地址后提交草稿核查。",
        "visible_signal": "任务进入 failed/partial_failure，页面显示失败原因，已完成证据和中间结果仍保留。",
        "recovery": "切换离线 demo/audit 路径，或恢复网络后使用已保存运行记录重试。",
        "offline_only": True,
    },
    {
        "id": "model_timeout",
        "title": "模型超时",
        "trigger": "把 backend_config.json 的 http.timeout_seconds 临时调低，再请求慢响应模型。",
        "visible_signal": "运行状态标明超时，未完成分块写入检查点，不生成未经验证的最终结论。",
        "recovery": "提高超时或改用离线确定性核查，并从检查点恢复已完成分块。",
        "offline_only": True,
    },
    {
        "id": "material_format_error",
        "title": "材料格式异常",
        "trigger": "上传扩展名为 .pdf、内容不是 PDF 的样例文件。",
        "visible_signal": "程序拒绝读取并显示材料格式错误，不把空结果包装成成功。",
        "recovery": "在页面使用内置合法 PDF 样例，或要求提供可解析的材料。",
        "offline_only": True,
    },
)


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _count_demo_pdfs(root: Path) -> int:
    folder = root / "data" / "demo"
    if not folder.exists():
        return 0
    return sum(1 for path in folder.rglob("*.pdf") if path.is_file())


def inspect_bundle(root: Path | str = ROOT) -> dict:
    """校验现场演示依赖的样例、入口、说明和四类主张。"""
    root = Path(root).resolve()
    checks = []
    problems = []
    for relative in REQUIRED_PATHS:
        path = root / relative
        exists = path.is_file()
        checks.append({"path": relative, "exists": exists})
        if not exists:
            problems.append(f"缺少文件：{relative}")

    samples = _load_json(root / "data" / "agent" / "samples.json")
    claims = samples.get("claims") or []
    expected = samples.get("expected_results") or {}
    outcomes = {name: 0 for name in sorted(REQUIRED_CLAIM_OUTCOMES)}
    unknown = []
    for claim in claims:
        raw = expected.get(str(claim.get("id")))
        category = CLAIM_OUTCOME_MAP.get(str(raw))
        if category is None:
            unknown.append(str(claim.get("id")))
        else:
            outcomes[category] += 1
    if unknown:
        problems.append("未识别的样例结论：" + ", ".join(unknown))
    missing_outcomes = sorted(REQUIRED_CLAIM_OUTCOMES - {key for key, value in outcomes.items() if value})
    if missing_outcomes:
        problems.append("样例未覆盖：" + ", ".join(missing_outcomes))

    audit_path = root / "data" / "demo" / "audit_claims.json"
    audit_claims = _load_json(audit_path) if audit_path.is_file() else []
    if len(audit_claims) < 3:
        problems.append("补充审计样例少于 3 条")

    material_count = _count_demo_pdfs(root)
    if material_count < 5:
        problems.append("内置 PDF 样例少于 5 份")

    return {
        "ready": not problems,
        "problems": problems,
        "checks": checks,
        "claim_count": len(claims),
        "claim_outcomes": outcomes,
        "audit_claim_count": len(audit_claims),
        "material_count": material_count,
        "failure_scenarios": [item["id"] for item in FAILURE_PLAYBOOK],
        "license_status": "第三方与来源许可状态见 docs/第三方与来源清单.md；整体 LICENSE 未由团队拍板",
    }


def failure_playbook() -> list[dict]:
    """返回不发起真实网络请求的现场故障演练口径。"""
    return [dict(item) for item in FAILURE_PLAYBOOK]


def cli_demo_command(
    python: str,
    root: Path | str,
    run_root: Path | str,
) -> list[str]:
    """构造隔离结果目录的离线 CLI 演示命令。"""
    root = Path(root).resolve()
    run_root = Path(run_root).resolve()
    return [
        str(python),
        "-B",
        "agent/main.py",
        "--run-root",
        str(run_root),
        "demo",
        "--render",
    ]


def run_cli_demo(
    root: Path | str,
    *,
    python: str,
    run_root: Path | str,
    runner: Callable[..., subprocess.CompletedProcess] = subprocess.run,
) -> int:
    """执行离线 CLI 演示；结果写入隔离目录，不覆盖仓库 results。"""
    root = Path(root).resolve()
    command = cli_demo_command(python, root, run_root)
    completed = runner(
        command,
        cwd=root,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
    )
    if completed.stdout:
        print(completed.stdout.rstrip())
    if completed.returncode:
        message = (completed.stderr or completed.stdout or "未知错误").strip()
        print(f"CLI 离线演示失败：{message}")
        return int(completed.returncode)
    print("CLI 离线演示：PASS")
    return 0


def _handler():
    try:
        from webui_http import Handler
    except ModuleNotFoundError:
        from agent.webui_http import Handler
    return Handler


def serve_web(
    host: str,
    port: int,
    *,
    server_factory: Callable[..., ThreadingHTTPServer] = ThreadingHTTPServer,
) -> int:
    """启动本地 Web；调用方负责在终端中按 Ctrl+C 停止。"""
    server = server_factory((host, port), _handler())
    print(f"Web 已启动：http://{host}:{port}/app/")
    print(f"兼容页面：http://{host}:{port}/")
    print("Ctrl+C 停止。")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")
    finally:
        server.server_close()
    return 0


def run_launcher(
    *,
    mode: str,
    host: str,
    port: int,
    root: Path | str = ROOT,
    python: str,
    run_root: Path | str,
    runner: Callable[..., subprocess.CompletedProcess] = subprocess.run,
    server_factory: Callable[..., ThreadingHTTPServer] = ThreadingHTTPServer,
) -> int:
    """执行现场自检、CLI 演示和/或 Web 启动。"""
    root = Path(root).resolve()
    report = inspect_bundle(root)
    print("P4 现场自检：" + ("READY" if report["ready"] else "BLOCKED"))
    print(
        f"样例 {report['claim_count']} 条；PDF {report['material_count']} 份；"
        f"补充审计 {report['audit_claim_count']} 条"
    )
    for drill in failure_playbook():
        print(f"故障演练 {drill['id']}：{drill['title']} -> {drill['visible_signal']}")
    if not report["ready"]:
        for problem in report["problems"]:
            print(f"- {problem}")
        return 1
    if mode == "check":
        return 0
    if mode in {"cli", "both"}:
        code = run_cli_demo(
            root,
            python=python,
            run_root=run_root,
            runner=runner,
        )
        if code:
            return code
    if mode == "cli":
        return 0
    if mode in {"web", "both"}:
        return serve_web(host, port, server_factory=server_factory)
    raise ValueError(f"未知模式：{mode}")


__all__ = [
    "FAILURE_PLAYBOOK",
    "ROOT",
    "cli_demo_command",
    "failure_playbook",
    "inspect_bundle",
    "run_cli_demo",
    "run_launcher",
    "serve_web",
]
