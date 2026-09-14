import { getStandaloneReportRun } from '@/client/api/report/standalone';
import ChartView from '@/components/chat/chat-content/chart-view';
import type { BackEndChartType } from '@/components/chart/autoChart/helpers';
import ClaimEvidenceGraph from './ClaimEvidenceGraph';
import type { ArtifactLineage, EvidenceReference, ReportArtifact, ReportBlock, ReportClaim, ReportRun } from '@/types/report';
import type { Datum } from '@antv/ava';
import React, { useCallback, useEffect, useLayoutEffect, useMemo, useState } from 'react';

export type TraceableReportView = 'report' | 'evidence';

interface TraceableReportWorkspaceProps {
  reportId: string;
  reportRunId: string;
  view: TraceableReportView;
  onArtifactFocus?: (stepId: string) => void;
}

type ClaimPresentationStatus = 'verified' | 'partial' | 'unverified' | 'bound';
type GraphClaim = {
  id: string;
  text: string;
  block: ReportBlock;
  sectionTitle: string;
  references: EvidenceReference[];
  status: ClaimPresentationStatus;
  score?: number;
  method?: string;
  legacy: boolean;
};

const MAX_CHARS = 1_500;
const display = (value: unknown) => {
  if (value === null || value === undefined) return '';
  if (typeof value === 'string') return value.slice(0, MAX_CHARS);
  if (typeof value === 'number' || typeof value === 'boolean') return String(value);
  try { return JSON.stringify(value, null, 2).slice(0, MAX_CHARS); } catch { return String(value).slice(0, MAX_CHARS); }
};

const artifactLabel = (artifact: ReportArtifact) => artifactDisplayName(artifact);
const artifactKind = (artifact: ReportArtifact) => {
  if (artifact.type === 'sql') return 'SQL';
  if (artifact.type === 'python') return artifact.language?.toUpperCase() || 'PY';
  if (artifact.type === 'chart') return 'CHART';
  if (artifact.reference_type === 'knowledge') return 'KB';
  if (artifact.reference_type === 'memory') return 'MEM';
  if (artifact.reference_type === 'model') return 'AI';
  return artifact.reference_type?.toUpperCase() || 'SOURCE';
};
const artifactSource = (artifact: ReportArtifact) => artifact.source_name || (artifact.type === 'sql' ? '当前连接数据库' : artifact.reference_type === 'memory' ? '会话记忆' : artifact.reference_type === 'model' ? 'Data Agent' : '任务输入');

/** Presentation labels are deliberately separate from immutable artifact names. */
const isPreparationArtifact = (artifact?: ReportArtifact) => {
  if (!artifact) return true;
  const text = `${artifact.name || ''}\n${artifact.sql || ''}\n${artifact.code || ''}`.toLowerCase();
  return /sqlite_master|information_schema|\bpragma\b|query the schema|schema of .*table|dispatch_parallel|sub-agent sql result/.test(text);
};

const artifactDisplayName = (artifact: ReportArtifact) => {
  const text = `${artifact.name || ''}\n${artifact.sql || ''}`.toLowerCase();
  if (artifact.type === 'chart') return '分析图表';
  if (artifact.type === 'python') return '数据计算';
  if (/payment.*(sum|amount)|sum.*payment/.test(text)) return '支付方式金额统计';
  if (/count\s*\(.*order|total_orders/.test(text)) return '订单总量统计';
  if (/installment|分期/.test(text)) return '分期情况统计';
  if (/avg|average|客单价/.test(text)) return '平均值与客单价计算';
  if (/date|month|月度|trend/.test(text)) return '经营趋势统计';
  if (artifact.type === 'sql') return '业务数据查询';
  if (artifact.reference_type === 'knowledge') return '知识库依据';
  return artifactKind(artifact) === 'AI' ? '模型分析依据' : '分析执行产物';
};

const localizedClaimText = (rawText: string, block: ReportBlock, artifact?: ReportArtifact) => {
  if (/[\u4e00-\u9fff]/.test(rawText)) return rawText;
  const basis = artifact ? artifactDisplayName(artifact) : '业务分析';
  if (block.type === 'metric') return `${basis}指标`;
  if (block.type === 'table') return `${basis}结果明细`;
  if (block.type === 'chart') return `${basis}图表结论`;
  return `${basis}分析结论`;
};

const tableData = (artifact?: ReportArtifact): { columns: string[]; rows: unknown[][] } => {
  const inline = artifact?.result?.inline;
  if (inline && typeof inline === 'object' && !Array.isArray(inline)) {
    const payload = inline as { columns?: unknown; rows?: unknown };
    if (Array.isArray(payload.columns) && Array.isArray(payload.rows)) return { columns: payload.columns.slice(0, 8).map(String), rows: payload.rows.slice(0, 8).filter(Array.isArray).map(row => (row as unknown[]).slice(0, 8)) };
  }
  return { columns: [], rows: [] };
};

const artifactLineage = (artifact: ReportArtifact): ArtifactLineage => {
  const candidate = artifact.metadata.lineage;
  if (!candidate || typeof candidate !== 'object' || Array.isArray(candidate)) return {};
  const lineage = candidate as Record<string, unknown>;
  return {
    source_id: typeof lineage.source_id === 'string' ? lineage.source_id : artifact.source_id,
    source_name: typeof lineage.source_name === 'string' ? lineage.source_name : artifact.source_name,
    tables: Array.isArray(lineage.tables) ? lineage.tables.filter((value): value is string => typeof value === 'string' && Boolean(value.trim())) : [],
  };
};

const tablesReadBy = (artifact: ReportArtifact) => artifactLineage(artifact).tables || [];
const recordedSource = (artifact: ReportArtifact) => artifactLineage(artifact).source_name || artifactLineage(artifact).source_id;

const legacyClaimText = (block: ReportBlock) => {
  if (block.type === 'metric') return `${block.title || '指标'}：${display(block.display_value ?? block.value)}${block.unit ? ` ${block.unit}` : ''}`;
  if (block.type === 'insight' || block.type === 'text') return display(block.content || block.description || block.title || '分析结论');
  return block.title || (block.type === 'table' ? '表格分析结果' : '图表分析结果');
};

const claimStatus = (claim?: ReportClaim): ClaimPresentationStatus => {
  const status = claim?.verification?.status;
  if (!status) return 'bound';
  if (status === 'verified') return 'verified';
  if (status === 'partially_supported') return 'partial';
  return 'unverified';
};

const statusLabel = (status: ClaimPresentationStatus) => ({
  verified: '已验证',
  partial: '部分支持',
  unverified: '待核验',
  bound: '已绑定证据',
}[status]);

const statusStyle = (status: ClaimPresentationStatus) => ({
  verified: 'border-emerald-200 bg-emerald-50 text-emerald-700 dark:border-emerald-500/30 dark:bg-emerald-500/10 dark:text-emerald-300',
  partial: 'border-amber-200 bg-amber-50 text-amber-700 dark:border-amber-500/30 dark:bg-amber-500/10 dark:text-amber-300',
  unverified: 'border-rose-200 bg-rose-50 text-rose-700 dark:border-rose-500/30 dark:bg-rose-500/10 dark:text-rose-300',
  bound: 'border-blue-200 bg-blue-50 text-blue-700 dark:border-blue-500/30 dark:bg-blue-500/10 dark:text-blue-300',
}[status]);

const EvidenceTag: React.FC<{ reference: EvidenceReference; artifact?: ReportArtifact; onClick: () => void }> = ({ reference, artifact, onClick }) => (
  <button type='button' onClick={event => { event.stopPropagation(); onClick(); }} className='rounded-md border border-blue-100 bg-blue-50 px-1.5 py-0.5 text-[10px] font-semibold text-blue-700 transition hover:border-blue-300 hover:bg-blue-100 dark:border-blue-500/20 dark:bg-blue-500/10 dark:text-blue-300'>{artifact ? artifactKind(artifact) : 'MISSING'}<span className='ml-1 font-normal opacity-70'>{reference.role}</span></button>
);

const EvidenceDrawer: React.FC<{ artifact?: ReportArtifact; reference?: EvidenceReference; block?: ReportBlock; onClose: () => void; onArtifactFocus?: (stepId: string) => void }> = ({ artifact, reference, block, onClose, onArtifactFocus }) => {
  if (!artifact) return null;
  const result = tableData(artifact); const code = artifact.sql || artifact.code || artifact.excerpt;
  return <div className='fixed inset-0 z-50 flex justify-end' role='dialog' aria-modal='true' aria-label='证据详情'>
    <button type='button' aria-label='关闭证据详情' onClick={onClose} className='absolute inset-0 cursor-default bg-slate-950/40 backdrop-blur-[1px]' />
    <aside className='relative flex h-full w-full max-w-[460px] flex-col overflow-hidden bg-white shadow-2xl dark:bg-[#15161b]'>
      <header className='border-b border-slate-200 px-5 py-5 dark:border-slate-700'><div className='flex items-start justify-between gap-4'><div className='min-w-0'><span className='rounded bg-blue-50 px-2 py-1 text-[10px] font-bold text-blue-700 dark:bg-blue-500/15 dark:text-blue-300'>{artifactKind(artifact)} · {artifact.status === 'succeeded' ? '执行成功' : artifact.status || '已捕获'}</span><h2 className='mt-3 break-words text-lg font-semibold text-slate-900 dark:text-slate-100'>{artifactLabel(artifact)}</h2><p className='mt-1 text-xs text-slate-500'>{block ? `报告块：${block.title || block.type}` : '真实执行产物'}</p></div><button type='button' onClick={onClose} className='rounded px-2 py-1 text-xs text-slate-500 hover:bg-slate-100 dark:hover:bg-white/10'>关闭</button></div></header>
      <div className='flex-1 space-y-5 overflow-y-auto p-5'>
        <div className='grid grid-cols-2 gap-3 text-xs'><div className='rounded-lg border border-slate-200 p-3 dark:border-slate-700'><p className='text-slate-400'>执行耗时</p><strong className='mt-1 block text-slate-800 dark:text-slate-100'>{artifact.duration_ms ?? '-'} ms</strong></div><div className='rounded-lg border border-slate-200 p-3 dark:border-slate-700'><p className='text-slate-400'>Run ID</p><strong className='mt-1 block truncate font-mono text-[10px] text-slate-700 dark:text-slate-200'>{artifact.execution.run_id || '-'}</strong></div></div>
        <section><p className='mb-2 text-xs font-semibold text-slate-500'>数据来源</p><div className='rounded-lg border border-slate-200 bg-slate-50 px-3 py-3 text-sm text-slate-700 dark:border-slate-700 dark:bg-white/[0.04] dark:text-slate-200'>{artifactSource(artifact)}</div></section>
        {code && <section><p className='mb-2 text-xs font-semibold text-slate-500'>查询 / 计算</p><pre className='max-h-64 overflow-auto whitespace-pre-wrap rounded-lg bg-slate-950 p-3 text-[11px] leading-5 text-slate-100'>{display(code)}</pre></section>}
        {result.columns.length > 0 && <section><p className='mb-2 text-xs font-semibold text-slate-500'>结果快照</p><div className='overflow-auto rounded-lg border border-emerald-100'><p className='bg-emerald-50 px-3 py-2 text-xs text-emerald-800'>{artifact.row_count ?? result.rows.length} rows</p><table className='min-w-full text-left text-xs'><thead><tr>{result.columns.map(column => <th className='border-t bg-slate-50 px-2 py-2 font-medium' key={column}>{column}</th>)}</tr></thead><tbody>{result.rows.map((row, index) => <tr key={index}>{row.map((cell, cellIndex) => <td className='border-t px-2 py-2' key={cellIndex}>{display(cell)}</td>)}</tr>)}</tbody></table></div></section>}
        {artifact.result?.sha256 && <p className='break-all rounded-lg bg-slate-50 p-3 text-[10px] text-slate-500 dark:bg-white/[0.04]'>内容 Hash：{artifact.result.sha256}</p>}
        {reference && <p className='text-xs text-slate-400'>报告引用：artifact://{reference.artifact_id}</p>}
      </div>
      {artifact.execution.step_id && onArtifactFocus && <footer className='border-t border-slate-200 p-4 dark:border-slate-700'><button type='button' onClick={() => onArtifactFocus(artifact.execution.step_id)} className='w-full rounded-lg bg-blue-600 px-4 py-2.5 text-sm font-medium text-white hover:bg-blue-700'>定位到执行步骤</button></footer>}
    </aside>
  </div>;
};

const nativeChartSpec = (artifact?: ReportArtifact): { data: Datum[]; sql: string; type: BackEndChartType } | undefined => {
  const spec = artifact?.spec;
  if (!spec || spec.renderer !== 'vis-db-chart' || !Array.isArray(spec.data) || typeof spec.type !== 'string') return undefined;
  const types: BackEndChartType[] = ['response_line_chart', 'response_bar_chart', 'response_pie_chart', 'response_scatter_chart', 'response_area_chart', 'response_heatmap_chart', 'response_table'];
  if (!types.includes(spec.type as BackEndChartType)) return undefined;
  return { data: spec.data as Datum[], sql: typeof spec.sql === 'string' ? spec.sql : '', type: spec.type as BackEndChartType };
};

const NativeChartBlock: React.FC<{ artifact?: ReportArtifact; onEvidence: () => void }> = ({ artifact, onEvidence }) => {
  const spec = nativeChartSpec(artifact);
  if (!spec) return <div className='mt-4 rounded-lg border border-dashed border-blue-200 bg-blue-50/40 p-5 text-sm text-blue-800 dark:border-blue-500/20 dark:bg-blue-500/10 dark:text-blue-200'><strong>图表规格不可重放</strong><p className='mt-1 text-xs opacity-75'>该历史图表没有保存原生 vis-db-chart 数据。可打开证据查看原始执行产物。</p><button type='button' onClick={onEvidence} className='mt-3 rounded-md border border-blue-200 bg-white px-2 py-1 text-xs font-semibold text-blue-700'>查看证据</button></div>;
  return <div className='mt-4 rounded-xl border border-slate-200 bg-white p-3 dark:border-slate-700 dark:bg-[#191a1f]'><div className='mb-2 flex items-center justify-between gap-3'><p className='text-xs text-slate-500'>DB-GPT 原生图表</p><button type='button' onClick={onEvidence} className='rounded-md border border-blue-200 bg-blue-50 px-2 py-1 text-xs font-semibold text-blue-700 hover:bg-blue-100'>查看图表证据</button></div><ChartView data={spec.data} type={spec.type} sql={spec.sql} /></div>;
};

const ReportBlockCard: React.FC<{ block: ReportBlock; artifacts: Map<string, ReportArtifact>; onSelect: (artifact: ReportArtifact, reference: EvidenceReference, block: ReportBlock) => void }> = ({ block, artifacts, onSelect }) => {
  const resultArtifact = block.type === 'table' ? artifacts.get(block.data_artifact_id || '') : block.type === 'chart' ? artifacts.get(block.chart_artifact_id || '') : undefined;
  const data = tableData(resultArtifact); const metric = block.type === 'metric'; const chartReference = block.evidence.find(reference => reference.artifact_id === resultArtifact?.artifact_id) || block.evidence[0];
  return <article className={`rounded-xl border border-slate-200 bg-white p-5 shadow-sm transition hover:border-blue-300 dark:border-slate-700 dark:bg-[#191a1f] ${metric ? '' : 'md:col-span-2'}`}><div className='flex flex-wrap items-start justify-between gap-3'><h3 className='text-sm font-semibold text-slate-800 dark:text-slate-100'>{block.title || block.type}</h3><div className='flex flex-wrap gap-1'>{block.evidence.map(reference => <EvidenceTag key={`${block.block_id}-${reference.artifact_id}`} reference={reference} artifact={artifacts.get(reference.artifact_id)} onClick={() => { const artifact = artifacts.get(reference.artifact_id); if (artifact) onSelect(artifact, reference, block); }} />)}</div></div>
    {metric && <p className='mt-5 text-3xl font-bold tracking-tight text-slate-900 dark:text-white'>{display(block.display_value ?? block.value)} <span className='text-base font-medium text-slate-500'>{display(block.unit)}</span></p>}
    {block.type === 'table' && (data.columns.length ? <div className='mt-4 overflow-auto'><table className='min-w-full text-left text-xs'><thead><tr>{data.columns.map(column => <th className='border-b bg-slate-50 px-2 py-2 font-medium dark:bg-white/[0.04]' key={column}>{column}</th>)}</tr></thead><tbody>{data.rows.map((row, index) => <tr key={index}>{row.map((cell, cellIndex) => <td className='border-b px-2 py-2' key={cellIndex}>{display(cell)}</td>)}</tr>)}</tbody></table></div> : <p className='mt-4 text-sm text-slate-500'>暂无可展示的结果快照</p>)}
    {block.type === 'chart' && <NativeChartBlock artifact={resultArtifact} onEvidence={() => { if (resultArtifact && chartReference) onSelect(resultArtifact, chartReference, block); }} />}
    {(block.type === 'insight' || block.type === 'text') && <p className='mt-4 whitespace-pre-wrap text-sm leading-7 text-slate-700 dark:text-slate-300'>{display(block.content || block.description)}</p>}
  </article>;
};

const ReportDashboard: React.FC<{ run: ReportRun; onArtifactFocus?: (stepId: string) => void }> = ({ run, onArtifactFocus }) => {
  const [selected, setSelected] = useState<{ artifact: ReportArtifact; reference: EvidenceReference; block: ReportBlock }>(); const artifacts = useMemo(() => new Map(run.document.artifacts.map(artifact => [artifact.artifact_id, artifact])), [run]); const sections = run.document.sections.filter(section => section.blocks.some(block => block.evidence.length > 0));
  if (!sections.length) return <div className='rounded-xl border border-amber-200 bg-amber-50 p-5 text-sm text-amber-900'>这是旧版未绑定摘要 Run：已保存 {run.document.artifacts.length} 个执行产物，但报告块尚未建立证据关联。请重新执行任务以生成可追溯分析报告。</div>;
  return <div className='space-y-8'><header className='rounded-2xl bg-gradient-to-br from-[#162582] to-[#4338a3] px-6 py-7 text-white shadow-sm'><p className='text-xs font-semibold uppercase tracking-[0.18em] text-blue-200'>Traceable Report · Schema v{run.schema_version}</p><h1 className='mt-3 text-2xl font-bold leading-9'>{run.document.title}</h1><p className='mt-3 text-sm text-blue-100'>真实执行产物 {run.document.artifacts.length} 个 · ReportRun Revision {run.revision}</p></header>{sections.map(section => <section key={section.section_id}><div className='mb-3 flex items-center justify-between'><h2 className='border-l-4 border-blue-600 pl-3 text-lg font-bold text-slate-900 dark:text-white'>{section.title}</h2><span className='text-xs text-slate-400'>{section.blocks.length} 个报告块</span></div><div className='grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3'>{section.blocks.map(block => <ReportBlockCard key={block.block_id} block={block} artifacts={artifacts} onSelect={(artifact, reference, selectedBlock) => setSelected({ artifact, reference, block: selectedBlock })} />)}</div></section>)}<EvidenceDrawer artifact={selected?.artifact} reference={selected?.reference} block={selected?.block} onClose={() => setSelected(undefined)} onArtifactFocus={onArtifactFocus} /></div>;
};

export const LegacyEvidenceGraph: React.FC<{ run: ReportRun; onArtifactFocus?: (stepId: string) => void }> = ({ run, onArtifactFocus }) => {
  const artifacts = useMemo(() => new Map(run.document.artifacts.map(artifact => [artifact.artifact_id, artifact])), [run]);
  const claims = useMemo<GraphClaim[]>(() => run.document.sections.flatMap(section => section.blocks.flatMap(block => {
    const realClaims = block.claims || [];
    const fromClaim = (claim: ReportClaim): GraphClaim => {
      const verifiedIds = claim.verification?.evidence_artifact_ids;
      const references = (verifiedIds?.length ? block.evidence.filter(reference => verifiedIds.includes(reference.artifact_id)) : block.evidence).filter(reference => !isPreparationArtifact(artifacts.get(reference.artifact_id)));
      return { id: claim.claim_id, text: localizedClaimText(claim.text, block, artifacts.get(references[0]?.artifact_id)), block, sectionTitle: section.title, references, status: claimStatus(claim), score: claim.verification?.support_score, method: claim.verification?.verification_method, legacy: false };
    };
    if (realClaims.length) return realClaims.map(fromClaim).filter(claim => claim.references.length);
    if (!block.evidence.length) return [];
    const references = block.evidence.filter(reference => !isPreparationArtifact(artifacts.get(reference.artifact_id)));
    return references.length ? [{ id: `bound:${block.block_id}`, text: localizedClaimText(legacyClaimText(block), block, artifacts.get(references[0]?.artifact_id)), block, sectionTitle: section.title, references, status: 'bound', legacy: true }] : [];
  })), [run, artifacts]);
  const [filter, setFilter] = useState<'all' | 'verified' | 'sql' | 'evidence' | 'claim'>('all');
  const [selectedClaimId, setSelectedClaimId] = useState<string>();
  const [selectedArtifactId, setSelectedArtifactId] = useState<string>();
  const [drawerArtifact, setDrawerArtifact] = useState<ReportArtifact>();
  const [showTechnicalDetail, setShowTechnicalDetail] = useState(false);
  useEffect(() => { setSelectedClaimId(current => claims.some(claim => claim.id === current) ? current : claims[0]?.id); setSelectedArtifactId(undefined); }, [claims]);
  const visibleClaims = filter === 'verified' ? claims.filter(claim => claim.status === 'verified') : claims;
  const selectedClaim = claims.find(claim => claim.id === selectedClaimId) || visibleClaims[0] || claims[0];
  const evidenceArtifacts = useMemo(() => (selectedClaim?.references || []).map(reference => ({ reference, artifact: artifacts.get(reference.artifact_id) })).filter((item): item is { reference: EvidenceReference; artifact: ReportArtifact } => Boolean(item.artifact)), [selectedClaim, artifacts]);
  const selectedArtifactGroups = useMemo(() => {
    const groups = new Map<string, ReportArtifact[]>();
    for (const { artifact } of evidenceArtifacts) {
      const key = artifactDisplayName(artifact);
      groups.set(key, [...(groups.get(key) || []), artifact]);
    }
    return groups;
  }, [evidenceArtifacts]);
  const graphArtifacts = useMemo(() => showTechnicalDetail ? [...new Map(claims.flatMap(claim => claim.references.map(reference => [reference.artifact_id, artifacts.get(reference.artifact_id)] as const)).filter((entry): entry is readonly [string, ReportArtifact] => Boolean(entry[1]))).values()] : [...selectedArtifactGroups.values()].map(group => group[0]), [showTechnicalDetail, claims, artifacts, selectedArtifactGroups]);
  const boundArtifactIds = new Set(claims.flatMap(claim => claim.references.map(reference => reference.artifact_id)));
  const datasets = [...new Set(graphArtifacts.flatMap(tablesReadBy))];
  const sources = [...new Set(graphArtifacts.map(recordedSource).filter((source): source is string => Boolean(source)))];
  const isArtifactVisible = (artifact: ReportArtifact) => !selectedArtifactId || selectedArtifactId === artifact.artifact_id;
  useLayoutEffect(() => {
    const svg = document.querySelector<SVGSVGElement>('svg[viewBox="0 0 1040 620"]');
    if (!svg || !svg.parentElement) return undefined;
    const canvas = svg.parentElement;
    const findNode = (label: string, kind: string) => [...canvas.querySelectorAll<HTMLButtonElement>('button')].find(button => {
      const text = button.textContent || '';
      return text.includes(label) && text.includes(kind);
    });
    const render = () => {
      const rect = canvas.getBoundingClientRect();
      if (!rect.width || !rect.height) return;
      svg.replaceChildren();
      svg.setAttribute('viewBox', `0 0 ${rect.width} ${rect.height}`);
      svg.setAttribute('preserveAspectRatio', 'none');
      const defs = document.createElementNS('http://www.w3.org/2000/svg', 'defs');
      const marker = document.createElementNS('http://www.w3.org/2000/svg', 'marker');
      marker.setAttribute('id', 'evidence-arrow'); marker.setAttribute('markerWidth', '8'); marker.setAttribute('markerHeight', '8'); marker.setAttribute('refX', '7'); marker.setAttribute('refY', '4'); marker.setAttribute('orient', 'auto');
      const arrow = document.createElementNS('http://www.w3.org/2000/svg', 'path'); arrow.setAttribute('d', 'M0,0 L0,8 L8,4 z'); arrow.setAttribute('fill', '#64748b'); marker.appendChild(arrow); defs.appendChild(marker); svg.appendChild(defs);
      const draw = (from: HTMLElement | undefined, to: HTMLElement | undefined, color: string) => {
        if (!from || !to) return;
        const a = from.getBoundingClientRect(); const b = to.getBoundingClientRect();
        const x1 = a.right - rect.left; const y1 = a.top + a.height / 2 - rect.top;
        const x2 = b.left - rect.left; const y2 = b.top + b.height / 2 - rect.top;
        if (x2 <= x1) return;
        const path = document.createElementNS('http://www.w3.org/2000/svg', 'path');
        path.setAttribute('d', `M ${x1} ${y1} C ${x1 + 28} ${y1}, ${x2 - 28} ${y2}, ${x2} ${y2}`);
        path.setAttribute('fill', 'none'); path.setAttribute('stroke', color); path.setAttribute('stroke-width', '2.5'); path.setAttribute('marker-end', 'url(#evidence-arrow)'); svg.appendChild(path);
      };
      for (const artifact of graphArtifacts) {
        const label = artifactDisplayName(artifact);
        const sameLabelCount = graphArtifacts.filter(candidate => artifactDisplayName(candidate) === label).length;
        if (sameLabelCount !== 1) continue;
        const computation = findNode(label, artifact.type === 'sql' ? 'SQL 查询' : artifact.type === 'python' ? 'Python 计算' : '计算');
        const evidence = findNode(label, '结果证据');
        draw(computation, evidence, '#3b82f6');
        for (const claim of visibleClaims.filter(candidate => candidate.references.some(reference => reference.artifact_id === artifact.artifact_id))) draw(evidence, findNode(claim.text, '关联证据'), '#7c3aed');
        for (const table of tablesReadBy(artifact)) draw(findNode(table, '数据表'), computation, '#0d9488');
        const source = recordedSource(artifact);
        if (source) for (const table of tablesReadBy(artifact)) draw(findNode(source, '数据源'), findNode(table, '数据表'), '#2563eb');
      }
    };
    const observer = new ResizeObserver(render); observer.observe(canvas); window.addEventListener('resize', render); requestAnimationFrame(render);
    return () => { observer.disconnect(); window.removeEventListener('resize', render); };
  }, [graphArtifacts, visibleClaims]);
  const Node: React.FC<{ kind: string; icon: string; title: string; detail?: string; active?: boolean; className?: string; onClick?: () => void }> = ({ kind, icon, title, detail, active, className = '', onClick }) => <button type='button' onClick={onClick} className={`w-full rounded-xl border p-3 text-left transition ${active ? 'border-blue-500 bg-blue-50 shadow-[0_5px_16px_rgba(59,130,246,.15)] dark:border-blue-400 dark:bg-blue-500/10' : 'border-slate-200 bg-white hover:border-blue-300 dark:border-slate-700 dark:bg-[#191a1f]'} ${className}`}><div className='flex items-start gap-2'><span className='grid h-7 w-7 shrink-0 place-items-center rounded-lg bg-slate-100 text-sm dark:bg-white/10'>{icon}</span><div className='min-w-0'><span className='text-[10px] font-bold tracking-wide text-slate-500'>{kind}</span><p className='mt-0.5 break-words text-sm font-semibold text-slate-800 dark:text-slate-100'>{title}</p>{detail && <p className='mt-1 line-clamp-2 text-[11px] leading-4 text-slate-500'>{detail}</p>}</div></div></button>;
  if (!claims.length) return <div className='rounded-xl border border-amber-200 bg-amber-50 p-5 text-sm text-amber-900'>本轮没有已绑定证据的报告结论。已捕获的执行产物仍可在“轨迹”和“任务文件”中查看；重新执行可生成带证据绑定的 ReportRun。</div>;
  const columnTitle = (order: string, title: string, dot: string) => <div className='mb-4 flex items-center gap-2 border-b border-slate-100 pb-3 dark:border-slate-800'><span className={`grid h-5 w-5 place-items-center rounded-full border text-[10px] font-bold ${dot}`}>{order}</span><h3 className='text-sm font-bold text-slate-800 dark:text-slate-100'>{title}</h3></div>;
  const shouldShow = (kind: 'sql' | 'evidence' | 'claim') => filter === 'all' || filter === kind || filter === 'verified';
  return <div className='space-y-5'><header className='rounded-2xl border border-violet-100 bg-gradient-to-r from-violet-50 via-white to-violet-50 px-6 py-6 dark:border-violet-900/40 dark:from-violet-950/20 dark:via-[#191a1f] dark:to-violet-950/20'><div className='flex items-center gap-4'><div className='grid h-14 w-14 place-items-center rounded-2xl bg-violet-100 text-3xl text-violet-600 dark:bg-violet-500/15'>⌘</div><div><h2 className='text-2xl font-bold text-slate-900 dark:text-white'>报告证据图谱</h2><p className='mt-1 text-sm text-slate-600 dark:text-slate-300'>默认按业务结论聚合；需要审计时再展开 SQL、Python、图表等技术细节。</p></div><span className='ml-auto hidden rounded-lg border border-violet-200 bg-white px-3 py-1.5 text-xs font-semibold text-violet-700 sm:block dark:border-violet-500/30 dark:bg-white/5 dark:text-violet-300'>{showTechnicalDetail ? `技术明细 ${boundArtifactIds.size} 个` : `业务结论 ${claims.length} 条`}</span></div></header>
    <div className='flex flex-wrap items-center justify-between gap-3'><div className='flex flex-wrap gap-2'>{([['all', '全部'], ['verified', '仅已验证'], ['sql', 'SQL'], ['evidence', '证据'], ['claim', '结论']] as const).map(([item, label]) => <button key={item} type='button' onClick={() => setFilter(item)} className={`rounded-md border px-3 py-1.5 text-xs font-medium transition ${filter === item ? 'border-violet-400 bg-violet-50 text-violet-700 dark:bg-violet-500/15 dark:text-violet-200' : 'border-slate-200 bg-white text-slate-600 hover:border-violet-200 dark:border-slate-700 dark:bg-[#191a1f] dark:text-slate-300'}`}>{label}</button>)}<button type='button' onClick={() => setShowTechnicalDetail(current => !current)} className={`rounded-md border px-3 py-1.5 text-xs font-medium transition ${showTechnicalDetail ? 'border-slate-700 bg-slate-800 text-white dark:border-slate-300 dark:bg-slate-100 dark:text-slate-900' : 'border-slate-200 bg-white text-slate-600 hover:border-violet-200 dark:border-slate-700 dark:bg-[#191a1f] dark:text-slate-300'}`}>{showTechnicalDetail ? '收起技术细节' : '展开技术细节'}</button></div><div className='flex flex-wrap items-center gap-3 text-[11px] text-slate-500'><span>● 数据源</span><span className='text-teal-600'>● 数据表 / 数据集</span><span className='text-orange-500'>● SQL / 计算</span><span className='text-blue-600'>● 证据</span><span className='text-violet-600'>● 结论</span></div></div>
    <div className='grid grid-cols-1 gap-5 xl:grid-cols-[minmax(0,1fr)_300px]'><section className='relative min-w-0 overflow-hidden rounded-2xl border border-slate-200 bg-white dark:border-slate-700 dark:bg-[#191a1f]'><svg aria-hidden='true' className='pointer-events-none absolute left-0 top-16 hidden h-[calc(100%-4rem)] w-full xl:block' viewBox='0 0 1040 620' preserveAspectRatio='none'><defs><marker id='evidence-arrow' markerWidth='7' markerHeight='7' refX='6' refY='3.5' orient='auto'><path d='M0,0 L0,7 L7,3.5 z' fill='#94a3b8' /></marker></defs>{graphArtifacts.slice(0, 6).map((artifact, index) => { const y = 85 + index * 78; const active = isArtifactVisible(artifact); const stroke = active ? '#3b82f6' : '#cbd5e1'; const hasTables = tablesReadBy(artifact).length > 0; const hasSource = Boolean(recordedSource(artifact)); const supportsSelectedClaim = Boolean(selectedClaim?.references.some(reference => reference.artifact_id === artifact.artifact_id)); return <g key={`path-${artifact.artifact_id}`} opacity={active ? 1 : .25}>{hasSource && hasTables && <path d={`M88 ${y} C145 ${y}, 150 ${y}, 204 ${y}`} fill='none' stroke={stroke} strokeWidth='1.6' markerEnd='url(#evidence-arrow)' />}{hasTables && <path d={`M296 ${y} C355 ${y}, 364 ${y}, 416 ${y}`} fill='none' stroke={stroke} strokeWidth='1.6' markerEnd='url(#evidence-arrow)' />}<path d={`M504 ${y} C565 ${y}, 574 ${y}, 624 ${y}`} fill='none' stroke={stroke} strokeWidth='1.6' markerEnd='url(#evidence-arrow)' />{supportsSelectedClaim && <path d={`M712 ${y} C774 ${y}, 782 ${y}, 832 ${y}`} fill='none' stroke='#7c3aed' strokeWidth='2.4' markerEnd='url(#evidence-arrow)' />}</g>; })}</svg><div className='relative z-10 grid min-w-0 grid-cols-5 divide-x divide-slate-100 dark:divide-slate-800'><div className='min-w-0 p-3'>{columnTitle('1', '数据源', 'border-blue-300 text-blue-600')}{sources.length ? sources.map(source => <Node key={source} kind='数据源' icon='◉' title={source} detail='执行时记录的数据来源' className='border-blue-200 dark:border-blue-500/30' active={graphArtifacts.some(artifact => isArtifactVisible(artifact) && recordedSource(artifact) === source)} onClick={() => setSelectedArtifactId(undefined)} />) : <p className='rounded-lg border border-dashed border-slate-200 p-2 text-[11px] leading-5 text-slate-400 dark:border-slate-700'>未捕获来源元数据。</p>}</div><div className='min-w-0 p-3'>{columnTitle('2', '数据表 / 数据集', 'border-teal-300 text-teal-600')}<div className='space-y-3'>{datasets.length ? datasets.map(dataset => <Node key={dataset} kind='数据表' icon='▦' title={dataset} detail='执行事件中记录的输入表' className='border-teal-200 dark:border-teal-500/30' active={graphArtifacts.some(artifact => isArtifactVisible(artifact) && tablesReadBy(artifact).includes(dataset))} onClick={() => setSelectedArtifactId(undefined)} />) : <p className='rounded-lg border border-dashed border-slate-200 p-2 text-[11px] leading-5 text-slate-400 dark:border-slate-700'>未捕获输入表血缘。</p>}</div></div><div className='min-w-0 p-3'>{columnTitle('3', 'SQL / 计算', 'border-orange-300 text-orange-600')}<div className='space-y-3'>{shouldShow('sql') && graphArtifacts.map(artifact => <Node key={artifact.artifact_id} kind={artifactKind(artifact) === 'SQL' ? 'SQL 查询' : '计算'} icon='‹/›' title={artifactLabel(artifact)} detail={(artifact.sql || artifact.code || artifact.excerpt || '已捕获的执行计算').split('\n')[0]} active={isArtifactVisible(artifact)} className='border-orange-200 dark:border-orange-500/30' onClick={() => { setSelectedArtifactId(artifact.artifact_id); setDrawerArtifact(artifact); }} />)}</div></div><div className='min-w-0 p-3'>{columnTitle('4', '证据', 'border-blue-300 text-blue-600')}<div className='space-y-3'>{shouldShow('evidence') && graphArtifacts.map(artifact => { const snapshot = tableData(artifact); const firstValue = snapshot.rows[0]?.[0]; return <Node key={`evidence-${artifact.artifact_id}`} kind='结果证据' icon='▥' title={artifactLabel(artifact)} detail={firstValue === undefined ? (artifact.result?.inline ? '已保存真实结果快照' : '已绑定执行证据') : `结果快照：${display(firstValue)}`} active={isArtifactVisible(artifact)} className='border-blue-200 dark:border-blue-500/30' onClick={() => { setSelectedArtifactId(artifact.artifact_id); setDrawerArtifact(artifact); }} />; })}</div></div><div className='min-w-0 p-3'>{columnTitle('5', '报告结论', 'border-violet-300 text-violet-600')}<div className='space-y-3'>{shouldShow('claim') && visibleClaims.map(claim => <button key={claim.id} type='button' onClick={() => { setSelectedClaimId(claim.id); setSelectedArtifactId(undefined); }} className={`w-full rounded-xl border p-3 text-left transition ${selectedClaim?.id === claim.id ? 'border-violet-600 bg-violet-50 shadow-[0_5px_16px_rgba(124,58,237,.16)] dark:bg-violet-500/10' : 'border-violet-200 bg-white hover:border-violet-400 dark:border-violet-500/30 dark:bg-[#191a1f]'}`}><div className='flex items-center justify-between gap-2'><span className='text-lg'>♛</span><span className={`rounded-full border px-1.5 py-0.5 text-[10px] font-semibold ${statusStyle(claim.status)}`}>{statusLabel(claim.status)}</span></div><p className='mt-2 line-clamp-3 break-words text-sm font-semibold leading-5 text-slate-800 dark:text-slate-100'>{claim.text}</p><p className='mt-2 text-[11px] text-slate-500'>{claim.references.length} 条关联证据</p></button>)}</div></div></div></section>
      {selectedClaim && <aside className='rounded-2xl border border-slate-200 bg-white dark:border-slate-700 dark:bg-[#191a1f]'><header className='border-b border-slate-100 px-5 py-4 dark:border-slate-800'><div className='flex items-center justify-between'><h3 className='font-bold text-slate-900 dark:text-white'>结论详情</h3><button type='button' onClick={() => setSelectedClaimId(undefined)} className='text-lg text-slate-400 hover:text-slate-700'>×</button></div></header><div className='space-y-5 p-5'><section><p className='text-xs font-semibold text-violet-600'>结论内容</p><div className='mt-3 flex gap-3'><span className='grid h-9 w-9 shrink-0 place-items-center rounded-lg bg-violet-100 text-violet-600 dark:bg-violet-500/15'>♛</span><p className='text-sm font-semibold leading-6 text-slate-800 dark:text-slate-100'>{selectedClaim.text}</p></div></section><section className='border-t border-slate-100 pt-4 dark:border-slate-800'><p className='text-xs text-slate-500'>验证状态</p><div className='mt-2 flex items-center gap-2'><span className={`rounded-full border px-2 py-1 text-xs font-semibold ${statusStyle(selectedClaim.status)}`}>{statusLabel(selectedClaim.status)}</span>{selectedClaim.score !== undefined && <span className='text-xs text-slate-500'>支持度 {Math.round(selectedClaim.score * 100)}%</span>}</div></section><section className='border-t border-slate-100 pt-4 dark:border-slate-800'><p className='text-xs font-semibold text-violet-600'>关联证据</p><div className='mt-3 space-y-2'>{evidenceArtifacts.map(({ artifact }) => <button key={artifact.artifact_id} type='button' onClick={() => { setSelectedArtifactId(artifact.artifact_id); setDrawerArtifact(artifact); }} className={`w-full rounded-lg border p-3 text-left ${selectedArtifactId === artifact.artifact_id ? 'border-blue-500 bg-blue-50 dark:bg-blue-500/10' : 'border-slate-200 hover:border-blue-300 dark:border-slate-700'}`}><span className='text-[10px] font-bold text-blue-600'>{artifactKind(artifact)}</span><p className='mt-1 text-xs font-semibold text-slate-800 dark:text-slate-100'>{artifactLabel(artifact)}</p></button>)}</div></section><section className='border-t border-slate-100 pt-4 dark:border-slate-800'><p className='text-xs font-semibold text-violet-600'>来源数据表</p><div className='mt-3 space-y-2'>{[...new Set(evidenceArtifacts.flatMap(({ artifact }) => tablesReadBy(artifact)))].map(table => <div key={table} className='rounded-lg border border-teal-100 bg-teal-50 px-3 py-2 text-xs font-medium text-teal-800 dark:border-teal-500/20 dark:bg-teal-500/10 dark:text-teal-200'>▦ {table}</div>)}</div></section><button type='button' disabled={!evidenceArtifacts[0]} onClick={() => evidenceArtifacts[0] && setDrawerArtifact(evidenceArtifacts[0].artifact)} className='w-full rounded-lg border border-slate-200 py-2.5 text-xs font-medium text-slate-700 hover:border-violet-300 disabled:opacity-40 dark:border-slate-700 dark:text-slate-200'>查看执行详情 ↗</button>{selectedClaim.legacy && <p className='rounded-lg bg-blue-50 p-3 text-[11px] leading-5 text-blue-800 dark:bg-blue-500/10 dark:text-blue-200'>此 Run 为旧版绑定证据：已展示真实链路，但没有独立 Claim 评分。</p>}</div></aside>}</div>
    <p className='text-xs text-slate-400'>仅将被报告结论引用的产物纳入图谱（{boundArtifactIds.size} 个）；其余 {Math.max(0, run.document.artifacts.length - boundArtifactIds.size)} 个执行过程产物继续保留在“轨迹”中。</p><EvidenceDrawer artifact={drawerArtifact} onClose={() => setDrawerArtifact(undefined)} onArtifactFocus={onArtifactFocus} />
  </div>;
};

const EvidenceGraph: React.FC<{ run: ReportRun; onArtifactFocus?: (stepId: string) => void }> = ({ run, onArtifactFocus }) => <ClaimEvidenceGraph run={run} onArtifactFocus={onArtifactFocus} />;

export const TraceableReportWorkspace: React.FC<TraceableReportWorkspaceProps> = ({ reportId, reportRunId, view, onArtifactFocus }) => {
  const [run, setRun] = useState<ReportRun>(); const [loading, setLoading] = useState(true); const [error, setError] = useState('');
  const load = useCallback(async () => { setLoading(true); setError(''); try { let response: Awaited<ReturnType<typeof getStandaloneReportRun>> | undefined; let lastError: unknown; for (let attempt = 0; attempt < 4; attempt += 1) { try { response = await getStandaloneReportRun(reportId, reportRunId); if (response.success) break; lastError = new Error(response.err_msg || 'Report not available'); } catch (reason) { lastError = reason; } await new Promise(resolve => window.setTimeout(resolve, 750)); } if (!response?.success) throw lastError || new Error('Report not available'); setRun(response.data); } catch (reason) { setError(reason instanceof Error ? reason.message : '无法加载本轮可追溯报告'); } finally { setLoading(false); } }, [reportId, reportRunId]);
  useEffect(() => { load(); }, [load]);
  if (loading) return <div className='grid h-full min-h-[240px] place-items-center text-sm text-slate-500'>正在加载本轮分析产物与证据…</div>;
  if (error || !run) return <div className='m-5 rounded-lg border border-amber-200 bg-amber-50 p-4 text-sm text-amber-800'>报告暂不可用：{error || '未知错误'} <button type='button' onClick={load} className='ml-2 underline'>重试</button></div>;
  return view === 'evidence' ? <EvidenceGraph run={run} onArtifactFocus={onArtifactFocus} /> : <ReportDashboard run={run} onArtifactFocus={onArtifactFocus} />;
};

export default TraceableReportWorkspace;
