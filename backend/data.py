"""데이터 계층 — 메모리 캐시 패턴.

startup에 마트를 최근 WINDOW_MONTHS(기본 13)개월만(exec_ym 파티션 필터) Athena에서
한 번 읽어 pandas DataFrame으로 메모리에 보관. 모든 화면 인터랙션(탭/필터/기간)은
`get_df()`로 이 메모리를 슬라이스·집계 → Athena 재호출 없음.

실제 Athena 접근은 awswrangler(`wr.athena.read_sql_query`)를 lazy import 하므로,
awswrangler 미설치 로컬/USE_MOCK/ATHENA_OUTPUT_LOCATION 미설정 시 자동으로
결정론적 mock DataFrame을 사용해 화면 개발이 가능합니다.

마트 스키마: sandbox_db_max.device_sales_summary_daily3 (56 컬럼) — 차원이 이미
사전 집계됨(device_group, mkt_div_org_nm, sub_model, storage, sim_only, scrb_type …).
"""
from __future__ import annotations

import os
import logging
import hashlib
import threading
from datetime import datetime, date, timedelta

import pandas as pd

log = logging.getLogger(__name__)

# ── 메모리 캐시 ────────────────────────────────────────────────────────────────
_CACHE: dict = {"df": None, "sku_full": None, "loaded_at": None, "source": None,
                "error": None, "loading": False}

WINDOW_MONTHS = int(os.getenv("DATA_WINDOW_MONTHS", "13"))
DATA_START_YM = os.getenv("DATA_START_YM", "202501")   # 고정 시작월(하한) — 프론트 MIN_DATE=2025-01-01과 정합


def _window_start_ym() -> str:
    """조회 윈도우 시작월(YYYYMM) = min(오늘-(WINDOW_MONTHS-1)개월, DATA_START_YM).
    DATA_START_YM(기본 202501)까지 항상 포함 → 롤링이 그보다 늦어도 2025-01부터 적재."""
    today = date.today()
    y, m = today.year, today.month
    m -= (WINDOW_MONTHS - 1)
    while m <= 0:
        m += 12
        y -= 1
    return min(f"{y}{m:02d}", DATA_START_YM)


def _window_yms() -> list[str]:
    """윈도우 시작월(_window_start_ym)부터 이번 달까지 모든 YYYYMM (오름차순)."""
    start = _window_start_ym()
    today = date.today()
    y, m = int(start[:4]), int(start[4:6])
    out = []
    while (y, m) <= (today.year, today.month):
        out.append(f"{y}{m:02d}")
        m += 1
        if m == 13:
            y, m = y + 1, 1
    return out


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
    tbl = _env("MART_TABLE_NAME", "mart_table_name", default="device_sales_summary_daily3")
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
# 메인 로드는 device_group 그레인까지만 (펫네임/서브모델/용량 제외 → 행수 ~7배↓, 504 방지).
# raw_series_nm 세부(SKU 드릴다운)는 클릭 시 sku_rows()로 온디맨드 조회.
_FETCH_DIMS = ["exec_dt", "exec_ym", "mkt_div_org_nm", "device_group",
               "sim_only", "scrb_type", "chnl_l", "agree_type"]   # chnl_l=판매채널, agree_type=약정유형
# SKU 드릴다운 온디맨드 조회용 차원 (특정 device_group·기간만)
_SKU_DIMS = ["raw_series_nm", "sub_model", "storage", "mkt_div_org_nm", "scrb_type"]


def _query_gateway() -> pd.DataFrame:
    """Polaris Data Gateway로 마트 조회 (auth_key 인증, output location 불필요).
    SELECT * 대신 대시보드 그레인으로 projection+집계 → 행수 급감(적재 속도/메모리 개선).
    ⚠️ Gateway가 큰 결과에서 일부 행을 흘리는(truncation) 현상 대응:
    exec_ym(파티션) 단위로 월별 쪼개 조회(각 조각이 한도 아래) → 합치고, 전체 COUNT로 완결성 검증."""
    from concurrent.futures import ThreadPoolExecutor
    from backend.data_gateway import DataGatewayClient, GatewayConfig
    cfg = GatewayConfig.from_env()        # env 1회 확인 후 스레드별 클라이언트에 공유
    client = DataGatewayClient(cfg)
    dims = ", ".join(_FETCH_DIMS)
    months = _window_yms()   # DATA_START_YM(2025-01)부터 이번 달까지 — 파티션 단위로 분할 조회
    order = ", ".join(str(i) for i in range(1, len(_FETCH_DIMS) + 2))  # 모든 컬럼 정렬(결정적 페이징)
    STEP = 900                            # <1000 → 각 조회가 단일 페이지 → 페이지 경계 유실 0

    def _fetch_month(ym: str) -> pd.DataFrame:
        """한 달치 집계를 next_token 페이지네이션으로 '1회 실행' 조회(빠름).
        run_query/get_all_results가 빈-페이지 재시도로 경계 유실을 복구. 그래도 실제 그룹수보다
        모자라면 → OFFSET/LIMIT(무손실)로 자동 폴백. (구: 페이지마다 집계 재실행 = O(n²)로 느림)
        스레드별 Session(클라이언트)로 병렬 실행."""
        c = DataGatewayClient(cfg)        # 스레드 전용 requests.Session (동시 조회 안전)
        base = (f"SELECT {dims}, SUM(sales_cnt) AS sales_cnt "
                f"FROM {source_table()} WHERE exec_ym = '{ym}' GROUP BY {dims}")
        rows = c.run_query(base)                           # ★ next_token 단일 실행(집계 1회)
        try:                                               # 실제 그룹 수와 대조 → 유실 감지
            exp = int(c.run_query(f"SELECT COUNT(*) AS n FROM ({base})")[0]["n"])
        except Exception:
            exp = len(rows)
        if len(rows) < exp:                                # next_token 유실 → OFFSET 무손실 폴백
            log.warning("월 %s next_token 유실(%d/%d) → OFFSET 폴백", ym, len(rows), exp)
            rows, off = [], 0
            while True:
                page = c.run_query(f"SELECT * FROM ({base}) ORDER BY {order} OFFSET {off} LIMIT {STEP}")
                rows.extend(page)
                if len(page) < STEP:
                    break
                off += STEP
        log.info("Gateway 월 %s 적재 완료: %d행", ym, len(rows))
        return pd.DataFrame(rows)

    # 월별 조회를 병렬화 — 각 조회가 Athena start→poll→results 왕복(수 초)이라
    # 순차로 ~90회면 7~8분. 월 단위 동시 실행으로 벽시계 시간을 '가장 느린 한 달'로 단축(1~2분).
    workers = min(6, len(months)) or 1
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="gw-month") as ex:
        frames = [f for f in ex.map(_fetch_month, months) if len(f)]
    df = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()

    try:                                  # 완결성 확인 로그 — 이제 경계 유실 0이라 일치해야 정상
        start_ym = _window_start_ym()
        exp = int(client.run_query(
            f"SELECT COUNT(*) AS n FROM (SELECT {dims} FROM {source_table()} "
            f"WHERE exec_ym >= '{start_ym}' GROUP BY {dims})")[0]["n"])
        (log.error if len(df) < exp else log.info)(
            "Gateway 적재(월별 병렬 LIMIT/OFFSET): %d행 / 기대 %d", len(df), exp)
    except Exception:
        log.info("Gateway 적재(월별 병렬 LIMIT/OFFSET): %d행", len(df))
    return df


def load_mart() -> pd.DataFrame:
    """startup 1회 호출. Gateway(or mock) → DataFrame 메모리 저장.
    메인 df는 device_group 그레인(코스). mock은 상세(펫네임)도 sku_full에 보관해 SKU 온디맨드에 사용."""
    src = data_source()
    _CACHE["loading"] = True               # 적재 진행 중 — 프런트가 "갱신 중" 표시
    try:
        if src == "mock":
            full = _normalize(_mock_df())                              # 상세(펫네임 포함)
            df = (full.groupby(_FETCH_DIMS, as_index=False)["sales_cnt"].sum()
                  if len(full) else full)                              # 코스 메인
            sku_full = full
        else:
            df = _normalize(_query_gateway())                         # Gateway는 코스만 적재
            sku_full = None                                            # SKU는 sku_rows()가 온디맨드 조회
        _CACHE.update(df=df, sku_full=sku_full, loaded_at=datetime.now(), source=src, error=None)
        log.info("마트 적재 완료: %s행 (source=%s)", len(df), src)
    except Exception as e:  # startup에서 죽지 않도록
        log.exception("마트 적재 실패")
        _CACHE.update(error=f"{type(e).__name__}: {e}")
        raise
    finally:
        _CACHE["loading"] = False
    return _CACHE["df"]


def sku_rows(group: str, start: str | None = None, end: str | None = None,
             *, channel: str | None = None, agree_type: str | None = None) -> pd.DataFrame:
    """특정 device_group의 SKU 세부(raw_series_nm×sub_model×storage×본부×가입유형) 온디맨드 조회.
    mock: 메모리 상세 df 필터. gateway: 마트에 targeted 쿼리(작은 결과).
    channel(chnl_l)·agree_type: 전역 필터를 SKU 드릴다운에도 반영."""
    full = _CACHE.get("sku_full")
    if full is not None:                                              # mock
        d = full[full["device_group"].astype(str) == str(group)]
        if start and end:
            ds = d["exec_dt"].astype(str)
            d = d[(ds >= str(start)) & (ds <= str(end))]
        if channel and channel != "전체" and "chnl_l" in d.columns:
            d = d[d["chnl_l"].astype(str) == str(channel)]
        if agree_type and agree_type != "전체" and "agree_type" in d.columns:
            d = d[d["agree_type"].astype(str) == str(agree_type)]
        cols = [c for c in _SKU_DIMS if c in d.columns] + ["sales_cnt"]
        return d[cols].copy()
    # gateway — 해당 단말군·기간·(채널·약정)만 집계 (결과 수백행 규모)
    from backend.data_gateway import DataGatewayClient
    dims = ", ".join(_SKU_DIMS)
    g = str(group).replace("'", "''")
    where = [f"device_group = '{g}'", f"exec_ym >= '{_window_start_ym()}'"]
    if start and end:
        where.append(f"exec_dt BETWEEN '{start}' AND '{end}'")
    if channel and channel != "전체":
        where.append(f"chnl_l = '{str(channel).replace(chr(39), chr(39) * 2)}'")
    if agree_type and agree_type != "전체":
        where.append(f"agree_type = '{str(agree_type).replace(chr(39), chr(39) * 2)}'")
    sql = (f"SELECT {dims}, SUM(sales_cnt) AS sales_cnt FROM {source_table()} "
           f"WHERE {' AND '.join(where)} GROUP BY {dims}")
    log.info("Gateway SKU fetch: %s", sql)
    return _normalize(pd.DataFrame(DataGatewayClient().run_query(sql)))


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


def refresh_async() -> dict:
    """재적재를 백그라운드 스레드로 트리거하고 즉시 반환.
    월별 LIMIT/OFFSET 적재가 수 분 걸려 동기 응답은 ALB 60초 타임아웃(504)에 걸리므로,
    프런트는 이 호출로 트리거만 하고 /api/diagnostics 폴링(loading 플래그)으로 완료를 감지한다.
    이미 적재 중이면 새로 시작하지 않음."""
    if _CACHE.get("loading"):
        return {"ok": True, "started": False, "loading": True, "reason": "already loading"}
    _CACHE["loading"] = True                        # 스레드 시작 전 즉시 표시(폴링 레이스 방지)

    def _worker():
        try:
            load_mart()                             # 내부에서 loading=True 재설정 후 finally에서 False
        except Exception:
            log.exception("비동기 재적재 실패")

    threading.Thread(target=_worker, name="mart-refresh", daemon=True).start()
    return {"ok": True, "started": True, "loading": True}


def cache_meta() -> dict:
    df = _CACHE["df"]
    return {
        "source": _CACHE["source"] or data_source(),
        "rows": int(len(df)) if df is not None else 0,
        "loading": bool(_CACHE.get("loading")),
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
    "sim_only", "scrb_type", "sales_cnt",
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
    loading = bool(_CACHE.get("loading"))                  # 재적재 진행 중(이전 데이터 있어도)
    overall = ("failed" if "failed" in statuses
               else "loading" if (loading or "in_progress" in statuses or "pending" in statuses)
               else "ok")
    return {"overall": overall, "loading": loading, "data_source": data_source(), "mock": mock,
            "source_table": source_table(), "stages": stages}


# ── 정규화 / 조회 헬퍼 ────────────────────────────────────────────────────────
_NUMERIC = ["sales_cnt", "subscriber_cnt", "agency_cnt"]


def _normalize(df: pd.DataFrame) -> pd.DataFrame:
    """필수 컬럼 보강 + 숫자 캐스팅 (NULL→0) + 판매 본부 외 조직 제거. 마트는 v3.3에서 NULL 처리됨."""
    if "exec_ym" not in df.columns and "exec_dt" in df.columns:
        df["exec_ym"] = df["exec_dt"].astype(str).str.slice(0, 6)
    for c in _NUMERIC:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0).astype("int64")
    df = _filter_hqs(df)
    return df


def _canon_hq(name) -> str:
    """본부명 정규화 — 마트 org명 '접두'로 판매 본부 표시명 매칭 (_HQ_PREFIX).
    예: '수도권마케팅담당'→'수도권', '유통사업부'→'PS&M', 'MNO AI마케팅'→'TDS'. 매칭 없으면 원본(→화이트리스트 제외)."""
    s = str(name).strip()
    if s in HQS:
        return s
    key = s.replace(" ", "")
    for hq, prefixes in _HQ_PREFIX.items():
        if any(key.startswith(p.replace(" ", "")) for p in prefixes):
            return hq
    return s


def _filter_hqs(df: pd.DataFrame) -> pd.DataFrame:
    """본부명 정규화(접두 매칭) 후 판매 9개 본부(HQS) 외 조직 행은 적재 직후 전부 제거.
    실마트 mkt_div_org_nm에 '#'/'Blank'/'CV추진실(가상)'/'Channel&Device담당'/
    'Connectivity사업'/'Product&Brand본부' 등 비판매·스태프·가상 조직이 섞여 들어옴 →
    화이트리스트(HQS)로 걸러 모든 탭·집계가 판매 본부만 보게 함. (mock은 HQS만이라 no-op)"""
    if "mkt_div_org_nm" not in df.columns:
        return df
    df["mkt_div_org_nm"] = df["mkt_div_org_nm"].map(_canon_hq)   # 접미사 정규화 먼저
    org = df["mkt_div_org_nm"]
    keep = org.isin(HQS)
    n_drop = int((~keep).sum())
    if n_drop:
        vc = org[~keep].value_counts()
        log.info("판매 본부 외 조직 %d행 제외 (%d종): %s", n_drop, int(len(vc)),
                 ", ".join(f"{k or '(공백)'}={int(v)}" for k, v in vc.items()))
    return df.loc[keep].reset_index(drop=True)


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
# 판매 본부 10개 (표시명 = MAMF 리포트 라벨). 마트 org명이 다른 본부는 _HQ_PREFIX로 흡수.
# ⚠️ PS&M = 마트 '유통사업부', TDS = 마트 'MNO AI마케팅', AIR서비스 = 'air서비스본부'.
# 10개 합 = 388,052(2026-05, ≈전체). Connectivity·Product&Brand(각 소량)는 비판매(제외).
HQS = ["수도권", "부산", "대구", "서부", "중부", "PS&M", "제휴", "기업사업본부", "TDS", "AIR서비스"]
_HQ_PREFIX = {                       # 표시명 → 마트 mkt_div_org_nm 접두(공백 제거 기준)
    "수도권": ["수도권"], "부산": ["부산"], "대구": ["대구"], "서부": ["서부"], "중부": ["중부"],
    "PS&M": ["유통"], "제휴": ["제휴"], "기업사업본부": ["기업사업본부"], "TDS": ["MNOAI"],
    "AIR서비스": ["air서비스", "AIR서비스"],
}
DEVICE_GROUPS = ["S26", "S25", "IP17", "IP16", "Foldable7", "Quantum6", "Wide", "A17", "StyleFolder2", "SIMonly", "Etc"]   # GORDER와 동일 순서
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


# 가입유형 분포(가중치) — 실마트 taxonomy: MNOMNP/MVNOMNP/기기변경/신규/010신규.
# (MNP 전체 = MNOMNP+MVNOMNP) SIMonly군은 010신규/MNP 성향으로 살짝 다르게.
_SCRB = [("MNOMNP", 0.32), ("MVNOMNP", 0.13), ("기기변경", 0.33), ("신규", 0.14), ("010신규", 0.08)]
_SCRB_SIM = [("010신규", 0.5), ("MNOMNP", 0.22), ("MVNOMNP", 0.08), ("신규", 0.15), ("기기변경", 0.05)]


# mock series명 — 단일-series 군은 고정, 여러 기기 섞인 군은 _GROUP_DEVICES(series, sub, storage)
_SERIES_OF = {"S26": "갤럭시 S26", "S25": "갤럭시 S25", "IP17": "아이폰 17", "IP16": "아이폰 16",
              "A17": "갤럭시 A17", "Quantum6": "갤럭시 퀀텀6", "StyleFolder2": "스타일폴더2", "Etc": "기타모델"}
_GROUP_DEVICES = {
    "SIMonly": [("갤럭시 S26", "울트라", "256"), ("갤럭시 S26", "기본", "256"),
                ("아이폰 17", "PRO", "256"), ("아이폰 17", "기본", "128"),
                ("갤럭시 A17", "기본", "128"), ("갤럭시 퀀텀6", "기본", "128")],
    "Foldable7": [("갤럭시 Z플립7", "", "256"), ("갤럭시 Z플립7 FE", "", "256"),
                  ("갤럭시 Z폴드7", "", "256"), ("갤럭시 Z폴드7", "", "512")],
    "Wide": [("갤럭시 와이드8", "", "128"), ("갤럭시 와이드9", "", "128")],
}


# 판매채널 그룹(마트 chnl_l = dsnet_chnl_grp_nm) — 가중 분포. mock은 조합별 결정론적 배정.
_CHANNELS = [("소매", 0.55), ("도매", 0.20), ("특판", 0.15), ("비즈", 0.10)]
# 약정유형(마트 agree_type = 원천 agrmt_cl_nm) — 실값: 선택약정/지원금약정(+무약정)
_AGREES = [("선택약정", 0.55), ("지원금약정", 0.35), ("무약정", 0.10)]


def _pick_w(r: float, table) -> str:
    acc = 0.0
    for name, w in table:
        acc += w
        if r <= acc:
            return name
    return table[-1][0]


def _pick_channel(r: float) -> str:
    return _pick_w(r, _CHANNELS)


def _emit_day(rows: list, exec_dt: str, ym: str, base: float) -> None:
    """특정 일자(또는 월 대표일)의 hq×group×기기×가입유형 행 생성."""
    for hq in HQS:
        hq_scale = 0.5 + _seed("hq", hq) * 1.5
        cd = f"D{abs(hash(hq)) % 9000 + 1000}"
        for g in DEVICE_GROUPS:
            gpop = 0.4 + _seed("g", g) * 1.6
            mix = _SCRB_SIM if g == "SIMonly" else _SCRB
            variants = _GROUP_DEVICES.get(g) or \
                [(_SERIES_OF.get(g, g), sub, sto) for sub, sto in _SUBMODEL.get(g, [("", "")])]
            for series, sub, sto in variants:
                noise = 0.5 + _seed("c", exec_dt, hq, g, series, sub, sto)
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
                        "device_group": g, "raw_series_nm": series,
                        "sub_model": sub, "storage": sto,
                        "sim_only": "SIM only" if g == "SIMonly" else "N",
                        "scrb_type": st,
                        "chnl_l": _pick_channel(_seed("ch", hq, g, series, st)),
                        "agree_type": _pick_w(_seed("ag", hq, g, st), _AGREES),
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
    ref_ym = ref.strftime("%Y%m")
    for ym in _recent_yms(WINDOW_MONTHS):     # 과거 달(일별 미포함): 월 1일 1행
        if ym in daily_months or ym > ref_ym:  # 일별 커버·미래(ref 이후) 달 제외
            continue
        _emit_day(rows, f"{ym}01", ym, base=90.0)
    return pd.DataFrame(rows)
