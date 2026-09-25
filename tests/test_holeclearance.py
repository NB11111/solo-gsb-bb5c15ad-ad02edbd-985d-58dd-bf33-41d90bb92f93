"""孔洞最大内接圆（钻孔选点）的几何单元测试。"""

import math
import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.holeclearance import (  # noqa: E402
    MAX_GRID_CELLS,
    compute_hole_clearance,
)
from app.svggeo import analyze_contours, point_segment_dist2  # noqa: E402


def _run(d, precision=0.01, fill_rule="nonzero"):
    contours, _ = analyze_contours(d, fill_rule)
    return compute_hole_clearance(contours, precision)


def _min_boundary_dist(contours, hole_idx, islands, p):
    """独立复核：点到孔洞及各岛边界（细分折线）的最短距离。"""
    best = math.inf
    for c in [contours[hole_idx], *islands]:
        pts = c.points
        n = len(pts)
        for k in range(n):
            best = min(best,
                       point_segment_dist2(p, pts[k], pts[(k + 1) % n]))
    return math.sqrt(best)


# ---------------------------------------------------------------------------
# 基本形状
# ---------------------------------------------------------------------------

def test_square_hole_exact_center_and_radius():
    # 外方 0..10，内孔 2..8：最大内接圆即孔中心 (5,5)，半径 3
    r = _run("M0,0h10v10h-10Z M2,2h6v6h-6Z")
    assert r["hole_count"] == 1
    assert r["cells_used"] > 0
    hole = r["holes"][0]
    assert hole["original_index"] == 1
    assert hole["role"] == "hole" and hole["depth"] == 1
    assert hole["center"] == {"x": 5.0, "y": 5.0}
    assert hole["radius"] == 3.0
    assert r["precision"] == 0.01
    assert r["skipped_contours"] == [
        {"original_index": 0, "role": "outer", "reason": "outer"}]


def test_rectangle_hole_radius_half_short_side():
    # 长条孔 2..18 x 8..12：最大半径为短边一半 2，圆心在中线上
    r = _run("M0,0h20v20h-20Z M2,8h16v4h-16Z")
    hole = r["holes"][0]
    assert 2.0 - 0.01 <= hole["radius"] <= 2.0 + 1e-9
    assert abs(hole["center"]["y"] - 10.0) <= 0.02
    assert 4.0 - 0.02 <= hole["center"]["x"] <= 16.0 + 0.02


def test_concave_hole_does_not_fall_back_to_bbox_center():
    # L 形凹孔：包围盒中心 (10,10) 恰为凹角顶点（距离 0，不可用）；
    # 真实最优受左/上两边与凹角顶点 (10,10) 共同约束
    # t-2 = (10-t)*sqrt(2) -> t = (2+10*sqrt(2))/(1+sqrt(2))
    d = "M0,0h30v30h-30Z M2,2 L18,2 L18,10 L10,10 L10,18 L2,18 Z"
    t = (2 + 10 * math.sqrt(2)) / (1 + math.sqrt(2))
    expected = t - 2
    r = _run(d)
    hole = r["holes"][0]
    assert expected - 0.01 <= hole["radius"] <= expected + 1e-6
    # 圆心严格位于孔内（不是包围盒中心）
    assert hole["center"] != {"x": 10.0, "y": 10.0}
    assert hole["radius"] > 4.0  # 大于“臂宽一半”的朴素估计


def test_precision_bounds_the_radius_error():
    # 同一凹孔：粗/细两种精度下，返回半径均落在 [r*-precision, r*] 内
    d = "M0,0h30v30h-30Z M2,2 L18,2 L18,10 L10,10 L10,18 L2,18 Z"
    t = (2 + 10 * math.sqrt(2)) / (1 + math.sqrt(2))
    expected = t - 2
    coarse = _run(d, precision=0.5)["holes"][0]
    fine = _run(d, precision=0.01)["holes"][0]
    assert expected - 0.5 <= coarse["radius"] <= expected + 1e-6
    assert expected - 0.01 <= fine["radius"] <= expected + 1e-6
    assert coarse["radius"] <= fine["radius"] + 0.5


def test_curve_hole_uses_flattened_polyline():
    # 半径 5 的圆弧孔细分为 32 边形：最大内接圆半径为正 32 边形 apothem
    d = "M0,0h20v20h-20Z M15,10 A5,5 0 1,0 5,10 A5,5 0 1,0 15,10 Z"
    r = _run(d)
    hole = r["holes"][0]
    apothem = 5 * math.cos(math.pi / 32)
    assert apothem - 0.01 <= hole["radius"] <= apothem + 1e-6
    assert abs(hole["center"]["x"] - 10.0) < 0.05
    assert abs(hole["center"]["y"] - 10.0) < 0.05


# ---------------------------------------------------------------------------
# 直属岛
# ---------------------------------------------------------------------------

def test_eccentric_island_pushes_center_away():
    # 孔 2..18，偏心岛 3..9 占据一角：最优点在对角线上
    # 18-t = (t-9)*sqrt(2) -> t = (18+9*sqrt(2))/(1+sqrt(2))
    d = "M0,0h20v20h-20Z M2,2h16v16h-16Z M3,3h6v6h-6Z"
    t = (18 + 9 * math.sqrt(2)) / (1 + math.sqrt(2))
    expected = 18 - t
    r = _run(d)
    hole = r["holes"][0]
    assert expected - 0.01 <= hole["radius"] <= expected + 1e-6
    cx, cy = hole["center"]["x"], hole["center"]["y"]
    # 圆心不在岛内（岛为 3..9 正方形）
    assert not (3.0 < cx < 9.0 and 3.0 < cy < 9.0)


def test_radius_is_min_distance_to_hole_or_island_boundary():
    # 返回半径等于圆心到孔洞/岛边界的最短距离（独立复核）
    d = "M0,0h20v20h-20Z M2,2h16v16h-16Z M3,3h6v6h-6Z"
    contours, _ = analyze_contours(d, "nonzero")
    r = compute_hole_clearance(contours, 0.01)
    hole = r["holes"][0]
    center = (hole["center"]["x"], hole["center"]["y"])
    islands = [c for c in contours if c.role == "outer"
               and c.parent_original_index == 1]
    dist = _min_boundary_dist(contours, 1, islands, center)
    assert dist == pytest.approx(hole["radius"], abs=1e-6)


def test_island_in_hole_and_nested_hole_both_processed():
    # 外 0..100 / 孔 10..90 / 岛 40..60 / 岛中孔 45..55：
    # 孔 10..90 避开直属岛（对角平衡 t-10=(40-t)*sqrt(2)），
    # 深度 3 的岛中孔自身也是有效孔洞（半径 5）
    d = ("M0,0h100v100h-100Z M10,10h80v80h-80Z "
         "M40,40h20v20h-20Z M45,45h10v10h-10Z")
    t = (10 + 40 * math.sqrt(2)) / (1 + math.sqrt(2))
    r = _run(d)
    assert r["hole_count"] == 2
    assert [h["original_index"] for h in r["holes"]] == [1, 3]
    outer_hole, inner_hole = r["holes"]
    assert t - 10 - 0.01 <= outer_hole["radius"] <= t - 10 + 1e-6
    assert outer_hole["depth"] == 1
    assert inner_hole["depth"] == 3
    assert inner_hole["center"] == {"x": 50.0, "y": 50.0}
    assert inner_hole["radius"] == 5.0


def test_sibling_holes_each_get_own_circle():
    # 同级双孔分别求解，按原始索引升序返回
    d = "M0,0h30v10h-30Z M1,1h8v8h-8Z M11,1h18v8h-18Z"
    r = _run(d)
    assert [h["original_index"] for h in r["holes"]] == [1, 2]
    assert r["holes"][0]["center"] == {"x": 5.0, "y": 5.0}
    assert r["holes"][0]["radius"] == 4.0
    # 第二个孔 18x8：半径 4，圆心在中线 y=5 上（x 不唯一）
    assert r["holes"][1]["radius"] == 4.0
    assert r["holes"][1]["center"]["y"] == 5.0
    assert 15.0 <= r["holes"][1]["center"]["x"] <= 25.0


# ---------------------------------------------------------------------------
# 跳过规则与空结果
# ---------------------------------------------------------------------------

def test_no_valid_holes_returns_empty_array():
    r = _run("M0,0h10v10h-10Z")
    assert r["holes"] == []
    assert r["hole_count"] == 0
    assert r["cells_used"] == 0
    assert r["skipped_contours"] == [
        {"original_index": 0, "role": "outer", "reason": "outer"}]


def test_open_and_degenerate_subpaths_skipped():
    d = ("M0,0h10v10h-10Z M2,2h6v6h-6Z "   # outer + hole
         "M20,0 L30,0 L30,10 "              # open
         "M5,5 L8,5 L5,5 Z")                # degenerate
    r = _run(d)
    assert r["hole_count"] == 1
    assert {(s["original_index"], s["reason"]) for s in r["skipped_contours"]} == {
        (0, "outer"), (2, "open"), (3, "degenerate")}


def test_empty_path_returns_empty_array():
    r = _run("")
    assert r["holes"] == []
    assert r["hole_count"] == 0


def test_fill_rule_does_not_change_result():
    d = "M0,0h20v20h-20Z M2,2h16v16h-16Z M3,3h6v6h-6Z"
    assert _run(d, fill_rule="nonzero") == _run(d, fill_rule="evenodd")


def test_deterministic_across_calls():
    d = "M0,0h20v20h-20Z M2,2h16v16h-16Z M3,3h6v6h-6Z"
    assert _run(d) == _run(d)


# ---------------------------------------------------------------------------
# 参数校验与预算上限
# ---------------------------------------------------------------------------

def test_invalid_precision_rejected():
    d = "M0,0h10v10h-10Z M2,2h6v6h-6Z"
    for bad in (0, -1, -0.5, float("nan"), float("inf"), float("-inf")):
        with pytest.raises(ValueError):
            _run(d, precision=bad)


def test_grid_cell_budget_exceeded_raises():
    # 细长孔（980x1）+ 极小精度：细分需求远超 20000 格 -> ValueError
    d = "M0,0h1000v1000h-1000Z M10,10 L990,10 L990,11 L10,11 Z"
    with pytest.raises(ValueError, match=str(MAX_GRID_CELLS)):
        _run(d, precision=1e-9)


def test_cells_used_within_budget():
    r = _run("M0,0h20v20h-20Z M2,8h16v4h-16Z")
    assert 0 < r["cells_used"] <= MAX_GRID_CELLS
