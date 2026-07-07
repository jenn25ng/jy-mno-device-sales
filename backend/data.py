"""데이터 계층 — 메모리 캐시 패턴.

startup에 마트를 최근 WINDOW_MONTHS(기본 13)개월만(exec_ym 파티션 필터) Athena에서
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
_CACHE: dict = {"df": None, "sku_full": None, "loaded_at": None, "source": None, "error": None}

WINDOW_MONTHS = int(os.getenv("DATA_WINDOW_MONTHS", "13"))


def _window_start_ym() -> str:
    """조회 윈도우 시작월(YYYYMM) = 오늘 기준 (WINDOW_MONTHS-1)개월 전. 파티션 프루닝용."""
    today = date.today()
    y, m = today.year, today.month
    m -= (WINDOW_MONTHS - 1)
    while m <= 0:
        m += 12
        y -= 1
    return f"{y}{m:02d}"


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
# 메인 로드는 device_group 그레인까지만 (펫네임/서브모델/용량 제외 → 행수 ~7배↓, 504 방지).
# raw_series_nm 세부(SKU 드릴다운)는 클릭 시 sku_rows()로 온디맨드 조회.
_FETCH_DIMS = ["exec_dt", "exec_ym", "mkt_div_org_nm", "device_group",
               "sim_only", "scrb_type"]
# SKU 드릴다운 온디맨드 조회용 차원 (특정 device_group·기간만)
_SKU_DIMS = ["raw_series_nm", "sub_model", "storage", "mkt_div_org_nm", "scrb_type"]


def _query_gateway() -> pd.DataFrame:
    """Polaris Data Gateway로 마트 조회 (auth_key 인증, output location 불필요).
    SELECT * 대신 대시보드 그레인으로 projection+집계 → 행수 급감(적재 속도/메모리 개선)."""
    from backend.data_gateway import DataGatewayClient
    dims = ", ".join(_FETCH_DIMS)
    start_ym = _window_start_ym()   # 최근 WINDOW_MONTHS(기본 13)개월만 — exec_ym 파티션 필터
    sql = (f"SELECT {dims}, SUM(sales_cnt) AS sales_cnt "
           f"FROM {source_table()} WHERE exec_ym >= '{start_ym}' GROUP BY {dims}")
    log.info("Gateway fetch: %s", sql)
    rows = DataGatewayClient().run_query(sql)   # page_size=1000 (Gateway 최대 한도)
    return pd.DataFrame(rows)


def load_mart() -> pd.DataFrame:
    """startup 1회 호출. Gateway(or mock) → DataFrame 메모리 저장.
    메인 df는 device_group 그레인(코스). mock은 상세(펫네임)도 sku_full에 보관해 SKU 온디맨드에 사용."""
    src = data_source()
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
    return _CACHE["df"]


def sku_rows(group: str, start: str | None = None, end: str | None = None) -> pd.DataFrame:
    """특정 device_group의 SKU 세부(raw_series_nm×sub_model×storage×본부×가입유형) 온디맨드 조회.
    mock: 메모리 상세 df 필터. gateway: 마트에 targeted 쿼리(작은 결과)."""
    full = _CACHE.get("sku_full")
    if full is not None:                                              # mock
        d = full[full["device_group"].astype(str) == str(group)]
        if start and end:
            ds = d["exec_dt"].astype(str)
            d = d[(ds >= str(start)) & (ds <= str(end))]
        cols = [c for c in _SKU_DIMS if c in d.columns] + ["sales_cnt"]
        return d[cols].copy()
    # gateway — 해당 단말군·기간만 집계 (결과 수백행 규모)
    from backend.data_gateway import DataGatewayClient
    dims = ", ".join(_SKU_DIMS)
    g = str(group).replace("'", "''")
    where = [f"device_group = '{g}'", f"exec_ym >= '{_window_start_ym()}'"]
    if start and end:
        where.append(f"exec_dt BETWEEN '{start}' AND '{end}'")
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
    overall = ("failed" if "failed" in statuses
               else "loading" if ("in_progress" in statuses or "pending" in statuses)
               else "ok")
    return {"overall": overall, "data_source": data_source(), "mock": mock,
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
    """본부명 정규화 — 접미사 변형(마케팅본부/사업본부/띄어쓰기 등)에 견디도록 판매 본부 '접두'로 매칭.
    예: '수도권마케팅본부'/'수도권 마케팅본부'/'수도권본부' → '수도권'. 매칭 없으면 원본(→화이트리스트에서 제외)."""
    s = str(name).strip()
    if s in HQS:
        return s
    key = s.replace(" ", "")
    for b in HQS:
        if key.startswith(b.replace(" ", "")):
            return b
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
HQS = ["수도권", "부산", "대구", "서부", "중부", "PS&M", "제휴", "기업사업본부", "TDS"]
DEVICE_GROUPS = ["S26", "IP17", "Foldable7", "A17", "Quantum6", "Wide", "StyleFolder2", "SIMonly", "Etc"]
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
_SERIES_OF = {"S26": "갤럭시 S26", "IP17": "아이폰 17", "A17": "갤럭시 A17",
              "Quantum6": "갤럭시 퀀텀6", "StyleFolder2": "스타일폴더2", "Etc": "기타모델"}
_GROUP_DEVICES = {
    "SIMonly": [("갤럭시 S26", "울트라", "256"), ("갤럭시 S26", "기본", "256"),
                ("아이폰 17", "PRO", "256"), ("아이폰 17", "기본", "128"),
                ("갤럭시 A17", "기본", "128"), ("갤럭시 퀀텀6", "기본", "128")],
    "Foldable7": [("갤럭시 Z플립7", "", "256"), ("갤럭시 Z플립7 FE", "", "256"),
                  ("갤럭시 Z폴드7", "", "256"), ("갤럭시 Z폴드7", "", "512")],
    "Wide": [("갤럭시 와이드8", "", "128"), ("갤럭시 와이드9", "", "128")],
}


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
