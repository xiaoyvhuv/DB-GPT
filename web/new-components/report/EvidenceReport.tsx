import { getStandaloneBlockEvidence } from '@/client/api/report/standalone';
import type { ReportArtifact, ReportBlock, ReportEvidenceDetail, ReportRun } from '@/types/report';
import React, { useMemo, useState } from 'react';

const MAX_VALUE_CHARS = 2_000;
const bounded = (value: unknown) => {
  if (value === null || value === undefined) return '';
  if (typeof value === 'string') return value.slice(0, MAX_VALUE_CHARS);
  if (typeof value === 'number' || typeof value === 'boolean') return String(value);
  try { return JSON.stringify(value).slice(0, MAX_VALUE_CHARS); } catch { return String(value).slice(0, MAX_VALUE_CHARS); }
};

const tableData = (artifact?: ReportArtifact): { columns: string[]; rows: unknown[][] } => {
  const inline = artifact?.result?.inline;
  if (inline && typeof inline === 'object' && !Array.isArray(inline)) {
    const data = inline as { columns?: unknown; rows?: unknown };
    if (Array.isArray(data.columns) && Array.isArray(data.rows)) {
      const columns = data.columns.slice(0, 25).map(bounded);
      return { columns, rows: data.rows.slice(0, 50).filter(Array.isArray).map(row => (row as unknown[]).slice(0, columns.length)) };
    }
  }
  return { columns: [], rows: [] };
};

export const EvidenceReport: React.FC<{ run: ReportRun }> = ({ run }) => {
  const [evidence, setEvidence] = useState<ReportEvidenceDetail[]>([]);
  const [selectedTitle, setSelectedTitle] = useState('');
  const [loading, setLoading] = useState(false);
  const artifacts = useMemo(() => new Map(run.document.artifacts.slice(0, 500).map(artifact => [artifact.artifact_id, artifact])), [run.document.artifacts]);

  const inspect = async (block: ReportBlock) => {
    if (!block.evidence.length) return;
    setSelectedTitle(block.title || block.type);
    setLoading(true);
    setEvidence([]);
    try {
      const response = await getStandaloneBlockEvidence(run.report_id, run.run_id, block.block_id);
      setEvidence(response.success ? response.data.slice(0, 50) : []);
    } finally { setLoading(false); }
  };

  const renderBlock = (block: ReportBlock) => {
    const inspectable = ['metric', 'chart', 'insight'].includes(block.type) && block.evidence.length > 0;
    const className = `rounded-xl border border-slate-200 bg-white p-5 shadow-sm ${inspectable ? 'cursor-pointer hover:border-blue-400' : ''} ${['table', 'chart', 'insight', 'text'].includes(block.type) ? 'md:col-span-2 xl:col-span-3' : ''}`;
    let content: React.ReactNode;
    if (block.type === 'metric') content = <p className='mt-3 text-3xl font-bold text-slate-900'>{bounded(block.display_value ?? block.value)} {bounded(block.unit)}</p>;
    else if (block.type === 'table' && block.data_artifact_id) {
      const data = tableData(artifacts.get(block.data_artifact_id));
      content = data.columns.length ? <div className='mt-3 overflow-auto'><table className='min-w-full text-left text-sm'><thead><tr>{data.columns.map(column => <th className='border-b p-2 font-semibold' key={column}>{column}</th>)}</tr></thead><tbody>{data.rows.map((row, rowIndex) => <tr key={rowIndex}>{row.map((cell, cellIndex) => <td className='border-b p-2' key={cellIndex}>{bounded(cell)}</td>)}</tr>)}</tbody></table></div> : <p className='mt-3 text-slate-500'>No inline table data for artifact {block.data_artifact_id}</p>;
    } else if (block.type === 'chart' && block.chart_artifact_id) {
      const artifact = artifacts.get(block.chart_artifact_id);
      content = <pre className='mt-3 max-h-80 overflow-auto rounded-lg bg-slate-50 p-3 text-xs'>{bounded(artifact?.output?.inline ?? artifact?.spec ?? {})}</pre>;
    } else content = <p className='mt-3 whitespace-pre-wrap leading-7 text-slate-700'>{bounded(block.content || block.description)}</p>;
    return <article className={className} key={block.block_id} onClick={inspectable ? () => inspect(block) : undefined}><div className='flex items-center justify-between gap-4'><h3 className='font-semibold text-slate-900'>{bounded(block.title || block.type)}</h3>{inspectable && <span className='rounded-full bg-blue-50 px-2 py-1 text-xs text-blue-700'>{block.evidence.length} evidence</span>}</div>{content}</article>;
  };

  return <div className='space-y-8'>
    {run.document.sections.slice(0, 50).map(section => <section key={section.section_id}><h2 className='mb-4 text-xl font-bold text-slate-900'>{bounded(section.title)}</h2><div className='grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3'>{section.blocks.slice(0, 100).map(renderBlock)}</div></section>)}
    {selectedTitle && <div className='fixed inset-y-0 right-0 z-50 w-full max-w-xl overflow-y-auto border-l border-slate-200 bg-white p-6 shadow-2xl'><button className='float-right rounded px-3 py-1 text-slate-500 hover:bg-slate-100' onClick={() => setSelectedTitle('')}>Close</button><h2 className='mb-6 text-xl font-bold'>Evidence details · {selectedTitle}</h2>{loading && <p>Loading evidence…</p>}{!loading && !evidence.length && <p className='text-slate-500'>No evidence bound to this block.</p>}<div className='space-y-4'>{evidence.map(item => <article className='rounded-lg border border-slate-200 p-4' key={`${item.ordinal}-${item.artifact.artifact_id}`}><h3 className='font-semibold'>{bounded(item.artifact.name || item.artifact.source_name || item.artifact.artifact_id)}</h3><dl className='mt-3 grid grid-cols-[100px_1fr] gap-y-2 text-sm'><dt>Role</dt><dd>{item.evidence.role}</dd><dt>Artifact ID</dt><dd className='break-all'>{item.artifact.artifact_id}</dd><dt>Status</dt><dd>{item.artifact.status || 'legacy'}</dd><dt>Action</dt><dd>{item.artifact.execution.action || '-'}</dd><dt>Duration</dt><dd>{item.artifact.duration_ms ?? '-'} ms</dd></dl>{item.artifact.excerpt && <p className='mt-3 text-sm'>{bounded(item.artifact.excerpt)}</p>}{item.artifact.sql && <><h4 className='mt-4 font-medium'>Executed SQL</h4><pre className='mt-2 overflow-auto rounded bg-slate-950 p-3 text-xs text-slate-50'>{bounded(item.artifact.sql)}</pre></>}{item.artifact.code && <><h4 className='mt-4 font-medium'>Executed {item.artifact.language || 'Python'}</h4><pre className='mt-2 overflow-auto rounded bg-slate-950 p-3 text-xs text-slate-50'>{bounded(item.artifact.code)}</pre></>}</article>)}</div></div>}
  </div>;
};

export default EvidenceReport;
