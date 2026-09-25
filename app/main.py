"""SVG 轮廓规范化 API。

端点：
    POST /paths                  写入 SVG path 数据及填充规则
    POST /paths/{id}/normalize   按轮廓包含层级分类外环/孔洞并重写方向
    POST /paths/{id}/metrics     只读：填充总面积与面积质心（外环/孔/岛交替计入）
    POST /paths/{id}/section-properties 只读：截面惯性矩分析（质心 Ix/Iy/Ixy、
                                 主惯性矩与较大主值主轴角）
    POST /paths/{id}/scanlines   只读：水平扫描线上的填充区间（供逐行加工）
    POST /paths/{id}/clearance   轮廓间安全间隙（最小欧氏距离）即时校验
    POST /paths/{id}/cut-plan    闭合轮廓加工计划（后代先加工 + 最近入刀点贪心）
    POST /paths/{id}/tabs        外环微桥布置（周长等分扇区 + 拐角余量/不重叠约束）
    POST /paths/{id}/intersections 只读：加工前轮廓交叉清单（自交/互交/接触/共线重叠）
    POST /paths/{id}/hit-test    只读：批量点命中测试（inside/outside/boundary）
    POST /paths/{id}/hole-clearance 只读：逐孔洞最大内接圆圆心（钻孔选点）
    POST /paths/{id}/window-coverage 只读：矩形窗口覆盖分析（逐窗净填充面积与覆盖率）
    POST /paths/compare          两条已存路径的版本差异审计（指纹匹配 + 三类变更）
    GET  /paths/{id}             查询原始路径、规范化结果及边界警告
    GET  /healthz                存活/就绪探针
"""

from __future__ import annotations

import json
import math
from contextlib import asynccontextmanager
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlite3 import Connection

from . import db
from .clearance import check_clearance
from .cutplan import compute_cut_plan
from .diff import compare_normalized
from .hittest import MAX_HIT_POINTS, hit_test_points
from .holeclearance import compute_hole_clearance
from .intersections import find_intersections
from .metrics import compute_metrics
from .scanlines import MAX_SCANLINES, compute_scanlines
from .sectionprops import compute_section_properties
from .svggeo import SVGPathError, analyze_contours, normalize_path
from .tabs import MAX_TABS, compute_tabs
from .windowcov import MAX_WINDOWS, compute_window_coverage


@asynccontextmanager
async def lifespan(_app: FastAPI):
    db.init_db()  # 自动初始化：启动即建表
    yield


app = FastAPI(
    title="SVG 轮廓规范化 API",
    version="1.11.0",
    description="写入 SVG path，按包含层级分类外环/孔洞并重写绕向，"
                "支持填充面积与面积质心分析、截面惯性矩分析、水平扫描线填充区间、"
                "轮廓间安全间隙校验、闭合轮廓加工计划、外环微桥布置、"
                "孔洞最大内接圆钻孔选点、矩形窗口覆盖分析"
                "与两条已存路径的版本差异审计。",
    lifespan=lifespan,
)


@app.exception_handler(RequestValidationError)
async def _validation_exception_handler(_request: Request,
                                        exc: RequestValidationError) -> JSONResponse:
    """422 处理器：净化 NaN/Infinity 等非合法 JSON 回显值（如 1e999）。"""
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={"detail": _json_safe(exc.errors())},
    )


def _json_safe(value: Any) -> Any:
    if isinstance(value, float) and not math.isfinite(value):
        return "non-finite"
    if isinstance(value, dict):
        return {k: _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    if isinstance(value, BaseException):
        return str(value)
    return value


def get_db() -> Connection:
    with db.get_conn() as conn:
        yield conn


# ---------------------------------------------------------------------------
# 模型
# ---------------------------------------------------------------------------

class PathIn(BaseModel):
    d: str = Field(..., description="SVG path 数据，如 'M0,0h10v10h-10Z'")
    fill_rule: str = Field("nonzero", description="nonzero 或 evenodd")
    metadata: dict[str, Any] = Field(default_factory=dict,
                                    description="路径级自定义元数据，原样保留")
    contour_metadata: list[dict[str, Any]] = Field(
        default_factory=list,
        description="按轮廓原始索引提供的自定义元数据，规范化时挂到对应轮廓")


class CompareIn(BaseModel):
    base_id: int = Field(..., gt=0, description="基线路径 id")
    candidate_id: int = Field(..., gt=0, description="候选（新版本）路径 id")


class ClearanceIn(BaseModel):
    min_distance: float = Field(..., gt=0,
                                description="允许的最小轮廓间欧氏距离（>0）；等于该值视为通过")
    include_open: bool = Field(False,
                               description="是否把未闭合的开放轮廓按折线纳入校验")

    @field_validator("min_distance")
    @classmethod
    def _finite(cls, v: float) -> float:
        if not math.isfinite(v):
            raise ValueError("min_distance 必须是有限数值")
        return v


class ScanlinesIn(BaseModel):
    spacing: float = Field(..., gt=0,
                           description="水平扫描线间距（>0）；从有效闭合轮廓整体"
                                       "包围盒最小 y 起每隔 spacing 取一条线")

    @field_validator("spacing")
    @classmethod
    def _finite(cls, v: float) -> float:
        if not math.isfinite(v):
            raise ValueError("spacing 必须是有限数值")
        return v


class CutPlanIn(BaseModel):
    x: float = Field(..., description="刀头起点 x 坐标（必须为有限数值）")
    y: float = Field(..., description="刀头起点 y 坐标（必须为有限数值）")

    @field_validator("x", "y")
    @classmethod
    def _finite(cls, v: float) -> float:
        if not math.isfinite(v):
            raise ValueError("刀头起点坐标必须是有限数值")
        return v


class TabsIn(BaseModel):
    count: int = Field(..., gt=0,
                       description="每条外环轮廓的微桥数量（正整数，"
                                   f"上限 {MAX_TABS}）")
    width: float = Field(..., gt=0,
                         description="微桥宽度（沿弧长方向，>0）")
    corner_margin: float = Field(0.0, ge=0,
                                 description="顶点两侧的拐角余量长度（≥0，"
                                             "桥段不得跨过该余量区）")

    @field_validator("count")
    @classmethod
    def _count_limit(cls, v: int) -> int:
        if v > MAX_TABS:
            raise ValueError(f"每条轮廓的微桥数量不能超过 {MAX_TABS} 个")
        return v

    @field_validator("width", "corner_margin")
    @classmethod
    def _finite(cls, v: float) -> float:
        if not math.isfinite(v):
            raise ValueError("微桥参数必须是有限数值")
        return v


class HitPoint(BaseModel):
    x: float = Field(..., description="查询点 x 坐标（必须为有限数值）")
    y: float = Field(..., description="查询点 y 坐标（必须为有限数值）")

    @field_validator("x", "y")
    @classmethod
    def _finite(cls, v: float) -> float:
        if not math.isfinite(v):
            raise ValueError("查询点坐标必须是有限数值")
        return v


class HitTestIn(BaseModel):
    points: list[HitPoint] = Field(
        ...,
        min_length=1,
        max_length=MAX_HIT_POINTS,
        description=f"待查询坐标点列表（至少 1 个，最多 {MAX_HIT_POINTS} 个，"
                    "按输入顺序返回判定）")


class HoleClearanceIn(BaseModel):
    precision: float = Field(..., gt=0,
                             description="最大内接圆半径的逼近精度（>0）；"
                                         "返回半径与真实最大半径之差不超过该值")

    @field_validator("precision")
    @classmethod
    def _finite(cls, v: float) -> float:
        if not math.isfinite(v):
            raise ValueError("precision 必须是有限数值")
        return v


class WindowIn(BaseModel):
    xmin: float = Field(..., description="窗口左边界 x（必须为有限数值）")
    ymin: float = Field(..., description="窗口上边界 y（必须为有限数值）")
    xmax: float = Field(..., description="窗口右边界 x（必须为有限数值）")
    ymax: float = Field(..., description="窗口下边界 y（必须为有限数值）")

    @field_validator("xmin", "ymin", "xmax", "ymax")
    @classmethod
    def _finite(cls, v: float) -> float:
        if not math.isfinite(v):
            raise ValueError("窗口坐标必须是有限数值")
        return v

    @model_validator(mode="after")
    def _positive_extent(self) -> "WindowIn":
        if not self.xmax > self.xmin:
            raise ValueError("窗口宽度必须为正（xmax > xmin）")
        if not self.ymax > self.ymin:
            raise ValueError("窗口高度必须为正（ymax > ymin）")
        return self


class WindowCoverageIn(BaseModel):
    windows: list[WindowIn] = Field(
        ...,
        min_length=1,
        max_length=MAX_WINDOWS,
        description=f"轴对齐窗口列表（至少 1 个，最多 {MAX_WINDOWS} 个，"
                    "按输入顺序返回逐窗结果）")


# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------

def _row_to_dict(row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "d": row["d"],
        "fill_rule": row["fill_rule"],
        "metadata": json.loads(row["metadata"] or "{}"),
        "contour_metadata": json.loads(row["contour_metadata"] or "[]"),
        "normalized": None
        if row["normalized_d"] is None
        else {
            "d": row["normalized_d"],
            **json.loads(row["normalized_info"]),
        },
        "created_at": row["created_at"],
    }


def _get_row(conn: Connection, path_id: int):
    row = conn.execute("SELECT * FROM paths WHERE id = ?", (path_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail=f"路径 id={path_id} 不存在")
    return row


# ---------------------------------------------------------------------------
# 路由
# ---------------------------------------------------------------------------

@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/paths", status_code=status.HTTP_201_CREATED)
def create_path(payload: PathIn,
                conn: Connection = Depends(get_db)) -> dict[str, Any]:
    if payload.fill_rule not in ("nonzero", "evenodd"):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                            detail="fill_rule 必须是 'nonzero' 或 'evenodd'")

    # 提前校验可解析性：坏数据不落库
    try:
        normalize_path(payload.d, payload.fill_rule)
    except SVGPathError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                            detail=f"SVG path 解析失败: {exc}")

    cur = conn.execute(
        "INSERT INTO paths (d, fill_rule, metadata, contour_metadata) "
        "VALUES (?, ?, ?, ?)",
        (payload.d, payload.fill_rule,
         json.dumps(payload.metadata, ensure_ascii=False),
         json.dumps(payload.contour_metadata, ensure_ascii=False)),
    )
    return {"id": cur.lastrowid, "d": payload.d,
            "fill_rule": payload.fill_rule,
            "metadata": payload.metadata,
            "contour_metadata": payload.contour_metadata}


def _normalize_row(row) -> dict[str, Any]:
    """对已落库行即时规范化（仅用于审计计算，不写库）。"""
    contour_meta = json.loads(row["contour_metadata"] or "[]")
    return normalize_path(
        row["d"], row["fill_rule"],
        contour_metadata=contour_meta if contour_meta else None,
    )


def _analyze_row(row):
    """对已落库行即时解析并完成轮廓层级分类（只读，不写库）。"""
    contour_meta = json.loads(row["contour_metadata"] or "[]")
    return analyze_contours(
        row["d"], row["fill_rule"],
        contour_metadata=contour_meta if contour_meta else None,
    )


@app.post("/paths/compare")
def compare_paths(payload: CompareIn,
                  conn: Connection = Depends(get_db)) -> dict[str, Any]:
    # 两路均须存在：先报告基线缺失，任一路径不存在返回 404
    base_row = conn.execute(
        "SELECT * FROM paths WHERE id = ?", (payload.base_id,)).fetchone()
    if base_row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail=f"base_id={payload.base_id} 路径不存在")
    cand_row = conn.execute(
        "SELECT * FROM paths WHERE id = ?", (payload.candidate_id,)).fetchone()
    if cand_row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"candidate_id={payload.candidate_id} 路径不存在")

    # 已存路径通常在写入时已校验，此处防御性处理损坏/历史脏数据 -> 422
    try:
        base_norm = _normalize_row(base_row)
    except SVGPathError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"base_id={payload.base_id} 路径无法规范化: {exc}")
    try:
        cand_norm = _normalize_row(cand_row)
    except SVGPathError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"candidate_id={payload.candidate_id} 路径无法规范化: {exc}")

    result = compare_normalized(base_norm, cand_norm)
    return {"base_id": payload.base_id,
            "candidate_id": payload.candidate_id,
            **result}


@app.post("/paths/{path_id}/normalize")
def normalize(path_id: int,
              conn: Connection = Depends(get_db)) -> dict[str, Any]:
    row = _get_row(conn, path_id)
    try:
        result = _normalize_row(row)
    except SVGPathError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                            detail=f"SVG path 规范化失败: {exc}")

    normalized_d = result.pop("normalized_d")
    conn.execute(
        "UPDATE paths SET normalized_d = ?, normalized_info = ?, warnings = ? "
        "WHERE id = ?",
        (normalized_d, json.dumps(result, ensure_ascii=False),
         json.dumps(result["warnings"], ensure_ascii=False), path_id),
    )

    return {
        "id": path_id,
        "d": row["d"],
        "fill_rule": row["fill_rule"],
        "normalized": {"d": normalized_d, **result},
    }


@app.get("/paths/{path_id}")
def get_path(path_id: int, conn: Connection = Depends(get_db)) -> dict[str, Any]:
    return _row_to_dict(_get_row(conn, path_id))


@app.post("/paths/{path_id}/clearance")
def clearance(path_id: int,
              payload: ClearanceIn,
              conn: Connection = Depends(get_db)) -> dict[str, Any]:
    row = _get_row(conn, path_id)

    # 即时解析/分类：不依赖已保存的规范化结果，也不写库
    try:
        contours, _warnings = _analyze_row(row)
    except SVGPathError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                            detail=f"SVG path 无法规范化: {exc}")

    result = check_clearance(contours,
                             min_distance=payload.min_distance,
                             include_open=payload.include_open)
    return {"id": path_id, **result}


@app.post("/paths/{path_id}/metrics")
def metrics(path_id: int,
            conn: Connection = Depends(get_db)) -> dict[str, Any]:
    row = _get_row(conn, path_id)

    # 即时解析/分类：不依赖已保存的规范化结果，也不写库
    try:
        contours, _warnings = _analyze_row(row)
    except SVGPathError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                            detail=f"SVG path 无法解析: {exc}")

    result = compute_metrics(contours)
    return {"id": path_id, "fill_rule": row["fill_rule"], **result}


@app.post("/paths/{path_id}/section-properties")
def section_properties(path_id: int,
                       conn: Connection = Depends(get_db)) -> dict[str, Any]:
    row = _get_row(conn, path_id)

    # 即时解析/分类：不依赖已保存的规范化结果，也不写库
    try:
        contours, _warnings = _analyze_row(row)
    except SVGPathError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                            detail=f"SVG path 无法解析: {exc}")

    result = compute_section_properties(contours)
    return {"id": path_id, "fill_rule": row["fill_rule"], **result}


@app.post("/paths/{path_id}/scanlines")
def scanlines(path_id: int,
              payload: ScanlinesIn,
              conn: Connection = Depends(get_db)) -> dict[str, Any]:
    row = _get_row(conn, path_id)

    # 即时解析/分类：不依赖已保存的规范化结果，也不写库
    try:
        contours, _warnings = _analyze_row(row)
    except SVGPathError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                            detail=f"SVG path 无法解析: {exc}")

    try:
        result = compute_scanlines(contours, spacing=payload.spacing)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                            detail=str(exc))
    return {"id": path_id, "fill_rule": row["fill_rule"], **result}


@app.post("/paths/{path_id}/cut-plan")
def cut_plan(path_id: int,
             payload: CutPlanIn,
             conn: Connection = Depends(get_db)) -> dict[str, Any]:
    row = _get_row(conn, path_id)

    # 即时解析/分类：不依赖已保存的规范化结果，也不写库
    try:
        contours, _warnings = _analyze_row(row)
    except SVGPathError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                            detail=f"SVG path 无法解析: {exc}")

    result = compute_cut_plan(contours, start_x=payload.x, start_y=payload.y)
    return {"id": path_id, "fill_rule": row["fill_rule"], **result}


@app.post("/paths/{path_id}/tabs")
def tabs(path_id: int,
         payload: TabsIn,
         conn: Connection = Depends(get_db)) -> dict[str, Any]:
    row = _get_row(conn, path_id)

    # 即时解析/分类：不依赖已保存的规范化结果，也不写库
    try:
        contours, _warnings = _analyze_row(row)
    except SVGPathError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                            detail=f"SVG path 无法解析: {exc}")

    try:
        result = compute_tabs(contours,
                              count=payload.count,
                              width=payload.width,
                              corner_margin=payload.corner_margin)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                            detail=str(exc))
    return {"id": path_id, "fill_rule": row["fill_rule"], **result}


@app.post("/paths/{path_id}/intersections")
def intersections(path_id: int,
                  conn: Connection = Depends(get_db)) -> dict[str, Any]:
    row = _get_row(conn, path_id)

    # 即时解析/细分：不依赖已保存的规范化结果，也不写库
    try:
        contours, _warnings = _analyze_row(row)
    except SVGPathError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                            detail=f"SVG path 无法解析: {exc}")

    result = find_intersections(contours)
    return {"id": path_id, "fill_rule": row["fill_rule"], **result}


@app.post("/paths/{path_id}/hit-test")
def hit_test(path_id: int,
             payload: HitTestIn,
             conn: Connection = Depends(get_db)) -> dict[str, Any]:
    row = _get_row(conn, path_id)

    # 即时解析/细分：不依赖已保存的规范化结果，也不写库
    try:
        contours, _warnings = _analyze_row(row)
    except SVGPathError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                            detail=f"SVG path 无法解析: {exc}")

    points = [(p.x, p.y) for p in payload.points]
    result = hit_test_points(contours, points=points,
                             fill_rule=row["fill_rule"])
    return {"id": path_id, "fill_rule": row["fill_rule"], **result}


@app.post("/paths/{path_id}/hole-clearance")
def hole_clearance(path_id: int,
                   payload: HoleClearanceIn,
                   conn: Connection = Depends(get_db)) -> dict[str, Any]:
    row = _get_row(conn, path_id)

    # 即时解析/分类：不依赖已保存的规范化结果，也不写库
    try:
        contours, _warnings = _analyze_row(row)
    except SVGPathError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                            detail=f"SVG path 无法解析: {exc}")

    try:
        result = compute_hole_clearance(contours, precision=payload.precision)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                            detail=str(exc))
    return {"id": path_id, "fill_rule": row["fill_rule"], **result}


@app.post("/paths/{path_id}/window-coverage")
def window_coverage(path_id: int,
                    payload: WindowCoverageIn,
                    conn: Connection = Depends(get_db)) -> dict[str, Any]:
    row = _get_row(conn, path_id)

    # 即时解析/分类：不依赖已保存的规范化结果，也不写库
    try:
        contours, _warnings = _analyze_row(row)
    except SVGPathError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                            detail=f"SVG path 无法解析: {exc}")

    windows = [(w.xmin, w.ymin, w.xmax, w.ymax) for w in payload.windows]
    result = compute_window_coverage(contours, windows=windows)
    return {"id": path_id, "fill_rule": row["fill_rule"], **result}
