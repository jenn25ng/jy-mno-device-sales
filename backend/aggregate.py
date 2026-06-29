"""집계 — 메모리 DataFrame → 6탭 brief(dict). 전부 pandas groupby (메모리 in/out).

프런트(frontend/index.html)가 소비하는 JSON 형태를 유지:
  meta / overview / sku{S26,IP17} / by_hq / matrix / alerts

시점(period) 비교: overview만 period 날짜 윈도우로 재집계하고,
나머지 탭(sku/by_hq/matrix/alerts)은 기준월(exec_ym) 기준 그대로 유지.
period 윈도우 기준일(ref) = 해당 월(또는 전체)에서 가장 최신 exec_dt.
"""
from __future__ import annotations

from datetime import datetime, date, timedelta

import pandas as pd

from backend.data import HQS as CANON_HQS, DEVICE_GROUPS as CANON_GROUPS

SKU_GROUPS = ("S26", "IP17")          # SKU 탭 보유 단말군
ALERT_THRESH = {"urgent": 12, "warn": 8, "info": 5}   # |과/과소 지수| 임계

COMPARE_LABEL = {"none": "없음", "prev_day": "전일", "prev_weekday": "전주 동요일",
                 "prev_month": "전월 동기간", "prev_year": "작년 동기간"}


def _order(values, canon) -> list[str]:
    present = set(values)
    out = [c for c in canon if c in present]
    out += sorted(v for v in present if v not in canon)
    return out


def _pct(part, whole) -> float:
    return round(part / whole * 100, 1) if whole else 0.0


def _sku_label(row) -> str:
    return " ".join(str(x) for x in (row["device_group"], row.get("sub_model", ""),
                                     row.get("storage", "")) if str(x).strip())


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
    return {"kpis": {"total_sales": total, "top3": by_group[:3]},
            "by_group": by_group, "hq_group_stacked": stacked}


def build_brief(df_all: pd.DataFrame, exec_ym: str | None = None,
                *, data_source: str = "mock") -> dict:
    """기준월(exec_ym) 기준 brief. overview는 해당 월 단순 집계.
    (시점/비교 overview는 build_overview + /api/overview 가 담당.)"""
    if df_all is None or len(df_all) == 0:
        return _empty(exec_ym, data_source)

    df_all = df_all.copy()
    df_all["sales_cnt"] = pd.to_numeric(df_all["sales_cnt"], errors="coerce").fillna(0).astype(int)

    yms = sorted(str(x) for x in df_all["exec_ym"].dropna().unique())
    ym = exec_ym if (exec_ym in yms) else (yms[-1] if yms else None)

    hqs = _order(df_all["mkt_div_org_nm"].dropna().unique(), CANON_HQS)
    groups = _order(df_all["device_group"].dropna().unique(), CANON_GROUPS)

    df = df_all[df_all["exec_ym"].astype(str) == str(ym)].copy()
    month_g_sum = df.groupby("device_group")["sales_cnt"].sum()
    month_total = int(df["sales_cnt"].sum())
    company_share = {g: _pct(int(month_g_sum.get(g, 0)), month_total) for g in groups}

    overview = _overview(df, hqs, groups)

    # ── SKU 탭 (월 기준) ──
    sku_tabs = {}
    for group in SKU_GROUPS:
        g_rows = df[df["device_group"] == group].copy()
        if len(g_rows) == 0:
            sku_tabs[group] = {"total": 0, "top_sku": None, "top_hq": None, "by_sku": [], "detail": []}
            continue
        g_rows["sku"] = g_rows.apply(_sku_label, axis=1)
        g_total = int(g_rows["sales_cnt"].sum())
        sku_sum = g_rows.groupby("sku")["sales_cnt"].sum().sort_values(ascending=False)
        by_sku = [{"sku": s, "count": int(c), "share": _pct(int(c), g_total)} for s, c in sku_sum.items()]
        hq_sum = g_rows.groupby("mkt_div_org_nm")["sales_cnt"].sum()
        top_hq = hq_sum.idxmax() if len(hq_sum) else None
        piv = g_rows.pivot_table(index="sku", columns="mkt_div_org_nm",
                                 values="sales_cnt", aggfunc="sum", fill_value=0)
        detail = []
        for s in sku_sum.index:
            hq_counts = {hq: int(piv.loc[s, hq]) if (s in piv.index and hq in piv.columns) else 0
                         for hq in hqs}
            detail.append({"sku": s, "hq_counts": hq_counts, "total": sum(hq_counts.values())})
        sku_tabs[group] = {"total": g_total, "top_sku": by_sku[0]["sku"] if by_sku else None,
                           "top_hq": top_hq, "by_sku": by_sku, "detail": detail}

    # ── 본부별 포트폴리오 + 과/과소 지수 (월 기준) ──
    by_hq = []
    for hq in hqs:
        h_rows = df[df["mkt_div_org_nm"] == hq]
        h_total = int(h_rows["sales_cnt"].sum())
        h_sum = h_rows.groupby("device_group")["sales_cnt"].sum()
        portfolio = []
        for g in groups:
            c = int(h_sum.get(g, 0))
            sh = _pct(c, h_total)
            portfolio.append({"group": g, "count": c, "share_in_hq": sh,
                              "share_company": company_share.get(g, 0.0),
                              "over_index": round(sh - company_share.get(g, 0.0), 1)})
        portfolio.sort(key=lambda x: x["count"], reverse=True)
        by_hq.append({"hq": hq, "total": h_total, "portfolio": portfolio})

    # ── 매트릭스 (월 기준) ──
    mpiv = df.pivot_table(index="mkt_div_org_nm", columns="device_group",
                          values="sales_cnt", aggfunc="sum", fill_value=0)
    cells = []
    for hq in hqs:
        h_total = int(mpiv.loc[hq].sum()) if hq in mpiv.index else 0
        for g in groups:
            c = int(mpiv.loc[hq, g]) if (hq in mpiv.index and g in mpiv.columns) else 0
            cells.append({"hq": hq, "group": g, "count": c, "ratio_in_hq": _pct(c, h_total)})

    alerts = _alerts(by_hq, ym)

    return {
        "meta": {"exec_ym": ym, "generated_at": datetime.now().isoformat(timespec="seconds"),
                 "data_source": data_source, "device_groups": groups, "hqs": hqs,
                 "available_exec_yms": yms},
        "overview": overview, "sku": sku_tabs, "by_hq": by_hq,
        "matrix": {"hqs": hqs, "groups": groups, "cells": cells}, "alerts": alerts,
    }


def _alerts(by_hq, ym) -> list[dict]:
    out = []
    for hq in by_hq:
        for p in hq["portfolio"]:
            oi = p["over_index"]
            level = ("urgent" if abs(oi) >= ALERT_THRESH["urgent"]
                     else "warn" if abs(oi) >= ALERT_THRESH["warn"]
                     else "info" if abs(oi) >= ALERT_THRESH["info"] else None)
            if not level:
                continue
            direction = "과다" if oi > 0 else "과소"
            out.append({"level": level, "exec_ym": ym, "hq": hq["hq"], "group": p["group"],
                        "over_index": oi,
                        "message": f"{hq['hq']} · {p['group']} 비중 {direction} ({oi:+.1f}p)"})
    rank = {"urgent": 0, "warn": 1, "info": 2}
    out.sort(key=lambda a: (rank[a["level"]], -abs(a["over_index"])))
    return out


def _empty(ym, data_source) -> dict:
    return {
        "meta": {"exec_ym": ym, "generated_at": datetime.now().isoformat(timespec="seconds"),
                 "data_source": data_source, "device_groups": [], "hqs": [],
                 "available_exec_yms": []},
        "overview": {"kpis": {"total_sales": 0, "top3": []}, "by_group": [], "hq_group_stacked": []},
        "sku": {}, "by_hq": [], "matrix": {"hqs": [], "groups": [], "cells": []}, "alerts": [],
    }


# ── 시점 + 비교 overview (전사 개요 탭 전용) ──────────────────────────────────
def build_overview(df_all: pd.DataFrame, start: str, end: str,
                   compare_to: str = "prev_day", *, data_source: str = "mock") -> dict:
    """[start,end] 기간 overview + compare_to로 시프트한 비교기간 overview + delta.
    start/end = 'YYYYMMDD'. compare_to ∈ none|prev_day|prev_weekday|prev_month|prev_year."""
    if compare_to not in COMPARE_LABEL:
        compare_to = "prev_day"
    meta = {"generated_at": datetime.now().isoformat(timespec="seconds"),
            "data_source": data_source, "device_groups": [], "hqs": [],
            "compare_to": compare_to, "compare_label": COMPARE_LABEL[compare_to],
            "range": {"start": start, "end": end}, "compare_range": None}
    if df_all is None or len(df_all) == 0:
        return {"meta": meta, "current": {"kpis": {"total_sales": 0, "top3": []},
                "by_group": [], "hq_group_stacked": []}, "compare": None, "delta": None}

    df_all = df_all.copy()
    df_all["sales_cnt"] = pd.to_numeric(df_all["sales_cnt"], errors="coerce").fillna(0).astype(int)
    dser = df_all["exec_dt"].astype(str)
    hqs = _order(df_all["mkt_div_org_nm"].dropna().unique(), CANON_HQS)
    groups = _order(df_all["device_group"].dropna().unique(), CANON_GROUPS)
    meta["device_groups"] = groups
    meta["hqs"] = hqs

    cur_df = df_all[(dser >= start) & (dser <= end)]
    current = _overview(cur_df, hqs, groups)

    compare = delta = None
    if compare_to != "none":
        cs = _shift(_to_date(start), compare_to).strftime("%Y%m%d")
        ce = _shift(_to_date(end), compare_to).strftime("%Y%m%d")
        meta["compare_range"] = {"start": cs, "end": ce}
        cmp_df = df_all[(dser >= cs) & (dser <= ce)]
        cmp_ov = _overview(cmp_df, hqs, groups)
        cmp_groups = {x["group"]: x["count"] for x in cmp_ov["by_group"]}
        compare = {"total_sales": cmp_ov["kpis"]["total_sales"], "by_group": cmp_groups}
        delta = {"total_sales": _delta(current["kpis"]["total_sales"], compare["total_sales"]),
                 "by_group": {x["group"]: _delta(x["count"], cmp_groups.get(x["group"], 0))
                              for x in current["by_group"]}}
    return {"meta": meta, "current": current, "compare": compare, "delta": delta}
