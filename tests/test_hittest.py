"""批量点命中测试的几何单元测试。"""

import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.hittest import MAX_HIT_POINTS, hit_test_points  # noqa: E402
from app.svggeo import analyze_contours  # noqa: E402


def _hit(d, points, fill_rule="nonzero"):
    contours, _ = analyze_contours(d, fill_rule)
    return hit_test_points(contours, list(map(tuple, points)), fill_rule)


def _results(d, points, fill_rule="nonzero"):
    return [r["result"] for r in _hit(d, points, fill_rule)["results"]]


def _map(d, points, fill_rule="nonzero"):
    return {(r["point"]["x"], r["point"]["y"]): r["result"]
            for r in _hit(d, points, fill_rule)["results"]}


# ---------------------------------------------------------------------------
# 基本判定：inside / outside / boundary
# ---------------------------------------------------------------------------

def test_simple_square():
    d = "M0,0h10v10h-10Z"
    pts = [(5, 5), (1, 1), (-1, 5), (50, 50), (0, 0), (10, 10),
           (5, 0), (0, 5), (5, 10), (10, 5)]
    assert _results(d, pts) == [
        "inside", "inside", "outside", "outside",
        "boundary", "boundary", "boundary", "boundary",
        "boundary", "boundary"]


def test_results_preserve_input_order_and_echo_index_point():
    d = "M0,0h10v10h-10Z"
    pts = [(20, 20), (5, 5), (0, 0)]
    res = _hit(d, pts)["results"]
    assert [r["index"] for r in res] == [0, 1, 2]
    assert [r["point"] for r in res] == [
        {"x": 20.0, "y": 20.0}, {"x": 5.0, "y": 5.0}, {"x": 0.0, "y": 0.0}]
    assert [r["result"] for r in res] == ["outside", "inside", "boundary"]


def test_boundary_on_every_edge_including_closing_edge():
    # 逆时针方块，闭合边 (0,10)->(0,0) 上的中点也要判 boundary
    d = "M0,0 L10,0 L10,10 L0,10 Z"
    pts = [(5, 0), (10, 5), (5, 10), (0, 5), (0, 0), (10, 10)]
    assert _results(d, pts) == ["boundary"] * 6


def test_boundary_on_diagonal_edge():
    d = "M0,0 L10,0 L10,10 Z"
    # 斜边 (0,0)->(10,10)
    assert _map(d, [(3, 3), (7, 7), (3, 4)]) == {
        (3, 3): "boundary", (7, 7): "boundary", (3, 4): "outside"}


def test_extreme_aspect_ratio_ring_has_no_visible_boundary_band():
    # 长宽 1e9:1 的闭合矩形：整环跨度不得沿长边扩出可见边界带
    d = "M0 0 H1000000000 V10 H0 Z"
    pts = [(5, 0.5),                  # 长边内侧 0.5 单位 -> inside
           (1000000000.5, 5),         # 右边外侧 0.5 单位 -> outside
           (5, 0),                    # 真正落在长边上 -> boundary
           (5, 10), (0, 5),
           (1000000000, 5),
           (0, 0), (1000000000, 10)]  # 端点
    assert _results(d, pts, "nonzero") == [
        "inside", "outside", "boundary",
        "boundary", "boundary", "boundary", "boundary", "boundary"]
    assert _results(d, pts, "evenodd") == [
        "inside", "outside", "boundary",
        "boundary", "boundary", "boundary", "boundary", "boundary"]
    # 远在长边外端的点也只按局部误差判定，不被整环跨度拉入 boundary
    m = _map(d, [(500000000, 0.5), (500000000, 5), (500000000, -0.5)])
    assert m == {(500000000, 0.5): "inside",
                 (500000000, 5): "inside",
                 (500000000, -0.5): "outside"}
    # 半像素级（0.5 单位）的边外近点在短边两端同样不得误判 boundary
    assert _results(d, [(-0.5, 5), (5, 10.5)]) == ["outside", "outside"]


def test_diagonal_edge_uses_same_local_tolerance():
    # 远处的斜边：同口径（局部数值误差）判定，端点与微小偏移分清
    d = "M1000000000,1000000000 L1000000010,1000000000 L1000000010,1000000010 Z"
    # 闭合斜边 (1e9+10,1e9+10)->(1e9,1e9) 的中点与端点
    pts = [(1000000005.0, 1000000005.0),
           (1000000000.0, 1000000000.0),
           (1000000010.0, 1000000010.0)]
    assert _results(d, pts) == ["boundary", "boundary", "boundary"]
    # 斜边法向 0.5 单位偏移：(dx=-0.25,dy=+0.25) 落在三角形外侧，
    # (dx=+0.25,dy=-0.25) 落在内侧，两者都不得因斜边容差判 boundary
    assert _results(d, [(1000000004.75, 1000000005.25),
                        (1000000005.25, 1000000004.75)]) == [
        "outside", "inside"]


def test_zero_length_edge_at_far_coordinates_matches_only_point():
    # 远处退化为单点的闭合路径：零长边只匹配该端点，不扩出可见带
    d = "M1000000000,5 Z"
    m = _map(d, [(1000000000.0, 5.0),
                 (1000000000.5, 5.0),
                 (1000000000.0, 5.5)])
    assert m == {(1000000000.0, 5.0): "boundary",
                 (1000000000.5, 5.0): "outside",
                 (1000000000.0, 5.5): "outside"}


def test_far_translated_collinear_points_still_boundary():
    # 平移到 1e12 处的细矩形：绝对坐标叉积会大数相消，
    # 边上细分/插值点（与曲线细分顶点同口径）仍必须判 boundary
    ox = 1_000_000_000_000.0
    oy = 1_000_000_000_000.0
    d = (f"M{ox},{oy} H{ox+1000} V{oy+10} H{ox} Z")
    mid_x = ox + (1000.0 / 12.0)   # 非简单比例的插值点
    pts = [(mid_x, oy),                 # 长边上的插值点 -> boundary
           (ox + 500, oy + 0.5),        # 长边内侧 -> inside
           (ox + 1000.5, oy + 5),       # 右边外侧 -> outside
           (ox, oy), (ox + 1000, oy + 10)]
    assert _results(d, pts) == [
        "boundary", "inside", "outside", "boundary", "boundary"]


# ---------------------------------------------------------------------------
# 填充规则：外-孔、同向/反向重叠
# ---------------------------------------------------------------------------

def test_outer_with_hole_both_rules():
    # 内孔反向绘制（逆时针）：nonzero 绕数抵消、evenodd 交叉为偶，均为 outside
    d = "M0,0h10v10h-10Z M2,8h6v-6h-6Z"
    pts = [(1, 1), (5, 5), (3, 5), (2, 2), (-1, 5)]
    assert _results(d, pts, "nonzero") == [
        "inside", "outside", "outside", "boundary", "outside"]
    assert _results(d, pts, "evenodd") == [
        "inside", "outside", "outside", "boundary", "outside"]


def test_same_winding_inner_square_is_filled_under_nonzero():
    # 同向内方在 nonzero 下被填充（原始方向，不做方向规范化），evenodd 下仍是孔
    d = "M0,0h10v10h-10Z M2,2h6v6h-6Z"
    assert _results(d, [(5, 5)], "nonzero") == ["inside"]
    assert _results(d, [(5, 5)], "evenodd") == ["outside"]


def test_same_direction_overlapping_contours():
    # 两个同向方块重叠：nonzero 绕数累加 -> inside；evenodd 交叉 2 次 -> outside
    d = "M0,0h10v10h-10Z M0,0h10v10h-10Z"
    assert _results(d, [(5, 5)], "nonzero") == ["inside"]
    assert _results(d, [(5, 5)], "evenodd") == ["outside"]
    assert _results(d, [(15, 5)], "nonzero") == ["outside"]


def test_opposite_direction_overlapping_contours():
    # 反向重叠：nonzero 绕数抵消 -> outside；evenodd 同样 outside
    d = "M0,0h10v10h-10Z M0,0 L0,10 L10,10 L10,0 Z"
    assert _results(d, [(5, 5)], "nonzero") == ["outside"]
    assert _results(d, [(5, 5)], "evenodd") == ["outside"]


def test_evenodd_three_overlaps():
    # 三个同向方块重叠：交叉 3 次 -> 奇数 inside（两种规则此处一致）
    d = "M0,0h10v10h-10Z M0,0h10v10h-10Z M0,0h10v10h-10Z"
    assert _results(d, [(5, 5)], "evenodd") == ["inside"]
    assert _results(d, [(5, 5)], "nonzero") == ["inside"]


# ---------------------------------------------------------------------------
# 零净面积闭合自交路径仍参与
# ---------------------------------------------------------------------------

def test_bowtie_zero_area_closed_still_participates():
    # 蝴蝶结：两三角叶 (0,0)-(5,5)-(0,10) 与 (10,0)-(5,5)-(10,10)
    d = "M0,0 L10,10 L10,0 L0,10 Z"
    pts = [(1, 5), (9, 5), (5, 2), (5, 8), (5, 5), (2, 2), (8, 8)]
    expected = ["inside", "inside", "outside", "outside",
                "boundary", "boundary", "boundary"]
    assert _results(d, pts, "nonzero") == expected
    assert _results(d, pts, "evenodd") == expected
    assert _hit(d, pts)["checked_contour_count"] == 1


def test_degenerate_closed_line_and_point_participate_as_boundary():
    # 闭合但退化为线段 / 单点：自身不产生填充区域，但边仍是 boundary
    d = "M0,0 L10,0 L0,0 Z M20,20 Z"
    m = _map(d, [(5, 0), (5, 1), (20, 20), (20, 21)])
    assert m == {(5, 0): "boundary", (5, 1): "outside",
                 (20, 20): "boundary", (20, 21): "outside"}


# ---------------------------------------------------------------------------
# 开放子路径不参与
# ---------------------------------------------------------------------------

def test_open_subpaths_never_participate():
    d = "M0,0h10v10h-10Z M20,0 L30,0 L30,10"
    res = _hit(d, [(5, 5), (25, 0), (25, 5)])
    assert [r["result"] for r in res["results"]] == [
        "inside", "outside", "outside"]  # 落在开放折线上也不是 boundary
    assert res["skipped_contours"] == [
        {"original_index": 1, "role": "open", "reason": "open"}]
    assert res["checked_contour_count"] == 1


def test_only_open_subpaths_all_outside():
    d = "M0,0 L10,0 L10,10 M20,20 L30,30"
    res = _hit(d, [(5, 0), (0, 0), (25, 25)])
    assert [r["result"] for r in res["results"]] == [
        "outside", "outside", "outside"]
    assert res["checked_contour_count"] == 0
    assert {s["original_index"] for s in res["skipped_contours"]} == {0, 1}


def test_empty_path():
    res = _hit("", [(0, 0), (5, 5)])
    assert [r["result"] for r in res["results"]] == ["outside", "outside"]
    assert res["point_count"] == 2
    assert res["checked_contour_count"] == 0
    assert res["skipped_contours"] == []


# ---------------------------------------------------------------------------
# 曲线细分
# ---------------------------------------------------------------------------

def test_curve_flattened_ring():
    # 单位圆（两段半圆弧闭合）
    d = "M-1,0 A1,1 0 1,0 1,0 A1,1 0 1,0 -1,0 Z"
    pts = [(0, 0), (2, 0), (-1, 0), (1, 0)]
    assert _results(d, pts) == [
        "inside", "outside", "boundary", "boundary"]


def test_quadratic_curve_point_on_flattened_vertex():
    # 二次贝塞尔 (0,0)->(10,0)->(10,10)：t=0.5 细分点 (7.5,2.5) 落在折线上
    d = "M0,0 Q10,0 10,10 L0,10 Z"
    m = _map(d, [(7.5, 2.5), (5, 5), (10, 10)])
    assert m == {(7.5, 2.5): "boundary", (5, 5): "inside",
                 (10, 10): "boundary"}


# ---------------------------------------------------------------------------
# 凹多边形与射线顶点规则
# ---------------------------------------------------------------------------

def test_concave_polygon_half_open_vertex_rule():
    # C 形（凹但不自交），y=5/y=25 水平线上在实体臂内，y=15 仅 x<10 为实体
    d = "M0,0 L30,0 L30,10 L10,10 L10,20 L30,20 L30,30 L0,30 Z"
    pts = [(5, 5), (20, 5), (5, 25), (20, 25), (5, 15), (20, 15)]
    assert _results(d, pts) == [
        "inside", "inside", "inside", "inside", "inside", "outside"]


# ---------------------------------------------------------------------------
# 汇总信息与参数校验
# ---------------------------------------------------------------------------

def test_checked_count_and_skips_mixed():
    d = ("M0,0h10v10h-10Z M2,2h4v4h-4Z "      # 闭合 outer + hole
         "M100,0 L110,0 L110,10 "                # open
         "M0,0 L5,5 L0,0 Z")                     # 闭合退化
    res = _hit(d, [(1, 1)])
    assert res["checked_contour_count"] == 3
    assert res["skipped_contours"] == [
        {"original_index": 2, "role": "open", "reason": "open"}]
    assert res["point_count"] == 1


def test_two_point_open_segment_marked_degenerate_but_still_skipped():
    # 仅两个点的未闭合子路径角色为 degenerate，但仍不参与命中
    d = "M0,0h10v10h-10Z M20,0 L30,0"
    res = _hit(d, [(25, 0)])
    assert res["checked_contour_count"] == 1
    assert [r["result"] for r in res["results"]] == ["outside"]
    assert res["skipped_contours"] == [
        {"original_index": 1, "role": "degenerate", "reason": "degenerate"}]


def test_invalid_fill_rule_raises():
    contours, _ = analyze_contours("M0,0h10v10h-10Z", "nonzero")
    with pytest.raises(ValueError):
        hit_test_points(contours, [(1.0, 1.0)], "winding")


def test_max_hit_points_constant():
    assert MAX_HIT_POINTS == 500


def test_deterministic_across_calls():
    d = "M0,0h10v10h-10Z M2,2h6v6h-6Z M20,0 L30,10"
    a = _hit(d, [(1, 1), (5, 5), (25, 5)], "evenodd")
    b = _hit(d, [(1, 1), (5, 5), (25, 5)], "evenodd")
    assert a == b
