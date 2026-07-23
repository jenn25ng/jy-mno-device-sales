"""집계 — 메모리 DataFrame → 6탭 brief(dict). 전부 pandas groupby (메모리 in/out).

프런트(frontend/index.html)가 소비하는 JSON 형태를 유지:
  meta / overview / sku{S26,IP17} / by_hq / matrix / alerts

시점(period) 비교: overview만 period 날짜 윈도우로 재집계하고,
나머지 탭(sku/by_hq/matrix/alerts)은 기준월(exec_ym) 기준 그대로 유지.
period 윈도우 기준일(ref) = 해당 월(또는 전체)에서 가장 최신 exec_dt.
"""
from __future__ import annotations

import os
from datetime import datetime, date, timedelta

import pandas as pd

from backend.data import HQS as CANON_HQS, DEVICE_GROUPS as CANON_GROUPS

SKU_GROUPS = ("S26", "IP17")          # SKU 탭 보유 단말군
_GLABEL = {"SIMonly": "SIMonly군", "S26": "S26군", "S25": "S25군", "IP17": "IP17군",
           "IP16": "IP16군", "A17": "A17/16군", "Foldable7": "폴더블7군", "Quantum6": "퀀텀6군",
           "Wide": "와이드군", "StyleFolder2": "스타일폴더2", "Etc": "기타"}
def _gl(g) -> str:
    return _GLABEL.get(g, str(g))
SCRB_ORDER = ["신규", "MNOMNP", "MVNOMNP", "기기변경", "MNP", "기변", "010신규"]   # 가입유형 표시 순서(실마트 우선)
CHANNEL_ORDER = ["소매", "도매", "특판", "비즈"]   # 판매채널 그룹(chnl_l=dsnet_chnl_grp_nm) 표시 순서
AGREE_ORDER = ["선택약정", "지원금약정", "무약정"]   # 약정유형(agree_type=agrmt_cl_nm) 표시 순서
# B2C only(전역 필터) = 6 지역본부만. 나머지 4(제휴·기업사업본부·TDS·AIR서비스)는 비B2C라 제외.
B2C_HQS = ["수도권", "부산", "대구", "서부", "중부", "PS&M"]
WORKDAY_MIN_SALES = int(os.getenv("WORKDAY_MIN_SALES", "10"))   # 전일 비교 = '운영일'(그날 total 판매 ≥ 이 값)
MNP_TYPES = {"MNOMNP", "MVNOMNP", "MNP"}          # MNP 전체 = MNO MNP + MVNO MNP (+mock "MNP")
_SCRB_ALIAS = {"MNP_ALL": MNP_TYPES, "기기변경": {"기기변경", "기변"}}


def _scrb_set(sel) -> set[str] | None:
    """가입유형 선택값 → 매칭할 scrb_type 집합. None이면 필터 없음(전체)."""
    if not sel or str(sel) == "전체":
        return None
    return _SCRB_ALIAS.get(str(sel), {str(sel)})
ALERT_THRESH = {"urgent": 5, "warn": 3, "info": 1.5}   # |과다/과소 지수(p.p)| 임계 (현실화·튜닝 가능)

COMPARE_LABEL = {"none": "없음", "prev_day": "전일", "prev_weekday": "전주 동요일",
                 "prev_month": "전월 동기간", "prev_year": "작년 동기간", "custom": "직접설정"}


def _order(values, canon) -> list[str]:
    present = set(values)
    out = [c for c in canon if c in present]
    out += sorted(v for v in present if v not in canon)
    return out


def _pct(part, whole) -> float:
    return round(part / whole * 100, 1) if whole else 0.0


def _variant_label(row) -> str:                        # 서브모델+용량 (군 내 SKU 변형)
    parts = []
    for x in (row.get("sub_model"), row.get("storage")):
        if pd.isna(x):                                 # NULL(None/NaN)은 제외 (실데이터 sub_model/storage 미지정)
            continue
        s = str(x).strip()
        if s and s.lower() not in ("-", "none", "nan"):
            parts.append(s)
    return " ".join(parts)


def _to_date(s: str) -> date:
    s = str(s)
    return date(int(s[:4]), int(s[4:6]), int(s[6:8]))


def _add_months(d: date, n: int) -> date:
    import calendar
    m = d.month - 1 + n
    y = d.year + m // 12
    m = m % 12 + 1
    return date(y, m, min(d.day, calendar.monthrange(y, m)[1]))


def _shift(d: date, compare_to: str) -> date:
    if compare_to == "prev_day":
        return d - timedelta(days=1)
    if compare_to == "prev_weekday":
        return d - timedelta(days=7)
    if compare_to == "prev_month":
        return _add_months(d, -1)
    if compare_to == "prev_year":
        return _add_months(d, -12)
    return d


def _resolve_compare(start, end, compare_to, compare_start=None, compare_end=None, op_days=None):
    """비교 기간 (cs, ce) 반환.
    - custom: 직접입력값. none: None.
    - prev_day: ⭐ '워킹데이 기준 전일' — end 이전의 가장 최근 운영일(op_days, 그날 total 판매 ≥ 임계).
      비운영일(휴무·공휴일 등 판매 ~0)을 건너뜀. op_days 없으면 달력 -1로 폴백.
    - 그 외(전주동요일/전월동기간/작년동기간): _shift 시프트."""
    if not compare_to or compare_to == "none":
        return None
    if compare_to == "custom":
        if compare_start and compare_end:
            return str(compare_start), str(compare_end)
        return None
    if compare_to == "prev_day":
        pe = None
        if op_days:
            prior = [d for d in op_days if d < str(end)]      # end 이전의 운영일들
            pe = prior[-1] if prior else None
        if pe is None:                                        # 폴백: 달력 전일
            pe = _shift(_to_date(str(end)), "prev_day").strftime("%Y%m%d")
        length = (_to_date(str(end)) - _to_date(str(start))).days   # 선택기간 길이 유지
        cs = (_to_date(pe) - timedelta(days=length)).strftime("%Y%m%d")
        return cs, pe
    cs = _shift(_to_date(start), compare_to).strftime("%Y%m%d")
    ce = _shift(_to_date(end), compare_to).strftime("%Y%m%d")
    return cs, ce


def _operating_days(df_all, threshold: int = WORKDAY_MIN_SALES) -> list[str]:
    """운영일(오름차순) — 그날 total 판매 ≥ threshold. 전일 비교의 워킹데이 판정용.
    ⚠️ 필터(가입유형/채널/약정) 걸기 前 전체 total로 판정(운영일은 날짜 고유 속성)."""
    if df_all is None or not len(df_all) or "exec_dt" not in df_all.columns:
        return []
    s = df_all.groupby(df_all["exec_dt"].astype(str))["sales_cnt"].sum()
    return sorted(d for d, v in s.items() if v >= threshold)


def _delta(cur: int, prev: int) -> dict:
    ab = int(cur) - int(prev)
    pct = round(ab / prev * 100, 1) if prev else None
    return {"abs": ab, "pct": pct}


def _overview(df: pd.DataFrame, hqs, groups) -> dict:
    """KPI + 단말군별 + 본부별 100% 누적 (주어진 df 윈도우 기준)."""
    total = int(df["sales_cnt"].sum())
    g_sum = df.groupby("device_group")["sales_cnt"].sum()
    by_group = sorted(
        ({"group": g, "count": int(g_sum.get(g, 0)), "share": _pct(int(g_sum.get(g, 0)), total),
          "sim_only": g == "SIMonly"} for g in groups),
        key=lambda x: x["count"], reverse=True)
    piv = df.pivot_table(index="mkt_div_org_nm", columns="device_group",
                         values="sales_cnt", aggfunc="sum", fill_value=0)
    stacked = []
    for hq in hqs:
        gv = {g: int(piv.loc[hq, g]) if (hq in piv.index and g in piv.columns) else 0
              for g in groups}
        stacked.append({"hq": hq, "total": sum(gv.values()), "groups": gv})
    # 가입유형별(010신규/MNP/기변/신규)
    by_scrb = []
    if "scrb_type" in df.columns:
        st_sum = df.groupby("scrb_type")["sales_cnt"].sum()
        st_order = _order(st_sum.index, SCRB_ORDER)
        by_scrb = sorted(
            ({"scrb_type": s, "count": int(st_sum.get(s, 0)), "share": _pct(int(st_sum.get(s, 0)), total)}
             for s in st_order),
            key=lambda x: x["count"], reverse=True)
    return {"kpis": {"total_sales": total, "top3": by_group[:3]},
            "by_group": by_group, "hq_group_stacked": stacked, "by_scrb_type": by_scrb}


def build_sku(rows, group: str, hqs, scrb_type: str | None = None) -> dict:
    """단말군 1개의 SKU 세부 — /api/sku 온디맨드용. rows=sku_rows() 결과(펫네임 포함)."""
    empty = {"total": 0, "top_sku": None, "top_hq": None, "by_sku": [], "detail": []}
    if rows is None or len(rows) == 0:
        return empty
    g_rows = rows.copy()
    g_rows["sales_cnt"] = pd.to_numeric(g_rows["sales_cnt"], errors="coerce").fillna(0).astype(int)
    sel_set = _scrb_set(scrb_type)
    if sel_set is not None and "scrb_type" in g_rows.columns:
        g_rows = g_rows[g_rows["scrb_type"].astype(str).isin(sel_set)]
    if len(g_rows) == 0:
        return empty
    g_rows["_series"] = (g_rows["raw_series_nm"].astype(str).str.strip()
                         if "raw_series_nm" in g_rows.columns else group)
    g_rows["_variant"] = g_rows.apply(_variant_label, axis=1)
    g_rows["sku"] = g_rows["_series"].fillna("") + "\u200b" + g_rows["_variant"].fillna("")  # 유니크키
    sv = g_rows.drop_duplicates("sku").set_index("sku")[["_series", "_variant"]].to_dict("index")
    disp = lambda k: (" ".join(x for x in (sv[k]["_series"], sv[k]["_variant"]) if x).strip()
                      or sv[k]["_series"] or group)
    g_total = int(g_rows["sales_cnt"].sum())
    sku_sum = g_rows.groupby("sku")["sales_cnt"].sum().sort_values(ascending=False)
    by_sku = [{"sku": disp(k), "series": sv[k]["_series"], "variant": sv[k]["_variant"],
               "count": int(c), "share": _pct(int(c), g_total)} for k, c in sku_sum.items()]
    hq_sum = g_rows.groupby("mkt_div_org_nm")["sales_cnt"].sum()
    top_hq = hq_sum.idxmax() if len(hq_sum) else None
    piv = g_rows.pivot_table(index="sku", columns="mkt_div_org_nm",
                             values="sales_cnt", aggfunc="sum", fill_value=0)
    detail = []
    for k in sku_sum.index:
        hq_counts = {hq: int(piv.loc[k, hq]) if (k in piv.index and hq in piv.columns) else 0
                     for hq in hqs}
        detail.append({"sku": disp(k), "series": sv[k]["_series"], "variant": sv[k]["_variant"],
                       "hq_counts": hq_counts, "total": sum(hq_counts.values())})
    return {"total": g_total, "top_sku": by_sku[0]["sku"] if by_sku else None,
            "top_hq": top_hq, "by_sku": by_sku, "detail": detail}


def build_brief(df_all: pd.DataFrame, start: str | None = None, end: str | None = None,
                *, scrb_type: str | None = None, channel: str | None = None,
                agree_type: str | None = None, compare_to: str | None = None,
                compare_start: str | None = None, compare_end: str | None = None,
                b2c_only: bool = False,
                data_source: str = "mock") -> dict:
    """[start,end] 기간(YYYYMMDD) 기준 6탭 brief — 전 탭이 동일 기간을 공유(전역).
    start/end 미지정 시 최신 월로 폴백. (시점/비교 overview는 build_overview 담당.)"""
    if df_all is None or len(df_all) == 0:
        return _empty(start, end, data_source)

    df_all = df_all.copy()
    df_all["sales_cnt"] = pd.to_numeric(df_all["sales_cnt"], errors="coerce").fillna(0).astype(int)
    op_days = _operating_days(df_all)                     # 운영일(전일 비교용) — 필터 前 전체 total 기준

    # 가입유형 필터(전 탭 공통) — MNP_ALL은 MNO+MVNO 합산
    sel_set = _scrb_set(scrb_type)
    if sel_set is not None and "scrb_type" in df_all.columns:
        df_all = df_all[df_all["scrb_type"].astype(str).isin(sel_set)]

    # 판매채널 필터(전 탭 공통) — chnl_l 그룹명
    channels = (_order(df_all["chnl_l"].dropna().astype(str).unique(), CHANNEL_ORDER)
                if "chnl_l" in df_all.columns else [])
    if channel and channel != "전체" and "chnl_l" in df_all.columns:
        df_all = df_all[df_all["chnl_l"].astype(str) == str(channel)]

    # 약정유형 필터(전 탭 공통) — agree_type
    agree_types = (_order(df_all["agree_type"].dropna().astype(str).unique(), AGREE_ORDER)
                   if "agree_type" in df_all.columns else [])
    if agree_type and agree_type != "전체" and "agree_type" in df_all.columns:
        df_all = df_all[df_all["agree_type"].astype(str) == str(agree_type)]

    # B2C only 필터(전역) — 6 지역본부만(제휴·기업사업본부·TDS·AIR서비스 제외)
    if b2c_only and "mkt_div_org_nm" in df_all.columns:
        df_all = df_all[df_all["mkt_div_org_nm"].isin(B2C_HQS)]

    hqs = _order(df_all["mkt_div_org_nm"].dropna().unique(), CANON_HQS)     # 축은 전체 윈도우 기준
    groups = _order(df_all["device_group"].dropna().unique(), CANON_GROUPS)

    dser = df_all["exec_dt"].astype(str)
    if start and end:                                # 기간 슬라이스
        df = df_all[(dser >= str(start)) & (dser <= str(end))].copy()
    else:                                            # 폴백: 최신 월
        yms = sorted(str(x) for x in df_all["exec_ym"].dropna().unique())
        ym = yms[-1] if yms else ""
        df = df_all[df_all["exec_ym"].astype(str) == ym].copy()
        start = (ym + "01") if ym else None
        end = str(df["exec_dt"].astype(str).max()) if len(df) else None

    month_g_sum = df.groupby("device_group")["sales_cnt"].sum()
    month_total = int(df["sales_cnt"].sum())
    company_share = {g: _pct(int(month_g_sum.get(g, 0)), month_total) for g in groups}

    overview = _overview(df, hqs, groups)

    # ── 단말군 × 일자별 추이(꺾은선) — 전사 + 본부별. 윈도우: 기간 2일+면 기간, 단일일이면 최근 30일 ──
    daily_group_all = {"dates": [], "groups": {}}
    tdf = df.iloc[0:0]
    if start and end:
        ws, we = _to_date(str(start)), _to_date(str(end))
        if (we - ws).days < 2:
            ws = we - timedelta(days=29)
        tws, twe = ws.strftime("%Y%m%d"), we.strftime("%Y%m%d")
        tdf = df_all[(dser >= tws) & (dser <= twe)]
        daily_group_all = _daily_group_series(tdf, groups)

    # ── SKU는 메인 로드에서 제외(펫네임=행수 폭증) → 드릴다운 시 /api/sku 온디맨드 ──
    sku_tabs = {}   # build_sku()가 요청 시 단말군별로 생성

    # ── 비교기간(E): 본부×단말군 비교 스냅샷 (전역 비교 필터가 켜졌을 때) ──
    cmp_rng = _resolve_compare(start, end, compare_to, compare_start, compare_end, op_days=op_days)
    cmp_hq_group = None
    if cmp_rng is not None:
        cs, ce = cmp_rng
        cdf = df_all[(dser >= cs) & (dser <= ce)]
        cmp_hq_group = {}
        for hq in hqs:
            hs = cdf[cdf["mkt_div_org_nm"] == hq].groupby("device_group")["sales_cnt"].sum()
            cmp_hq_group[hq] = {g: int(hs.get(g, 0)) for g in groups}

    # ── 본부별 포트폴리오 + 과다/과소 지수 (월 기준) ──
    by_hq = []
    for hq in hqs:
        h_rows = df[df["mkt_div_org_nm"] == hq]
        h_total = int(h_rows["sales_cnt"].sum())
        h_sum = h_rows.groupby("device_group")["sales_cnt"].sum()
        portfolio = []
        for g in groups:
            c = int(h_sum.get(g, 0))
            sh = _pct(c, h_total)
            item = {"group": g, "count": c, "share_in_hq": sh,
                    "share_company": company_share.get(g, 0.0),
                    "over_index": round(sh - company_share.get(g, 0.0), 1)}
            if cmp_hq_group is not None:                      # 비교기간 대비 증감
                item["delta"] = _delta(c, cmp_hq_group[hq].get(g, 0))
            portfolio.append(item)
        portfolio.sort(key=lambda x: x["count"], reverse=True)
        entry = {"hq": hq, "total": h_total, "portfolio": portfolio,
                 "daily_group_series": _daily_group_series(tdf[tdf["mkt_div_org_nm"] == hq], groups)}
        if cmp_hq_group is not None:                          # 급증/급감 단말군 하이라이트
            diffs = [(p["group"], p["count"] - cmp_hq_group[hq].get(p["group"], 0)) for p in portfolio]
            up = max(diffs, key=lambda x: x[1]); dn = min(diffs, key=lambda x: x[1])
            entry["total_delta"] = _delta(h_total, sum(cmp_hq_group[hq].values()))
            entry["movers"] = {"up": {"group": up[0], "abs": up[1]},
                               "down": {"group": dn[0], "abs": dn[1]}}
        by_hq.append(entry)

    # ── 매트릭스 (월 기준) ──
    mpiv = df.pivot_table(index="mkt_div_org_nm", columns="device_group",
                          values="sales_cnt", aggfunc="sum", fill_value=0)
    cells = []
    for hq in hqs:
        h_total = int(mpiv.loc[hq].sum()) if hq in mpiv.index else 0
        for g in groups:
            c = int(mpiv.loc[hq, g]) if (hq in mpiv.index and g in mpiv.columns) else 0
            cells.append({"hq": hq, "group": g, "count": c, "ratio_in_hq": _pct(c, h_total)})

    # ── 단말별 분석 탭 (by_hq의 대칭: 단말군 → 본부 분해) ──
    by_group = _by_group_block(df, tdf, mpiv, groups, hqs, month_g_sum, company_share, cmp_hq_group)

    alerts, alert_daily = build_alerts(df, by_hq, sku_tabs, groups, company_share, end)

    # 알림 탭 '일별 판매 추이' 차트는 선택 기간(하루/구간)과 무관하게
    # 선택일이 속한 '월' 전체의 일별 판매를 보여준다. (알림 목록은 선택 기간 그대로)
    month_ym = str(end)[:6] if end else None
    if month_ym and "exec_dt" in df_all.columns and "exec_ym" in df_all.columns:
        mdf = df_all[df_all["exec_ym"].astype(str) == month_ym]
        if len(mdf):
            mds = mdf.groupby(mdf["exec_dt"].astype(str))["sales_cnt"].sum().sort_index()
            alert_daily = [{"date": str(d), "total": int(v)} for d, v in zip(mds.index, mds.values)]

    return {
        "meta": {"exec_ym": str(end)[:6] if end else None, "range": {"start": start, "end": end},
                 "generated_at": datetime.now().isoformat(timespec="seconds"),
                 "data_source": data_source, "device_groups": groups, "hqs": hqs,
                 "compare_to": compare_to or "none",
                 "compare_label": COMPARE_LABEL.get(compare_to or "none", "없음"),
                 "compare_range": ({"start": cmp_rng[0], "end": cmp_rng[1]} if cmp_rng else None),
                 "channels": channels, "channel": (channel or "전체"),
                 "agree_types": agree_types, "agree_type": (agree_type or "전체"),
                 "b2c_only": bool(b2c_only),
                 "unknown_groups": sorted(g for g in groups if g not in CANON_GROUPS)},
        "overview": overview, "sku": sku_tabs, "by_hq": by_hq,
        "by_group": by_group,                           # 단말별 분석 탭(단말군 → 본부)
        "daily_group_series": daily_group_all,          # 전사(본부 '전체' 선택 시 차트용)
        "matrix": {"hqs": hqs, "groups": groups, "cells": cells},
        "alerts": alerts, "alert_daily": alert_daily,
    }


def build_alerts(df, by_hq, sku_tabs, groups, company_share, fallback_dt):
    """룰 기반 알림(LLM 미사용) + 일별 판매 시리즈. fallback_dt=일별 없을 때 대표일(기간 종료일).
    설명 문구는 트리거 차원(기여 상위 본부/편중 SKU 등)을 문장 템플릿에 채워 조립.
    반환: (items, daily). item={level,category,cat_label,group,title,detail,note,date}."""
    items = []
    daily = []
    last_dt = str(fallback_dt or "")

    # ── 일별 판매 추이 + 판매량 전일대비 급증/급감(판매량 카테고리) ──
    if "exec_dt" in df.columns and len(df):
        ds = df.groupby(df["exec_dt"].astype(str))["sales_cnt"].sum().sort_index()
        dates = [str(d) for d in ds.index]
        vals = [int(v) for v in ds.values]
        daily = [{"date": d, "total": v} for d, v in zip(dates, vals)]
        if dates:
            last_dt = dates[-1]
        hqday = df.pivot_table(index=df["exec_dt"].astype(str), columns="mkt_div_org_nm",
                               values="sales_cnt", aggfunc="sum", fill_value=0)
        sales_items = []
        for i in range(1, len(dates)):
            cur, prev = vals[i], vals[i - 1]
            if prev <= 0:
                continue
            pct = (cur - prev) / prev * 100
            lvl = ("urgent" if abs(pct) >= 15 else "warn" if abs(pct) >= 8
                   else "info" if abs(pct) >= 5 else None)
            if not lvl:
                continue
            up = pct > 0
            drv = ""
            try:  # 기여 상위 2개 본부 추출 → 문구 조립
                diff = (hqday.loc[dates[i]] - hqday.loc[dates[i - 1]]).sort_values(ascending=not up)
                tops = [h for h in diff.index[:2] if str(h).strip()]
                if tops:
                    drv = f"{'·'.join(tops)} 채널 {'동시 급증' if up else '동반 급감'}. "
            except Exception:
                pass
            sales_items.append({"_mag": abs(pct),
                "level": lvl, "category": "sales", "cat_label": "판매량", "group": None,
                "title": f"전사 판매 {'급증' if up else '급감'}",
                "detail": f"{cur:,}건 (전일 {prev:,}건 대비 {pct:+.1f}%)",
                "note": drv + ("월말 수요/프로모션 집중 효과 추정." if up else "수요 둔화 구간 — 원인 점검 필요."),
                "date": dates[i]})
        sales_items.sort(key=lambda a: -a["_mag"])   # 변동폭 상위만 노출(노이즈 억제)
        for a in sales_items[:6]:
            a.pop("_mag", None)
            items.append(a)

    # ── 본부 편중(과다/과소 지수) — 본부 카테고리 ──
    hq_items = []
    for hq in by_hq:
        for p in hq["portfolio"]:
            oi = abs(p["over_index"])
            lvl = ("urgent" if oi >= ALERT_THRESH["urgent"] else "warn" if oi >= ALERT_THRESH["warn"]
                   else "info" if oi >= ALERT_THRESH["info"] else None)
            if not lvl:
                continue
            over = p["over_index"] > 0
            hq_items.append({"_oi": oi,
                "level": lvl, "category": "hq", "cat_label": "본부", "group": p["group"],
                "title": f"{hq['hq']} · {_gl(p['group'])} 비중 {'과다' if over else '과소'}",
                "detail": f"본부내 {p['share_in_hq']}% vs 전사 {p['share_company']}% ({p['over_index']:+.1f}%)",
                "note": f"{hq['hq']}에서 {_gl(p['group'])} {'집중' if over else '취약'} — 채널 믹스 점검 권장.",
                "date": last_dt})
    hq_items.sort(key=lambda a: -a["_oi"])
    for a in hq_items[:4]:
        a.pop("_oi", None)
        items.append(a)

    # ── SKU 편중(S26/IP17) — SKU 카테고리 ──
    for g in ("S26", "IP17"):
        s = sku_tabs.get(g) or {}
        by_sku = s.get("by_sku") or []
        if by_sku and by_sku[0]["share"] >= 35:
            top = by_sku[0]
            lvl = "warn" if top["share"] >= 45 else "info"
            items.append({
                "level": lvl, "category": "sku", "cat_label": "SKU", "group": g,
                "title": f"{top['sku']} 비중 집중",
                "detail": f"{g}군 내 비중 {top['share']}% ({top['count']:,}건)",
                "note": f"{g}군 내 특정 SKU 편중 심화 — 재고 편중 모니터링 필요.",
                "date": last_dt})

    # ── 단말군 최상위(참고) — 단말군 카테고리 ──
    nong = [g for g in groups if g != "SIMonly"]
    if nong:
        topg = max(nong, key=lambda g: company_share.get(g, 0))
        items.append({
            "level": "info", "category": "group", "cat_label": "단말군", "group": topg,
            "title": f"{_gl(topg)} 전사 비중 최상위",
            "detail": f"전사비중 {company_share.get(topg, 0)}%",
            "note": f"{_gl(topg)}가 전사 단말 판매를 견인 중.",
            "date": last_dt})

    rank = {"urgent": 0, "warn": 1, "info": 2}
    items.sort(key=lambda a: (rank[a["level"]], -int(a["date"] or 0)))
    return items, daily


def _empty(start, end, data_source) -> dict:
    return {
        "meta": {"exec_ym": str(end)[:6] if end else None, "range": {"start": start, "end": end},
                 "generated_at": datetime.now().isoformat(timespec="seconds"),
                 "data_source": data_source, "device_groups": [], "hqs": [], "unknown_groups": []},
        "overview": {"kpis": {"total_sales": 0, "top3": []}, "by_group": [], "hq_group_stacked": []},
        "sku": {}, "by_hq": [], "matrix": {"hqs": [], "groups": [], "cells": []},
        "alerts": [], "alert_daily": [],
    }


# ── 시점 + 비교 overview (전사 개요 탭 전용) ──────────────────────────────────
def _daily_group_series(tdf, groups) -> dict:
    """일별×단말군 판매 시리즈 {dates:[...], groups:{g:[일별값...]}} — 꺾은선용. tdf=일별 윈도우 슬라이스."""
    if tdf is None or not len(tdf):
        return {"dates": [], "groups": {}}
    tg = tdf.pivot_table(index=tdf["exec_dt"].astype(str), columns="device_group",
                         values="sales_cnt", aggfunc="sum", fill_value=0)
    return {"dates": [str(d) for d in tg.index],
            "groups": {g: [int(tg.loc[d, g]) if g in tg.columns else 0 for d in tg.index] for g in groups}}


def _by_group_block(df, tdf, mpiv, groups, hqs, month_g_sum, company_share, cmp_hq_group) -> dict:
    """단말별 분석 탭 데이터 — by_hq의 대칭(단말군 → 본부 분해).
    KPI/도넛/비교는 period(df·mpiv) 기준, 일별 라인/표는 tdf(일별 윈도우) 기준.
    본부내비중 = 본부의 G / 본부 전체판매,  본부간점유비 = 본부의 G / G 전사합 (둘 다 가중).
    반환 {dates:[...], items:[{group, total, company_share, by_hq:[...], daily_total,
                              total_delta?, movers?}, ...]} — dates는 한 번만 실어 페이로드 절약."""
    def _mget(piv, r, c) -> int:
        try:
            return int(piv.loc[r, c])
        except Exception:
            return 0

    has_daily = tdf is not None and len(tdf) > 0
    dates = [str(d) for d in sorted(tdf["exec_dt"].astype(str).unique())] if has_daily else []
    hq_date = (tdf.pivot_table(index="mkt_div_org_nm", columns=tdf["exec_dt"].astype(str),
                               values="sales_cnt", aggfunc="sum", fill_value=0)
               if has_daily else None)

    items = []
    for g in groups:
        g_total = int(month_g_sum.get(g, 0))
        gd = tdf[tdf["device_group"] == g] if has_daily else None
        ghq = (gd.pivot_table(index="mkt_div_org_nm", columns=gd["exec_dt"].astype(str),
                              values="sales_cnt", aggfunc="sum", fill_value=0)
               if (gd is not None and len(gd)) else None)
        daily_total = [int(ghq[d].sum()) if (ghq is not None and d in ghq.columns) else 0 for d in dates]
        dt_by_date = dict(zip(dates, daily_total))

        by_hq = []
        for hq in hqs:
            c = _mget(mpiv, hq, g)                                   # period 판매량
            hq_tot = int(mpiv.loc[hq].sum()) if hq in mpiv.index else 0
            entry = {
                "hq": hq, "count": c,
                "share_of_group": _pct(c, g_total),                 # 본부간점유비(기간 가중)
                "share_in_hq": _pct(c, hq_tot),                     # 본부내비중(기간 가중)
                "daily": [_mget(ghq, hq, d) for d in dates],
                "daily_share_in_hq": [_pct(_mget(ghq, hq, d), _mget(hq_date, hq, d)) for d in dates],
                "daily_share_of_group": [_pct(_mget(ghq, hq, d), dt_by_date.get(d, 0)) for d in dates],
            }
            if cmp_hq_group is not None:                            # 본부별 증감(비교 활성 시 KPI 카드용)
                entry["delta"] = _delta(c, cmp_hq_group[hq].get(g, 0))
            by_hq.append(entry)
        by_hq.sort(key=lambda x: x["count"], reverse=True)          # KPI Top1~3 / 도넛 순서

        item = {"group": g, "total": g_total,
                "company_share": company_share.get(g, 0.0),
                "by_hq": by_hq, "daily_total": daily_total}
        if cmp_hq_group is not None:                                 # 비교 하이라이트
            g_cmp = sum(cmp_hq_group[hq].get(g, 0) for hq in hqs)
            diffs = [(hq, _mget(mpiv, hq, g) - cmp_hq_group[hq].get(g, 0)) for hq in hqs]
            up = max(diffs, key=lambda x: x[1]); dn = min(diffs, key=lambda x: x[1])
            item["total_delta"] = _delta(g_total, g_cmp)
            item["movers"] = {"up": {"hq": up[0], "abs": up[1]},
                              "down": {"hq": dn[0], "abs": dn[1]}}
        items.append(item)
    return {"dates": dates, "items": items}


def build_overview(df_all: pd.DataFrame, start: str, end: str,
                   compare_to: str = "prev_day", *, scrb_type: str | None = None,
                   channel: str | None = None, agree_type: str | None = None,
                   compare_start: str | None = None, compare_end: str | None = None,
                   b2c_only: bool = False,
                   data_source: str = "mock") -> dict:
    """[start,end] 기간 overview + compare_to로 시프트한 비교기간 overview + delta.
    start/end = 'YYYYMMDD'. compare_to ∈ none|prev_day|prev_weekday|prev_month|prev_year.
    scrb_type: 가입유형 필터(신규/MNOMNP/MVNOMNP/기기변경, MNP_ALL=MNO+MVNO). None/'전체'면 전체 합산."""
    if compare_to not in COMPARE_LABEL:
        compare_to = "prev_day"
    meta = {"generated_at": datetime.now().isoformat(timespec="seconds"),
            "data_source": data_source, "device_groups": [], "hqs": [],
            "compare_to": compare_to, "compare_label": COMPARE_LABEL[compare_to],
            "range": {"start": start, "end": end}, "compare_range": None,
            "scrb_types": [], "scrb_type": "전체", "channels": [], "channel": "전체",
            "agree_types": [], "agree_type": "전체", "b2c_only": bool(b2c_only)}
    if df_all is None or len(df_all) == 0:
        return {"meta": meta, "current": {"kpis": {"total_sales": 0, "top3": []},
                "by_group": [], "hq_group_stacked": []}, "compare": None, "delta": None}

    df_all = df_all.copy()
    df_all["sales_cnt"] = pd.to_numeric(df_all["sales_cnt"], errors="coerce").fillna(0).astype(int)
    op_days = _operating_days(df_all)                     # 운영일(전일 비교용) — 필터 前 전체 total 기준

    # 가입유형 필터 — 선택 유형만 남김(기본 = 전체). 유형 목록은 필터 前 전체에서 산출.
    if "scrb_type" in df_all.columns:
        meta["scrb_types"] = _order(df_all["scrb_type"].dropna().astype(str).unique(), SCRB_ORDER)
    sel_set = _scrb_set(scrb_type)
    if sel_set is not None and "scrb_type" in df_all.columns:
        meta["scrb_type"] = "MNP 전체" if str(scrb_type) == "MNP_ALL" else str(scrb_type)
        df_all = df_all[df_all["scrb_type"].astype(str).isin(sel_set)]

    # 판매채널 필터(전역) — chnl_l(그룹명). 목록은 필터 前 산출.
    if "chnl_l" in df_all.columns:
        meta["channels"] = _order(df_all["chnl_l"].dropna().astype(str).unique(), CHANNEL_ORDER)
    if channel and channel != "전체" and "chnl_l" in df_all.columns:
        meta["channel"] = str(channel)
        df_all = df_all[df_all["chnl_l"].astype(str) == str(channel)]

    # 약정유형 필터(전역) — agree_type. 목록은 필터 前 산출.
    if "agree_type" in df_all.columns:
        meta["agree_types"] = _order(df_all["agree_type"].dropna().astype(str).unique(), AGREE_ORDER)
    if agree_type and agree_type != "전체" and "agree_type" in df_all.columns:
        meta["agree_type"] = str(agree_type)
        df_all = df_all[df_all["agree_type"].astype(str) == str(agree_type)]

    # B2C only 필터(전역) — 6 지역본부만(제휴·기업사업본부·TDS·AIR서비스 제외)
    if b2c_only and "mkt_div_org_nm" in df_all.columns:
        df_all = df_all[df_all["mkt_div_org_nm"].isin(B2C_HQS)]

    dser = df_all["exec_dt"].astype(str)
    hqs = _order(df_all["mkt_div_org_nm"].dropna().unique(), CANON_HQS)
    groups = _order(df_all["device_group"].dropna().unique(), CANON_GROUPS)
    meta["device_groups"] = groups
    meta["hqs"] = hqs

    cur_df = df_all[(dser >= start) & (dser <= end)]
    current = _overview(cur_df, hqs, groups)

    # 일별 추이 — 기간이 2일 이상이면 그 기간, 단일일이면 최근 30일(기준일까지)
    ws, we = _to_date(start), _to_date(end)
    if (we - ws).days < 2:
        ws = we - timedelta(days=29)
    tws, twe = ws.strftime("%Y%m%d"), we.strftime("%Y%m%d")
    tdf = df_all[(dser >= tws) & (dser <= twe)]
    ds = tdf.groupby(tdf["exec_dt"].astype(str))["sales_cnt"].sum().sort_index()
    current["daily_series"] = [{"date": d, "sales_cnt": int(c)} for d, c in ds.items()]
    current["daily_window"] = {"start": tws, "end": twe}

    # 단말군 × 일자별 시리즈 (전사개요 꺾은선 그래프용) — 일별 추이 윈도우와 동일 구간
    current["daily_group_series"] = _daily_group_series(tdf, groups)

    compare = delta = None
    cmp_rng = _resolve_compare(start, end, compare_to, compare_start, compare_end, op_days=op_days)
    if cmp_rng is not None:
        cs, ce = cmp_rng
        meta["compare_range"] = {"start": cs, "end": ce}
        cmp_df = df_all[(dser >= cs) & (dser <= ce)]
        cmp_ov = _overview(cmp_df, hqs, groups)
        cmp_groups = {x["group"]: x["count"] for x in cmp_ov["by_group"]}
        compare = {"total_sales": cmp_ov["kpis"]["total_sales"], "by_group": cmp_groups}
        # 비교 하이라이트용 — 본부·가입유형 증감도 계산(시장대비 상대 판정은 프런트)
        cur_hq = {x["hq"]: x["total"] for x in current["hq_group_stacked"]}
        cmp_hq = {x["hq"]: x["total"] for x in cmp_ov["hq_group_stacked"]}
        cur_scrb = {x["scrb_type"]: x["count"] for x in current.get("by_scrb_type", [])}
        cmp_scrb = {x["scrb_type"]: x["count"] for x in cmp_ov.get("by_scrb_type", [])}
        delta = {"total_sales": _delta(current["kpis"]["total_sales"], compare["total_sales"]),
                 "by_group": {x["group"]: _delta(x["count"], cmp_groups.get(x["group"], 0))
                              for x in current["by_group"]},
                 "by_hq": {hq: _delta(cur_hq[hq], cmp_hq.get(hq, 0)) for hq in cur_hq},
                 "by_scrb": {s: _delta(cur_scrb[s], cmp_scrb.get(s, 0)) for s in cur_scrb}}
    return {"meta": meta, "current": current, "compare": compare, "delta": delta}
