import { describe, expect, it, vi } from "vitest";
import { ApiClient, ApiError } from "./api";

const info = { baseUrl: "http://127.0.0.1:9999", token: "secret-token", mode: "browser" as const };

function jsonResponse(status: number, body: unknown) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

describe("ApiClient", () => {
  it("sends the bearer token and JSON body", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(201, { id: "p1", name: "Dự án" }));
    const api = new ApiClient(info, fetchMock);
    const project = await api.createProject("Dự án", "mô tả");
    expect(project.id).toBe("p1");
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("http://127.0.0.1:9999/projects");
    expect(init.method).toBe("POST");
    expect(init.headers.Authorization).toBe("Bearer secret-token");
    expect(JSON.parse(init.body)).toEqual({ name: "Dự án", description: "mô tả" });
  });

  it("uses multipart form data for sources", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(201, { id: "s1" }));
    const api = new ApiClient(info, fetchMock);
    await api.addSource("p1", { text: "Xin chào", adapterId: "script", config: { language: "vi" } });
    const init = fetchMock.mock.calls[0][1];
    expect(init.body).toBeInstanceOf(FormData);
    expect(init.headers["Content-Type"]).toBeUndefined();
    const form = init.body as FormData;
    expect(form.get("text")).toBe("Xin chào");
    expect(form.get("adapter_id")).toBe("script");
    expect(JSON.parse(String(form.get("config")))).toEqual({ language: "vi" });
  });

  it("raises ApiError with the FastAPI detail message", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(401, { detail: "Missing or invalid token" }));
    const api = new ApiClient(info, fetchMock);
    await expect(api.health()).rejects.toEqual(
      new ApiError(401, "Missing or invalid token", "Missing or invalid token"),
    );
  });

  it("builds websocket URLs with the token as query param", () => {
    const api = new ApiClient(info, vi.fn());
    expect(api.wsUrl("/ws/jobs", { project_id: "p1" })).toBe(
      "ws://127.0.0.1:9999/ws/jobs?token=secret-token&project_id=p1",
    );
  });
});

describe("ApiClient — phase 2", () => {
  it("sends folder uploads as files + relative paths", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(201, { id: "s1" }));
    const api = new ApiClient(info, fetchMock);
    const md = new File(["# Bài học\n![](img/a.png)"], "bai.md");
    const img = new File([new Uint8Array([1, 2])], "a.png");
    await api.addSource("p1", {
      files: [
        { file: md, path: "bai-hoc/bai.md" },
        { file: img, path: "bai-hoc/img/a.png" },
      ],
      main: "bai-hoc/bai.md",
    });
    const form = fetchMock.mock.calls[0][1].body as FormData;
    expect(form.getAll("files")).toHaveLength(2);
    expect(form.getAll("paths")).toEqual(["bai-hoc/bai.md", "bai-hoc/img/a.png"]);
    expect(form.get("main")).toBe("bai-hoc/bai.md");
    expect(form.get("file")).toBeNull();
  });

  it("builds library filters and publication payloads", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse(200, { items: [], counts: {}, states: [] }))
      .mockResolvedValueOnce(jsonResponse(201, []));
    const api = new ApiClient(info, fetchMock);
    await api.listVideos({ status: "approved,scheduled", q: "quang hợp" });
    expect(fetchMock.mock.calls[0][0]).toBe(
      "http://127.0.0.1:9999/videos?status=approved%2Cscheduled&q=quang+h%E1%BB%A3p",
    );
    await api.createPublications("v1", [
      { account_id: "a1", schedule: { mode: "local", at: "2026-01-01T10:00:00.000Z" } },
    ]);
    expect(fetchMock.mock.calls[1][0]).toBe("http://127.0.0.1:9999/videos/v1/publications");
    expect(JSON.parse(fetchMock.mock.calls[1][1].body)).toEqual({
      items: [{ account_id: "a1", schedule: { mode: "local", at: "2026-01-01T10:00:00.000Z" } }],
    });
  });

  it("keeps structured error details (publish validation issues)", async () => {
    const detail = {
      message: "Video chưa được duyệt",
      issues: { a1: [{ level: "error", code: "not_approved", message: "Video chưa được duyệt" }] },
    };
    const api = new ApiClient(info, vi.fn().mockResolvedValue(jsonResponse(422, { detail })));
    const err = (await api.createPublications("v1", [{ account_id: "a1" }]).catch(
      (e: unknown) => e,
    )) as ApiError;
    expect(err).toBeInstanceOf(ApiError);
    expect(err.message).toBe("Video chưa được duyệt");
    expect(err.detail).toEqual(detail);
  });
});
