"""SVG path 解析、轮廓层级分类与方向规范化。

无第三方依赖：自行实现 SVG path 数据的词法/语法解析，
曲线段（二次/三次贝塞尔、椭圆弧）通过细分为线段处理。

方向约定（SVG 屏幕坐标系，y 轴向下）：
    外环（outer，深度偶数）→ 顺时针，signed area > 0
    孔洞（hole，深度奇数） → 逆时针，signed area < 0

该约定与 OpenCV RETR_CCOUNTER（外正内负）及多数形状库一致；
对 nonzero 填充规则方向相反的轮廓会被重写，evenodd 规则下
原本不依赖方向，但统一方向后同样正确且便于下游使用。
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass, field
from typing import Any

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

_NUMBER_RE = re.compile(r"[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?")
_COMMANDS = set("MmLlHhVvCcSsQqTtAaZz")

CURVE_FLAT_STEPS = 12          # 贝塞尔细分段数
_ARC_MAX_SEGMENTS = 128        # 椭圆弧最多细分段数
FINGERPRINT_DECIMALS = 6       # 几何指纹坐标量化位数


class SVGPathError(ValueError):
    """SVG path 数据无法解析。"""


# ---------------------------------------------------------------------------
# 解析
# ---------------------------------------------------------------------------

def parse_path(d: str) -> list[dict[str, Any]]:
    """解析 SVG path 的 ``d`` 字符串为子路径列表。

    每个子路径形如::

        {"segments": [["M", x, y], ["L", x, y], ["Z"]], "closed": True}

    所有坐标统一转换为绝对坐标；重复隐式命令已展开；
    ``M ... M`` 之间隐式 ``Z`` 的子路径标记为 ``closed=False``。
    """
    i, n = 0, len(d)
    subpaths: list[dict[str, Any]] = []
    cur: dict[str, Any] | None = None
    cmd = ""
    pen = (0.0, 0.0)
    start = (0.0, 0.0)
    last_c_ctrl: tuple[float, float] | None = None
    last_q_ctrl: tuple[float, float] | None = None

    def skip_sep(pos: int) -> int:
        while pos < n and d[pos] in " \t\r\n,":
            pos += 1
        return pos

    def read_number(pos: int) -> tuple[float, int]:
        pos = skip_sep(pos)
        m = _NUMBER_RE.match(d, pos)
        if not m:
            context = d[max(0, pos - 10):pos + 10]
            raise SVGPathError(f"在位置 {pos} 附近期望数值，得到 '{context}'")
        return float(m.group()), m.end()

    def read_flag(pos: int) -> tuple[int, int]:
        # 逗号只可能是分隔符，跳过空格与逗号；数字紧贴标志时直接读取下一位
        while pos < n and d[pos] in " \t\r\n,":
            pos += 1
        if pos >= n:
            raise SVGPathError(f"在位置 {pos} 期望弧线标志 0/1，但路径已结束（弧线参数被截断）")
        if d[pos] in "01":
            return int(d[pos]), pos + 1
        raise SVGPathError(f"在位置 {pos} 期望弧线标志 0/1，得到 '{d[pos]}'")

    def new_sub(pos: tuple[float, float]) -> dict[str, Any]:
        return {"segments": [["M", pos[0], pos[1]]], "closed": False}

    def add(seg: list[Any]) -> None:
        assert cur is not None
        cur["segments"].append(seg)

    def ensure_open() -> None:
        """绘图命令前确保有打开的子路径（处理 Z 后的隐式 moveto）。"""
        nonlocal cur
        if cur is None or cur["closed"]:
            cur = new_sub(pen)
            subpaths.append(cur)

    while i < n:
        i = skip_sep(i)
        if i >= n:
            break
        if d[i] in _COMMANDS:
            cmd = d[i]
            i += 1
        elif cmd == "":
            raise SVGPathError(f"路径必须以移动/绘图命令开始（位置 {i}）")
        elif cmd in ("Z", "z"):
            # Z 是零参数命令，按 SVG 规范不得隐式重复；此处每轮循环要么
            # 消耗至少一个字符、要么抛错，否则会在非法字符上死循环
            raise SVGPathError(
                f"Z 命令后必须显式给出新命令（位置 {i}，得到 '{d[i]}'）")
        # else: 命令重复（隐式重复上一条命令），cmd 保持不变

        relative = cmd.islower()
        c = cmd.upper()

        def pt(x: float, y: float) -> tuple[float, float]:
            return (x + pen[0], y + pen[1]) if relative else (x, y)

        if c == "M":
            x, i = read_number(i)
            y, i = read_number(i)
            if cur is not None and not cur["closed"] and len(cur["segments"]) == 1:
                # 退化的空 M 子路径：丢弃
                subpaths.pop()
            pen = start = pt(x, y)
            cur = new_sub(pen)
            subpaths.append(cur)
            last_c_ctrl = last_q_ctrl = None
            cmd = "l" if relative else "L"  # 后续坐标对隐式为 lineto

        elif c == "L":
            x, i = read_number(i)
            y, i = read_number(i)
            ensure_open()
            pen = pt(x, y)
            add(["L", pen[0], pen[1]])
            last_c_ctrl = last_q_ctrl = None

        elif c == "H":
            x, i = read_number(i)
            ensure_open()
            pen = (x + pen[0], pen[1]) if relative else (x, pen[1])
            add(["L", pen[0], pen[1]])
            last_c_ctrl = last_q_ctrl = None

        elif c == "V":
            y, i = read_number(i)
            ensure_open()
            pen = (pen[0], y + pen[1]) if relative else (pen[0], y)
            add(["L", pen[0], pen[1]])
            last_c_ctrl = last_q_ctrl = None

        elif c == "C":
            x1, i = read_number(i); y1, i = read_number(i)
            x2, i = read_number(i); y2, i = read_number(i)
            x, i = read_number(i); y, i = read_number(i)
            ensure_open()
            p1, p2, end = pt(x1, y1), pt(x2, y2), pt(x, y)
            add(["C", p1[0], p1[1], p2[0], p2[1], end[0], end[1]])
            pen = end
            last_c_ctrl, last_q_ctrl = p2, None

        elif c == "S":
            x2, i = read_number(i); y2, i = read_number(i)
            x, i = read_number(i); y, i = read_number(i)
            ensure_open()
            p1 = reflect(last_c_ctrl, pen) if last_c_ctrl is not None else pen
            p2, end = pt(x2, y2), pt(x, y)
            add(["C", p1[0], p1[1], p2[0], p2[1], end[0], end[1]])
            pen = end
            last_c_ctrl, last_q_ctrl = p2, None

        elif c == "Q":
            x1, i = read_number(i); y1, i = read_number(i)
            x, i = read_number(i); y, i = read_number(i)
            ensure_open()
            p1, end = pt(x1, y1), pt(x, y)
            add(["Q", p1[0], p1[1], end[0], end[1]])
            pen = end
            last_q_ctrl, last_c_ctrl = p1, None

        elif c == "T":
            x, i = read_number(i); y, i = read_number(i)
            ensure_open()
            p1 = reflect(last_q_ctrl, pen) if last_q_ctrl is not None else pen
            end = pt(x, y)
            add(["Q", p1[0], p1[1], end[0], end[1]])
            pen = end
            last_q_ctrl, last_c_ctrl = p1, None

        elif c == "A":
            rx, i = read_number(i)
            ry, i = read_number(i)
            rot, i = read_number(i)
            large, i = read_flag(i)
            sweep, i = read_flag(i)
            x, i = read_number(i)
            y, i = read_number(i)
            ensure_open()
            end = pt(x, y)
            add(["A", abs(rx), abs(ry), rot, large, sweep, end[0], end[1]])
            pen = end
            last_c_ctrl = last_q_ctrl = None

        elif c == "Z":
            if cur is None:
                raise SVGPathError("Z 命令没有对应的子路径")
            add(["Z"])
            cur["closed"] = True
            pen = start
            last_c_ctrl = last_q_ctrl = None
        else:  # pragma: no cover - 理论不可达
            raise SVGPathError(f"不支持的命令: {cmd}")

    return subpaths


def reflect(point: tuple[float, float] | None, pen: tuple[float, float]) -> tuple[float, float]:
    if point is None:
        return pen
    return (2 * pen[0] - point[0], 2 * pen[1] - point[1])


# ---------------------------------------------------------------------------
# 细分（flatten）：把曲线段展开成折线
# ---------------------------------------------------------------------------

def flatten_subpath(sub: dict[str, Any], steps: int = CURVE_FLAT_STEPS) -> list[tuple[float, float]]:
    """将子路径展开为顶点列表（不含回到起点的重复点）。"""
    pts: list[tuple[float, float]] = []
    pen: tuple[float, float] | None = None
    for seg in sub["segments"]:
        c = seg[0]
        if c == "M":
            pen = (seg[1], seg[2])
            pts.append(pen)
        elif c == "L":
            pen = (seg[1], seg[2])
            pts.append(pen)
        elif c == "Q":
            assert pen is not None
            c1, end = (seg[1], seg[2]), (seg[3], seg[4])
            for k in range(1, steps + 1):
                pts.append(_quad(pen, c1, end, k / steps))
            pen = end
        elif c == "C":
            assert pen is not None
            c1, c2, end = (seg[1], seg[2]), (seg[3], seg[4]), (seg[5], seg[6])
            for k in range(1, steps + 1):
                pts.append(_cubic(pen, c1, c2, end, k / steps))
            pen = end
        elif c == "A":
            assert pen is not None
            ex, ey = seg[6], seg[7]
            arc_pts = _arc_points(pen, seg[1], seg[2], seg[3], seg[4], seg[5], (ex, ey))
            pts.extend(arc_pts[1:])  # arc_pts[0] == pen，避免重复
            pen = (ex, ey)
        elif c == "Z":
            pen = pts[0]
            # 不追加闭合重复点；面积与点内测试按闭合环处理
    return pts


def _quad(p0, p1, p2, t):
    mt = 1 - t
    x = mt * mt * p0[0] + 2 * mt * t * p1[0] + t * t * p2[0]
    y = mt * mt * p0[1] + 2 * mt * t * p1[1] + t * t * p2[1]
    return (x, y)


def _cubic(p0, p1, p2, p3, t):
    mt = 1 - t
    x = (mt**3 * p0[0] + 3 * mt*mt*t * p1[0]
         + 3 * mt*t*t * p2[0] + t**3 * p3[0])
    y = (mt**3 * p0[1] + 3 * mt*mt*t * p1[1]
         + 3 * mt*t*t * p2[1] + t**3 * p3[1])
    return (x, y)


def _arc_points(p0, rx, ry, phi_deg, large_arc, sweep, p1):
    """椭圆弧端点参数化 -> 细分点列表（含起点与终点）。"""
    if p0 == p1 or rx == 0 or ry == 0:
        return [p0, p1]

    phi = math.radians(phi_deg)
    cos_phi, sin_phi = math.cos(phi), math.sin(phi)

    # 中点变换到椭圆主轴坐标系
    dx, dy = (p0[0] - p1[0]) / 2.0, (p0[1] - p1[1]) / 2.0
    x1p = cos_phi * dx + sin_phi * dy
    y1p = -sin_phi * dx + cos_phi * dy

    # 半径自动修正（SVG 规范 6.5.1 实现说明 F6.6）
    lam = (x1p * x1p) / (rx * rx) + (y1p * y1p) / (ry * ry)
    if lam > 1:
        scale = math.sqrt(lam)
        rx *= scale
        ry *= scale

    rxy = rx * rx * ry * ry
    radicand = (rxy - rx * rx * y1p * y1p - ry * ry * x1p * x1p) \
        / (rx * rx * y1p * y1p + ry * ry * x1p * x1p)
    factor = math.sqrt(max(0.0, radicand))
    if large_arc == sweep:
        factor = -factor
    cxp = factor * rx * y1p / ry
    cyp = -factor * ry * x1p / rx

    cx = cos_phi * cxp - sin_phi * cyp + (p0[0] + p1[0]) / 2.0
    cy = sin_phi * cxp + cos_phi * cyp + (p0[1] + p1[1]) / 2.0

    def angle(ux, uy, vx, vy):
        dot = max(-1.0, min(1.0, (ux * vx + uy * vy)
                            / math.hypot(ux, uy) / math.hypot(vx, vy)))
        a = math.acos(dot)
        if ux * vy - uy * vx < 0:
            a = -a
        return a

    theta1 = angle(1, 0, (x1p - cxp) / rx, (y1p - cyp) / ry)
    dtheta = angle((x1p - cxp) / rx, (y1p - cyp) / ry,
                   (-x1p - cxp) / rx, (-y1p - cyp) / ry) % (2 * math.pi)
    if sweep and dtheta < 0:
        dtheta += 2 * math.pi
    if not sweep and dtheta > 0:
        dtheta -= 2 * math.pi

    seg_count = max(2, min(_ARC_MAX_SEGMENTS,
                           int(math.ceil(abs(dtheta) / (math.pi / 16)))))
    pts = []
    for k in range(seg_count + 1):
        th = theta1 + dtheta * k / seg_count
        ex = rx * math.cos(th)
        ey = ry * math.sin(th)
        pts.append((cx + cos_phi * ex - sin_phi * ey,
                    cy + sin_phi * ex + cos_phi * ey))
    pts[-1] = p1  # 消除浮点误差，终点精确
    return pts


# ---------------------------------------------------------------------------
# 几何计算
# ---------------------------------------------------------------------------

def signed_area(points: list[tuple[float, float]]) -> float:
    """鞋带公式。y 轴向下坐标系中：正值=顺时针，负值=逆时针。

    求和在以**首顶点为局部参考点**的相对坐标下进行：点列始终按
    闭合环（含末点回首点的闭合边）求和，参考点引入的附加项沿环
    伸缩相消（``Σ(x2−x1) = Σ(y1−y2) = 0``），数学上与绝对坐标
    鞋带公式恒等；但相对坐标的量级由环自身尺寸决定，而非其到原点
    的距离——避免远离原点时 ``x1*y2 − x2*y1`` 两个大数相消吞掉
    小环面积（坐标 1e9 处单个乘积的浮点步长已达 128，足以吞没
    10×10 环的全部面积）。同一环整体平移后相对坐标不变，面积
    因此平移不变。
    """
    if len(points) < 3:
        return 0.0
    rx, ry = points[0]
    s = 0.0
    for (x1, y1), (x2, y2) in zip(points, points[1:] + points[:1]):
        s += (x1 - rx) * (y2 - ry) - (x2 - rx) * (y1 - ry)
    return s / 2.0


def bbox(points: list[tuple[float, float]]) -> tuple[float, float, float, float]:
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    return (min(xs), min(ys), max(xs), max(ys))


def dedupe_ring(points: list[tuple[float, float]],
                eps: float = 1e-12) -> list[tuple[float, float]]:
    """去除连续重复点，包括首尾重复。"""
    out: list[tuple[float, float]] = []
    for p in points:
        if not out or abs(p[0] - out[-1][0]) > eps or abs(p[1] - out[-1][1]) > eps:
            out.append(p)
    if len(out) > 1 and abs(out[0][0] - out[-1][0]) <= eps and abs(out[0][1] - out[-1][1]) <= eps:
        out.pop()
    return out


def point_in_ring(p: tuple[float, float],
                  ring: list[tuple[float, float]]) -> int:
    """射线法 + 边界判定。返回 1=内部, 0=在边上, -1=外部。

    边界容差按环的尺寸自适应。
    """
    minx, miny, maxx, maxy = bbox(ring)
    eps = 1e-9 * max(1.0, maxx - minx, maxy - miny)
    x, y = p
    inside = False
    n = len(ring)
    for k in range(n):
        a, b = ring[k], ring[(k + 1) % n]
        if _on_segment(a, b, p, eps):
            return 0
        if (a[1] > y) != (b[1] > y):
            xint = a[0] + (b[0] - a[0]) * (y - a[1]) / (b[1] - a[1])
            if xint > x:
                inside = not inside
    return 1 if inside else -1


def _on_segment(a, b, p, eps) -> bool:
    cross = (b[0] - a[0]) * (p[1] - a[1]) - (b[1] - a[1]) * (p[0] - a[0])
    if abs(cross) > eps * max(1.0, math.hypot(b[0] - a[0], b[1] - a[1])):
        return False
    return (min(a[0], b[0]) - eps <= p[0] <= max(a[0], b[0]) + eps
            and min(a[1], b[1]) - eps <= p[1] <= max(a[1], b[1]) + eps)


def point_segment_dist2(p: tuple[float, float],
                        a: tuple[float, float],
                        b: tuple[float, float]) -> float:
    """点 ``p`` 到线段 ``ab`` 的平方距离（自动处理 a==b 的零长度段）。"""
    abx, aby = b[0] - a[0], b[1] - a[1]
    apx, apy = p[0] - a[0], p[1] - a[1]
    denom = abx * abx + aby * aby
    if denom == 0.0:
        return apx * apx + apy * apy
    t = (apx * abx + apy * aby) / denom
    if t <= 0.0:
        cx, cy = a
    elif t >= 1.0:
        cx, cy = b
    else:
        cx, cy = a[0] + t * abx, a[1] + t * aby
    dx, dy = p[0] - cx, p[1] - cy
    return dx * dx + dy * dy


def segment_segment_dist2(a: tuple[float, float],
                          b: tuple[float, float],
                          c: tuple[float, float],
                          d: tuple[float, float]) -> float:
    """两线段间的最小平方欧氏距离。

    先按参数方程求解内点最近对（线段相交时距离为 0），
    无解时退化为四个端点-线段距离的最小值；天然兼容零长度段。
    """
    ux, uy = b[0] - a[0], b[1] - a[1]
    vx, vy = d[0] - c[0], d[1] - c[1]
    wx, wy = a[0] - c[0], a[1] - c[1]
    uu = ux * ux + uy * uy
    vv = vx * vx + vy * vy
    uv = ux * vx + uy * vy
    uw = ux * wx + uy * wy
    vw = vx * wx + vy * wy
    denom = uu * vv - uv * uv

    s, t = 0.0, 0.0
    parallel = False
    if denom != 0.0:
        s = (uv * vw - vv * uw) / denom
        if 0.0 <= s <= 1.0:
            t = (uv * s + vw) / vv if vv != 0.0 else 0.0
            if not (0.0 <= t <= 1.0):
                parallel = True
        else:
            parallel = True
    else:
        parallel = True

    if parallel:
        # 解不在两线段内部：最小值一定落在端点到另一线段的距离上
        return min(point_segment_dist2(a, c, d),
                   point_segment_dist2(b, c, d),
                   point_segment_dist2(c, a, b),
                   point_segment_dist2(d, a, b))

    cx, cy = a[0] + s * ux, a[1] + s * uy
    ex, ey = c[0] + t * vx, c[1] + t * vy
    dx, dy = cx - ex, cy - ey
    return dx * dx + dy * dy



# ---------------------------------------------------------------------------
# 几何指纹：版本差异审计中跨版本稳定地识别“同一轮廓”
# ---------------------------------------------------------------------------

def contour_fingerprint(points: list[tuple[float, float]],
                        closed: bool,
                        decimals: int = FINGERPRINT_DECIMALS) -> str:
    """轮廓几何指纹（与起点、绘制方向无关）。

    - 坐标量化到 ``decimals`` 位后重新去重，吸收表示层浮点噪声；
    - 闭合环：环上任意起点等价（循环旋转），顺/逆时针等价（反向），
      取「反向 × 旋转」四种形态中字典序最小的序列；
    - 开放路径：无循环等价，仅消除方向（取原序与逆序的较小者）；
    - 闭合标志参与指纹，同一折线的开放/闭合形态不会互相匹配。

    返回 sha256 十六进制摘要。
    """
    seq = _quantize_ring(points, decimals)
    if closed:
        # 闭合环：循环旋转 + 反向，四种形态取字典序最小（退化环同样成立）
        canonical = min(
            _min_rotation(seq),
            _min_rotation(list(reversed(seq))),
        )
    else:
        # 开放路径：无循环等价，仅消除方向（取原序与逆序的较小者）
        canonical = min(seq, list(reversed(seq)))
    payload = json.dumps(
        {"closed": closed, "points": canonical},
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _quantize_ring(points: list[tuple[float, float]],
                   decimals: int) -> list[tuple[float, float]]:
    """量化取整（去掉 -0.0）并去除量化后连续重复的点。"""
    q = 10 ** decimals
    out: list[tuple[float, float]] = []
    for x, y in points:
        px = round(math.floor(x * q + 0.5) / q, decimals)
        py = round(math.floor(y * q + 0.5) / q, decimals)
        px = px + 0.0  # -0.0 -> 0.0
        py = py + 0.0
        if not out or out[-1] != (px, py):
            out.append((px, py))
    if len(out) > 1 and out[0] == out[-1]:
        out.pop()
    return out


def _min_rotation(seq: list) -> list:
    """Booth 算法：返回字典序最小的循环旋转（O(n)）。"""
    n = len(seq)
    if n == 0:
        return []
    s = seq + seq
    i, j, k = 0, 1, 0
    while i < n and j < n and k < n:
        a, b = s[i + k], s[j + k]
        if a == b:
            k += 1
        elif a > b:
            i = i + k + 1
            if i <= j:
                i = j + 1
            k = 0
        else:
            j = j + k + 1
            if j <= i:
                j = i + 1
            k = 0
    start = min(i, j)
    return s[start:start + n]


# ---------------------------------------------------------------------------
# 方向重写
# ---------------------------------------------------------------------------

def reverse_subpath(sub: dict[str, Any]) -> dict[str, Any]:
    """反转子路径绘制方向，保留原始命令形态（贝塞尔/弧不展平）。

    通过追踪每个段的“起点”构建端点链，确保反转后曲线连接到
    原段起点而非错误地保留原终点：

    - L 坐标点顺序反转
    - C / Q：控制点顺序交换
    - A：仅切换 sweep-flag，其余参数不变
    - Z 闭合环：第一条反转段连到原起始点，末尾仍以 Z 闭合
    """
    segs = sub["segments"]
    if not segs or segs[0][0] != "M":
        raise SVGPathError("子路径缺少起始 M 命令")

    closed = sub["closed"]
    start = (segs[0][1], segs[0][2])
    draw = segs[1:]

    # 每段的起点（Z 段由其前一段决定）
    chain_start: list[tuple[float, float]] = []
    pen = start
    for seg in draw:
        chain_start.append(pen)
        c = seg[0]
        if c == "L":
            pen = (seg[1], seg[2])
        elif c == "Q":
            pen = (seg[3], seg[4])
        elif c == "C":
            pen = (seg[5], seg[6])
        elif c == "A":
            pen = (seg[6], seg[7])
        elif c == "Z":
            pen = start

    if closed:
        new_start = start
    else:
        new_start = pen  # 开放路径的原末点
    out: list[list[Any]] = [["M", new_start[0], new_start[1]]]

    rev = list(zip(reversed(draw), reversed(chain_start)))
    for k, (seg, seg_start) in enumerate(rev):
        c = seg[0]
        target = start if closed and k == len(rev) - 1 else seg_start
        if c == "L":
            out.append(["L", target[0], target[1]])
        elif c == "Q":
            c1 = (seg[1], seg[2])
            out.append(["Q", c1[0], c1[1], target[0], target[1]])
        elif c == "C":
            c1, c2 = (seg[1], seg[2]), (seg[3], seg[4])
            # 反向：控制点顺序交换
            out.append(["C", c2[0], c2[1], c1[0], c1[1],
                        target[0], target[1]])
        elif c == "A":
            _, rx, ry, rot, large, sweep, _, _ = seg
            out.append(["A", rx, ry, rot, large, 1 - sweep,
                        target[0], target[1]])
        elif c == "Z":
            out.append(["L", target[0], target[1]])

    if closed:
        out.append(["Z"])

    return {"segments": out, "closed": closed}


def serialize_subpath(sub: dict[str, Any]) -> str:
    parts: list[str] = []
    pen: tuple[float, float] | None = None
    for seg in sub["segments"]:
        c = seg[0]
        if c == "M":
            pen = (seg[1], seg[2])
            parts.append(f"M{_fmt(pen[0])},{_fmt(pen[1])}")
        elif c == "L":
            end = (seg[1], seg[2])
            if pen is not None and end == pen and parts:
                continue  # 零长度段（反转闭合边产生），跳过
            parts.append(f"L{_fmt(end[0])},{_fmt(end[1])}")
            pen = end
        elif c == "Q":
            parts.append("Q{0},{1} {2},{3}".format(
                _fmt(seg[1]), _fmt(seg[2]), _fmt(seg[3]), _fmt(seg[4])))
            pen = (seg[3], seg[4])
        elif c == "C":
            parts.append("C{0},{1} {2},{3} {4},{5}".format(
                _fmt(seg[1]), _fmt(seg[2]), _fmt(seg[3]),
                _fmt(seg[4]), _fmt(seg[5]), _fmt(seg[6])))
            pen = (seg[5], seg[6])
        elif c == "A":
            parts.append("A{0},{1} {2} {3},{4} {5},{6}".format(
                _fmt(seg[1]), _fmt(seg[2]), _fmt(seg[3]),
                seg[4], seg[5], _fmt(seg[6]), _fmt(seg[7])))
            pen = (seg[6], seg[7])
        elif c == "Z":
            parts.append("Z")
            if parts and parts[0].startswith("M"):
                pen = sub["segments"][0][1], sub["segments"][0][2]
    return "".join(parts)


def serialize(subpaths: list[dict[str, Any]]) -> str:
    return "".join(serialize_subpath(s) for s in subpaths)


def _fmt(v: float) -> str:
    if v == int(v) and abs(v) < 1e15:
        return str(int(v))
    return f"{v:.6f}".rstrip("0").rstrip(".")


# ---------------------------------------------------------------------------
# 规范化主流程
# ---------------------------------------------------------------------------

@dataclass
class Contour:
    original_index: int
    subpath: dict[str, Any]
    points: list[tuple[float, float]]
    area: float
    bounds: tuple[float, float, float, float]
    closed: bool
    degenerate: bool
    fingerprint: str = ""
    role: str = "unknown"          # outer / hole / open / degenerate
    depth: int = -1
    parent_original_index: int | None = None
    reversed: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)


def build_contours(d: str,
                   contour_metadata: list[dict[str, Any]] | None = None
                   ) -> list[Contour]:
    """解析 ``d`` 并构造轮廓列表（细分、面积/包围盒、退化判定、指纹）。

    不做包含层级分类与方向重写。
    """
    subs = parse_path(d)
    contours: list[Contour] = []

    for idx, sub in enumerate(subs):
        pts = dedupe_ring(flatten_subpath(sub))
        area = signed_area(pts)
        bounds = bbox(pts) if pts else (0.0, 0.0, 0.0, 0.0)
        diag = math.hypot(bounds[2] - bounds[0], bounds[3] - bounds[1])
        degenerate = len(pts) < 3 or abs(area) <= 1e-10 * max(1.0, diag) ** 2
        contours.append(Contour(
            original_index=idx,
            subpath=sub,
            points=pts,
            area=area,
            bounds=bounds,
            closed=sub["closed"],
            degenerate=degenerate,
            fingerprint=contour_fingerprint(pts, sub["closed"]),
            metadata=dict(contour_metadata[idx])
            if contour_metadata and idx < len(contour_metadata) else {},
        ))

    return contours


def classify_contours(contours: list[Contour]) -> list[dict[str, Any]]:
    """按包含层级把轮廓分类为 outer / hole，其余标记 open / degenerate。

    直接就地更新 ``Contour.role / depth / parent_original_index``，
    返回层级/退化相关警告（不含 EMPTY_PATH 与 NONZERO_DIR_CONFLICT）。
    """
    warnings: list[dict[str, Any]] = []

    # ---- 层级分类 ---------------------------------------------------------
    # 仅闭合且非退化轮廓参与包含层级；按面积由大到小处理，
    # 保证父轮廓深度先于子轮廓确定。
    valid = [c for c in contours if not c.degenerate and c.closed]
    valid_sorted = sorted(valid, key=lambda c: abs(c.area), reverse=True)

    for c in valid_sorted:
        probes = _probe_points(c.points)
        best: Contour | None = None
        for cand in valid_sorted:
            if cand is c or abs(cand.area) <= abs(c.area):
                continue  # 包含者必须严格更大（面积相等的异常重叠单独警告）
            if not _bbox_contains(cand.bounds, c.bounds):
                continue
            res = [point_in_ring(p, cand.points) for p in probes]
            inside = sum(1 for r in res if r == 1)
            boundary = sum(1 for r in res if r == 0)
            if inside == 0 and boundary == 0:
                continue
            if boundary and not inside:
                warnings.append({
                    "code": "TOUCHING_PARENT_BOUNDARY",
                    "contour_original_index": c.original_index,
                    "message": f"轮廓 {c.original_index} 的采样点落在轮廓 "
                               f"{cand.original_index} 的边上，包含关系可能不严格"})
            # 选择“最紧”的包含者：bbox 面积最小，其次几何面积最小
            if (best is None
                    or _bbox_area(cand.bounds) < _bbox_area(best.bounds)
                    or (_bbox_area(cand.bounds) == _bbox_area(best.bounds)
                        and abs(cand.area) < abs(best.area))):
                best = cand
        if best is not None:
            c.parent_original_index = best.original_index
            c.depth = best.depth + 1
        else:
            c.depth = 0
        c.role = "outer" if c.depth % 2 == 0 else "hole"

    # 防止环数据异常导致的引用环（理论上不会出现）
    for c in valid:
        seen, node = set(), c
        while node.parent_original_index is not None:
            if node.original_index in seen:
                c.depth = 0
                c.parent_original_index = None
                c.role = "outer"
                warnings.append({
                    "code": "HIERARCHY_CYCLE",
                    "contour_original_index": c.original_index,
                    "message": "轮廓包含关系出现环，已按顶层外环处理"})
                break
            seen.add(node.original_index)
            node = next(v for v in valid
                        if v.original_index == node.parent_original_index)

    # ---- open / degenerate 标记与警告 -------------------------------------
    for c in contours:
        if not c.closed and not c.degenerate:
            c.role = "open"
            warnings.append({
                "code": "OPEN_CONTOUR",
                "contour_original_index": c.original_index,
                "message": "子路径未用 Z 闭合，无法可靠判定包含层级，"
                           "方向保持不变"})
        if c.degenerate:
            c.role = "degenerate"
            warnings.append({
                "code": "DEGENERATE_CONTOUR",
                "contour_original_index": c.original_index,
                "message": "轮廓面积为 0 或顶点不足，跳过方向重写"})

    return warnings


def analyze_contours(d: str, fill_rule: str,
                     contour_metadata: list[dict[str, Any]] | None = None
                     ) -> tuple[list[Contour], list[dict[str, Any]]]:
    """解析并完成轮廓层级分类（不重写方向、不序列化）。

    供规范化主流程与轮廓间安全间隙校验共用。
    返回 ``(contours, warnings)``；空路径产生一条 EMPTY_PATH 警告。
    """
    if fill_rule not in ("nonzero", "evenodd"):
        raise SVGPathError("fill_rule 必须是 'nonzero' 或 'evenodd'")

    contours = build_contours(d, contour_metadata)
    warnings = classify_contours(contours)
    if not contours:
        warnings.append({"code": "EMPTY_PATH",
                         "contour_original_index": None,
                         "message": "path 数据为空或不包含任何子路径"})
    return contours, warnings


def normalize_path(d: str, fill_rule: str,
                   contour_metadata: list[dict[str, Any]] | None = None
                   ) -> dict[str, Any]:
    """规范化入口。

    参数:
        d: 原始 SVG path 数据
        fill_rule: ``nonzero`` 或 ``evenodd``
        contour_metadata: 按原始子路径索引提供的自定义元数据（可选）

    返回:
        ``{normalized_d, fill_rule, contours, warnings}``
    """
    contours, warnings = analyze_contours(d, fill_rule, contour_metadata)

    valid = [c for c in contours if c.role in ("outer", "hole")]
    if fill_rule == "nonzero":
        for c in valid:
            if c.role == "outer" and c.area < 0:
                warnings.append({
                    "code": "NONZERO_DIR_CONFLICT",
                    "contour_original_index": c.original_index,
                    "message": "外环原始方向为逆时针（与 nonzero 约定相反），"
                               "已重写为顺时针"})
            elif c.role == "hole" and c.area > 0:
                warnings.append({
                    "code": "NONZERO_DIR_CONFLICT",
                    "contour_original_index": c.original_index,
                    "message": "孔洞原始方向为顺时针（与 nonzero 约定相反），"
                               "已重写为逆时针"})

    # ---- 方向重写 ---------------------------------------------------------
    out_subs: list[dict[str, Any]] = []
    for c in contours:
        target_positive = c.role == "outer"  # 外环顺时针 area>0，孔洞逆时针 area<0
        if c.role in ("outer", "hole"):
            needs_reverse = (c.area > 0) != target_positive
            if needs_reverse:
                out_subs.append(reverse_subpath(c.subpath))
                c.reversed = True
            else:
                out_subs.append(c.subpath)
        else:
            out_subs.append(c.subpath)  # open / degenerate：原样保留

    # 父轮廓同时给出原始索引
    contour_payload = []
    for c in contours:
        contour_payload.append({
            "original_index": c.original_index,
            "role": c.role,
            "depth": c.depth if c.depth >= 0 else None,
            "parent_original_index": c.parent_original_index,
            "closed": c.closed,
            "fingerprint": c.fingerprint,
            "signed_area": c.area,
            "bounds": {"minx": c.bounds[0], "miny": c.bounds[1],
                       "maxx": c.bounds[2], "maxy": c.bounds[3]},
            "reversed": c.reversed,
            "metadata": c.metadata,
        })

    return {
        "normalized_d": serialize(out_subs),
        "fill_rule": fill_rule,
        "orientation_convention": {
            "coordinate_system": "svg-screen-y-down",
            "outer": "clockwise (signed area > 0)",
            "hole": "counterclockwise (signed area < 0)",
        },
        "contours": contour_payload,
        "warnings": warnings,
    }


def _bbox_contains(outer: tuple[float, float, float, float],
                   inner: tuple[float, float, float, float]) -> bool:
    return (outer[0] <= inner[0] + 1e-9 and outer[1] <= inner[1] + 1e-9
            and outer[2] >= inner[2] - 1e-9 and outer[3] >= inner[3] - 1e-9)


def _bbox_area(b: tuple[float, float, float, float]) -> float:
    return (b[2] - b[0]) * (b[3] - b[1])


def _probe_points(points: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """选取若干相互远离的采样点做包含投票，降低边界/共线误判。"""
    n = len(points)
    if n <= 4:
        return list(points) or [(0.0, 0.0)]
    offsets = [0, n // 4, n // 2, (3 * n) // 4]
    return [points[o] for o in offsets]
