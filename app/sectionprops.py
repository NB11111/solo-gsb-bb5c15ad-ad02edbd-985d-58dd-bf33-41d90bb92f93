"""截面惯性矩分析（section-properties，只读即时分析）。

对一份已存 SVG path 即时解析并完成轮廓包含层级分类后（与
metrics / clearance / scanlines 等同一份解析与曲线细分折线，见
:func:`app.svggeo.analyze_contours` 与
:func:`app.svggeo.flatten_subpath`），对每个**有效闭合环**
（``outer`` / ``hole``）在细分多边形上按格林公式积分面积与
一、二阶面积矩，再按**包含层级交替**合成净填充截面：

- 外环（``outer``，深度偶数）**+** 计入；
- 孔洞（``hole``，深度奇数）**−** 扣减；
- 孔中“岛”（深度 2）重新计入，岛中孔继续扣减，依此类推。

所有惯性矩均绕**净填充质心** ``(cx, cy)`` 取值：

- ``Ix  = ∫(y−cy)² dA``（绕质心 x 轴的二次矩）
- ``Iy  = ∫(x−cx)² dA``（绕质心 y 轴的二次矩）
- ``Ixy = ∫(x−cx)(y−cy) dA``（质心惯性积）

主惯性矩 ``max`` / ``min`` 为二维惯性张量 ``[[Iy, Ixy], [Ixy, Ix]]``
的两个特征值；``axis_angle_deg`` 为**较大主值**对应主轴的方向角：
按 SVG 屏幕坐标（y 轴向下）从 +x 朝 +y 计量，取值 [0, 180) 度；
两个主值相等（各向同性，如正方形、圆）时主轴不唯一，约定返回 0°。

- 一阶面积矩在统一**局部参考点**下累加（与 metrics 同口径），
  每环二阶矩先绕**自身质心**积分，再按平行轴定理搬到净质心合成，
  避免远离原点的大坐标相消误差；方向符号归一后结果与环的书写方向无关；
- 开放（``open``）与退化（``degenerate``）轮廓不参与，列入
  ``skipped_contours``；
- 净填充面积为零（空路径、仅开放/退化轮廓，或净面积被孔洞完全
  抵消）时 ``centroid``、``ix`` / ``iy`` / ``ixy`` 与
  ``principal`` 均为 ``null``；
- 整个计算只读、即时完成：不依赖已保存的规范化结果，也不写库。
"""

from __future__ import annotations

import math
from typing import Any

from .metrics import polygon_centroid
from .svggeo import Contour

# 净面积小于该阈值（按轮廓尺寸缩放）视为“无有效填充区域”（与 metrics 同口径）
_AREA_EPS = 1e-10
# 输出量化位数（仅影响 JSON 展示，不改变判定）
_DECIMALS = 9


def compute_section_properties(contours: list[Contour]) -> dict[str, Any]:
    """对已分类的轮廓列表计算净填充截面的质心惯性矩与主惯性矩。

    参数:
        contours: :func:`app.svggeo.analyze_contours` 返回的轮廓
            （已带 ``role`` / ``depth``）。

    返回:
        ``{area, centroid, ix, iy, ixy, principal,
        contributing_contour_count, contours, skipped_contours}``；
        无有效填充区域时 ``centroid`` / ``ix`` / ``iy`` / ``ixy`` /
        ``principal`` 为 ``None``。
    """
    valid = [c for c in contours if c.role in ("outer", "hole")]
    valid.sort(key=lambda c: c.original_index)

    scale = 1.0
    for c in valid:
        diag = math.hypot(c.bounds[2] - c.bounds[0], c.bounds[3] - c.bounds[1])
        scale = max(scale, diag)
    eps = _AREA_EPS * scale ** 2

    total_area = 0.0
    moment_x = 0.0  # Σ sign·A·(cx−ref_x)，统一局部参考点下对 y 轴的一阶面积矩
    moment_y = 0.0  # Σ sign·A·(cy−ref_y)，统一局部参考点下对 x 轴的一阶面积矩
    rings: list[dict[str, float]] = []
    items: list[dict[str, Any]] = []

    # 与 metrics 同口径：一阶面积矩在统一局部参考点（首个有效环的
    # 首顶点）下累加，避免远离原点时大数相消吞掉偏心孔的微小净矩
    ref_x, ref_y = valid[0].points[0] if valid else (0.0, 0.0)

    for c in valid:
        sign = 1.0 if c.role == "outer" else -1.0
        ring_area = abs(c.area)
        cx, cy = polygon_centroid(c.points, c.area)
        ixx, iyy, ixy = _ring_second_moments(c.points, cx, cy, c.area)
        signed_contrib = sign * ring_area
        total_area += signed_contrib
        moment_x += signed_contrib * (cx - ref_x)
        moment_y += signed_contrib * (cy - ref_y)
        rings.append({"sign": sign, "area": ring_area, "cx": cx, "cy": cy,
                      "ixx": ixx, "iyy": iyy, "ixy": ixy})
        items.append({
            "original_index": c.original_index,
            "role": c.role,
            "depth": c.depth,
            "area": _num(ring_area),
            "signed_contribution": _num(signed_contrib),
            "centroid": {"x": _num(cx), "y": _num(cy)},
        })

    skipped = [
        {"original_index": c.original_index, "role": c.role,
         "reason": ("open" if c.role == "open" else "degenerate")}
        for c in sorted(contours, key=lambda c: c.original_index)
        if c.role not in ("outer", "hole")
    ]

    # 净面积被孔洞完全抵消（理论上的等面积异常嵌套）或本就没有闭合区域
    if abs(total_area) <= eps:
        return {
            "area": 0.0,
            "centroid": None,
            "ix": None,
            "iy": None,
            "ixy": None,
            "principal": None,
            "contributing_contour_count": len(valid),
            "contours": items,
            "skipped_contours": skipped,
        }

    cx_net = ref_x + moment_x / total_area
    cy_net = ref_y + moment_y / total_area

    # 平行轴定理：各环绕自身质心的二阶矩搬到净填充质心后交替代数合成
    ix = iy = ixy_net = 0.0
    for r in rings:
        dx = r["cx"] - cx_net
        dy = r["cy"] - cy_net
        ix += r["sign"] * (r["ixx"] + r["area"] * dy * dy)
        iy += r["sign"] * (r["iyy"] + r["area"] * dx * dx)
        ixy_net += r["sign"] * (r["ixy"] + r["area"] * dx * dy)

    # 数学上 Ix/Iy ≥ 0；层级抵消的浮点噪声把微小负值钳到 0
    eps_i = 1e-9 * scale ** 4
    if -eps_i <= ix < 0.0:
        ix = 0.0
    if -eps_i <= iy < 0.0:
        iy = 0.0

    # 主惯性矩：惯性张量 [[Iy, Ixy], [Ixy, Ix]] 的两个特征值
    avg = (ix + iy) / 2.0
    radius = math.hypot((ix - iy) / 2.0, ixy_net)
    i_max = avg + radius
    i_min = avg - radius
    if -eps_i <= i_min < 0.0:
        i_min = 0.0

    # 较大主值对应的主轴角：tan(2θ) = -2·Ixy / (Ix − Iy)，
    # 从 +x 朝 +y（SVG y 轴向下）计，量化到 [0, 180)；主值相等约定 0°
    if radius <= 1e-9 * max(abs(ix), abs(iy)):
        angle = 0.0
    else:
        angle = math.degrees(
            math.atan2(-2.0 * ixy_net, ix - iy) / 2.0) % 180.0
        angle = _num(angle)
        if angle >= 180.0:  # 量化进位到 180 时回到 0
            angle = 0.0

    return {
        "area": _num(total_area),
        "centroid": {"x": _num(cx_net), "y": _num(cy_net)},
        "ix": _num(ix),
        "iy": _num(iy),
        "ixy": _num(ixy_net),
        "principal": {
            "max": _num(i_max),
            "min": _num(i_min),
            "axis_angle_deg": angle,
        },
        "contributing_contour_count": len(valid),
        "contours": items,
        "skipped_contours": skipped,
    }


def _ring_second_moments(points: list[tuple[float, float]],
                         cx: float, cy: float,
                         area: float) -> tuple[float, float, float]:
    """细分多边形绕**自身质心**的二阶面积矩 ``(∫y²dA, ∫x²dA, ∫xydA)``。

    先在质心相对坐标下用格林公式精确积分（避免远离原点的大坐标
    相消），再按环绕方向符号归一——返回正面积意义下的矩，
    与环的书写方向无关。
    """
    factor = 1.0 if area > 0 else -1.0
    pts = [(x - cx, y - cy) for x, y in points]
    sxx = syy = sxy = 0.0
    for (x1, y1), (x2, y2) in zip(pts, pts[1:] + pts[:1]):
        cross = x1 * y2 - x2 * y1
        sxx += (y1 * y1 + y1 * y2 + y2 * y2) * cross
        syy += (x1 * x1 + x1 * x2 + x2 * x2) * cross
        sxy += (x1 * y2 + 2.0 * x1 * y1
                + 2.0 * x2 * y2 + x2 * y1) * cross
    return (factor * sxx / 12.0,
            factor * syy / 12.0,
            factor * sxy / 24.0)


def _num(v: float) -> float:
    """量化展示值并消除 -0.0。"""
    r = round(v, _DECIMALS)
    return r + 0.0 if r == 0 else r
