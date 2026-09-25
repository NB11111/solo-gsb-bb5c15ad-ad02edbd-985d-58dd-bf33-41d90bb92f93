"""闭合轮廓加工计划（cut-plan，只读即时规划）。

对一份已存 SVG path 即时解析并完成轮廓包含层级分类后（与
metrics / clearance / scanlines 同一份解析与曲线细分折线，见
:func:`app.svggeo.analyze_contours`），按以下规则生成闭合轮廓的
加工顺序与每步空走（rapid traverse）距离：

- 仅闭合且非退化轮廓（``outer`` / ``hole``）参与，开放与退化轮廓
  跳过并列入 ``skipped_contours``；
- **后代先加工**：一个轮廓必须等它的**全部后代**（任意深度的
  孔/岛，按 ``parent_original_index`` 传递）加工完后才可加工，
  即先加工最深层的轮廓，逐层向外；
- 每轮在当前**可加工**（未加工且所有后代均已加工）的轮廓中，
  从各自**细分后的全部顶点**里找出距刀头当前位置最近的一个作为
  该轮廓的入刀点，再在这些候选里取最近者；距离相同（平方距离
  精确相等）时依次按**原始索引**、**顶点序号**决胜；
- 加工闭合轮廓后刀头停在入刀点（闭合环走刀一圈后回到入刀点），
  随后以该点为起点继续选择下一步，直至所有有效轮廓加工完毕。

每步的 ``air_distance`` 为刀头从当前位置空走到该步入刀点的
欧氏距离；``total_air_distance`` 为各步之和。整个计算只读、
即时完成：不依赖已保存的规范化结果，也不写库。没有任何有效
闭合轮廓时返回空计划（``steps`` 为空，总空走距离为 0）。
"""

from __future__ import annotations

import math
from typing import Any

from .svggeo import Contour

# 输出量化位数（仅影响 JSON 展示，不改变选点判定）
_DECIMALS = 9


def compute_cut_plan(contours: list[Contour],
                     start_x: float,
                     start_y: float) -> dict[str, Any]:
    """对已分类的轮廓列表生成闭合轮廓加工计划。

    参数:
        contours: :func:`app.svggeo.analyze_contours` 返回的轮廓
            （已带 ``role`` / ``depth`` / ``parent_original_index``）。
        start_x / start_y: 刀头起点，必须为有限数（由路由层校验）。

    返回:
        ``{start, total_contours, steps, total_air_distance,
        skipped_contours}``；``steps`` 每项含加工序号、轮廓
        ``original_index`` / ``role`` / ``depth``、入刀点及其顶点
        序号、本步空走距离与加工后刀头位置。
    """
    valid = [c for c in contours if c.role in ("outer", "hole")]

    skipped = [
        {"original_index": c.original_index, "role": c.role,
         "reason": ("open" if c.role == "open" else "degenerate")}
        for c in sorted(contours, key=lambda c: c.original_index)
        if c.role not in ("outer", "hole")
    ]

    if not valid:
        return {
            "start": {"x": _num(start_x), "y": _num(start_y)},
            "total_contours": 0,
            "steps": [],
            "total_air_distance": 0.0,
            "skipped_contours": skipped,
        }

    by_index = {c.original_index: c for c in valid}

    # 直接子轮廓：parent_original_index 为 None 的是顶层轮廓
    children: dict[int, list[int]] = {c.original_index: [] for c in valid}
    for c in valid:
        pid = c.parent_original_index
        if pid is not None and pid in by_index:
            children[pid].append(c.original_index)

    # 全部后代（任意深度）：子树 DFS
    descendants: dict[int, set[int]] = {}

    def collect(idx: int) -> set[int]:
        if idx not in descendants:
            acc: set[int] = set()
            for ch in children[idx]:
                acc.add(ch)
                acc.update(collect(ch))
            descendants[idx] = acc
        return descendants[idx]

    for c in valid:
        collect(c.original_index)

    remaining = set(by_index)
    steps: list[dict[str, Any]] = []
    head = (start_x, start_y)
    total_air = 0.0
    order = 0

    while remaining:
        # 可加工轮廓：所有后代都已加工（从最深层轮廓开始）
        available = [by_index[i] for i in sorted(remaining)
                     if descendants[i].isdisjoint(remaining)]

        # 每个可加工轮廓选距刀头最近的细分顶点；
        # 顶点序号天然决胜，min 取到的即该轮廓内的最近顶点
        best: tuple[float, int, int, tuple[float, float], Contour] | None = None
        for c in available:
            for v, (px, py) in enumerate(c.points):
                d2 = (px - head[0]) ** 2 + (py - head[1]) ** 2
                # 决胜键：平方距离 -> 原始索引 -> 顶点序号
                key = (d2, c.original_index, v)
                if best is None or key < best[:3]:
                    best = (d2, c.original_index, v, (px, py), c)

        assert best is not None  # 层级为森林结构，必有可加工轮廓
        d2, idx, v, entry, contour = best
        air = math.sqrt(max(0.0, d2))
        order += 1
        total_air += air
        steps.append({
            "order": order,
            "original_index": idx,
            "role": contour.role,
            "depth": contour.depth,
            "entry_vertex": v,
            "entry_point": {"x": _num(entry[0]), "y": _num(entry[1])},
            "air_distance": _num(air),
            "head_after": {"x": _num(entry[0]), "y": _num(entry[1])},
        })

        # 闭合轮廓加工完刀头停在入刀点
        head = entry
        remaining.remove(idx)

    return {
        "start": {"x": _num(start_x), "y": _num(start_y)},
        "total_contours": len(valid),
        "steps": steps,
        "total_air_distance": _num(total_air),
        "skipped_contours": skipped,
    }


def _num(v: float) -> float:
    """量化展示值并消除 -0.0。"""
    r = round(v, _DECIMALS)
    return r + 0.0 if r == 0 else r
