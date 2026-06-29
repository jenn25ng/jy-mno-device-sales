"""
Polaris Colab Data Gateway 클라이언트.

start-query → poll → get-results 흐름 + 페이지네이션 + 타입 캐스팅.
가이드: 02920aef-DATA_GATEWAY_VIBE_GUIDE.md

PLACEHOLDER 항목 (환경변수로 교체):
- DATA_GATEWAY_AUTH_KEY   ← 사용자가 사내에서 발급받아 .env에 넣을 자리
- DATA_GATEWAY_USER_ID    ← Polaris Colab user_id (예: 1112917)
- DATA_GATEWAY_APP_NAME   ← 배포 앱 이름 (Polaris 포털 ENV_VARS — env 필수)
"""
from __future__ import annotations

import os
import time
import logging
from dataclasses import dataclass
from typing import Any

import requests

log = logging.getLogger(__name__)

BASE_URL = os.getenv(
    "DATA_GATEWAY_URL",
    "https://polaris-colab.sktelecom.com/api/data-gateway",
)
POLL_INTERVAL_SEC = 2
MAX_POLL_SEC = 30 * 60  # Athena DML 타임아웃
PAGE_SIZE = 1000
REQUEST_TIMEOUT = 30

PLACEHOLDER_AUTH_KEY = "PLACEHOLDER_AUTH_KEY_REPLACE_ME"

# 진행률 콜백 — backend.main이 등록해 STATE.fetch_progress 갱신
_progress_cb = None
def set_progress_callback(fn):
    global _progress_cb
    _progress_cb = fn
def _notify(**kw):
    if _progress_cb:
        try: _progress_cb(**kw)
        except Exception: pass


class DataGatewayError(Exception):
    """Data Gateway API 호출 실패. detail 객체를 그대로 보관."""
    def __init__(self, message: str, *, status: int | None = None, detail: dict | None = None):
        super().__init__(message)
        self.status = status
        self.detail = detail or {}


def _read_env_priority(keys: list[str], default: str = "") -> tuple[str, str]:
    """주어진 env 이름들을 순서대로 시도. 가장 먼저 유효한 값과 그 이름 반환.
    placeholder 값은 무시. 모두 비면 (default, 'default' or 'missing')."""
    for k in keys:
        v = (os.getenv(k) or "").strip()
        if v and v != PLACEHOLDER_AUTH_KEY:
            return v, k
    return default, ("default" if default else "missing")


# 사용자가 Polaris ENV_VARS에 JSON payload와 동일한 소문자(auth_key, user_id, app_name,
# database)를 쓰는 패턴. 우선순위: 소문자 > 대문자 > DATA_GATEWAY_* > 디폴트.
_AUTH_KEY_NAMES = ["auth_key", "AUTH_KEY", "DATA_GATEWAY_AUTH_KEY"]
_USER_ID_NAMES  = ["user_id",  "USER_ID",  "DATA_GATEWAY_USER_ID"]
_APP_NAME_NAMES = ["app_name", "APP_NAME", "DATA_GATEWAY_APP_NAME"]


def _read_auth_key() -> tuple[str, str]:
    """auth_key 값과 출처 (어느 env name에서 읽혔는지)."""
    return _read_env_priority(_AUTH_KEY_NAMES)


def env_sources() -> dict:
    """4가지 핵심 항목의 env 출처 진단. 값은 노출 안 함, 어디서 읽혔는지만.
    default 없음 — 누락 시 source='missing'."""
    _, ak = _read_env_priority(_AUTH_KEY_NAMES)
    _, uid_src = _read_env_priority(_USER_ID_NAMES)
    _, app_src = _read_env_priority(_APP_NAME_NAMES)
    return {
        "auth_key": ak,
        "user_id":  uid_src,
        "app_name": app_src,
    }


def auth_key_status() -> dict:
    """auth_key 진단 정보 (값은 노출하지 않음, 출처만)."""
    val, source = _read_auth_key()
    return {"present": bool(val), "source": source}


@dataclass
class GatewayConfig:
    auth_key: str
    user_id: str
    app_name: str
    app_type: str = "app"

    @classmethod
    def from_env(cls) -> "GatewayConfig":
        """env 필수 — 누락 시 fast-fail (default 없음).
        Polaris 포털 ENV_VARS에 4종 (auth_key / user_id / app_name / database) 주입 필수."""
        auth_key, auth_src = _read_auth_key()
        user_id,  uid_src  = _read_env_priority(_USER_ID_NAMES)
        app_name, app_src  = _read_env_priority(_APP_NAME_NAMES)
        missing = []
        if not auth_key:
            missing.append("auth_key (또는 AUTH_KEY / DATA_GATEWAY_AUTH_KEY)")
        if not user_id:
            missing.append("user_id (또는 USER_ID / DATA_GATEWAY_USER_ID)")
        if not app_name:
            missing.append("app_name (또는 APP_NAME / DATA_GATEWAY_APP_NAME)")
        if missing:
            raise DataGatewayError(
                f"환경변수 누락: {missing}. Polaris 포털 ENV_VARS에 자산화 발급받은 "
                f"auth_key/user_id/app_name/database 4종을 주입하세요 (소문자 권장)."
            )
        log.info("Gateway env resolved: auth_key=%s, user_id=%s, app_name=%s",
                 auth_src, uid_src, app_src)
        return cls(auth_key=auth_key, user_id=user_id, app_name=app_name)


# ── 타입 캐스팅 ───────────────────────────────────────────────────────────────
_INT_TYPES = {"integer", "int", "bigint", "smallint", "tinyint"}
_FLOAT_TYPES = {"double", "float", "real", "decimal"}
_BOOL_TYPES = {"boolean", "bool"}


def cast_value(raw: str | None, col_type: str) -> Any:
    """Athena 결과는 모두 string으로 반환됨 — columns[].type 기준 캐스팅.

    None/빈 문자열은 None으로 통일. 캐스팅 실패는 원본 문자열 유지(데이터 손상 방지).
    """
    if raw is None or raw == "":
        return None
    t = (col_type or "").lower()
    try:
        if t in _INT_TYPES:
            return int(raw)
        if t in _FLOAT_TYPES:
            return float(raw)
        if t in _BOOL_TYPES:
            return raw.lower() in ("true", "t", "1", "yes")
    except (ValueError, TypeError):
        return raw  # 캐스팅 실패 시 원본 유지
    return raw  # varchar/char/date/timestamp 등은 문자열 유지


def cast_rows(columns: list[dict], rows: list[list]) -> list[dict]:
    """rows를 [{col_name: typed_value, ...}, ...] 로 변환."""
    names = [c["name"] for c in columns]
    types = [c.get("type", "varchar") for c in columns]
    return [
        {names[i]: cast_value(v, types[i]) for i, v in enumerate(row)}
        for row in rows
    ]


# ── 클라이언트 ─────────────────────────────────────────────────────────────────
class DataGatewayClient:
    def __init__(self, cfg: GatewayConfig | None = None, base_url: str | None = None):
        self.cfg = cfg or GatewayConfig.from_env()
        self.base = (base_url or BASE_URL).rstrip("/")
        self._session = requests.Session()

    def _post(self, path: str, body: dict) -> dict:
        url = f"{self.base}{path}"
        try:
            r = self._session.post(
                url,
                json=body,
                headers={"Content-Type": "application/json"},
                timeout=REQUEST_TIMEOUT,
            )
        except requests.RequestException as e:
            raise DataGatewayError(f"네트워크 오류 ({path}): {e}") from e
        if not r.ok:
            try:
                detail = r.json().get("detail", {})
            except Exception:
                detail = {"raw": r.text[:500]}
            raise DataGatewayError(
                f"{path} 실패 [{r.status_code}] {detail.get('error_code')}: {detail.get('message') or r.text[:200]}",
                status=r.status_code,
                detail=detail,
            )
        return r.json()

    def start_query(self, sql: str) -> str:
        body = {
            "query_string": sql,
            "user_id": self.cfg.user_id,
            "app_type": self.cfg.app_type,
            "app_name": self.cfg.app_name,
            "auth_key": self.cfg.auth_key,
        }
        resp = self._post("/start-query-execution", body)
        qid = resp["query_execution_id"]
        log.info("Query started qid=%s", qid)
        return qid

    def poll_until_done(self, qid: str, *, interval: int = POLL_INTERVAL_SEC, max_sec: int = MAX_POLL_SEC) -> dict:
        elapsed = 0
        last: dict = {}
        while elapsed < max_sec:
            last = self._post("/get-query-execution", {"query_execution_id": qid})
            status = last.get("status")
            if status == "SUCCEEDED":
                return last
            if status in ("FAILED", "CANCELLED"):
                raise DataGatewayError(
                    f"쿼리 종료 status={status}: {last.get('state_change_reason')}",
                    detail=last,
                )
            time.sleep(interval)
            elapsed += interval
        # 타임아웃 — 명시적 stop
        try:
            self._post("/stop-query-execution", {"query_execution_id": qid})
        except DataGatewayError:
            pass
        raise DataGatewayError(f"쿼리 타임아웃 ({max_sec}s): qid={qid}", detail=last)

    def get_all_results(self, qid: str, *, page_size: int = PAGE_SIZE,
                        on_page=None) -> dict:
        """페이지네이션으로 전체 결과 수집 — {columns, rows} 반환.
        on_page(fetched_so_far) 콜백으로 진행률 통보."""
        columns: list[dict] = []
        all_rows: list[list] = []
        next_token: str | None = None
        page = 0
        while True:
            body: dict = {"query_execution_id": qid, "max_results": page_size}
            if next_token:
                body["next_token"] = next_token
            resp = self._post("/get-query-results", body)
            if not columns:
                columns = resp.get("columns", [])
            page_rows = resp.get("rows", [])
            all_rows.extend(page_rows)
            if on_page:
                try: on_page(len(all_rows))
                except Exception: pass
            next_token = resp.get("next_token")
            page += 1
            if not next_token:
                break
        log.info("Query results qid=%s pages=%d rows=%d", qid, page, len(all_rows))
        return {"columns": columns, "rows": all_rows}

    def run_query(self, sql: str) -> list[dict]:
        """start → poll → results → 타입 캐스팅까지 한 번에. 행 list[dict]를 반환."""
        qid = self.start_query(sql)
        self.poll_until_done(qid)
        res = self.get_all_results(qid)
        return cast_rows(res["columns"], res["rows"])
