import { getStandaloneReportRun } from '@/client/api/report/standalone';
import { AutoChart, getChartType } from '@/components/chart/autoChart';
import type { ReportArtifact, ReportRun } from '@/types/report';
import type { Datum } from '@antv/ava';
import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { createPortal } from 'react-dom';

type Props = {
  html: string;
  reportId: string;
  reportRunId: string;
  iframeRef?: React.RefObject<HTMLIFrameElement>;
  onArtifactFocus?: (stepId: string) => void;
};

type NativeChartSpec = { type?: string; data?: unknown };
type NativeChartMount = { artifact: ReportArtifact; spec: NativeChartSpec; host: HTMLElement };

const nativeChartSpec = (artifact: ReportArtifact): NativeChartSpec | undefined => {
  if (artifact.type !== 'chart' || artifact.spec?.renderer !== 'vis-db-chart') return undefined;
  const spec = artifact.spec as NativeChartSpec;
  return Array.isArray(spec.data) ? spec : undefined;
};

const chartRuntimeCache = new Map<string, Promise<string>>();
// `API_BASE_URL` is server-only in Next.js.  Use the public variant for a
// deployed frontend and the local DB-GPT backend as the development fallback.
const reportAssetBaseUrl = (
  process.env.NEXT_PUBLIC_API_BASE_URL ||
  process.env.API_BASE_URL ||
  'http://127.0.0.1:5670'
).replace(/\/$/, '');

/**
 * A report is rendered in ``srcdoc`` under the frontend origin (normally
 * :3001), while Python images are served by the DB-GPT backend (:5670/images).
 * Keep the persisted HTML portable, but resolve those known backend assets at
 * display time so a relative /images URL never becomes a frontend 404.
 */
export const resolveReportAssetUrls = (html: string): string => {
  if (!reportAssetBaseUrl) return html;
  return html.replace(
    /\bsrc\s*=\s*(["'])\/images\//gi,
    (_whole, quote: string) => `src=${quote}${reportAssetBaseUrl}/images/`,
  );
};

/**
 * HTML reports often use a CDN-hosted Chart.js runtime.  A srcdoc iframe can
 * load that dependency unreliably (enterprise proxies and iframe policies
 * commonly block it), leaving correctly sized but empty canvases.  Fetching
 * the known runtime from the application context and inlining it makes the
 * saved report self-contained at display time without changing its template.
 */
const inlineChartRuntimes = async (html: string): Promise<string> => {
  const sources = Array.from(
    html.matchAll(
      /<script\b([^>]*?)\bsrc\s*=\s*(["'])(https:\/\/[^"']*(?:chart\.js|chartjs)[^"']*)\2([^>]*)><\/script\s*>/gi,
    ),
  );
  if (!sources.length) return html;

  let resolved = html;
  for (const match of sources) {
    const original = match[0];
    const url = match[3];
    try {
      let pending = chartRuntimeCache.get(url);
      if (!pending) {
        pending = fetch(url)
          .then(response => {
            if (!response.ok) throw new Error(`Chart runtime request failed: ${response.status}`);
            return response.text();
          })
          .then(source => source.replace(/<\/script/gi, '<\\/script'));
        chartRuntimeCache.set(url, pending);
      }
      const source = await pending;
      resolved = resolved.replace(original, `<script>${source}</script>`);
    } catch {
      // Keep the original tag as a best-effort fallback for unusual runtimes.
    }
  }
  return resolved;
};

const text = (value: unknown) => {
  if (value === null || value === undefined) return '';
  if (typeof value === 'string') return value.slice(0, 4_000);
  try { return JSON.stringify(value).slice(0, 4_000); } catch { return String(value).slice(0, 4_000); }
};

const bridgeHtml = (html: string) => {
  const bridge = `<style>
    [data-dbgpt-artifacts]{cursor:pointer!important;outline:2px solid transparent;outline-offset:3px;transition:outline-color .15s,box-shadow .15s}
    [data-dbgpt-artifacts]:hover,[data-dbgpt-artifacts]:focus{outline-color:#2563eb!important;box-shadow:0 0 0 5px rgba(37,99,235,.12)!important}
    .dbgpt-evidence-trigger{border:0!important;cursor:pointer!important;background:#eff6ff!important;color:#155eef!important;font:700 12px/1 system-ui,sans-serif!important;box-shadow:0 1px 4px rgba(15,23,42,.16)!important}
    .dbgpt-evidence-trigger--inline{display:inline-flex!important;align-items:center!important;justify-content:center!important;vertical-align:middle!important;margin-left:5px!important;min-width:19px!important;height:19px!important;padding:0 4px!important;border-radius:999px!important;font-size:9px!important}
    .dbgpt-evidence-host{position:relative!important;box-sizing:border-box!important}
    /* Keep a dedicated action area inside the existing report card.  Adding a
       sibling changes flex/grid templates into an extra card/column. */
    .dbgpt-evidence-host--reserve-corner{padding-right:58px!important}
    .dbgpt-evidence-trigger--corner{position:absolute!important;right:10px!important;top:10px!important;z-index:2147483646!important;display:grid!important;place-items:center!important;min-width:30px!important;height:30px!important;padding:0 8px!important;border-radius:999px!important}
    .dbgpt-evidence-table-caption{caption-side:top!important;padding:6px 10px!important;text-align:right!important}
    .dbgpt-evidence-trigger--table{position:relative!important;display:inline-flex!important;align-items:center!important;justify-content:center!important;min-width:30px!important;height:30px!important;padding:0 8px!important;border-radius:999px!important}
    .dbgpt-evidence-trigger:hover{background:#dbeafe!important;color:#1d4ed8!important}
    .dbgpt-evidence-value{font-weight:800!important;color:#0f3a8a!important;text-decoration:underline dotted #2563eb 2px;text-underline-offset:3px}
    .dbgpt-native-chart-host{display:block!important;width:100%!important;min-width:0!important;min-height:360px!important;overflow:hidden!important;position:relative!important}
  </style><script>
    (function () {
      function emitEvidence(node) {
        if (!node) return;
        var artifactIds = (node.getAttribute('data-dbgpt-artifacts') || '').split(',').map(function (id) { return id.trim(); }).filter(Boolean);
        // srcdoc has an opaque ("null") location.origin. The receiving React
        // component validates event.source against its own iframe, so a wildcard
        // target is required here without accepting messages from other frames.
        if (artifactIds.length) parent.postMessage({ type: 'dbgpt-report-evidence', artifactIds: artifactIds, elementTag: node.tagName || '', blockId: node.getAttribute('data-dbgpt-block-id') || '', claimId: node.getAttribute('data-dbgpt-claim-id') || '', label: (node.getAttribute('data-dbgpt-evidence-label') || node.getAttribute('aria-label') || node.textContent || '').trim().slice(0, 160) }, '*');
      }
      function isInline(node) { return /^(STRONG|B|SPAN|EM|MARK|A)$/i.test(node.tagName); }
      function isTextBlock(node) { return /^(P|LI|H[1-6]|TD|TH|BLOCKQUOTE|LABEL|SUMMARY)$/i.test(node.tagName); }
      function isTableStructure(node) { return /^(TR|THEAD|TBODY|TFOOT|COLGROUP|COL)$/i.test(node.tagName); }
      function tableControlHost(node) {
        var table = node.tagName === 'TABLE' ? node : node.closest('table');
        if (!table) return null;
        var caption = table.querySelector(':scope > caption.dbgpt-evidence-table-caption');
        if (!caption) {
          caption = document.createElement('caption');
          caption.className = 'dbgpt-evidence-table-caption';
          table.insertBefore(caption, table.firstChild);
        }
        return caption;
      }
      function decorateEvidenceTargets() {
        Array.prototype.forEach.call(document.querySelectorAll('[data-dbgpt-artifacts]'), function (node) {
          if (node.hasAttribute('data-dbgpt-evidence-summary') || node.getAttribute('data-dbgpt-evidence-control') === 'true') return;
          if (node.getAttribute('data-dbgpt-evidence-decorated') === 'true') return;
          var inline = isInline(node);
          var textBlock = !inline && isTextBlock(node);
          var tableBound = node.tagName === 'TABLE' || isTableStructure(node);
          var host = tableBound ? tableControlHost(node) : (inline ? node : (/^(CANVAS|SVG|IMG)$/i.test(node.tagName) ? node.parentElement : node));
          if (!host) return;
          node.setAttribute('data-dbgpt-evidence-decorated', 'true');
          if (inline) node.classList.add('dbgpt-evidence-value');
          else if (!textBlock) {
            host.classList.add('dbgpt-evidence-host', 'dbgpt-evidence-host--reserve-corner');
          }
          var control = document.createElement('button');
          control.type = 'button'; control.setAttribute('data-dbgpt-evidence-control', 'true');
          control.setAttribute('data-dbgpt-artifacts', node.getAttribute('data-dbgpt-artifacts') || '');
          if (node.getAttribute('data-dbgpt-block-id')) control.setAttribute('data-dbgpt-block-id', node.getAttribute('data-dbgpt-block-id'));
          if (node.getAttribute('data-dbgpt-claim-id')) control.setAttribute('data-dbgpt-claim-id', node.getAttribute('data-dbgpt-claim-id'));
          control.setAttribute('data-dbgpt-evidence-label', node.getAttribute('data-dbgpt-evidence-label') || node.textContent || '报告内容');
          control.setAttribute('data-dbgpt-evidence-element-tag', node.tagName || '');
          control.setAttribute('aria-label', '查看该内容的执行证据');
          control.title = '查看执行证据'; control.className = 'dbgpt-evidence-trigger ' + (tableBound ? 'dbgpt-evidence-trigger--table' : ((inline || textBlock) ? 'dbgpt-evidence-trigger--inline' : 'dbgpt-evidence-trigger--corner'));
          control.textContent = '⌁';
          control.addEventListener('click', function (event) { event.preventDefault(); event.stopPropagation(); emitEvidence(control); });
          if (inline) node.insertAdjacentElement('afterend', control); else host.appendChild(control);
        });
      }
      document.addEventListener('click', function (event) {
        var node = event.target && event.target.closest && event.target.closest('[data-dbgpt-artifacts]');
      if (!node) return;
      event.preventDefault();
        emitEvidence(node);
      });
      document.addEventListener('keydown', function (event) {
        if (event.key !== 'Enter' && event.key !== ' ') return;
        var node = event.target && event.target.closest && event.target.closest('[data-dbgpt-artifacts]');
        if (!node) return; event.preventDefault(); emitEvidence(node);
      });
      window.addEventListener('message', function (event) {
        if (event.source !== parent || event.data?.type !== 'dbgpt-report-artifact-types') return;
        var types = event.data.types || {};
        Array.prototype.forEach.call(document.querySelectorAll('[data-dbgpt-evidence-control="true"]'), function (control) {
          var labels = (control.getAttribute('data-dbgpt-artifacts') || '').split(',').map(function (id) { return types[id.trim()]; }).filter(Boolean);
          if (control.getAttribute('data-dbgpt-evidence-element-tag') === 'TABLE') labels = labels.filter(function (value) { return value !== '图'; });
          labels = labels.filter(function (value, index) { return labels.indexOf(value) === index; });
          if (labels.length) control.textContent = labels.slice(0, 3).join('·');
        });
      });
      if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', decorateEvidenceTargets); else decorateEvidenceTargets();
    })();
  </script>`;
  return /<\/head\s*>/i.test(html) ? html.replace(/<\/head\s*>/i, `${bridge}</head>`) : `${bridge}${html}`;
};

const formatMilliseconds = (duration?: number) => {
  if (duration === undefined || duration === null) return '未采集';
  return duration === 0 ? '< 1 ms' : `${duration} ms`;
};

function formatArtifactDuration(artifact: ReportArtifact) {
  const duration = artifact.timing?.server_execution_ms ?? artifact.duration_ms;
  if (artifact.execution.action === 'chart_action.render') {
    if (duration === 0) return '服务端图表规格生成 < 1 ms；浏览器绘制未采集';
    if (duration === undefined || duration === null) return '服务端图表规格生成未采集；浏览器绘制未采集';
    return `${duration} ms（服务端图表规格生成；浏览器绘制未采集）`;
  }
  if (artifact.execution.action === 'code_interpreter.image' && duration === 0) {
    return '由上游 Python 生成；本图像产物未单独计时';
  }
  return duration === undefined || duration === null ? '-' : `${duration} ms`;
}

const derivativeTiming = (artifact: ReportArtifact) => {
  const timing = artifact.timing;
  if (timing?.derivative_processing_state === 'captured') return formatMilliseconds(timing.derivative_processing_ms);
  if (timing?.derivative_processing_state === 'included_in_source_execution') return '已包含在上游执行中';
  if (timing?.derivative_processing_state === 'not_measured') return '未单独采集';
  if (artifact.execution.action === 'code_interpreter.image') return '已包含在上游 Python 执行中';
  return '不适用';
};

const browserTiming = (artifact: ReportArtifact, browserRenderMs?: number) => {
  if (browserRenderMs !== undefined) return `${formatMilliseconds(browserRenderMs)}（本次浏览器绘制）`;
  const timing = artifact.timing;
  if (timing?.browser_render_state === 'captured') return formatMilliseconds(timing.browser_render_ms);
  if (timing?.browser_render_state === 'not_measured' || artifact.type === 'chart') return '未采集';
  return '不适用';
};

const ArtifactDetail: React.FC<{ artifact: ReportArtifact; browserRenderMs?: number; onFocus?: (stepId: string) => void }> = ({ artifact, browserRenderMs, onFocus }) => (
  <article className='rounded-xl border border-slate-200 p-4 dark:border-slate-700'>
    <div className='flex items-start justify-between gap-3'><div><p className='text-[11px] font-bold uppercase text-blue-600'>{artifact.type}</p><h3 className='mt-1 break-all text-sm font-semibold text-slate-900 dark:text-white'>{artifact.name || artifact.artifact_id}</h3></div><span className='rounded-full bg-emerald-50 px-2 py-1 text-[11px] text-emerald-700'>{artifact.status || 'captured'}</span></div>
    <dl className='mt-4 grid grid-cols-[104px_1fr] gap-y-2 text-xs text-slate-600 dark:text-slate-300'><dt>Run ID</dt><dd className='break-all'>{artifact.execution.run_id || '-'}</dd><dt>执行操作</dt><dd>{artifact.execution.action || '-'}</dd><dt>服务端执行</dt><dd>{formatArtifactDuration(artifact)}</dd><dt>派生产物处理</dt><dd>{derivativeTiming(artifact)}</dd><dt>浏览器绘制</dt><dd>{browserTiming(artifact, browserRenderMs)}</dd><dt>数据源</dt><dd>{artifact.database || artifact.source_name || '-'}</dd></dl>
    {artifact.sql && <><h4 className='mt-4 text-xs font-semibold text-slate-700 dark:text-slate-200'>执行 SQL</h4><pre className='mt-2 max-h-48 overflow-auto rounded-lg bg-slate-950 p-3 text-xs text-slate-50'>{text(artifact.sql)}</pre></>}
    {artifact.code && <><h4 className='mt-4 text-xs font-semibold text-slate-700 dark:text-slate-200'>执行 {artifact.language || 'Python'}</h4><pre className='mt-2 max-h-48 overflow-auto rounded-lg bg-slate-950 p-3 text-xs text-slate-50'>{text(artifact.code)}</pre></>}
    {artifact.reference_type && <><h4 className='mt-4 text-xs font-semibold text-slate-700 dark:text-slate-200'>引用来源</h4><div className='mt-2 rounded-lg bg-teal-50 p-3 text-xs text-teal-950 dark:bg-teal-500/10 dark:text-teal-100'><p className='font-semibold'>{artifact.source_name || artifact.source_id || artifact.reference_type}</p>{artifact.uri && <p className='mt-1 break-all text-teal-800 dark:text-teal-200'>{artifact.uri}</p>}{artifact.excerpt && <p className='mt-2 whitespace-pre-wrap leading-5'>{text(artifact.excerpt)}</p>}</div></>}
    {artifact.result?.inline !== undefined && <><h4 className='mt-4 text-xs font-semibold text-slate-700 dark:text-slate-200'>结果快照</h4><pre className='mt-2 max-h-36 overflow-auto rounded-lg bg-emerald-50 p-3 text-xs text-emerald-950 dark:bg-emerald-500/10 dark:text-emerald-100'>{text(artifact.result.inline)}</pre></>}
    {onFocus && artifact.execution.step_id && <button type='button' onClick={() => onFocus(artifact.execution.step_id)} className='mt-4 w-full rounded-lg bg-blue-600 py-2.5 text-xs font-semibold text-white hover:bg-blue-700'>定位到执行步骤</button>}
  </article>
);

const BrowserTimedNativeChart: React.FC<{ artifactId: string; spec: NativeChartSpec; onRendered: (artifactId: string, durationMs: number) => void }> = ({ artifactId, spec, onRendered }) => {
  const startedAt = useRef<number>();
  useEffect(() => {
    startedAt.current = performance.now();
    let secondFrame = 0;
    const firstFrame = requestAnimationFrame(() => {
      secondFrame = requestAnimationFrame(() => {
        onRendered(artifactId, Math.max(0, Math.round(performance.now() - (startedAt.current || performance.now()))));
      });
    });
    return () => { cancelAnimationFrame(firstFrame); if (secondFrame) cancelAnimationFrame(secondFrame); };
  }, [artifactId, onRendered]);
  return <AutoChart data={spec.data as Datum[]} chartType={getChartType((spec.type || 'response_table') as Parameters<typeof getChartType>[0])} showControls={false} />;
};

export const HtmlReportEvidence: React.FC<Props> = ({ html, reportId, reportRunId, iframeRef, onArtifactFocus }) => {
  const ownedIframeRef = useRef<HTMLIFrameElement>(null);
  const reportIframeRef = iframeRef || ownedIframeRef;
  const [run, setRun] = useState<ReportRun>();
  const [error, setError] = useState('');
  const [selection, setSelection] = useState<{ artifactIds: string[]; elementTag?: string; blockId?: string; claimId?: string; label: string }>();
  const [iframeDocument, setIframeDocument] = useState<Document>();
  const [nativeChartMounts, setNativeChartMounts] = useState<NativeChartMount[]>([]);
  const [browserRenderMsByArtifactId, setBrowserRenderMsByArtifactId] = useState<Record<string, number>>({});
  const [iframeHeight, setIframeHeight] = useState<number>();
  useEffect(() => { let alive = true; const load = async () => { let lastError = '报告证据暂不可用'; for (let attempt = 0; attempt < 5 && alive; attempt += 1) { try { const result = await getStandaloneReportRun(reportId, reportRunId); if (result.success) { setRun(result.data); setError(''); return; } lastError = result.err_msg || lastError; } catch (reason) { lastError = reason instanceof Error ? reason.message : lastError; } await new Promise(resolve => window.setTimeout(resolve, 600 * (attempt + 1))); } if (alive) setError(lastError); }; void load(); return () => { alive = false; }; }, [reportId, reportRunId]);
  const sendArtifactTypes = useCallback(() => {
    if (!run) return;
    const labels: Record<string, string> = { sql: 'SQL', python: 'PY', chart: '图' };
    const types = Object.fromEntries(run.document.artifacts.map(artifact => [artifact.artifact_id, artifact.type === 'reference' ? ({ knowledge: 'KB', web: 'WEB', memory: '记忆', model: '模型' }[artifact.reference_type || ''] || '来源') : labels[artifact.type] || artifact.type.toUpperCase()]));
    reportIframeRef.current?.contentWindow?.postMessage({ type: 'dbgpt-report-artifact-types', types }, window.location.origin);
  }, [reportIframeRef, run]);
  useEffect(() => { sendArtifactTypes(); }, [sendArtifactTypes]);
  useEffect(() => { const onMessage = (event: MessageEvent) => { if (event.source !== reportIframeRef.current?.contentWindow || event.data?.type !== 'dbgpt-report-evidence') return; setSelection({ artifactIds: Array.isArray(event.data.artifactIds) ? event.data.artifactIds : [], elementTag: String(event.data.elementTag || '') || undefined, blockId: String(event.data.blockId || '') || undefined, claimId: String(event.data.claimId || '') || undefined, label: String(event.data.label || '报告内容') }); }; window.addEventListener('message', onMessage); return () => window.removeEventListener('message', onMessage); }, [reportIframeRef]);
  const artifacts = useMemo(() => {
    const byId = new Map(run?.document.artifacts.map(item => [item.artifact_id, item]));
    const blocks = run?.document.sections.flatMap(section => section.blocks) || [];
    const structuredBlock = blocks.find(block => {
      if (selection?.blockId && block.block_id === selection.blockId) return true;
      const target = block.metadata?.presentation_target as { kind?: string; target_id?: string } | undefined;
      return Boolean(selection?.claimId && target?.kind === 'html_claim' && target.target_id === selection.claimId);
    });
    const artifactIds = structuredBlock?.evidence.map(item => item.artifact_id) || selection?.artifactIds || [];
    return artifactIds.map(id => byId.get(id)).filter((item): item is ReportArtifact => Boolean(item) && (selection?.elementTag !== 'TABLE' || item.type !== 'chart'));
  }, [run, selection]);
  const nativeCharts = useMemo(() => (run?.document.artifacts || [])
    .map(artifact => ({ artifact, spec: nativeChartSpec(artifact) }))
    .filter((item): item is { artifact: ReportArtifact; spec: NativeChartSpec } => Boolean(item.spec)), [run]);
  const persistedPresentation = run?.document.metadata?.presentation as { format?: string; content?: string } | undefined;
  const reportHtml = persistedPresentation?.format === 'html' && persistedPresentation.content ? persistedPresentation.content : html;
  const [runtimeReadyHtml, setRuntimeReadyHtml] = useState(reportHtml);
  useEffect(() => {
    let alive = true;
    setIframeDocument(undefined);
    setNativeChartMounts([]);
    setBrowserRenderMsByArtifactId({});
    setIframeHeight(undefined);
    setRuntimeReadyHtml(reportHtml);
    void inlineChartRuntimes(reportHtml).then(nextHtml => {
      if (alive) setRuntimeReadyHtml(nextHtml);
    });
    return () => { alive = false; };
  }, [reportHtml]);
  const onNativeChartRendered = useCallback((artifactId: string, durationMs: number) => {
    setBrowserRenderMsByArtifactId(previous => previous[artifactId] === durationMs ? previous : { ...previous, [artifactId]: durationMs });
  }, []);
  const presentationStatus = String(run?.document.metadata?.presentation_status || '');
  const noPresentationMessage = presentationStatus === 'not_generated'
    ? '本轮仅保存了执行证据，未完成 HTML 报告生成。请重新执行或从最后一个失败步骤继续。'
    : '本轮尚未生成 HTML 报告时，不会以执行产物卡片替代报告。';
  const onIframeLoad = useCallback(() => {
    sendArtifactTypes();
    const document = reportIframeRef.current?.contentDocument;
    setIframeDocument(document || undefined);
  }, [reportIframeRef, sendArtifactTypes]);
  useEffect(() => {
    if (!iframeDocument) return undefined;
    const resize = () => {
      const height = Math.max(
        iframeDocument.body?.scrollHeight || 0,
        iframeDocument.documentElement?.scrollHeight || 0,
      );
      setIframeHeight(Math.max(160, height));
    };
    resize();
    const observer = new ResizeObserver(resize);
    if (iframeDocument.body) observer.observe(iframeDocument.body);
    window.setTimeout(resize, 0);
    return () => observer.disconnect();
  }, [iframeDocument]);
  useEffect(() => {
    if (!iframeDocument) return;
    // Remove mounts created by an earlier report version / hot reload before
    // calculating the current targets. Without this, a formerly table-bound
    // chart host remains in the iframe even after the selection rule is fixed.
    iframeDocument.querySelectorAll('[data-dbgpt-native-chart-host]').forEach(host => host.remove());
    setNativeChartMounts([]);
    const mounts = nativeCharts.flatMap(({ artifact, spec }) => {
      const target = Array.from(iframeDocument.querySelectorAll<HTMLElement>('[data-dbgpt-artifacts]'))
        .find(node => {
          const isBound = (node.getAttribute('data-dbgpt-artifacts') || '').split(',').map(id => id.trim()).includes(artifact.artifact_id);
          if (!isBound || node.tagName === 'TABLE') return false;
          // An empty figure is an explicit chart placeholder.  For legacy
          // malformed image markup, retain the in-place repair path as well.
          // Do not append charts to generic text/card containers.
          return node.tagName === 'FIGURE' || Boolean(node.querySelector('img,canvas,svg'));
        });
      if (!target || target.querySelector(`[data-dbgpt-native-chart-host="${artifact.artifact_id}"]`)) return [];
      const visual = target.querySelector('img,canvas,svg');
      const image = visual instanceof HTMLImageElement ? visual : null;
      const source = image?.getAttribute('src') || '';
      const validImage = /^(?:https?:|\/images\/|data:image\/)/i.test(source);
      if (visual && (!image || validImage)) return [];
      // Native chart_query records a complete DB-GPT chart specification. Some
      // older reports bind it to an otherwise empty <figure> (or write prose
      // into img.src) but omit the rendered node. Restore only that missing
      // visual in the already-bound non-table target; never alter cards, tables,
      // or unrelated template content.
      const host = iframeDocument.createElement('div');
      host.className = 'dbgpt-native-chart-host';
      host.setAttribute('data-dbgpt-native-chart-host', artifact.artifact_id);
      if (image) image.replaceWith(host); else target.appendChild(host);
      // The report author binds the figure to both the source SQL and its
      // generated chart artifact.  Make that relationship explicit on the
      // already-injected control as well: otherwise a control created before
      // the native-chart portal mounts can misleadingly display only “SQL”.
      target.querySelectorAll<HTMLButtonElement>('[data-dbgpt-evidence-control="true"]').forEach(control => {
        const artifactIds = (control.getAttribute('data-dbgpt-artifacts') || '')
          .split(',').map(id => id.trim()).filter(Boolean);
        if (!artifactIds.includes(artifact.artifact_id)) {
          artifactIds.push(artifact.artifact_id);
          control.setAttribute('data-dbgpt-artifacts', artifactIds.join(','));
        }
        const hasSql = artifactIds.some(id => id.startsWith('sql-'));
        control.textContent = hasSql ? 'SQL·图' : '图';
        control.title = '查看图表及其数据证据';
        control.setAttribute('aria-label', '查看图表及其数据证据');
      });
      return [{ artifact, spec, host }];
    });
    setNativeChartMounts(mounts);
  }, [iframeDocument, nativeCharts]);
  return <div className='flex h-full min-h-0 bg-slate-100 dark:bg-[#111217]'>
    <div className='min-w-0 flex-1 overflow-auto'>{runtimeReadyHtml.trim() ? <iframe ref={reportIframeRef} onLoad={onIframeLoad} title='分析报告' srcDoc={bridgeHtml(resolveReportAssetUrls(runtimeReadyHtml))} sandbox='allow-scripts allow-same-origin allow-modals' className='block w-full border-0 bg-white' style={{ height: iframeHeight ? `${iframeHeight}px` : 320 }} /> : <div className='grid h-full min-h-[360px] place-items-center p-8 text-center'><div><p className='text-sm font-medium text-slate-700 dark:text-slate-200'>本轮未生成 HTML 分析报告</p><p className='mt-2 text-xs text-slate-500'>{error || noPresentationMessage}</p></div></div>}{nativeChartMounts.map(({ artifact, spec, host }) => createPortal(<BrowserTimedNativeChart artifactId={artifact.artifact_id} spec={spec} onRendered={onNativeChartRendered} />, host, artifact.artifact_id))}</div>
    {selection && <aside className='z-10 w-[420px] shrink-0 overflow-y-auto border-l border-slate-200 bg-white shadow-2xl dark:border-slate-700 dark:bg-[#191a1f]'><header className='sticky top-0 border-b border-slate-100 bg-white px-5 py-4 dark:border-slate-800 dark:bg-[#191a1f]'><div className='flex items-start justify-between gap-3'><div><p className='text-xs font-bold text-blue-600'>证据详情</p><h2 className='mt-1 text-base font-semibold text-slate-900 dark:text-white'>{selection.label || '报告内容'}</h2></div><button type='button' onClick={() => setSelection(undefined)} className='rounded px-2 text-xl text-slate-400 hover:bg-slate-100'>×</button></div></header><div className='space-y-4 p-5'>{!run && <p className='text-sm text-slate-500'>{error || '正在加载证据…'}</p>}{run && !artifacts.length && <p className='rounded-lg bg-amber-50 p-3 text-sm text-amber-800'>该报告元素引用的证据不存在于本次运行中。</p>}{artifacts.map(artifact => <ArtifactDetail key={artifact.artifact_id} artifact={artifact} browserRenderMs={browserRenderMsByArtifactId[artifact.artifact_id]} onFocus={onArtifactFocus} />)}</div></aside>}
    {!selection && <div className='pointer-events-none absolute bottom-5 right-5 max-w-xs rounded-xl border border-blue-100 bg-white/95 p-3 text-xs leading-5 text-slate-600 shadow-lg dark:border-blue-500/30 dark:bg-[#191a1f]/95 dark:text-slate-300'>{error ? '当前报告证据尚不可用。' : '带“证据”标记的指标、图表和结论可点击查看其执行依据。'}</div>}
  </div>;
};

export default HtmlReportEvidence;
