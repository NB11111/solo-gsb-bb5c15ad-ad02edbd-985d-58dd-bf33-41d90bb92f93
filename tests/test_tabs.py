"""外环微桥布置（tabs）的几何单元测试。"""

import math
import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.svggeo import analyze_contours  # noqa: E402
from app.tabs import MAX_TABS, compute_tabs  # noqa: E402


def _tabs(d, count, width, corner_margin=0.0, fill_rule="nonzero"):
    contours, _ = analyze_contours(d, fill_rule)
    return contours, compute_tabs(contours, count, width, corner_margin)


def _vertex_arcs(points):
    """细分折线上各顶点的累计弧长（首尾相接，最后一个为周长）。"""
    cum = [0.0]
    n = len(points)
    for i in range(n):
        ax, ay = points[i]
        bx, by = points[(i + 1) % n]
        cum.append(cum[-1] + math.hypot(bx - ax, by - ay))
    return cum


def _assert_constraints(result, contours, count, width, margin):
    """独立校验：扇区归属、拐角余量、桥宽与互不重叠。"""
    for ce in result["contours"]:
        contour = next(c for c in contours
                       if c.original_index == ce["original_index"])
        cum = _vertex_arcs(contour.points)
        perimeter = cum[-1]
        assert ce["tab_count"] + ce["unplaced_count"] == count
        placed = ce["tabs"]
        centers = []
        for tab in placed:
            s, e, c = (tab["start"]["arc"], tab["end"]["arc"],
                       tab["center"]["arc"])
            assert math.isclose(e - s, width, abs_tol=1e-8)
            assert math.isclose(c - s, width / 2, abs_tol=1e-8)
            # 中心必须落在所属扇区内
            k = tab["sector"]
            assert k * perimeter / count - 1e-9 <= c \
                <= (k + 1) * perimeter / count + 1e-9
            # 桥段不得跨入顶点两侧的拐角余量区
            for v in cum[:-1]:
                dv = min(abs(c - v), perimeter - abs(c - v))
                assert dv >= margin + width / 2 - 1e-9
            centers.append(c)
        # 桥段互不重叠（环向上中心间距 >= 桥宽，端点相切允许）
        centers.sort()
        for a, b in zip(centers, centers[1:]):
            assert b - a >= width - 1e-9
        if centers:
            assert perimeter - centers[-1] + centers[0] >= width - 1e-9


# ---------------------------------------------------------------------------
# 基本布置：周长等分扇区中点目标
# ---------------------------------------------------------------------------

def test_square_tabs_at_sector_midpoints():
    # 40x40 正方形周长 160，4 扇区中点即各边中点 20/60/100/140
    contours, result = _tabs("M0,0h40v40h-40Z", 4, 4, 5)
    ce = result["contours"][0]
    assert ce["original_index"] == 0
    assert ce["role"] == "outer"
    assert ce["depth"] == 0
    assert ce["perimeter"] == 160.0
    assert ce["tab_count"] == 4 and ce["unplaced_count"] == 0
    assert result["tab_count"] == 4 and result["unplaced_count"] == 0
    assert [t["sector"] for t in ce["tabs"]] == [0, 1, 2, 3]

    expect = [
        ((18.0, 0.0), (22.0, 0.0), (20.0, 0.0), 20.0),
        ((40.0, 18.0), (40.0, 22.0), (40.0, 20.0), 60.0),
        ((22.0, 40.0), (18.0, 40.0), (20.0, 40.0), 100.0),
        ((0.0, 22.0), (0.0, 18.0), (0.0, 20.0), 140.0),
    ]
    for tab, (st, en, ce_pt, arc) in zip(ce["tabs"], expect):
        assert (tab["start"]["x"], tab["start"]["y"]) == st
        assert (tab["end"]["x"], tab["end"]["y"]) == en
        assert (tab["center"]["x"], tab["center"]["y"]) == ce_pt
        assert tab["start"]["arc"] == arc - 2
        assert tab["end"]["arc"] == arc + 2
        assert tab["center"]["arc"] == arc
    _assert_constraints(result, contours, 4, 4, 5)


def test_request_echoes_in_payload():
    _, result = _tabs("M0,0h40v40h-40Z", 4, 4, 5)
    assert result["count"] == 4
    assert result["width"] == 4.0
    assert result["corner_margin"] == 5.0


def test_default_corner_margin_is_zero():
    # 等价显式传 0
    contours, _ = analyze_contours("M0,0h40v40h-40Z", "nonzero")
    r1 = compute_tabs(contours, 4, 4)
    r2 = compute_tabs(contours, 4, 4, 0.0)
    assert r1 == r2


# ---------------------------------------------------------------------------
# 拐角余量与不可行位置
# ---------------------------------------------------------------------------

def test_bridge_kept_inside_corner_margin():
    # 20x20 正方形周长 80，2 扇区：目标弧长 20（顶点）与 60（顶点），
    # margin=6、width=4：顶点两侧各 6+2=8 内不可放，
    # 扇区 0 [0,40) 内边 0 可行中心 [8,12]、边 1 [28,32]，
    # 距目标 20 等距（8），取较小弧长 12
    contours, result = _tabs("M0,0h20v20h-20Z", 2, 4, 6)
    centers = [t["center"]["arc"] for t in result["contours"][0]["tabs"]]
    assert centers[0] == 12.0
    assert result["contours"][0]["tabs"][0]["center"] == {"x": 12.0, "y": 0.0,
                                                          "arc": 12.0}
    _assert_constraints(result, contours, 2, 4, 6)


def test_equal_distance_prefers_smaller_arc():
    # 4x4 正方形周长 16，2 扇区目标为弧长 4、12（恰为顶点），
    # width=2、margin=0：两侧可行中心 3 与 5 等距，取 3（较小弧长）
    contours, result = _tabs("M0,0h4v4h-4Z", 2, 2, 0.0)
    centers = [t["center"]["arc"] for t in result["contours"][0]["tabs"]]
    assert centers == [3.0, 11.0]
    _assert_constraints(result, contours, 2, 2, 0.0)


def test_target_infeasible_clamps_to_nearest_edge_feasible_point():
    # margin=9、width=2：顶点两侧 10 内不可放中心，20 长的边各只剩
    # 边中点处的小区间（边 0 [10,10]、边 1 [30,30]、边 2 [50,50]、
    # 边 3 [70,70]）；唯一扇区目标弧长 40（顶点），距其最近的是 30
    contours, result = _tabs("M0,0h20v20h-20Z", 1, 2, 9.0)
    tab = result["contours"][0]["tabs"][0]
    assert tab["center"] == {"x": 20.0, "y": 10.0, "arc": 30.0}
    _assert_constraints(result, contours, 1, 2, 9.0)


def test_unplaced_when_no_edge_can_fit_bridge():
    # 1x10 矩形周长 22；短边（长 1）放不下 width=2 的桥，
    # count=11 扇区宽 2：多个短边扇区必然无法放置
    contours, result = _tabs("M0,0h1v10h-1Z", 11, 2, 0.0)
    ce = result["contours"][0]
    assert ce["unplaced_count"] >= 1
    assert result["unplaced_count"] == ce["unplaced_count"]
    assert ce["tab_count"] + ce["unplaced_count"] == 11
    _assert_constraints(result, contours, 11, 2, 0.0)


def test_width_exceeding_perimeter_all_unplaced():
    contours, result = _tabs("M0,0h4v4h-4Z", 2, 100, 0.0)
    ce = result["contours"][0]
    assert ce["tabs"] == []
    assert ce["tab_count"] == 0
    assert ce["unplaced_count"] == 2
    assert result["unplaced_count"] == 2


def test_margin_consuming_entire_edge_all_unplaced():
    # 边长 4，margin=2、width 任意正数：边内余量区相接，无可行中心
    contours, result = _tabs("M0,0h4v4h-4Z", 1, 0.1, 2.0)
    assert result["contours"][0]["unplaced_count"] == 1


# ---------------------------------------------------------------------------
# 拥挤布置：扇区顺序 + 互不重叠
# ---------------------------------------------------------------------------

def test_crowded_tabs_never_overlap_and_count_unplaced():
    # 周长 160，width=4：最多容纳 40 个相切的桥
    contours, result = _tabs("M0,0h40v40h-40Z", 40, 4, 0.0)
    assert result["tab_count"] == 40 and result["unplaced_count"] == 0
    arcs = sorted(t["center"]["arc"]
                  for t in result["contours"][0]["tabs"])
    gaps = [b - a for a, b in zip(arcs, arcs[1:])]
    gaps.append(160 - arcs[-1] + arcs[0])
    assert all(g >= 4 - 1e-9 for g in gaps)
    _assert_constraints(result, contours, 40, 4, 0.0)

    # 41 个总宽 164 > 160：至少一个无法放置
    contours2, result2 = _tabs("M0,0h40v40h-40Z", 41, 4, 0.0)
    assert result2["contours"][0]["tab_count"] \
        + result2["contours"][0]["unplaced_count"] == 41
    assert result2["unplaced_count"] >= 1
    _assert_constraints(result2, contours2, 41, 4, 0.0)


def test_tabs_stay_within_their_own_sector():
    # 每个已放置桥的中心必须在其对应扇区内（不跨扇区借用位置）
    contours, result = _tabs("M0,0h40v40h-40Z", 13, 3, 2.5)
    ce = result["contours"][0]
    for tab in ce["tabs"]:
        k = tab["sector"]
        c = tab["center"]["arc"]
        assert k * 160 / 13 - 1e-9 <= c <= (k + 1) * 160 / 13 + 1e-9
    _assert_constraints(result, contours, 13, 3, 2.5)


def test_later_sector_blocked_by_earlier_bridge():
    # 两扇区共享一条边且目标被同一侧顶点阻挡时，先放置的桥占用中心区间，
    # 后一桥只能选不重叠的位置（中心弧长差 >= width）
    contours, result = _tabs("M0,0h40v40h-40Z", 20, 5, 4)
    arcs = sorted(t["center"]["arc"]
                  for t in result["contours"][0]["tabs"])
    for a, b in zip(arcs, arcs[1:]):
        assert b - a >= 5 - 1e-9
    _assert_constraints(result, contours, 20, 5, 4)


# ---------------------------------------------------------------------------
# 跳过规则：仅闭合非退化 outer
# ---------------------------------------------------------------------------

def test_skips_hole_open_and_degenerate():
    d = ("M0,0h40v40h-40Z M5,5h30v30h-30Z "   # outer + hole
         "M50,0 L60,0 L60,10 "                  # open
         "M20,20 L25,20 L20,20 Z")              # degenerate
    contours, result = _tabs(d, 1, 2, 0)
    assert result["contour_count"] == 1
    assert result["contours"][0]["original_index"] == 0
    assert {(s["original_index"], s["role"], s["reason"])
            for s in result["skipped_contours"]} == {
                (1, "hole", "hole"),
                (2, "open", "open"),
                (3, "degenerate", "degenerate")}


def test_island_outer_at_even_depth_is_processed():
    d = "M0,0h100v100h-100Z M10,10h80v80h-80Z M40,40h20v20h-20Z"
    contours, result = _tabs(d, 2, 1, 1)
    indices = [c["original_index"] for c in result["contours"]]
    assert indices == [0, 2]          # 外环与孔中岛
    assert result["contours"][1]["depth"] == 2
    assert {(s["original_index"], s["reason"])
            for s in result["skipped_contours"]} == {(1, "hole")}
    _assert_constraints(result, contours, 2, 1, 1)


def test_no_valid_outer_empty_result():
    contours, result = _tabs(
        "M5,5 L8,5 L5,5 Z M20,0 L30,0 L30,10 L20,10", 1, 1, 0)
    assert result["contour_count"] == 0
    assert result["contours"] == []
    assert result["tab_count"] == 0 and result["unplaced_count"] == 0
    assert {s["reason"] for s in result["skipped_contours"]} == {
        "degenerate", "open"}


def test_empty_path():
    contours, result = _tabs("", 1, 1, 0)
    assert result["contours"] == []
    assert result["skipped_contours"] == []
    assert result["tab_count"] == 0


# ---------------------------------------------------------------------------
# 曲线细分 / 确定性 / 填充规则
# ---------------------------------------------------------------------------

def test_curve_contour_uses_flattened_polyline():
    d = "M-1,0 A1,1 0 1,0 1,0 A1,1 0 1,0 -1,0 Z"
    contours, result = _tabs(d, 4, 0.02, 0.0)
    ce = result["contours"][0]
    assert ce["tab_count"] == 4
    # 细分多边形周长在 (3, 2π) 之间，桥中心约在四个四分点
    assert 3.0 < ce["perimeter"] < 2 * math.pi
    centers = [(t["center"]["x"], t["center"]["y"]) for t in ce["tabs"]]
    for (x, y) in centers:
        assert 0.65 < math.hypot(x, y) < 1.05
    _assert_constraints(result, contours, 4, 0.02, 0.0)


def test_deterministic():
    d = ("M0,0h100v100h-100Z M10,10h80v80h-80Z M40,40h20v20h-20Z "
         "M60,60h5v5h-5Z")
    contours, _ = analyze_contours(d, "nonzero")
    r1 = compute_tabs(contours, 7, 2.5, 1.0)
    r2 = compute_tabs(contours, 7, 2.5, 1.0)
    assert r1 == r2


def test_same_for_both_fill_rules():
    d = "M0,0h20v20h-20Z M5,5h10v10h-10Z"
    _, r_nonzero = _tabs(d, 3, 1, 0.5, "nonzero")
    _, r_evenodd = _tabs(d, 3, 1, 0.5, "evenodd")
    assert r_nonzero == r_evenodd


# ---------------------------------------------------------------------------
# 参数校验
# ---------------------------------------------------------------------------

def test_invalid_params_raise_value_error():
    contours, _ = analyze_contours("M0,0h4v4h-4Z", "nonzero")
    with pytest.raises(ValueError):
        compute_tabs(contours, 0, 1, 0)
    with pytest.raises(ValueError):
        compute_tabs(contours, -2, 1, 0)
    with pytest.raises(ValueError):
        compute_tabs(contours, 2, 0, 0)
    with pytest.raises(ValueError):
        compute_tabs(contours, 2, -1, 0)
    with pytest.raises(ValueError):
        compute_tabs(contours, 2, 1, -0.5)
    with pytest.raises(ValueError):
        compute_tabs(contours, 2, float("nan"), 0)
    with pytest.raises(ValueError):
        compute_tabs(contours, 2, 1, float("inf"))
    with pytest.raises(ValueError):
        compute_tabs(contours, MAX_TABS + 1, 1, 0)
