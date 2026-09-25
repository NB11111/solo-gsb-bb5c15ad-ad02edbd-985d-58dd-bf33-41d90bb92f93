# SVG 轮廓规范化 API

基于 **FastAPI + SQLite + Docker** 的服务：接收 SVG `path` 数据与填充规则，
按轮廓之间的**包含层级**把每个子路径分类为**外环 / 孔洞（可嵌套出“岛”）**，
并重写绘制方向（外环顺时针、孔洞逆时针），同时保留轮廓层级、原始索引与自定义元数据，
对边界情况给出明确警告；另提供 **`POST /paths/{id}/metrics` 填充面积与面积质心分析**
（按外环/孔洞/岛交替计入，曲线经细分多边形近似，面积矩在局部参考坐标下稳定累加，
远离原点的大坐标不会把质心推离，只读不写库）、
**`POST /paths/{id}/section-properties` 截面惯性矩分析**
（对有效闭合环积分面积与一、二阶面积矩并按层级交替合成，绕净填充质心返回
Ix / Iy / Ixy、两项主惯性矩与较大主值对应的主轴角，只读不写库）、
**`POST /paths/{id}/scanlines` 水平扫描线填充区间**
（在整体包围盒内等间距取水平扫描线，按层级交替合并外环/孔/岛，输出逐行填充区间）、
**`POST /paths/{id}/clearance` 轮廓间安全间隙校验**
（按细分线段计算任意两轮廓边界的最小欧氏距离）、
**`POST /paths/{id}/cut-plan` 闭合轮廓加工计划**
（后代先加工、每轮贪心选取距刀头最近的细分顶点入刀，输出加工顺序与逐段空走距离）、
**`POST /paths/{id}/tabs` 外环微桥布置**
（沿细分折线建立首尾相接的弧长坐标，按周长等分扇区就近放置带拐角余量、
互不重叠的微桥，输出桥段起终点/中心坐标与弧长并报告未能放置的数量）、
**`POST /paths/{id}/intersections` 加工前轮廓交叉清单**
（只读检查细分折线的自交与子路径互交，报告交叉 / 端点接触 / 共线重叠
的类型、交点坐标及原始轮廓与边索引）、
**`POST /paths/{id}/hit-test` 批量点命中测试**
（最多 500 个有限坐标点，逐边判定 boundary 优先，其余按保存的
evenodd / nonzero 填充规则汇总为 inside / outside，开放子路径不参与）、
**`POST /paths/{id}/hole-clearance` 孔洞最大内接圆钻孔选点**
（逐有效孔洞在孔内、避开直属岛的可行区域中按距离上界优先细分网格，
在给定精度内逼近最大内接圆圆心与半径）、
**`POST /paths/{id}/window-coverage` 矩形窗口覆盖分析**
（最多 50 个轴对齐窗口，把每个有效闭合环按 Sutherland–Hodgman 裁剪到窗口后
按外环/孔洞/岛层级交替累加，逐窗返回净填充面积、窗口面积与覆盖率）
与 **`POST /paths/compare` 版本差异审计**，
按几何指纹稳定匹配两条已存路径并给出 added / removed / modified 三组差异。启动后自动建表，
可直接调用。

## 目录结构

```
.
├── app/
│   ├── __init__.py
│   ├── main.py        # FastAPI 路由、模型、启动自动初始化
│   ├── db.py          # SQLite 连接与幂等建表
│   ├── svggeo.py      # SVG path 解析 / 细分 / 包含层级 / 方向重写 / 几何指纹 / 线段距离（零三方依赖）
│   ├── metrics.py     # 填充面积与面积质心：外/孔/岛交替计入，基于细分多边形
│   ├── sectionprops.py # 截面惯性矩分析：环面积矩积分 + 平行轴合成、主惯性矩与主轴角
│   ├── scanlines.py   # 水平扫描线填充区间：半开边求交、按层级交替合并，逐行输出
│   ├── clearance.py   # 轮廓间安全间隙校验：逐段最小欧氏距离、孔-直接 parent 标记
│   ├── cutplan.py     # 闭合轮廓加工计划：后代先加工 + 最近入刀点贪心、逐段空走距离
│   ├── tabs.py        # 外环微桥布置：周长等分扇区、拐角余量/互不重叠约束、逐桥段坐标与弧长
│   ├── intersections.py # 加工前轮廓交叉清单：非相邻边自交/子路径互交、交叉/接触/共线重叠分类合并
│   ├── hittest.py      # 批量点命中测试：boundary 优先，evenodd 交叉奇偶 / nonzero 有向边绕数
│   ├── holeclearance.py # 孔洞最大内接圆：距离上界优先网格细分、直属岛避让、预算超限报错
│   ├── windowcov.py   # 矩形窗口覆盖分析：Sutherland–Hodgman 裁剪、层级交替累加、逐窗覆盖率
│   └── diff.py        # 版本差异审计：指纹+索引稳定匹配、三类 modified 分类
├── tests/             # pytest 单元 + API 端到端测试（388 个）
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
└── requirements-dev.txt
```

## 快速开始（Docker Compose，推荐）

单条命令即可启动（构建镜像、建表、健康检查全自动，无需其他配置）：

```bash
docker compose up --build
```

服务在 <http://localhost:8000>，交互式文档在 <http://localhost:8000/docs>。
容器启动时通过 FastAPI lifespan 自动在 `/data/svgapi.db` 建表（数据存放在命名卷
`svgapi-data` 中），健康探针 `GET /healthz` 通过后即可调用。

必要配置（均有默认值，通常无需修改）：

| 环境变量 | 默认值 | 说明 |
|----------|--------|------|
| `SVGAPI_DB_PATH` | `/data/svgapi.db`（容器内） | SQLite 数据库文件路径 |
| 端口映射 | `8000:8000` | 宿主机 8000 -> 容器 8000，API 地址 <http://localhost:8000> |

不用 Compose 时：

```bash
docker build -t svg-contour-normalizer .
docker run -p 8000:8000 -v "$PWD/data:/data" svg-contour-normalizer
```

本地裸机运行：

```bash
pip install -r requirements.txt
SVGAPI_DB_PATH=./svgapi.db uvicorn app.main:app --reload
```

## 方向约定

采用 SVG 屏幕坐标系（**y 轴向下**）：

| 角色 | 层级深度 | 目标方向 | 有符号面积（鞋带公式） |
|------|----------|----------|------------------------|
| 外环 `outer` | 偶数（0, 2, 4 …） | 顺时针 | `> 0` |
| 孔洞 `hole`  | 奇数（1, 3, 5 …） | 逆时针 | `< 0` |

- 包含层级通过“多点采样 + 射线法点在多边形内 + 边界检测”投票判定，
  选择**最紧的包含轮廓**作为父轮廓；支持孔中岛、岛中孔的任意偶数层嵌套。
- `nonzero` 下与约定相反的原始方向会被重写并产生 `NONZERO_DIR_CONFLICT` 警告；
  `evenodd` 渲染本身不依赖方向，服务仍统一为外顺内逆，结果对两种填充规则均正确。
- 贝塞尔（`C/S/Q/T`）与椭圆弧（`A`）在几何分析时细分为折线；
  方向重写时**保留原始命令形态**（曲线只交换控制点顺序，弧只翻转 `sweep-flag`）。

## API

### `POST /paths` — 写入 path 与填充规则

请求体：

| 字段 | 类型 | 说明 |
|------|------|------|
| `d` | string | SVG path 数据（必填） |
| `fill_rule` | `nonzero` \| `evenodd` | 默认 `nonzero` |
| `metadata` | object | 路径级自定义元数据，原样保留 |
| `contour_metadata` | object[] | 按**轮廓原始索引**给出的自定义元数据 |

无法解析的 `d` 或非法填充规则返回 `422`，坏数据不会落库。

```bash
curl -s -X POST localhost:8000/paths -H 'Content-Type: application/json' -d '{
  "d": "M0,0h40v40h-40Z M10,10h20v20h-20Z",
  "fill_rule": "nonzero",
  "metadata": {"part": "法兰", "material": "Q235"},
  "contour_metadata": [{"name": "外法兰"}, {"name": "螺栓孔"}]
}'
# -> {"id":1, "d":"...", "fill_rule":"nonzero", "metadata":{...}, "contour_metadata":[...]}
```

非法命令流（如 `Z` 之后给出零参数命令无法消费的字符）与截断的椭圆弧
（缺失 large-arc / sweep 标志或终点坐标）同样返回 `422`，响应体给出
中文解析错误，且**不会写入数据库**：

```bash
# Z 是零参数命令，其后不得隐式重复：'@' 无法被任何命令消费
curl -s -X POST localhost:8000/paths -H 'Content-Type: application/json' -d '{
  "d": "M0,0Z@"
}'
# -> 422 {"detail":"SVG path 解析失败: Z 命令后必须显式给出新命令（位置 5，得到 '@'）"}

# 椭圆弧在 large-arc-flag 处被截断
curl -s -X POST localhost:8000/paths -H 'Content-Type: application/json' -d '{
  "d": "M0,0 A1,1 0"
}'
# -> 422 {"detail":"SVG path 解析失败: 在位置 11 期望弧线标志 0/1，但路径已结束（弧线参数被截断）"}
```

### `POST /paths/{id}/normalize` — 分类并重写方向

幂等：重复调用结果一致。返回原始 `d`、规范化结果（新 `d`、每个轮廓的层级信息、
边界警告）。

```bash
curl -s -X POST localhost:8000/paths/1/normalize
```

响应中的 `normalized.contours[]`：

| 字段 | 说明 |
|------|------|
| `original_index` | 该轮廓在原始 `d` 中的子路径序号（从 0 开始） |
| `role` | `outer` / `hole` / `open` / `degenerate` |
| `depth` | 嵌套深度，0 为最外层；开放/退化轮廓为 `null` |
| `parent_original_index` | 直接父轮廓的原始索引（顶层为 `null`） |
| `closed` | 原始子路径是否以 `Z` 闭合 |
| `fingerprint` | 轮廓几何指纹（sha256），与起点、绘制方向无关，供版本匹配 |
| `signed_area` | 细分折线的有符号面积（**原始方向**，未重写前的符号） |
| `bounds` | `minx/miny/maxx/maxy` 包围盒 |
| `reversed` | 本次是否重写了方向 |
| `metadata` | 创建时按索引提交的自定义元数据 |

### `POST /paths/{id}/metrics` — 填充总面积与面积质心（只读分析）

对已存路径**即时解析**并完成轮廓包含层级分类（无需事先调用 normalize，
**不写规范化状态、不写数据库**），再按**包含层级交替**统计填充区域：

- 外环 `outer`（深度 0、2、4 …）面积 **+** 计入；
- 孔洞 `hole`（深度 1、3、5 …）面积 **−** 扣减；
- 孔中的“岛”（深度 2）重新计入，岛中孔（深度 3）继续扣减，依此类推；
- **开放轮廓**（未以 `Z` 闭合）与**退化轮廓**（面积为 0 或顶点不足）不参与。

面积在曲线（贝塞尔/椭圆弧）**细分后的多边形**上按鞋带公式近似，与
normalize / clearance 使用同一份细分折线；质心为各环带符号面积矩加权的
几何质心（SVG 屏幕坐标，y 轴向下），与轮廓书写方向无关。单环质心与
外环/孔洞/岛的净质心都在**局部参考坐标**下累加面积矩（与
`signed_area` 同一思路）：远离原点（如坐标 1e9 量级）的小尺寸路径
不会因 `x1*y2 − x2*y1` 大数相消再被 `(x1+x2)` 放大而把质心推离真实
位置，整体平移同一形状（含偏心孔组合）时质心结果与原点附近一致。
无请求体。

```bash
# 外方 0..10 含内孔 2..8：填充面积 100 - 36 = 64
curl -s -X POST localhost:8000/paths/1/metrics
```

响应：

```json
{
  "id": 1,
  "fill_rule": "nonzero",
  "area": 64.0,
  "centroid": {"x": 5.0, "y": 5.0},
  "contributing_contour_count": 2,
  "contours": [
    {"original_index": 0, "role": "outer", "depth": 0,
     "area": 100.0, "signed_contribution": 100.0,
     "centroid": {"x": 5.0, "y": 5.0}},
    {"original_index": 1, "role": "hole", "depth": 1,
     "area": 36.0, "signed_contribution": -36.0,
     "centroid": {"x": 5.0, "y": 5.0}}
  ],
  "skipped_contours": []
}
```

- `area`：填充总面积（外环/岛为正贡献、孔洞为负贡献后的净面积，恒 ≥ 0）；
- `centroid`：`{"x", "y"}` 面积质心；**没有任何有效闭合区域**（空路径、
  仅开放/退化轮廓，或净面积被孔洞完全抵消）时为 `null`，同时 `area` 为 `0.0`；
- `contours[]`：逐环明细（几何面积、对总面积的带符号贡献、单环质心），
  按 `original_index` 升序；
- `skipped_contours[]`：未参与统计的轮廓及 `reason`（`open` / `degenerate`）。

孔中岛示例（`100 − 64 + 4 = 40`，质心偏向位于 `(7,7)` 的小岛）：

```bash
curl -s -X POST localhost:8000/paths -H 'Content-Type: application/json' -d '{
  "d": "M0,0h10v10h-10Z M1,1h8v8h-8Z M6,6h2v2h-2Z"
}'
curl -s -X POST localhost:8000/paths/1/metrics
# -> {"id":1, "area":40.0, "centroid":{"x":5.2,"y":5.2}, ...}
```

路径不存在返回 `404`；已存路径无法解析（历史脏数据）返回 `422`。

远离原点示例（10×10 方形放在 `(1e9, 1e9)`：面积仍为 100，
质心精确为 `(1000000005, 1000000005)`）：

```bash
curl -s -X POST localhost:8000/paths -H 'Content-Type: application/json' -d '{
  "d": "M1000000000,1000000000h10v10h-10Z"
}'
curl -s -X POST localhost:8000/paths/1/metrics
# -> {"id":1, "area":100.0, "centroid":{"x":1000000005.0,"y":1000000005.0}, ...}
```

### `POST /paths/{id}/section-properties` — 截面惯性矩分析（只读）

对已存路径**即时解析**并完成轮廓包含层级分类（无需事先调用 normalize，
**不写规范化状态、不写数据库**），复用与 metrics / clearance 相同的曲线
细分折线，对每个**有效闭合环**（`outer` / `hole`）按格林公式在细分多边形上
积分面积与一、二阶面积矩，再按**包含层级交替**合成净填充截面：

- 外环 `outer`（深度 0、2、4 …）**+** 计入，孔洞 `hole`（深度奇数）**−** 扣减，
  孔中岛（深度 2）重新计入，岛中孔继续扣减，依此类推；
- 一阶面积矩在统一**局部参考点**下累加（与 metrics 同口径），每环先绕
  **自身质心**积分，再按**平行轴定理**搬到净填充质心合成
  （避免远离原点的大坐标相消误差），结果与轮廓书写方向无关；
- **开放轮廓**（未以 `Z` 闭合）与**退化轮廓**（面积为 0 或顶点不足）不参与，
  列入 `skipped_contours`；
- 净填充面积为零（空路径、仅开放/退化轮廓，或净面积被孔洞完全抵消）时
  `centroid`、`ix` / `iy` / `ixy` 与 `principal` 均为 `null`，`area` 为 `0.0`。

无请求体。

```bash
# 外方 0..10 含内孔 2..8：净面积 64，Ix = Iy = 10·10³/12 − 6·6³/12 = 725.333
curl -s -X POST localhost:8000/paths/1/section-properties
```

响应：

```json
{
  "id": 1,
  "fill_rule": "nonzero",
  "area": 64.0,
  "centroid": {"x": 5.0, "y": 5.0},
  "ix": 725.333333333,
  "iy": 725.333333333,
  "ixy": 0.0,
  "principal": {"max": 725.333333333, "min": 725.333333333,
                "axis_angle_deg": 0.0},
  "contributing_contour_count": 2,
  "contours": [
    {"original_index": 0, "role": "outer", "depth": 0,
     "area": 100.0, "signed_contribution": 100.0,
     "centroid": {"x": 5.0, "y": 5.0}},
    {"original_index": 1, "role": "hole", "depth": 1,
     "area": 36.0, "signed_contribution": -36.0,
     "centroid": {"x": 5.0, "y": 5.0}}
  ],
  "skipped_contours": []
}
```

- `ix` / `iy` / `ixy`：绕**净填充质心** `(cx, cy)` 的截面二次矩——
  `ix = ∫(y−cy)²dA`、`iy = ∫(x−cx)²dA`、`ixy = ∫(x−cx)(y−cy)dA`；
- `principal`：主惯性矩 `max` / `min`（惯性张量两个特征值，`max ≥ min`）
  与 `axis_angle_deg`——**较大主值**对应主轴的方向角，按 SVG 屏幕坐标
  （y 轴向下）从 +x 朝 +y 计量，取值 **[0, 180) 度**；两个主值相等
  （各向同性，如正方形、圆）时主轴不唯一，约定返回 `0°`；
- `contours[]` / `skipped_contours[]`：与 metrics 同口径的逐环明细
  （几何面积、带符号贡献、单环质心）与跳过原因（`open` / `degenerate`）；
- 路径不存在返回 `404`；已存路径无法解析（历史脏数据）返回 `422`。

偏心孔示例（外方 0..10 扣右上角 2×2 孔：净质心偏移，`ixy` 非零，
较大主值 832.0 对应主轴角 135°）：

```bash
curl -s -X POST localhost:8000/paths -H 'Content-Type: application/json' -d '{
  "d": "M0,0h10v10h-10Z M6,2h2v2h-2Z"
}'
curl -s -X POST localhost:8000/paths/1/section-properties
# -> {"area":96.0, "centroid":{"x":4.916666667,"y":5.083333333},
#     "ix":815.333333333, "iy":815.333333333, "ixy":16.666666667,
#     "principal":{"max":832.0, "min":798.666666667, "axis_angle_deg":135.0}, ...}
```

### `POST /paths/{id}/scanlines` — 水平扫描线填充区间（只读，供逐行加工）

对已存路径**即时解析**并完成轮廓包含层级分类（无需事先调用 normalize，
**不写数据库**），复用与 metrics / clearance 相同的曲线细分折线，在
**有效闭合轮廓（`outer` / `hole`）的整体包围盒**内等间距布置水平扫描线，
逐行求交并按包含层级交替合并，供逐行加工（如激光/打印路径规划）使用：

- 从整体包围盒最小 y 起，每隔 `spacing` 取一条线（`y = miny + k*spacing`），
  直到最大 y（**不含** `maxy`）；高度恰好是 `spacing` 整数倍时，
  线条数恰为 `高度/spacing`；`spacing` 大于高度时仍输出最小 y 处一条线；
- **开放轮廓**（未以 `Z` 闭合）与**退化轮廓**（面积为 0 或顶点不足）跳过；
  没有任何有效闭合轮廓（空路径、仅开放/退化轮廓）时返回空列表、`bounds` 为 `null`；
- 交点采用**半开边规则** `(y1 > y) != (y2 > y)`：水平边永不相交，扫描线
  穿过顶点时只被上行/下行两条邻边之一计数，避免顶点重复计数；
- 每个环的交点升序两两配对，再按**深度奇偶**做事件扫描合并：外环/岛
  （深度偶数）计入，孔洞（深度奇数）扣除；每行输出**升序、互不重叠**的
  `[x_start, x_end]` 填充区间；
- 单次最多 **1000** 条扫描线，超过返回 `422`（请增大 `spacing`）。

请求体：

| 字段 | 类型 | 说明 |
|------|------|------|
| `spacing` | number | 扫描线间距，必须 **> 0**（NaN/无穷/非数字返回 `422`） |

```bash
# 外方 0..10 含内孔 2..8（y=0,5 两条线）
curl -s -X POST localhost:8000/paths/1/scanlines \
  -H 'Content-Type: application/json' \
  -d '{"spacing": 5}'
```

响应：

```json
{
  "id": 1,
  "fill_rule": "nonzero",
  "spacing": 5.0,
  "bounds": {"miny": 0.0, "maxy": 10.0},
  "scanline_count": 2,
  "lines": [
    {"y": 0.0, "intervals": [[0.0, 10.0]]},
    {"y": 5.0, "intervals": [[0.0, 2.0], [8.0, 10.0]]}
  ],
  "skipped_contours": []
}
```

孔中岛在同一行被重新填回（`100 − 64 + 4` 的嵌套，`spacing=2`，
`y=6` 一行的 `6..8` 即岛内填充）：

```bash
curl -s -X POST localhost:8000/paths -H 'Content-Type: application/json' -d '{
  "d": "M0,0h10v10h-10Z M2,2h6v6h-6Z M6,6h2v2h-2Z"
}'
curl -s -X POST localhost:8000/paths/1/scanlines \
  -H 'Content-Type: application/json' -d '{"spacing": 2}'
# y=6 行 -> {"y":6.0, "intervals":[[0.0,2.0],[6.0,10.0]]}
```

- `bounds`：参与计算的有效闭合轮廓整体包围盒（仅 `miny` / `maxy`），
  无有效轮廓时为 `null`；
- `lines[]`：按 y 升序的扫描线结果，每条含 `y` 与升序、互不重叠的
  `intervals`（`[x_start, x_end]`）；扫描线与轮廓仅相切（如包围盒最小 y
  的底边切点）时该行 `intervals` 为空数组；
- `skipped_contours[]`：跳过的轮廓及 `reason`（`open` / `degenerate`）；
- 路径不存在返回 `404`，`spacing` 缺失/≤0/非有限数或扫描线超过
  1000 条返回 `422`，已存路径无法解析返回 `422`。

### `POST /paths/{id}/clearance` — 轮廓间安全间隙校验

对已存路径**即时规范化**（只读：无需事先调用 normalize，也不写库），把贝塞尔/弧
细分为折线后，逐段计算**任意两个参与校验的轮廓边界**之间的最小欧氏距离。
当某对轮廓的距离**严格小于** `min_distance` 时记为一条冲突；
**距离恰好等于阈值视为通过**（含 1e-9 相对容差以吸收细分浮点误差）。

请求体：

| 字段 | 类型 | 说明 |
|------|------|------|
| `min_distance` | number | 允许的最小间隙，必须 **> 0**（NaN/无穷/非数字返回 `422`） |
| `include_open` | boolean | 是否把未闭合的开放轮廓按折线纳入，默认 `false` |

参与/跳过规则：

- 闭合且非退化轮廓（`outer` / `hole`）始终参与；
- 开放轮廓默认跳过，`include_open=true` 时按**开放折线**（不含首尾闭合边）参与；
- 退化轮廓（面积为 0 或顶点不足，含只有两个点的“轮廓”）始终跳过；
- 同一轮廓不与自身比较。

响应中的冲突条目 `conflicts[]` **按 `(较小 original_index, 较大 original_index)`
升序排列**，多次调用结果确定；当一对轮廓中一侧是 `hole`、另一侧正是其
**直接 parent**（`parent_original_index`）时，该条目标记 `"hole_parent": true`。

```bash
curl -s -X POST localhost:8000/paths/1/clearance \
  -H 'Content-Type: application/json' \
  -d '{"min_distance": 8, "include_open": false}'
```

响应：

```json
{
  "id": 1,
  "min_distance": 8.0,
  "include_open": false,
  "checked_contour_count": 2,
  "skipped_contours": [],
  "conflict_count": 1,
  "conflicts": [
    {
      "a": {"original_index": 0, "role": "outer"},
      "b": {"original_index": 1, "role": "hole"},
      "distance": 5.0,
      "threshold": 8.0,
      "hole_parent": true
    }
  ]
}
```

- `a` / `b`：冲突两侧轮廓的 `original_index` 与 `role`（`a` 索引恒小于 `b`）；
- `distance`：两轮廓细分线段之间的最小欧氏距离，`threshold` 回显本次阈值；
- `skipped_contours[]`：未参与比较的轮廓，每项含 `original_index`、`role`
  与 `reason`（`open` 表示默认跳过的开放轮廓，`degenerate` 表示退化轮廓）；
- 间隙达标时 `conflicts` 为空数组；路径不存在返回 `404`，
  `min_distance` 缺失/≤0/非有限数返回 `422`，已存路径无法规范化返回 `422`。

含开放折线与同级孔的示例（`include_open=true` 时开放轮廓才参与，
同级孔之间的冲突 `hole_parent=false`）：

```bash
# 外方 0..20 与开放折线 M21,5 L22,6 L21,7 的最近距离为 1
curl -s -X POST localhost:8000/paths/1/clearance \
  -H 'Content-Type: application/json' \
  -d '{"min_distance": 2, "include_open": true}'
# -> conflict_count=1，b.role="open"，distance=1.0，hole_parent=false
```

### `POST /paths/{id}/cut-plan` — 闭合轮廓加工计划（只读）

对已存路径**即时解析**并完成轮廓包含层级分类（无需事先调用 normalize，
**不写数据库**），从给定刀头起点 `(x, y)` 出发，为全部**闭合且非退化**
轮廓（`outer` / `hole`）生成加工顺序与每步空走（rapid traverse）距离：

- **后代先加工**：一个轮廓必须等它的**全部后代**（任意深度的孔/岛，
  按 `parent_original_index` 传递）加工完后才可加工——先加工最深层
  轮廓，逐层向外；
- **最近入刀点贪心**：每轮在当前可加工（未加工且所有后代均已加工）的
  轮廓中，从各自**细分后的全部顶点**里找出距刀头当前位置最近的一个
  作为该轮廓的入刀点，再在这些候选里取最近者；距离相同（平方距离
  精确相等）时依次按**原始索引**、**顶点序号**决胜；
- **刀头停在入刀点**：闭合轮廓走刀一圈后回到入刀点，随后以该点为
  起点继续选择下一步，直至所有有效轮廓加工完毕；
- **开放轮廓**（未以 `Z` 闭合）与**退化轮廓**（面积为 0 或顶点不足）
  跳过并列入 `skipped_contours`；没有任何有效闭合轮廓（空路径、
  仅开放/退化轮廓）时返回空计划（`steps` 为空、总空走距离为 0）。

请求体：

| 字段 | 类型 | 说明 |
|------|------|------|
| `x` | number | 刀头起点 x 坐标，必须为有限数值（NaN/无穷/非数字返回 `422`） |
| `y` | number | 刀头起点 y 坐标，必须为有限数值（NaN/无穷/非数字返回 `422`） |

```bash
# 外方 0..100 含孔 10..90、孔中岛 40..60，刀头起点 (0,0)
curl -s -X POST localhost:8000/paths -H 'Content-Type: application/json' -d '{
  "d": "M0,0h100v100h-100Z M10,10h80v80h-80Z M40,40h20v20h-20Z"
}'
curl -s -X POST localhost:8000/paths/1/cut-plan \
  -H 'Content-Type: application/json' \
  -d '{"x": 0, "y": 0}'
```

响应：

```json
{
  "id": 1,
  "fill_rule": "nonzero",
  "start": {"x": 0.0, "y": 0.0},
  "total_contours": 3,
  "steps": [
    {"order": 1, "original_index": 2, "role": "outer", "depth": 2,
     "entry_vertex": 0, "entry_point": {"x": 40.0, "y": 40.0},
     "air_distance": 56.568542495, "head_after": {"x": 40.0, "y": 40.0}},
    {"order": 2, "original_index": 1, "role": "hole", "depth": 1,
     "entry_vertex": 0, "entry_point": {"x": 10.0, "y": 10.0},
     "air_distance": 42.426406871, "head_after": {"x": 10.0, "y": 10.0}},
    {"order": 3, "original_index": 0, "role": "outer", "depth": 0,
     "entry_vertex": 0, "entry_point": {"x": 0.0, "y": 0.0},
     "air_distance": 14.142135624, "head_after": {"x": 0.0, "y": 0.0}}
  ],
  "total_air_distance": 113.13708499,
  "skipped_contours": []
}
```

- `steps[]`：按加工顺序排列，每步含 `order`（从 1 开始）、轮廓
  `original_index` / `role` / `depth`、入刀点 `entry_point` 及其在
  细分顶点列表中的序号 `entry_vertex`、本步空走距离 `air_distance`
  （刀头当前位置到入刀点的欧氏距离）与加工后刀头位置 `head_after`
  （闭合轮廓恒等于入刀点）；
- `total_air_distance`：各步空走距离之和；
- `skipped_contours[]`：跳过的轮廓及 `reason`（`open` / `degenerate`）；
- 路径不存在返回 `404`，`x` / `y` 缺失或非有限数返回 `422`，
  已存路径无法解析返回 `422`。

### `POST /paths/{id}/tabs` — 外环微桥布置（只读）

激光切割等工艺中，轮廓在切断前需要用若干**微桥（tab / bridge）**与板材
相连。本端点对已存路径**即时解析**并完成轮廓包含层级分类（无需事先调用
normalize，**不写数据库**），仅对**闭合且非退化的外环**（`outer`，含
孔中岛等深度偶数轮廓）在曲线细分后的折线上建立**首尾相接的弧长坐标**，
按下列规则为每条轮廓放置 `count` 个宽为 `width` 的微桥：

- **周长等分扇区**：第 `k` 个扇区为弧长区间
  `[k·P/count, (k+1)·P/count]`（`P` 为周长），以扇区**中点**
  `(k+0.5)·P/count` 为桥段中心的目标位置；
- **拐角余量**：每个顶点沿前后两条边各留出长度 `corner_margin` 的
  余量区，桥段不得跨过余量区——即整个桥段必须落在**单条细分边**的
  余量区之间（桥段中心到任一顶点的弧距 ≥ `corner_margin + width/2`）；
- **互不重叠**：两个桥段的中心弧距必须 ≥ `width`（端点恰好相切允许），
  已放置桥段会遮挡后续中心；
- **选择规则**：按轮廓**原始索引升序**处理，轮廓内按**扇区顺序**逐个
  放置；在本扇区的可行中心区间（扣除拐角余量、桥半宽与既有桥段后）中，
  选择距扇区目标中点**最近**的中心，距离相同时取**较小弧长**；
- **未能放置**：扇区内不存在可行中心（边太短、余量过大或与既有桥段
  冲突）时该桥段无法放置并计入 `unplaced_count`，不影响后续扇区；
  几何放不下属于正常结果（`200`）而非参数错误；
- **跳过规则**：孔洞（`hole`）、开放轮廓（`open`）与退化轮廓
  （`degenerate`）一律跳过并列入 `skipped_contours`；没有任何外环时
  `contours` 为空、`tab_count` 为 0。

请求体：

| 字段 | 类型 | 说明 |
|------|------|------|
| `count` | integer | 每条外环请求的微桥数量，必须为**正整数**（上限 10000，超限 `422`） |
| `width` | number | 桥宽（沿弧长方向），必须 **> 0** 且为有限数 |
| `corner_margin` | number | 顶点两侧拐角余量长度，必须 **≥ 0** 且为有限数，默认 `0` |

```bash
# 外方 0..40（周长 160）内有一个孔：仅外环放置 4 个宽 4、拐角余量 5 的微桥
curl -s -X POST localhost:8000/paths -H 'Content-Type: application/json' -d '{
  "d": "M0,0h40v40h-40Z M10,10h20v20h-20Z"
}'
curl -s -X POST localhost:8000/paths/1/tabs \
  -H 'Content-Type: application/json' \
  -d '{"count": 4, "width": 4, "corner_margin": 5}'
```

响应：

```json
{
  "id": 1,
  "fill_rule": "nonzero",
  "count": 4,
  "width": 4.0,
  "corner_margin": 5.0,
  "contour_count": 1,
  "tab_count": 4,
  "unplaced_count": 0,
  "contours": [
    {
      "original_index": 0,
      "role": "outer",
      "depth": 0,
      "perimeter": 160.0,
      "requested_count": 4,
      "tab_count": 4,
      "unplaced_count": 0,
      "tabs": [
        {"sector": 0,
         "start":  {"x": 18.0, "y": 0.0, "arc": 18.0},
         "end":    {"x": 22.0, "y": 0.0, "arc": 22.0},
         "center": {"x": 20.0, "y": 0.0, "arc": 20.0}},
        {"sector": 1,
         "start":  {"x": 40.0, "y": 18.0, "arc": 58.0},
         "end":    {"x": 40.0, "y": 22.0, "arc": 62.0},
         "center": {"x": 40.0, "y": 20.0, "arc": 60.0}}
      ]
    }
  ],
  "skipped_contours": [
    {"original_index": 1, "role": "hole", "reason": "hole"}
  ]
}
```

- 每个 `tabs[]` 条目含所属 `sector`（扇区序号，从 0 开始）以及桥段的
  `start` / `end` / `center`，三者均给出 `{x, y, arc}`——坐标在曲线
  细分折线上按弧长线性插值，`arc` 为沿轮廓（从绘制起点起、沿绘制方向）
  的首尾相接弧长；`end.arc - start.arc = width`，
  `center` 位于桥段正中；
- 顶层 `tab_count` / `unplaced_count` 为全部外环的合计，逐轮廓数量见
  各 `contours[]` 条目的 `tab_count` / `unplaced_count`；
- `perimeter` 为该外环细分折线周长，`contours[]` 按
  `original_index` 升序；
- `corner_margin` 省略时默认 `0`（桥段端点可与顶点相接，但中心仍
  必须落在单条边内，桥段不会跨越顶点）；
- 路径不存在返回 `404`；`count` 缺失/非正整数/超过 10000、
  `width` 缺失/≤0/非有限数、`corner_margin` <0/非有限数返回 `422`；
  已存路径无法解析返回 `422`。

拐角余量导致扇区中点不可行的示例（`20x20` 正方形周长 80，请求 2 个
宽 4 的桥、余量 6；扇区目标弧长 20 与 60 恰为顶点，桥被钳到同一边内
距目标最近的可行端点，弧长 12 与 52）：

```bash
curl -s -X POST localhost:8000/paths -H 'Content-Type: application/json' -d '{
  "d": "M0,0h20v20h-20Z"
}'
curl -s -X POST localhost:8000/paths/1/tabs \
  -H 'Content-Type: application/json' \
  -d '{"count": 2, "width": 4, "corner_margin": 6}'
# -> 中心弧长 [12.0, 52.0]；若 width=100（任何边都放不下），
#    则 tab_count=0、unplaced_count=2
```

### `POST /paths/{id}/intersections` — 加工前轮廓交叉清单（只读）

对已存路径**即时解析**（复用与 metrics / clearance / cut-plan / tabs 相同的
曲线细分折线，无需事先调用 normalize，**不写数据库**），在**加工前**对每条
**闭合子路径**检查边相交，并汇总成稳定排序的交叉清单：

- **环内自交**：检查同一闭合环上所有**非相邻边**对；相邻边仅共享端点，
  属正常拐角连接，不报告（闭合环包含末点回首点的闭合边）；
- **环间互交**：检查不同闭合子路径之间的全部边对；
- **开放轮廓**（未以 `Z` 闭合）不参与，列入 `skipped_contours`；
  闭合但退化的子路径（面积为 0 或顶点不足）仍按字面参与边检查；
- 每条交点报告三类 `type` 之一：
  - `crossing`：两线段在**内部**真正交叉；
  - `endpoint_contact`：**端点接触**——某条边的端点落在另一条边上
    （含环内非相邻边共享端点的“收尖/捏合”、T 型接触与角点相触）；
  - `collinear_overlap`：两共线边共享一段**非零长度**区间（仅相触于
    一点归为 `endpoint_contact`）；
- `crossing` / `endpoint_contact` 给出 `point: {x, y}`；
  `collinear_overlap` 给出区间两端 `points: [{x,y},{x,y}]`（按坐标排序）；
- **同坐标、同类型**的交点合并为一条，其涉及的全部
  `(轮廓 original_index, 边索引)` 汇总到该条目的 `contours[]`
  （每项含 `role` 与升序的 `edge_indices`；边 k 指细分顶点
  `points[k] → points[(k+1) mod n]`）；结果按涉及的轮廓/边索引稳定排序。

无请求体。路径不存在返回 `404`；已存路径无法解析（历史脏数据）返回 `422`。

```bash
# 蝴蝶结形闭合子路径：边 0 与边 2 在 (5,5) 自交
curl -s -X POST localhost:8000/paths -H 'Content-Type: application/json' -d '{
  "d": "M0,0 L10,10 L10,0 L0,10 Z"
}'
curl -s -X POST localhost:8000/paths/1/intersections
```

响应：

```json
{
  "id": 1,
  "fill_rule": "nonzero",
  "checked_contour_count": 1,
  "intersection_count": 1,
  "intersections": [
    {"type": "crossing", "point": {"x": 5.0, "y": 5.0},
     "contours": [
       {"original_index": 0, "role": "degenerate", "edge_indices": [0, 2]}
     ]}
  ],
  "skipped_contours": []
}
```

共线重叠同时报告重叠区间（给两端）与两端的端点接触，例如外方
`M0,0h10v10h-10Z` 与轮廓 `M2,-10 L8,-10 L8,0 L2,0 Z` 在底边
`(2,0)→(8,0)` 共线重叠：

```json
{
  "type": "collinear_overlap",
  "points": [{"x": 2.0, "y": 0.0}, {"x": 8.0, "y": 0.0}],
  "contours": [
    {"original_index": 0, "role": "outer", "edge_indices": [0]},
    {"original_index": 1, "role": "outer", "edge_indices": [2]}
  ]
}
```

干净的轮廓（如外方 + 不接触的内孔）与空路径返回空清单
（`intersection_count` 为 `0`、`intersections` 为 `[]`）。

平行 / 共线判定使用**量纲一致的容差**：角度阈值是无量纲的
`|u×v|/(|u||v|)`（sin 夹角），共线/点落边阈值是**长度量纲**且按
**较短边**长度缩放。因此同一构型经**整体平移或等比放大**后，
`crossing` / `endpoint_contact` / `collinear_overlap` 的类型与涉及边索引
保持一致——例如长约 10⁹ 的水平边与短竖边在内部正交相交时，不会再因
尺度被误判为“平行”而漏报、放行相交路径；近乎平行但真正分离的边
（垂直间距大于相对容差 × 短边）仍返回空清单，不会误报。

```bash
# 两条闭合轮廓：长 1e9 的水平边被短竖边在内部正交穿过
curl -s -X POST localhost:8000/paths -H 'Content-Type: application/json' -d '{
  "d": "M-500000000,0 L500000000,0 L500000000,2 L-500000000,2 Z M0,-2 L1,-2 L1,2 L0,2 Z"
}'
curl -s -X POST localhost:8000/paths/1/intersections
# -> intersection_count=5；短轮廓底/左边与大矩形底边在 (0,0)/(1,0)
#    的内部交叉均为 "crossing"（不被尺度吞掉），端点压边仍为 endpoint_contact
```

### `POST /paths/{id}/hit-test` — 批量点命中测试（只读）

对已存路径的**原始 `d` 即时解析**（复用与 metrics / clearance / intersections
相同的曲线细分折线，无需事先调用 normalize，**不写规范化状态、不写数据库**），
对一批坐标点逐点给出 `inside` / `outside` / `boundary` 判定：

- **boundary 优先**：点落在任一**闭合子路径**细分折线的**任一条边**
  （含末点回首点的闭合边与边的端点）上即判 `boundary`，不再参与填充汇总；
  边界容差按环尺寸自适应（与规范化包含判定同口径）；
- 其余点按**保存的 `fill_rule`** 对全部闭合子路径汇总：
  - `evenodd`：右向水平射线**交叉次数为奇数** → `inside`，偶数 → `outside`；
  - `nonzero`：**有向边绕数非零** → `inside`，为零 → `outside`
    （同向重叠轮廓绕数累加、反向重叠相互抵消）；
- **开放子路径**（未以 `Z` 闭合）不参与——点落在开放折线上也不是
  `boundary`，列入 `skipped_contours`；**闭合但净面积为 0** 的自交路径
  （如蝴蝶结）仍按字面参与，其自交分区仍可被填充；
- 交叉/绕数计数采用与 scanlines 相同的**半开边规则**
  `(y1 > y) != (y2 > y)`：水平边永不计数，穿过顶点时只被上行/下行
  两条邻边之一计数；
- 结果**按输入顺序**返回；单次最多 **500** 个点，每个坐标必须是有限数
  （NaN/无穷/非数字、空点列表或超过 500 个点返回 `422`）。

请求体：

| 字段 | 类型 | 说明 |
|------|------|------|
| `points` | `{x: number, y: number}[]` | 查询点列表，**1 ~ 500** 个有限坐标点，必填 |

```bash
# 先写入：外方 0..10（顺时针）+ 内孔 2..8（逆时针，nonzero 下绕数抵消）
curl -s -X POST localhost:8000/paths -H 'Content-Type: application/json' -d '{
  "d": "M0,0h10v10h-10Z M2,8h6v-6h-6Z", "fill_rule": "nonzero"
}'
# 点依次为：外框内 / 孔内 / 孔边上 / 外框顶点
curl -s -X POST localhost:8000/paths/1/hit-test \
  -H 'Content-Type: application/json' \
  -d '{"points": [{"x":1,"y":1}, {"x":5,"y":5}, {"x":5,"y":2}, {"x":0,"y":0}]}'
```

响应：

```json
{
  "id": 1,
  "fill_rule": "nonzero",
  "point_count": 4,
  "checked_contour_count": 2,
  "results": [
    {"index": 0, "point": {"x": 1.0, "y": 1.0}, "result": "inside"},
    {"index": 1, "point": {"x": 5.0, "y": 5.0}, "result": "outside"},
    {"index": 2, "point": {"x": 5.0, "y": 2.0}, "result": "boundary"},
    {"index": 3, "point": {"x": 0.0, "y": 0.0}, "result": "boundary"}
  ],
  "skipped_contours": []
}
```

- `results[]`：与输入等长、同序，每项含 `index`（从 0 开始）、
  回显坐标 `point` 与 `result`（`inside` / `outside` / `boundary`）；
- `checked_contour_count`：参与判定的闭合子路径数（含闭合退化/零面积
  自交路径）；`skipped_contours[]` 为未参与的非闭合子路径，每项含
  `original_index`、`role` 与 `reason`（`open` / `degenerate`）；
- 注意命中判定基于**原始子路径方向**：同向绘制的内方在 `nonzero` 下
  会被填充（绕数累加），只有反向轮廓才作为孔抵消；`evenodd` 不依赖方向；
- 路径不存在返回 `404`；`points` 缺失/为空/超过 500 个、坐标缺失/非数字/
  非有限数返回 `422`；已存路径无法解析返回 `422`。

### `POST /paths/{id}/hole-clearance` — 孔洞最大内接圆（钻孔选点，只读）

对已存路径**即时解析**并完成轮廓包含层级分类（复用与 metrics / clearance /
scanlines 相同的曲线细分折线，无需事先调用 normalize，**不写数据库**），
为每个**有效孔洞**（`role == "hole"`，闭合且非退化）寻找孔内最大内接圆的
圆心，供钻孔选点：

- **可行区域**：候选圆心必须严格位于孔洞内部，且不在任何**直属岛**
  （`parent_original_index` 指向该孔洞的深度偶数 `outer` 岛）内部；
- **半径定义**：候选点的可用半径为到**孔洞边界**与**各直属岛边界**的
  最短欧氏距离（在曲线细分折线上逐边计算）；
- **搜索方式**：不取包围盒中心（凹孔或偏心岛会使其失效），而是在孔洞
  包围盒上按**距离上界优先**细分网格（polylabel 式）——优先队列按
  `格心最小边界距离 + 半格对角线` 的上界出队，上界仍可能把当前最优半径
  提升超过 `precision` 时四分子格，整格落在孔外/岛内则剪枝；
- **收敛保证**：返回半径 `r` 满足 `r* - precision <= r <= r*`（`r*` 为
  细分折线上的真实最大半径）；
- **预算上限**：单次请求全部孔洞合计最多评估 **20000** 个网格单元，
  预算耗尽仍未达到 `precision` 时返回 `422`（请增大 `precision`）；
- **跳过规则**：外环（`outer`）、开放轮廓（`open`）与退化轮廓
  （`degenerate`）一律跳过并列入 `skipped_contours`；没有任何有效孔洞时
  `holes` 为空数组、`cells_used` 为 0。

请求体：

| 字段 | 类型 | 说明 |
|------|------|------|
| `precision` | number | 最大半径的逼近精度，必须 **> 0** 且为有限数（NaN/无穷/非数字返回 `422`） |

```bash
# 外方 0..20 含孔 2..18，孔内有偏心岛 3..9：圆心被推到对角远端
curl -s -X POST localhost:8000/paths -H 'Content-Type: application/json' -d '{
  "d": "M0,0h20v20h-20Z M2,2h16v16h-16Z M3,3h6v6h-6Z"
}'
curl -s -X POST localhost:8000/paths/1/hole-clearance \
  -H 'Content-Type: application/json' \
  -d '{"precision": 0.01}'
```

响应（服务地址 <http://localhost:8000>）：

```json
{
  "id": 1,
  "fill_rule": "nonzero",
  "precision": 0.01,
  "hole_count": 1,
  "cells_used": 125,
  "holes": [
    {"original_index": 1, "role": "hole", "depth": 1,
     "center": {"x": 12.7265625, "y": 12.7265625},
     "radius": 5.270155229}
  ],
  "skipped_contours": [
    {"original_index": 0, "role": "outer", "reason": "outer"},
    {"original_index": 2, "role": "outer", "reason": "outer"}
  ]
}
```

- `holes[]`：按 `original_index` 升序，每项含 `center: {x, y}`（可行圆心）
  与 `radius`（到孔洞/岛边界的最短距离）；上例理论最优半径
  `18-(18+9√2)/(1+√2) ≈ 5.2721`，误差在 `precision=0.01` 之内；
- `cells_used`：本次请求实际评估的网格单元数（全部孔洞合计，上限 20000）；
- `skipped_contours[]`：未参与的轮廓及 `reason`
  （`outer` / `open` / `degenerate`）；
- 路径不存在返回 `404`；`precision` 缺失/≤0/非有限数、网格预算耗尽未收敛、
  已存路径无法解析（历史脏数据）均返回 `422`。

### `POST /paths/{id}/window-coverage` — 矩形窗口覆盖分析（只读）

对已存路径**即时解析**并完成轮廓包含层级分类（复用与 metrics / clearance /
scanlines 相同的曲线细分折线，无需事先调用 normalize，**不写数据库**），
把每个**有效闭合环**（`outer` / `hole`）裁剪到轴对齐矩形窗口内求面积，
再按**包含层级交替**累加，逐窗返回净填充面积、窗口面积与覆盖率：

- 外环（`outer`，深度偶数）裁剪面积 **+** 计入，孔洞（`hole`，深度奇数）
  **−** 扣减，孔中岛（深度 2）重新计入，岛中孔继续扣减，依此类推；
- 裁剪采用 **Sutherland–Hodgman 多边形裁剪**：环被窗口四条边所在的半平面
  依次截断，因此**穿越窗口但所有顶点都在窗外的环**（如完全包住窗口的大环、
  边斜穿窗口的环）同样被正确计入；完全在窗外的环裁剪为空，贡献 0；
- **开放轮廓**（未以 `Z` 闭合）与**退化轮廓**（面积为 0 或顶点不足）
  不参与，列入 `skipped_contours`；空路径或仅开放/退化轮廓时逐窗返回
  `covered_area = 0.0`、`coverage = 0.0`；
- 净填充面积在数值误差内钳制到 `[0, 窗口面积]`，`coverage` 恒在 `[0, 1]`；
- 环面积（含裁剪后面积）在**局部参考点**下按鞋带公式稳定求和：远离原点
  （如坐标 1e9 量级）的小尺寸路径不会因大数相消把面积误判为 0，
  整体平移同一形状与窗口不改变覆盖面积与覆盖率；
- 单次最多 **50** 个窗口，按输入顺序返回逐窗结果。

请求体：

| 字段 | 类型 | 说明 |
|------|------|------|
| `windows` | `{xmin, ymin, xmax, ymax}[]` | 轴对齐窗口列表，**1 ~ 50** 个；坐标必须为有限数，且宽（`xmax - xmin`）与高（`ymax - ymin`）均为正，否则 `422` |

```bash
# 外方 0..10 含内孔 2..8：整窗 100-36=64；5..15 窗口 25-9=16
curl -s -X POST localhost:8000/paths -H 'Content-Type: application/json' -d '{
  "d": "M0,0h10v10h-10Z M2,2h6v6h-6Z"
}'
curl -s -X POST localhost:8000/paths/1/window-coverage \
  -H 'Content-Type: application/json' \
  -d '{"windows": [
        {"xmin": 0, "ymin": 0, "xmax": 10, "ymax": 10},
        {"xmin": 5, "ymin": 5, "xmax": 15, "ymax": 15}]}'
```

响应：

```json
{
  "id": 1,
  "fill_rule": "nonzero",
  "window_count": 2,
  "contributing_contour_count": 2,
  "windows": [
    {"index": 0,
     "window": {"xmin": 0.0, "ymin": 0.0, "xmax": 10.0, "ymax": 10.0},
     "window_area": 100.0, "covered_area": 64.0, "coverage": 0.64},
    {"index": 1,
     "window": {"xmin": 5.0, "ymin": 5.0, "xmax": 15.0, "ymax": 15.0},
     "window_area": 100.0, "covered_area": 16.0, "coverage": 0.16}
  ],
  "skipped_contours": []
}
```

- `windows[]`：与输入等长、同序，每项含 `index`（从 0 开始）、回显窗口
  `window`、`window_area`（窗口面积）、`covered_area`（窗口内净填充面积）
  与 `coverage`（`covered_area / window_area`）；
- `contributing_contour_count`：参与统计的有效闭合轮廓数；
- `skipped_contours[]`：未参与的轮廓及 `reason`（`open` / `degenerate`）；
- 路径不存在返回 `404`；`windows` 缺失/为空/超过 50 个、坐标缺失/非数字/
  非有限数、窗口宽或高非正返回 `422`；已存路径无法解析返回 `422`。

顶点全部在窗外但穿越窗口的轮廓同样计入——大环 `-10..30` 完全包住窗口
`0..10` 时覆盖率为 1：

```bash
curl -s -X POST localhost:8000/paths -H 'Content-Type: application/json' -d '{
  "d": "M-10,-10h40v40h-40Z"
}'
curl -s -X POST localhost:8000/paths/1/window-coverage \
  -H 'Content-Type: application/json' \
  -d '{"windows": [{"xmin": 0, "ymin": 0, "xmax": 10, "ymax": 10}]}'
# -> {"covered_area": 100.0, "coverage": 1.0, ...}
```

### `GET /paths/{id}` — 查询原始路径、规范化结果与警告

未规范化时 `normalized` 为 `null`；已规范化时包含 `d`、`contours`、
`warnings` 与方向约定说明。`id` 不存在返回 `404`。

```bash
curl -s localhost:8000/paths/1
```

### `POST /paths/compare` — 两条已存路径的版本差异审计

对 `base_id`（基线）与 `candidate_id`（新版本）两条**已存**路径分别即时规范化
（审计为只读操作：无需事先调用 normalize，也不会写库），再按**轮廓几何指纹**建立
稳定匹配：

- 指纹对**绘制起点与顺/逆时针不敏感**（坐标量化到 1e-6 后做循环旋转 + 反向归一），
  且含闭合标志与绝对坐标，因此同一几何被**重排子路径顺序**、仅改变方向时仍被识别为
  同一轮廓——**相同指纹但 `original_index` 变化不算差异**；
- 同指纹轮廓先按相同 `original_index` 配对，剩余项（如多个全等重复轮廓）按索引就近
  稳定一对一配对；
- 指纹匹配不上的轮廓退而按相同 `original_index` 配对（作为几何变化），
  再配不上的计入 `added` / `removed`。

`modified` 的 `change_type`（优先级从高到低）：

| change_type | 含义 |
|-------------|------|
| `geometry` | 几何指纹不同（形状/坐标变化；开放折线闭合等也归此类） |
| `hierarchy` | 几何相同，但 `role`（outer/hole/…）或 `depth` 发生变化 |
| `orientation` | 几何与层级均相同，仅原始绕向相反（有符号面积符号翻转） |

任一路径不存在返回 `404`；参数非法（id 缺失/非正整数）或已存路径无法规范化
返回 `422`。

```bash
curl -s -X POST localhost:8000/paths/compare \
  -H 'Content-Type: application/json' \
  -d '{"base_id": 1, "candidate_id": 2}'
```

响应：

```json
{
  "base_id": 1,
  "candidate_id": 2,
  "added": [
    {
      "candidate_original_index": 2,
      "candidate_role": "hole",
      "candidate_depth": 1,
      "candidate_fingerprint": "a5d4b697…",
      "candidate_metadata": {"name": "新增螺栓孔"}
    }
  ],
  "removed": [
    {
      "base_original_index": 1,
      "base_role": "hole",
      "base_depth": 1,
      "base_fingerprint": "…",
      "base_metadata": {}
    }
  ],
  "modified": [
    {
      "change_type": "orientation",
      "matched_by": "fingerprint",
      "fingerprint": {"base": "d39a2033…", "candidate": "d39a2033…"},
      "base": {
        "original_index": 1, "role": "hole", "depth": 1,
        "parent_original_index": 0, "closed": true,
        "reversed": true, "metadata": {"name": "螺栓孔"}
      },
      "candidate": {
        "original_index": 1, "role": "hole", "depth": 1,
        "parent_original_index": 0, "closed": true,
        "reversed": false, "metadata": {"name": "螺栓孔"}
      }
    }
  ],
  "summary": {
    "base_contour_count": 2,
    "candidate_contour_count": 3,
    "added": 1,
    "removed": 1,
    "modified": 1,
    "unchanged": 1
  }
}
```

- `matched_by` 标识配对依据：`fingerprint`（同指纹，含索引变化的同一轮廓）或
  `index`（指纹不同、仅原始索引相同的几何变化）；
- `modified` 两侧均携带 `original_index`、`role`、`depth`、
  `parent_original_index`、`closed`、`reversed` 与 `metadata`；
- 仅 `metadata` 不同（几何/层级/方向均未变）不计为 `modified`，元数据仍可在配对
  快照中查看；
- `added` / `removed` 条目按对应侧 `original_index` 升序，字段以
  `candidate_` / `base_` 前缀扁平化给出。

典型调用流程（创建两个版本后审计）：

```bash
curl -s -X POST localhost:8000/paths -H 'Content-Type: application/json' -d '{
  "d": "M0,0h40v40h-40Z M10,10h20v20h-20Z"
}'                                   # -> {"id":1, ...}

curl -s -X POST localhost:8000/paths -H 'Content-Type: application/json' -d '{
  "d": "M0,0h40v40h-40Z M12,12h16v16h-16Z M30,10h5v5h-5Z"
}'                                   # -> {"id":2, ...}

curl -s -X POST localhost:8000/paths/compare \
  -H 'Content-Type: application/json' \
  -d '{"base_id": 1, "candidate_id": 2}'
# -> added: 1（新小孔）; modified: 1（原孔 geometry，matched_by=index）
```

### `GET /healthz` — 健康探针

返回 `{"status":"ok"}`，同时用于 Docker HEALTHCHECK。

## 警告码（`normalized.warnings[]`）

| code | 含义 |
|------|------|
| `EMPTY_PATH` | `d` 为空或没有子路径 |
| `OPEN_CONTOUR` | 子路径未以 `Z` 闭合，不参与层级判定，方向原样保留 |
| `DEGENERATE_CONTOUR` | 面积为 0 或顶点不足，跳过方向重写 |
| `NONZERO_DIR_CONFLICT` | `nonzero` 下原方向与外顺内逆约定相反，已重写 |
| `TOUCHING_PARENT_BOUNDARY` | 采样点落在候选父轮廓边上，包含关系可能不严格 |

每条警告带 `contour_original_index`（全局警告为 `null`）与中文 `message`。

## 支持的 path 语法

`M/m L/l H/h V/v C/c S/s Q/q T/t A/a Z/z`，包括：相对/绝对坐标、
隐式命令重复（多组坐标对；`Z` 为零参数命令，**不允许隐式重复**，其后必须
显式给出新命令，否则 422）、`Z` 之后的显式 moveto / lineto、标志位与数字紧贴
（如 `A5,5 0 00 10,0`）或逗号分隔（`0,1,1`）。弧线半径自动修正遵循
SVG 规范的端点参数化算法；在标志位或坐标处被截断的非法弧线返回 `SVGPathError`
（API 层为 `422`），不会产生未定义行为或 500。

## 测试

```bash
pip install -r requirements-dev.txt
pytest -q          # 388 passed
```

覆盖：解析器边界（非法命令流、`Z` 后零参数命令不得隐式重复的死循环回归、
截断椭圆弧的 large-arc / sweep 标志与终点坐标缺失返回 `SVGPathError` 而非
`IndexError`、合法弧线标志紧贴/逗号分隔、隐式重复坐标与多子路径不受影响）、
面积符号、外/孔/岛三层嵌套、同级双孔、曲线与弧反转几何等价性、
开放与退化轮廓、元数据保留、完整 API 生命周期、404/422、非法命令流与截断
弧线 422 且不落库（含死循环超时保护）、重复规范化幂等；
几何指纹的起点/方向不变性、闭合区分、量化噪声吸收；版本审计的仅方向变化、
层级变化、几何变化、子路径重排（同指纹索引变化）、全等重复轮廓就近配对、
added/removed、元数据透传、未规范化即时审计与脏数据 422；
填充面积/质心的单环、外-孔、外-孔-岛、四层交替、书写方向无关、
两种填充规则一致、曲线细分近似、开放与退化排除、空路径返回面积 0 与质心 null、
远离原点（1e9）单环质心精确回归、偏心孔组合与外-孔-岛整体平移 1e9 后
净质心与原点附近一致、只读不落库、404/脏数据 422；
截面惯性矩的标准矩形公式、直角三角形质心矩、外-孔与外-孔-岛层级交替、
偏心孔平行轴合成与非零惯性积、旋转矩形主值不变与主轴角随动（模 180 扫描）、
45° 细杆主轴垂直、圆细分各向同性取 0°、书写方向无关、两种填充规则一致、
开放与退化跳过、空路径与净面积抵消全 null、远离原点坐标无相消误差、
偏心孔组合平移 1e9 后净质心随动且惯性矩逐位一致、
无 -0.0、确定性与只读不落库、404/非法 id/脏数据 422；
扫描线填充区间的单环/外-孔/孔-岛三层交替、间距起止与最大 y 不含、
半开边顶点不重复计数、相切行空区间、离散外环区间升序不重叠、曲线细分、
开放与退化跳过、无有效轮廓空列表、两种填充规则一致、恰好 1000 条放行与
超限 422、非法 spacing 422、确定性与只读不落库、404/脏数据 422；
安全间隙的点/线段距离原语（相交、零长度段）、孔-直接 parent 标记、
同级孔与隔层 island 不标记、**距离等于阈值通过**、开放轮廓默认跳过与
`include_open` 纳入、退化轮廓始终跳过、曲线细分、包围盒剔除、结果确定性与
只读不落库、404/非法参数 422 等；
闭合轮廓加工计划的后代先加工（外-孔、外-孔-岛三层、同级孔全部先于父轮廓）、
每轮最近细分顶点入刀与刀头停在入刀点的链式空走距离、距离相等时按原始索引、
顶点序号决胜、曲线细分顶点入刀、开放与退化跳过、无有效轮廓空计划、
两种填充规则一致、确定性与只读不落库、404/非有限起点 422 等；
外环微桥布置的周长等分扇区中点目标、桥段起终点/中心坐标与弧长、
拐角余量使扇区中点不可行时钳到最近可行端点、等距取较小弧长、
桥段不跨顶点余量区且互不重叠（相切允许、超限拥挤时报告未放置数）、
边太短/余量过大/桥宽超过周长整轮廓无法放置、仅闭合非退化 outer 参与
（跳过 hole/open/degenerate、孔中岛深度 2 仍处理）、曲线细分折线、
默认余量 0、确定性与两种填充规则一致、只读不落库、404/非法参数 422 等；
加工前轮廓交叉清单的简单/凹/嵌套无交叉、蝴蝶结非相邻边自交、相邻边共享
端点不报、非相邻边共享顶点与顶点压边（收尖/T 型）端点接触、环内共线重叠
（区间两端与重叠并存）、不同子路径交叉/角点接触/T 接触/共线重叠、
三轮廓同点同类型合并边索引、开放轮廓跳过且不与闭合轮廓比较、闭合退化轮廓
仍参与、单点闭合、贝塞尔/弧细分后求交、按轮廓与边索引稳定排序、跨调用
确定性、两种填充规则一致、空路径空清单、
**量纲一致容差的尺度不变性**（1e9 长边×短竖边内部正交相交不被误判平行
而漏报，同一非退化构型整体平移/等比放大 1e-3…1e9 后 crossing/
endpoint_contact/collinear_overlap 的类型与涉及边索引一致，大坐标
非有理数捏合顶点按相对量化仍合并、近乎平行但分离的边不误报）、
只读不落库、404/非法 id/脏数据 422 等；
批量点命中测试的单环 inside/outside、各边与闭合边 boundary 优先、对角线/
斜边命中、外-孔在两种填充规则下一致、同向重叠 nonzero 填充而 evenodd
留空、反向重叠绕数抵消、三重重叠奇偶、蝴蝶结等零净面积闭合自交路径仍
参与（叶内 inside、叶间 outside、自交点 boundary）、闭合退化线段/点只
贡献 boundary、开放子路径（含两点退化开放线）不参与且落在其上也不是
boundary、空路径全 outside、曲线细分顶点命中、凹多边形半开边顶点规则、
按输入顺序回显索引与坐标、两种填充规则差异、最多 500 点边界（恰好 500
放行、501 拒绝）、空列表/缺字段/非数字/NaN/Infinity/1e999 返回 422、
确定性与只读不落库、404/非法 id/脏数据 422 等；
孔洞最大内接圆的方孔精确圆心半径、长条孔短边一半半径、L 形凹孔不退化为
包围盒中心、粗细两种精度下半径误差均在 precision 内、圆弧孔细分折线
apothem、偏心岛把圆心推离且半径等于到孔/岛边界最短距离（独立复核）、
孔中岛与岛中孔各自求解、同级双孔按原始索引升序、无有效孔洞返回空数组、
开放与退化子路径跳过、两种填充规则一致、跨调用确定性、非法 precision
422、细长孔极小精度触发 20000 网格预算 422、只读不落库、404/非法 id/
脏数据 422 等；
矩形窗口覆盖分析的窗内/窗外/部分重叠、外-孔-岛层级交替、孔被窗口部分
裁剪、包住窗口或边斜穿窗口且顶点均在窗外的轮廓计入、窗口与轮廓仅相切
为零、多窗口按输入顺序回显、开放与退化跳过、空路径零覆盖、曲线细分
近似、全等重叠外环钳制到窗口面积、负坐标窗口、远离原点（1e9）小尺寸
路径的窗内全覆盖回归与微小环不误判退化、大坐标下外-孔-岛交替、
形状与窗口整体平移结果不变、两种填充规则一致、
书写方向无关、无 -0.0、恰好 50 个窗口放行与 51 个 422、缺字段/空列表/
宽高非正/非数字/NaN/Infinity/1e999 返回 422、确定性与只读不落库、
404/非法 id/脏数据 422 等。

## 数据存储

SQLite 单表 `paths`（WAL 模式）保存原始 `d`、填充规则、两类元数据、
规范化 `d`、规范化详情与警告 JSON。默认路径由环境变量 `SVGAPI_DB_PATH`
控制（容器内 `/data/svgapi.db`）。
