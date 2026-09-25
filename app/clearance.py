"""轮廓间安全间隙（clearance）校验。

对一份 SVG path 即时规范化后，按**细分后的折线**逐段计算任意两个
参与校验的轮廓边界之间的最小欧氏距离，距离严格小于阈值
（``distance < min_distance``）即判为冲突；**距离等于阈值视为通过**。

参与规则：
- 闭合且非退化轮廓（outer / hole）始终参与；
- 开放轮廓（未以 ``Z`` 闭合）默认跳过，``include_open=True`` 时按
  开放折线参与；
- 退化轮廓（面积为 0 或顶点不足）始终跳过；
- 同一轮廓自身不比较。

若一对轮廓中一侧是 hole、另一侧正是其直接 parent（parent_original_index），
该冲突条目标记 ``hole_parent=True``。

整个计算只读、即时完成：不依赖已保存的规范化结果，也不写库。
结果按 ``(较小 original_index, 较大 original_index)`` 排序，保证确定性。
"""

from __future__ import annotations

import math
from typing import Any, Iterator

from .svggeo import Contour, segment_segment_dist2

# 阈值比较的相对容差：吸收细分折线的浮点表示误差，保证“等于阈值通过”
_EPS_REL = 1e-9
# 输出距离的量化位数（仅影响 JSON 展示，不影响判定）
_DISTANCE_DECIMALS = 9


def check_clearance(contours: list[Contour],
                    min_distance: float,
                    include_open: bool = False) -> dict[str, Any]:
    """对已分类的轮廓列表做安全间隙校验。

    参数:
        contours: :func:`app.svggeo.analyze_contours` 返回的轮廓
        min_distance: 允许的最小间隙（> 0），等于该值视为通过
        include_open: 是否把开放轮廓按折线纳入比较

    返回:
        ``{min_distance, include_open, checked_contour_count,
           skipped_contours, conflict_count, conflicts}``
    """
    if not (isinstance(min_distance, (int, float))
            and math.isfinite(min_distance) and min_distance > 0):
        raise ValueError("min_distance 必须是大于 0 的有限数")

    checked = [c for c in contours if _eligible(c, include_open)]
    checked.sort(key=lambda c: c.original_index)

    skipped = [
        {"original_index": c.original_index, "role": c.role,
         "reason": ("open" if c.role == "open" else "degenerate")}
        for c in sorted(contours, key=lambda c: c.original_index)
        if not _eligible(c, include_open)
    ]

    # 距离域比较（平方会放大 sqrt() 往返的浮点误差，例如 sqrt(2)**2 > 2）
    tol = _EPS_REL * max(1.0, min_distance)

    conflicts: list[dict[str, Any]] = []
    for i, ci in enumerate(checked):
        for cj in checked[i + 1:]:
            # 包围盒快速剔除：外扩阈值（含容差）后仍不相交则距离必然达标
            if not _bboxes_proximate(ci.bounds, cj.bounds, min_distance + tol):
                continue
            dist = math.sqrt(max(0.0, _contours_min_dist2(ci, cj)))
            if dist + tol < min_distance:
                conflicts.append(_conflict(ci, cj, dist, min_distance))

    conflicts.sort(key=lambda item: (item["a"]["original_index"],
                                     item["b"]["original_index"]))
    return {
        "min_distance": min_distance,
        "include_open": include_open,
        "checked_contour_count": len(checked),
        "skipped_contours": skipped,
        "conflict_count": len(conflicts),
        "conflicts": conflicts,
    }


# ---------------------------------------------------------------------------
# 内部工具
# ---------------------------------------------------------------------------

def _eligible(c: Contour, include_open: bool) -> bool:
    if c.degenerate:
        return False
    if not c.closed:
        return include_open  # role == "open"
    return True  # outer / hole


def _segments(c: Contour) -> Iterator[tuple[tuple[float, float],
                                            tuple[float, float]]]:
    """展开后的边序列：闭合环包含末点回首点的闭合边，开放折线不含。"""
    pts = c.points
    n = len(pts)
    last = n if c.closed else n - 1
    for k in range(last):
        yield pts[k], pts[(k + 1) % n]


def _contours_min_dist2(ci: Contour, cj: Contour) -> float:
    """两轮廓所有边对之间的最小平方距离。"""
    best = float("inf")
    for a, b in _segments(ci):
        for c, d in _segments(cj):
            d2 = segment_segment_dist2(a, b, c, d)
            if d2 < best:
                best = d2
                if best == 0.0:
                    return 0.0  # 相交/接触：不可能更小，提前结束
    return best


def _bboxes_proximate(b1: tuple[float, float, float, float],
                      b2: tuple[float, float, float, float],
                      gap: float) -> bool:
    """包围盒沿坐标轴的最小间距是否可能小于 ``gap``。

    间距为 0 表示包围盒在该轴上重叠。
    """
    gap_x = max(b2[0] - b1[2], b1[0] - b2[2], 0.0)
    gap_y = max(b2[1] - b1[3], b1[1] - b2[3], 0.0)
    return gap_x < gap and gap_y < gap


def _conflict(ci: Contour, cj: Contour, distance: float,
              threshold: float) -> dict[str, Any]:
    hole_parent = ((ci.role == "hole"
                    and cj.original_index == ci.parent_original_index)
                   or (cj.role == "hole"
                       and ci.original_index == cj.parent_original_index))
    a, b = sorted((ci, cj), key=lambda c: c.original_index)
    return {
        "a": {"original_index": a.original_index, "role": a.role},
        "b": {"original_index": b.original_index, "role": b.role},
        "distance": round(distance, _DISTANCE_DECIMALS),
        "threshold": threshold,
        "hole_parent": hole_parent,
    }
