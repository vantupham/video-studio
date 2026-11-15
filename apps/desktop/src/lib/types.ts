import type { ContentIR } from "@ir/content-ir";

export type JobStatus = "queued" | "running" | "succeeded" | "failed" | "cancelled";

export interface JobLogEntry {
  ts: string;
  level: string;
  message: string;
}

export interface Job {
  id: string;
  type: string;
  project_id: string | null;
  status: JobStatus;
  progress: number;
  message: string;
  payload: Record<string, unknown>;
  result: Record<string, unknown> | null;
  error: string | null;
  attempts: number;
  cancel_requested: boolean;
  created_at: string | null;
  started_at: string | null;
  finished_at: string | null;
  logs?: JobLogEntry[];
}

export interface Project {
  id: string;
  name: string;
  description: string;
  created_at: string;
  updated_at: string;
  source_count: number;
  document_count: number;
}

export interface Source {
  id: string;
  project_id: string;
  name: string;
  kind: "file" | "text";
  adapter_id: string;
  config: Record<string, unknown>;
  filename: string;
  size_bytes: number;
  created_at: string;
}

export interface DocumentSummary {
  id: string;
  project_id: string;
  source_id: string | null;
  job_id: string | null;
  adapter_id: string;
  title: string;
  rel_path: string;
  stats: Record<string, number>;
  created_at: string;
}

export interface IRDocument extends DocumentSummary {
  data: ContentIR;
}

export interface ProjectDetail extends Project {
  sources: Source[];
  documents: DocumentSummary[];
}

export interface JSONSchemaProperty {
  type?: "string" | "integer" | "number" | "boolean" | "object" | "array";
  title?: string;
  description?: string;
  enum?: (string | number)[];
  "x-enumLabels"?: string[];
  default?: unknown;
  minimum?: number;
  maximum?: number;
}

export interface JSONSchemaObject {
  type: "object";
  properties?: Record<string, JSONSchemaProperty>;
  required?: string[];
  additionalProperties?: boolean;
}

export interface Adapter {
  id: string;
  name: string;
  description: string;
  file_extensions: string[];
  config_schema: JSONSchemaObject;
  origin: string;
}

export interface SystemInfo {
  version: string;
  dev: boolean;
  data_dir: string;
  db_path: string;
  platform: string;
  python: string;
  frozen: boolean;
  ffmpeg: {
    found: boolean;
    ffmpeg: string | null;
    ffprobe: string | null;
    version: string | null;
    source: string | null;
  };
  secret_store: string;
  workers: number;
  job_types: string[];
  plugins: Record<string, unknown>;
}

export type JobEvent =
  | { type: "hello" }
  | { type: "ping" }
  | { type: "job"; job: Job }
  | { type: "job_log"; job_id: string; level: string; message: string; ts: string };
