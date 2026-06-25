"""집계 파이프라인 — 행(list[dict]) → 6탭 brief(dict).

Phase A 스캐폴드: `mock_rows()`가 결정론적 가짜 행을 생성하고 `build_brief()`가
이를 6탭 구조로 집계합니다. Phase C에서 mock_rows를 gateway fetch로 바꾸면
build_brief는 그대로 재사용됩니다 (행 스키마만 일치시키면 됨).

행 스키마(가정):
  {exec_ym, hq, device_group, sku, sim_only(bool), sales_cnt(int)}
"""
from __future__ import annotations

import hashlib
from datetime import datetime

# ── 차원 정의 ─────────────────────────────────────────────────────────────────
HQS = ["수도권", "PS&M", "제휴", "부산", "서부", "대구", "중부", "기업사업본부", "TDS"]

# 단말군 8종 — 마트 device_group 컬럼 실제 값과 동일. SIMonly는 sim_only로도 식별.
DEVICE_GROUPS = ["SIMonly", "S26", "IP17", "A17", "ZFlip7", "ZFold7", "Wide8", "Etc"]

# SKU 변형 (S26 / IP17만 SKU 탭 보유). 마트 sub_model(Base/PRO/MAX/AIR/울트라/플러스) × storage 가정.
SKU_MAP = {
    "S26": ["S26 Base 256", "S26 Base 512", "S26 플러스 256", "S26 울트라 256", "S26 울트라 512"],
    "IP17": ["IP17 Base 256", "IP17 PRO 256", "IP17 MAX 256", "IP17 MAX 512", "IP17 AIR 256"],
}


def _seed(*parts) -> float:
    h = hashlib.md5("|".join(str(p) for p in parts).encode()).hexdigest()
    return int(h[:8], 16) / 0xFFFFFFFF


def _sku_list(group: str) -> list[str]:
    return SKU_MAP.get(group, [group])


# ── mock 행 생성 (Phase C에서 gateway fetch로 교체) ───────────────────────────
def mock_rows(exec_ym: str) -> list[dict]:
    rows: list[dict] = []
    for hq in HQS:
        hq_scale = 0.5 + _seed("hq", hq) * 1.5
        for group in DEVICE_GROUPS:
            group_pop = 0.4 + _seed("grp", group) * 1.6
            for sku in _sku_list(group):
                base = 40 * hq_scale * group_pop
                noise = 0.5 + _seed("cell", exec_ym, hq, group, sku)
                cnt = round(base * noise)
                if cnt <= 0:
                    continue
                rows.append({
                    "exec_ym": exec_ym,
                    "hq": hq,
                    "device_group": group,
                    "sku": sku,
                    "sim_only": group == "SIMonly",
                    "sales_cnt": cnt,
                })
    return rows


# ── 집계 헬퍼 ─────────────────────────────────────────────────────────────────
def _pct(part: int, whole: int) -> float:
    return round(part / whole * 100, 1) if whole else 0.0


def _agg(rows, key) -> dict[str, int]:
    out: dict[str, int] = {}
    for r in rows:
        out[r[key]] = out.get(r[key], 0) + r["sales_cnt"]
    return out


# ── brief 빌드 ────────────────────────────────────────────────────────────────
def build_brief(rows: list[dict], exec_ym: str, *, data_source: str = "mock") -> dict:
    total = sum(r["sales_cnt"] for r in rows)

    # 단말군별 집계
    by_group_raw = _agg(rows, "device_group")
    by_group = sorted(
        ({"group": g, "count": c, "share": _pct(c, total),
          "sim_only": g == "SIMonly"} for g, c in by_group_raw.items()),
        key=lambda x: x["count"], reverse=True,
    )
    top3 = by_group[:3]

    # 본부별 단말군 100% 누적
    hq_group_stacked = []
    for hq in HQS:
        hq_rows = [r for r in rows if r["hq"] == hq]
        groups = _agg(hq_rows, "device_group")
        hq_total = sum(groups.values())
        hq_group_stacked.append({
            "hq": hq, "total": hq_total,
            "groups": {g: groups.get(g, 0) for g in DEVICE_GROUPS},
        })

    overview = {
        "kpis": {"total_sales": total, "top3": top3},
        "by_group": by_group,
        "hq_group_stacked": hq_group_stacked,
    }

    # SKU 탭 (S26 / IP17)
    sku_tabs = {}
    for group in ("S26", "IP17"):
        g_rows = [r for r in rows if r["device_group"] == group]
        g_total = sum(r["sales_cnt"] for r in g_rows)
        by_sku_raw = _agg(g_rows, "sku")
        by_sku = sorted(
            ({"sku": s, "count": c, "share": _pct(c, g_total)} for s, c in by_sku_raw.items()),
            key=lambda x: x["count"], reverse=True,
        )
        by_hq_raw = _agg(g_rows, "hq")
        top_hq = max(by_hq_raw, key=by_hq_raw.get) if by_hq_raw else None
        # SKU × 본부 상세
        detail = []
        for s in by_sku_raw:
            hq_counts = {hq: 0 for hq in HQS}
            for r in g_rows:
                if r["sku"] == s:
                    hq_counts[r["hq"]] += r["sales_cnt"]
            detail.append({"sku": s, "hq_counts": hq_counts,
                           "total": sum(hq_counts.values())})
        sku_tabs[group] = {
            "total": g_total,
            "top_sku": by_sku[0]["sku"] if by_sku else None,
            "top_hq": top_hq,
            "by_sku": by_sku,
            "detail": detail,
        }

    # 본부별 포트폴리오 + 과/과소 지수
    by_hq = []
    for hq in HQS:
        hq_rows = [r for r in rows if r["hq"] == hq]
        hq_total = sum(r["sales_cnt"] for r in hq_rows)
        groups = _agg(hq_rows, "device_group")
        portfolio = []
        for g in DEVICE_GROUPS:
            c = groups.get(g, 0)
            share_in_hq = _pct(c, hq_total)
            share_company = next((x["share"] for x in by_group if x["group"] == g), 0.0)
            portfolio.append({
                "group": g, "count": c,
                "share_in_hq": share_in_hq,
                "share_company": share_company,
                "over_index": round(share_in_hq - share_company, 1),
            })
        portfolio.sort(key=lambda x: x["count"], reverse=True)
        by_hq.append({"hq": hq, "total": hq_total, "portfolio": portfolio})

    # 본부×단말군 매트릭스
    matrix_cells = []
    for hq in HQS:
        hq_rows = [r for r in rows if r["hq"] == hq]
        hq_total = sum(r["sales_cnt"] for r in hq_rows)
        groups = _agg(hq_rows, "device_group")
        for g in DEVICE_GROUPS:
            c = groups.get(g, 0)
            matrix_cells.append({
                "hq": hq, "group": g, "count": c,
                "ratio_in_hq": _pct(c, hq_total),
            })

    # 알림 — 과/과소 지수 기반 단순 룰 (Phase E에서 정교화)
    alerts = _build_alerts(by_hq, exec_ym)

    return {
        "meta": {
            "exec_ym": exec_ym,
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "data_source": data_source,
            "device_groups": DEVICE_GROUPS,
            "hqs": HQS,
        },
        "overview": overview,
        "sku": sku_tabs,
        "by_hq": by_hq,
        "matrix": {"hqs": HQS, "groups": DEVICE_GROUPS, "cells": matrix_cells},
        "alerts": alerts,
    }


def _build_alerts(by_hq: list[dict], exec_ym: str) -> list[dict]:
    """과/과소 지수 절댓값으로 3단계 알림 생성 (스캐폴드용 단순 룰)."""
    alerts = []
    for hq in by_hq:
        for p in hq["portfolio"]:
            oi = p["over_index"]
            if abs(oi) >= 12:
                level = "urgent"
            elif abs(oi) >= 8:
                level = "warn"
            elif abs(oi) >= 5:
                level = "info"
            else:
                continue
            direction = "과다" if oi > 0 else "과소"
            alerts.append({
                "level": level,
                "exec_ym": exec_ym,
                "hq": hq["hq"],
                "group": p["group"],
                "over_index": oi,
                "message": f"{hq['hq']} · {p['group']} 비중 {direction} ({oi:+.1f}p)",
            })
    order = {"urgent": 0, "warn": 1, "info": 2}
    alerts.sort(key=lambda a: (order[a["level"]], -abs(a["over_index"])))
    return alerts
