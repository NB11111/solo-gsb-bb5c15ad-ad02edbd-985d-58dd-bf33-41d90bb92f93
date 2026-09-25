"""svggeo 几何/解析单元测试。"""

import math
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.svggeo import (  # noqa: E402
    contour_fingerprint,
    flatten_subpath,
    normalize_path,
    parse_path,
    reverse_subpath,
    serialize,
    serialize_subpath,
    signed_area,
)


def _subs(d):
    return parse_path(d)


def test_parse_absolute_and_implicit_repeat():
    subs = _subs("M 0,0 H 10 V 10 H 0 Z")
    assert len(subs) == 1
    assert subs[0]["closed"] is True
    assert subs[0]["segments"][1] == ["L", 10.0, 0.0]
    assert subs[0]["segments"][2] == ["L", 10.0, 10.0]


def test_parse_relative_and_multiple_moveto():
    subs = _subs("m10,10h5v5h-5z m20,20h5v5h-5z")
    assert len(subs) == 2
    # 第二个子路径起始点：10+20, 10+20
    assert subs[1]["segments"][0] == ["M", 30.0, 30.0]
    assert subs[0]["closed"] and subs[1]["closed"]


def test_implicit_moveto_after_close():
    subs = _subs("M0,0L5,0L5,5L0,5Z M6,6L8,6L8,8L6,8Z")
    assert len(subs) == 2


def test_arc_flag_adjacent_digits():
    # 标志位与后续数字之间没有分隔符：0 0 10,20
    subs = _subs("M0,0 A5,5 0 00 10,0 Z")
    arc = next(s for s in subs[0]["segments"] if s[0] == "A")
    assert arc[4] == 0 and arc[5] == 0
    assert (arc[6], arc[7]) == (10.0, 0.0)


def test_invalid_path_raises():
    import pytest
    from app.svggeo import SVGPathError

    with pytest.raises(SVGPathError):
        _subs("M0,0 L")
    with pytest.raises(SVGPathError):
        _subs("XYZ")


def test_zero_arg_command_after_close_raises_instead_of_loop():
    # 回归：Z 是零参数命令，其后的非法字符不得被当作 Z 的隐式重复
    # （旧实现每轮不消耗输入，会在此死循环）
    import multiprocessing as mp
    import pytest
    from app.svggeo import SVGPathError

    def _try_parse(d, out):
        try:
            parse_path(d)
            out.put("NO-ERROR")
        except SVGPathError:
            out.put("SVGPathError")
        except Exception as exc:  # 任何其他异常（如 IndexError）也算失败
            out.put(f"OTHER:{type(exc).__name__}")

    def assert_terminates(d):
        ctx = mp.get_context("fork")
        out = ctx.Queue()
        proc = ctx.Process(target=_try_parse, args=(d, out))
        proc.start()
        proc.join(2.0)
        if proc.is_alive():
            proc.terminate()
            proc.join()
            pytest.fail(f"解析 {d!r} 超时：扫描器陷入死循环")
        assert proc.exitcode == 0
        assert out.get_nowait() == "SVGPathError"

    assert_terminates("M0,0Z@")
    assert_terminates("M0,0z@")
    assert_terminates("M0,0 Z 5")


def test_truncated_arc_raises_not_indexerror():
    # 回归：弧线在 large-arc / sweep 标志或端点坐标处被截断时，
    # 必须抛 SVGPathError（旧实现因 d[pos] 越界抛 IndexError -> 500）
    import pytest
    from app.svggeo import SVGPathError

    for d in ("M0,0 A1,1 0",        # 缺 large-arc 标志
              "M0,0 A1,1 0 ",       # 标志前只有空白
              "M0,0 A1,1 0 0",      # 缺 sweep 标志
              "M0,0 A1,1 0 0 ",
              "M0,0 A1,1 0 0 10",   # 缺终点 y
              "M0,0 A1,1 0 0 10,",
              ):
        with pytest.raises(SVGPathError):
            _subs(d)


def test_valid_arc_and_implicit_repeat_unaffected():
    # 修复不得影响：紧贴/逗号分隔的合法标志、隐式重复坐标、多子路径
    arc = next(s for s in _subs("M0,0 A5,5 0 00 10,0 Z")[0]["segments"]
               if s[0] == "A")
    assert arc[4] == 0 and arc[5] == 0 and (arc[6], arc[7]) == (10.0, 0.0)

    arc = next(s for s in _subs("M0,0 A1,1 0 0,1,10,0 Z")[0]["segments"]
               if s[0] == "A")
    assert arc[4] == 0 and arc[5] == 1

    seg = _subs("M0,0 L1,1 2,2 3,3")[0]["segments"]
    assert [tuple(s[1:]) for s in seg if s[0] == "L"] == [
        (1.0, 1.0), (2.0, 2.0), (3.0, 3.0)]

    # Z 之后显式给出的新命令仍然合法
    subs = _subs("M0,0 L5,0 L5,5 Z L0,5")
    assert len(subs) == 2 and subs[1]["closed"] is False


def test_signed_area_screen_coords():
    # 顺时针（屏幕坐标）：(0,0)->(10,0)->(10,10)->(0,10)
    pts = [(0, 0), (10, 0), (10, 10), (0, 10)]
    assert signed_area(pts) > 0
    assert math.isclose(signed_area(pts), 100.0, abs_tol=1e-9)


def test_signed_area_local_reference_translation_invariant():
    # 局部参考点稳定求和：远离原点的小环面积不被 x1*y2-x2*y1
    # 大数相消吞没（1e9 处单个乘积的浮点步长已达 128），
    # 同一环整体平移后面积完全一致
    base = [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0)]
    assert signed_area(base) == 100.0
    for off in (1e6, 1e9):
        moved = [(x + off, y + off) for x, y in base]
        assert signed_area(moved) == 100.0
    # 1x1 小环在 1e9 处：面积 1 不被误判为 0，符号约定不变
    tiny_cw = [(1e9, 1e9), (1e9 + 1, 1e9), (1e9 + 1, 1e9 + 1), (1e9, 1e9 + 1)]
    assert signed_area(tiny_cw) == 1.0
    assert signed_area(list(reversed(tiny_cw))) == -1.0


def test_outer_and_hole_classification():
    # 外环顺时针 + 内部孔洞顺时针（需要被反转为逆时针）
    d = "M0,0h40v40h-40Z M10,10h20v20h-20Z"
    res = normalize_path(d, "nonzero")
    assert res["contours"][0]["role"] == "outer"
    assert res["contours"][0]["depth"] == 0
    assert res["contours"][1]["role"] == "hole"
    assert res["contours"][1]["depth"] == 1
    assert res["contours"][1]["parent_original_index"] == 0
    # 孔洞被反转
    hole = parse_path(res["normalized_d"])[1]
    pts = flatten_subpath(hole)
    assert signed_area(pts) < 0


def test_nested_three_levels():
    # 外环 > 孔 > 最内环（岛）
    d = ("M0,0h60v60h-60Z "
         "M5,5h50v50h-50Z "
         "M10,10h40v40h-40Z")
    res = normalize_path(d, "nonzero")
    roles = [c["role"] for c in res["contours"]]
    depths = [c["depth"] for c in res["contours"]]
    assert roles == ["outer", "hole", "outer"]
    assert depths == [0, 1, 2]
    parents = [c["parent_original_index"] for c in res["contours"]]
    assert parents == [None, 0, 1]


def test_reverse_orientation_already_correct():
    # 外环顺时针、孔洞逆时针 -> 不做任何反转
    d = "M0,0h40v40h-40Z M10,30h20v-20h-20Z"
    res = normalize_path(d, "nonzero")
    assert all(c["reversed"] is False for c in res["contours"])


def test_reverse_preserves_curve_geometry():
    sub = parse_path("M0,0 C10,0 10,10 0,10 L0,20 Z")[0]
    rev = reverse_subpath(sub)
    orig_pts = flatten_subpath(sub)
    rev_pts = flatten_subpath(rev)
    # 反转保留所有曲线采样点（含原隐式 Z 闭合边，故 rev 多一个显式点）
    assert set(map(_round_pt, orig_pts)).issubset(set(map(_round_pt, rev_pts)))
    assert rev_pts[0] == orig_pts[0]
    # 面积符号取反、绝对值相同
    assert math.isclose(signed_area(orig_pts), -signed_area(rev_pts), abs_tol=1e-6)
    # 反转后第一个点应沿原环顺序遇到原第二点
    assert rev_pts[1] == orig_pts[-1]


def _round_pt(p):
    return (round(p[0], 9), round(p[1], 9))


def test_reverse_arc_flips_sweep_only():
    sub = parse_path("M0,0 A5,5 0 0 1 10,0 L10,10 L0,10 Z")[0]
    rev = reverse_subpath(sub)
    rev_arc = next(s for s in rev["segments"] if s[0] == "A")
    orig_arc = next(s for s in sub["segments"] if s[0] == "A")
    assert rev_arc[5] == 1 - orig_arc[5]
    assert rev_arc[1] == orig_arc[1] and rev_arc[2] == orig_arc[2]
    assert math.isclose(signed_area(flatten_subpath(sub)),
                        -signed_area(flatten_subpath(rev)), abs_tol=1e-6)


def test_open_contour_warning_and_untouched():
    d = "M0,0 L40,0 L40,40"  # 没有 Z
    res = normalize_path(d, "nonzero")
    assert res["contours"][0]["role"] == "open"
    assert res["contours"][0]["reversed"] is False
    assert any(w["code"] == "OPEN_CONTOUR" for w in res["warnings"])


def test_degenerate_contour_warning():
    d = "M0,0 L10,0 L0,0 Z"
    res = normalize_path(d, "nonzero")
    assert res["contours"][0]["role"] == "degenerate"
    assert any(w["code"] == "DEGENERATE_CONTOUR" for w in res["warnings"])


def test_custom_contour_metadata_preserved():
    d = "M0,0h40v40h-40Z M10,10h20v20h-20Z"
    res = normalize_path(d, "nonzero",
                         contour_metadata=[{"name": "壳体"}, {"name": "轴孔"}])
    assert res["contours"][0]["metadata"] == {"name": "壳体"}
    assert res["contours"][1]["metadata"] == {"name": "轴孔"}


def test_nonzero_direction_conflict_warning():
    # 外环逆时针 -> 与 nonzero 约定冲突
    d = "M0,40 L40,40 L40,0 L0,0 Z"
    res = normalize_path(d, "nonzero")
    assert any(w["code"] == "NONZERO_DIR_CONFLICT" for w in res["warnings"])


def test_evenodd_also_rewritten():
    d = "M0,0h40v40h-40Z M10,10h20v20h-20Z"
    res = normalize_path(d, "evenodd")
    # 即便 evenodd 不依赖方向，输出仍统一为外顺内逆
    for c, sub in zip(res["contours"], parse_path(res["normalized_d"])):
        a = signed_area(flatten_subpath(sub))
        assert (a > 0) if c["role"] == "outer" else (a < 0)


def test_roundtrip_serialize():
    d = "M0,0h40v40h-40Z M10,10h20v20h-20Z"
    res = normalize_path(d, "nonzero")
    subs = parse_path(res["normalized_d"])
    again = serialize(subs)
    # 再次解析序列化结果必须等价（可再次规范化且不报错）
    res2 = normalize_path(again, "nonzero")
    assert len(res2["contours"]) == 2


def test_separate_sibling_holes_not_nested():
    # 一个大外环，两个互不包含的孔
    d = ("M0,0h100v100h-100Z "
         "M10,10h20v20h-20Z "
         "M60,60h20v20h-20Z")
    res = normalize_path(d, "nonzero")
    assert res["contours"][0]["role"] == "outer"
    assert res["contours"][1]["role"] == "hole"
    assert res["contours"][2]["role"] == "hole"
    assert res["contours"][1]["parent_original_index"] == 0
    assert res["contours"][2]["parent_original_index"] == 0


def test_empty_path_warning():
    res = normalize_path("", "nonzero")
    assert any(w["code"] == "EMPTY_PATH" for w in res["warnings"])
    assert res["normalized_d"] == ""


def test_smooth_and_quadratic_parse():
    subs = _subs("M0,0 Q5,10 10,0 T20,0 Z")
    seg_kinds = [s[0] for s in subs[0]["segments"]]
    assert seg_kinds == ["M", "Q", "Q", "Z"]


# ---------------------------------------------------------------------------
# 几何指纹（版本差异审计）
# ---------------------------------------------------------------------------

SQUARE_CW = [(0.0, 0.0), (40.0, 0.0), (40.0, 40.0), (0.0, 40.0)]
SQUARE_CCW = [(0.0, 0.0), (0.0, 40.0), (40.0, 40.0), (40.0, 0.0)]


def test_fingerprint_invariant_to_orientation_and_startpoint():
    rotated = [SQUARE_CW[2], SQUARE_CW[3], SQUARE_CW[0], SQUARE_CW[1]]
    assert contour_fingerprint(SQUARE_CW, True) \
        == contour_fingerprint(SQUARE_CCW, True) \
        == contour_fingerprint(rotated, True)


def test_fingerprint_open_path_orientation_invariant():
    pts = [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0)]
    assert contour_fingerprint(pts, False) \
        == contour_fingerprint(list(reversed(pts)), False)
    # 开放路径不做循环旋转：换起点视为不同
    shifted = [(10.0, 0.0), (10.0, 10.0), (0.0, 0.0)]
    assert contour_fingerprint(pts, False) != contour_fingerprint(shifted, False)


def test_fingerprint_distinguishes_closed_vs_open_and_geometry():
    pts = [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0)]
    assert contour_fingerprint(pts, True) != contour_fingerprint(pts, False)
    other = [(0.0, 0.0), (11.0, 0.0), (11.0, 10.0), (0.0, 10.0)]
    assert contour_fingerprint(SQUARE_CW, True) \
        != contour_fingerprint(other, True)


def test_fingerprint_quantizes_float_noise():
    noisy = [(x + 0.00000003, y - 0.00000002) for x, y in SQUARE_CW]
    assert contour_fingerprint(SQUARE_CW, True) \
        == contour_fingerprint(noisy, True)


def test_fingerprint_on_reversed_and_reordered_real_paths():
    # 同一几何：外方 + 内孔，仅内孔反向
    d1 = "M0,0h40v40h-40Z M10,10h20v20h-20Z"
    d2 = "M0,0h40v40h-40Z M10,30h20v-20h-20Z"
    r1 = normalize_path(d1, "nonzero")
    r2 = normalize_path(d2, "nonzero")
    assert r1["contours"][0]["fingerprint"] == r2["contours"][0]["fingerprint"]
    assert r1["contours"][1]["fingerprint"] == r2["contours"][1]["fingerprint"]
    # 子路径重排：指纹集合相同
    d3 = "M10,10h20v20h-20Z M0,0h40v40h-40Z"
    r3 = normalize_path(d3, "nonzero")
    assert ({c["fingerprint"] for c in r1["contours"]}
            == {c["fingerprint"] for c in r3["contours"]})
