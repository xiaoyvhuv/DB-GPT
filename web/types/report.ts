export type ReportBlockType = 'metric' | 'table' | 'chart' | 'insight' | 'text';
export type ArtifactType = 'sql' | 'python' | 'chart' | 'reference';

export interface ArtifactContent {
  inline?: unknown;
  uri?: string;
  media_type?: string;
  sha256?: string;
  size?: number;
}

export interface ReportArtifact {
  type: ArtifactType;
  artifact_id: string;
  name?: string;
  status?: 'succeeded' | 'failed' | 'cancelled' | 'timed_out';
  duration_ms?: number;
  error?: string;
  input_artifact_ids: string[];
  metadata: Record<string, unknown>;
  execution: {
    conv_uid: string;
    step_id: string;
    message_id?: string;
    task_id?: string;
    run_id?: string;
    action?: string;
  };
  result?: ArtifactContent;
  output?: ArtifactContent;
  columns?: string[];
  row_count?: number;
  sql?: string;
  code?: string;
  language?: string;
  stdout?: string;
  stderr?: string;
  chart_type?: string;
  spec?: Record<string, unknown>;
  reference_type?: 'knowledge' | 'memory' | 'web' | 'model';
  source_id?: string;
  source_name?: string;
  uri?: string;
  locator?: Record<string, unknown>;
  excerpt?: string;
}

export interface EvidenceReference {
  artifact_id: string;
  role: 'source' | 'calculation' | 'support';
  selector: Record<string, unknown>;
  target_selector: Record<string, unknown>;
}

export interface ReportBlock {
  type: ReportBlockType;
  block_id: string;
  title?: string;
  evidence: EvidenceReference[];
  metadata: Record<string, unknown>;
  value?: unknown;
  display_value?: string;
  unit?: string;
  content?: string;
  data_artifact_id?: string;
  chart_artifact_id?: string;
  description?: string;
}

export interface ReportDocument {
  schema_version: 1 | 2;
  report_id: string;
  title: string;
  conv_uid: string;
  created_at: string;
  sections: Array<{
    section_id: string;
    title: string;
    blocks: ReportBlock[];
  }>;
  artifacts: ReportArtifact[];
  metadata: Record<string, unknown>;
}

export interface ReportRunSummary {
  run_id: string;
  report_id: string;
  revision: number;
  schema_version: number;
  trigger_type: string;
  parent_run_id?: string;
  status: string;
  created_at: string;
}

export interface ReportRun extends ReportRunSummary {
  document: ReportDocument;
}

export interface ReportEvidenceDetail {
  section_id: string;
  block_id: string;
  ordinal: number;
  evidence: EvidenceReference;
  artifact: ReportArtifact;
}
