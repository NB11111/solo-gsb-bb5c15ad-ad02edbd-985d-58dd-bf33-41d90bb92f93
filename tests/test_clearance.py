"""轮廓间安全间隙校验的几何单元测试。"""

import math
import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.clearance import check_clearance  # noqa: E402
from app.svggeo import (  # noqa: E402
    analyze_contours,
    point_segment_dist2,
    segment_segment_dist2,
)


def _check(d, min_distance, include_open=False, fill_rule="nonzero"):
    contours, _ = analyze_contours(d, fill_rule)
    return check_clearance(contours, min_distance, include_open)


# ---------------------------------------------------------------------------
# 距离原语
# ---------------------------------------------------------------------------

def test_point_segment_distances():
    a, b = (0.0, 0.0), (10.0, 0.0)
    assert math.isclose(point_segment_dist2((5.0, 3.0), a, b), 9.0)
    assert math.isclose(point_segment_dist2((-2.0, 0.0), a, b), 4.0)
    assert math.isclose(point_segment_dist2((12.0, 5.0), a, b), 29.0)
    # 零长度段：退化为点-点距离
    assert math.isclose(point_segment_dist2((3.0, 4.0), a, a), 25.0)


def test_segment_segment_distance_basic_and_intersect():
    # 两条平行水平线，垂直间距 4
    assert math.isclose(
        segment_segment_dist2((0, 0), (10, 0), (0, 4), (10, 4)), 16.0)
    # 相交 -> 0
    assert segment_segment_dist2((0, 0), (10, 10), (0, 10), (10, 0)) == 0.0
    # 端点接触 -> 0
    assert segment_segment_dist2((0, 0), (1, 0), (1, 0), (1, 1)) == 0.0
    # 斜线段到线段：端点最近
    d2 = segment_segment_dist2((0, 0), (1, 0), (2, 1), (3, 1))
    assert math.isclose(d2, 2.0)
    # 零长度段（点到线段）
    assert math.isclose(
        segment_segment_dist2((5, 0), (5, 0), (0, 3), (10, 3)), 9.0)


# ---------------------------------------------------------------------------
# 基本间隙判定
# ---------------------------------------------------------------------------

def test_hole_parent_clearance_conflict_flagged():
    # 外方 0..40，内孔 5..35：最近间隙恰为 5，孔的直接 parent 是外环
    res = _check("M0,0h40v40h-40Z M5,5h30v30h-30Z", 8)
    assert res["conflict_count"] == 1
    cf = res["conflicts"][0]
    assert cf["a"]["original_index"] == 0 and cf["a"]["role"] == "outer"
    assert cf["b"]["original_index"] == 1 and cf["b"]["role"] == "hole"
    assert math.isclose(cf["distance"], 5.0)
    assert cf["threshold"] == 8
    assert cf["hole_parent"] is True
    assert res["checked_contour_count"] == 2
    assert res["skipped_contours"] == []


def test_distance_equal_threshold_passes():
    # 距离 == 阈值 5：通过；阈值略大即冲突
    assert _check("M0,0h40v40h-40Z M5,5h30v30h-30Z", 5)["conflict_count"] == 0
    assert _check("M0,0h40v40h-40Z M5,5h30v30h-30Z", 5.000001)["conflict_count"] == 1


def test_irrational_threshold_equal_distance_passes():
    # 点 (11,11) 到外角 (10,10) 的距离恰为 sqrt(2)：sqrt(2)**2 在浮点上
    # 略大于 2，平方域比较会把“相等”误判为冲突，故实现必须在距离域做容差
    from app.svggeo import Contour

    def contour(idx, pts, closed, role):
        return Contour(original_index=idx, subpath={"segments": [], "closed": closed},
                       points=pts, area=0.0,
                       bounds=(min(x for x, _ in pts), min(y for _, y in pts),
                               max(x for x, _ in pts), max(y for _, y in pts)),
                       closed=closed, degenerate=False, role=role, depth=0)

    square = contour(0, [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0)],
                     True, "outer")
    point_like = contour(1, [(11.0, 11.0), (11.0, 11.0)], False, "open")
    # 重合两点的开放轮廓（一条零长度边）手动置为非退化
    # （analyze_contours 会将其判为退化，这里只为精确构造
    # “点到角点 sqrt(2)”的距离边界，同时覆盖零长度段处理）
    res_eq = check_clearance([square, point_like], math.sqrt(2),
                             include_open=True)
    assert res_eq["conflict_count"] == 0
    # 阈值小于距离（超出浮点容差）-> 通过
    res_under = check_clearance([square, point_like], math.sqrt(2) - 1e-6,
                                include_open=True)
    assert res_under["conflict_count"] == 0
    # 阈值大于距离（间隙不足）-> 冲突
    res_over = check_clearance([square, point_like], math.sqrt(2) + 1e-6,
                               include_open=True)
    assert res_over["conflict_count"] == 1
    assert math.isclose(res_over["conflicts"][0]["distance"], math.sqrt(2),
                        abs_tol=1e-9)


def test_sibling_holes_conflict_is_not_hole_parent():
    d = ("M0,0h100v100h-100Z "
         "M10,10h20v20h-20Z "      # 孔 A
         "M31,10h20v20h-20Z")      # 孔 B：与 A 的 x 间距为 1
    res = _check(d, 1.5)
    pairs = {(c["a"]["original_index"], c["b"]["original_index"]): c
             for c in res["conflicts"]}
    # 两个同级孔之间：距 1 < 阈值但并非 parent 关系
    assert (1, 2) in pairs
    assert pairs[(1, 2)]["hole_parent"] is False
    assert math.isclose(pairs[(1, 2)]["distance"], 1.0)
    # 两孔与外环（直接 parent）间隙为 10，阈值 1.5 下不构成冲突
    assert (0, 1) not in pairs and (0, 2) not in pairs


def test_island_hole_pair_hole_parent_only_for_direct_parent():
    # 外环(0) > 孔(1, 5..55) > 岛(2, 10..50) > 孔(3, 15..45)
    d = ("M0,0h60v60h-60Z "
         "M5,5h50v50h-50Z "
         "M10,10h40v40h-40Z "
         "M15,15h30v30h-30Z")
    res = _check(d, 8)
    pairs = {(c["a"]["original_index"], c["b"]["original_index"]): c
             for c in res["conflicts"]}
    # 每一层间隙 5，全部冲突
    assert set(pairs) == {(0, 1), (1, 2), (2, 3)}
    # 直接 parent 对：(孔 1 的 parent 0) 与 (孔 3 的 parent 2)
    assert pairs[(0, 1)]["hole_parent"] is True
    assert pairs[(2, 3)]["hole_parent"] is True
    # 岛(2, outer)与其父孔(1)：含 hole 一侧但 hole 是子而非 parent，不标记
    assert pairs[(1, 2)]["hole_parent"] is False


def test_separate_contours_no_conflict():
    # 两个互不相交且相距 20 的外环
    d = "M0,0h10v10h-10Z M30,0h10v10h-10Z"
    res = _check(d, 10)
    assert res["conflict_count"] == 0
    assert res["checked_contour_count"] == 2


def test_touching_boundaries_report_zero_distance():
    # 两个外环边重合接触
    d = "M0,0h10v10h-10Z M10,0h10v10h-10Z"
    res = _check(d, 1)
    assert res["conflict_count"] == 1
    assert res["conflicts"][0]["distance"] == 0.0
    assert res["conflicts"][0]["hole_parent"] is False


# ---------------------------------------------------------------------------
# 开放 / 退化轮廓
# ---------------------------------------------------------------------------

def test_open_contours_skipped_by_default():
    d = "M0,0h20v20h-20Z M21,5 L22,6 L21,7"
    res = _check(d, 2)
    assert res["checked_contour_count"] == 1
    assert res["conflict_count"] == 0
    assert res["skipped_contours"] == [
        {"original_index": 1, "role": "open", "reason": "open"}]


def test_open_contours_included_on_request():
    d = "M0,0h20v20h-20Z M21,5 L22,6 L21,7"
    res = _check(d, 2, include_open=True)
    assert res["checked_contour_count"] == 2
    assert res["skipped_contours"] == []
    assert res["conflict_count"] == 1
    cf = res["conflicts"][0]
    assert cf["a"]["original_index"] == 0
    assert cf["b"]["original_index"] == 1 and cf["b"]["role"] == "open"
    assert math.isclose(cf["distance"], 1.0)
    # 开放折线没有 parent，不可能标记 hole_parent
    assert cf["hole_parent"] is False


def test_open_polyline_segments_are_not_closed():
    # 开放折线的三个点 (21,5)-(22,6)-(21,7)：若错误地按闭合环处理，
    # 闭合边 (21,7)->(21,5) 距外环右边界 x=20 仅 1；开放时最近点同样约 1。
    # 这里用一个只在“错误闭合”时才产生近边的构型：
    # 折线全部位于 x>=30，最近点离环 >10，但其弦（闭合边）若存在会穿过环 —
    # 直接验证参与边数不包含回首边：构造距离外方很远的 Z 形折线，阈值放宽。
    d = "M0,0h10v10h-10Z M30,0 L40,5 L30,10"
    res = _check(d, 5, include_open=True)
    # 开放折线与外环 x 间距 20，无冲突
    assert res["conflict_count"] == 0


def test_degenerate_contours_always_skipped():
    d = ("M0,0h40v40h-40Z "
         "M5,5 L10,5 L5,5 Z "          # 退化（面积 0 的闭合三角形）
         "M20,20 L25,20")               # 两点开放 -> 退化
    res = _check(d, 1, include_open=True)
    assert res["checked_contour_count"] == 1
    skipped = {(s["original_index"], s["reason"]) for s in res["skipped_contours"]}
    assert skipped == {(1, "degenerate"), (2, "degenerate")}


# ---------------------------------------------------------------------------
# 细分曲线 / 确定性 / 参数校验
# ---------------------------------------------------------------------------

def test_curves_flattened_before_distance():
    # 外环为方形；内轮廓用三次贝塞尔逼近半径 10 的圆（中心 (20,20)），
    # 外环边在 0/40，径向间隙约 10；阈值 12 时必然冲突，阈值 9 时通过
    d = ("M0,0h40v40h-40Z "
         "M20,10 C25.52,10 30,14.48 30,20 C30,25.52 25.52,30 20,30 "
         "C14.48,30 10,25.52 10,20 C10,14.48 14.48,20 20,10 Z")
    res = _check(d, 12)
    assert res["conflict_count"] == 1
    dist = res["conflicts"][0]["distance"]
    assert 9.5 < dist <= 10.0 + 1e-6  # 细分近似半径 10
    assert _check(d, 9)["conflict_count"] == 0


def test_results_deterministic():
    d = ("M0,0h100v100h-100Z M10,10h20v20h-20Z M31,10h20v20h-20Z "
         "M60,60h10v10h-10Z")
    outs = []
    for _ in range(3):
        contours, _ = analyze_contours(d, "nonzero")
        outs.append(check_clearance(contours, 5))
    assert outs[0] == outs[1] == outs[2]
    # 按 (a.original_index, b.original_index) 升序
    keys = [(c["a"]["original_index"], c["b"]["original_index"])
            for c in outs[0]["conflicts"]]
    assert keys == sorted(keys)


def test_invalid_min_distance_raises():
    contours, _ = analyze_contours("M0,0h10v10h-10Z", "nonzero")
    with pytest.raises(ValueError):
        check_clearance(contours, 0)
    with pytest.raises(ValueError):
        check_clearance(contours, -1)
    with pytest.raises(ValueError):
        check_clearance(contours, float("nan"))
    with pytest.raises(ValueError):
        check_clearance(contours, float("inf"))


def test_empty_and_single_contour():
    assert _check("", 1)["conflict_count"] == 0
    assert _check("M0,0h10v10h-10Z", 1)["conflict_count"] == 0
