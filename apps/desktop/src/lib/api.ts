import type { BackendInfo } from "./backend";
import type {
  Adapter,
  IRDocument,
  Job,
  Project,
  ProjectDetail,
  Source,
  SystemInfo,
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
    const headers: Record<string, string> = { Authorization: `Bearer ${this.info.token}` };
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
    return this.request<ProjectDetail>("POST", "/projects", { name, description });
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
    return this.request<{ backend: string; ok: boolean; read_back: string | null }>(
      "POST",
      "/secrets/selftest",
      { value },
    );
  }
}
