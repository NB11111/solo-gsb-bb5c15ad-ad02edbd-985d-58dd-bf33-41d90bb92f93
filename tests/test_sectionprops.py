"""section-properties：截面惯性矩分析单元测试。"""

import math
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.svggeo import analyze_contours  # noqa: E402
from app.sectionprops import compute_section_properties  # noqa: E402


def _sp(d, fill_rule="nonzero"):
    contours, _warnings = analyze_contours(d, fill_rule)
    return compute_section_properties(contours), contours


# ---------------------------------------------------------------------------
# 基本形状：标准矩形公式 Ix=b·h³/12、Iy=h·b³/12
# ---------------------------------------------------------------------------

def test_single_square():
    m, _ = _sp("M0,0h10v10h-10Z")
    assert m["area"] == 100.0
    assert m["centroid"] == {"x": 5.0, "y": 5.0}
    assert math.isclose(m["ix"], 10 * 10 ** 3 / 12, rel_tol=1e-12)
    assert math.isclose(m["iy"], 10 * 10 ** 3 / 12, rel_tol=1e-12)
    assert m["ixy"] == 0.0
    p = m["principal"]
    assert math.isclose(p["max"], m["ix"], rel_tol=1e-12)
    assert math.isclose(p["min"], m["iy"], rel_tol=1e-12)
    # 主值相等（各向同性）-> 约定 0°
    assert p["axis_angle_deg"] == 0.0
    assert m["contributing_contour_count"] == 1
    assert m["skipped_contours"] == []


def test_wide_rectangle_larger_axis_is_y_90deg():
    # 宽 20 高 10：较大主值绕 y 轴 -> 轴角 90°
    m, _ = _sp("M0,0h20v10h-20Z")
    assert math.isclose(m["ix"], 20 * 10 ** 3 / 12, rel_tol=1e-12)
    assert math.isclose(m["iy"], 10 * 20 ** 3 / 12, rel_tol=1e-12)
    assert m["ixy"] == 0.0
    assert math.isclose(m["principal"]["max"], m["iy"], rel_tol=1e-12)
    assert m["principal"]["axis_angle_deg"] == 90.0


def test_tall_rectangle_larger_axis_is_x_0deg():
    # 宽 10 高 20：较大主值绕 x 轴 -> 轴角 0°
    m, _ = _sp("M0,0h10v20h-10Z")
    assert math.isclose(m["ix"], 10 * 20 ** 3 / 12, rel_tol=1e-12)
    assert math.isclose(m["principal"]["max"], m["ix"], rel_tol=1e-12)
    assert m["principal"]["axis_angle_deg"] == 0.0


def test_right_triangle_centroidal_moments():
    # 直角三角形（直角在原点）b=4, h=2，面积 4
    m, contours = _sp("M0,0 L4,0 L0,2 Z")
    assert contours[0].role == "outer"
    assert math.isclose(m["area"], 4.0, rel_tol=1e-12)
    assert math.isclose(m["centroid"]["x"], 4 / 3, abs_tol=1e-8)
    assert math.isclose(m["centroid"]["y"], 2 / 3, abs_tol=1e-8)
    # Ix = b·h³/36，Iy = h·b³/36，Ixy = -b²h²/72（首象限直角三角，质心积为负）
    assert math.isclose(m["ix"], 4 * 2 ** 3 / 36, abs_tol=1e-8)
    assert math.isclose(m["iy"], 2 * 4 ** 3 / 36, abs_tol=1e-8)
    assert math.isclose(m["ixy"], -(4 ** 2 * 2 ** 2) / 72, abs_tol=1e-8)
    assert 0.0 <= m["principal"]["axis_angle_deg"] < 180.0


# ---------------------------------------------------------------------------
# 层级交替合成
# ---------------------------------------------------------------------------

def test_outer_minus_hole():
    # 外方 0..10 扣内方 2..8：同心，无平行轴项
    # Ix = 10·10³/12 - 6·6³/12 = 833.333 - 108 = 725.333
    m, _ = _sp("M0,0h10v10h-10Z M2,2h6v6h-6Z")
    assert m["area"] == 64.0
    assert m["centroid"] == {"x": 5.0, "y": 5.0}
    assert math.isclose(m["ix"], 10 * 10 ** 3 / 12 - 6 * 6 ** 3 / 12,
                        rel_tol=1e-12)
    assert math.isclose(m["iy"], m["ix"], rel_tol=1e-12)
    assert m["ixy"] == 0.0
    assert m["principal"]["axis_angle_deg"] == 0.0
    assert [c["role"] for c in m["contours"]] == ["outer", "hole"]
    assert m["contours"][1]["signed_contribution"] == -36.0


def test_outer_hole_island_alternation():
    # 100 - 64 + 4 = 40，同心：Ix = 833.333 - 341.333 + 1.333 = 493.333
    d = "M0,0h10v10h-10Z M1,1h8v8h-8Z M4,4h2v2h-2Z"
    m, contours = _sp(d)
    assert m["area"] == 40.0
    assert [c.role for c in contours] == ["outer", "hole", "outer"]
    assert math.isclose(
        m["ix"],
        10 * 10 ** 3 / 12 - 8 * 8 ** 3 / 12 + 2 * 2 ** 3 / 12,
        rel_tol=1e-12)
    assert math.isclose(m["iy"], m["ix"], rel_tol=1e-12)
    assert m["ixy"] == 0.0
    assert m["principal"]["axis_angle_deg"] == 0.0
    assert m["contours"][2]["signed_contribution"] == 4.0


def test_offcentre_hole_parallel_axis_and_angle():
    # 外 0..10 扣右上角 2x2 孔（孔心 (7,3)）：净质心 (4.91667, 5.08333)
    m, _ = _sp("M0,0h10v10h-10Z M6,2h2v2h-2Z")
    assert math.isclose(m["area"], 96.0, rel_tol=1e-12)
    assert math.isclose(m["centroid"]["x"], 4.916666667, abs_tol=1e-9)
    assert math.isclose(m["centroid"]["y"], 5.083333333, abs_tol=1e-9)
    assert math.isclose(m["ix"], 815.333333333, rel_tol=1e-9)
    assert math.isclose(m["iy"], 815.333333333, rel_tol=1e-9)
    assert math.isclose(m["ixy"], 16.666666667, rel_tol=1e-9)
    p = m["principal"]
    assert math.isclose(p["max"], 832.0, rel_tol=1e-9)
    assert math.isclose(p["min"], 798.666666667, rel_tol=1e-9)
    assert p["axis_angle_deg"] == 135.0


# ---------------------------------------------------------------------------
# 旋转：主值不变，主轴角随形状整体旋转（模 180）
# ---------------------------------------------------------------------------

def _rect_path(half_w, half_h, deg):
    c, s = math.cos(math.radians(deg)), math.sin(math.radians(deg))
    verts = [(half_w, half_h), (half_w, -half_h),
             (-half_w, -half_h), (-half_w, half_h)]
    rot = [(x * c - y * s, x * s + y * c) for x, y in verts]
    return "M" + " L".join(f"{x},{y}" for x, y in rot) + " Z"


def test_rotated_rectangle_principal_axis():
    # 20x10 矩形旋转 30°：Imax 主轴（原 y 轴方向 90°）转到 120°
    m, _ = _sp(_rect_path(10, 5, 30))
    i_max = 10 * 20 ** 3 / 12   # 6666.67
    i_min = 20 * 10 ** 3 / 12   # 1666.67
    assert math.isclose(m["principal"]["max"], i_max, rel_tol=1e-9)
    assert math.isclose(m["principal"]["min"], i_min, rel_tol=1e-9)
    assert abs(m["principal"]["axis_angle_deg"] - 120.0) < 1e-6


def test_principal_angle_rotation_sweep():
    i_max = 10 * 20 ** 3 / 12
    i_min = 20 * 10 ** 3 / 12
    for deg in range(0, 180, 10):
        m, _ = _sp(_rect_path(10, 5, deg))
        angle = m["principal"]["axis_angle_deg"]
        assert 0.0 <= angle < 180.0
        # 主值旋转不变
        assert math.isclose(m["principal"]["max"], i_max, rel_tol=1e-9)
        assert math.isclose(m["principal"]["min"], i_min, rel_tol=1e-9)
        # 角度与 (90°+deg) 在模 180 意义下一致（最短角差）
        diff = (angle - (90 + deg) + 90) % 180 - 90
        assert abs(diff) < 1e-6, (deg, angle)


def test_diagonal_thin_rod_axis_perpendicular():
    # 沿 y=x 的细杆（L=10, w=2）：最大主轴与杆垂直 -> 135°
    m, _ = _sp(_rect_path(5, 1, 45))
    assert math.isclose(m["principal"]["max"], 2 * 10 ** 3 / 12, rel_tol=1e-9)
    assert math.isclose(m["principal"]["min"], 10 * 2 ** 3 / 12, rel_tol=1e-9)
    assert math.isclose(m["ixy"], 80.0, rel_tol=1e-9)
    assert abs(m["principal"]["axis_angle_deg"] - 135.0) < 1e-6


# ---------------------------------------------------------------------------
# 曲线细分与方向无关
# ---------------------------------------------------------------------------

def test_circle_isotropic_angle_zero():
    # 单位圆（两段半弧细分）：Ix≈Iy≈π/4，主值相等 -> 0°
    m, contours = _sp("M-1,0 A1,1 0 1,0 1,0 A1,1 0 1,0 -1,0 Z")
    assert contours[0].role == "outer"
    assert 0.77 < m["ix"] < math.pi / 4
    assert math.isclose(m["ix"], m["iy"], rel_tol=1e-9)
    assert abs(m["ixy"]) < 1e-9
    assert m["principal"]["axis_angle_deg"] == 0.0


def test_arc_principal_values_rotation_invariant():
    # 圆细分多边形各向同性：主值相等且对旋转不敏感
    m0, _ = _sp("M-1,0 A1,1 0 1,0 1,0 A1,1 0 1,0 -1,0 Z")
    m90, _ = _sp("M0,-1 A1,1 0 1,0 0,1 A1,1 0 1,0 0,-1 Z")
    assert math.isclose(m0["principal"]["max"],
                        m90["principal"]["max"], rel_tol=1e-9)


def test_quadratic_curve_positive_finite():
    m, _ = _sp("M0,0 L10,0 Q10,10 0,10 Z")
    assert m["area"] > 0.0
    assert m["ix"] > 0.0 and m["iy"] > 0.0
    assert math.isfinite(m["ixy"])


def test_orientation_independent():
    # 同一 20x10 矩形分别顺/逆时针书写
    m_cw, _ = _sp("M0,0h20v10h-20Z")
    m_ccw, _ = _sp("M0,0 L0,10 L20,10 L20,0 Z")
    for key in ("area", "ix", "iy", "ixy"):
        assert m_cw[key] == m_ccw[key], key
    assert m_cw["centroid"] == m_ccw["centroid"]
    assert m_cw["principal"] == m_ccw["principal"]


def test_evenodd_same_as_nonzero():
    d = "M0,0h10v10h-10Z M2,2h6v6h-6Z M3,3h4v4h-4Z"
    m_nz, _ = _sp(d, "nonzero")
    m_eo, _ = _sp(d, "evenodd")
    assert m_nz["ix"] == m_eo["ix"]
    assert m_nz["iy"] == m_eo["iy"]
    assert m_nz["ixy"] == m_eo["ixy"]
    assert m_nz["principal"] == m_eo["principal"]


# ---------------------------------------------------------------------------
# 跳过与零面积
# ---------------------------------------------------------------------------

def test_open_and_degenerate_excluded():
    d = "M0,0h10v10h-10Z M5,5 L8,5 L5,5 Z M20,0 L30,0 L30,10"
    m, _ = _sp(d)
    assert m["area"] == 100.0
    assert m["contributing_contour_count"] == 1
    assert m["contours"][0]["role"] == "outer"
    assert {(s["original_index"], s["reason"])
            for s in m["skipped_contours"]} == {(1, "degenerate"), (2, "open")}


def test_empty_path_null():
    m, _ = _sp("")
    assert m["area"] == 0.0
    assert m["centroid"] is None
    assert m["ix"] is None and m["iy"] is None and m["ixy"] is None
    assert m["principal"] is None
    assert m["contributing_contour_count"] == 0


def test_only_open_null():
    m, _ = _sp("M0,0 L10,0 L10,10")
    assert m["area"] == 0.0
    assert m["ix"] is None
    assert m["principal"] is None


def test_net_area_cancelled_by_holes_null():
    # 外 100 - 孔 64 - 孔 36 = 0：惯性矩与主轴角全部 null
    d = "M0,0h10v10h-10Z M1,1h8v8h-8Z M0.5,0.5h9v4h-9Z"
    m, contours = _sp(d)
    assert [c.role for c in contours] == ["outer", "hole", "hole"]
    assert m["area"] == 0.0
    assert m["centroid"] is None
    assert m["ix"] is None and m["iy"] is None and m["ixy"] is None
    assert m["principal"] is None
    assert m["contributing_contour_count"] == 3


def test_far_from_origin_no_cancellation_error():
    # 1x1 正方形放在 (1e6, 1e6)：远离原点，仍精确得到 Ix=Iy=1/12
    m, _ = _sp("M1000000,1000000h1v1h-1Z")
    assert math.isclose(m["ix"], 1 / 12, abs_tol=1e-8)
    assert math.isclose(m["iy"], 1 / 12, abs_tol=1e-8)
    assert m["principal"]["axis_angle_deg"] == 0.0


def test_far_from_origin_centroid_translation_invariant():
    # 偏心孔组合整体平移 1e9：净质心随动 1e9，惯性矩与原点附近逐位一致
    m0, _ = _sp("M0,0h10v10h-10Z M6,2h2v2h-2Z")
    m1, _ = _sp("M1000000000,1000000000h10v10h-10Z "
                "M1000000006,1000000002h2v2h-2Z")
    assert m1["area"] == m0["area"] == 96.0
    assert math.isclose(m1["centroid"]["x"], m0["centroid"]["x"] + 1e9,
                        abs_tol=1e-6)
    assert math.isclose(m1["centroid"]["y"], m0["centroid"]["y"] + 1e9,
                        abs_tol=1e-6)
    assert m1["ix"] == m0["ix"]
    assert m1["iy"] == m0["iy"]
    assert m1["ixy"] == m0["ixy"]
    assert m1["principal"] == m0["principal"]


def test_no_negative_zero_in_output():
    m, _ = _sp("M-1,0 A1,1 0 1,0 1,0 A1,1 0 1,0 -1,0 Z")
    assert "-0.0" not in repr(m)


def test_contours_detail_sorted_and_skipped_listed():
    d = ("M0,0h10v10h-10Z M2,2h6v6h-6Z M20,0 L30,0 L30,10 "
         "M5,5 L8,5 L5,5 Z")
    m, _ = _sp(d)
    assert [c["original_index"] for c in m["contours"]] == [0, 1]
    outer, hole = m["contours"]
    assert outer == {
        "original_index": 0, "role": "outer", "depth": 0,
        "area": 100.0, "signed_contribution": 100.0,
        "centroid": {"x": 5.0, "y": 5.0}}
    assert hole["area"] == 36.0
    assert hole["signed_contribution"] == -36.0
    assert [s["original_index"] for s in m["skipped_contours"]] == [2, 3]
