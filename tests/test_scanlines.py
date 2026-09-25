"""scanlines：水平扫描线填充区间单元测试。"""

import math
import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.scanlines import MAX_SCANLINES, compute_scanlines  # noqa: E402
from app.svggeo import analyze_contours  # noqa: E402


def _scan(d, spacing, fill_rule="nonzero"):
    contours, _warnings = analyze_contours(d, fill_rule)
    return compute_scanlines(contours, spacing), contours


def test_single_outer_square():
    r, _ = _scan("M0,0h10v10h-10Z", 2.0)
    assert r["bounds"] == {"miny": 0.0, "maxy": 10.0}
    assert r["scanline_count"] == 5
    assert [line["y"] for line in r["lines"]] == [0.0, 2.0, 4.0, 6.0, 8.0]
    for line in r["lines"]:
        assert line["intervals"] == [[0.0, 10.0]]
    assert r["skipped_contours"] == []


def test_max_y_exclusive_exact_integer_height():
    # 高度恰好等于 spacing 整数倍：最大 y 不取线
    r, _ = _scan("M0,0h4v4h-4Z", 1.0)
    assert r["scanline_count"] == 4
    assert [line["y"] for line in r["lines"]] == [0.0, 1.0, 2.0, 3.0]


def test_spacing_larger_than_height_still_emits_min_y_line():
    r, _ = _scan("M0,0h4v4h-4Z", 5.0)
    assert r["scanline_count"] == 1
    assert r["lines"][0]["y"] == 0.0
    assert r["lines"][0]["intervals"] == [[0.0, 4.0]]


def test_outer_with_hole_alternates():
    # 外方 0..10，内孔 2..8
    r, _ = _scan("M0,0h10v10h-10Z M2,2h6v6h-6Z", 2.0)
    by_y = {line["y"]: line["intervals"] for line in r["lines"]}
    assert by_y[0.0] == [[0.0, 10.0]]
    assert by_y[2.0] == [[0.0, 2.0], [8.0, 10.0]]
    assert by_y[4.0] == [[0.0, 2.0], [8.0, 10.0]]
    assert by_y[8.0] == [[0.0, 10.0]]
    # 区间升序、互不重叠
    for line in r["lines"]:
        ivs = line["intervals"]
        assert ivs == sorted(ivs)
        assert all(b[0] >= a[1] for a, b in zip(ivs, ivs[1:]))


def test_hole_with_island_three_levels():
    # 外方 0..10、孔 2..8、岛 6..8
    r, _ = _scan("M0,0h10v10h-10Z M2,2h6v6h-6Z M6,6h2v2h-2Z", 2.0)
    by_y = {line["y"]: line["intervals"] for line in r["lines"]}
    # y=6/8 切到岛（y=6 为岛底边，半开规则不计；y=8 是岛的最大 y）
    assert by_y[6.0] == [[0.0, 2.0], [6.0, 10.0]]
    assert by_y[8.0] == [[0.0, 10.0]]
    assert by_y[2.0] == [[0.0, 2.0], [8.0, 10.0]]


def test_vertex_scanline_no_double_count():
    # 扫描线精确落在横/纵边连接的顶点上：半开边规则不重复计数
    d = "M0,0 L10,0 L10,5 L5,5 L5,10 L0,10 Z"  # L 形
    r, _ = _scan(d, 5.0)
    by_y = {line["y"]: line["intervals"] for line in r["lines"]}
    assert by_y[0.0] == [[0.0, 10.0]]   # 底边
    assert by_y[5.0] == [[0.0, 5.0]]    # 内凹顶点（10,5)-(5,5) 水平边不计数）


def test_half_open_rule_bottom_tangent_is_empty():
    # 扫描线恰为整体最小 y（圆底切点）：交点成对规则下无填充区间
    r, _ = _scan("M-1,0 A1,1 0 1,0 1,0 A1,1 0 1,0 -1,0 Z", 0.5)
    first = r["lines"][0]
    assert first["y"] == -1.0
    assert first["intervals"] == []
    # 其余线为单一对称区间，宽度关于 y=0 对称且向中部递增
    mids = [line for line in r["lines"] if line["intervals"]]
    widths = [line["intervals"][0][1] - line["intervals"][0][0]
              for line in mids]
    assert widths == pytest.approx(widths[::-1], abs=1e-9)
    half = widths[:len(widths) // 2 + 1]
    assert half == sorted(half)
    assert max(widths) <= 2.0 + 1e-9


def test_two_disjoint_outers_produce_sorted_intervals():
    r, _ = _scan("M0,0h2v10h-2Z M8,0h2v10h-2Z", 5.0)
    assert r["bounds"] == {"miny": 0.0, "maxy": 10.0}
    for line in r["lines"]:
        assert line["intervals"] == [[0.0, 2.0], [8.0, 10.0]]


def test_skips_open_and_degenerate_contours():
    d = "M0,0h10v10h-10Z M5,5 L8,5 L5,5 Z M20,0 L30,0 L30,10"
    r, _ = _scan(d, 5.0)
    assert r["scanline_count"] == 2
    for line in r["lines"]:
        assert line["intervals"] == [[0.0, 10.0]]
    reasons = {(s["original_index"], s["reason"])
               for s in r["skipped_contours"]}
    assert reasons == {(1, "degenerate"), (2, "open")}


def test_no_valid_contours_returns_empty_and_none_bounds():
    r, _ = _scan("M0,0 L10,0 L10,10", 1.0)
    assert r["bounds"] is None
    assert r["scanline_count"] == 0
    assert r["lines"] == []
    assert r["skipped_contours"][0]["reason"] == "open"

    r2, _ = _scan("", 1.0)
    assert r2["bounds"] is None and r2["lines"] == []


def test_curves_flattened_are_used():
    # 与 normalize/metrics 相同的弧细分：y=0 过圆心，半宽约为半径 1
    r, _ = _scan("M-1,0 A1,1 0 1,0 1,0 A1,1 0 1,0 -1,0 Z", 0.25)
    mid = next(line for line in r["lines"] if line["y"] == 0.0)
    x0, x1 = mid["intervals"][0]
    assert abs(x0 + 1.0) < 1e-6 and abs(x1 - 1.0) < 1e-6


def test_evenodd_same_hierarchy_result():
    d = "M0,0h10v10h-10Z M2,2h6v6h-6Z M6,6h2v2h-2Z"
    r_nonzero, _ = _scan(d, 2.0, "nonzero")
    r_evenodd, _ = _scan(d, 2.0, "evenodd")
    assert r_nonzero["lines"] == r_evenodd["lines"]


def test_too_many_scanlines_raises():
    # 高 10 的轮廓，spacing=0.005 -> 2000 条，超过上限
    with pytest.raises(ValueError, match=str(MAX_SCANLINES)):
        _scan("M0,0h1v10h-1Z", 0.005)


def test_exactly_at_cap_allowed():
    # 4 / 0.004 = 1000 条：恰好上限允许
    r, _ = _scan("M0,0h4v4h-4Z", 0.004)
    assert r["scanline_count"] == MAX_SCANLINES


@pytest.mark.parametrize("bad", [0, -1.0, float("nan"), float("inf"), -float("inf")])
def test_invalid_spacing_raises(bad):
    with pytest.raises(ValueError):
        _scan("M0,0h4v4h-4Z", bad)
