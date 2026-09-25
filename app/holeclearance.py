"""孔洞最大内接圆圆心搜索（hole-clearance，只读即时分析），供钻孔选点。

对一份已存 SVG path 即时解析并完成轮廓包含层级分类后（与
metrics / clearance / scanlines 等同一份解析与曲线细分折线，见
:func:`app.svggeo.analyze_contours` 与
:func:`app.svggeo.flatten_subpath`），为每个**有效孔洞**
（``role == "hole"``，闭合且非退化）寻找孔内最大内接圆的圆心：

- 候选圆心必须**严格位于孔洞内部**，且**不在任何直属岛**
  （``parent_original_index`` 指向该孔洞的深度偶数 ``outer`` 岛）内部；
- 候选点的可用半径为到**孔洞边界**与**各直属岛边界**的最短欧氏距离
  （均在曲线细分后的折线上逐边计算）；
- 搜索采用**距离上界优先**的网格细分（polylabel 式）：初始网格覆盖孔洞
  包围盒，优先队列按 ``格心最小边界距离 + 半格对角线`` 的距离上界出队，
  上界仍可能把当前最优半径提升超过 ``precision`` 时把该格四分子格，
  否则收敛——凹孔或偏心岛不会使搜索退化为包围盒中心；
- 整格落在孔外或整格落入岛内的网格被几何剪枝，不参与细分；
- 单次请求全部孔洞合计最多评估 :data:`MAX_GRID_CELLS` 个网格单元，
  预算耗尽仍未达到 ``precision`` 时抛出 ``ValueError``（路由层返回 422）。

收敛保证：返回半径 ``r`` 满足 ``r* - precision <= r <= r*``，
其中 ``r*`` 为细分折线上的真实最大内接圆半径。

整个计算只读、即时完成：不依赖已保存的规范化结果，也不写库。
开放（``open``）与退化（``degenerate``）子路径跳过并列入
``skipped_contours``；没有有效孔洞时 ``holes`` 为空数组。
"""

from __future__ import annotations

import heapq
import math
from typing import Any

from .svggeo import Contour, point_segment_dist2

# 单次请求全部孔洞合计允许评估的最多网格单元数，超限由路由层返回 422
MAX_GRID_CELLS = 20000
# 输出量化位数（仅影响 JSON 展示，不改变判定）
_DECIMALS = 9
_SQRT2 = math.sqrt(2.0)


def compute_hole_clearance(contours: list[Contour],
                           precision: float) -> dict[str, Any]:
    """对已分类的轮廓列表逐孔洞搜索最大内接圆圆心。

    参数:
        contours: :func:`app.svggeo.analyze_contours` 返回的轮廓
            （已带 ``role`` / ``depth`` / ``parent_original_index``）。
        precision: 最大半径的逼近精度，必须为大于 0 的有限数。

    返回:
        ``{precision, hole_count, cells_used, holes, skipped_contours}``；
        ``holes`` 按 ``original_index`` 升序，每项含
        ``{original_index, role, depth, center: {x, y}, radius}``。

    抛出:
        ValueError: ``precision`` 非法、网格单元预算耗尽仍未收敛，
            或某孔洞内部不存在可行圆心（被直属岛覆盖）。
    """
    if not (isinstance(precision, (int, float))
            and math.isfinite(precision) and precision > 0):
        raise ValueError("precision 必须是大于 0 的有限数")
    precision = float(precision)

    holes = sorted((c for c in contours if c.role == "hole"),
                   key=lambda c: c.original_index)
    # 直属岛：parent_original_index 指向某孔洞的深度偶数 outer 轮廓
    islands_by_parent: dict[int, list[Contour]] = {}
    for c in contours:
        if c.role == "outer" and c.parent_original_index is not None:
            islands_by_parent.setdefault(c.parent_original_index, []).append(c)

    skipped = [
        {"original_index": c.original_index, "role": c.role, "reason": c.role}
        for c in sorted(contours, key=lambda c: c.original_index)
        if c.role != "hole"
    ]

    budget = _CellBudget(MAX_GRID_CELLS)
    results = [
        _hole_inscribed_circle(hole,
                               islands_by_parent.get(hole.original_index, []),
                               precision, budget)
        for hole in holes
    ]

    return {
        "precision": _num(precision),
        "hole_count": len(results),
        "cells_used": budget.used,
        "holes": results,
        "skipped_contours": skipped,
    }


# ---------------------------------------------------------------------------
# 内部工具
# ---------------------------------------------------------------------------

class _CellBudget:
    """单次请求的网格单元评估预算（全部孔洞共享）。"""

    def __init__(self, limit: int) -> None:
        self.limit = limit
        self.used = 0

    def use(self) -> None:
        if self.used >= self.limit:
            raise ValueError(
                f"网格单元评估数量达到单次上限 {self.limit} 个仍未收敛，"
                f"请增大 precision")
        self.used += 1


def _hole_inscribed_circle(hole: Contour,
                           islands: list[Contour],
                           precision: float,
                           budget: _CellBudget) -> dict[str, Any]:
    """在单个孔洞内按距离上界优先的网格细分搜索最大内接圆。"""
    minx, miny, maxx, maxy = hole.bounds
    cell = min(maxx - minx, maxy - miny)
    half = cell / 2.0

    best = 0.0
    best_pt: tuple[float, float] | None = None
    # 大顶堆（存负上界）：(-upper, 序号, 格心x, 格心y, 半格边长)
    heap: list[tuple[float, int, float, float, float]] = []
    seq = 0

    def evaluate(cx: float, cy: float, h: float) -> float | None:
        """评估格心：刷新最优解并返回该格半径上界；整格无可行点返回 None。"""
        nonlocal best, best_pt
        budget.use()
        p = (cx, cy)
        d_hole = math.sqrt(_point_ring_dist2(p, hole.points))
        inside_hole = _point_in_ring(p, hole.points)
        if not inside_hole and h * _SQRT2 < d_hole:
            return None  # 整格在孔外：不含任何可行点
        d = d_hole
        valid = inside_hole
        for isl in islands:
            lb2 = _bbox_dist2(p, isl.bounds)
            inside_isl = lb2 == 0.0 and _point_in_ring(p, isl.points)
            if not inside_isl and lb2 >= d * d:
                continue  # 岛边界不可能刷新最小距离，且点不在岛内
            d_isl = math.sqrt(_point_ring_dist2(p, isl.points))
            if inside_isl:
                if h * _SQRT2 < d_isl:
                    return None  # 整格在岛内：不含任何可行点
                valid = False
            if d_isl < d:
                d = d_isl
        if valid and d > best:
            best = d
            best_pt = p
        return d + h * _SQRT2

    def enqueue(cx: float, cy: float, h: float) -> None:
        nonlocal seq
        upper = evaluate(cx, cy, h)
        if upper is None:
            return
        # 尚未找到可行圆心时持续搜索（忽略精度剪枝）；
        # 之后只保留可能把最优半径提升超过 precision 的格
        if best_pt is None or upper > best + precision:
            heapq.heappush(heap, (-upper, seq, cx, cy, h))
            seq += 1

    # 初始网格：以 min(宽, 高) 为格边长覆盖孔洞包围盒
    x = minx
    while x < maxx:
        y = miny
        while y < maxy:
            enqueue(x + half, y + half, half)
            y += cell
        x += cell

    while heap:
        neg_upper, _, cx, cy, h = heapq.heappop(heap)
        if best_pt is not None and -neg_upper - best <= precision:
            break  # 剩余网格最多把半径提升 precision：已收敛
        quarter = h / 2.0
        enqueue(cx - quarter, cy - quarter, quarter)
        enqueue(cx + quarter, cy - quarter, quarter)
        enqueue(cx - quarter, cy + quarter, quarter)
        enqueue(cx + quarter, cy + quarter, quarter)

    if best_pt is None:
        raise ValueError(
            f"孔洞（原始索引 {hole.original_index}）内找不到可行钻孔点："
            f"内部被直属岛覆盖或自由区域小于细分精度")

    return {
        "original_index": hole.original_index,
        "role": "hole",
        "depth": hole.depth,
        "center": {"x": _num(best_pt[0]), "y": _num(best_pt[1])},
        "radius": _num(best),
    }


def _point_ring_dist2(p: tuple[float, float],
                      pts: list[tuple[float, float]]) -> float:
    """点到闭合环各边（含末点回首点的闭合边）的最小平方距离。"""
    px, py = p
    best = math.inf
    n = len(pts)
    for k in range(n):
        a = pts[k]
        b = pts[(k + 1) % n]
        # 边包围盒的距离下界剪枝：不可能刷新最小值时跳过精确计算
        lx = min(a[0], b[0]) - px
        if lx < 0.0:
            lx = px - max(a[0], b[0])
            if lx < 0.0:
                lx = 0.0
        ly = min(a[1], b[1]) - py
        if ly < 0.0:
            ly = py - max(a[1], b[1])
            if ly < 0.0:
                ly = 0.0
        if lx * lx + ly * ly >= best:
            continue
        d2 = point_segment_dist2(p, a, b)
        if d2 < best:
            best = d2
    return best


def _point_in_ring(p: tuple[float, float],
                   pts: list[tuple[float, float]]) -> bool:
    """射线法判点是否在闭合环内（半开边规则，与 scanlines / hit-test 同口径）。"""
    x, y = p
    inside = False
    n = len(pts)
    for k in range(n):
        ax, ay = pts[k]
        bx, by = pts[(k + 1) % n]
        if (ay > y) != (by > y):
            xint = ax + (bx - ax) * (y - ay) / (by - ay)
            if xint > x:
                inside = not inside
    return inside


def _bbox_dist2(p: tuple[float, float],
                bounds: tuple[float, float, float, float]) -> float:
    """点到包围盒的平方距离（盒内为 0），用作精确距离的下界。"""
    dx = max(bounds[0] - p[0], p[0] - bounds[2], 0.0)
    dy = max(bounds[1] - p[1], p[1] - bounds[3], 0.0)
    return dx * dx + dy * dy


def _num(v: float) -> float:
    """量化展示值并消除 -0.0。"""
    r = round(v, _DECIMALS)
    return r + 0.0 if r == 0 else r
