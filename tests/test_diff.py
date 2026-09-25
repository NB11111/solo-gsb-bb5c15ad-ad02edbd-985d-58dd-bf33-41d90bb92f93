"""compare_normalized 差异分类的直接单元测试。"""

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.diff import compare_normalized  # noqa: E402
from app.svggeo import normalize_path  # noqa: E402


def _norm(d, fill_rule="nonzero", meta=None):
    return normalize_path(d, fill_rule, contour_metadata=meta)


def _contour(c, **over):
    out = {
        "original_index": c["original_index"],
        "role": c["role"],
        "depth": c["depth"],
        "parent_original_index": c["parent_original_index"],
        "closed": c["closed"],
        "fingerprint": c["fingerprint"],
        "signed_area": c["signed_area"],
        "reversed": c["reversed"],
        "metadata": c["metadata"],
    }
    out.update(over)
    return out


def test_orientation_change_for_open_polyline():
    # 同一条开放折线反向绘制：指纹相同（反向归一）、面积符号相反
    fwd = "M0,0 L40,0 L40,40"
    rev = "M40,40 L40,0 L0,0"
    diff = compare_normalized(_norm(fwd), _norm(rev))
    assert diff["summary"]["modified"] == 1
    m = diff["modified"][0]
    assert m["change_type"] == "orientation"
    assert m["matched_by"] == "fingerprint"
    assert m["base"]["role"] == m["candidate"]["role"] == "open"
    assert m["base"]["depth"] is None


def test_unchanged_when_only_metadata_differs():
    # 几何/层级/方向完全一致、仅 metadata 不同 -> 不计为 modified
    a = _norm("M0,0h40v40h-40Z", meta=[{"v": 1}])
    b = _norm("M0,0h40v40h-40Z", meta=[{"v": 2}])
    diff = compare_normalized(a, b)
    assert diff["modified"] == []
    assert diff["summary"]["unchanged"] == 1


def test_geometry_change_dominates_hierarchy_and_orientation():
    # 手工构造：指纹不同且角色也不同 -> geometry 优先
    n1 = _norm("M0,0h100v100h-100Z M10,10h20v20h-20Z")
    n2 = _norm("M5,5h20v20h-20Z")
    diff = compare_normalized(n1, n2)
    # 大外环 removed；小环几何变化（即使层级从 hole 变 outer）
    kinds = {m["change_type"] for m in diff["modified"]}
    assert kinds == {"geometry"}
    assert len(diff["removed"]) == 1


def test_degenerate_contours_with_zero_area_no_orientation_false_positive():
    # 两个退化轮廓面积为 0：方向不可判定，同指纹不算 orientation
    a = _norm("M0,0 L10,0 L0,0 Z")
    b = _norm("M0,0 L10,0 L0,0 Z")
    diff = compare_normalized(a, b)
    assert diff["modified"] == []
    assert diff["summary"]["unchanged"] == 1


def test_mixed_added_removed_and_reordered():
    base = _norm("M0,0h40v40h-40Z M10,10h5v5h-5Z M20,20h5v5h-5Z")
    # 保留两个小孔但交换顺序，外环几何变化 -> 按索引兜底成几何变化，
    # 同时新增一个孔
    cand = _norm("M0,0h41v41h-41Z M20,20h5v5h-5Z M10,10h5v5h-5Z M30,30h5v5h-5Z")
    diff = compare_normalized(base, cand)
    s = diff["summary"]
    assert s["added"] == 1 and s["removed"] == 0
    # 外环 idx0：index 兜底几何变化；两个同指纹小孔：unchanged
    assert s["modified"] == 1
    assert diff["modified"][0]["change_type"] == "geometry"
    assert s["unchanged"] == 2
