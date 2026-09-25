"""点命中测试（hit-test，只读即时分析）。

对一份已存 SVG path 即时解析（复用 :func:`app.svggeo.analyze_contours`
的解析与曲线细分折线，见 :func:`app.svggeo.flatten_subpath`），对每个
待查坐标点给出 ``inside`` / ``outside`` / ``boundary`` 三种判定：

- **boundary 优先**：点落在任一**闭合子路径**细分折线的**任一条边**
  （含末点回首点的闭合边、边的端点）上即判 ``boundary``，不再参与
  填充汇总；边界容差**逐条边按该边附近的浮点前向误差**自适应
  （叉积/点积的舍入上界加坐标 ulp），不按整环包围盒放大——长宽悬殊
  的环不会沿长边长出可见的“边界带”，斜边、端点与零长边同口径；
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
# IEEE-754 双精度机器 epsilon（~2.22e-16）
_EPS = 2.220446049250313e-16
# 叉积/点积前向舍入误差的安全倍数（>3e 即可覆盖逐项乘积与加减的舍入）
_EPS_K = 8.0


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
    rings = [c.points for c in closed]

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
              rings: list[list[tuple[float, float]]],
              fill_rule: str) -> str:
    """逐边判定单点：先 boundary，再按填充规则汇总交叉/绕数。"""
    px, py = point
    crossings = 0
    winding = 0

    for pts in rings:
        n = len(pts)
        for k in range(n):
            ax, ay = pts[k]
            bx, by = pts[(k + 1) % n]
            # boundary 优先：点落在任一边（含闭合边/端点）上即返回
            if _on_segment(ax, ay, bx, by, px, py):
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


def _on_segment(ax: float, ay: float, bx: float, by: float,
                px: float, py: float) -> bool:
    """点 ``p`` 是否在线段 ``ab`` 上（含端点与零长度段）。

    容差**只按该候选边附近的数值误差**取，不引用整环包围盒，因此
    长宽悬殊轮廓的长边不会把判定带扩到可见宽度。判定分两步，各自的
    舍入误差量级分别处理：

    - **垂直方向（叉积）**：``cross = (b-a)×(p-a)`` 的前向舍入误差为
      ``O(eps)`` 乘以参与运算的最大坐标量级，细分插值点在长边上的
      垂直残差实测 ~``eps·coord``。容差
      ``perp_tol = K·eps·max(|a|,|b|,|p|,|b-a|)``；
    - **沿边方向（点积）**：``dot = (b-a)·(p-a)`` 用来确认投影落在
      区间 ``[0, |b-a|²]`` 内，其舍入误差为 ``O(eps·|b-a|²)``，
      故区间两端各放宽 ``K·eps·len²``——端点与整条边按同一条判定线
      处理，端点外的点必须先在端点的坐标 ulp 内才会被接受；
    - **零长度段**（``a == b``）退化为点到端点的距离判定，容差
      ``K·eps·max(|a|,|p|)``。

    对长 1e9、高 10 的环，沿长边的垂直判定带宽约 2e-6（相对环高不可见），
    短边端点也不会被邻接长边的大坐标“拉长”出可见的端点球；
    而真正在边上的细分点仍判 boundary。斜边与轴对齐边共用本式。
    """
    abx, aby = bx - ax, by - ay
    apx, apy = px - ax, py - ay
    len2 = abx * abx + aby * aby
    cross = abx * apy - aby * apx
    coord = max(math.hypot(ax, ay), math.hypot(bx, by),
                math.hypot(px, py), math.hypot(abx, aby), 1.0)
    if len2 == 0.0:
        return math.hypot(apx, apy) <= _EPS_K * _EPS * coord
    perp_tol = _EPS_K * _EPS * coord * math.sqrt(len2)
    along_tol = _EPS_K * _EPS * len2
    dot = abx * apx + aby * apy
    return (abs(cross) <= perp_tol
            and -along_tol <= dot <= len2 + along_tol)

