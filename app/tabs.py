"""外环微桥布置（tabs，只读即时计算）。

对一份已存 SVG path 即时解析并完成轮廓包含层级分类后（与
metrics / clearance / scanlines / cut-plan 同一份解析与曲线细分折线，
见 :func:`app.svggeo.analyze_contours`），仅对**闭合且非退化的外环**
（``role == "outer"``，含孔中岛等深度偶数轮廓）沿细分折线建立
首尾相接的弧长坐标并布置激光切割用微桥（bridge / tab）：

- 将轮廓周长 ``P`` 等分为 ``count`` 个扇区，第 ``k`` 个扇区为
  ``[kP/count, (k+1)P/count]``，以其中点 ``(k+0.5)P/count`` 为
  桥段中心的目标位置；
- 每个顶点沿两侧各留出长度 ``corner_margin`` 的拐角余量区，桥段
  不得跨过余量区（即桥段必须整体落在单条细分边的余量区之间）；
- 桥段宽 ``width``，已布置桥段的开区间 ``(c-width, c+width)``
  内不得再放入中心（端点相切允许：新桥半宽 ``width/2`` 与既有桥段
  端点恰好相接不算重叠），即桥段之间不得重叠；
- 按**原始索引升序**逐轮廓处理，轮廓内按**扇区顺序**依次在本扇区
  的可行中心区间（余量/桥宽限制并扣除既有桥段后的闭区间之并）中
  选择距扇区目标中点最近的中心；距离相同时取较小弧长；
- 扇区内无任何可行位置（边太短、余量过大或与既有桥段冲突）时该桥
  段无法放置并计入 ``unplaced_count``，不影响后续扇区。

整个计算只读、即时完成：不依赖已保存的规范化结果，也不写库。
孔洞（``hole``）、开放轮廓（``open``）与退化轮廓（``degenerate``）
跳过并列入 ``skipped_contours``。
"""

from __future__ import annotations

import math
from typing import Any

from .svggeo import Contour

# 每条轮廓允许请求的最多微桥数，超限由路由层返回 422
MAX_TABS = 10000
# 输出量化位数（仅影响 JSON 展示，不改变判定）
_DECIMALS = 9
# 弧长区间判定的绝对浮点容差
_EPS = 1e-12


def compute_tabs(contours: list[Contour],
                 count: int,
                 width: float,
                 corner_margin: float = 0.0) -> dict[str, Any]:
    """对已分类的轮廓列表在外环上布置微桥。

    参数:
        contours: :func:`app.svggeo.analyze_contours` 返回的轮廓
            （已带 ``role`` / ``depth``）。
        count: 每条外环请求的微桥数量，必须为正整数且不超过
            :data:`MAX_TABS`。
        width: 桥宽（弧长方向长度），必须为大于 0 的有限数。
        corner_margin: 顶点两侧的拐角余量长度，必须为非负有限数。

    返回:
        ``{count, width, corner_margin, contour_count, tab_count,
        unplaced_count, contours, skipped_contours}``；``contours``
        每项含逐桥段的起点/终点/中心坐标与对应弧长。

    抛出:
        ValueError: 参数非法或 ``count`` 超过上限。
    """
    if not isinstance(count, int) or isinstance(count, bool) or count <= 0:
        raise ValueError("count 必须为正整数")
    if count > MAX_TABS:
        raise ValueError(f"每条轮廓的微桥数量不能超过 {MAX_TABS} 个")
    if not isinstance(width, (int, float)) or not math.isfinite(width) \
            or width <= 0:
        raise ValueError("width 必须是大于 0 的有限数")
    if not isinstance(corner_margin, (int, float)) \
            or not math.isfinite(corner_margin) or corner_margin < 0:
        raise ValueError("corner_margin 必须是非负有限数")

    width = float(width)
    corner_margin = float(corner_margin)

    skipped = [
        {"original_index": c.original_index, "role": c.role, "reason": c.role}
        for c in sorted(contours, key=lambda c: c.original_index)
        if c.role != "outer"
    ]

    out_contours: list[dict[str, Any]] = []
    total_placed = 0
    total_unplaced = 0

    for c in sorted(contours, key=lambda c: c.original_index):
        if c.role != "outer":
            continue
        entry = _contour_tabs(c, count, width, corner_margin)
        out_contours.append(entry)
        total_placed += entry["tab_count"]
        total_unplaced += entry["unplaced_count"]

    return {
        "count": count,
        "width": _num(width),
        "corner_margin": _num(corner_margin),
        "contour_count": len(out_contours),
        "tab_count": total_placed,
        "unplaced_count": total_unplaced,
        "contours": out_contours,
        "skipped_contours": skipped,
    }


def _contour_tabs(contour: Contour,
                  count: int,
                  width: float,
                  margin: float) -> dict[str, Any]:
    """在单个外环上按扇区贪心布置微桥。"""
    pts = contour.points
    n = len(pts)

    # 首尾相接的弧长坐标：cum[i] 为顶点 i 的累计弧长，cum[n] 即周长
    cum = [0.0]
    for i in range(n):
        ax, ay = pts[i]
        bx, by = pts[(i + 1) % n]
        cum.append(cum[-1] + math.hypot(bx - ax, by - ay))
    perimeter = cum[-1]
    half = width / 2.0

    tabs: list[dict[str, Any]] = []
    blocked: list[tuple[float, float]] = []  # 已布置桥段的开弧长区间
    unplaced = 0

    for k in range(count):
        s_lo = k * perimeter / count
        s_hi = (k + 1) * perimeter / count
        target = (k + 0.5) * perimeter / count

        # 收集本扇区内可行的桥中心闭区间：
        # 每条边先扣除两端拐角余量与桥半宽，再与扇区相交，
        # 最后扣除既有桥段的开中心区间（|Δcenter| < width 即重叠，
        # 端点保留：桥段端点相切不算重叠）。
        feasible: list[tuple[float, float]] = []
        for i in range(n):
            lo = max(s_lo, cum[i] + margin + half)
            hi = min(s_hi, cum[i + 1] - margin - half)
            if lo > hi + _EPS:
                continue
            feasible.extend(_subtract_blocked(lo, hi, blocked))

        center: float | None = None
        if feasible:
            best_key: tuple[float, float] | None = None
            for lo, hi in feasible:
                cand = lo if target <= lo else hi if target >= hi else target
                key = (abs(cand - target), cand)  # 等距取较小弧长
                if best_key is None or key < best_key:
                    best_key, center = key, cand

        if center is None:
            unplaced += 1
            continue

        # 既有桥段中心 c1 与新中心 c2 的间距 < width 即桥段重叠，
        # 故遮挡新中心的开区间为 (center-width, center+width)
        blocked.append((center - width, center + width))
        start_s, end_s = center - half, center + half
        tabs.append({
            "sector": k,
            "start": _point(pts, cum, start_s),
            "end": _point(pts, cum, end_s),
            "center": _point(pts, cum, center),
        })

    return {
        "original_index": contour.original_index,
        "role": "outer",
        "depth": contour.depth,
        "perimeter": _num(perimeter),
        "requested_count": count,
        "tab_count": len(tabs),
        "unplaced_count": unplaced,
        "tabs": tabs,
    }


def _subtract_blocked(lo: float,
                      hi: float,
                      blocked: list[tuple[float, float]]
                      ) -> list[tuple[float, float]]:
    """从闭区间 ``[lo, hi]`` 中扣除已布置桥段的开区间之并。

    开区间 ``(a, b)`` 的端点保留（桥段端点相切不算重叠）。
    ``blocked`` 按弧长升序（随扇区处理顺序自然有序）。
    """
    pieces: list[tuple[float, float]] = []
    cur = lo
    for a, b in blocked:
        if b <= cur:
            continue  # 整个开区间在当前起点之前（含端点相切）
        if a >= hi:
            break  # 自 hi 起才被遮挡，hi 本身仍可行
        if a > cur:
            pieces.append((cur, a))
        cur = max(cur, b)
        if cur > hi + _EPS:
            break
    if cur <= hi + _EPS:
        pieces.append((cur, hi))
    return [(a, b) for a, b in pieces if b - a >= -_EPS]


def _point(pts: list[tuple[float, float]],
           cum: list[float],
           s: float) -> dict[str, Any]:
    """弧长 ``s`` 处的坐标（沿细分折线线性插值）。"""
    perimeter = cum[-1]
    if s <= 0.0:
        s = 0.0
    elif s >= perimeter:
        s = perimeter
    for i in range(len(pts)):
        if s <= cum[i + 1] or i == len(pts) - 1:
            edge_len = cum[i + 1] - cum[i]
            t = (s - cum[i]) / edge_len if edge_len > 0 else 0.0
            ax, ay = pts[i]
            bx, by = pts[(i + 1) % len(pts)]
            return {
                "x": _num(ax + (bx - ax) * t),
                "y": _num(ay + (by - ay) * t),
                "arc": _num(s),
            }
    raise AssertionError("弧长坐标定位失败")  # pragma: no cover


def _num(v: float) -> float:
    """量化展示值并消除 -0.0。"""
    r = round(v, _DECIMALS)
    return r + 0.0 if r == 0 else r
