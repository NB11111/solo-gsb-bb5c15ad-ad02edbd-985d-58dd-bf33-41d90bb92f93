"""闭合轮廓加工计划（cut-plan）的几何单元测试。"""

import math
import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.cutplan import compute_cut_plan  # noqa: E402
from app.svggeo import analyze_contours  # noqa: E402


def _plan(d, x, y, fill_rule="nonzero"):
    contours, _ = analyze_contours(d, fill_rule)
    return contours, compute_cut_plan(contours, x, y)


def _orders(plan):
    return [s["original_index"] for s in plan["steps"]]


# ---------------------------------------------------------------------------
# 后代先加工
# ---------------------------------------------------------------------------

def test_hole_machined_before_its_parent_outer():
    # 外方 0..20 含内孔 5..15：孔（后代）必须先于外环
    _, plan = _plan("M0,0h20v20h-20Z M5,5h10v10h-10Z", 0.0, 0.0)
    assert plan["total_contours"] == 2
    assert _orders(plan) == [1, 0]
    assert [s["order"] for s in plan["steps"]] == [1, 2]
    assert [s["role"] for s in plan["steps"]] == ["hole", "outer"]
    assert [s["depth"] for s in plan["steps"]] == [1, 0]


def test_three_level_nesting_machined_inside_out():
    # 外环 -> 孔 -> 孔中岛：岛（最深）先，然后孔，最后外环
    d = "M0,0h100v100h-100Z M10,10h80v80h-80Z M40,40h20v20h-20Z"
    _, plan = _plan(d, 0.0, 0.0)
    assert _orders(plan) == [2, 1, 0]


def test_sibling_holes_all_before_parent():
    # 外环含两个同级孔：两个孔都加工完才轮到外环
    d = ("M0,0h100v100h-100Z "
         "M10,10h10v10h-10Z "
         "M80,80h10v10h-10Z")
    contours, plan = _plan(d, 0.0, 0.0)
    orders = _orders(plan)
    assert orders[-1] == 0
    assert set(orders[:2]) == {1, 2}
    # 每步加工时其所有后代/祖先约束满足：外环永远在最后
    assert plan["total_contours"] == 3


# ---------------------------------------------------------------------------
# 入刀点与空走距离
# ---------------------------------------------------------------------------

def test_nearest_flattened_vertex_chosen_as_entry():
    # 起点 (2,2)：外方 0..20 的最近顶点是 (0,0)；但内孔 5..15 先加工，
    # 孔的最近顶点是 (5,5)
    _, plan = _plan("M0,0h20v20h-20Z M5,5h10v10h-10Z", 2.0, 2.0)
    first = plan["steps"][0]
    assert first["original_index"] == 1
    assert first["entry_point"] == {"x": 5.0, "y": 5.0}
    assert math.isclose(first["air_distance"], math.hypot(3, 3))
    # 入刀顶点即细分顶点列表中的对应点
    assert first["entry_point"]["x"] == 5.0


def test_head_stays_at_entry_point_and_feeds_next_step():
    # 三层嵌套：起点 (0,0)，岛入刀点 (40,40)；第二步刀头应从 (40,40) 起算
    d = "M0,0h100v100h-100Z M10,10h80v80h-80Z M40,40h20v20h-20Z"
    _, plan = _plan(d, 0.0, 0.0)
    s1, s2, s3 = plan["steps"]
    assert s1["entry_point"] == {"x": 40.0, "y": 40.0}
    assert math.isclose(s1["air_distance"], math.hypot(40, 40))
    # 加工后刀头停在入刀点
    assert s1["head_after"] == s1["entry_point"]
    # 第二步：刀头在 (40,40)，孔最近顶点 (10,10)
    assert s2["entry_point"] == {"x": 10.0, "y": 10.0}
    assert math.isclose(s2["air_distance"], math.hypot(30, 30))
    assert s2["head_after"] == s2["entry_point"]
    # 第三步：刀头在 (10,10)，外环最近顶点 (0,0)
    assert s3["entry_point"] == {"x": 0.0, "y": 0.0}
    assert math.isclose(s3["air_distance"], math.hypot(10, 10))


def test_total_air_distance_is_sum_of_steps():
    d = "M0,0h100v100h-100Z M10,10h80v80h-80Z M40,40h20v20h-20Z"
    _, plan = _plan(d, 0.0, 0.0)
    assert math.isclose(
        plan["total_air_distance"],
        sum(s["air_distance"] for s in plan["steps"]))


def test_entry_vertex_index_points_into_flattened_points():
    # 单环，起点在中心 (5,5)，四顶点等距，顶点序号 0 决胜
    contours, plan = _plan("M0,0h10v10h-10Z", 5.0, 5.0)
    step = plan["steps"][0]
    pts = contours[step["original_index"]].points
    assert pts[step["entry_vertex"]] == (0.0, 0.0)
    assert step["entry_vertex"] == 0


def test_curve_contour_entry_is_a_flattened_vertex():
    # 圆（两条半圆弧细分）：入刀点必须是细分顶点之一，起点 (-2,0)
    d = "M-1,0 A1,1 0 1,0 1,0 A1,1 0 1,0 -1,0 Z"
    contours, plan = _plan(d, -2.0, 0.0)
    assert plan["total_contours"] == 1
    step = plan["steps"][0]
    pts = contours[0].points
    entry = (step["entry_point"]["x"], step["entry_point"]["y"])
    assert entry in [(round(a, 9) + 0.0, round(b, 9) + 0.0) for a, b in pts]
    # (-1,0) 距 (-2,0) 最近
    assert math.isclose(step["air_distance"], 1.0, abs_tol=1e-9)


# ---------------------------------------------------------------------------
# 决胜规则
# ---------------------------------------------------------------------------

def test_equal_distance_tie_breaks_by_original_index():
    # 两个独立外环：方 0..10 与方 20..30；起点 (15,0)
    # 到轮廓 0 最近点 (10,0) 与轮廓 1 最近点 (20,0) 距离均为 5 -> 轮廓 0 先
    contours, plan = _plan("M0,0h10v10h-10Z M20,0h10v10h-10Z", 15.0, 0.0)
    s1, s2 = plan["steps"]
    assert s1["original_index"] == 0
    assert s1["entry_point"] == {"x": 10.0, "y": 0.0}
    assert math.isclose(s1["air_distance"], 5.0)
    # 第二步刀头在 (10,0)，最近点 (20,0) 距离 10
    assert s2["original_index"] == 1
    assert s2["entry_point"] == {"x": 20.0, "y": 0.0}
    assert math.isclose(s2["air_distance"], 10.0)
    assert math.isclose(plan["total_air_distance"], 15.0)


def test_equal_distance_within_contour_tie_breaks_by_vertex_index():
    # 起点恰在正方形中心：四个顶点等距，取顶点序号最小者
    _, plan = _plan("M0,0h10v10h-10Z", 5.0, 5.0)
    assert plan["steps"][0]["entry_vertex"] == 0
    assert plan["steps"][0]["entry_point"] == {"x": 0.0, "y": 0.0}


def test_greedy_choice_recomputed_each_round():
    # 两个独立孔：近的先；加工完近孔后，刀头停在近孔入刀点，
    # 下一步在剩余轮廓中重选最近者
    # 方 0..10 与方 100..110（互不包含），起点 (0,0)
    _, plan = _plan("M0,0h10v10h-10Z M100,0h10v10h-10Z", 0.0, 0.0)
    orders = _orders(plan)
    assert orders == [0, 1]
    assert math.isclose(plan["steps"][0]["air_distance"], 0.0)  # 起点即顶点
    assert math.isclose(plan["steps"][1]["air_distance"], 100.0)


# ---------------------------------------------------------------------------
# 跳过与空计划
# ---------------------------------------------------------------------------

def test_open_and_degenerate_listed_as_skipped():
    d = "M0,0h10v10h-10Z M5,5 L8,5 L5,5 Z M20,0 L30,0 L30,10"
    contours, plan = _plan(d, 0.0, 0.0)
    assert plan["total_contours"] == 1
    assert _orders(plan) == [0]
    reasons = {(s["original_index"], s["reason"])
               for s in plan["skipped_contours"]}
    assert reasons == {(1, "degenerate"), (2, "open")}


def test_no_valid_contours_returns_empty_plan():
    # 仅开放轮廓
    contours, plan = _plan("M0,0 L10,0 L10,10", 3.0, 4.0)
    assert plan["total_contours"] == 0
    assert plan["steps"] == []
    assert plan["total_air_distance"] == 0.0
    assert plan["start"] == {"x": 3.0, "y": 4.0}
    assert plan["skipped_contours"] == [
        {"original_index": 0, "role": "open", "reason": "open"}]


def test_empty_path_returns_empty_plan():
    contours, plan = _plan("", 1.0, 2.0)
    assert plan == {
        "start": {"x": 1.0, "y": 2.0},
        "total_contours": 0,
        "steps": [],
        "total_air_distance": 0.0,
        "skipped_contours": [],
    }


# ---------------------------------------------------------------------------
# 确定性 / 填充规则一致
# ---------------------------------------------------------------------------

def test_plan_deterministic():
    d = ("M0,0h100v100h-100Z M10,10h20v20h-20Z M80,80h20v20h-20Z "
         "M40,40h5v5h-5Z")
    contours, _ = analyze_contours(d, "nonzero")
    p1 = compute_cut_plan(contours, 7.0, -3.0)
    p2 = compute_cut_plan(contours, 7.0, -3.0)
    assert p1 == p2


def test_plan_same_for_both_fill_rules():
    d = "M0,0h20v20h-20Z M5,5h10v10h-10Z"
    _, p_nonzero = _plan(d, 2.0, 2.0, "nonzero")
    _, p_evenodd = _plan(d, 2.0, 2.0, "evenodd")
    assert p_nonzero == p_evenodd
