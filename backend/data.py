"""데이터 계층 — 메모리 캐시 패턴.

startup에 마트 전체(최근 24개월, 마트 SQL v3.3에서 이미 윈도잉됨)를 Athena에서
한 번 읽어 pandas DataFrame으로 메모리에 보관. 모든 화면 인터랙션(탭/필터/기간)은
`get_df()`로 이 메모리를 슬라이스·집계 → Athena 재호출 없음.

실제 Athena 접근은 awswrangler(`wr.athena.read_sql_query`)를 lazy import 하므로,
awswrangler 미설치 로컬/USE_MOCK/ATHENA_OUTPUT_LOCATION 미설정 시 자동으로
결정론적 mock DataFrame을 사용해 화면 개발이 가능합니다.

마트 스키마: sandbox_db_max.device_sales_summary_daily2 (56 컬럼) — 차원이 이미
사전 집계됨(device_group, mkt_div_org_nm, sub_model, storage, sim_only, scrb_type …).
"""
from __future__ import annotations

import os
import logging
import hashlib
from datetime import datetime, date, timedelta

import pandas as pd

log = logging.getLogger(__name__)

# ── 메모리 캐시 ────────────────────────────────────────────────────────────────
_CACHE: dict = {"df": None, "loaded_at": None, "source": None, "error": None}

WINDOW_MONTHS = int(os.getenv("DATA_WINDOW_MONTHS", "24"))


# ── env 헬퍼 ──────────────────────────────────────────────────────────────────
def _env(*names: str, default: str = "") -> str:
    for n in names:
        v = (os.getenv(n) or "").strip()
        if v:
            return v
    return default


def _database() -> str:
    return _env("DATABASE", "database", "DATA_GATEWAY_DATABASE", default="obt_encore_max")


def source_table() -> str:
    st = _env("SOURCE_TABLE", "source_table")
    if st:
        return st
    tbl = _env("MART_TABLE_NAME", "mart_table_name", default="device_sales_summary_daily2")
    return f"{_database()}.{tbl}"


def _auth_key() -> str:
    return _env("auth_key", "AUTH_KEY", "DATA_GATEWAY_AUTH_KEY")


def use_mock() -> bool:
    if _env("USE_MOCK").lower() in ("1", "true", "yes"):
        return True
    # Data Gateway 인증키 없으면 실조회 불가 → mock
    return not _auth_key()


def data_source() -> str:
    return "mock" if use_mock() else "gateway"


# ── 적재 ──────────────────────────────────────────────────────────────────────
# 대시보드가 실제로 쓰는 차원만 — 마트 전체(26차원) 대신 이 그레인으로 GROUP BY해 가져옴
# (가입유형/채널/결합/요금제 등 미사용 차원을 합쳐 행 수를 수십~수백배 축소 → Gateway 적재 빠름)
_FETCH_DIMS = ["exec_dt", "exec_ym", "mkt_div_org_nm", "device_group",
               "sub_model", "storage", "sim_only", "scrb_type"]


def _query_gateway() -> pd.DataFrame:
    """Polaris Data Gateway로 마트 조회 (auth_key 인증, output location 불필요).
    SELECT * 대신 대시보드 그레인으로 projection+집계 → 행수 급감(적재 속도/메모리 개선)."""
    from backend.data_gateway import DataGatewayClient
    dims = ", ".join(_FETCH_DIMS)
    sql = (f"SELECT {dims}, SUM(sales_cnt) AS sales_cnt "
           f"FROM {source_table()} GROUP BY {dims}")
    log.info("Gateway fetch: %s", sql)
    rows = DataGatewayClient().run_query(sql)   # page_size=1000 (Gateway 최대 한도)
    return pd.DataFrame(rows)


def load_mart() -> pd.DataFrame:
    """startup 1회 호출. Gateway(or mock) → DataFrame 메모리 저장."""
    src = data_source()
    try:
        df = _mock_df() if src == "mock" else _query_gateway()
        df = _normalize(df)
        _CACHE.update(df=df, loaded_at=datetime.now(), source=src, error=None)
        log.info("마트 적재 완료: %s행 (source=%s)", len(df), src)
    except Exception as e:  # startup에서 죽지 않도록
        log.exception("마트 적재 실패")
        _CACHE.update(error=f"{type(e).__name__}: {e}")
        raise
    return _CACHE["df"]


def get_df() -> pd.DataFrame:
    if _CACHE["df"] is None:
        load_mart()
    return _CACHE["df"]


def refresh() -> dict:
    load_mart()
    return {
        "ok": True,
        "rows": int(len(_CACHE["df"])) if _CACHE["df"] is not None else 0,
        "source": _CACHE["source"],
        "loaded_at": _CACHE["loaded_at"].isoformat(timespec="seconds")
        if _CACHE["loaded_at"] else None,
    }


def cache_meta() -> dict:
    df = _CACHE["df"]
    return {
        "source": _CACHE["source"] or data_source(),
        "rows": int(len(df)) if df is not None else 0,
        "loaded_at": _CACHE["loaded_at"].isoformat(timespec="seconds")
        if _CACHE["loaded_at"] else None,
        "error": _CACHE["error"],
        "window_months": WINDOW_MONTHS,
        "source_table": source_table(),
        "available_exec_yms": available_exec_yms(),
        "latest_exec_ym": latest_exec_ym(),
        "latest_exec_dt": latest_exec_dt(),
        "earliest_exec_dt": earliest_exec_dt(),
    }


# ── 진단 (4단계 체크리스트) — mno-ltv-monitor _build_diagnostics 패턴 미러 ─────
# 대시보드가 실제로 쓰는 필수 컬럼 (이게 마트에 있어야 화면이 그려짐)
REQUIRED_COLUMNS = [
    "exec_dt", "exec_ym", "mkt_div_org_nm", "device_group",
    "sub_model", "storage", "sim_only", "scrb_type", "sales_cnt",
]
_GW_ENVS = {"auth_key": ["auth_key", "AUTH_KEY", "DATA_GATEWAY_AUTH_KEY"],
            "user_id": ["user_id", "USER_ID", "DATA_GATEWAY_USER_ID"],
            "app_name": ["app_name", "APP_NAME", "DATA_GATEWAY_APP_NAME"],
            "database": ["DATABASE", "database", "DATA_GATEWAY_DATABASE"]}


def _stage(key, label, status, detail="", **extra) -> dict:
    return {"key": key, "label": label, "status": status, "detail": detail, **extra}


def diagnostics() -> dict:
    """4단계 진단: 환경변수 → Gateway 조회 → 컬럼 검증 → 메모리 캐시.
    각 단계 status: ok | failed | in_progress | pending | skipped. 실패 시 detail에 풀 에러."""
    mock = use_mock()
    df = _CACHE["df"]
    err = _CACHE["error"]
    stages = []

    # 1. 환경변수 (Polaris Data Gateway 4종: auth_key/user_id/app_name/database)
    if mock:
        stages.append(_stage("env_config", "환경변수 설정", "skipped",
                             "mock 모드 — auth_key 미설정 또는 USE_MOCK=1 (Gateway 미사용)"))
    else:
        miss = [name for name, keys in _GW_ENVS.items() if not _env(*keys)]
        if miss:
            stages.append(_stage("env_config", "환경변수 설정", "failed",
                                 f"누락: {', '.join(miss)} — Polaris ENV_VARS에 주입 필요",
                                 missing=miss))
        else:
            stages.append(_stage("env_config", "환경변수 설정", "ok",
                                 "auth_key / user_id / app_name / database 설정됨",
                                 source_table=source_table()))

    # 2. Gateway 조회 (= 마트 reachable + 적재 성공)
    if df is not None:
        stages.append(_stage("athena_fetch", "Gateway 조회", "ok",
                             f"마트 reachable · {len(df):,}행 적재됨",
                             rows=int(len(df)), source_table=source_table()))
    elif err:
        stages.append(_stage("athena_fetch", "Gateway 조회", "failed", err,
                             source_table=source_table()))
    else:
        stages.append(_stage("athena_fetch", "Gateway 조회", "in_progress",
                             "startup 적재 진행 중… (잠시 후 재확인)"))

    # 3. 컬럼 검증
    if df is not None:
        missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
        present = len(REQUIRED_COLUMNS) - len(missing)
        if missing:
            stages.append(_stage("schema_validation", "컬럼 검증", "failed",
                                 f"{present}/{len(REQUIRED_COLUMNS)} 인식 · 누락: {', '.join(missing)}",
                                 missing=missing, required=REQUIRED_COLUMNS,
                                 total_columns=int(len(df.columns))))
        else:
            stages.append(_stage("schema_validation", "컬럼 검증", "ok",
                                 f"필수 {len(REQUIRED_COLUMNS)}개 모두 인식 (전체 {len(df.columns)}컬럼)",
                                 required=REQUIRED_COLUMNS, total_columns=int(len(df.columns))))
    elif err:
        stages.append(_stage("schema_validation", "컬럼 검증", "skipped", "fetch 실패로 미실행"))
    else:
        stages.append(_stage("schema_validation", "컬럼 검증", "pending", "fetch 대기"))

    # 4. 메모리 캐시
    if df is not None:
        mn = mx = None
        if "exec_dt" in df.columns and len(df):
            s = df["exec_dt"].dropna().astype(str)
            if len(s):
                mn, mx = s.min(), s.max()
        stages.append(_stage("memory_cache", "메모리 캐시", "ok",
                             f"{len(df):,}행 · exec_dt {mn}~{mx}",
                             rows=int(len(df)), min_exec_dt=mn, max_exec_dt=mx,
                             loaded_at=_CACHE["loaded_at"].isoformat(timespec="seconds")
                             if _CACHE["loaded_at"] else None,
                             months=len(available_exec_yms())))
    elif err:
        stages.append(_stage("memory_cache", "메모리 캐시", "failed", "적재 실패"))
    else:
        stages.append(_stage("memory_cache", "메모리 캐시", "pending", "적재 대기"))

    statuses = [s["status"] for s in stages]
    overall = ("failed" if "failed" in statuses
               else "loading" if ("in_progress" in statuses or "pending" in statuses)
               else "ok")
    return {"overall": overall, "data_source": data_source(), "mock": mock,
            "source_table": source_table(), "stages": stages}


# ── 정규화 / 조회 헬퍼 ────────────────────────────────────────────────────────
_NUMERIC = ["sales_cnt", "subscriber_cnt", "agency_cnt"]


def _normalize(df: pd.DataFrame) -> pd.DataFrame:
    """필수 컬럼 보강 + 숫자 캐스팅 (NULL→0). 마트는 v3.3에서 NULL 처리됨."""
    if "exec_ym" not in df.columns and "exec_dt" in df.columns:
        df["exec_ym"] = df["exec_dt"].astype(str).str.slice(0, 6)
    for c in _NUMERIC:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0).astype("int64")
    return df


def available_exec_yms() -> list[str]:
    df = _CACHE["df"]
    if df is None or "exec_ym" not in df.columns:
        return []
    return sorted(str(x) for x in df["exec_ym"].dropna().unique())


def latest_exec_ym() -> str | None:
    yms = available_exec_yms()
    return yms[-1] if yms else None


def latest_exec_dt() -> str | None:
    df = _CACHE["df"]
    if df is None or "exec_dt" not in df.columns or len(df) == 0:
        return None
    s = df["exec_dt"].dropna().astype(str)
    return str(s.max()) if len(s) else None


def earliest_exec_dt() -> str | None:
    df = _CACHE["df"]
    if df is None or "exec_dt" not in df.columns or len(df) == 0:
        return None
    s = df["exec_dt"].dropna().astype(str)
    return str(s.min()) if len(s) else None


# ── mock DataFrame (실제 마트 컬럼 부분집합) ──────────────────────────────────
HQS = ["수도권", "PS&M", "제휴", "부산", "서부", "대구", "중부", "기업사업본부", "TDS"]
DEVICE_GROUPS = ["SIMonly", "S26", "IP17", "A17", "ZFlip7", "ZFold7", "Wide8", "Etc"]
_SUBMODEL = {
    "S26": [("Base", "256"), ("Base", "512"), ("플러스", "256"), ("울트라", "256"), ("울트라", "512")],
    "IP17": [("Base", "256"), ("PRO", "256"), ("MAX", "256"), ("MAX", "512"), ("AIR", "256")],
}


def _seed(*p) -> float:
    return int(hashlib.md5("|".join(map(str, p)).encode()).hexdigest()[:8], 16) / 0xFFFFFFFF


def _recent_yms(n: int) -> list[str]:
    today = date.today()
    y, m = today.year, today.month
    out = []
    for _ in range(n):
        out.append(f"{y}{m:02d}")
        m -= 1
        if m == 0:
            y, m = y - 1, 12
    return sorted(out)


# 가입유형 분포(가중치). SIMonly군은 대부분 010신규/MNP 성향으로 살짝 다르게.
_SCRB = [("MNP", 0.45), ("기변", 0.33), ("신규", 0.14), ("010신규", 0.08)]
_SCRB_SIM = [("010신규", 0.5), ("MNP", 0.3), ("신규", 0.15), ("기변", 0.05)]


def _emit_day(rows: list, exec_dt: str, ym: str, base: float) -> None:
    """특정 일자(또는 월 대표일)의 hq×group×sku×가입유형 행 생성."""
    for hq in HQS:
        hq_scale = 0.5 + _seed("hq", hq) * 1.5
        cd = f"D{abs(hash(hq)) % 9000 + 1000}"
        for g in DEVICE_GROUPS:
            gpop = 0.4 + _seed("g", g) * 1.6
            mix = _SCRB_SIM if g == "SIMonly" else _SCRB
            for sub, sto in _SUBMODEL.get(g, [("", "")]):
                noise = 0.5 + _seed("c", exec_dt, hq, g, sub, sto)
                cnt = round(base * hq_scale * gpop * noise)
                if cnt <= 0:
                    continue
                for st, w in mix:                       # 가입유형별로 분해
                    c = round(cnt * w)
                    if c <= 0:
                        continue
                    rows.append({
                        "exec_dt": exec_dt, "exec_ym": ym,
                        "mkt_div_org_nm": hq, "mkt_div_org_cd": cd,
                        "device_group": g, "sub_model": sub, "storage": sto,
                        "sim_only": "SIM only" if g == "SIMonly" else "N",
                        "scrb_type": st,
                        "sales_cnt": c, "subscriber_cnt": round(c * 0.97),
                    })


def _mock_df() -> pd.DataFrame:
    """결정론적 mock. 최근 ~90일은 **일별** 그레인(시점 비교 검증용),
    그 이전 달은 **월별**(1일 대표) 그레인. 최신 데이터일 = 어제."""
    ref = date.today() - timedelta(days=1)   # 어제 = 최신 데이터일
    rows: list = []
    daily_months = set()
    d = ref - timedelta(days=89)             # 최근 90일 일별
    while d <= ref:
        ym = d.strftime("%Y%m")
        daily_months.add(ym)
        _emit_day(rows, d.strftime("%Y%m%d"), ym, base=3.0)
        d += timedelta(days=1)
    for ym in _recent_yms(WINDOW_MONTHS):     # 과거 달(일별 미포함): 월 1일 1행
        if ym in daily_months:
            continue
        _emit_day(rows, f"{ym}01", ym, base=90.0)
    return pd.DataFrame(rows)
