# DB-GPT 原生可追溯数据库报告迁移设计

## 目标

将“分析报告”从模型拼接的 `html_interpreter` iframe 页面，迁移为 DB-GPT 原生
`ReportDocument` 驱动的 React 报告工作区。报告中的指标、表格、图表和数值结论
必须由真实执行 artifact 支持，并可打开对应证据抽屉。

用户的自然语言请求不承担图表实现选择的职责；系统不得通过入口提示词要求
“必须使用 native_chart”或要求 HTML 中写入图表占位符。

## 已确认的 DB-GPT 原生能力与边界

1. `ChartAction` 执行 SQL 后通过 `VisChart` 输出 `vis-db-chart` 协议；前端已有
   `VisChart -> ChartView -> AutoChart` 渲染路径。
2. `html_interpreter` 仅负责渲染原始 HTML 或 skill 内置 HTML/ECharts 模板，不能在
   iframe 中解析或挂载 `vis-db-chart`。
3. `ReportDocument` 已有 `MetricBlock`、`TableBlock`、`ChartBlock` 和 evidence
   schema，但当前报告页只把 `ChartBlock` 显示为占位说明，服务端 HTML preview 也只
   显示 chart artifact ID。
4. `ReportGenerationAgent` 输出 Markdown 叙述，不是通用 HTML 图表报告模板。

## 目标架构

```text
数据库分析执行
  -> 原生 ChartAction（SQL + vis-db-chart spec）
  -> SQLArtifact / ChartArtifact（ChartArtifact.input_artifact_ids 指向 SQL）
  -> ReportAssembler（MetricBlock / TableBlock / ChartBlock / InsightBlock）
  -> ReportDocument 持久化
  -> 分析报告 React 工作区
       -> MetricBlock：指标卡 + evidence drawer
       -> TableBlock：数据表 + evidence drawer
       -> ChartBlock：ChartView / AutoChart + evidence drawer
       -> InsightBlock：claim 级数值证据
```

`ReportDocument` 是展示和可追溯的唯一事实源；HTML 仅可作为导出/兼容预览，不能再
承担图表运行时或绑定协议。

## 后端改动

1. 扩展原生 `ChartAction` 的 chart artifact 捕获：从 `vis-db-chart` view 中解析并
   保存结构化 spec（`renderer`、`type`、`title`、`describe`、`data`、`sql`），而不是
   仅保存 `display_type` 与一段协议文本。
2. 在正常数据库分析流程接入 `ChartAction`，取代新增的平行 `native_chart` 工具；图表
   类型是执行 action 的结构化参数，不是用户提示词中的硬约束。
3. `ReportAssembler` 在报告生成时把成功的图表 artifact 建为 `ChartBlock`，并递归带入
   SQL/Python/Reference 上游证据。指标、表格和结论仍只绑定其实际支持 artifact。
4. 删除 agentic 数据入口中关于“必须 native_chart”和 HTML 图表 placeholder 的提示词；
   删除临时 `native_chart` 工具及其专用测试。保留 HTML 完整性和对象级证据验证，仅用
   于明确选择 HTML/skill 模板的交付。

## 前端改动

1. `分析报告` 主视图改用 `TraceableReportWorkspace`，不再用 `HtmlReportEvidence` 作为
   已持久化 ReportRun 的主报告渲染器。
2. 为 `ChartBlock` 新建原生图表块，读取对应 `ChartArtifact.spec` 并复用现有
   `ChartView`。它显示 Chart / SQL / Data 三个原生标签页，并在块右上角提供证据入口。
3. 证据抽屉保持现有 artifact 与血缘展示；点击图表先展示 ChartArtifact，再展示其
   `input_artifact_ids` 中的 SQL/Python/reference 依赖。
4. 删除 `HtmlReportEvidence` 中为 `data-dbgpt-native-chart` 增加的 Portal 挂载以及
   iframe 外图表渲染。它仅继续服务于用户明确生成的 HTML/skill 模板预览。

## 兼容性与失败处理

- 没有原生图表 spec 的历史 ChartArtifact：展示“历史图表产物”及证据，不伪造图表。
- HTML/Skill 报告：保持现有 HTML 预览和 data attribute 证据桥接，不迁移为原生图表。
- ChartAction 执行失败：持久化失败 artifact，但不创建可展示 ChartBlock。
- 图表 spec 数据不合法：图表块展示可恢复错误状态及 evidence drawer，SQL/Data 标签页
  仍可用。

## 验收标准

1. 普通数据库分析请求无需包含任何“原生图表”提示语，仍可通过 `ChartAction` 产生
   `vis-db-chart` ChartArtifact。
2. 报告 API 返回的 `ReportDocument` 包含 `ChartBlock`，且 chart artifact spec 为
   `renderer=vis-db-chart` 并有 SQL 上游血缘。
3. 报告页在 ChartBlock 原位呈现 `ChartView`；不出现 HTML 图表占位文字，不依赖 iframe
   Portal。
4. 图表、SQL、数据与结论的证据抽屉可从同一报告块打开。
5. Olist 端到端请求通过，并覆盖：SQL、原生图表、持久化 ReportDocument、前端报告块。

## 验证策略

- Python：ChartAction 捕获 spec、ReportAssembler 构造 ChartBlock、报告 API 持久化测试。
- React：ChartBlock spec 到 `ChartView` 的渲染测试，以及无 spec 的历史兼容测试。
- E2E：Olist 数据库运行一次普通分析请求，检查 ReportDocument、artifact lineage 和
  浏览器中实际 Chart canvas。
