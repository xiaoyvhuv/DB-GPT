import { getStandaloneReportRun } from '@/client/api/report/standalone';
import type { EvidenceReference, ReportArtifact, ReportBlock, ReportRun } from '@/types/report';
import React, { useCallback, useEffect, useMemo, useState } from 'react';

export type TraceableReportView = 'report' | 'evidence';

interface TraceableReportWorkspaceProps {
  reportId: string;
  reportRunId: string;
  view: TraceableReportView;
  onArtifactFocus?: (stepId: string) => void;
}

type GraphColumn = 'source' | 'artifact' | 'block';
type GraphNode = { id: string; label: string; kind: string; column: GraphColumn; artifact?: ReportArtifact; block?: ReportBlock };
type GraphEdge = { sourceId: string; artifactId: string; blockId?: string };

const MAX_CHARS = 1_500;
const display = (value: unknown) => {
  if (value === null || value === undefined) return '';
  if (typeof value === 'string') return value.slice(0, MAX_CHARS);
  if (typeof value === 'number' || typeof value === 'boolean') return String(value);
  try { return JSON.stringify(value, null, 2).slice(0, MAX_CHARS); } catch { return String(value).slice(0, MAX_CHARS); }
};

const artifactLabel = (artifact: ReportArtifact) => artifact.name || artifact.source_name || artifact.execution.action || artifact.artifact_id;
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

const tableData = (artifact?: ReportArtifact): { columns: string[]; rows: unknown[][] } => {
  const inline = artifact?.result?.inline;
  if (inline && typeof inline === 'object' && !Array.isArray(inline)) {
    const payload = inline as { columns?: unknown; rows?: unknown };
    if (Array.isArray(payload.columns) && Array.isArray(payload.rows)) return { columns: payload.columns.slice(0, 8).map(String), rows: payload.rows.slice(0, 8).filter(Array.isArray).map(row => (row as unknown[]).slice(0, 8)) };
  }
  return { columns: [], rows: [] };
};

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

const ReportBlockCard: React.FC<{ block: ReportBlock; artifacts: Map<string, ReportArtifact>; onSelect: (artifact: ReportArtifact, reference: EvidenceReference, block: ReportBlock) => void }> = ({ block, artifacts, onSelect }) => {
  const resultArtifact = block.type === 'table' ? artifacts.get(block.data_artifact_id || '') : block.type === 'chart' ? artifacts.get(block.chart_artifact_id || '') : undefined;
  const data = tableData(resultArtifact); const metric = block.type === 'metric';
  return <article className={`rounded-xl border border-slate-200 bg-white p-5 shadow-sm transition hover:border-blue-300 dark:border-slate-700 dark:bg-[#191a1f] ${metric ? '' : 'md:col-span-2'}`}><div className='flex flex-wrap items-start justify-between gap-3'><h3 className='text-sm font-semibold text-slate-800 dark:text-slate-100'>{block.title || block.type}</h3><div className='flex flex-wrap gap-1'>{block.evidence.map(reference => <EvidenceTag key={`${block.block_id}-${reference.artifact_id}`} reference={reference} artifact={artifacts.get(reference.artifact_id)} onClick={() => { const artifact = artifacts.get(reference.artifact_id); if (artifact) onSelect(artifact, reference, block); }} />)}</div></div>
    {metric && <p className='mt-5 text-3xl font-bold tracking-tight text-slate-900 dark:text-white'>{display(block.display_value ?? block.value)} <span className='text-base font-medium text-slate-500'>{display(block.unit)}</span></p>}
    {block.type === 'table' && (data.columns.length ? <div className='mt-4 overflow-auto'><table className='min-w-full text-left text-xs'><thead><tr>{data.columns.map(column => <th className='border-b bg-slate-50 px-2 py-2 font-medium dark:bg-white/[0.04]' key={column}>{column}</th>)}</tr></thead><tbody>{data.rows.map((row, index) => <tr key={index}>{row.map((cell, cellIndex) => <td className='border-b px-2 py-2' key={cellIndex}>{display(cell)}</td>)}</tr>)}</tbody></table></div> : <p className='mt-4 text-sm text-slate-500'>暂无可展示的结果快照</p>)}
    {block.type === 'chart' && <div className='mt-4 rounded-lg border border-dashed border-blue-200 bg-blue-50/40 p-5 text-sm text-blue-800 dark:border-blue-500/20 dark:bg-blue-500/10 dark:text-blue-200'><strong>图表产物</strong><p className='mt-1 text-xs opacity-75'>{resultArtifact?.chart_type || 'Chart'} · 点击证据标签查看图表配置、数据来源和执行步骤。</p></div>}
    {(block.type === 'insight' || block.type === 'text') && <p className='mt-4 whitespace-pre-wrap text-sm leading-7 text-slate-700 dark:text-slate-300'>{display(block.content || block.description)}</p>}
  </article>;
};

const ReportDashboard: React.FC<{ run: ReportRun; onArtifactFocus?: (stepId: string) => void }> = ({ run, onArtifactFocus }) => {
  const [selected, setSelected] = useState<{ artifact: ReportArtifact; reference: EvidenceReference; block: ReportBlock }>(); const artifacts = useMemo(() => new Map(run.document.artifacts.map(artifact => [artifact.artifact_id, artifact])), [run]); const sections = run.document.sections.filter(section => section.blocks.some(block => block.evidence.length > 0));
  if (!sections.length) return <div className='rounded-xl border border-amber-200 bg-amber-50 p-5 text-sm text-amber-900'>这是旧版未绑定摘要 Run：已保存 {run.document.artifacts.length} 个执行产物，但报告块尚未建立证据关联。请重新执行任务以生成可追溯分析报告。</div>;
  return <div className='space-y-8'><header className='rounded-2xl bg-gradient-to-br from-[#162582] to-[#4338a3] px-6 py-7 text-white shadow-sm'><p className='text-xs font-semibold uppercase tracking-[0.18em] text-blue-200'>Traceable Report · Schema v{run.schema_version}</p><h1 className='mt-3 text-2xl font-bold leading-9'>{run.document.title}</h1><p className='mt-3 text-sm text-blue-100'>真实执行产物 {run.document.artifacts.length} 个 · ReportRun Revision {run.revision}</p></header>{sections.map(section => <section key={section.section_id}><div className='mb-3 flex items-center justify-between'><h2 className='border-l-4 border-blue-600 pl-3 text-lg font-bold text-slate-900 dark:text-white'>{section.title}</h2><span className='text-xs text-slate-400'>{section.blocks.length} 个报告块</span></div><div className='grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3'>{section.blocks.map(block => <ReportBlockCard key={block.block_id} block={block} artifacts={artifacts} onSelect={(artifact, reference, selectedBlock) => setSelected({ artifact, reference, block: selectedBlock })} />)}</div></section>)}<EvidenceDrawer artifact={selected?.artifact} reference={selected?.reference} block={selected?.block} onClose={() => setSelected(undefined)} onArtifactFocus={onArtifactFocus} /></div>;
};

const EvidenceGraph: React.FC<{ run: ReportRun; onArtifactFocus?: (stepId: string) => void }> = ({ run, onArtifactFocus }) => {
  const [selectedId, setSelectedId] = useState<string>(); const [selectedArtifact, setSelectedArtifact] = useState<ReportArtifact>();
  const graph = useMemo(() => { const sources = new Map<string, GraphNode>(); const artifacts = new Map<string, GraphNode>(); const blocks = new Map<string, GraphNode>(); const edges: GraphEdge[] = []; for (const artifact of run.document.artifacts) { const sourceId = `source:${artifactSource(artifact)}`; sources.set(sourceId, { id: sourceId, label: artifactSource(artifact), kind: artifact.type === 'sql' ? 'DB' : artifactKind(artifact), column: 'source' }); artifacts.set(artifact.artifact_id, { id: artifact.artifact_id, label: artifactLabel(artifact), kind: artifactKind(artifact), column: 'artifact', artifact }); edges.push({ sourceId, artifactId: artifact.artifact_id }); for (const inputId of artifact.input_artifact_ids) edges.push({ sourceId: inputId, artifactId: artifact.artifact_id }); } for (const section of run.document.sections) for (const block of section.blocks) { if (!block.evidence.length) continue; blocks.set(block.block_id, { id: block.block_id, label: block.title || block.type, kind: block.type.toUpperCase(), column: 'block', block }); for (const reference of block.evidence) edges.push({ sourceId: reference.artifact_id, artifactId: reference.artifact_id, blockId: block.block_id }); } return { sources: [...sources.values()], artifacts: [...artifacts.values()], blocks: [...blocks.values()], edges }; }, [run]);
  const related = (node: GraphNode) => !selectedId || node.id === selectedId || graph.edges.some(edge => (edge.sourceId === selectedId && (edge.artifactId === node.id || edge.blockId === node.id)) || (edge.artifactId === selectedId && (edge.sourceId === node.id || edge.blockId === node.id)) || (edge.blockId === selectedId && edge.artifactId === node.id));
  const column = (title: string, nodes: GraphNode[]) => <section><h3 className='mb-3 text-xs font-semibold uppercase tracking-wider text-slate-500'>{title}</h3><div className='space-y-3'>{nodes.map(node => <button type='button' key={node.id} onClick={() => { setSelectedId(node.id); if (node.artifact) setSelectedArtifact(node.artifact); }} className={`w-full rounded-xl border p-3 text-left transition ${related(node) ? 'border-blue-200 bg-white shadow-sm dark:border-blue-500/40 dark:bg-[#191a1f]' : 'border-slate-100 bg-slate-50 opacity-40 dark:border-slate-800 dark:bg-white/[0.02]'}`}><span className='rounded bg-blue-50 px-1.5 py-0.5 text-[10px] font-bold text-blue-700 dark:bg-blue-500/10 dark:text-blue-300'>{node.kind}</span><p className='mt-2 text-sm font-medium text-slate-800 dark:text-slate-100'>{node.label}</p></button>)}</div></section>;
  return <div className='space-y-5'><header className='rounded-xl border border-violet-100 bg-violet-50 p-5 dark:border-violet-900/40 dark:bg-violet-950/20'><h2 className='text-xl font-bold text-violet-950 dark:text-violet-100'>报告证据图谱</h2><p className='mt-1 text-sm text-violet-700 dark:text-violet-300'>从真实数据源和执行产物，追踪到最终报告块。共 {graph.edges.length} 条关系。</p></header><div className='grid grid-cols-1 gap-5 lg:grid-cols-3'>{column('1 · 数据源', graph.sources)}{column('2 · 执行产物', graph.artifacts)}{column('3 · 报告内容', graph.blocks)}</div><EvidenceDrawer artifact={selectedArtifact} onClose={() => setSelectedArtifact(undefined)} onArtifactFocus={onArtifactFocus} /></div>;
};

export const TraceableReportWorkspace: React.FC<TraceableReportWorkspaceProps> = ({ reportId, reportRunId, view, onArtifactFocus }) => {
  const [run, setRun] = useState<ReportRun>(); const [loading, setLoading] = useState(true); const [error, setError] = useState('');
  const load = useCallback(async () => { setLoading(true); setError(''); try { let response: Awaited<ReturnType<typeof getStandaloneReportRun>> | undefined; let lastError: unknown; for (let attempt = 0; attempt < 4; attempt += 1) { try { response = await getStandaloneReportRun(reportId, reportRunId); if (response.success) break; lastError = new Error(response.err_msg || 'Report not available'); } catch (reason) { lastError = reason; } await new Promise(resolve => window.setTimeout(resolve, 750)); } if (!response?.success) throw lastError || new Error('Report not available'); setRun(response.data); } catch (reason) { setError(reason instanceof Error ? reason.message : '无法加载本轮可追溯报告'); } finally { setLoading(false); } }, [reportId, reportRunId]);
  useEffect(() => { load(); }, [load]);
  if (loading) return <div className='grid h-full min-h-[240px] place-items-center text-sm text-slate-500'>正在加载本轮分析产物与证据…</div>;
  if (error || !run) return <div className='m-5 rounded-lg border border-amber-200 bg-amber-50 p-4 text-sm text-amber-800'>报告暂不可用：{error || '未知错误'} <button type='button' onClick={load} className='ml-2 underline'>重试</button></div>;
  return view === 'evidence' ? <EvidenceGraph run={run} onArtifactFocus={onArtifactFocus} /> : <ReportDashboard run={run} onArtifactFocus={onArtifactFocus} />;
};

export default TraceableReportWorkspace;
