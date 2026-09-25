"""轮廓交叉清单（intersections，只读即时检查）。

对一份已存 SVG path 即时解析（复用 :func:`app.svggeo.analyze_contours`
的解析与曲线细分折线，见 :func:`app.svggeo.flatten_subpath`），在
**加工前**对每条**闭合子路径**做两类边对检查：

- **自交**：同一闭合环内所有**非相邻边**对（相邻边仅共享端点，
  属正常连接，跳过；闭合环含末点回首点的闭合边）；
- **互交**：不同闭合子路径之间的全部边对。

开放轮廓（未以 ``Z`` 闭合）不参与（列入 ``skipped_contours``）；
闭合但退化的子路径（面积为 0 或顶点不足）仍按字面意义参与边检查。

交点分三类：

- ``crossing``：两线段在内部真正交叉（交点严格位于两条边内部）；
- ``endpoint_contact``：端点接触（某条边的端点落在另一条边上，
  含环内非相邻边共享端点的“收尖/捏合”构型、T 型接触与零长度边点接触）；
- ``collinear_overlap``：共线重叠（两条边共线且共享一段非零长度
  区间；仅共享一点时归为 ``endpoint_contact``）。

**同坐标、同类型**的交点合并为一条，合并其涉及的全部（轮廓, 边索引）；
``collinear_overlap`` 的合并键为（字典序排列的）重叠区间两端。
最终清单按涉及的轮廓/边索引稳定排序。整个计算只读、即时完成：
不依赖已保存的规范化结果，也不写库。
"""

from __future__ import annotations

import math
from typing import Any

from .svggeo import Contour

# 参数域容差：t/u 距端点 1e-9 以内视为端点接触，吸收细分浮点误差
_EPS_PAR = 1e-9
# 角域（无量纲）容差：|cross|/(|u||v|) <= 该值视为平行，即 sin(夹角) 阈值
_EPS_ANGLE = 1e-9
# 线域（无量纲）相对容差：判共线/点落边时的垂直距离阈值 = 该值 ×
# 对应长度基准（共线取较短边、点落边取被落边）
_EPS_LINE = 1e-9
# 输出量化位数（仅影响 JSON 展示，不改变判定与合并键）
_DECIMALS = 9

# 类型在输出中的固定排序（同一点位的不同类型不再合并，类型仅参与次序）
_TYPE_ORDER = {"crossing": 0, "endpoint_contact": 1, "collinear_overlap": 2}


def find_intersections(contours: list[Contour]) -> dict[str, Any]:
    """对已分类的轮廓列表生成加工前轮廓交叉清单。

    参数:
        contours: :func:`app.svggeo.analyze_contours` 返回的轮廓
            （已带 ``role``）。

    返回:
        ``{checked_contour_count, intersection_count, intersections,
        skipped_contours}``；``intersections`` 每项给出 ``type``、
        交点坐标（``point``；共线重叠为区间两端 ``points``）以及涉及的
        轮廓与边索引（``contours[].edge_indices``，边 k 为细分顶点
        ``points[k] -> points[(k+1) mod n]``，含闭合环的回首边）。
    """
    closed = [c for c in contours if c.closed]
    closed.sort(key=lambda c: c.original_index)

    skipped = [
        {"original_index": c.original_index, "role": c.role,
         "reason": ("open" if c.role == "open" else "degenerate")}
        for c in sorted(contours, key=lambda c: c.original_index)
        if not c.closed
    ]

    # 合并键 -> 聚合条目（同坐标同类型交点合并其涉及边索引）
    merged: dict[tuple, dict[str, Any]] = {}

    def add(kind: str, key: tuple, coord: Any,
            contour_idx: int, edge: int) -> None:
        item = merged.get(key)
        if item is None:
            item = {"kind": kind, "coord": coord,
                    "groups": {contour_idx: set()}}
            merged[key] = item
        item["groups"].setdefault(contour_idx, set()).add(edge)

    # ---- 环内自交：非相邻边对 --------------------------------------------
    for c in closed:
        pts, n = c.points, len(c.points)
        for i in range(n):
            a, b = pts[i], pts[(i + 1) % n]
            for j in range(i + 1, n):
                if j == i + 1 or (i == 0 and j == n - 1):
                    continue  # 相邻边仅共享端点，属正常连接
                p, q = pts[j], pts[(j + 1) % n]
                for kind, coord in _classify(a, b, p, q):
                    key = _merge_key(kind, coord)
                    add(kind, key, coord, c.original_index, i)
                    add(kind, key, coord, c.original_index, j)

    # ---- 不同闭合子路径之间：全部边对 ------------------------------------
    for ai in range(len(closed)):
        ca = closed[ai]
        pa, na = ca.points, len(ca.points)
        for cb in closed[ai + 1:]:
            pb, nb = cb.points, len(cb.points)
            for i in range(na):
                a, b = pa[i], pa[(i + 1) % na]
                for j in range(nb):
                    p, q = pb[j], pb[(j + 1) % nb]
                    for kind, coord in _classify(a, b, p, q):
                        key = _merge_key(kind, coord)
                        add(kind, key, coord, ca.original_index, i)
                        add(kind, key, coord, cb.original_index, j)

    role_by_index = {c.original_index: c.role for c in contours}
    intersections = [
        _entry(item, role_by_index)
        for item in sorted(merged.values(),
                           key=lambda it: _sort_key(it, role_by_index))
    ]

    return {
        "checked_contour_count": len(closed),
        "intersection_count": len(intersections),
        "intersections": intersections,
        "skipped_contours": skipped,
    }


# ---------------------------------------------------------------------------
# 线段对分类
# ---------------------------------------------------------------------------

def _classify(a, b, c, d):
    """分类线段 ab 与 cd 的关系。

    返回 ``(kind, coord)`` 元组的列表（一对线段至多一条），
    ``coord`` 为交点元组（共线重叠为两端点的元组）；无交点返回空列表。
    """
    ux, uy = b[0] - a[0], b[1] - a[1]
    vx, vy = d[0] - c[0], d[1] - c[1]
    wx, wy = a[0] - c[0], a[1] - c[1]
    uu = ux * ux + uy * uy
    vv = vx * vx + vy * vy
    lu, lv = math.sqrt(uu), math.sqrt(vv)
    # 角域容差无量纲（sin 夹角阈值）；线域容差也是无量纲相对值，由各判定
    # 乘上与自身量纲一致的长度基准（共线按较短边、点落边按被落边）。
    # 二者只依赖边向量（与原点位置无关），故同一构型整体平移或等比放大后
    # 判定不变：长度比 1e9 的长边遇短边正交相交不会再被吞进“平行”分支。
    sin_tol = _EPS_ANGLE
    line_rel = _EPS_LINE

    # 零长度边：退化为点是否落在另一条边上；距离容差按被落边长度缩放
    if uu == 0.0:
        return [("endpoint_contact", (a,))] if _point_on_segment(c, d, a, line_rel) else []
    if vv == 0.0:
        return [("endpoint_contact", (c,))] if _point_on_segment(a, b, c, line_rel) else []

    denom = ux * vy - uy * vx
    if abs(denom) > sin_tol * lu * lv:
        # 非平行：解 a + t·u = c + s·v（w = a - c）
        t = (wy * vx - wx * vy) / denom
        s = (wy * ux - wx * uy) / denom
        if t < -_EPS_PAR or t > 1 + _EPS_PAR or s < -_EPS_PAR or s > 1 + _EPS_PAR:
            return []
        tc = min(1.0, max(0.0, t))
        point = (a[0] + tc * ux, a[1] + tc * uy)
        at_end = (t <= _EPS_PAR or t >= 1 - _EPS_PAR
                  or s <= _EPS_PAR or s >= 1 - _EPS_PAR)
        kind = "endpoint_contact" if at_end else "crossing"
        return [(kind, (point,))]

    # 平行：垂直距离 |u×w|/|u| 超过相对容差 × 较短边长度则不共线
    if abs(ux * wy - uy * wx) > line_rel * lu * min(lu, lv):
        return []

    # 共线：沿 u 方向参数化（q = a + s·u），比较两线段的参数区间
    def param(p):
        return ((p[0] - a[0]) * ux + (p[1] - a[1]) * uy) / uu

    s3, s4 = param(c), param(d)
    lo = max(0.0, min(s3, s4))
    hi = min(1.0, max(s3, s4))
    if hi < lo - _EPS_PAR:
        return []  # 共线但不重叠
    if hi - lo <= _EPS_PAR:
        t = min(1.0, max(0.0, lo))
        return [("endpoint_contact", ((a[0] + t * ux, a[1] + t * uy),))]

    p_lo = (a[0] + lo * ux, a[1] + lo * uy)
    p_hi = (a[0] + hi * ux, a[1] + hi * uy)
    if (p_hi[0], p_hi[1]) < (p_lo[0], p_lo[1]):
        p_lo, p_hi = p_hi, p_lo  # 端点按字典序排列
    return [("collinear_overlap", (p_lo, p_hi))]


def _point_on_segment(a, b, p, line_rel) -> bool:
    """点 p 是否在线段 ab 上（含端点）。

    ``line_rel`` 为无量纲相对容差：垂直距离阈值 ``line_rel·|ab|``，
    沿轴包围盒的纵向余量同口径（长度量纲），故等比放大后判定不变。
    """
    ux, uy = b[0] - a[0], b[1] - a[1]
    length = math.hypot(ux, uy)
    dist_eps = line_rel * length
    cross = ux * (p[1] - a[1]) - uy * (p[0] - a[0])
    if abs(cross) > dist_eps * length:
        return False
    return (min(a[0], b[0]) - dist_eps <= p[0] <= max(a[0], b[0]) + dist_eps
            and min(a[1], b[1]) - dist_eps <= p[1] <= max(a[1], b[1]) + dist_eps)


# ---------------------------------------------------------------------------
# 合并 / 输出
# ---------------------------------------------------------------------------

def _merge_key(kind: str, coord: tuple) -> tuple:
    """同坐标同类型交点的合并键（相对量化，吸收表示层浮点噪声）。"""
    if kind == "collinear_overlap":
        p1, p2 = coord
        return (kind, _qm(p1), _qm(p2))
    return (kind, _qm(coord[0]))


def _q(p: tuple[float, float]) -> tuple[float, float]:
    """显示量化：绝对 9 位小数量化（仅影响 JSON 展示）。"""
    qx, qy = round(p[0], _DECIMALS), round(p[1], _DECIMALS)
    return (qx + 0.0 if qx == 0 else qx,
            qy + 0.0 if qy == 0 else qy)


def _qm(p: tuple[float, float]) -> tuple[float, float]:
    """合并键量化：按坐标量级取**相对**精度（量纲一致）。

    同一几何交点由不同边对独立插值或由顶点字面量给出时，在大坐标
    （如 1e13 处 ULP≈5e-4）下绝对 1e-9 量化无法吸收其差，会把同一
    交点拆成多条；按相对 1e-9（即 ``_EPS_PAR``，与参数域容差同口径）
    取分格则与位置、尺度无关：整体平移或等比放大后合并结果一致。
    """
    def one(v: float) -> float:
        if v == 0.0:
            return 0.0
        step = 10 ** math.floor(math.log10(abs(v))) * _EPS_PAR
        return round(v / step) * step
    return (one(p[0]), one(p[1]))


def _pt(p: tuple[float, float]) -> dict[str, float]:
    qx, qy = _q(p)
    return {"x": qx, "y": qy}


def _sort_key(item: dict[str, Any], role_by_index: dict[int, str]) -> tuple:
    """按涉及的（轮廓索引, 最小边索引）序列稳定排序，坐标/类型兜底。"""
    groups = sorted(
        (idx, min(edges)) for idx, edges in item["groups"].items())
    coord = item["coord"]
    if item["kind"] == "collinear_overlap":
        tail = (_q(coord[0]) + _q(coord[1]))
    else:
        tail = _q(coord[0])
    return (tuple(groups), _TYPE_ORDER[item["kind"]], tail)


def _entry(item: dict[str, Any],
           role_by_index: dict[int, str]) -> dict[str, Any]:
    kind, coord = item["kind"], item["coord"]
    groups = [
        {"original_index": idx,
         "role": role_by_index.get(idx, "unknown"),
         "edge_indices": sorted(item["groups"][idx])}
        for idx in sorted(item["groups"])
    ]
    if kind == "collinear_overlap":
        return {"type": kind, "points": [_pt(coord[0]), _pt(coord[1])],
                "contours": groups}
    return {"type": kind, "point": _pt(coord[0]), "contours": groups}
