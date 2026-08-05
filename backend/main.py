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

from urllib.parse import unquote

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.responses import HTMLResponse

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
# (사번 접근 게이트 제거 — 2026-08-05: 접근 통제는 Polaris 앱 권한(Permission)만으로.
#  ALLOWED_SABUNS·GATE_HOST_MARKER·_gate_applies·sabun_gate 미들웨어 전부 삭제. env 남아도 무효.)
KST = timezone(timedelta(hours=9))
REFRESH_HOUR = int(os.getenv("REFRESH_HOUR_KST", "7"))    # 매일 재적재 시각(KST). 0~23, 배치 이후로.
REFRESH_MIN = int(os.getenv("REFRESH_MIN_KST", "30"))     # 재적재 분(KST). 기본 07:30 (아침 배치 직후로 당김).

APP_VERSION = datetime.now(KST).strftime("%Y%m%d-%H%M%S")   # 프로세스 시작 시각 = 배포 식별자(재배포마다 변경)
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
        nxt = now.replace(hour=REFRESH_HOUR, minute=REFRESH_MIN, second=0, microsecond=0)
        if nxt <= now:
            nxt += timedelta(days=1)
        wait = (nxt - now).total_seconds()
        log.info("다음 자동 재적재 예정: %s KST (%.0f분 후)", nxt.strftime("%Y-%m-%d %H:%M"), wait / 60)
        time.sleep(wait)
        try:
            data.refresh()
            log.info("자동 재적재 완료 (%02d:%02d KST)", REFRESH_HOUR, REFRESH_MIN)
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


@app.get("/api/version")
def api_version():
    """앱 버전 = 프로세스 시작 시각. 재배포 시 새 프로세스라 값이 바뀜 →
    프런트가 이 값 변화를 감지해 '새 버전 배포됨 · 새로고침' 안내를 띄운다."""
    return {"version": APP_VERSION}


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
              agree_type: str | None = None, compare_to: str | None = None,
              compare_start: str | None = None, compare_end: str | None = None,
              b2c_only: bool = False):
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
    return build_brief(df, s, e, scrb_type=scrb_type, channel=channel, agree_type=agree_type,
                       compare_to=compare_to, compare_start=cs, compare_end=ce,
                       b2c_only=b2c_only, data_source=data.data_source())


@app.get("/api/sku")
def get_sku(device_group: str, period_start: str | None = None, period_end: str | None = None,
            scrb_type: str | None = None, channel: str | None = None, agree_type: str | None = None,
            hq: str | None = None, b2c_only: bool = False, compare_to: str | None = None,
            compare_start: str | None = None, compare_end: str | None = None):
    """단말군 1개의 SKU 세부(펫네임×서브모델×용량×본부) — 드릴다운 클릭 시 온디맨드 조회.
    메인 로드는 device_group 그레인이라(행수 축소), 세부는 여기서 그때그때 조회.
    전역 필터(scrb_type·channel·agree_type·b2c_only) + hq(폴더블8 탭 본부칩)를 반영.
    compare_to(전역 비교) 지정 시 KPI 총계·모델별 증감(delta)도 반환 — 폴더블8 탭 전용."""
    try:
        df = data.get_df()
    except Exception as e:
        raise HTTPException(503, f"마트 적재 전/실패: {type(e).__name__}: {str(e)[:200]}")
    s = _vdate(period_start, "period_start") if period_start else None
    e = _vdate(period_end, "period_end") if period_end else None
    hqs = (_agg._order(df["mkt_div_org_nm"].dropna().unique(), _agg.CANON_HQS)
           if df is not None and len(df) else [])
    if b2c_only:                                           # 전역 B2C — 본부 축도 6 지역본부로
        hqs = [h for h in hqs if h in _agg.B2C_HQS]
    op_days = _agg._operating_days(df) if df is not None and len(df) else None
    rows = data.sku_rows(device_group, s, e, channel=channel, agree_type=agree_type,
                         hq=hq, b2c_only=b2c_only)
    result = build_sku(rows, device_group, hqs, scrb_type=scrb_type, op_days=op_days)
    # 전역 비교 — 비교기간을 한 번 더 조회해 KPI 증감 산출(폴더블8 탭). 비교기간 0건이면 base_zero
    if compare_to and compare_to != "none" and s and e:
        cs = _vdate(compare_start, "compare_start") if compare_start else None
        ce = _vdate(compare_end, "compare_end") if compare_end else None
        cmp_p = _agg._resolve_compare(s, e, compare_to, compare_start=cs, compare_end=ce, op_days=op_days)
        if cmp_p:
            ccs, cce = cmp_p
            crows = data.sku_rows(device_group, ccs, cce, channel=channel, agree_type=agree_type,
                                  hq=hq, b2c_only=b2c_only)
            cres = build_sku(crows, device_group, hqs, scrb_type=scrb_type)
            result["delta"] = _agg._sku_delta(result, cres, compare_to, ccs, cce)
    return result


def _vdate(s: str, name: str) -> str:
    s = s.strip()
    if not (len(s) == 8 and s.isdigit()):
        raise HTTPException(400, f"invalid {name}: {s} (YYYYMMDD)")
    return s


@app.get("/api/overview")
def overview(period_start: str | None = None, period_end: str | None = None,
             compare_to: str = "prev_day", scrb_type: str | None = None,
             channel: str | None = None, agree_type: str | None = None,
             compare_start: str | None = None, compare_end: str | None = None,
             b2c_only: bool = False):
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
                          agree_type=agree_type, compare_start=cs, compare_end=ce,
                          b2c_only=b2c_only, data_source=data.data_source())


# ── 수동 재적재 ───────────────────────────────────────────────────────────────
@app.post("/api/refresh")
def refresh(full: int = 0, x_admin_token: str | None = Header(default=None, alias="X-Admin-Token")):
    """재적재를 백그라운드로 트리거만 하고 즉시 반환(ALB 60초 타임아웃/504 회피).
    프런트는 /api/diagnostics의 loading 플래그를 폴링해 완료를 감지·갱신한다.
    기본=증분(최근 2개월). **full=1** 이면 전체 재조회(과거 전 기간 소급 변경 시, 예: 색상·용량 최초 적재)."""
    if ADMIN_TOKEN and x_admin_token != ADMIN_TOKEN:
        raise HTTPException(401, "Invalid X-Admin-Token")
    try:
        return data.refresh_async(full=bool(full))
    except Exception as e:
        raise HTTPException(502, f"{type(e).__name__}: {str(e)[:200]}")


# ── 뷰어 신원 (사내 SSO 프록시 헤더) — 화면 워터마크용 ─────────────────────────
# Polaris mydesk 프록시가 주입: x-auth-user(사번)·x-sm-name(이름)·x-sm-email·x-sm-dept 등.
# 요청자 '본인' 헤더만 반환(타인 정보 아님). 값이 URL-encoded일 수 있어 unquote.
def _hdr(request: Request, *names: str) -> str:
    for n in names:
        v = request.headers.get(n)
        if v:
            return unquote(v).strip()
    return ""


@app.get("/api/me")
def me(request: Request):
    return {
        "sabun": _hdr(request, "x-auth-user", "x-sm-empno", "x-sm-uid"),
        "name":  _hdr(request, "x-sm-name"),
        "email": _hdr(request, "x-sm-email"),
        "dept":  _hdr(request, "x-sm-dept"),
    }


# ── 정적 SPA ──────────────────────────────────────────────────────────────────
app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
