import type { ReportEvidenceDetail, ReportRun, ReportRunSummary } from '@/types/report';
import { GET, GET_RAW } from '../index';

const reportPath = (reportId: string) => `/api/v1/reports/${encodeURIComponent(reportId)}`;

export const getLatestReport = (reportId: string) => GET<undefined, ReportRun>(reportPath(reportId));

export const getReportRun = (reportId: string, runId: string) =>
  GET<undefined, ReportRun>(`${reportPath(reportId)}/runs/${encodeURIComponent(runId)}`);

export const listReportRuns = (reportId: string) =>
  GET<{ limit: number }, ReportRunSummary[]>(`${reportPath(reportId)}/runs`, { limit: 200 });

export const getReportBlockEvidence = (reportId: string, runId: string, blockId: string) =>
  GET<undefined, ReportEvidenceDetail[]>(
    `${reportPath(reportId)}/runs/${encodeURIComponent(runId)}/blocks/${encodeURIComponent(blockId)}/evidence`,
  );

export const getReportPreview = (reportId: string, runId: string) =>
  GET_RAW<Blob>(`${reportPath(reportId)}/runs/${encodeURIComponent(runId)}/preview`, { responseType: 'blob' });
