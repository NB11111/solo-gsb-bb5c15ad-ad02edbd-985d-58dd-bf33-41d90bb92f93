"""metrics：填充面积与面积质心单元测试。"""

import math
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.metrics import compute_metrics, polygon_centroid  # noqa: E402
from app.svggeo import (  # noqa: E402
    analyze_contours,
    dedupe_ring,
    flatten_subpath,
    parse_path,
    signed_area,
)


def _metrics(d, fill_rule="nonzero"):
    contours, _warnings = analyze_contours(d, fill_rule)
    return compute_metrics(contours), contours


def test_single_outer_square():
    m, _ = _metrics("M0,0h10v10h-10Z")
    assert m["area"] == 100.0
    assert m["centroid"] == {"x": 5.0, "y": 5.0}
    assert m["contributing_contour_count"] == 1
    assert m["skipped_contours"] == []
    item = m["contours"][0]
    assert item["role"] == "outer" and item["depth"] == 0
    assert item["signed_contribution"] == 100.0


def test_outer_minus_hole():
    # 外方 0..10 扣内方 2..8：100 - 36 = 64
    m, _ = _metrics("M0,0h10v10h-10Z M2,2h6v6h-6Z")
    assert m["area"] == 64.0
    assert m["centroid"] == {"x": 5.0, "y": 5.0}
    roles = [(c["role"], c["depth"]) for c in m["contours"]]
    assert roles == [("outer", 0), ("hole", 1)]
    assert m["contours"][1]["signed_contribution"] == -36.0


def test_hole_with_island_readded():
    # 外 100 - 孔 64 + 岛 4 = 40
    d = ("M0,0h10v10h-10Z M1,1h8v8h-8Z M6,6h2v2h-2Z")
    m, contours = _metrics(d)
    assert m["area"] == 40.0
    roles = [(c.original_index, c.role, c.depth) for c in contours]
    assert roles == [(0, "outer", 0), (1, "hole", 1), (2, "outer", 2)]
    # 质心偏向小岛（7,7）：(100*5 - 64*5 + 4*7)/40 = 5.2
    assert math.isclose(m["centroid"]["x"], 5.2, abs_tol=1e-9)
    assert math.isclose(m["centroid"]["y"], 5.2, abs_tol=1e-9)


def test_four_level_alternation():
    # 外 100 - 孔 64 + 岛 16 - 岛中孔 4 = 48
    d = ("M0,0h10v10h-10Z M1,1h8v8h-8Z "
         "M3,3h4v4h-4Z M4,4h2v2h-2Z")
    m, contours = _metrics(d)
    assert m["area"] == 48.0
    assert [c.depth for c in contours] == [0, 1, 2, 3]
    assert [c.role for c in contours] == ["outer", "hole", "outer", "hole"]
    assert m["centroid"] == {"x": 5.0, "y": 5.0}


def test_orientation_independent():
    # 外环按逆时针书写（nonzero 下会被视为方向冲突），面积分析仍为正
    d_ccw = "M0,0 L0,10 L10,10 L10,0 Z M2,2h6v6h-6Z"
    m, _ = _metrics(d_ccw)
    assert m["area"] == 64.0
    assert m["centroid"] == {"x": 5.0, "y": 5.0}


def test_evenodd_alternation_same_result():
    d = "M0,0h10v10h-10Z M2,2h6v6h-6Z M3,3h4v4h-4Z"
    m_nonzero, _ = _metrics(d, "nonzero")
    m_evenodd, _ = _metrics(d, "evenodd")
    assert m_evenodd["area"] == m_nonzero["area"]
    assert m_evenodd["centroid"] == m_nonzero["centroid"]


def test_open_and_degenerate_excluded():
    # 外方 + 退化（往返零面积闭合）+ 开放折线
    d = "M0,0h10v10h-10Z M5,5 L8,5 L5,5 Z M20,0 L30,0 L30,10"
    m, _ = _metrics(d)
    assert m["area"] == 100.0
    assert m["centroid"] == {"x": 5.0, "y": 5.0}
    assert m["contributing_contour_count"] == 1
    skipped = {(s["original_index"], s["reason"])
               for s in m["skipped_contours"]}
    assert skipped == {(1, "degenerate"), (2, "open")}


def test_empty_path_zero_and_null_centroid():
    m, _ = _metrics("")
    assert m["area"] == 0.0
    assert m["centroid"] is None
    assert m["contributing_contour_count"] == 0


def test_only_open_contours_zero_and_null():
    m, _ = _metrics("M0,0 L10,0 L10,10")
    assert m["area"] == 0.0
    assert m["centroid"] is None


def test_only_degenerate_zero_and_null():
    m, _ = _metrics("M0,0 L10,0 L0,0 Z")
    assert m["area"] == 0.0
    assert m["centroid"] is None


def test_two_point_ring_degenerate():
    m, _ = _metrics("M0,0 L10,0 Z")
    assert m["area"] == 0.0
    assert m["centroid"] is None


def test_area_uses_flattened_curves():
    # 单位圆经两次半弧细分为多边形，面积近似 π 且不超过真圆
    d = "M-1,0 A1,1 0 1,0 1,0 A1,1 0 1,0 -1,0 Z"
    m, contours = _metrics(d)
    assert len(contours) == 1 and contours[0].role == "outer"
    assert m["centroid"] is not None
    assert math.isclose(m["centroid"]["x"], 0.0, abs_tol=1e-9)
    assert math.isclose(m["centroid"]["y"], 0.0, abs_tol=1e-9)
    # 细分多边形内接于圆：面积应为 π 的接近下界
    assert 3.0 < m["area"] < math.pi
    # 与直接对细分折线求面积一致（同一细分近似）
    pts = dedupe_ring(flatten_subpath(parse_path(d)[0]))
    assert math.isclose(m["area"], abs(signed_area(pts)), rel_tol=1e-9)


def test_quadratic_curve_square_area():
    # Q 描述的抛物线弓形也走细分近似
    m, _ = _metrics("M0,0 L10,0 Q10,10 0,10 Z")
    assert m["area"] > 0.0
    assert m["contributing_contour_count"] == 1


def test_centroid_negative_coordinates():
    m, _ = _metrics("M-10,-10h20v20h-20Z")
    assert m["area"] == 400.0
    assert m["centroid"] == {"x": 0.0, "y": 0.0}


def test_polygon_centroid_direction_independent():
    pts_cw = [(0, 0), (10, 0), (10, 10), (0, 10)]
    pts_ccw = list(reversed(pts_cw))
    cx1, cy1 = polygon_centroid(pts_cw)
    cx2, cy2 = polygon_centroid(pts_ccw)
    assert math.isclose(cx1, cx2, abs_tol=1e-12)
    assert math.isclose(cy1, cy2, abs_tol=1e-12)
    assert (cx1, cy1) == (5.0, 5.0)


def test_no_negative_zero_in_output():
    m, _ = _metrics("M-1,0 A1,1 0 1,0 1,0 A1,1 0 1,0 -1,0 Z")
    s = repr(m)
    assert "-0.0" not in s


def test_far_from_origin_single_ring_centroid():
    # 回归：10×10 方形放在 (1e9, 1e9)，面积仍为 100，质心必须精确为
    # (1e9+5, 1e9+5)——绝对坐标面积矩的大数相消曾把质心推离近 2e8
    m, _ = _metrics("M1000000000,1000000000h10v10h-10Z")
    assert m["area"] == 100.0
    assert m["centroid"] == {"x": 1000000005.0, "y": 1000000005.0}
    assert m["contours"][0]["centroid"] == {"x": 1000000005.0,
                                            "y": 1000000005.0}


def test_far_from_origin_eccentric_hole_matches_origin():
    # 偏心孔组合整体平移 1e9：净质心与逐环质心都和原点附近结果一致
    m0, _ = _metrics("M0,0h10v10h-10Z M6,2h2v2h-2Z")
    m1, _ = _metrics("M1000000000,1000000000h10v10h-10Z "
                     "M1000000006,1000000002h2v2h-2Z")
    assert m1["area"] == m0["area"] == 96.0
    assert math.isclose(m1["centroid"]["x"], m0["centroid"]["x"] + 1e9,
                        abs_tol=1e-6)
    assert math.isclose(m1["centroid"]["y"], m0["centroid"]["y"] + 1e9,
                        abs_tol=1e-6)
    for it0, it1 in zip(m0["contours"], m1["contours"]):
        assert math.isclose(it1["centroid"]["x"], it0["centroid"]["x"] + 1e9,
                            abs_tol=1e-6)
        assert math.isclose(it1["centroid"]["y"], it0["centroid"]["y"] + 1e9,
                            abs_tol=1e-6)


def test_far_from_origin_hole_island_alternation():
    # 外-孔-岛三层交替在 1e9 处仍与原点附近一致（净质心偏向小岛）
    m0, _ = _metrics("M0,0h10v10h-10Z M1,1h8v8h-8Z M6,6h2v2h-2Z")
    m1, _ = _metrics("M1000000000,1000000000h10v10h-10Z "
                     "M1000000001,1000000001h8v8h-8Z "
                     "M1000000006,1000000006h2v2h-2Z")
    assert m1["area"] == m0["area"] == 40.0
    assert math.isclose(m1["centroid"]["x"], m0["centroid"]["x"] + 1e9,
                        abs_tol=1e-6)
    assert math.isclose(m1["centroid"]["y"], m0["centroid"]["y"] + 1e9,
                        abs_tol=1e-6)


def test_polygon_centroid_far_from_origin():
    # 直接对多边形验证：1e9 处 10×10 方环质心精确为 (1e9+5, 1e9+5)，方向无关
    pts_cw = [(1e9, 1e9), (1e9 + 10, 1e9),
              (1e9 + 10, 1e9 + 10), (1e9, 1e9 + 10)]
    pts_ccw = list(reversed(pts_cw))
    assert polygon_centroid(pts_cw) == (1000000005.0, 1000000005.0)
    assert polygon_centroid(pts_ccw) == (1000000005.0, 1000000005.0)
