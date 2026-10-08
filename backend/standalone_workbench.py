"""Small standalone workbench server shared by the six module launchers.

This server deliberately stays outside the professional modules.  It provides
one upload/status/result shell and delegates the actual analysis to the
registered module entry point through ``backend.run_module.run_one``.
"""

from __future__ import annotations

import os
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dotenv import set_key
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from backend.core.config import PROJECT_ROOT, config_path, load_runtime_config
from backend.deepseek_client import api_key_configured
from backend.module_registry import registrations
from backend.pdf_reader import MAX_UPLOAD_BYTES
from backend.run_module import run_one


ROOT = PROJECT_ROOT
UPLOAD_DIR = ROOT / "data" / "modular_workbench" / "uploads"
OUTPUT_DIR = ROOT / "data" / "modular_workbench" / "runs"

MODULE_LABELS = {
    "business": "模块一 · 业务与经营背景",
    "profit": "模块二 · 盈利来源与变化",
    "assets": "模块三 · 资产质量与经营效率",
    "cashflow": "模块四 · 现金流与利润兑现",
    "solvency": "模块五 · 偿债能力与资金压力",
    "disclosure": "模块六 · 披露可信度与特殊事项",
}
MODULE_ALIASES = {
    "part1_business": "business",
    "part2_profit": "profit",
    "part3_assets": "assets",
    "part4_cashflow": "cashflow",
    "part5_solvency": "solvency",
    "part6_disclosure": "disclosure",
}


def _module_id() -> str:
    requested = os.getenv("FINLAB_STANDALONE_MODULE", "business").strip().lower()
    return MODULE_ALIASES.get(requested, requested)


MODULE_ID = _module_id()
if MODULE_ID not in MODULE_LABELS:
    raise RuntimeError(f"未知模块：{MODULE_ID}")

load_runtime_config()
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


class DeepSeekKeyUpdate(BaseModel):
    api_key: str


_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="modular-workbench")
_lock = threading.Lock()
_runs: dict[str, dict[str, Any]] = {}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _public_run(run: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": run["id"],
        "module_id": run["module_id"],
        "module_label": MODULE_LABELS[run["module_id"]],
        "module_version": run.get("module_version"),
        "file_name": run["file_name"],
        "status": run["status"],
        "created_at": run["created_at"],
        "updated_at": run["updated_at"],
        "error": run.get("error"),
        "result": run.get("result"),
        "run_dir": run.get("run_dir"),
    }


def _set_run(run_id: str, **changes: Any) -> None:
    with _lock:
        current = _runs.get(run_id)
        if current is not None:
            current.update(changes)
            current["updated_at"] = _now()


def _run_in_background(run_id: str, pdf_path: Path, config_file: Path) -> None:
    _set_run(run_id, status="running")
    try:
        result = run_one(MODULE_ID, pdf_path, OUTPUT_DIR, config_file)
        _set_run(
            run_id,
            status=result.get("status", "completed"),
            result=result,
            run_dir=result.get("run_dir"),
            module_version=result.get("module_version"),
        )
    except Exception as exc:  # the detailed trace remains in the run directory
        _set_run(run_id, status="failed", error=str(exc))


def _html() -> str:
    template = r"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width,initial-scale=1" />
<title>__MODULE_LABEL__ · 独立工作台</title>
<style>
:root{font-family:system-ui,-apple-system,"Microsoft YaHei",sans-serif;color:#1d2a2a;background:#f3f7f5}
body{max-width:980px;margin:0 auto;padding:42px 24px}.top{display:flex;justify-content:space-between;gap:24px;align-items:flex-start}
h1{margin:6px 0 8px;font-size:30px}.sub{color:#657573;margin:0 0 28px}.card{background:#fff;border:1px solid #d9e5e1;border-radius:16px;padding:22px;box-shadow:0 8px 28px #173b2b0d;margin:16px 0}
label{display:block;font-weight:650;margin-bottom:8px}input[type=password],input[type=file]{width:100%;box-sizing:border-box;padding:11px;border:1px solid #cbdad5;border-radius:9px;background:#fbfdfc}
button{border:0;border-radius:9px;padding:11px 16px;background:#157a59;color:#fff;font-weight:700;cursor:pointer;margin-top:12px}button.secondary{background:#e7f1ed;color:#166648}
.row{display:flex;gap:12px;align-items:center;flex-wrap:wrap}.status{padding:5px 10px;border-radius:999px;background:#e7f1ed;color:#166648;font-size:13px}.muted{color:#74837f;font-size:13px}.error{color:#a42b2b;background:#fff0f0;padding:10px;border-radius:8px;white-space:pre-wrap}.ok{color:#166648}
pre{background:#152220;color:#e4f6ee;border-radius:10px;padding:16px;overflow:auto;min-height:100px;white-space:pre-wrap;word-break:break-word}.hidden{display:none}
</style>
</head>
<body>
<div class="top"><div><div class="status">独立模块工作台 · __MODULE_ID__</div><h1>__MODULE_LABEL__</h1><p class="sub">上传一份年报，调用本模块自己的专业流程。原文出处、计算和运行记录由共同底座保存。</p></div><div id="health" class="status">正在检查连接…</div></div>
<section class="card"><h2>第一次使用先配置 DeepSeek</h2><p class="muted">Key 只写入本机项目目录的 .env，不会回显到页面。</p><label for="key">DeepSeek API Key</label><input id="key" type="password" autocomplete="off" placeholder="sk-…" /><button class="secondary" id="saveKey">保存 Key</button><span id="keyMsg" class="muted"></span></section>
<section class="card"><h2>开始本模块分析</h2><input id="pdf" type="file" accept="application/pdf,.pdf" /><button id="start">上传并分析</button><div id="message" class="muted"></div></section>
<section class="card"><h2>运行结果</h2><div id="result" class="muted">还没有运行。</div></section>
<script>
const $=id=>document.getElementById(id);
async function json(url,options){const r=await fetch(url,options);const d=await r.json().catch(()=>({detail:r.statusText}));if(!r.ok)throw new Error(d.detail||'请求失败');return d}
async function refreshHealth(){try{const d=await json('/api/health');$('health').textContent=d.api_configured?'DeepSeek 已配置':'等待配置 Key';$('health').className='status '+(d.api_configured?'ok':'');}catch(e){$('health').textContent='服务不可用';$('health').className='status error'}}
async function poll(id){for(let i=0;i<360;i++){const d=await json('/api/runs/'+id);$('message').textContent=d.status==='running'?'正在分析，模块会按需读取年报原文…':d.status==='queued'?'任务已排队…':'';if(d.status==='completed'){ $('result').innerHTML='<pre>'+JSON.stringify(d.result,null,2).replaceAll('&','&amp;').replaceAll('<','&lt;')+'</pre>';$('message').textContent='分析完成。';return}if(d.status==='failed'){ $('result').innerHTML='<div class="error">'+(d.error||'分析失败')+'</div>';return}await new Promise(r=>setTimeout(r,1000))}throw new Error('等待超时，请查看后台运行记录。')}
$('saveKey').onclick=async()=>{const key=$('key').value.trim();if(!key){$('keyMsg').textContent='请先输入 Key';return}try{await json('/api/settings/deepseek-key',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({api_key:key})});$('key').value='';$('keyMsg').textContent='已保存';refreshHealth()}catch(e){$('keyMsg').textContent=e.message}};
$('start').onclick=async()=>{const f=$('pdf').files[0];if(!f){$('message').textContent='请选择 PDF';return}const form=new FormData();form.append('file',f);$('start').disabled=true;$('result').textContent='';try{const d=await json('/api/analyze',{method:'POST',body:form});await poll(d.id)}catch(e){$('message').textContent=e.message}finally{$('start').disabled=false}};
refreshHealth();
</script>
</body></html>"""
    return template.replace("__MODULE_ID__", MODULE_ID).replace("__MODULE_LABEL__", MODULE_LABELS[MODULE_ID])


app = FastAPI(title=f"{MODULE_LABELS[MODULE_ID]}独立工作台", version="modular-0.1")


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return _html()


@app.get("/api/health")
def health() -> dict[str, Any]:
    registration = registrations()[MODULE_ID]
    return {
        "status": "ok",
        "module_id": MODULE_ID,
        "module_label": MODULE_LABELS[MODULE_ID],
        "module_version": registration.version,
        "api_configured": api_key_configured(),
    }


@app.get("/api/modules")
def list_modules() -> dict[str, Any]:
    available = registrations()
    return {
        "modules": [
            {"module_id": key, "label": MODULE_LABELS.get(key, key), "version": item.version}
            for key, item in sorted(available.items())
        ]
    }


@app.post("/api/settings/deepseek-key")
def save_deepseek_key(payload: DeepSeekKeyUpdate) -> dict[str, bool]:
    api_key = payload.api_key.strip()
    if not api_key or len(api_key) > 512 or any(character.isspace() for character in api_key):
        raise HTTPException(status_code=400, detail="API Key 不能为空，且不能包含空格或换行。")
    target = config_path()
    try:
        saved, _, _ = set_key(target, "DEEPSEEK_API_KEY", api_key, quote_mode="always")
    except OSError as exc:
        raise HTTPException(status_code=500, detail="无法写入本机 .env 文件。") from exc
    if not saved:
        raise HTTPException(status_code=500, detail="无法写入本机 .env 文件。")
    os.environ["DEEPSEEK_API_KEY"] = api_key
    return {"api_configured": True}


@app.post("/api/analyze")
async def create_analysis(file: UploadFile = File(...)) -> dict[str, Any]:
    if not api_key_configured():
        raise HTTPException(status_code=503, detail="尚未配置 DeepSeek API Key。请先保存 Key。")
    file_name = Path(file.filename or "").name.strip()
    if not file_name.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="请上传 PDF 文件。")
    content = await file.read(MAX_UPLOAD_BYTES + 1)
    await file.close()
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="文件超过 25 MB，请先压缩 PDF 后重试。")
    if not content.startswith(b"%PDF-"):
        raise HTTPException(status_code=400, detail="上传内容不是有效的 PDF 文件。")

    run_id = uuid.uuid4().hex
    pdf_path = UPLOAD_DIR / f"{run_id}.pdf"
    pdf_path.write_bytes(content)
    registration = registrations()[MODULE_ID]
    record = {
        "id": run_id,
        "module_id": MODULE_ID,
        "module_version": registration.version,
        "file_name": file_name[:240],
        "status": "queued",
        "created_at": _now(),
        "updated_at": _now(),
        "error": None,
        "result": None,
    }
    with _lock:
        _runs[run_id] = record
    _executor.submit(_run_in_background, run_id, pdf_path, config_path())
    return _public_run(record)


@app.get("/api/runs")
def list_runs() -> dict[str, Any]:
    with _lock:
        values = list(_runs.values())
    return {"runs": [_public_run(item) for item in reversed(values)]}


@app.get("/api/runs/{run_id}")
def read_run(run_id: str) -> dict[str, Any]:
    with _lock:
        record = _runs.get(run_id)
    if record is None:
        raise HTTPException(status_code=404, detail="没有找到这次运行。")
    return _public_run(record)
