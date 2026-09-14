import { getStandaloneReportRun } from '@/client/api/report/standalone';
import { EvidenceReport } from '@/new-components/report/EvidenceReport';
import type { ReportArtifact, ReportRun } from '@/types/report';
import React, { useEffect, useMemo, useState } from 'react';

const RUN_ID = 'db147250-1766-495c-a11b-6db87c949f41';
const REPORT_ID = 'olist-demo-preview';

const labelFor = (artifact: ReportArtifact) => artifact.name || artifact.source_name || artifact.type.toUpperCase();

/** A data-assistant presentation backed by one persisted, real Olist report run. */
export default function OlistAssistantDemo() {
  const [run, setRun] = useState<ReportRun>();
  const [error, setError] = useState('');
  const [active, setActive] = useState<string>();
  useEffect(() => {
    getStandaloneReportRun(REPORT_ID, RUN_ID)
      .then(response => (response.success ? setRun(response.data) : setError(response.err_msg || 'Unable to load Olist report')))
      .catch(reason => setError(reason instanceof Error ? reason.message : 'Unable to load Olist report'));
  }, []);
  const artifacts = useMemo(() => run?.document.artifacts || [], [run]);
  if (error) return <main className='grid h-full place-items-center bg-slate-50 text-red-700'>{error}</main>;
  if (!run) return <main className='grid h-full place-items-center bg-slate-50 text-slate-600'>Loading real Olist analysis run…</main>;
  const selected = artifacts.find(artifact => artifact.artifact_id === active);
  return <div className='flex h-full min-h-0 bg-white text-slate-900'>
    <aside className='flex w-[360px] shrink-0 flex-col border-r border-slate-200 bg-slate-50'>
      <header className='border-b border-slate-200 bg-white px-5 py-4'><p className='text-xs font-semibold uppercase tracking-wide text-blue-600'>DB-GPT Data Assistant</p><h1 className='mt-1 text-lg font-bold'>Olist 电商经营分析</h1><p className='mt-1 text-sm text-slate-500'>真实运行：{run.run_id.slice(0, 8)} · 版本 {run.revision}</p></header>
      <div className='flex-1 overflow-y-auto p-4'><div className='mb-5 rounded-xl bg-white p-4 shadow-sm'><p className='text-xs text-slate-500'>用户问题</p><p className='mt-1 font-medium'>分析 Olist 巴西电商数据的 GMV、履约表现与客户评价，并给出可追溯结论。</p></div><p className='mb-3 text-xs font-semibold uppercase tracking-wide text-slate-500'>执行过程</p><ol className='space-y-3'>{artifacts.map((artifact, index) => <li key={artifact.artifact_id}><button onClick={() => setActive(artifact.artifact_id)} className={`flex w-full gap-3 rounded-xl border p-3 text-left transition ${active === artifact.artifact_id ? 'border-blue-400 bg-blue-50' : 'border-slate-200 bg-white hover:border-slate-300'}`}><span className='grid h-6 w-6 shrink-0 place-items-center rounded-full bg-slate-900 text-xs text-white'>{index + 1}</span><span><span className='block text-sm font-semibold'>{labelFor(artifact)}</span><span className='mt-1 block text-xs text-slate-500'>{artifact.type.toUpperCase()} · {artifact.status || 'captured'} · {artifact.duration_ms ?? '-'} ms</span></span></button></li>)}</ol></div>
      <footer className='border-t border-slate-200 bg-white p-4 text-xs text-slate-500'>数据源、SQL、计算结果和报告块均来自已保存的 Olist 真实运行。</footer>
    </aside>
    <section className='min-w-0 flex-1 overflow-y-auto bg-slate-100 p-6 lg:p-8'><div className='mx-auto max-w-6xl'>{selected && <div className='mb-5 rounded-xl border border-blue-200 bg-blue-50 p-4'><div className='flex items-center justify-between'><strong>已选执行产物：{labelFor(selected)}</strong><button className='text-sm text-blue-700' onClick={() => setActive(undefined)}>关闭</button></div><p className='mt-2 text-sm text-slate-600'>状态：{selected.status || 'captured'}；耗时：{selected.duration_ms ?? '-'} ms</p>{selected.sql && <pre className='mt-3 max-h-48 overflow-auto rounded bg-slate-950 p-3 text-xs text-slate-50'>{selected.sql}</pre>}</div>}<div className='rounded-2xl bg-white p-6 shadow-sm'><div className='mb-6 border-b border-slate-100 pb-5'><p className='text-xs font-semibold uppercase tracking-wide text-blue-600'>Traceable Report</p><h2 className='mt-1 text-2xl font-bold'>{run.document.title}</h2><p className='mt-2 text-sm text-slate-500'>点击报告中的指标、图表或结论，查看它绑定的真实证据。</p></div><EvidenceReport run={run} /></div></div></section>
  </div>;
}
