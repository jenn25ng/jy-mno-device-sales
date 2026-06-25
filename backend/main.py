"""FastAPI 백엔드 — 메모리 캐시 패턴.

startup에 마트(최근 24개월)를 Athena→메모리(pandas) 1회 적재(`backend.data.load_mart`).
모든 endpoint는 `get_df()`로 메모리 DataFrame을 받아 pandas 집계 → JSON. Athena 재호출 X.

Polaris/배포 규칙: 0.0.0.0:8080, GET /health 항상 200, 시크릿은 env, 쓰기는 /tmp.
실제 Athena 연결은 사용자가 배포 환경에서 수행 (로컬은 mock 자동).
"""
from __future__ import annotations

import os
import sys
import logging
import threading
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s | %(message)s",
)
log = logging.getLogger("mno-device-sales")

from backend import data  # noqa: E402
from backend.aggregate import build_brief  # noqa: E402

FRONTEND_DIR = str(_ROOT / "frontend")
ADMIN_TOKEN = os.getenv("ADMIN_TOKEN", "")

app = FastAPI(title="MNO Device Sales Dashboard", version="0.2.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[os.getenv("FRONTEND_ORIGIN", "*")],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


@app.on_event("startup")
def _startup():
    """마트 메모리 적재 — 백그라운드 스레드로 (헬스체크 블로킹 방지).
    실패해도 앱은 살아있고 /api/status에 error 노출."""
    def _worker():
        try:
            data.load_mart()
        except Exception:
            log.exception("startup 마트 적재 실패 (앱은 계속 동작)")
    threading.Thread(target=_worker, name="mart-load", daemon=True).start()


# ── Health / Status ───────────────────────────────────────────────────────────
@app.get("/health")
def health():
    """Polaris liveness — 무조건 200."""
    return {"status": "ok"}


@app.get("/api/health")
def api_health():
    """마트 메모리 적재 sanity check — 캐시 행 수/적재시각."""
    m = data.cache_meta()
    ok = m["rows"] > 0 and m["error"] is None
    return {"ok": ok, **m}


@app.get("/api/status")
def status():
    return {"service": "mno-device-sales", **data.cache_meta()}


# ── 6탭 brief (메모리 슬라이스) ───────────────────────────────────────────────
@app.get("/api/brief")
def get_brief(exec_ym: str | None = None):
    """기준월 6탭 brief. exec_ym 없으면 캐시 내 최신월. 전부 메모리에서 집계."""
    if exec_ym is not None:
        exec_ym = exec_ym.strip()
        if not (len(exec_ym) == 6 and exec_ym.isdigit()):
            raise HTTPException(400, f"invalid exec_ym: {exec_ym} (YYYYMM)")
    try:
        df = data.get_df()
    except Exception as e:
        raise HTTPException(503, f"마트 적재 전/실패: {type(e).__name__}: {str(e)[:200]}")
    return build_brief(df, exec_ym, data_source=data.data_source())


# ── 수동 재적재 ───────────────────────────────────────────────────────────────
@app.post("/api/refresh")
def refresh(x_admin_token: str | None = Header(default=None, alias="X-Admin-Token")):
    if ADMIN_TOKEN and x_admin_token != ADMIN_TOKEN:
        raise HTTPException(401, "Invalid X-Admin-Token")
    try:
        return data.refresh()
    except Exception as e:
        raise HTTPException(502, f"{type(e).__name__}: {str(e)[:200]}")


# ── 정적 SPA ──────────────────────────────────────────────────────────────────
app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
