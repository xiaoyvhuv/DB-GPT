---
title: 可追溯分析报告技术设计与运维指南
---

# 可追溯分析报告技术设计与运维指南

## 1. 文档目的与当前状态

本文说明 DB-GPT 可追溯数据分析报告的当前实现：从 Agent 执行 SQL、Python、原生图表和知识库检索，到生成可点击回溯的 HTML 报告、证据抽屉、证据图谱与 PDF 打印。

目标不是给一次性自然语言回答附加一串日志，而是让报告中的每个展示对象能定位到实际执行的产物。对象包括 KPI、数据表、图表容器、结论中的数值声明，以及知识库口径引用。

当前实现已覆盖：

- 统一的 `ReportDocument`、`ReportRun`、Artifact 和 evidence binding 模型；
- SQL、Python、Chart、Reference/知识库等实际产物的捕获和持久化；
- HTML 容器与数值声明的对象级 evidence binding 校验；
- 原生 DB-GPT 图表的结构化 chart artifact；
- 报告侧证据抽屉、血缘图谱、原生图表挂载和打印/PDF 导出路径；
- 对“生成 N 条建议”等明确用户要求的结果完整性校验。

当前未把 Markdown 导出作为产品能力。PDF 使用浏览器打印路径；浏览器级多图表 PDF 验收仍应持续维护。

## 2. 设计原则

1. **执行产物优先。** 报告不能凭模型文本构造不存在的证据。每个 artifact ID 必须来自本次实际执行。
2. **对象级绑定。** 不能把同一批 artifact 粗暴地绑定到整页或所有元素。一个 KPI、表、图或结论数值只绑定支持它的最小证据集。
3. **血缘可递归。** ChartArtifact 可引用其 SQLArtifact；PythonArtifact 可引用上游 SQL/文件；报告展示时递归展开。
4. **展示与事实分离。** `ReportDocument` 和 artifact 是事实与持久化模型；HTML 是兼容性展示层。HTML 不应伪造执行结果。
5. **自然语言提问不承担技术选择。** 用户只说明分析目标。系统根据需要调用 SQL、Python、图表、知识库；不要求用户在 prompt 中写“使用 SQL”或“绑定证据”。
6. **失败必须可见。** 无报告、绑定无效、图表 spec 不可用或 Agent 提前结束时，应返回明确状态，而不能显示虚假的“任务完成”。

## 3. 总体架构

```text
用户自然语言问题
        |
        v
Agentic Data Agent
  | SQL / Python / 知识库 / chart_query
  v
ArtifactCaptureContext
  |-- SQLArtifact
  |-- PythonArtifact
  |-- ChartArtifact
  `-- ReferenceArtifact
        |
        v
report_evidence_manifest
  返回本轮真实 artifact ID、类型、依赖和可绑定目标
        |
        v
html_interpreter
  校验 HTML、容器绑定、数值 claim 绑定、用户明确要求
        |
        v
ReportAssembler + ReportService
  生成并保存 ReportDocument / ReportRun / revision
        |
        +--------------------+
        |                    |
        v                    v
分析报告 HTML 视图        证据图谱视图
证据抽屉、图表挂载        claim -> artifact -> 上游依赖
        |
        v
浏览器打印/PDF
```

## 4. 核心数据模型

核心定义位于 `packages/dbgpt-core/src/dbgpt/core/interface/report.py`。

| 类型 | 作用 | 关键字段 |
| --- | --- | --- |
| `ReportDocument` | 可持久化的报告事实源 | `sections`、`artifacts`、`metadata`、版本、完整性校验 |
| `ReportRun` | 一次运行及其版本上下文 | 报告 ID、运行 ID、状态、revision、时间 |
| `SQLArtifact` | SQL 执行证据 | SQL 文本、数据源、行数/结果摘要、耗时、输入 artifact |
| `PythonArtifact` | Python 执行证据 | 代码、stdout/结果、输出文件、上游依赖、耗时 |
| `ChartArtifact` | 图表执行或图像产物 | renderer、spec、图表描述、输入 SQL/Python artifact |
| `ReferenceArtifact` | 知识库、网页或引用资料 | 来源标识、片段、位置、检索元数据 |
| `MetricBlock` | KPI/指标块 | label、value、单位、evidence artifact ID |
| `TableBlock` | 数据表块 | 列、行、证据 artifact ID |
| `ChartBlock` | 图表块 | ChartArtifact ID、标题、说明、证据 |
| `InsightBlock` | 文字洞察或建议 | 文本、claim、evidence artifact ID |
| `EvidenceReference` | 展示对象到 artifact 的连接 | artifact ID、角色、说明 |

模型完整性校验至少保证：artifact ID 唯一、引用目标存在、失败 artifact 不被伪装为成功证据、图表/计算的输入血缘可解析。

## 5. 产物捕获与血缘

### 5.1 捕获上下文

`report_capture.py` 提供 `ArtifactCaptureContext` 和内存 sink。每个 Agent turn 建立一个上下文，工具执行时写入 artifact；因此证据 ID 不依赖模型自行编造。

### 5.2 SQL

`sql_query.py` 与 `artifact_capture.py` 捕获：

- SQL 原文和参数化后的执行上下文；
- 数据源/连接器标识；
- 结果行数、有限快照或摘要；
- 成功/失败状态与耗时；
- 本 SQL 依赖的已有 artifact。

SQL 既可直接成为 KPI、表和结论的证据，也可作为 Python 或图表的上游节点。

### 5.3 Python

`code_interpreter.py` 捕获 Python 代码、标准输出、结构化结果、生成文件和图片。Python 执行使用的 SQL 结果会写入输入 artifact 关系，而不是只在提示词中留下文字描述。

Python 生成图片时，图片是 `ChartArtifact` 或派生产物；其独立渲染耗时不能被误报为 Python 执行时间。前端对这类派生图标记为“上游 Python 生成”，避免把 `0 ms` 解释成真实的浏览器绘制耗时。

### 5.4 原生 DB-GPT 图表

`chart_query.py` 是 `ChartAction` 的薄适配器。它将数据库连接器包装成 DB-GPT 原生资源，并调用 `chart_action.render` 生成 `vis-db-chart` 结构化 spec。

该路径的特点是：

- 图表基于真实、只读 SQL；
- ChartArtifact 的 `input_artifact_ids` 指向产生数据的 SQLArtifact；
- 图表类型和 spec 由原生 ChartAction 决定，用户 prompt 无需写技术实现细节；
- 服务端耗时仅代表 spec 生成。实际浏览器 canvas/DOM 绘制不在服务端耗时内。

### 5.5 知识库与引用资料

知识库检索成功后，捕获逻辑将引用转为 `ReferenceArtifact`。它可用于支持指标口径、业务解释或数据治理约束，但不能代替 SQL 结果支持具体经营数值。

以 Olist 为例，`docs/knowledge/olist-analytics-metric-governance.md` 定义商品 GMV、AOV、运费占比、准时率等口径。只有本次执行真实调用检索并引用该文档时，报告中才会出现 KB 证据；“知识库已挂载”本身不等于“本报告使用了知识库”。

## 6. Agent 与报告生成协议

入口在 `packages/dbgpt-app/src/dbgpt_app/openapi/api_v1/agentic_data_api.py`。

### 6.1 可用工具

| 工具 | 用途 | 证据结果 |
| --- | --- | --- |
| `sql_query` | 查询关系型数据库 | SQLArtifact |
| `code_interpreter` | 计算、统计或生成图片 | PythonArtifact，可产生 ChartArtifact |
| `chart_query` | 调用原生 ChartAction | SQLArtifact + ChartArtifact |
| 知识库检索工具 | 查询指标口径、业务资料 | ReferenceArtifact |
| `report_evidence_manifest` | 枚举本轮真实可绑定产物 | manifest，不新增伪造业务证据 |
| `html_interpreter` | 生成/保存 HTML 报告 | presentation binding + ReportDocument |

### 6.2 Manifest 是绑定边界

在 `html_interpreter` 之前，Agent 必须调用 `report_evidence_manifest`。manifest 返回 artifact ID、类型、状态、输入依赖、适合绑定的展示对象和可用摘要。

因此 HTML 不允许使用历史 ID、模型想象的 ID 或把所有 ID 都挂到每一个元素。验证不通过时返回 `TRACEABILITY_BINDING_REQUIRED`，Agent 应重新请求 manifest 后修正。

### 6.3 HTML 绑定约定

HTML 报告中的主要展示对象使用 `data-dbgpt-artifacts`：

```html
<section data-dbgpt-artifacts="sql-gmv-summary">
  <h2>核心经营指标</h2>
  <strong data-dbgpt-artifacts="sql-gmv-summary">R$ 13,181,027</strong>
</section>

<figure data-dbgpt-artifacts="chart-monthly-trend sql-monthly-trend">
  <!-- 原生图表或图片位置 -->
</figure>
```

要求如下：

1. KPI 卡片、表格、图表容器必须含真实 artifact ID；
2. 结论中每个数值主张使用单独的 `<strong data-dbgpt-artifacts="...">`，不能只给整段结论绑定；
3. 只绑定直接支持该对象的最少 artifact；
4. 图表可绑定 ChartArtifact 及其必要上游 SQLArtifact；
5. 只有实际被使用的 KB 引用可以绑定到口径说明或结论。

### 6.4 完整性与结束行为

`html_interpreter.py` 负责：

- 验证 HTML 是完整文档且包含可展示内容；
- 验证 artifact ID 存在、可用且对象级绑定；
- 捕获 HTML 中引用的生成图片；
- 将 presentation block 与 claim 固化到 ReportDocument；
- 当用户明确要求“给出三条经营建议”等固定数量时，检查相应的建议章节与条目数。

Agent 终止不能把未完成 todo 伪标记为完成。对于无 HTML 产物的分析，接口展示实际分析过程或失败状态，而不会虚构“分析报告”链接。

## 7. 报告组装、保存与刷新

### 7.1 组装和持久化

`ReportAssembler` 将 artifact 与 HTML presentation binding 组装为 `ReportDocument`；`dbgpt_serve/report/service.py` 负责创建、保存 revision 与读取运行记录。

一份报告保存的数据至少包含：

- 请求、数据源和运行上下文；
- 所有执行 artifact 和输入依赖；
- 章节/指标/表/图/洞察/claim 到 artifact 的 evidence binding；
- 原始 HTML 兼容预览和 presentation 元数据；
- revision、创建时间、执行状态和错误信息。

### 7.2 刷新语义

报告刷新应重放其依赖的数据源、SQL/计算和图表，并创建新的 ReportRun/revision，而不是覆盖旧产物。旧报告仍需可审计；新版本应保留与前一版本的关联。

当前数据模型和服务具备 revision/persistence 基础。端到端“自动识别受影响任务、仅重跑最小依赖集合、自动重新生成洞察”的调度策略仍需要作为后续能力继续完善；目前不应承诺任意历史报告都可无配置地一键增量刷新。

## 8. 前端实现

### 8.1 报告页面

主要组件：

- `HtmlReportEvidence.tsx`：在 iframe 中展示 HTML，并注入证据点击桥接；
- `ManusRightPanel.tsx`：分析报告、证据图谱和 PDF 导出入口；
- `TraceableReportWorkspace.tsx`：报告运行与证据图谱工作区；
- `ClaimEvidenceGraph.tsx`：claim、artifact、上游依赖图；
- `AutoChart`：DB-GPT 原生图表渲染组件。

HTML 为兼容预览时，前端会把 `vis-db-chart` artifact 挂载到明确的图表容器。不会把图表挂到数据表中，也不会为包含已有 `img`、`canvas`、`svg` 的容器重复挂载。

### 8.2 证据抽屉

点击带 SQL、PY、图、KB 标识的对象后，抽屉展示：

- artifact 名称、类型、运行 ID、状态、耗时；
- SQL 语句及结果快照；
- Python 代码、输出和生成文件；
- Chart spec/图表上游查询；
- 知识库来源和引用片段；
- “定位到执行步骤”动作。

### 8.3 证据图谱去重

图谱只把有显式 `claims` 的展示块视为 claim。相同本地化标题且引用同一 artifact 集合的 claim 合并显示，避免把标题、数值 `<strong>`、表格和结论段落重复渲染成多条“订单总量统计分析结论”。

### 8.4 PDF

`report-print.ts` 在打印前等待字体和动画帧完成，将 iframe 内的 canvas 序列化为 PNG，再克隆文档到打印窗口。这样避免 portal 挂载的图表在直接浏览器打印时丢失。

## 9. 运行配置

模型代理配置示例：

- `configs/dbgpt-proxy-deepseek.toml`；
- `configs/dbgpt-proxy-deepseek-siliconflow.toml`。

配置文件只引用环境变量，密钥必须放在本地环境文件或密钥管理系统中。任何 `*.env`、上传文件、浏览器 profile 和 `tmp/` 都不应提交到 Git。

建议为本地环境文件增加忽略规则，并在密钥曾出现在终端、截图或聊天记录后立即轮换。

## 10. 验证与回归测试

后端重点测试目录：

```text
packages/dbgpt-core/src/dbgpt/core/interface/tests/
packages/dbgpt-core/src/dbgpt/agent/expand/actions/tests/
packages/dbgpt-app/src/dbgpt_app/openapi/api_v1/tools/tests/
packages/dbgpt-app/src/dbgpt_app/openapi/api_v1/tests/
packages/dbgpt-serve/src/dbgpt_serve/report/tests/
```

当前针对报告链路的回归集覆盖 artifact 捕获、HTML 绑定、manifest、原生 chart query、报告组装、服务保存、Agent 生命周期、ReAct 解析及图表捕获。前端使用 `web/new-components/report/ReportViewRouting.test.js` 和 TypeScript 检查验证路由、绑定桥接、图谱去重、打印路径等静态契约。

建议每次改动至少执行：

```powershell
$env:PYTHONPATH='packages/dbgpt-core/src;packages/dbgpt-serve/src;packages/dbgpt-app/src'
.\.venv.make\Scripts\python.exe -m pytest -q <报告相关测试目录>
node web/new-components/report/ReportViewRouting.test.js
.\web\node_modules\.bin\tsc.cmd -p .\web\tsconfig.json --noEmit --pretty false
```

还应保留浏览器端 E2E：执行 Olist 真实分析、验证报告生成、点击 SQL/PY/Chart/KB 证据、检查图表绘制并导出 PDF。

## 11. 故障排查

| 现象 | 常见原因 | 排查和修复 |
| --- | --- | --- |
| `TRACEABILITY_BINDING_REQUIRED` | 未调用 manifest、artifact ID 不存在或把全部证据绑定到所有对象 | 重新调用 `report_evidence_manifest`，按对象替换成真实且最小的 ID 集合 |
| 图表空白或是破图 | HTML 只引用图片相对路径、图表未挂到 figure、Chart spec 不合法 | 查看 ChartArtifact 和浏览器网络请求；确认容器、图片 URL、spec 和 SQL 上游存在 |
| 图表出现“自动推荐/下载”控件 | 报告模式复用了编辑态 AutoChart | 确认 `showControls={false}` 和报告专用挂载路径 |
| 图表标记 `0 ms` | 服务端仅生成 chart spec；或图片是 Python 派生产物 | 前端应展示“服务端规格生成 < 1 ms”或“上游 Python 生成”，不要当作浏览器绘制耗时 |
| 任务显示完成但没有报告 | Agent 过早 terminate、模型 ReAct 格式异常、HTML 校验失败 | 查看执行步骤/日志；确认没有伪造 todo completion，检查最终 `html_interpreter` 响应 |
| 图谱重复节点 | 将所有 HTML 展示元素当成 claim | 只保留显式 claim，并以标题+证据集合去重 |
| PDF 没有图表 | 直接打印时 canvas/portal 不在静态 HTML 中 | 走 `printRenderedReport`，等待绘制并把 canvas 转 PNG |

## 12. 后续演进建议

1. 完成从 HTML 兼容预览到 `ReportDocument` 原生块渲染的迁移，减少 iframe 对图表的特殊挂载逻辑。
2. 补齐以 dependency graph 驱动的最小化刷新执行器，并将刷新状态、差异和失败范围呈现给用户。
3. 为 SQL/Python/Chart/KB 建立统一的执行耗时定义，分别展示服务端执行、派生产物和浏览器绘制。
4. 将 Olist E2E、图表 PDF 和知识库 evidence 纳入 CI 浏览器回归。
5. 将密钥配置迁移到标准密钥管理方案，并把本地 `.env`、上传内容和临时产物纳入默认忽略规则。
