const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const source = fs.readFileSync(
  path.join(__dirname, '../chat/content/ManusRightPanel.tsx'),
  'utf8',
);
const reportStart = source.indexOf("reportId && reportRunId && panelView === 'report'");
const graphStart = source.indexOf("panelView === 'evidence-graph'", reportStart);

assert.notEqual(reportStart, -1, 'the persisted report branch must exist');
assert.notEqual(graphStart, -1, 'the evidence graph branch must exist');

const reportBranch = source.slice(reportStart, graphStart);
assert.match(
  reportBranch,
  /<HtmlReportEvidence/,
  'the report tab must preserve the original HTML report renderer',
);
assert.doesNotMatch(
  reportBranch,
  /<TraceableReportWorkspace/,
  'the report tab must not replace the original report with an evidence workspace',
);

const htmlRenderer = fs.readFileSync(path.join(__dirname, 'HtmlReportEvidence.tsx'), 'utf8');
assert.doesNotMatch(
  htmlRenderer,
  /data-dbgpt-native-chart="/,
  'the renderer must not require authors to add a custom native-chart marker',
);
assert.match(
  htmlRenderer,
  /const validImage = .*source.*;[\s\S]*image\.replaceWith\(host\)/,
  'only malformed image sources bound to a native chart may be replaced in place',
);
assert.match(
  htmlRenderer,
  /if \(image\) image\.replaceWith\(host\); else target\.appendChild\(host\)/,
  'a chart-bound empty figure must receive its captured native chart',
);
assert.match(
  htmlRenderer,
  /node\.tagName === 'TABLE'\) return false/,
  'a table must never become a chart mount',
);
assert.match(
  htmlRenderer,
  /node\.tagName === 'FIGURE' \|\| Boolean\(node\.querySelector\('img,canvas,svg'\)\)/,
  'only an explicit figure or existing visual container may receive a chart mount',
);
assert.match(
  htmlRenderer,
  /if \(visual && \(!image \|\| validImage\)\) return \[\];/,
  'a figure that already renders an SVG, canvas, or valid image must not receive a duplicate chart',
);
assert.match(
  htmlRenderer,
  /<AutoChart[\s\S]*showControls=\{false\}/,
  'native charts mounted into a report must hide chat-only chart controls',
);
const autoChart = fs.readFileSync(path.join(__dirname, '../../components/chart/autoChart/index.tsx'), 'utf8');
assert.match(
  autoChart,
  /showControls = true/,
  'the shared AutoChart must preserve controls by default for chat usage',
);
assert.match(
  autoChart,
  /if \(!showControls\) return <div className='flex'>\{visComponent\}<\/div>;/,
  'report mode must render only the visual and omit the advice selector/download toolbar',
);
assert.match(
  htmlRenderer,
  /querySelectorAll\('\[data-dbgpt-native-chart-host\]'\).*host\.remove\(\)/,
  'stale chart hosts must be removed before remounting',
);
assert.match(
  htmlRenderer,
  /NEXT_PUBLIC_API_BASE_URL[\s\S]*http:\/\/127\.0\.0\.1:5670/,
  'HTML reports must resolve backend-generated /images assets from the backend origin',
);
assert.match(htmlRenderer, /ResizeObserver\(resize\)/, 'the report iframe must follow document height');
assert.doesNotMatch(htmlRenderer, /min-h-\[600px\]/, 'the report iframe must not reserve a fixed blank area');
assert.match(
  htmlRenderer,
  /function isTextBlock\(node\)/,
  'textual evidence such as KB citations must use an inline placement rule',
);
assert.match(
  htmlRenderer,
  /dbgpt-evidence-host--reserve-corner/,
  'block evidence must reserve an in-card action area without changing the template layout',
);
assert.match(
  htmlRenderer,
  /function tableControlHost\(node\)/,
  'a table evidence control must use a dedicated valid table action host',
);
assert.match(
  htmlRenderer,
  /table\.insertBefore\(caption, table\.firstChild\)/,
  'a table evidence control must be placed in a caption rather than in a grid or flex parent',
);
assert.match(
  htmlRenderer,
  /dbgpt-evidence-trigger--corner[\s\S]*right:10px[\s\S]*top:10px/,
  'the in-card action must be positioned in its reserved corner area',
);
assert.match(
  htmlRenderer,
  /function formatArtifactDuration\(artifact: ReportArtifact\)/,
  'chart evidence must distinguish server-side chart-spec preparation from browser rendering',
);
assert.match(
  htmlRenderer,
  /服务端图表规格生成 < 1 ms；浏览器绘制未采集/,
  'a sub-millisecond chart-spec preparation must not be presented as a zero-millisecond chart render',
);
assert.match(
  htmlRenderer,
  /服务端执行[\s\S]*派生产物处理[\s\S]*浏览器绘制/,
  'the evidence drawer must show the three timing dimensions separately',
);
assert.match(
  htmlRenderer,
  /browserRenderMsByArtifactId/,
  'native chart paint timing must be recorded in the browser instead of being inferred from server timing',
);

const claimGraph = fs.readFileSync(path.join(__dirname, 'ClaimEvidenceGraph.tsx'), 'utf8');
assert.match(
  claimGraph,
  /const dedupeGraphClaims = \(claims: GraphClaim\[\]\)/,
  'the evidence graph must consolidate equivalent claim entries instead of listing every bound HTML fragment',
);
assert.match(
  claimGraph,
  /if \(!block\.claims\?\.length\) return \[\];/,
  'presentation-only text and chart containers must not be fabricated as business conclusions',
);

const rightPanel = fs.readFileSync(path.join(__dirname, '../chat/content/ManusRightPanel.tsx'), 'utf8');
const reportPrint = fs.readFileSync(path.join(__dirname, 'report-print.ts'), 'utf8');
assert.match(
  rightPanel,
  /printRenderedReport\(iframe\)/,
  'PDF export must print a captured report snapshot rather than immediately printing a dynamic iframe',
);
assert.match(
  reportPrint,
  /await waitForReportPaint\(source\)/,
  'the print path must wait until dynamically mounted native charts have painted',
);
assert.match(
  reportPrint,
  /toDataURL\('image\/png'\)/,
  'the print snapshot must preserve native canvas charts as print-safe images',
);
