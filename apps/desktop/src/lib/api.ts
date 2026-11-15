import type { BackendInfo } from "./backend";
import type {
  Adapter,
  AppNotification,
  DesktopPrefs,
  MetadataSuggestion,
  OAuthFlow,
  Publication,
  PublishCheck,
  PublisherInfo,
  PublishMeta,
  RenderDiff,
  ScheduleInput,
  SocialAccount,
  SocialAppConfig,
  VideoDetail,
  VideoLibrary,
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
  /** Raw ``detail`` from the response (e.g. ``{message, issues}`` from publishing). */
  readonly detail: unknown;

  constructor(status: number, message: string, detail?: unknown) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
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
  /** Folder or multi-file upload: files with their relative paths (Markdown + images). */
  files?: { file: File; path: string }[];
  /** Relative path of the main document inside ``files`` (auto-detected when omitted). */
  main?: string;
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
    if (!res.ok) {
      const detail = data && typeof data === "object" && "detail" in data
        ? (data as { detail: unknown }).detail
        : undefined;
      throw new ApiError(res.status, errorMessage(res.status, data), detail);
    }
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
    for (const f of input.files ?? []) {
      form.append("files", f.file, f.file.name);
      form.append("paths", f.path);
    }
    if (input.main) form.append("main", input.main);
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

  render(storyboardId: string, preview = false, aspect?: string) {
    return this.request<{ job: Job; render_id: string }>(
      "POST",
      `/storyboards/${storyboardId}/render`,
      { preview, ...(aspect ? { aspect } : {}) },
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

  // ---------------------------------------------------------------- videos & review

  listVideos(params: { status?: string; projectId?: string; q?: string; platform?: string } = {}) {
    const q = new URLSearchParams();
    if (params.status) q.set("status", params.status);
    if (params.projectId) q.set("project_id", params.projectId);
    if (params.q) q.set("q", params.q);
    if (params.platform) q.set("platform", params.platform);
    const qs = q.toString();
    return this.request<VideoLibrary>("GET", `/videos${qs ? `?${qs}` : ""}`);
  }

  getVideo(id: string) {
    return this.request<VideoDetail>("GET", `/videos/${id}`);
  }

  videoAction(id: string, action: string, note = "") {
    return this.request<VideoDetail>("POST", `/videos/${id}/actions`, { action, note });
  }

  compareRenders(id: string, a: string, b = "current") {
    return this.request<RenderDiff>(
      "GET",
      `/videos/${id}/compare?a=${encodeURIComponent(a)}&b=${encodeURIComponent(b)}`,
    );
  }

  // ---------------------------------------------------------------- publishing

  publishMeta(id: string) {
    return this.request<PublishMeta>("GET", `/videos/${id}/publish-meta`);
  }

  savePublishMeta(id: string, patch: Partial<PublishMeta> | Record<string, unknown>) {
    return this.request<PublishMeta>("PUT", `/videos/${id}/publish-meta`, patch);
  }

  suggestMetadata(id: string, platform: string) {
    return this.request<MetadataSuggestion>("POST", `/videos/${id}/suggest-metadata`, {
      platform,
    });
  }

  thumbnail(
    id: string,
    body: {
      mode?: string;
      frame_s?: number | null;
      show_title?: boolean;
      text?: string;
      render_id?: string;
    } = {},
  ) {
    return this.request<{ thumbnail: PublishMeta["thumbnail"]; url: string | null; aspect: string }>(
      "POST",
      `/videos/${id}/thumbnail`,
      body,
    );
  }

  uploadThumbnail(id: string, file: File) {
    const form = new FormData();
    form.append("file", file, file.name);
    return this.request<{ thumbnail: PublishMeta["thumbnail"]; url: string | null }>(
      "POST",
      `/videos/${id}/thumbnail/upload`,
      form,
    );
  }

  validatePublication(
    id: string,
    body: { account_id: string; options?: Record<string, unknown>; schedule?: ScheduleInput;
      render_id?: string },
  ) {
    return this.request<PublishCheck>("POST", `/videos/${id}/validate`, body);
  }

  createPublications(
    id: string,
    items: {
      account_id: string;
      options?: Record<string, unknown>;
      schedule?: ScheduleInput;
      render_id?: string;
      allow_repost?: boolean;
    }[],
  ) {
    return this.request<Publication[]>("POST", `/videos/${id}/publications`, { items });
  }

  listPublications(params: { videoId?: string; status?: string } = {}) {
    const q = new URLSearchParams();
    if (params.videoId) q.set("video_id", params.videoId);
    if (params.status) q.set("status", params.status);
    const qs = q.toString();
    return this.request<Publication[]>("GET", `/publications${qs ? `?${qs}` : ""}`);
  }

  cancelPublication(id: string) {
    return this.request<Publication>("POST", `/publications/${id}/cancel`);
  }

  retryPublication(id: string) {
    return this.request<Publication>("POST", `/publications/${id}/retry`);
  }

  reschedulePublication(id: string, at: string) {
    return this.request<Publication>("POST", `/publications/${id}/reschedule`, { at });
  }

  missedSchedules() {
    return this.request<Publication[]>("GET", "/schedule/missed");
  }

  resolveMissed(ids: string[], action: "publish_now" | "reschedule" | "skip", at?: string) {
    return this.request<Publication[]>("POST", "/schedule/missed/resolve", {
      ids,
      action,
      at: at ?? null,
    });
  }

  queueState() {
    return this.request<{ paused: boolean }>("GET", "/queue/state");
  }

  setQueuePaused(paused: boolean) {
    return this.request<{ paused: boolean }>("PUT", "/queue/state", { paused });
  }

  desktopPrefs() {
    return this.request<DesktopPrefs>("GET", "/settings/desktop");
  }

  saveDesktopPrefs(patch: Partial<DesktopPrefs>) {
    return this.request<DesktopPrefs>("PUT", "/settings/desktop", patch);
  }

  notifications() {
    return this.request<AppNotification[]>("GET", "/notifications");
  }

  markNotificationsRead() {
    return this.request<void>("POST", "/notifications/read");
  }

  // ---------------------------------------------------------------- social accounts

  socialApps() {
    return this.request<SocialAppConfig[]>("GET", "/social/apps");
  }

  saveSocialApp(
    platform: string,
    body: { client_id?: string; client_secret?: string; testing_mode?: boolean },
  ) {
    return this.request<SocialAppConfig>("PUT", `/social/apps/${platform}`, body);
  }

  clearSocialAppSecret(platform: string) {
    return this.request<SocialAppConfig>("DELETE", `/social/apps/${platform}/secret`);
  }

  socialAccounts() {
    return this.request<SocialAccount[]>("GET", "/social/accounts");
  }

  publishers() {
    return this.request<PublisherInfo[]>("GET", "/social/publishers");
  }

  connectMockAccount(platform: string) {
    return this.request<SocialAccount>("POST", "/social/accounts/mock", { platform });
  }

  disconnectAccount(id: string) {
    return this.request<void>("DELETE", `/social/accounts/${id}`);
  }

  connectYouTube(openBrowser = true) {
    return this.request<OAuthFlow>("POST", "/social/youtube/connect", {
      open_browser: openBrowser,
    });
  }

  oauthFlow(id: string) {
    return this.request<OAuthFlow>("GET", `/social/oauth/${id}`);
  }

  connectFacebook() {
    return this.request<OAuthFlow>("POST", "/social/facebook/connect");
  }

  completeFacebook(flowId: string, url: string) {
    return this.request<OAuthFlow>("POST", "/social/facebook/complete", { flow_id: flowId, url });
  }

  selectFacebookPages(flowId: string, pageIds: string[]) {
    return this.request<OAuthFlow>("POST", "/social/facebook/pages", {
      flow_id: flowId,
      page_ids: pageIds,
    });
  }
}
