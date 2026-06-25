"""데이터 로더 — env 해석 + SQL 빌드 + Gateway fetch (+ mock fallback).

Phase A 스캐폴드 단계: 실제 마트(`sandbox_db_max.device_sales_summary_daily`)가
준비되기 전까지는 auth_key 미설정/USE_MOCK 시 mock 데이터로 동작합니다.
Phase B에서 `_build_query()`와 컬럼 매핑을 실제 스키마에 맞춰 확정합니다.

env 우선순위 (data_gateway.py와 동일 철학): 소문자 > 대문자 > DATA_GATEWAY_* > 디폴트.
"""
from __future__ import annotations

import os
import logging
from datetime import date

log = logging.getLogger(__name__)


# ── env 헬퍼 ──────────────────────────────────────────────────────────────────
def _env_first(keys: list[str], default: str = "") -> tuple[str, str]:
    for k in keys:
        v = (os.getenv(k) or "").strip()
        if v:
            return v, k
    return default, ("default" if default else "missing")


def _read_database() -> tuple[str, str]:
    return _env_first(["database", "DATABASE", "DATA_GATEWAY_DATABASE"], "sandbox_db_max")


def _resolve_source_table() -> str:
    """`SOURCE_TABLE`(db.table) 우선. 없으면 DATABASE + MART_TABLE_NAME 조합.
    (ltv-monitor와 동일한 DATABASE + MART_TABLE_NAME 컨벤션)"""
    st, _ = _env_first(["SOURCE_TABLE", "source_table"])
    if st:
        return st
    db, _ = _read_database()
    tbl, _ = _env_first(["MART_TABLE_NAME", "mart_table_name"],
                        "device_sales_summary_daily")
    if db and tbl:
        return f"{db}.{tbl}"
    return tbl


SOURCE_TABLE = _resolve_source_table()
# summary 마트 단일 모드 (LTV monitor의 raw/summary 이원화 대신 단순화)
MART_MODE = "summary"


def use_mock() -> bool:
    """auth_key 없거나 USE_MOCK 켜져 있으면 mock 모드."""
    if (os.getenv("USE_MOCK") or "").strip().lower() in ("1", "true", "yes"):
        return True
    ak, _ = _env_first(["auth_key", "AUTH_KEY", "DATA_GATEWAY_AUTH_KEY"])
    return not ak


def resolve_data_source() -> str:
    return "mock" if use_mock() else "gateway"


def current_exec_ym() -> str:
    """기준월 YYYYMM. CURRENT_EXEC_YM env 우선, 없으면 전월."""
    v, _ = _env_first(["CURRENT_EXEC_YM", "current_exec_ym"])
    if v and len(v) == 6 and v.isdigit():
        return v
    today = date.today()
    y, m = today.year, today.month
    if m == 1:
        y, m = y - 1, 12
    else:
        m -= 1
    return f"{y}{m:02d}"


# ── SQL 빌드 (Phase B에서 실제 스키마로 확정) ─────────────────────────────────
def _build_query(exec_ym: str | None = None) -> str:
    """집계 마트에서 기준월 데이터 SELECT.

    TODO(Phase B): 실제 컬럼명/파티션키 확정 후 교체.
    summary 마트는 사전 집계되어 있다고 가정 (행=본부×단말군×SKU 단위).
    """
    ym = exec_ym or current_exec_ym()
    return (
        f"SELECT * FROM {_resolve_source_table()} "
        f"WHERE exec_ym = '{ym}'"
    )


# ── fetch ─────────────────────────────────────────────────────────────────────
def fetch_rows(exec_ym: str | None = None) -> list[dict]:
    """Gateway에서 기준월 행을 가져옴. mock 모드면 mock 행 반환."""
    if use_mock():
        from backend.data_pipeline import mock_rows
        log.info("mock 모드 — gateway 호출 생략")
        return mock_rows(exec_ym or current_exec_ym())
    from backend.data_gateway import DataGatewayClient
    sql = _build_query(exec_ym)
    log.info("Gateway fetch: %s", sql[:200])
    client = DataGatewayClient()
    return client.run_query(sql)
