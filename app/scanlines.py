"""水平扫描线填充区间（只读即时分析），供逐行加工使用。

对一份已存 SVG path 即时解析并完成轮廓包含层级分类后（与
metrics / clearance 同一份解析与曲线细分折线，见
:func:`app.svggeo.analyze_contours` 与
:func:`app.svggeo.flatten_subpath`），在有效闭合轮廓的**整体包围盒**
内沿 y 轴等间距布置水平扫描线，逐条求与各轮廓边的交点，并按
**包含层级交替**合并出填充区间：

- 扫描线 y 取 ``miny + k*spacing``（``k=0,1,2,...``），直到
  ``y < maxy`` 不成立为止（最大 y 不取线）；
- 仅闭合且非退化轮廓（``outer`` / ``hole``）参与，开放与退化轮廓跳过；
- 交点采用**半开边规则** ``(y1 > y) != (y2 > y)``：水平边永不相交，
  落在扫描线上的顶点只被上行/下行两条邻边中的一条计数，避免重复；
- 每个环自身的交点升序配对成区间，再按深度奇偶做事件扫描合并：
  外环/岛（深度偶数）计入，孔洞（深度奇数）扣除，最终输出每条线上
  升序、互不重叠的 ``[x_start, x_end]`` 填充区间。

整个计算只读、即时完成：不依赖已保存的规范化结果，也不写库。
没有任何有效闭合轮廓时返回空扫描线列表。
"""

from __future__ import annotations

import math
from typing import Any

from .svggeo import Contour

# 单次请求允许的最多扫描线条数，超限由路由层返回 422
MAX_SCANLINES = 1000
# 输出量化位数（仅影响 JSON 展示，不改变判定）
_DECIMALS = 9
# 线条数 / 交点合并的相对浮点容差
_EPS_REL = 1e-12
_X_EPS_REL = 1e-9


def compute_scanlines(contours: list[Contour],
                      spacing: float) -> dict[str, Any]:
    """对已分类的轮廓列表生成水平扫描线填充区间。

    参数:
        contours: :func:`app.svggeo.analyze_contours` 返回的轮廓
            （已带 ``role`` / ``depth``）
        spacing: 扫描线间距，必须为大于 0 的有限数

    返回:
        ``{spacing, bounds, scanline_count, lines, skipped_contours}``；
        ``lines`` 每项为 ``{"y", "intervals": [[x_start, x_end], ...]}``。
        无有效闭合轮廓时 ``bounds`` 为 ``None``、``lines`` 为空列表。

    抛出:
        ValueError: ``spacing`` 非法，或生成的扫描线超过
            :data:`MAX_SCANLINES` 条。
    """
    if not (isinstance(spacing, (int, float))
            and math.isfinite(spacing) and spacing > 0):
        raise ValueError("spacing 必须是大于 0 的有限数")

    valid = [c for c in contours if c.role in ("outer", "hole")]
    valid.sort(key=lambda c: c.original_index)

    skipped = [
        {"original_index": c.original_index, "role": c.role,
         "reason": ("open" if c.role == "open" else "degenerate")}
        for c in sorted(contours, key=lambda c: c.original_index)
        if c.role not in ("outer", "hole")
    ]

    if not valid:
        return {
            "spacing": _num(spacing),
            "bounds": None,
            "scanline_count": 0,
            "lines": [],
            "skipped_contours": skipped,
        }

    miny = min(c.bounds[1] for c in valid)
    maxy = max(c.bounds[3] for c in valid)
    minx = min(c.bounds[0] for c in valid)
    maxx = max(c.bounds[2] for c in valid)
    x_eps = _X_EPS_REL * max(1.0, maxx - minx, maxy - miny)

    # y < maxy（不含）：高度恰好是 spacing 整数倍时，线条数 = 高度/间距
    count = int(math.ceil((maxy - miny) / spacing - _EPS_REL))
    count = max(0, count)
    if count > MAX_SCANLINES:
        raise ValueError(
            f"扫描线数量 {count} 超过单次上限 {MAX_SCANLINES} 条，"
            f"请增大 spacing")

    lines: list[dict[str, Any]] = []
    for k in range(count):
        y = miny + k * spacing
        intervals = _line_intervals(valid, y, x_eps)
        lines.append({"y": _num(y),
                      "intervals": [[_num(a), _num(b)] for a, b in intervals]})

    return {
        "spacing": _num(spacing),
        "bounds": {"miny": _num(miny), "maxy": _num(maxy)},
        "scanline_count": count,
        "lines": lines,
        "skipped_contours": skipped,
    }


def _line_intervals(contours: list[Contour],
                    y: float,
                    x_eps: float) -> list[tuple[float, float]]:
    """求单条扫描线 ``y`` 上按深度交替合并后的升序填充区间。

    每个环先用半开边规则收集交点并两两配对，再把深度偶数环的区间
    作为 +1 事件、孔洞（奇数）作为 -1 事件做一维扫描：覆盖度由 0
    转正为区间起点，降回 0 为终点。
    """
    events: list[tuple[float, int]] = []
    for c in contours:
        xs = _ring_crossings(c.points, y)
        # 正常情况下交点成对；防御性丢弃落单交点（异常相切配置）
        if len(xs) % 2 == 1:
            xs.pop()
        delta = 1 if c.depth % 2 == 0 else -1
        xs.sort()
        for i in range(0, len(xs), 2):
            events.append((xs[i], delta))
            events.append((xs[i + 1], -delta))

    if not events:
        return []

    # 相同（在容差内）位置的事件先合并，保证嵌套环端点共点时覆盖度正确
    events.sort(key=lambda e: e[0])
    groups: list[tuple[float, int]] = []
    for x, delta in events:
        if groups and abs(x - groups[-1][0]) <= x_eps:
            groups[-1] = (groups[-1][0], groups[-1][1] + delta)
        else:
            groups.append((x, delta))

    intervals: list[tuple[float, float]] = []
    coverage = 0
    start = 0.0
    for x, delta in groups:
        before = coverage
        coverage += delta
        if before == 0 and coverage > 0:
            start = x
        elif before > 0 and coverage == 0:
            if x - start > x_eps:  # 丢弃数值零宽度区间
                intervals.append((start, x))
    return intervals


def _ring_crossings(points: list[tuple[float, float]],
                    y: float) -> list[float]:
    """半开边规则下扫描线与闭合环各边的交点 x 坐标。

    判定 ``(y1 > y) != (y2 > y)``：
    - 水平边两端同侧，永不计数；
    - 穿过顶点时，上行/下行两条邻边恰有一条满足，顶点不重复计数；
    - 局部极值处两条邻边同侧，按切线处理（计 0 次）。
    """
    xs: list[float] = []
    n = len(points)
    for k in range(n):
        x1, y1 = points[k]
        x2, y2 = points[(k + 1) % n]
        if (y1 > y) != (y2 > y):
            xs.append(x1 + (x2 - x1) * (y - y1) / (y2 - y1))
    return xs


def _num(v: float) -> float:
    """量化展示值并消除 -0.0。"""
    r = round(v, _DECIMALS)
    return r + 0.0 if r == 0 else r
