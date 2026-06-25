"""집계 — 메모리 DataFrame → 6탭 brief(dict). 전부 pandas groupby (메모리 in/out).

프런트(frontend/index.html)가 소비하는 JSON 형태를 유지:
  meta / overview / sku{S26,IP17} / by_hq / matrix / alerts
"""
from __future__ import annotations

from datetime import datetime

import pandas as pd

from backend.data import HQS as CANON_HQS, DEVICE_GROUPS as CANON_GROUPS

SKU_GROUPS = ("S26", "IP17")          # SKU 탭 보유 단말군
ALERT_THRESH = {"urgent": 12, "warn": 8, "info": 5}   # |과/과소 지수| 임계


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


def build_brief(df_all: pd.DataFrame, exec_ym: str | None = None,
                *, data_source: str = "mock") -> dict:
    if df_all is None or len(df_all) == 0:
        return _empty(exec_ym, data_source)

    yms = sorted(str(x) for x in df_all["exec_ym"].dropna().unique())
    ym = exec_ym if (exec_ym in yms) else (yms[-1] if yms else None)
    df = df_all[df_all["exec_ym"].astype(str) == str(ym)].copy()
    df["sales_cnt"] = pd.to_numeric(df["sales_cnt"], errors="coerce").fillna(0).astype(int)

    hqs = _order(df["mkt_div_org_nm"].dropna().unique(), CANON_HQS)
    groups = _order(df["device_group"].dropna().unique(), CANON_GROUPS)
    total = int(df["sales_cnt"].sum())

    # ── 단말군별 ──
    g_sum = df.groupby("device_group")["sales_cnt"].sum()
    by_group = sorted(
        ({"group": g, "count": int(g_sum.get(g, 0)), "share": _pct(int(g_sum.get(g, 0)), total),
          "sim_only": g == "SIMonly"} for g in groups),
        key=lambda x: x["count"], reverse=True)
    company_share = {x["group"]: x["share"] for x in by_group}
    top3 = by_group[:3]

    # ── 본부별 100% 누적 ──
    hq_grp = df.pivot_table(index="mkt_div_org_nm", columns="device_group",
                            values="sales_cnt", aggfunc="sum", fill_value=0)
    hq_group_stacked = []
    for hq in hqs:
        gv = {g: int(hq_grp.loc[hq, g]) if (hq in hq_grp.index and g in hq_grp.columns) else 0
              for g in groups}
        hq_group_stacked.append({"hq": hq, "total": sum(gv.values()), "groups": gv})

    overview = {"kpis": {"total_sales": total, "top3": top3},
                "by_group": by_group, "hq_group_stacked": hq_group_stacked}

    # ── SKU 탭 (S26 / IP17) ──
    sku_tabs = {}
    for group in SKU_GROUPS:
        g_rows = df[df["device_group"] == group].copy()
        if len(g_rows) == 0:
            sku_tabs[group] = {"total": 0, "top_sku": None, "top_hq": None,
                               "by_sku": [], "detail": []}
            continue
        g_rows["sku"] = g_rows.apply(_sku_label, axis=1)
        g_total = int(g_rows["sales_cnt"].sum())
        sku_sum = g_rows.groupby("sku")["sales_cnt"].sum().sort_values(ascending=False)
        by_sku = [{"sku": s, "count": int(c), "share": _pct(int(c), g_total)}
                  for s, c in sku_sum.items()]
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

    # ── 본부별 포트폴리오 + 과/과소 지수 ──
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

    # ── 매트릭스 ──
    cells = []
    for hq in hqs:
        h_total = next((x["total"] for x in hq_group_stacked if x["hq"] == hq), 0)
        gv = next((x["groups"] for x in hq_group_stacked if x["hq"] == hq), {})
        for g in groups:
            c = int(gv.get(g, 0))
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
