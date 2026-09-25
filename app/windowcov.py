"""矩形窗口覆盖分析（window-coverage，只读即时分析）。

对一份已存 SVG path 即时解析并完成轮廓包含层级分类后（与
metrics / clearance / scanlines 等同一份解析与曲线细分折线，见
:func:`app.svggeo.analyze_contours` 与
:func:`app.svggeo.flatten_subpath`），把每个**有效闭合环**
（``outer`` / ``hole``）裁剪到轴对齐矩形窗口内求面积，再按
**包含层级交替**累加为窗口内的净填充面积：

- 外环（``outer``，深度偶数）裁剪面积 **+** 计入；
- 孔洞（``hole``，深度奇数）裁剪面积 **−** 扣减；
- 孔中“岛”（深度 2）重新计入，岛中孔继续扣减，依此类推。

裁剪采用 Sutherland–Hodgman 多边形裁剪：环被窗口四条边所在的
半平面依次截断，因此**穿越窗口但所有顶点都在窗外的环**（如完全
包住窗口的大环、边斜穿窗口的环）同样被正确计入；完全在窗外的环
裁剪为空，贡献 0。

环面积（含裁剪后面积）按**局部参考点下的稳定求和**计算（见
:func:`app.svggeo.signed_area`：鞋带求和在相对首顶点的坐标下
进行），远离原点的大坐标不会因 ``x1*y2 − x2*y1`` 大数相消而把
小环面积误判为 0；整体平移同一形状与窗口不改变覆盖面积与覆盖率。

- 开放（``open``）与退化（``degenerate``）轮廓不参与，列入
  ``skipped_contours``；
- 没有任何有效闭合区域（空路径、仅开放/退化轮廓，或净面积被孔洞
  完全抵消）时，逐窗返回 ``covered_area = 0.0``、``coverage = 0.0``；
- 净填充面积在数值误差内钳制到 ``[0, 窗口面积]``，``coverage``
  恒落在 ``[0, 1]``；
- 整个计算只读、即时完成：不依赖已保存的规范化结果，也不写库。
"""

from __future__ import annotations

from typing import Any

from .svggeo import Contour, signed_area

# 单次请求允许的最多窗口数，超限由路由层（Pydantic）返回 422
MAX_WINDOWS = 50
# 净面积小于该阈值（按窗口尺寸缩放）视为“无有效填充区域”
_AREA_EPS = 1e-10
# 输出量化位数（仅影响 JSON 展示，不改变判定）
_DECIMALS = 9


def compute_window_coverage(
        contours: list[Contour],
        windows: list[tuple[float, float, float, float]]) -> dict[str, Any]:
    """对已分类的轮廓列表逐窗口计算净填充面积与覆盖率。

    参数:
        contours: :func:`app.svggeo.analyze_contours` 返回的轮廓
            （已带 ``role`` / ``depth``）。
        windows: ``(xmin, ymin, xmax, ymax)`` 窗口列表，坐标均为
            有限数且宽高为正（由路由层模型保证）。

    返回:
        ``{window_count, contributing_contour_count, windows,
        skipped_contours}``；``windows`` 按输入顺序给出每项
        ``{index, window, window_area, covered_area, coverage}``。
    """
    valid = [c for c in contours if c.role in ("outer", "hole")]
    valid.sort(key=lambda c: c.original_index)

    skipped = [
        {"original_index": c.original_index, "role": c.role,
         "reason": ("open" if c.role == "open" else "degenerate")}
        for c in sorted(contours, key=lambda c: c.original_index)
        if c.role not in ("outer", "hole")
    ]

    results: list[dict[str, Any]] = []
    for idx, (xmin, ymin, xmax, ymax) in enumerate(windows):
        window_area = (xmax - xmin) * (ymax - ymin)
        eps = _AREA_EPS * max(1.0, xmax - xmin, ymax - ymin) ** 2

        net = 0.0
        for c in valid:
            # 包围盒剔除：与窗口不相交的环贡献为 0
            if (c.bounds[2] < xmin or c.bounds[0] > xmax
                    or c.bounds[3] < ymin or c.bounds[1] > ymax):
                continue
            clipped = _clip_ring_to_window(c.points, xmin, ymin, xmax, ymax)
            if len(clipped) < 3:
                continue
            area = abs(signed_area(clipped))
            net += area if c.role == "outer" else -area

        # 物理上净填充面积落在 [0, 窗口面积]；钳制吸收细分/裁剪浮点噪声
        if abs(net) <= eps:
            net = 0.0
        net = min(max(net, 0.0), window_area)

        results.append({
            "index": idx,
            "window": {"xmin": _num(xmin), "ymin": _num(ymin),
                       "xmax": _num(xmax), "ymax": _num(ymax)},
            "window_area": _num(window_area),
            "covered_area": _num(net),
            "coverage": _num(net / window_area),
        })

    return {
        "window_count": len(windows),
        "contributing_contour_count": len(valid),
        "windows": results,
        "skipped_contours": skipped,
    }


def _clip_ring_to_window(
        points: list[tuple[float, float]],
        xmin: float, ymin: float,
        xmax: float, ymax: float) -> list[tuple[float, float]]:
    """Sutherland–Hodgman：把闭合环依次裁剪到窗口四个半平面内。"""
    pts = _clip_halfplane(points, 0, xmin, keep_ge=True)   # x >= xmin
    pts = _clip_halfplane(pts, 0, xmax, keep_ge=False)     # x <= xmax
    pts = _clip_halfplane(pts, 1, ymin, keep_ge=True)      # y >= ymin
    pts = _clip_halfplane(pts, 1, ymax, keep_ge=False)     # y <= ymax
    return pts


def _clip_halfplane(points: list[tuple[float, float]],
                    axis: int, bound: float,
                    keep_ge: bool) -> list[tuple[float, float]]:
    """保留坐标 ``>= bound``（``keep_ge``）或 ``<= bound`` 的半平面。

    遍历闭合环的每条边（含末点回首点的闭合边）：入侧顶点保留，
    进出穿越处在边界上插入交点；全部顶点在另一侧时返回空列表。
    """
    out: list[tuple[float, float]] = []
    n = len(points)
    for i in range(n):
        cur = points[i]
        nxt = points[(i + 1) % n]
        if keep_ge:
            cur_in = cur[axis] >= bound
            nxt_in = nxt[axis] >= bound
        else:
            cur_in = cur[axis] <= bound
            nxt_in = nxt[axis] <= bound
        if cur_in:
            out.append(cur)
            if not nxt_in:
                out.append(_edge_intersect(cur, nxt, axis, bound))
        elif nxt_in:
            out.append(_edge_intersect(cur, nxt, axis, bound))
    return out


def _edge_intersect(a: tuple[float, float], b: tuple[float, float],
                    axis: int, bound: float) -> tuple[float, float]:
    """边 ``a→b`` 与直线 ``coord[axis] == bound`` 的交点（a、b 分属两侧）。"""
    t = (bound - a[axis]) / (b[axis] - a[axis])
    return (a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1]))


def _num(v: float) -> float:
    """量化展示值并消除 -0.0。"""
    r = round(v, _DECIMALS)
    return r + 0.0 if r == 0 else r
