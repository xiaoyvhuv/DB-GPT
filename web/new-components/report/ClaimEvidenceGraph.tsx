import type { EvidenceReference, ReportArtifact, ReportBlock, ReportClaim, ReportRun } from '@/types/report';
import React, { useCallback, useLayoutEffect, useMemo, useRef, useState } from 'react';

type ViewMode = 'business' | 'evidence' | 'provenance';
type ClaimStatus = 'verified' | 'partial' | 'unverified' | 'conflicted';
type NodeKind = 'claim' | 'evidence' | 'computation' | 'data' | 'source';
type GraphNode = { id: string; kind: NodeKind; title: string; subtitle?: string; value?: string; status?: ClaimStatus; artifact?: ReportArtifact; block?: ReportBlock };
type GraphEdge = { from: string; to: string; relation: 'supported_by' | 'produced_by' | 'uses' | 'derived_from' };
type GraphClaim = { id: string; title: string; block: ReportBlock; references: EvidenceReference[]; status: ClaimStatus; confidence?: number };

const statusText: Record<ClaimStatus, string> = { verified: '已验证', partial: '部分支持', unverified: '待核验', conflicted: '存在冲突' };
const statusClass: Record<ClaimStatus, string> = { verified: 'border-emerald-200 bg-emerald-50 text-emerald-700', partial: 'border-amber-200 bg-amber-50 text-amber-700', unverified: 'border-slate-200 bg-slate-50 text-slate-600', conflicted: 'border-rose-200 bg-rose-50 text-rose-700' };
const kindClass: Record<NodeKind, string> = { claim: 'border-violet-300', evidence: 'border-blue-300', computation: 'border-orange-300', data: 'border-teal-300', source: 'border-slate-300' };
const kindIcon: Record<NodeKind, string> = { claim: '♛', evidence: '◈', computation: '</>', data: '▦', source: '◉' };
const kindName: Record<NodeKind, string> = { claim: '业务结论', evidence: '数据证据', computation: '计算过程', data: '数据', source: '数据来源' };

const hasChinese = (text: string) => /[\u4e00-\u9fff]/.test(text);
const isPreparation = (artifact?: ReportArtifact) => /sqlite_master|information_schema|\bpragma\b|query the schema|schema of .*table|dispatch_parallel|sub-agent sql result/i.test(`${artifact?.name || ''}\n${artifact?.sql || ''}\n${artifact?.code || ''}`);
const artifactName = (artifact: ReportArtifact) => {
  const text = `${artifact.name || ''}\n${artifact.sql || ''}`.toLowerCase();
  if (artifact.type === 'python') return '数据计算';
  if (artifact.type === 'chart') return '分析图表';
  if (/payment.*(sum|amount)|sum.*payment/.test(text)) return '支付方式金额统计';
  if (/count\s*\(.*order|total_orders/.test(text)) return '订单总量统计';
  if (/installment|分期/.test(text)) return '分期情况统计';
  if (/avg|average|客单价/.test(text)) return '平均值与客单价计算';
  if (/date|month|月度|trend/.test(text)) return '经营趋势统计';
  return artifact.type === 'sql' ? '业务数据查询' : '分析执行产物';
};
const lineage = (artifact: ReportArtifact) => {
  const raw = artifact.metadata.lineage;
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return { tables: [] as string[] };
  const data = raw as Record<string, unknown>;
  return { source: typeof data.source_name === 'string' ? data.source_name : typeof data.source_id === 'string' ? data.source_id : undefined, tables: Array.isArray(data.tables) ? data.tables.filter((item): item is string => typeof item === 'string' && Boolean(item.trim())) : [] };
};
const inlineValue = (artifact: ReportArtifact) => {
  const inline = artifact.result?.inline;
  if (!inline || typeof inline !== 'object' || Array.isArray(inline)) return undefined;
  const rows = (inline as { rows?: unknown }).rows;
  if (!Array.isArray(rows) || !Array.isArray(rows[0])) return undefined;
  return rows[0][0] === undefined ? undefined : String(rows[0][0]);
};
const mapStatus = (claim?: ReportClaim): ClaimStatus => {
  if (claim?.verification?.status === 'verified') return 'verified';
  if (claim?.verification?.status === 'partially_supported') return 'partial';
  if (claim?.verification?.status === 'contradicted') return 'conflicted';
  return 'unverified';
};
const localizedClaim = (block: ReportBlock, raw: string, artifact?: ReportArtifact) => hasChinese(raw) ? raw : `${artifact ? artifactName(artifact) : '业务分析'}${block.type === 'metric' ? '指标' : block.type === 'table' ? '结果明细' : '分析结论'}`;
const dedupeGraphClaims = (claims: GraphClaim[]) => {
  const unique = new Map<string, GraphClaim>();
  for (const claim of claims) {
    const evidenceKey = claim.references.map(reference => reference.artifact_id).sort().join(',');
    const key = `${claim.title}\u0000${evidenceKey}`;
    if (!unique.has(key)) unique.set(key, claim);
  }
  return [...unique.values()];
};

const relationLabel: Record<GraphEdge['relation'], string> = { supported_by: '支持', produced_by: '产生', uses: '使用', derived_from: '来源于' };

interface Props { run: ReportRun; onArtifactFocus?: (stepId: string) => void; }

export const ClaimEvidenceGraph: React.FC<Props> = ({ run, onArtifactFocus }) => {
  const artifacts = useMemo(() => new Map(run.document.artifacts.map(artifact => [artifact.artifact_id, artifact])), [run]);
  const claims = useMemo<GraphClaim[]>(() => dedupeGraphClaims(run.document.sections.flatMap(section => section.blocks.flatMap(block => {
    const makeClaim = (id: string, raw: string, verification?: ReportClaim['verification']): GraphClaim | undefined => {
      const ids = verification?.evidence_artifact_ids;
      const references = (ids?.length ? block.evidence.filter(reference => ids.includes(reference.artifact_id)) : block.evidence).filter(reference => !isPreparation(artifacts.get(reference.artifact_id)));
      if (!references.length) return undefined;
      return { id, title: localizedClaim(block, raw, artifacts.get(references[0].artifact_id)), block, references, status: mapStatus(verification ? { claim_id: id, text: raw, verification } : undefined), confidence: verification?.support_score };
    };
    if (!block.claims?.length) return [];
    return block.claims.map(claim => makeClaim(claim.claim_id, claim.text, claim.verification)).filter((claim): claim is GraphClaim => Boolean(claim));
  }))), [run, artifacts]);
  const [mode, setMode] = useState<ViewMode>('evidence');
  const [claimIndex, setClaimIndex] = useState(0);
  const [selectedNodeId, setSelectedNodeId] = useState<string>();
  const [showAllEvidence, setShowAllEvidence] = useState(false);
  const canvasRef = useRef<HTMLDivElement>(null);
  const nodeRefs = useRef(new Map<string, HTMLButtonElement>());
  const [paths, setPaths] = useState<Array<{ d: string; label: string; color: string }>>([]);
  const claim = claims[Math.min(claimIndex, Math.max(0, claims.length - 1))];

  const model = useMemo(() => {
    if (!claim) return { nodes: [] as GraphNode[], edges: [] as GraphEdge[], levels: [] as GraphNode[][] };
    const evidenceArtifacts = claim.references.map(reference => ({ reference, artifact: artifacts.get(reference.artifact_id) })).filter((item): item is { reference: EvidenceReference; artifact: ReportArtifact } => Boolean(item.artifact));
    const selectedEvidence = showAllEvidence ? evidenceArtifacts : evidenceArtifacts.slice(0, 4);
    const nodes: GraphNode[] = [{ id: `claim:${claim.id}`, kind: 'claim', title: claim.title, subtitle: `${claim.references.length} 条证据`, status: claim.status, value: claim.confidence === undefined ? undefined : `可信度 ${Math.round(claim.confidence * 100)}%`, block: claim.block }];
    const edges: GraphEdge[] = [];
    for (const { reference, artifact } of selectedEvidence) {
      const evidenceId = `evidence:${artifact.artifact_id}`;
      const value = claim.block.type === 'metric' ? String(claim.block.display_value ?? claim.block.value ?? inlineValue(artifact) ?? '') : inlineValue(artifact);
      nodes.push({ id: evidenceId, kind: 'evidence', title: artifactName(artifact), subtitle: reference.role === 'calculation' ? '计算结果' : '支撑事实', value, status: claim.status, artifact, block: claim.block });
      edges.push({ from: `claim:${claim.id}`, to: evidenceId, relation: 'supported_by' });
      if (mode !== 'business') {
        const computationId = `computation:${artifact.artifact_id}`;
        nodes.push({ id: computationId, kind: 'computation', title: artifactName(artifact), subtitle: artifact.type === 'sql' ? 'SQL 查询' : artifact.type === 'python' ? 'Python 计算' : artifact.type === 'chart' ? '图表生成' : '分析计算', value: artifact.duration_ms === undefined ? undefined : `${artifact.duration_ms} ms`, artifact, block: claim.block });
        edges.push({ from: evidenceId, to: computationId, relation: 'produced_by' });
        if (mode === 'provenance') {
          const itemLineage = lineage(artifact);
          for (const table of itemLineage.tables) {
            const dataId = `data:${table}`;
            if (!nodes.some(node => node.id === dataId)) nodes.push({ id: dataId, kind: 'data', title: table, subtitle: '执行时记录的输入数据', artifact });
            edges.push({ from: computationId, to: dataId, relation: 'uses' });
            if (itemLineage.source) {
              const sourceId = `source:${itemLineage.source}`;
              if (!nodes.some(node => node.id === sourceId)) nodes.push({ id: sourceId, kind: 'source', title: itemLineage.source, subtitle: '执行时记录的数据来源', artifact });
              edges.push({ from: dataId, to: sourceId, relation: 'derived_from' });
            }
          }
        }
      }
    }
    const levelKinds: NodeKind[] = mode === 'business' ? ['claim', 'evidence'] : mode === 'evidence' ? ['claim', 'evidence', 'computation'] : ['claim', 'evidence', 'computation', 'data', 'source'];
    return { nodes, edges, levels: levelKinds.map(kind => nodes.filter(node => node.kind === kind)) };
  }, [artifacts, claim, mode, showAllEvidence]);

  const registerNode = useCallback((id: string) => (element: HTMLButtonElement | null) => { if (element) nodeRefs.current.set(id, element); else nodeRefs.current.delete(id); }, []);
  useLayoutEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return undefined;
    const update = () => {
      const root = canvas.getBoundingClientRect();
      const next = model.edges.flatMap(edge => {
        const from = nodeRefs.current.get(edge.from)?.getBoundingClientRect(); const to = nodeRefs.current.get(edge.to)?.getBoundingClientRect();
        if (!from || !to) return [];
        const x1 = from.left + from.width / 2 - root.left; const y1 = from.bottom - root.top;
        const x2 = to.left + to.width / 2 - root.left; const y2 = to.top - root.top;
        return [{ d: `M ${x1} ${y1} C ${x1} ${y1 + 28}, ${x2} ${y2 - 28}, ${x2} ${y2}`, label: relationLabel[edge.relation], color: edge.relation === 'supported_by' ? '#7c3aed' : edge.relation === 'produced_by' ? '#2563eb' : edge.relation === 'uses' ? '#f97316' : '#64748b' }];
      });
      setPaths(next);
    };
    const observer = new ResizeObserver(update); observer.observe(canvas); window.addEventListener('resize', update); requestAnimationFrame(update);
    return () => { observer.disconnect(); window.removeEventListener('resize', update); };
  }, [model]);

  const selectedNode = model.nodes.find(node => node.id === selectedNodeId) || model.nodes[0];
  if (!claims.length || !claim) return <div className='rounded-xl border border-amber-200 bg-amber-50 p-5 text-sm text-amber-900'>本轮没有可用于构建证据图谱的报告结论。准备性执行过程仍可在“轨迹”中查看。</div>;
  const NodeCard: React.FC<{ node: GraphNode }> = ({ node }) => <button ref={registerNode(node.id)} type='button' onClick={() => setSelectedNodeId(node.id)} className={`relative z-10 w-[190px] rounded-xl border bg-white p-3 text-left shadow-sm transition hover:shadow-md dark:bg-[#191a1f] ${kindClass[node.kind]} ${selectedNode?.id === node.id ? 'ring-2 ring-violet-300' : ''}`}><div className='flex items-start justify-between gap-2'><span className='text-base'>{kindIcon[node.kind]}</span>{node.status && <span className={`rounded-full border px-1.5 py-0.5 text-[10px] ${statusClass[node.status]}`}>{statusText[node.status]}</span>}</div><p className='mt-2 text-[11px] font-semibold text-slate-500'>{kindName[node.kind]}</p><p className='mt-1 break-words text-sm font-bold leading-5 text-slate-800 dark:text-slate-100'>{node.title}</p>{node.value && <p className='mt-2 text-base font-bold text-slate-900 dark:text-white'>{node.value}</p>}{node.subtitle && <p className='mt-1 text-[11px] text-slate-500'>{node.subtitle}</p>}</button>;
  return <div className='space-y-4'><header className='rounded-2xl border border-violet-100 bg-gradient-to-r from-violet-50 via-white to-violet-50 px-6 py-5'><div className='flex flex-wrap items-center justify-between gap-3'><div><h2 className='text-2xl font-bold text-slate-900'>报告证据图谱</h2><p className='mt-1 text-sm text-slate-600'>从业务结论出发，追溯证据、计算过程与数据来源。</p></div><div className='flex gap-3 text-sm'><span className='rounded-lg border border-violet-200 bg-white px-3 py-1.5 text-violet-700'>业务结论 {claims.length} 条</span><span className='rounded-lg border border-blue-200 bg-white px-3 py-1.5 text-blue-700'>关联证据 {claim.references.length} 条</span></div></div></header>
    <div className='flex flex-wrap gap-2'>{([['business', '业务视图'], ['evidence', '证据视图'], ['provenance', '完整溯源']] as const).map(([value, label]) => <button key={value} type='button' onClick={() => setMode(value)} className={`rounded-md border px-3 py-1.5 text-xs font-medium ${mode === value ? 'border-violet-400 bg-violet-50 text-violet-700' : 'border-slate-200 bg-white text-slate-600'}`}>{label}</button>)}</div>
    <div className='grid grid-cols-1 gap-5 xl:grid-cols-[220px_minmax(0,1fr)_300px]'><aside className='rounded-xl border border-slate-200 bg-white p-3'><p className='mb-3 text-sm font-bold text-slate-800'>报告结论</p><div className='space-y-2'>{claims.map((item, index) => <button type='button' key={item.id} onClick={() => { setClaimIndex(index); setSelectedNodeId(undefined); setShowAllEvidence(false); }} className={`w-full rounded-lg border p-2.5 text-left text-xs ${index === claimIndex ? 'border-violet-400 bg-violet-50' : 'border-slate-200'}`}><span className={`mr-1 ${item.status === 'verified' ? 'text-emerald-600' : item.status === 'partial' ? 'text-amber-600' : 'text-slate-400'}`}>{item.status === 'verified' ? '✓' : item.status === 'partial' ? '!' : '○'}</span>{item.title}</button>)}</div><div className='mt-4 flex items-center justify-between text-xs text-slate-500'><button type='button' disabled={claimIndex === 0} onClick={() => setClaimIndex(current => current - 1)}>← 上一个</button><span>{claimIndex + 1} / {claims.length}</span><button type='button' disabled={claimIndex === claims.length - 1} onClick={() => setClaimIndex(current => current + 1)}>下一个 →</button></div></aside>
      <section ref={canvasRef} className='relative min-h-[570px] overflow-auto rounded-xl border border-slate-200 bg-slate-50/60 p-5'><svg className='pointer-events-none absolute inset-0 h-full w-full' aria-hidden='true'><defs><marker id='claim-graph-arrow' markerWidth='8' markerHeight='8' refX='7' refY='4' orient='auto'><path d='M0,0 L0,8 L8,4 z' fill='#64748b' /></marker></defs>{paths.map((path, index) => <g key={`${path.d}-${index}`}><path d={path.d} fill='none' stroke={path.color} strokeWidth='2' markerEnd='url(#claim-graph-arrow)' /><text x={0} y={0} className='hidden'>{path.label}</text></g>)}</svg><p className='relative z-10 text-center text-xs font-semibold uppercase tracking-wider text-slate-400'>为什么可以相信这个结论？</p><div className='relative z-10 mt-5 space-y-9'>{model.levels.map((level, index) => level.length ? <div key={index} className='flex flex-wrap justify-center gap-4'>{level.map(node => <NodeCard key={node.id} node={node} />)}{index === 1 && claim.references.length > 4 && !showAllEvidence && <button type='button' onClick={() => setShowAllEvidence(true)} className='relative z-10 h-24 w-[190px] rounded-xl border border-dashed border-blue-300 bg-white text-sm font-medium text-blue-700'>+{claim.references.length - 4} 条证据</button>}</div> : null)}</div></section>
      <aside className='rounded-xl border border-slate-200 bg-white'><header className='border-b border-slate-100 px-5 py-4'><h3 className='font-bold text-slate-900'>{selectedNode?.kind === 'claim' ? '结论详情' : selectedNode?.kind === 'evidence' ? '证据详情' : selectedNode?.kind === 'computation' ? '计算过程' : '数据详情'}</h3></header>{selectedNode && <div className='space-y-4 p-5'><p className='text-base font-bold leading-6 text-slate-900'>{selectedNode.title}</p>{selectedNode.value && <p className='text-2xl font-bold text-slate-900'>{selectedNode.value}</p>}{selectedNode.status && <span className={`inline-block rounded-full border px-2 py-1 text-xs ${statusClass[selectedNode.status]}`}>{statusText[selectedNode.status]}</span>}{selectedNode.kind === 'claim' && <><section className='border-t border-slate-100 pt-4'><p className='text-xs font-semibold text-violet-600'>为什么可以相信这个结论？</p><p className='mt-2 text-sm text-slate-600'>{claim.references.length} 条关联证据，其中 {claim.status === 'verified' ? claim.references.length : 0} 条已完成验证。</p><div className='mt-3 h-2 overflow-hidden rounded-full bg-slate-100'><div className='h-full bg-emerald-500' style={{ width: `${claim.status === 'verified' ? 100 : claim.status === 'partial' ? 50 : 0}%` }} /></div></section><button type='button' onClick={() => setMode('evidence')} className='w-full rounded-lg border border-violet-200 py-2 text-xs font-medium text-violet-700'>展开计算过程</button></>}{selectedNode.artifact && <><section className='border-t border-slate-100 pt-4'><p className='text-xs text-slate-500'>执行状态</p><p className='mt-1 text-sm font-medium'>{selectedNode.artifact.status === 'succeeded' ? '执行成功' : selectedNode.artifact.status || '已捕获'}{selectedNode.artifact.duration_ms === undefined ? '' : ` · ${selectedNode.artifact.duration_ms} ms`}</p></section>{selectedNode.artifact.execution.step_id && onArtifactFocus && <button type='button' onClick={() => onArtifactFocus(selectedNode.artifact!.execution.step_id)} className='w-full rounded-lg border border-blue-200 py-2 text-xs font-medium text-blue-700'>查看 Agent 执行记录</button>}</>}</div>}</aside></div>
  </div>;
};

export default ClaimEvidenceGraph;
