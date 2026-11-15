import type { BackendInfo } from "./backend";
import type {
  Adapter,
  AssetRecord,
  Branding,
  Capability,
  GenerationSettings,
  IRDocument,
  Job,
  JobStep,
  ModelInfo,
  Project,
  ProjectDetail,
  ProviderInstance,
  ProviderType,
  RenderRecord,
  Source,
  Storyboard,
  StoryboardRecord,
  StoryboardSummary,
  StyleInfo,
  SystemInfo,
  TestResult,
  VoiceInfo,
} from "./types";

export class ApiError extends Error {
  readonly status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

function errorMessage(status: number, body: unknown): string {
  if (body && typeof body === "object" && "detail" in body) {
    const detail = (body as { detail: unknown }).detail;
    if (typeof detail === "string") return detail;
    if (detail && typeof detail === "object" && "message" in detail) {
      return String((detail as { message: unknown }).message);
    }
    if (Array.isArray(detail)) {
      return detail
        .map((d) => (d && typeof d === "object" && "msg" in d ? String(d.msg) : String(d)))
        .join("; ");
    }
  }
  return `Lỗi HTTP ${status}`;
}

export interface NewSourceInput {
  file?: File;
  text?: string;
  name?: string;
  adapterId?: string;
  config?: Record<string, unknown>;
}

export class ApiClient {
  readonly info: BackendInfo;
  private readonly fetchImpl: typeof fetch;

  constructor(info: BackendInfo, fetchImpl: typeof fetch = (...args) => fetch(...args)) {
    this.info = info;
    this.fetchImpl = fetchImpl;
  }

  async request<T>(method: string, path: string, body?: unknown): Promise<T> {
    const headers: Record<string, string> = {
      Authorization: `Bearer ${this.info.token}`,
    };
    let payload: BodyInit | undefined;
    if (body instanceof FormData) {
      payload = body;
    } else if (body !== undefined) {
      headers["Content-Type"] = "application/json";
      payload = JSON.stringify(body);
    }
    const res = await this.fetchImpl(`${this.info.baseUrl}${path}`, {
      method,
      headers,
      body: payload,
    });
    if (res.status === 204) return undefined as T;
    const isJson = (res.headers.get("content-type") || "").includes("application/json");
    const data: unknown = isJson ? await res.json() : await res.text();
    if (!res.ok) throw new ApiError(res.status, errorMessage(res.status, data));
    return data as T;
  }

  /** Absolute URL for a media path returned by the API (``/media/…?k=…``). */
  mediaUrl(path: string | null | undefined): string {
    if (!path) return "";
    return path.startsWith("http") ? path : `${this.info.baseUrl}${path}`;
  }

  wsUrl(path: string, params: Record<string, string> = {}): string {
    const url = new URL(`${this.info.baseUrl.replace(/^http/, "ws")}${path}`);
    url.searchParams.set("token", this.info.token);
    for (const [k, v] of Object.entries(params)) url.searchParams.set(k, v);
    return url.toString();
  }

  health() {
    return this.request<{ status: string; version: string }>("GET", "/health");
  }

  systemInfo() {
    return this.request<SystemInfo>("GET", "/system/info");
  }

  adapters() {
    return this.request<Adapter[]>("GET", "/adapters");
  }

  listProjects() {
    return this.request<Project[]>("GET", "/projects");
  }

  createProject(name: string, description = "") {
    return this.request<ProjectDetail>("POST", "/projects", {
      name,
      description,
    });
  }

  getProject(id: string) {
    return this.request<ProjectDetail>("GET", `/projects/${id}`);
  }

  deleteProject(id: string) {
    return this.request<void>("DELETE", `/projects/${id}`);
  }

  addSource(projectId: string, input: NewSourceInput) {
    const form = new FormData();
    if (input.file) form.append("file", input.file, input.file.name);
    if (input.text !== undefined) form.append("text", input.text);
    if (input.name) form.append("name", input.name);
    if (input.adapterId) form.append("adapter_id", input.adapterId);
    if (input.config) form.append("config", JSON.stringify(input.config));
    return this.request<Source>("POST", `/projects/${projectId}/sources`, form);
  }

  sourceContent(projectId: string, sourceId: string) {
    return this.request<string>("GET", `/projects/${projectId}/sources/${sourceId}/content`);
  }

  deleteSource(projectId: string, sourceId: string) {
    return this.request<void>("DELETE", `/projects/${projectId}/sources/${sourceId}`);
  }

  ingest(projectId: string, sourceIds?: string[]) {
    return this.request<Job>("POST", `/projects/${projectId}/ingest`, {
      source_ids: sourceIds ?? null,
    });
  }

  getDocument(projectId: string, docId: string) {
    return this.request<IRDocument>("GET", `/projects/${projectId}/documents/${docId}`);
  }

  listJobs(params: { projectId?: string; status?: string; limit?: number } = {}) {
    const q = new URLSearchParams();
    if (params.projectId) q.set("project_id", params.projectId);
    if (params.status) q.set("status", params.status);
    if (params.limit) q.set("limit", String(params.limit));
    const qs = q.toString();
    return this.request<Job[]>("GET", `/jobs${qs ? `?${qs}` : ""}`);
  }

  getJob(id: string) {
    return this.request<Job>("GET", `/jobs/${id}`);
  }

  cancelJob(id: string) {
    return this.request<Job>("POST", `/jobs/${id}/cancel`);
  }

  demoJob(seconds = 6, steps = 12) {
    return this.request<Job>("POST", "/jobs/demo", { seconds, steps });
  }

  getSelftestSecret() {
    return this.request<{ backend: string; value: string | null }>("GET", "/secrets/selftest");
  }

  selftestSecret(value: string) {
    return this.request<{
      backend: string;
      ok: boolean;
      read_back: string | null;
    }>("POST", "/secrets/selftest", { value });
  }

  // ---------------------------------------------------------------- providers

  providerTypes(capability?: Capability) {
    const qs = capability ? `?capability=${capability}` : "";
    return this.request<{
      capabilities: { id: Capability; label: string }[];
      types: ProviderType[];
    }>("GET", `/providers/types${qs}`);
  }

  listProviders(capability?: Capability) {
    const qs = capability ? `?capability=${capability}` : "";
    return this.request<{
      instances: ProviderInstance[];
      defaults: Record<Capability, string | null>;
    }>("GET", `/providers${qs}`);
  }

  createProvider(input: {
    type: string;
    name?: string;
    config: Record<string, unknown>;
    make_default?: boolean;
  }) {
    return this.request<ProviderInstance>("POST", "/providers", input);
  }

  updateProvider(id: string, input: { name?: string; config?: Record<string, unknown> }) {
    return this.request<ProviderInstance>("PATCH", `/providers/${id}`, input);
  }

  deleteProvider(id: string) {
    return this.request<void>("DELETE", `/providers/${id}`);
  }

  setDefaultProvider(id: string) {
    return this.request<ProviderInstance>("POST", `/providers/${id}/default`);
  }

  testProvider(id: string) {
    return this.request<TestResult>("POST", `/providers/${id}/test`);
  }

  testProviderConfig(type: string, config: Record<string, unknown>, instanceId?: string) {
    return this.request<TestResult>("POST", "/providers/test", {
      type,
      config,
      instance_id: instanceId ?? null,
    });
  }

  providerModels(id: string) {
    return this.request<{ models: ModelInfo[] }>("GET", `/providers/${id}/models`);
  }

  providerVoices(id: string, lang = "vi") {
    return this.request<{ voices: VoiceInfo[]; default_voice: string | null }>(
      "GET",
      `/providers/${id}/voices?lang=${encodeURIComponent(lang)}`,
    );
  }

  voiceSample(id: string, voice?: string | null, text?: string) {
    return this.request<{ url: string; duration_s: number; cached: boolean }>(
      "POST",
      `/providers/${id}/sample`,
      { voice: voice || null, ...(text ? { text } : {}) },
    );
  }

  // ---------------------------------------------------------------- pipeline

  styles() {
    return this.request<StyleInfo[]>("GET", "/styles");
  }

  generationDefaults() {
    return this.request<{
      settings: GenerationSettings;
      render_steps: JobStep[];
      script_steps: JobStep[];
    }>("GET", "/generation/defaults");
  }

  projectSettings(projectId: string) {
    return this.request<{
      settings: Partial<GenerationSettings>;
      effective: GenerationSettings;
      defaults: Record<Capability, string | null>;
    }>("GET", `/projects/${projectId}/settings`);
  }

  saveProjectSettings(projectId: string, settings: Partial<GenerationSettings>) {
    return this.request<{ settings: Partial<GenerationSettings> }>(
      "PUT",
      `/projects/${projectId}/settings`,
      settings,
    );
  }

  createStoryboard(
    projectId: string,
    input: {
      document_id?: string | null;
      settings: Partial<GenerationSettings>;
      save_as_project_default?: boolean;
    },
  ) {
    return this.request<Job>("POST", `/projects/${projectId}/storyboards`, input);
  }

  listStoryboards(projectId: string) {
    return this.request<StoryboardSummary[]>("GET", `/projects/${projectId}/storyboards`);
  }

  getStoryboard(id: string) {
    return this.request<StoryboardRecord>("GET", `/storyboards/${id}`);
  }

  saveStoryboard(id: string, data: Storyboard, settings?: Partial<GenerationSettings>) {
    return this.request<StoryboardRecord>("PUT", `/storyboards/${id}`, {
      data,
      settings,
    });
  }

  deleteStoryboard(id: string) {
    return this.request<void>("DELETE", `/storyboards/${id}`);
  }

  regenerateScene(storyboardId: string, sceneId: string, instruction: string) {
    return this.request<Job>("POST", `/storyboards/${storyboardId}/scenes/${sceneId}/regenerate`, {
      instruction,
    });
  }

  synthesize(storyboardId: string, sceneIds?: string[]) {
    return this.request<Job>("POST", `/storyboards/${storyboardId}/tts`, {
      scene_ids: sceneIds ?? null,
    });
  }

  render(storyboardId: string, preview = false) {
    return this.request<{ job: Job; render_id: string }>(
      "POST",
      `/storyboards/${storyboardId}/render`,
      { preview },
    );
  }

  listRenders(projectId: string) {
    return this.request<RenderRecord[]>("GET", `/projects/${projectId}/renders`);
  }

  getRender(id: string) {
    return this.request<RenderRecord>("GET", `/renders/${id}`);
  }

  deleteRender(id: string) {
    return this.request<void>("DELETE", `/renders/${id}`);
  }

  openRenderFolder(id: string) {
    return this.request<{ path: string; opened: boolean }>("POST", `/renders/${id}/open-folder`);
  }

  uploadAsset(file: File, kind: AssetRecord["kind"], projectId?: string) {
    const form = new FormData();
    form.append("file", file, file.name);
    form.append("kind", kind);
    if (projectId) form.append("project_id", projectId);
    return this.request<AssetRecord>("POST", "/assets", form);
  }

  listAssets(params: { projectId?: string; kind?: AssetRecord["kind"] } = {}) {
    const q = new URLSearchParams();
    if (params.projectId) q.set("project_id", params.projectId);
    if (params.kind) q.set("kind", params.kind);
    const qs = q.toString();
    return this.request<AssetRecord[]>("GET", `/assets${qs ? `?${qs}` : ""}`);
  }

  getBranding() {
    return this.request<Branding>("GET", "/settings/branding");
  }

  saveBranding(value: Partial<Branding>) {
    return this.request<Branding>("PUT", "/settings/branding", value);
  }
}
