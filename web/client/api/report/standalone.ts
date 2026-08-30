import type { ReportEvidenceDetail, ReportRun, ReportRunSummary } from '@/types/report';

type Result<T> = { success: boolean; data: T; err_msg?: string | null };

const apiBaseUrl = (process.env.API_BASE_URL || 'http://127.0.0.1:5670').replace(/\/$/, '');
const reportPath = (reportId: string) => `${apiBaseUrl}/api/v1/reports/${encodeURIComponent(reportId)}`;

const getJson = async <T>(path: string): Promise<Result<T>> => {
  const response = await fetch(path);
  if (!response.ok) throw new Error(`Request failed (${response.status})`);
  return response.json() as Promise<Result<T>>;
};

export const getStandaloneLatestReport = (reportId: string) => getJson<ReportRun>(reportPath(reportId));
export const getStandaloneReportRun = (reportId: string, runId: string) =>
  getJson<ReportRun>(`${reportPath(reportId)}/runs/${encodeURIComponent(runId)}`);
export const listStandaloneReportRuns = (reportId: string) =>
  getJson<ReportRunSummary[]>(`${reportPath(reportId)}/runs?limit=200`);
export const getStandaloneBlockEvidence = (reportId: string, runId: string, blockId: string) =>
  getJson<ReportEvidenceDetail[]>(`${reportPath(reportId)}/runs/${encodeURIComponent(runId)}/blocks/${encodeURIComponent(blockId)}/evidence`);
export const getStandaloneReportPreview = (reportId: string, runId: string) =>
  fetch(`${reportPath(reportId)}/runs/${encodeURIComponent(runId)}/preview`);
