"""데이터 계층 — 메모리 캐시 패턴.

startup에 마트 전체(최근 24개월, 마트 SQL v3.3에서 이미 윈도잉됨)를 Athena에서
한 번 읽어 pandas DataFrame으로 메모리에 보관. 모든 화면 인터랙션(탭/필터/기간)은
`get_df()`로 이 메모리를 슬라이스·집계 → Athena 재호출 없음.

실제 Athena 접근은 awswrangler(`wr.athena.read_sql_query`)를 lazy import 하므로,
awswrangler 미설치 로컬/USE_MOCK/ATHENA_OUTPUT_LOCATION 미설정 시 자동으로
결정론적 mock DataFrame을 사용해 화면 개발이 가능합니다.

마트 스키마: sandbox_db_max.device_sales_summary_daily (56 컬럼) — 차원이 이미
사전 집계됨(device_group, mkt_div_org_nm, sub_model, storage, sim_only, scrb_type …).
"""
from __future__ import annotations

import os
import logging
import hashlib
from datetime import datetime, date

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
    return _env("DATABASE", "database", default="sandbox_db_max")


def source_table() -> str:
    st = _env("SOURCE_TABLE", "source_table")
    if st:
        return st
    tbl = _env("MART_TABLE_NAME", "mart_table_name", default="device_sales_summary_daily")
    return f"{_database()}.{tbl}"


def use_mock() -> bool:
    if _env("USE_MOCK").lower() in ("1", "true", "yes"):
        return True
    # Athena 출력 위치가 없으면 실 조회 불가 → mock
    return not _env("ATHENA_OUTPUT_LOCATION")


def data_source() -> str:
    return "mock" if use_mock() else "athena"


# ── 적재 ──────────────────────────────────────────────────────────────────────
def _query_athena() -> pd.DataFrame:
    """awswrangler로 마트 조회. 마트 SQL v3.3이 이미 24개월 윈도잉 → SELECT *."""
    import awswrangler as wr  # lazy: 로컬/mock 환경에서 import 강제 안 함
    sql = f"SELECT * FROM {source_table()}"
    log.info("Athena fetch: %s", sql)
    df = wr.athena.read_sql_query(
        sql=sql,
        database=_database(),
        s3_output=os.getenv("ATHENA_OUTPUT_LOCATION"),
        ctas_approach=False,
    )
    return df


def load_mart() -> pd.DataFrame:
    """startup 1회 호출. Athena(or mock) → DataFrame 메모리 저장."""
    src = data_source()
    try:
        df = _mock_df() if src == "mock" else _query_athena()
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
    }


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


def _mock_df() -> pd.DataFrame:
    """결정론적 mock. 월 그레인(집계는 sales_cnt 합이라 그레인 무관)."""
    rows = []
    for ym in _recent_yms(WINDOW_MONTHS):
        for hq in HQS:
            hq_scale = 0.5 + _seed("hq", hq) * 1.5
            for g in DEVICE_GROUPS:
                gpop = 0.4 + _seed("g", g) * 1.6
                variants = _SUBMODEL.get(g, [("", "")])
                for sub, sto in variants:
                    base = 40 * hq_scale * gpop
                    cnt = round(base * (0.5 + _seed("c", ym, hq, g, sub, sto)))
                    if cnt <= 0:
                        continue
                    rows.append({
                        "exec_dt": f"{ym}01",
                        "exec_ym": ym,
                        "mkt_div_org_nm": hq,
                        "mkt_div_org_cd": f"D{abs(hash(hq)) % 9000 + 1000}",
                        "device_group": g,
                        "sub_model": sub,
                        "storage": sto,
                        "sim_only": "SIM only" if g == "SIMonly" else "N",
                        "scrb_type": "MNP",
                        "sales_cnt": cnt,
                        "subscriber_cnt": round(cnt * 0.97),
                    })
    return pd.DataFrame(rows)
