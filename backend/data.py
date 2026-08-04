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
                "error": None, "loading": False, "load_progress": None}

import threading
_LOAD_LOCK = threading.Lock()    # 동시 적재 방지(single-flight) — startup·get_df·refresh가 겹쳐도
                                 # 게이트웨이 전체조회는 한 번에 하나만. 대기 중 이미 적재됐으면 스킵.

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
_SKU_DIMS = ["exec_dt", "raw_series_nm", "sub_model", "storage", "mkt_div_org_nm", "scrb_type", "agree_type", "ext_dim_1"]
# ext_dim_1 = 색상 슬롯(마트 예약 컬럼, 현재 NULL). 폴더블8 색상별 분석용 — 배치가 원천 색상으로 채우면 라이브.
# scrb_type·agree_type = 폴더블8 파이(가입유형/약정) 분해용으로 반환 dim에 포함.


def _query_gateway(months=None) -> pd.DataFrame:
    """Polaris Data Gateway로 마트 조회 (auth_key 인증, output location 불필요).
    SELECT * 대신 대시보드 그레인으로 projection+집계 → 행수 급감(적재 속도/메모리 개선).
    ⚠️ Gateway가 큰 결과에서 일부 행을 흘리는(truncation) 현상 대응:
    exec_ym(파티션) 단위로 월별 쪼개 조회(각 조각이 한도 아래) → 합치고, 전체 COUNT로 완결성 검증."""
    from concurrent.futures import ThreadPoolExecutor
    from backend.data_gateway import DataGatewayClient, GatewayConfig
    cfg = GatewayConfig.from_env()        # env 1회 확인 후 스레드별 클라이언트에 공유
    client = DataGatewayClient(cfg)
    dims = ", ".join(_FETCH_DIMS)
    is_full = months is None                  # months 지정 시 = 증분(그 달만), None = 전체 윈도우
    if months is None:
        months = _window_yms()   # DATA_START_YM(2025-01)부터 이번 달까지 — 파티션 단위로 분할 조회
    import threading
    order = ", ".join(str(i) for i in range(1, len(_FETCH_DIMS) + 2))  # 모든 컬럼 정렬(결정적 페이징)
    STEP = 900                            # 최후 OFFSET 폴백 시 페이지 크기(<1000 → 단일 페이지)
    PAGE = 1000                           # 게이트웨이 첫 페이지 실측 상한(~1010) 근사 — 이 이하면 next_token 미사용 → 무손실
    SPLIT_DIMS = ["mkt_div_org_nm", "scrb_type", "chnl_l"]  # 페이지 초과 시 순차 세분(본부→가입유형→채널)

    # ── 진행률 추적(진단창 표시용) — 월이 하나 끝날 때마다 갱신 ──
    prog_lock = threading.Lock()
    _CACHE["load_progress"] = {"done": 0, "total": len(months), "recent": []}

    def _fetch_slice(c: "DataGatewayClient", where: str, split_dims: list) -> list:
        """where 한 조각을 단일 페이지로 조회. PAGE를 넘겨 유실이 감지되면 OFFSET(재집계) 대신
        split_dims[0]로 **더 잘게 쪼개** 각 하위 조각을 단일 페이지로 재귀 조회(무손실·재집계 없음).
        세분 차원이 소진되면 최후에만 OFFSET 폴백. 조각이 PAGE 이하면 next_token 자체를 안 써 유실 원천 차단."""
        base = (f"SELECT {dims}, SUM(sales_cnt) AS sales_cnt "
                f"FROM {source_table()} WHERE {where} GROUP BY {dims}")
        rows = c.run_query(base)
        if len(rows) < PAGE:                               # 여유롭게 단일 페이지 → 무손실
            return rows
        try:                                               # 페이지 꽉 참 → 유실 가능 → COUNT 대조
            exp = int(c.run_query(f"SELECT COUNT(*) AS n FROM ({base})")[0]["n"])
        except Exception:
            exp = len(rows)
        if len(rows) >= exp:                               # 페이지에 딱 맞게 다 들어옴 → OK
            return rows
        if split_dims:                                     # 유실 → 다음 차원으로 세분(무손실, 재집계 없음)
            dim, rest = split_dims[0], split_dims[1:]
            vals = c.run_query(f"SELECT DISTINCT {dim} AS v FROM {source_table()} WHERE {where}")
            out = []
            for r in vals:
                v = r.get("v")
                cond = f"{dim} IS NULL" if v is None else f"{dim} = '{str(v).replace(chr(39), chr(39) * 2)}'"
                out.extend(_fetch_slice(c, f"{where} AND {cond}", rest))
            log.info("조각(%s) 페이지 초과(%d/%d) → %s %d개로 세분", where, len(rows), exp, dim, len(vals))
            return out
        log.warning("조각(%s) 세분 차원 소진 → OFFSET 최후 폴백(%d/%d)", where, len(rows), exp)
        rows, off = [], 0
        while True:
            page = c.run_query(f"SELECT * FROM ({base}) ORDER BY {order} OFFSET {off} LIMIT {STEP}")
            rows.extend(page)
            if len(page) < STEP:
                break
            off += STEP
        return rows

    def _fetch_month(ym: str) -> pd.DataFrame:
        """월(exec_ym 파티션)을 일(exec_dt) 단위로 쪼개 조회. 무거운 하루는 _fetch_slice가 본부→…
        순으로 더 잘게 세분해 단일 페이지로 처리(폴백 최소화). 스레드별 Session으로 병렬 실행."""
        c = DataGatewayClient(cfg)        # 스레드 전용 requests.Session (동시 조회 안전)
        days = [str(r["d"]) for r in c.run_query(
            f"SELECT DISTINCT exec_dt AS d FROM {source_table()} "
            f"WHERE exec_ym = '{ym}' ORDER BY 1")]
        frames = []
        for d in days:
            r = _fetch_slice(c, f"exec_ym = '{ym}' AND exec_dt = '{d}'", SPLIT_DIMS)
            if r:
                frames.append(pd.DataFrame(r))
        out = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
        with prog_lock:                    # 진행률 갱신(진단창 폴링이 읽어감)
            p = _CACHE.get("load_progress") or {"done": 0, "total": len(months), "recent": []}
            p["done"] += 1
            p["recent"] = (p["recent"] + [f"{ym} · {len(out):,}행 ({len(days)}일)"])[-8:]
            _CACHE["load_progress"] = p
            done = p["done"]
        log.info("Gateway 월 %s 적재 완료: %d행 (%d일 분할) [%d/%d]",
                 ym, len(out), len(days), done, len(months))
        return out

    # 월별 조회를 병렬화 — 각 월이 '일 단위 순차 조회'를 도므로 월끼리 동시에 돌려 벽시계 단축.
    workers = min(6, len(months)) or 1
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="gw-month") as ex:
        frames = [f for f in ex.map(_fetch_month, months) if len(f)]
    df = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    _CACHE["load_progress"] = None                         # 완료 → 진행률 해제

    if is_full:
        try:                              # 완결성 확인 로그 — 전체 로드 때만(증분은 일부 월이라 대조 불가)
            start_ym = _window_start_ym()
            exp = int(client.run_query(
                f"SELECT COUNT(*) AS n FROM (SELECT {dims} FROM {source_table()} "
                f"WHERE exec_ym >= '{start_ym}' GROUP BY {dims})")[0]["n"])
            (log.error if len(df) < exp else log.info)(
                "Gateway 적재(월별 병렬 LIMIT/OFFSET): %d행 / 기대 %d", len(df), exp)
        except Exception:
            log.info("Gateway 적재(월별 병렬 LIMIT/OFFSET): %d행", len(df))
    else:
        log.info("Gateway 증분 적재(%s): %d행", ",".join(months), len(df))
    return df


def load_mart(force: bool = False) -> pd.DataFrame:
    """마트 전체 적재 (Gateway or mock → DataFrame 메모리).
    ⭐ single-flight: 여러 스레드(startup·get_df 지연로드·refresh)가 동시에 불러도 실제 적재는
    한 번만. 락 대기 중 다른 스레드가 이미 적재를 끝냈으면(df 존재) 재적재 스킵 → 게이트웨이
    전체조회가 2·3중으로 중복 실행되는 것 방지. force=True면(refresh 전체) 무조건 재적재.
    메인 df는 device_group 그레인(코스). mock은 상세(펫네임)도 sku_full에 보관해 SKU 온디맨드에 사용."""
    with _LOAD_LOCK:                        # 동시 적재 직렬화
        if not force and _CACHE.get("df") is not None:
            return _CACHE["df"]            # 대기하는 사이 다른 스레드가 이미 적재 완료 → 중복 스킵
        return _load_mart_locked()


def _load_mart_locked() -> pd.DataFrame:
    """_LOAD_LOCK 보유 상태에서 실제 적재 수행 (load_mart 내부 전용)."""
    src = data_source()
    started = datetime.now()                # 적재 시작 시각
    _CACHE["loading"] = True               # 적재 진행 중 — 프런트가 "갱신 중" 표시
    _CACHE["loading_mode"] = "full"        # load_mart = 전체 로드
    _CACHE["load_started_at"] = started
    try:
        if src == "mock":
            full = _normalize(_mock_df())                              # 상세(펫네임 포함)
            df = (full.groupby(_FETCH_DIMS, as_index=False)["sales_cnt"].sum()
                  if len(full) else full)                              # 코스 메인
            sku_full = full
        else:
            df = _normalize(_query_gateway())                         # Gateway는 코스만 적재
            sku_full = None                                            # SKU는 sku_rows()가 온디맨드 조회
        ended = datetime.now(); secs = (ended - started).total_seconds()
        _CACHE.update(df=df, sku_full=sku_full, loaded_at=ended, source=src, error=None,
                      load_secs=secs)
        log.info("마트 적재 완료: %s행 (source=%s) · 소요 %.0f초 (%s→%s)",
                 len(df), src, secs, started.strftime("%H:%M:%S"), ended.strftime("%H:%M:%S"))
    except Exception as e:  # startup에서 죽지 않도록
        log.exception("마트 적재 실패")
        _CACHE.update(error=f"{type(e).__name__}: {e}")
        raise
    finally:
        _CACHE["loading"] = False
    return _CACHE["df"]


def sku_rows(group: str, start: str | None = None, end: str | None = None,
             *, channel: str | None = None, agree_type: str | None = None,
             hq: str | None = None) -> pd.DataFrame:
    """특정 device_group의 SKU 세부(raw_series_nm×sub_model×storage×본부×가입유형) 온디맨드 조회.
    mock: 메모리 상세 df 필터. gateway: 마트에 targeted 쿼리(작은 결과).
    channel(chnl_l)·agree_type: 전역 필터를 SKU 드릴다운에도 반영. hq: 본부 필터(폴더블8 탭 본부칩)."""
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
        if hq and hq != "전체" and "mkt_div_org_nm" in d.columns:
            d = d[d["mkt_div_org_nm"].astype(str) == str(hq)]
        cols = [c for c in _SKU_DIMS if c in d.columns] + ["sales_cnt"]
        return d[cols].copy()
    # gateway — 해당 단말군·기간·(채널·약정·본부)만 집계 (결과 수백행 규모)
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
    if hq and hq != "전체":
        where.append(f"mkt_div_org_nm = '{str(hq).replace(chr(39), chr(39) * 2)}'")
    sql = (f"SELECT {dims}, SUM(sales_cnt) AS sales_cnt FROM {source_table()} "
           f"WHERE {' AND '.join(where)} GROUP BY {dims}")
    log.info("Gateway SKU fetch: %s", sql)
    return _normalize(pd.DataFrame(DataGatewayClient().run_query(sql)))


def get_df() -> pd.DataFrame:
    if _CACHE["df"] is None:
        load_mart()
    return _CACHE["df"]


def _recent_refresh_yms() -> list[str]:
    """증분 재적재 대상 월 — 당월 + 전월 (일 배치 proc_ym>=전월과 정합)."""
    today = date.today()
    cur = f"{today.year}{today.month:02d}"
    py, pm = (today.year, today.month - 1) if today.month > 1 else (today.year - 1, 12)
    return [f"{py}{pm:02d}", cur]


def refresh(full: bool = False) -> dict:
    """마트 메모리 재적재.
    full=False(기본): **증분** — 최근 2개월(당월+전월)만 Gateway 재조회 → 메모리의 그 달만 교체.
      (일 배치가 최근 2개월만 갱신하므로 나머지 달은 재사용 → 재적재 대폭 단축)
    full=True: 전체 재조회(load_mart). ⚠️ 과거 전 기간이 바뀐 경우(예: 색상·용량 소급 적재)엔 full 필수.
    (mock/최초적재/증분 실패 시엔 자동으로 full로 폴백)"""
    src = data_source()
    mode = "full"
    if full:
        load_mart(force=True)                       # 명시적 전체 재적재(색상·용량 소급 등)
    elif _CACHE.get("df") is None or src == "mock":
        load_mart(force=False)                      # 최초 적재 보장(single-flight: startup과 중복 안 됨) / mock
    else:
        recent = _recent_refresh_yms()
        started = datetime.now()
        _CACHE["loading"] = True
        _CACHE["loading_mode"] = "incremental"
        _CACHE["load_started_at"] = started
        try:
            fresh = _normalize(_query_gateway(months=recent))          # 최근 2개월만 재조회
            base = _CACHE["df"]
            keep = base[~base["exec_ym"].astype(str).isin(recent)]     # 그 달들 제거(옛 데이터 유지)
            merged = pd.concat([keep, fresh], ignore_index=True) if len(fresh) else keep
            ended = datetime.now(); secs = (ended - started).total_seconds()
            _CACHE.update(df=merged, loaded_at=ended, error=None, load_secs=secs)
            mode = "incremental(" + ",".join(recent) + ")"
            log.info("증분 재적재 완료: %s개월 → %d행 · 소요 %.0f초 (%s→%s)",
                     recent, len(merged), secs, started.strftime("%H:%M:%S"), ended.strftime("%H:%M:%S"))
        except Exception:
            log.exception("증분 재적재 실패 → 전체 로드 폴백")
            load_mart(force=True); mode = "full(fallback)"
        finally:
            _CACHE["loading"] = False
    return {
        "ok": True, "mode": mode,
        "rows": int(len(_CACHE["df"])) if _CACHE["df"] is not None else 0,
        "source": _CACHE["source"],
        "loaded_at": _CACHE["loaded_at"].isoformat(timespec="seconds")
        if _CACHE["loaded_at"] else None,
    }


def refresh_async(full: bool = False) -> dict:
    """재적재를 백그라운드 스레드로 트리거하고 즉시 반환.
    적재가 수 분 걸려 동기 응답은 ALB 60초 타임아웃(504)에 걸리므로,
    프런트는 이 호출로 트리거만 하고 /api/diagnostics 폴링(loading 플래그)으로 완료를 감지한다.
    full=False(기본): 증분(최근 2개월) / full=True: 전체. 이미 적재 중이면 새로 시작하지 않음."""
    if _CACHE.get("loading"):
        return {"ok": True, "started": False, "loading": True, "reason": "already loading"}
    _CACHE["loading"] = True                        # 스레드 시작 전 즉시 표시(폴링 레이스 방지)
    _CACHE["loading_mode"] = "full" if full else "incremental"

    def _worker():
        try:
            refresh(full=full)                      # 증분/전체 (내부에서 loading 관리)
        except Exception:
            log.exception("비동기 재적재 실패")
        finally:
            _CACHE["loading"] = False

    threading.Thread(target=_worker, name="mart-refresh", daemon=True).start()
    return {"ok": True, "started": True, "loading": True, "full": bool(full)}


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
        p = _CACHE.get("load_progress") or {}
        if p.get("total"):
            stages.append(_stage("athena_fetch", "Gateway 조회", "in_progress",
                                 f"월별 적재 중 · {p['done']}/{p['total']}개월 완료",
                                 done=p["done"], total=p["total"], recent=p.get("recent") or []))
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
        _secs = _CACHE.get("load_secs")
        _st = _CACHE.get("load_started_at")
        _en = _CACHE.get("loaded_at")
        _dur = (f" · 적재 {_st.strftime('%H:%M:%S')}~{_en.strftime('%H:%M:%S')} ({int(_secs // 60)}분 {int(_secs % 60)}초)"
                if (_secs is not None and _st and _en) else "")
        stages.append(_stage("memory_cache", "메모리 캐시", "ok",
                             f"{len(df):,}행 · exec_dt {mn}~{mx}{_dur}",
                             rows=int(len(df)), min_exec_dt=mn, max_exec_dt=mx,
                             loaded_at=_CACHE["loaded_at"].isoformat(timespec="seconds")
                             if _CACHE["loaded_at"] else None,
                             load_started_at=_st.isoformat(timespec="seconds") if _st else None,
                             load_secs=round(_secs) if _secs is not None else None,
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
    return {"overall": overall, "loading": loading,
            "loading_mode": _CACHE.get("loading_mode") if loading else None,  # full | incremental
            "load_progress": _CACHE.get("load_progress") if loading else None,  # {done,total,recent}
            "data_source": data_source(), "mock": mock,
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
DEVICE_GROUPS = ["S26", "S25", "IP17", "IP16", "Foldable8", "Foldable7", "Quantum6", "Wide", "A17", "StyleFolder2", "SIMonly", "Etc"]   # GORDER와 동일 순서. Foldable8=신제품(8/4 출시)
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
    "Foldable8": [("갤럭시Z플립8", "", "256"), ("갤럭시Z플립8", "", "512"),
                  ("갤럭시Z폴드8", "", "256"), ("갤럭시Z폴드8", "", "512"), ("갤럭시Z폴드8", "", "1024"),
                  ("갤럭시Z폴드8울트라", "", "256"), ("갤럭시Z폴드8울트라", "", "512"),
                  ("갤럭시Z폴드8울트라", "", "1024")],
    "Foldable7": [("갤럭시 Z플립7", "", "256"), ("갤럭시 Z플립7 FE", "", "256"),
                  ("갤럭시 Z폴드7", "", "256"), ("갤럭시 Z폴드7", "", "512")],
    "Wide": [("갤럭시 와이드8", "", "128"), ("갤럭시 와이드9", "", "128")],
}


# 폴더블8 mock 색상(모델별) — ext_dim_1 채움. 삼성 공식 마스터의 색상명 기준.
# ⚠️ 실배치는 모델코드(단말코드 A7xx)→색상명 마스터 조인으로 ext_dim_1을 채워야 함(접미 규칙 X).
_FOLD8_COLORS = {
    "플립": [("블랙/검정", 0.40), ("화이트/하양", 0.35), ("라이트핑크", 0.25)],
    "폴드": [("블랙/검정", 0.42), ("화이트/하양", 0.33), ("라이트퍼플", 0.25)],
    "울트라": [("블랙/검정", 0.45), ("바이올렛", 0.32), ("화이트/하양", 0.23)],
}
def _fold8_model(series: str):
    s = str(series or "")
    return "울트라" if "울트라" in s else "플립" if "플립" in s else "폴드" if "폴드" in s else None


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
                color = None                             # ext_dim_1(색상) — 폴더블8만 mock 배정
                if g == "Foldable8":
                    fm = _fold8_model(series)
                    if fm:
                        color = _pick_w(_seed("clr", hq, series, sto), _FOLD8_COLORS[fm])
                for st, w in mix:                       # 가입유형별로 분해
                    c = round(cnt * w)
                    if c <= 0:
                        continue
                    rows.append({
                        "exec_dt": exec_dt, "exec_ym": ym,
                        "mkt_div_org_nm": hq, "mkt_div_org_cd": cd,
                        "device_group": g, "raw_series_nm": series,
                        "sub_model": sub, "storage": sto, "ext_dim_1": color,
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
