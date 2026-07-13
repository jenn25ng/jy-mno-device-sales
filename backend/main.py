"""FastAPI 백엔드 — 메모리 캐시 패턴.

startup에 마트(최근 24개월)를 Athena→메모리(pandas) 1회 적재(`backend.data.load_mart`).
모든 endpoint는 `get_df()`로 메모리 DataFrame을 받아 pandas 집계 → JSON. Athena 재호출 X.

Polaris/배포 규칙: 0.0.0.0:8080, GET /health 항상 200, 시크릿은 env, 쓰기는 /tmp.
실제 Athena 연결은 사용자가 배포 환경에서 수행 (로컬은 mock 자동).
"""
from __future__ import annotations

import os
import sys
import time
import logging
import threading
from datetime import datetime, timedelta, timezone
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
from backend import aggregate as _agg  # noqa: E402
from backend.aggregate import build_brief, build_overview, build_sku  # noqa: E402

FRONTEND_DIR = str(_ROOT / "frontend")
ADMIN_TOKEN = os.getenv("ADMIN_TOKEN", "")
KST = timezone(timedelta(hours=9))
REFRESH_HOUR = int(os.getenv("REFRESH_HOUR_KST", "8"))    # 매일 재적재 시각(KST). 0~23, 배치 이후로.

app = FastAPI(title="MNO Device Sales Dashboard", version="0.2.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[os.getenv("FRONTEND_ORIGIN", "*")],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


def _daily_refresh_worker():
    """매일 REFRESH_HOUR시(KST)에 마트 메모리 재적재 — 아침 배치 이후 자동 갱신.
    데몬 스레드에서 다음 시각까지 sleep → refresh 반복. 실패해도 다음날 재시도."""
    while True:
        now = datetime.now(KST)
        nxt = now.replace(hour=REFRESH_HOUR, minute=0, second=0, microsecond=0)
        if nxt <= now:
            nxt += timedelta(days=1)
        wait = (nxt - now).total_seconds()
        log.info("다음 자동 재적재 예정: %s KST (%.0f분 후)", nxt.strftime("%Y-%m-%d %H:%M"), wait / 60)
        time.sleep(wait)
        try:
            data.refresh()
            log.info("자동 재적재 완료 (%02d시 KST)", REFRESH_HOUR)
        except Exception:
            log.exception("자동 재적재 실패 (다음날 재시도)")


@app.on_event("startup")
def _startup():
    """마트 메모리 적재 — 백그라운드 스레드로 (헬스체크 블로킹 방지).
    실패해도 앱은 살아있고 /api/status에 error 노출. + 매일 8시(KST) 자동 재적재 스케줄러."""
    def _worker():
        try:
            data.load_mart()
        except Exception:
            log.exception("startup 마트 적재 실패 (앱은 계속 동작)")
    threading.Thread(target=_worker, name="mart-load", daemon=True).start()
    threading.Thread(target=_daily_refresh_worker, name="daily-refresh", daemon=True).start()


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


@app.get("/api/diagnostics")
def diagnostics():
    """데이터 연결 4단계 진단 (환경변수→Athena fetch→컬럼 검증→메모리 캐시).
    프런트 상단 status chip + 디테일 드로어가 소비."""
    return data.diagnostics()


@app.get("/api/status")
def status():
    return {"service": "mno-device-sales", **data.cache_meta()}


# ── 6탭 brief (메모리 슬라이스) ───────────────────────────────────────────────
@app.get("/api/brief")
def get_brief(period_start: str | None = None, period_end: str | None = None,
              scrb_type: str | None = None, channel: str | None = None,
              compare_to: str | None = None,
              compare_start: str | None = None, compare_end: str | None = None):
    """전 탭 공통 기간(period_start/end, YYYYMMDD) 기준 6탭 brief. 미지정 시 최신 월 폴백.
    compare_to(전역 비교) 지정 시 본부별 단말군 증감(delta·movers)도 반환.
    compare_to='custom'이면 compare_start/end(YYYYMMDD) 직접 기간 사용."""
    try:
        df = data.get_df()
    except Exception as e:
        raise HTTPException(503, f"마트 적재 전/실패: {type(e).__name__}: {str(e)[:200]}")
    s = _vdate(period_start, "period_start") if period_start else None
    e = _vdate(period_end, "period_end") if period_end else None
    cs = _vdate(compare_start, "compare_start") if compare_start else None
    ce = _vdate(compare_end, "compare_end") if compare_end else None
    return build_brief(df, s, e, scrb_type=scrb_type, channel=channel, compare_to=compare_to,
                       compare_start=cs, compare_end=ce, data_source=data.data_source())


@app.get("/api/sku")
def get_sku(device_group: str, period_start: str | None = None, period_end: str | None = None,
            scrb_type: str | None = None):
    """단말군 1개의 SKU 세부(펫네임×서브모델×용량×본부) — 드릴다운 클릭 시 온디맨드 조회.
    메인 로드는 device_group 그레인이라(행수 축소), 세부는 여기서 그때그때 조회."""
    try:
        df = data.get_df()
    except Exception as e:
        raise HTTPException(503, f"마트 적재 전/실패: {type(e).__name__}: {str(e)[:200]}")
    s = _vdate(period_start, "period_start") if period_start else None
    e = _vdate(period_end, "period_end") if period_end else None
    hqs = (_agg._order(df["mkt_div_org_nm"].dropna().unique(), _agg.CANON_HQS)
           if df is not None and len(df) else [])
    rows = data.sku_rows(device_group, s, e)
    return build_sku(rows, device_group, hqs, scrb_type=scrb_type)


def _vdate(s: str, name: str) -> str:
    s = s.strip()
    if not (len(s) == 8 and s.isdigit()):
        raise HTTPException(400, f"invalid {name}: {s} (YYYYMMDD)")
    return s


@app.get("/api/overview")
def overview(period_start: str | None = None, period_end: str | None = None,
             compare_to: str = "prev_day", scrb_type: str | None = None,
             channel: str | None = None,
             compare_start: str | None = None, compare_end: str | None = None):
    """전사 개요 시점+비교. period_start/end(YYYYMMDD) 미지정 시 최신일 단일.
    compare_to ∈ none|prev_day|prev_weekday|prev_month|prev_year → {current, compare, delta}.
    scrb_type: 가입유형 필터(신규/MNOMNP/MVNOMNP/기기변경, MNP_ALL=MNO+MVNO). 미지정/'전체'면 전체 합산."""
    try:
        df = data.get_df()
    except Exception as e:
        raise HTTPException(503, f"마트 적재 전/실패: {type(e).__name__}: {str(e)[:200]}")
    led = data.latest_exec_dt()
    s = _vdate(period_start or led or "", "period_start")
    e = _vdate(period_end or led or "", "period_end")
    cs = _vdate(compare_start, "compare_start") if compare_start else None
    ce = _vdate(compare_end, "compare_end") if compare_end else None
    return build_overview(df, s, e, compare_to, scrb_type=scrb_type, channel=channel,
                          compare_start=cs, compare_end=ce, data_source=data.data_source())


# ── 수동 재적재 ───────────────────────────────────────────────────────────────
@app.post("/api/refresh")
def refresh(x_admin_token: str | None = Header(default=None, alias="X-Admin-Token")):
    """재적재를 백그라운드로 트리거만 하고 즉시 반환(ALB 60초 타임아웃/504 회피).
    프런트는 /api/diagnostics의 loading 플래그를 폴링해 완료를 감지·갱신한다."""
    if ADMIN_TOKEN and x_admin_token != ADMIN_TOKEN:
        raise HTTPException(401, "Invalid X-Admin-Token")
    try:
        return data.refresh_async()
    except Exception as e:
        raise HTTPException(502, f"{type(e).__name__}: {str(e)[:200]}")


# ── 정적 SPA ──────────────────────────────────────────────────────────────────
app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
