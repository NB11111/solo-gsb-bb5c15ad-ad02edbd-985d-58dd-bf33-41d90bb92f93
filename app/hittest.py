"""点命中测试（hit-test，只读即时分析）。

对一份已存 SVG path 即时解析（复用 :func:`app.svggeo.analyze_contours`
的解析与曲线细分折线，见 :func:`app.svggeo.flatten_subpath`），对每个
待查坐标点给出 ``inside`` / ``outside`` / ``boundary`` 三种判定：

- **boundary 优先**：点落在任一**闭合子路径**细分折线的**任一条边**
  （含末点回首点的闭合边、边的端点）上即判 ``boundary``，不再参与
  填充汇总；边界容差按环尺寸自适应（与
  :func:`app.svggeo.point_in_ring` 同一容差口径）；
- 其余点按保存的 ``fill_rule`` 对**全部闭合子路径**汇总：
  - ``evenodd``：右向水平射线交叉次数为奇数 -> ``inside``，
    偶数 -> ``outside``；
  - ``nonzero``：有向边绕数（屏幕坐标下 y 递增边 +1、y 递减边 -1）
    非零 -> ``inside``，为零 -> ``outside``；同向重叠轮廓绕数累加，
    反向重叠相互抵消。
- **开放子路径**（未以 ``Z`` 闭合）不参与（即使点恰好落在开放折线上
  也不是 boundary），列入 ``skipped_contours``；**闭合但净面积为 0**
  的自交路径（如蝴蝶结）仍按字面参与——其自交分区在两种填充规则下
  都可能被填充。

交叉/绕数计数采用与扫描线相同的**半开边规则**
``(y1 > y) != (y2 > y)``：水平边永不计数，穿过顶点时只被上行/下行
两条邻边之一计数。整个计算只读、即时完成：不依赖已保存的规范化结果，
也不写库。没有任何闭合子路径时所有点均判 ``outside``。
"""

from __future__ import annotations

import math
from typing import Any

from .svggeo import Contour

# 单次请求允许的最多查询点数，超限由路由层（Pydantic）返回 422
MAX_HIT_POINTS = 500
# 边界判定的相对浮点容差（与 svggeo.point_in_ring 同口径）
_EPS_REL = 1e-9


def hit_test_points(contours: list[Contour],
                    points: list[tuple[float, float]],
                    fill_rule: str) -> dict[str, Any]:
    """对已解析的轮廓列表批量判定每个查询点的填充归属。

    参数:
        contours: :func:`app.svggeo.analyze_contours` 返回的轮廓
            （含开放/退化等全部角色；本函数只取 ``closed`` 的）。
        points: ``(x, y)`` 查询点列表，每个坐标均须为有限数
            （由路由层模型保证）。
        fill_rule: ``nonzero`` 或 ``evenodd``。

    返回:
        ``{point_count, checked_contour_count, results,
        skipped_contours}``；``results`` 按输入顺序给出每项
        ``{index, point: {x, y}, result}``，
        ``result`` 为 ``inside`` / ``outside`` / ``boundary``。
    """
    if fill_rule not in ("nonzero", "evenodd"):
        raise ValueError("fill_rule 必须是 'nonzero' 或 'evenodd'")

    closed = [c for c in contours if c.closed]
    closed.sort(key=lambda c: c.original_index)
    rings = [(c.points, _ring_eps(c.bounds)) for c in closed]

    results = [
        {"index": idx,
         "point": {"x": float(x), "y": float(y)},
         "result": _classify((x, y), rings, fill_rule)}
        for idx, (x, y) in enumerate(points)
    ]

    skipped = [
        {"original_index": c.original_index, "role": c.role,
         "reason": ("open" if c.role == "open" else "degenerate")}
        for c in sorted(contours, key=lambda c: c.original_index)
        if not c.closed
    ]

    return {
        "point_count": len(points),
        "checked_contour_count": len(closed),
        "results": results,
        "skipped_contours": skipped,
    }


def _classify(point: tuple[float, float],
              rings: list[tuple[list[tuple[float, float]], float]],
              fill_rule: str) -> str:
    """逐边判定单点：先 boundary，再按填充规则汇总交叉/绕数。"""
    px, py = point
    crossings = 0
    winding = 0

    for pts, eps in rings:
        n = len(pts)
        for k in range(n):
            ax, ay = pts[k]
            bx, by = pts[(k + 1) % n]
            # boundary 优先：点落在任一边（含闭合边/端点）上即返回
            if _on_segment(ax, ay, bx, by, px, py, eps):
                return "boundary"
            # 半开边规则：水平边不计数，顶点只被一条邻边计数
            if (ay > py) != (by > py):
                xint = ax + (bx - ax) * (py - ay) / (by - ay)
                if xint > px:
                    crossings += 1
                    # 屏幕坐标（y 向下）：y 递增边与 y 递减边方向相反；
                    # 全局符号翻转不影响 nonzero 的零/非零判定
                    winding += 1 if ay < by else -1

    if fill_rule == "evenodd":
        return "inside" if crossings % 2 == 1 else "outside"
    return "inside" if winding != 0 else "outside"


def _ring_eps(bounds: tuple[float, float, float, float]) -> float:
    minx, miny, maxx, maxy = bounds
    return _EPS_REL * max(1.0, maxx - minx, maxy - miny)


def _on_segment(ax: float, ay: float, bx: float, by: float,
                px: float, py: float, eps: float) -> bool:
    """点 ``p`` 是否在线段 ``ab`` 上（含端点与零长度段），容差按边长缩放。"""
    cross = (bx - ax) * (py - ay) - (by - ay) * (px - ax)
    if abs(cross) > eps * max(1.0, math.hypot(bx - ax, by - ay)):
        return False
    return (min(ax, bx) - eps <= px <= max(ax, bx) + eps
            and min(ay, by) - eps <= py <= max(ay, by) + eps)
