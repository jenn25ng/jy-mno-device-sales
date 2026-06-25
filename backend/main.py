"""FastAPI 백엔드 — Data Gateway fetch → 6탭 brief JSON → SPA 서빙.

Polaris Colab 배포 규칙 (mno-ltv-monitor에서 검증):
- 0.0.0.0:8080 리슨 (Dockerfile CMD)
- GET /health → 항상 200 (fetch 실패와 무관)
- 파일 쓰기는 /tmp만, 시크릿은 env로만
- 데이터 접근은 Data Gateway API만 (SELECT/WITH/SHOW/DESCRIBE)

summary 마트는 사전 집계되어 가벼우므로 LTV monitor의 snapshot 메모리 아키텍처
대신 단순 in-memory 캐시 + on-demand fetch를 사용합니다.
"""
from __future__ import annotations

import os
import sys
import time
import logging
import threading
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse

_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s | %(message)s",
)
log = logging.getLogger("mno-device-sales")

from backend.data_loader import (  # noqa: E402
    SOURCE_TABLE, MART_MODE, resolve_data_source, current_exec_ym,
    use_mock, fetch_rows, _resolve_source_table,
)
from backend.data_pipeline import build_brief  # noqa: E402

FRONTEND_DIR = str(_ROOT / "frontend")
ADMIN_TOKEN = os.getenv("ADMIN_TOKEN", "")

# ── 간단한 brief 캐시 (exec_ym → brief) ───────────────────────────────────────
_cache: dict[str, dict] = {}
_cache_lock = threading.Lock()
_last_error: str | None = None
_last_built_at: str | None = None


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _get_brief(exec_ym: str, *, force: bool = False) -> dict:
    global _last_error, _last_built_at
    if not force:
        with _cache_lock:
            if exec_ym in _cache:
                return _cache[exec_ym]
    rows = fetch_rows(exec_ym)
    brief = build_brief(rows, exec_ym, data_source=resolve_data_source())
    with _cache_lock:
        _cache[exec_ym] = brief
        _last_built_at = _now()
        _last_error = None
    return brief


# ── FastAPI ───────────────────────────────────────────────────────────────────
app = FastAPI(title="MNO Device Sales Dashboard", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[os.getenv("FRONTEND_ORIGIN", "*")],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


@app.get("/health")
def health():
    """Polaris liveness 필수 — fetch 실패와 무관하게 무조건 200."""
    return {"status": "ok"}


@app.get("/api/health")
def api_health():
    """마트 연결 sanity check — current_exec_ym 파티션 행 수를 가벼운 1쿼리로 확인.
    mock 모드면 gateway 호출 없이 ok. gateway 실패 시 503 + 에러 상세."""
    ym = current_exec_ym()
    if use_mock():
        return {"ok": True, "mode": "mock", "exec_ym": ym,
                "row_count": None, "message": "mock 모드 — gateway 미사용"}
    started = time.time()
    try:
        from backend.data_gateway import DataGatewayClient
        table = _resolve_source_table()
        client = DataGatewayClient()
        # exec_ym 파티션 프루닝으로 가벼운 COUNT
        rows = client.run_query(
            f"SELECT COUNT(*) AS n FROM {table} WHERE exec_ym = '{ym}'")
        n = rows[0].get("n") if rows else None
        return {"ok": True, "mode": "gateway", "source_table": table,
                "exec_ym": ym, "row_count": n,
                "latency_ms": int((time.time() - started) * 1000)}
    except Exception as e:
        detail = getattr(e, "detail", None) or {}
        return JSONResponse(status_code=503, content={
            "ok": False, "mode": "gateway", "exec_ym": ym,
            "error_code": detail.get("error_code") or type(e).__name__,
            "error_message": detail.get("message") or str(e),
            "latency_ms": int((time.time() - started) * 1000)})


@app.get("/api/status")
def status():
    with _cache_lock:
        cached = sorted(_cache.keys())
        err = _last_error
        built = _last_built_at
    out = {
        "service": "mno-device-sales",
        "data_source": resolve_data_source(),
        "mart_mode": MART_MODE,
        "use_mock": use_mock(),
        "current_exec_ym": current_exec_ym(),
        "cached_exec_yms": cached,
        "last_built_at": built,
        "last_error": err,
    }
    try:
        out["source_table"] = _resolve_source_table() or SOURCE_TABLE
    except Exception:
        out["source_table"] = SOURCE_TABLE
    return out


@app.get("/api/brief")
def get_brief(exec_ym: str | None = None):
    """기준월 6탭 brief. exec_ym 없으면 전월(또는 CURRENT_EXEC_YM)."""
    global _last_error
    ym = (exec_ym or current_exec_ym()).strip()
    if not (len(ym) == 6 and ym.isdigit()):
        raise HTTPException(400, f"invalid exec_ym: {ym} (YYYYMM)")
    try:
        return _get_brief(ym)
    except Exception as e:
        log.exception("brief 빌드 실패 exec_ym=%s", ym)
        with _cache_lock:
            _last_error = f"{type(e).__name__}: {str(e)[:300]}"
        raise HTTPException(502, f"{type(e).__name__}: {str(e)[:200]}")


@app.post("/api/refresh")
def refresh(
    exec_ym: str | None = None,
    x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
):
    if not ADMIN_TOKEN:
        raise HTTPException(503, "ADMIN_TOKEN 미설정 — 비활성화")
    if x_admin_token != ADMIN_TOKEN:
        raise HTTPException(401, "Invalid X-Admin-Token")
    ym = (exec_ym or current_exec_ym()).strip()
    try:
        _get_brief(ym, force=True)
        return {"refreshed": True, "exec_ym": ym, "at": _now()}
    except Exception as e:
        raise HTTPException(502, f"{type(e).__name__}: {str(e)[:200]}")


@app.post("/api/test-connection")
def test_connection():
    """Gateway 연결/권한 빠른 확인 (LIMIT 1). mock 모드면 skip."""
    if use_mock():
        return {"success": True, "mode": "mock",
                "message": "mock 모드 — gateway 미사용"}
    started = time.time()
    try:
        from backend.data_gateway import DataGatewayClient
        from backend.data_loader import _resolve_source_table
        table = _resolve_source_table()
        client = DataGatewayClient()
        qid = client.start_query(f"SELECT * FROM {table} LIMIT 1")
        client.poll_until_done(qid, interval=1, max_sec=60)
        res = client.get_all_results(qid, page_size=1)
        return {
            "success": True, "mode": "gateway", "source_table": table,
            "sample_columns": [c.get("name") for c in res.get("columns", [])],
            "latency_ms": int((time.time() - started) * 1000),
        }
    except Exception as e:
        detail = getattr(e, "detail", None) or {}
        return {
            "success": False,
            "error_code": detail.get("error_code") or type(e).__name__,
            "error_message": detail.get("message") or str(e),
            "latency_ms": int((time.time() - started) * 1000),
        }


# ── 정적 SPA ──────────────────────────────────────────────────────────────────
app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
