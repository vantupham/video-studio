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
  /** Extra files (images…) copied in with a folder / zip / multi-file upload. */
  attachments?: number;
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

export type Capability = "llm" | "tts" | "alignment" | "media" | "ocr";

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
    variant?: boolean;
    encoder?: string;
    transition?: string;
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

// ------------------------------------------------------------------ review & publishing

export type VideoStatus =
  | "draft"
  | "generating"
  | "rendered"
  | "in_review"
  | "changes_requested"
  | "approved"
  | "scheduled"
  | "publishing"
  | "published"
  | "failed";

export type PublicationStatus =
  | "queued"
  | "uploading"
  | "processing"
  | "scheduled"
  | "scheduled_remote"
  | "published"
  | "failed"
  | "missed"
  | "cancelled"
  | "retry_wait";

export interface PublicationBrief {
  id: string;
  platform: Platform;
  publisher_id: string;
  account_id: string | null;
  status: PublicationStatus;
  scheduled_at: string | null;
  remote_url: string | null;
  progress: number;
  error: string | null;
  published_at: string | null;
}

export interface VideoSummary {
  id: string;
  project_id: string;
  project_name: string | null;
  title: string;
  status: VideoStatus;
  status_label: string;
  status_changed_at: string;
  style: string;
  aspect: string;
  scenes: number;
  duration_s: number | null;
  thumbnail_url: string | null;
  latest_render_id: string | null;
  approved_render_id: string | null;
  outdated: boolean;
  renders: number;
  rendering: boolean;
  publications: PublicationBrief[];
  created_at: string;
  updated_at: string;
}

export interface VideoEvent {
  id: number;
  ts: string;
  action: string;
  from_status: VideoStatus | null;
  to_status: VideoStatus | null;
  actor: string;
  note: string;
  render_id: string | null;
  data: Record<string, unknown>;
}

export interface TimelineEntry {
  scene_id: string;
  kind: "intro" | "scene" | "outro";
  start: number;
  end: number;
}

export interface RenderVersion extends Omit<RenderRecord, "settings"> {
  version?: number;
  kind: "main" | "variant" | "preview";
  content_hash: string | null;
  current: boolean;
  approved_content: boolean;
  timeline: TimelineEntry[];
}

export interface VideoAction {
  id: "submit" | "approve" | "request_changes" | "reopen";
  label: string;
  note_required: boolean;
}

export interface VideoDetail extends Omit<VideoSummary, "renders"> {
  content_hash: string;
  approved_hash: string | null;
  actions: VideoAction[];
  render_locked: boolean;
  renders: RenderVersion[];
  events: VideoEvent[];
  publish_meta: PublishMeta;
}

export interface VideoLibrary {
  items: VideoSummary[];
  counts: Record<VideoStatus, number>;
  states: { id: VideoStatus; label: string }[];
}

export interface SceneDiff {
  scene_id: string;
  change: "added" | "removed" | "changed" | "same";
  fields: string[];
  before: Record<string, unknown> | null;
  after: Record<string, unknown> | null;
}

export interface RenderDiff {
  title_changed: boolean;
  title: { before: string | null; after: string | null };
  reordered: boolean;
  summary: Record<"added" | "removed" | "changed" | "same", number>;
  scenes: SceneDiff[];
}

export type Platform = "youtube" | "facebook";

export interface PlatformMeta {
  title: string;
  description: string;
  tags: string[];
  options: Record<string, unknown>;
}

export interface PublishMeta {
  common: { title: string; description: string; tags: string[]; hashtags: string[] };
  youtube: PlatformMeta;
  facebook: PlatformMeta;
  thumbnail: {
    mode: "auto" | "custom" | "none";
    frame_s: number | null;
    show_title: boolean;
    text: string;
    custom_path: string | null;
  };
}

export interface PublishConstraints {
  platform: string;
  format: string;
  aspect_ratios: string[];
  preferred_aspect: string | null;
  min_duration_s: number | null;
  max_duration_s: number | null;
  max_file_size_mb: number | null;
  max_title_len: number | null;
  max_description_len: number | null;
  max_description_bytes: number | null;
  max_tags_chars: number | null;
  supports_scheduling: boolean;
  min_schedule_ahead_s: number | null;
  max_schedule_ahead_s: number | null;
  supports_captions: boolean;
  supports_thumbnail: boolean;
  supports_playlist: boolean;
  privacy_options: string[];
  notes: string[];
}

export interface PublisherInfo {
  id: string;
  name: string;
  platform: Platform;
  mock: boolean;
  description: string;
  metadata_schema: JSONSchemaObject;
  constraints: PublishConstraints;
}

export interface SocialAccount {
  id: string;
  platform: Platform;
  publisher_id: string;
  name: string;
  external_id: string;
  kind: "channel" | "page";
  avatar_url: string | null;
  status: "connected" | "expired";
  last_error: string | null;
  token_expires_at: string | null;
  warning: string | null;
  mock: boolean;
  meta: Record<string, unknown>;
  created_at: string;
}

export interface SocialAppConfig {
  platform: Platform;
  client_id: string;
  client_secret: string | null;
  has_secret: boolean;
  configured: boolean;
  testing_mode?: boolean;
  redirect: string;
}

export interface OAuthFlow {
  id: string;
  platform: Platform;
  status: "pending" | "exchanging" | "select_pages" | "done" | "error";
  error: string | null;
  account_ids: string[];
  auth_url: string;
  redirect_uri: string;
  pages: { id: string; name: string; picture: string | null; category: string | null;
    tasks: string[] }[];
}

export interface PublishIssue {
  level: "error" | "warning" | "info";
  code: string;
  message: string;
  field?: string;
  fix?: { action: "rerender_aspect" | "use_variant"; aspect: string };
}

export interface ScheduleInput {
  mode: "now" | "local" | "platform";
  at?: string | null;
}

export interface PublishCheck {
  account: SocialAccount;
  publisher: { id: string; name: string; mock: boolean };
  render: { id: string; aspect: string; duration_s: number | null;
    size_bytes: number | null } | null;
  available_aspects: string[];
  meta: PlatformMeta;
  constraints: PublishConstraints;
  issues: PublishIssue[];
  ok: boolean;
}

export interface Publication extends PublicationBrief {
  storyboard_id: string;
  video_title: string | null;
  render_id: string | null;
  account_name: string | null;
  mock: boolean;
  meta: Record<string, unknown> & { title?: string; aspect?: string };
  schedule_mode: "now" | "local" | "platform";
  next_attempt_at: string | null;
  attempts: number;
  remote_id: string | null;
  warnings: string[];
  job_id: string | null;
  created_at: string;
  updated_at: string;
  resumable: boolean;
  thumbnail_url?: string;
}

export interface MetadataSuggestion {
  titles: string[];
  description: string;
  tags: string[];
  hashtags: string[];
}

export interface AppNotification {
  id: number;
  ts: string;
  level: "info" | "success" | "warning" | "error";
  title: string;
  message: string;
  link: string | null;
  read: boolean;
  data: Record<string, unknown>;
}

export interface DesktopPrefs {
  close_to_tray: boolean;
  autostart: boolean;
  start_minimized: boolean;
  wake_for_schedule: boolean;
  keep_awake: boolean;
  notifications: boolean;
}

export type AppEvent =
  | { type: "video"; id: string; status: VideoStatus }
  | ({ type: "publication" } & Publication)
  | { type: "publication.progress"; id: string; progress: number; message: string | null }
  | ({ type: "notification" } & AppNotification)
  | { type: "queue"; paused: boolean };
