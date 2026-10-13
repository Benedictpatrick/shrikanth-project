"""FastAPI application: REST + SSE for the Legacy Code Automation UI."""
from __future__ import annotations

import asyncio
import io
import json
import tempfile
import zipfile
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .config import REPO_ROOT, settings
from .graph import gitops, service
from .graph.store import store
from .llm import provider as llm_provider
from .verification import harness

app = FastAPI(title="Legacy Code Automation", version="0.1.0")
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
                   allow_methods=["*"], allow_headers=["*"])


@app.on_event("startup")
def _mark_interrupted_runs() -> None:
    for r in store.list():
        if r["status"] == "running":
            store.update(r["id"], status="failed",
                         error="the server restarted while this run was executing; start a new run "
                               "(runs waiting for approval are preserved and can be resumed)")


# ------------------------------------------------------------------ models
class RunRequest(BaseModel):
    sample: str | None = None
    path: str | None = None
    auto_approve_verified: bool = False


class PlanDecision(BaseModel):
    approved: bool
    programs: list[str] | None = None
    note: str = ""


class UnitDecision(BaseModel):
    decision: str  # approve | reject | retry
    note: str = ""
    force: bool = False  # commit a blocked/failed unit as a known issue


class LLMSettings(BaseModel):
    api_key: str | None = None   # None = leave unchanged, "" = remove the key
    model: str | None = None
    persist: bool = False        # also write to the local .env file
    rpm: int | None = None       # client-side requests/minute budget
    tpm: int | None = None       # client-side tokens/minute budget


class LLMTest(BaseModel):
    api_key: str | None = None   # None = test the key currently in use
    model: str | None = None


# ------------------------------------------------------------------ helpers
def _run_or_404(run_id: str) -> dict:
    run = store.get(run_id)
    if not run:
        raise HTTPException(404, "run not found")
    return run


def _artifact_or_404(run_id: str, name: str):
    _run_or_404(run_id)
    data = store.read_artifact(run_id, name)
    if data is None:
        raise HTTPException(404, f"{name} is not available yet")
    return data


# ------------------------------------------------------------------ system
_model_check: dict = {"key": None, "at": 0.0, "ok": None, "detail": ""}


def _check_model(llm) -> tuple[bool | None, str]:
    """Is the configured model in the provider's catalogue? Cached, never raises."""
    import time
    if not isinstance(llm, llm_provider.GroqProvider):
        return None, ""
    key = (llm.api_key[-6:], llm.model)
    if _model_check["key"] == key and time.time() - _model_check["at"] < 300:
        return _model_check["ok"], _model_check["detail"]
    try:
        models = llm.list_models()
        ok = llm.model in models
        detail = "" if ok else f"model '{llm.model}' is not available for this key; pick one of: {', '.join(models[:8])}"
    except Exception as exc:  # network / bad key
        ok, detail = False, f"could not verify the key/model: {exc}"
    _model_check.update(key=key, at=time.time(), ok=ok, detail=detail)
    return ok, detail


@app.get("/api/health")
def health() -> dict:
    llm = llm_provider.get_provider()
    configured = bool(getattr(llm, "api_key", ""))
    model_ok, model_detail = _check_model(llm) if llm.available() else (None, "")
    usage = llm.stats() if hasattr(llm, "stats") else {}
    return {
        "ok": True,
        "llm": {**settings.public(), "available": llm.available(), "active_model": llm.model,
                "configured": configured, "model_ok": model_ok, "model_detail": model_detail, "usage": usage},
        "cobc": harness.find_cobc(),
        "git": gitops.available(),
    }


@app.post("/api/settings/llm")
def set_llm(body: LLMSettings) -> dict:
    """Apply the user's own Groq key/model. Memory-only unless `persist` is set."""
    from .config import persist_env
    if body.api_key is not None:
        settings.groq_api_key = body.api_key.strip()
        settings.llm_provider = "groq" if settings.groq_api_key else "none"
    if body.model:
        settings.groq_model = body.model.strip()
    if body.rpm:
        settings.groq_rpm = max(1, min(body.rpm, 10_000))
    if body.tpm:
        settings.groq_tpm = max(1000, min(body.tpm, 10_000_000))
    llm_provider.reset_provider()
    _model_check["key"] = None
    persisted = False
    if body.persist:
        values = {"GROQ_API_KEY": settings.groq_api_key, "GROQ_MODEL": settings.groq_model,
                  "GROQ_RPM": str(settings.groq_rpm), "GROQ_TPM": str(settings.groq_tpm)}
        if settings.groq_api_key:
            values["LLM_PROVIDER"] = "groq"
        persist_env(values)
        persisted = True
    return {**health()["llm"], "persisted": persisted}


@app.post("/api/settings/llm/reset-quota")
def reset_quota() -> dict:
    """Clear the quota circuit breaker so the next call is attempted again."""
    llm = llm_provider.get_provider()
    if hasattr(llm, "reset_breaker"):
        llm.reset_breaker()
    return health()["llm"]


@app.post("/api/settings/llm/test")
def test_llm(body: LLMTest) -> dict:
    """Validate a key (without saving it) and list the models it can use."""
    key = (body.api_key or "").strip() or settings.groq_api_key
    if not key:
        raise HTTPException(400, "enter an API key first")
    probe = llm_provider.GroqProvider(key, body.model or settings.groq_model, settings.groq_base_url)
    try:
        models = probe.list_models()
    except Exception as exc:
        status = getattr(getattr(exc, "response", None), "status_code", None)
        if status in (401, 403):
            raise HTTPException(400, "Groq rejected this key (unauthorized)") from exc
        raise HTTPException(502, f"could not reach Groq: {exc}") from exc
    return {"ok": True, "models": models, "model_available": probe.model in models}


@app.get("/api/llm/models")
def llm_models() -> dict:
    llm = llm_provider.get_provider()
    if not isinstance(llm, llm_provider.GroqProvider):
        raise HTTPException(400, "Groq is not configured")
    try:
        return {"models": llm.list_models(), "current": llm.model}
    except Exception as exc:
        raise HTTPException(502, f"could not list models: {exc}") from exc


@app.get("/api/samples")
def samples() -> list[dict]:
    return service.sample_projects()


# -------------------------------------------------------------------- runs
@app.post("/api/runs")
def create_run(req: RunRequest) -> dict:
    options = {"auto_approve_verified": req.auto_approve_verified}
    if req.sample:
        match = next((s for s in service.sample_projects() if s["id"] == req.sample), None)
        if not match:
            raise HTTPException(404, "unknown sample")
        run_id = service.create_run(f"sample:{req.sample}", Path(match["path"]), None, options)
    elif req.path:
        p = Path(req.path).expanduser()
        if not p.is_dir():
            raise HTTPException(400, f"not a directory: {req.path}")
        run_id = service.create_run(p.name, p, None, options)
    else:
        raise HTTPException(400, "provide a sample id or a path")
    service.start_run(run_id)
    return store.get(run_id)


@app.post("/api/runs/upload")
async def upload_run(file: UploadFile = File(...), auto_approve_verified: bool = Form(False)) -> dict:
    if not (file.filename or "").lower().endswith(".zip"):
        raise HTTPException(400, "upload a .zip archive of the repository")
    with tempfile.TemporaryDirectory() as tmp:
        zpath = Path(tmp) / "upload.zip"
        zpath.write_bytes(await file.read())
        try:
            run_id = service.create_run(Path(file.filename).stem, None, zpath,
                                        {"auto_approve_verified": auto_approve_verified})
        except (ValueError, zipfile.BadZipFile) as exc:
            raise HTTPException(400, str(exc)) from exc
    service.start_run(run_id)
    return store.get(run_id)


@app.get("/api/runs")
def list_runs() -> list[dict]:
    return store.list()


@app.get("/api/runs/{run_id}")
def get_run(run_id: str) -> dict:
    run = _run_or_404(run_id)
    units = {}
    for p in (store.artifact_dir(run_id) / "units").glob("*.json") if (store.artifact_dir(run_id) / "units").is_dir() else []:
        u = json.loads(p.read_text(encoding="utf-8"))
        units[u["program"] if "program" in u else p.stem] = {
            "status": u["status"], "oracle": u["oracle"], "decision": u.get("decision"),
            "passed": u["verification"].get("passed", 0), "total": u["verification"].get("total", 0),
            "attempts": len(u["attempts"])}
    return {**run, "units": units, "has_analysis": (store.artifact_dir(run_id) / "analysis.json").is_file(),
            "has_plan": (store.artifact_dir(run_id) / "plan.json").is_file(),
            "has_report": (store.artifact_dir(run_id) / "report.json").is_file()}


@app.delete("/api/runs/{run_id}")
def delete_run(run_id: str) -> dict:
    import shutil
    _run_or_404(run_id)
    shutil.rmtree(store.run_dir(run_id), ignore_errors=True)
    with store._conn() as c:
        c.execute("DELETE FROM runs WHERE id=?", (run_id,))
        c.execute("DELETE FROM events WHERE run_id=?", (run_id,))
    return {"deleted": run_id}


@app.get("/api/runs/{run_id}/analysis")
def analysis(run_id: str):
    return _artifact_or_404(run_id, "analysis.json")


@app.get("/api/runs/{run_id}/plan")
def plan(run_id: str):
    return _artifact_or_404(run_id, "plan.json")


@app.get("/api/runs/{run_id}/units/{program}")
def unit(run_id: str, program: str):
    return _artifact_or_404(run_id, f"units/{program}.json")


@app.get("/api/runs/{run_id}/baseline/{program}")
def baseline(run_id: str, program: str):
    return _artifact_or_404(run_id, f"baseline/{program}.json")


@app.get("/api/runs/{run_id}/report")
def report(run_id: str):
    return _artifact_or_404(run_id, "report.json")


@app.get("/api/runs/{run_id}/source/{path:path}")
def source_file(run_id: str, path: str):
    _run_or_404(run_id)
    root = store.source_dir(run_id).resolve()
    target = (root / path).resolve()
    if root not in target.parents or not target.is_file():
        raise HTTPException(404, "file not found")
    return Response(target.read_text(encoding="latin-1"), media_type="text/plain; charset=utf-8")


# --------------------------------------------------------------- approvals
@app.post("/api/runs/{run_id}/plan/decision")
def decide_plan(run_id: str, body: PlanDecision) -> dict:
    run = _run_or_404(run_id)
    if not run["pending"] or run["pending"].get("type") != "plan_approval":
        raise HTTPException(409, "the run is not waiting for plan approval")
    service.resume_run(run_id, {"approved": body.approved, "programs": body.programs, "note": body.note})
    return store.get(run_id)


@app.post("/api/runs/{run_id}/units/{program}/decision")
def decide_unit(run_id: str, program: str, body: UnitDecision) -> dict:
    run = _run_or_404(run_id)
    pending = run["pending"]
    if not pending or pending.get("type") != "unit_approval" or pending.get("program") != program:
        raise HTTPException(409, f"the run is not waiting for a decision on {program}")
    if body.decision not in ("approve", "reject", "retry"):
        raise HTTPException(400, "decision must be approve, reject or retry")
    service.resume_run(run_id, {"decision": body.decision, "note": body.note, "force": body.force})
    return store.get(run_id)


# ------------------------------------------------------------------ events
@app.get("/api/runs/{run_id}/events")
def events(run_id: str, after: int = 0) -> list[dict]:
    _run_or_404(run_id)
    return store.events(run_id, after)


@app.get("/api/runs/{run_id}/stream")
async def stream(run_id: str, after: int = 0) -> StreamingResponse:
    _run_or_404(run_id)

    async def gen():
        last = after
        idle = 0
        while True:
            evs = store.events(run_id, last)
            for e in evs:
                last = e["id"]
                yield f"id: {e['id']}\nevent: log\ndata: {json.dumps(e)}\n\n"
            run = store.get(run_id)
            if run:
                yield f"event: state\ndata: {json.dumps({k: run[k] for k in ('status', 'stage', 'pending', 'error')})}\n\n"
            idle = 0 if evs else idle + 1
            if run and run["status"] in ("completed", "failed", "rejected") and not evs and idle > 2:
                break
            await asyncio.sleep(0.7)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ------------------------------------------------------------------ output
@app.get("/api/runs/{run_id}/output/tree")
def output_tree(run_id: str) -> dict:
    _run_or_404(run_id)
    root = store.output_dir(run_id)
    files = []
    if root.is_dir():
        for p in sorted(root.rglob("*")):
            if p.is_file() and ".git" not in p.parts and "__pycache__" not in p.parts:
                files.append({"path": str(p.relative_to(root)).replace("\\", "/"), "size": p.stat().st_size})
    return {"files": files, "log": gitops.log(root)}


@app.get("/api/runs/{run_id}/output/file")
def output_file(run_id: str, path: str):
    _run_or_404(run_id)
    root = store.output_dir(run_id).resolve()
    target = (root / path).resolve()
    if root not in target.parents or not target.is_file() or ".git" in target.parts:
        raise HTTPException(404, "file not found")
    return Response(target.read_text(encoding="utf-8", errors="replace"), media_type="text/plain; charset=utf-8")


@app.get("/api/runs/{run_id}/output/archive")
def output_archive(run_id: str):
    _run_or_404(run_id)
    root = store.output_dir(run_id)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in root.rglob("*"):
            if p.is_file() and ".git" not in p.parts and "__pycache__" not in p.parts:
                zf.write(p, p.relative_to(root))
    return Response(buf.getvalue(), media_type="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="migrated-{run_id}.zip"'})


# ------------------------------------------------------------- static UI
_dist = REPO_ROOT / "frontend" / "dist"
if _dist.is_dir():
    app.mount("/assets", StaticFiles(directory=_dist / "assets"), name="assets")

    @app.get("/{full_path:path}")
    def spa(full_path: str):
        candidate = _dist / full_path
        if full_path and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(_dist / "index.html")
