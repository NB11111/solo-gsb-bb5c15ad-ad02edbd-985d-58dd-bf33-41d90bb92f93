"""加工前轮廓交叉清单的几何单元测试。"""

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.intersections import find_intersections  # noqa: E402
from app.svggeo import Contour, analyze_contours  # noqa: E402


def _find(d, fill_rule="nonzero"):
    contours, _ = analyze_contours(d, fill_rule)
    return contours, find_intersections(contours)


def _types(res):
    return [item["type"] for item in res["intersections"]]


def _by_point(res):
    out = {}
    for item in res["intersections"]:
        if "point" in item:
            out[(item["point"]["x"], item["point"]["y"])] = item
        else:
            out[tuple((p["x"], p["y"]) for p in item["points"])] = item
    return out


# ---------------------------------------------------------------------------
# 无交叉
# ---------------------------------------------------------------------------

def test_empty_path_is_empty():
    _contours, res = _find("")
    assert res["intersections"] == []
    assert res["intersection_count"] == 0
    assert res["checked_contour_count"] == 0
    assert res["skipped_contours"] == []


def test_simple_polygon_has_no_self_intersection():
    _c, res = _find("M0,0h10v10h-10Z")
    assert res["intersection_count"] == 0
    assert res["checked_contour_count"] == 1


def test_concave_polygon_has_no_self_intersection():
    # 凹但不自交的 C 形（左下开口）轮廓
    _c, res = _find("M0,0 L30,0 L30,20 L20,20 L20,10 L10,10 L10,20 L0,20 Z")
    assert res["intersections"] == []


def test_nested_square_and_hole_do_not_intersect():
    _c, res = _find("M0,0h40v40h-40Z M10,10h20v20h-20Z")
    assert res["intersection_count"] == 0
    assert res["checked_contour_count"] == 2


# ---------------------------------------------------------------------------
# 环内自交
# ---------------------------------------------------------------------------

def test_bowtie_self_crossing():
    # 边 0 (0,0)->(10,10) 与边 2 (10,0)->(0,10) 在 (5,5) 真正交叉
    _c, res = _find("M0,0 L10,10 L10,0 L0,10 Z")
    assert _types(res) == ["crossing"]
    item = res["intersections"][0]
    assert item["point"] == {"x": 5.0, "y": 5.0}
    assert item["contours"] == [
        {"original_index": 0, "role": "degenerate", "edge_indices": [0, 2]}]


def test_adjacent_edges_sharing_endpoint_are_not_reported():
    # 普通矩形：相邻边 0/1、1/2、…、3/0 仅共享端点，不应报告
    _c, res = _find("M0,0 L10,0 L10,10 L0,10 Z")
    assert res["intersections"] == []


def test_non_adjacent_shared_vertex_is_endpoint_contact():
    # 收尖/捏合：非相邻边在顶点 (5,5) 共享端点
    # 顶点：0(0,0) 1(10,0) 2(5,5) 3(0,10) 4(5,5) 5(10,10)；共 6 条边 0..5
    _c, res = _find("M0,0 L10,0 L5,5 L0,10 L5,5 L10,10 Z")
    assert _types(res) == ["endpoint_contact"]
    item = res["intersections"][0]
    assert item["point"] == {"x": 5.0, "y": 5.0}
    # 共享该点的边：点 2 处的 1,2、点 4 处的 3,4,5；其中 2/3 本相邻
    # （不报），其余非相邻对合并为 [1,2,3,4,5]
    assert item["contours"][0]["edge_indices"] == [1, 2, 3, 4, 5]


def test_vertex_landing_on_nonadjacent_edge_interior():
    # 顶点 (5,0) 落在非相邻的底边（边 0，0..10）内部：T 型端点接触
    _c, res = _find("M0,0 L10,0 L10,10 L5,0 L0,10 Z")
    by = _by_point(res)
    item = by[(5.0, 0.0)]
    assert item["type"] == "endpoint_contact"
    assert item["contours"][0]["edge_indices"] == [0, 2, 3]


def test_self_collinear_overlap_reports_both_ends_and_overlap():
    # 边 0 (0,5)->(10,5) 与边 3 (7,5)->(3,5) 共线重叠 3..7；
    # 边 3 的两个端点分别落在边 0 上，形成两条端点接触
    _c, res = _find("M0,5 L10,5 L10,0 L7,5 L3,5 L0,0 Z")
    by = _by_point(res)
    overlap = by[((3.0, 5.0), (7.0, 5.0))]
    assert overlap["type"] == "collinear_overlap"
    assert overlap["points"] == [{"x": 3.0, "y": 5.0}, {"x": 7.0, "y": 5.0}]
    assert overlap["contours"][0]["edge_indices"] == [0, 3]
    assert by[(3.0, 5.0)]["type"] == "endpoint_contact"
    assert by[(7.0, 5.0)]["type"] == "endpoint_contact"


# ---------------------------------------------------------------------------
# 不同闭合子路径互交
# ---------------------------------------------------------------------------

def test_crossing_contours_reported():
    # 方块与横穿矩形在左右边界各交叉两次
    _c, res = _find(
        "M0,0 L0,10 L10,10 L10,0 Z M-5,5 L15,5 L15,6 L-5,6 Z")
    crossings = [i for i in res["intersections"] if i["type"] == "crossing"]
    assert len(crossings) == 4
    pts = sorted((i["point"]["x"], i["point"]["y"]) for i in crossings)
    assert pts == [(0.0, 5.0), (0.0, 6.0), (10.0, 5.0), (10.0, 6.0)]
    # 每条交叉都涉及两个轮廓
    for item in crossings:
        assert [g["original_index"] for g in item["contours"]] == [0, 1]


def test_corner_touch_between_contours_is_endpoint_contact():
    # 方块与三角形仅共享角点 (10,0)，无共线重叠
    _c, res = _find("M0,0h10v10h-10Z M10,0 L20,0 L20,10 Z")
    assert _types(res) == ["endpoint_contact"]
    item = res["intersections"][0]
    assert item["point"] == {"x": 10.0, "y": 0.0}
    assert [g["original_index"] for g in item["contours"]] == [0, 1]
    assert item["contours"][0]["edge_indices"] == [0, 1]
    assert item["contours"][1]["edge_indices"] == [0, 2]


def test_three_contours_sharing_corner_merge_edges():
    d = ("M0,0h10v10h-10Z M10,0h10v10h-10Z M5,-5 L10,-5 L10,0 Z")
    _c, res = _find(d)
    by = _by_point(res)
    item = by[(10.0, 0.0)]
    assert item["type"] == "endpoint_contact"
    # 同坐标同类型：三个轮廓的边索引合并到同一条
    assert [g["original_index"] for g in item["contours"]] == [0, 1, 2]
    assert item["contours"][2]["edge_indices"] == [1, 2]


def test_t_contact_between_contours():
    # 第二轮廓顶点 (10,5) 落在第一轮廓的右边（边 1）内部
    _c, res = _find("M0,0h10v10h-10Z M5,5 L15,5 L15,-5 Z")
    by = _by_point(res)
    assert by[(10.0, 5.0)]["type"] == "crossing"      # 边真正穿入
    # (10,0) 是顶点正好压在另一轮廓顶点上：端点接触
    contact = by[(10.0, 0.0)]
    assert contact["type"] == "endpoint_contact"


def test_collinear_overlap_between_contours():
    # 第二轮廓顶边 (2,0)->(8,0) 与第一轮廓底边 (0,0)->(10,0) 共线重叠
    _c, res = _find("M0,0h10v10h-10Z M2,-10 L8,-10 L8,0 L2,0 Z")
    by = _by_point(res)
    overlap = by[((2.0, 0.0), (8.0, 0.0))]
    assert overlap["type"] == "collinear_overlap"
    assert [g["original_index"] for g in overlap["contours"]] == [0, 1]
    assert overlap["contours"][0]["edge_indices"] == [0]
    assert overlap["contours"][1]["edge_indices"] == [2]
    # 重叠区间两个端点同时是端点接触
    assert by[(2.0, 0.0)]["type"] == "endpoint_contact"
    assert by[(8.0, 0.0)]["type"] == "endpoint_contact"


# ---------------------------------------------------------------------------
# 开放 / 退化 / 曲线
# ---------------------------------------------------------------------------

def test_open_contours_skipped():
    _c, res = _find("M0,0 L10,0 L10,10")
    assert res["checked_contour_count"] == 0
    assert res["skipped_contours"] == [
        {"original_index": 0, "role": "open", "reason": "open"}]


def test_open_contour_not_checked_against_closed():
    # 非退化的开放折线穿过方块：因开放轮廓不参与，清单仍为空
    _c, res = _find("M0,0h10v10h-10Z M-5,5 L15,5 L15,6")
    assert res["intersection_count"] == 0
    assert res["checked_contour_count"] == 1
    assert res["skipped_contours"][0]["reason"] == "open"


def test_closed_degenerate_contour_still_checked():
    # 闭合但面积为 0 的折返“三角”仍按字面参与边检查
    d = "M0,0 L10,0 L0,0 Z M5,0 L15,0 L15,10 Z"
    _c, res = _find(d)
    assert res["checked_contour_count"] == 2
    assert res["skipped_contours"] == []
    assert res["intersection_count"] >= 1
    assert any(i["type"] == "collinear_overlap"
               for i in res["intersections"])


def test_single_point_closed_subpath():
    _c, res = _find("M5,5 Z")
    assert res["checked_contour_count"] == 1
    assert res["intersections"] == []


def test_curves_flattened_before_intersection():
    # 两条“圆”（三次贝塞尔细分近似）圆心不同、半径之和大于圆心距 -> 相交
    d = ("M20,10 C25.52,10 30,14.48 30,20 C30,25.52 25.52,30 20,30 "
         "C14.48,30 10,25.52 10,20 C10,14.48 14.48,10 20,10 Z "
         "M34,10 C39.52,10 44,14.48 44,20 C44,25.52 39.52,30 34,30 "
         "C28.48,30 24,25.52 24,20 C24,14.48 28.48,10 34,10 Z")
    _c, res = _find(d)
    crossings = [i for i in res["intersections"] if i["type"] == "crossing"]
    # 两圆横向重叠（圆心距 14，半径各 10）：必有 2 个交叉
    assert len(crossings) == 2


def test_arc_subpaths_flattened():
    # 两条椭圆弧闭合轮廓（弧细分为折线后）相交：圆心距 14、半径各 10 的两圆
    d = ("M-10,0 A10,10 0 0,1 10,0 A10,10 0 0,1 -10,0 Z "
         "M4,0 A10,10 0 0,1 24,0 A10,10 0 0,1 4,0 Z")
    _c, res = _find(d)
    crossings = [i for i in res["intersections"] if i["type"] == "crossing"]
    assert len(crossings) == 2
    for item in crossings:
        assert abs(item["point"]["x"] - 7.0) < 1e-6


# ---------------------------------------------------------------------------
# 合并 / 排序 / 确定性
# ---------------------------------------------------------------------------

def test_same_point_same_type_merges_edge_indices():
    # 同坐标同类型交点不重复造条目：竖线 x=5 穿过方块，(5,0)/(5,10)
    # 各只出现一次，且每条涉及两侧各一条边
    contours = [
        _contour(0, [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0)]),
        _contour(1, [(5.0, -5.0), (5.0, 15.0), (15.0, 15.0), (15.0, -5.0)]),
    ]
    res = find_intersections(contours)
    crossings = [i for i in res["intersections"] if i["type"] == "crossing"]
    pts = sorted((i["point"]["x"], i["point"]["y"]) for i in crossings)
    assert pts == [(5.0, 0.0), (5.0, 10.0)]
    assert len(pts) == len(set(pts))
    for item in crossings:
        assert len(item["contours"]) == 2


def test_results_sorted_by_contour_and_edge_indices():
    # 方块（边 0 底/1 右/2 顶/3 左）与水平矩形（边 0 y=2、边 2 y=8）：
    # 四个交叉的排序键为（轮廓, 最小边索引）：
    # (10,2): ((0,1),(1,0)) < (10,8): ((0,1),(1,2)) <
    # (0,2):  ((0,3),(1,0)) < (0,8):  ((0,3),(1,2))
    square = _contour(0, [(0.0, 0.0), (10.0, 0.0),
                          (10.0, 10.0), (0.0, 10.0)])
    bar = _contour(1, [(-5.0, 2.0), (15.0, 2.0),
                       (15.0, 8.0), (-5.0, 8.0)])
    res = find_intersections([square, bar])
    crossings = [i for i in res["intersections"] if i["type"] == "crossing"]
    pts = [(i["point"]["x"], i["point"]["y"]) for i in crossings]
    assert pts == [(10.0, 2.0), (10.0, 8.0), (0.0, 2.0), (0.0, 8.0)]


def test_results_deterministic_across_calls():
    d = "M0,0 L10,10 L10,0 L0,10 Z M-5,5 L15,5 L15,6 L-5,6 Z"
    outs = []
    for _ in range(3):
        contours, _ = analyze_contours(d, "nonzero")
        outs.append(find_intersections(contours))
    assert outs[0] == outs[1] == outs[2]


def test_evenodd_same_as_nonzero_for_geometry():
    d = "M0,0 L10,10 L10,0 L0,10 Z"
    _c, a = _find(d, "nonzero")
    _c, b = _find(d, "evenodd")
    assert a["intersections"] == b["intersections"]


# ---------------------------------------------------------------------------
# 尺度不变性：量纲一致的平行/共线容差（1e9 长边 × 短竖边回归）
# ---------------------------------------------------------------------------

def test_long_horizontal_short_vertical_internal_crossing_not_missed():
    # 两条闭合轮廓：长约 1e9 的水平边被短竖边在内部正交穿过——
    # 旧实现的角域容差 _EPS_REL*Lmax≈1（弧度）会把正交边误判为平行，
    # 再被共线距离阈值（≈1）挡掉而返回空清单，放行相交路径
    K = 5e8
    d = (f"M-{K},0 L{K},0 L{K},2 L-{K},2 Z "
         f"M0,-2 L1,-2 L1,2 L0,2 Z")
    _c, res = _find(d)
    assert res["intersection_count"] >= 1
    by = _by_point(res)
    # 小矩形底边 (0,-2)->(1,-2) 与左边 (0,-2)->(0,2)：与大矩形
    # 底边 (y=0) 的交点 (1,0)/(0,0) 严格位于两条边各自内部 -> crossing。
    # 旧实现下 (0,0) 因 1e9 长边被吞进“平行”分支而漏报/降级为空清单。
    assert by[(0.0, 0.0)]["type"] == "crossing"
    assert by[(1.0, 0.0)]["type"] == "crossing"
    # 短竖边端点 (0,2) 落在大矩形顶边内部：仍是 endpoint_contact，不升级
    assert by[(0.0, 2.0)]["type"] == "endpoint_contact"


def _transformed_d(k, tx=0.0, ty=0.0):
    """固定非退化构型经整体平移 + 等比放大后的路径（顶点数、边序一一对应）。

    基准尺度下小矩形左边 (0,-1)->(0,3) 在大矩形两条长边内部 crossing，
    其余边对产生 endpoint_contact / collinear_overlap。
    """
    def p(x, y):
        return f"{x*k+tx},{y*k+ty}"
    return ("M" + p(-5, 0) + " L" + p(5, 0) + " L" + p(5, 2) +
            " L" + p(-5, 2) + " Z M" + p(0, -1) + " L" + p(1, -1) +
            " L" + p(1, 3) + " L" + p(0, 3) + " Z")


def _signature(res, k, tx=0.0, ty=0.0):
    """提取 (类型, 涉及轮廓/边索引) 序列，坐标归一化回基准尺度。"""
    out = []
    for item in res["intersections"]:
        groups = tuple((g["original_index"], tuple(g["edge_indices"]))
                       for g in item["contours"])
        if "point" in item:
            xy = (round((item["point"]["x"] - tx) / k, 6),
                  round((item["point"]["y"] - ty) / k, 6))
            key = (item["type"], xy)
        else:
            xy = tuple((round((q["x"] - tx) / k, 6),
                        round((q["y"] - ty) / k, 6)) for q in item["points"])
            key = (item["type"], xy)
        out.append((key, groups))
    return sorted(out)


def test_classification_invariant_under_translation_and_uniform_scale():
    # 基准
    _c, base = _find(_transformed_d(1.0))
    sig_base = _signature(base, 1.0)
    assert sig_base  # 构型本身确有交叉，不是平凡空清单
    for k, tx, ty in [
        (1e3, 0.0, 0.0),
        (1e6, 0.0, 0.0),
        (1e9, 0.0, 0.0),      # 原漏报尺度
        (1e9, 1e12, 1e12),    # 大尺度 + 远离原点平移
        (1e-3, 0.0, 0.0),     # 等比缩小
    ]:
        _c, res = _find(_transformed_d(k, tx, ty))
        assert _signature(res, k, tx, ty) == sig_base


def test_near_parallel_separated_edges_not_false_positive():
    # 近乎平行但分离（垂直间距 >> 相对容差 × 短边）的边对不得误报
    import math
    from app.intersections import _classify
    ang = 1e-11
    for lu, lv, gap in [(1.0, 1.0, 1e-6),
                        (1e9, 1.0, 1e-3),
                        (1e12, 1e3, 1.0)]:
        a, b = (0.0, 0.0), (lu, 0.0)
        c = (lu / 2, gap)
        d = (c[0] + lv * math.cos(ang), c[1] + lv * math.sin(ang))
        assert _classify(a, b, c, d) == []
    # 对照：真正共线重叠仍被识别
    r = _classify((0.0, 0.0), (1e9, 0.0),
                  (4e8, 0.0), (6e8, 0.0))
    assert r and r[0][0] == "collinear_overlap"
    # 对照：近乎平行但延长后相交（距端点非参数容差内）的真正交叉仍报
    # 角度略大于角域阈值、交点在线段内部
    a, b = (0.0, 0.0), (1.0, 0.0)
    c, d = (0.5, -1.0), (0.5 + 2.0 * math.tan(1e-7), 1.0)
    r = _classify(a, b, c, d)
    assert r and r[0][0] == "crossing"


def test_collinear_overlap_consistent_under_scale():
    # 共线重叠区间与端点接触的类型/边索引在放大后保持一致
    def d_for(k, tx, ty):
        def p(x, y):
            return f"{x*k+tx},{y*k+ty}"
        return ("M" + p(0, 0) + "h" + f"{10*k}v" + f"{10*k}h" + f"-{10*k}Z "
                + "M" + p(2, -10) + " L" + p(8, -10) + " L" + p(8, 0)
                + " L" + p(2, 0) + " Z")
    _c, base = _find(d_for(1.0, 0.0, 0.0))
    sig = _signature(base, 1.0)
    for k, tx, ty in [(1e9, 0.0, 0.0), (1e9, 1e13, -1e13), (1e-6, 0.0, 0.0)]:
        _c, res = _find(d_for(k, tx, ty))
        assert _signature(res, k, tx, ty) == sig


def test_pinch_contact_merges_at_large_coordinates():
    # 非相邻边共享非有理数顶点（K/3）：大坐标下插值点与字面顶点相差 ULP，
    # 合并键须按相对精度吸收，仍合并为一条 endpoint_contact（边索引同基准）
    def d_for(k):
        third = k / 3
        return (f"M0,0 L{k},0 L{third},{third} L0,{k} L{third},{third} "
                f"L{k},{k} Z")
    _c, small = _find(d_for(1.0))
    _c, big = _find(d_for(1e13))
    assert len(small["intersections"]) == 1
    assert len(big["intersections"]) == 1
    s, b = small["intersections"][0], big["intersections"][0]
    assert s["type"] == b["type"] == "endpoint_contact"
    assert (s["contours"][0]["edge_indices"]
            == b["contours"][0]["edge_indices"] == [1, 2, 3, 4, 5])


# ---------------------------------------------------------------------------
# 手工 Contour 原语
# ---------------------------------------------------------------------------

def _contour(idx, pts, role="outer"):
    return Contour(
        original_index=idx,
        subpath={"segments": [], "closed": True},
        points=pts,
        area=1.0,
        bounds=(min(x for x, _ in pts), min(y for _, y in pts),
                max(x for x, _ in pts), max(y for _, y in pts)),
        closed=True,
        degenerate=False,
        role=role,
        depth=0,
    )
