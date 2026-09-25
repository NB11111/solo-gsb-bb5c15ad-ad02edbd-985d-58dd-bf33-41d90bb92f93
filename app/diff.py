"""两条已规范化路径之间的版本差异审计。

匹配策略（稳定、与轮廓书写顺序无关）：

1. 以 :func:`app.svggeo.contour_fingerprint` 几何指纹分组，
   同指纹轮廓先按相同 ``original_index`` 配对，剩余项按索引最近贪心配对；
   因此**相同指纹但原始索引变化**（如子路径被重排/插入）仍视为同一轮廓。
2. 指纹未配对的轮廓退而使用 ``original_index`` 一对一配对，
   作为几何变化的候选；索引也对应不上的计入 added / removed。

配对后的差异分类：

- ``geometry``    几何指纹不同（坐标/形状变化；开放路径闭合等也归此类）
- ``hierarchy``   几何相同，但 ``role`` 或 ``depth`` 变化（层级变化优先于方向）
- ``orientation`` 几何与层级均相同，仅原始绕向（有符号面积符号）相反
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

# 面积小于该阈值视为无方向（退化/开放折线），不参与方向判定
_ORIENTATION_AREA_EPS = 1e-12


def compare_normalized(base: dict[str, Any],
                       candidate: dict[str, Any]) -> dict[str, Any]:
    """对比两份 ``normalize_path`` 结果。

    返回 ``{base_id, candidate_id, added, removed, modified, summary}``。
    """
    base_contours = list(base["contours"])
    cand_contours = list(candidate["contours"])

    base_by_fp = _group_by_fingerprint(base_contours)
    cand_by_fp = _group_by_fingerprint(cand_contours)

    pairs: list[tuple[dict[str, Any], dict[str, Any], str]] = []
    matched_base: set[int] = set()
    matched_cand: set[int] = set()

    # ---- 阶段 1：几何指纹匹配 ---------------------------------------------
    for fp, b_group in base_by_fp.items():
        c_group = cand_by_fp.get(fp, [])
        if not c_group:
            continue
        for b, c in _pair_same_fingerprint(b_group, c_group):
            pairs.append((b, c, "fingerprint"))
            matched_base.add(b["original_index"])
            matched_cand.add(c["original_index"])

    # ---- 阶段 2：原始索引兜底配对（几何已变化） ----------------------------
    b_left = [c for c in base_contours if c["original_index"] not in matched_base]
    c_left = [c for c in cand_contours if c["original_index"] not in matched_cand]
    b_by_idx = {c["original_index"]: c for c in b_left}
    for c in c_left:
        b = b_by_idx.pop(c["original_index"], None)
        if b is not None:
            pairs.append((b, c, "index"))
            matched_base.add(b["original_index"])
            matched_cand.add(c["original_index"])

    # ---- 阶段 3：汇总 added / removed / modified --------------------------
    added = [_one_side(c, "candidate")
             for c in cand_contours
             if c["original_index"] not in matched_cand]
    removed = [_one_side(c, "base")
               for c in base_contours
               if c["original_index"] not in matched_base]

    modified: list[dict[str, Any]] = []
    unchanged = 0
    for b, c, matched_by in pairs:
        kind = _classify(b, c)
        if kind is None:
            unchanged += 1
            continue
        modified.append({
            "change_type": kind,
            "matched_by": matched_by,
            "fingerprint": {
                "base": b["fingerprint"],
                "candidate": c["fingerprint"],
            },
            "base": _snapshot(b),
            "candidate": _snapshot(c),
        })

    added.sort(key=lambda e: e["candidate_original_index"])
    removed.sort(key=lambda e: e["base_original_index"])
    modified.sort(key=lambda e: (
        e["base"]["original_index"],
        e["candidate"]["original_index"],
    ))

    return {
        "added": added,
        "removed": removed,
        "modified": modified,
        "summary": {
            "base_contour_count": len(base_contours),
            "candidate_contour_count": len(cand_contours),
            "added": len(added),
            "removed": len(removed),
            "modified": len(modified),
            "unchanged": unchanged,
        },
    }


# ---------------------------------------------------------------------------
# 内部工具
# ---------------------------------------------------------------------------

def _group_by_fingerprint(contours: list[dict[str, Any]]
                          ) -> dict[str, list[dict[str, Any]]]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for c in contours:
        groups[c["fingerprint"]].append(c)
    return groups


def _pair_same_fingerprint(b_group: list[dict[str, Any]],
                           c_group: list[dict[str, Any]]
                           ) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    """同指纹轮廓的稳定一对一配对。

    先配 ``original_index`` 相同的项（最常见的“同一轮廓”情形）；
    剩余项按索引差贪心就近配对（同形重复轮廓，如两个全等螺栓孔）。
    """
    b_by_idx = {c["original_index"]: c for c in b_group}
    pairs: list[tuple[dict[str, Any], dict[str, Any]]] = []
    used_b: set[int] = set()
    used_c: set[int] = set()

    for ci in sorted(c["original_index"] for c in c_group):
        if ci in b_by_idx:
            b = b_by_idx[ci]
            pairs.append((b, next(c for c in c_group
                                  if c["original_index"] == ci)))
            used_b.add(ci)
            used_c.add(ci)

    b_rest = sorted((c for c in b_group if c["original_index"] not in used_b),
                    key=lambda c: c["original_index"])
    c_rest = sorted((c for c in c_group if c["original_index"] not in used_c),
                    key=lambda c: c["original_index"])
    while b_rest and c_rest:
        best_b, best_c, best_d = 0, 0, None
        for bi, b in enumerate(b_rest):
            for ci, c in enumerate(c_rest):
                d = abs(b["original_index"] - c["original_index"])
                if best_d is None or d < best_d:
                    best_b, best_c, best_d = bi, ci, d
        pairs.append((b_rest.pop(best_b), c_rest.pop(best_c)))
    return pairs


def _classify(b: dict[str, Any], c: dict[str, Any]) -> str | None:
    """返回 ``geometry`` / ``hierarchy`` / ``orientation`` / ``None``。"""
    if b["fingerprint"] != c["fingerprint"]:
        return "geometry"
    # 几何一致：层级（角色/深度）变化优先
    if b["role"] != c["role"] or b["depth"] != c["depth"]:
        return "hierarchy"
    b_dir = _orientation_sign(b)
    c_dir = _orientation_sign(c)
    if b_dir != 0 and c_dir != 0 and b_dir != c_dir:
        return "orientation"
    return None


def _orientation_sign(c: dict[str, Any]) -> int:
    area = c.get("signed_area") or 0.0
    if abs(area) < _ORIENTATION_AREA_EPS:
        return 0
    return 1 if area > 0 else -1


def _snapshot(c: dict[str, Any]) -> dict[str, Any]:
    """modified 条目单侧快照：携带索引、角色、深度与元数据。"""
    return {
        "original_index": c["original_index"],
        "role": c["role"],
        "depth": c["depth"],
        "parent_original_index": c["parent_original_index"],
        "closed": c["closed"],
        "reversed": c["reversed"],
        "metadata": c["metadata"],
    }


def _one_side(c: dict[str, Any], side: str) -> dict[str, Any]:
    """added（candidate 侧）/ removed（base 侧）条目，字段扁平化便于消费。"""
    snap = _snapshot(c)
    return {
        f"{side}_fingerprint": c["fingerprint"],
        f"{side}_original_index": snap["original_index"],
        f"{side}_role": snap["role"],
        f"{side}_depth": snap["depth"],
        f"{side}_metadata": snap["metadata"],
    }
