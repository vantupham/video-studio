import type { ContentIR } from "@ir/content-ir";
import type { Storyboard } from "@ir/storyboard";

export type { Scene, Storyboard, Visual, VisualType } from "@ir/storyboard";

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
  /** Labels by index (array) or by value (object). */
  "x-enumLabels"?: string[] | Record<string, string>;
  /** Secret field: rendered as a password input, never echoed back by the API. */
  "x-secret"?: boolean;
  /** Options can be loaded from the provider ("models" / "voices"). */
  "x-source"?: "models" | "voices";
  "x-multiline"?: boolean;
  format?: string;
  default?: unknown;
  minimum?: number;
  maximum?: number;
  properties?: Record<string, JSONSchemaProperty>;
  required?: string[];
}

export interface JSONSchemaObject {
  type: "object";
  properties?: Record<string, JSONSchemaProperty>;
  required?: string[];
  additionalProperties?: boolean;
}

export type Capability = "llm" | "tts" | "alignment" | "media";

export interface ProviderType {
  id: string;
  name: string;
  capability: Capability;
  description: string;
  docs_url: string | null;
  unofficial: boolean;
  requires_network: boolean;
  is_mock: boolean;
  config_schema: JSONSchemaObject;
  secret_fields: string[];
}

export interface ProviderInstance {
  id: string;
  capability: Capability;
  type: string;
  type_name: string;
  available: boolean;
  name: string;
  config: Record<string, unknown>;
  /** Masked values (••••last4) or null when not set. */
  secrets: Record<string, string | null>;
  is_default: boolean;
  is_mock: boolean;
  unofficial: boolean;
  created_at: string;
  updated_at: string;
}

export interface TestResult {
  ok: boolean;
  message: string;
  details: Record<string, unknown>;
}

export interface VoiceInfo {
  id: string;
  name: string;
  lang: string;
  gender: string | null;
  description: string | null;
}

export interface ModelInfo {
  id: string;
  name: string | null;
}

export interface ProviderChoice {
  instance_id?: string | null;
  model?: string | null;
  voice?: string | null;
}

export interface GenerationSettings {
  style: string;
  aspect: "16:9" | "9:16" | "1:1" | null;
  target_duration_s: number;
  language: string;
  tone: string;
  use_as_is: boolean;
  max_scenes: number;
  llm: ProviderChoice;
  tts: ProviderChoice;
  alignment: ProviderChoice;
  media: ProviderChoice;
  music_asset_id: string | null;
  music_volume: number;
  captions: boolean;
  burn_subtitles: boolean;
}

export interface StyleInfo {
  id: string;
  name: string;
  description: string;
  aspect: "16:9" | "9:16" | "1:1";
  captions_mode: string;
  customized: boolean;
}

export interface SceneAsset {
  tts_key: string;
  audio: string;
  audio_url: string;
  duration_s: number;
  word_count: number;
  timing_source: string;
  provider: string;
  voice: string | null;
}

export interface StoryboardRecord {
  id: string;
  project_id: string;
  document_id: string | null;
  title: string;
  data: Storyboard;
  settings: GenerationSettings;
  assets: Record<string, SceneAsset>;
  image_urls: Record<string, string>;
  created_at: string;
  updated_at: string;
}

export interface StoryboardSummary {
  id: string;
  title: string;
  style: string;
  aspect: string;
  scenes: number;
  created_at: string;
  updated_at: string;
}

export interface RenderRecord {
  id: string;
  project_id: string;
  storyboard_id: string | null;
  job_id: string | null;
  status: "queued" | "running" | "done" | "failed" | "cancelled";
  style: string;
  aspect: string;
  settings: {
    preview?: boolean;
    encoder?: string;
    width?: number;
    height?: number;
    burned_subtitles?: boolean;
  };
  outputs: Record<string, string>;
  urls: Record<string, string>;
  folder: string | null;
  duration_s: number | null;
  size_bytes: number | null;
  error: string | null;
  created_at: string;
  finished_at: string | null;
}

export interface AssetRecord {
  id: string;
  project_id: string | null;
  kind: "image" | "logo" | "audio";
  filename: string;
  rel_path: string;
  mime: string | null;
  size_bytes: number;
  url: string;
  created_at: string;
}

export interface Branding {
  enabled: boolean;
  logo_asset_id: string | null;
  logo_url?: string | null;
  logo_position: "top-left" | "top-right" | "bottom-left" | "bottom-right";
  logo_scale: number;
  logo_opacity: number;
  intro_text: string;
  outro_text: string;
  card_seconds: number;
}

export interface JobStep {
  id: string;
  label: string;
  start: number;
  end: number;
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
  plugins: {
    input_adapters: string[];
    providers: Record<string, string[]>;
    renderers: string[];
    publishers: string[];
    errors: { origin: string; error: string }[];
  };
}

export type JobEvent =
  | { type: "hello" }
  | { type: "ping" }
  | { type: "job"; job: Job }
  | {
      type: "job_log";
      job_id: string;
      level: string;
      message: string;
      ts: string;
    };
