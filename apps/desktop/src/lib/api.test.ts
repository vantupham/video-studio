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
    await expect(api.health()).rejects.toEqual(new ApiError(401, "Missing or invalid token"));
  });

  it("builds websocket URLs with the token as query param", () => {
    const api = new ApiClient(info, vi.fn());
    expect(api.wsUrl("/ws/jobs", { project_id: "p1" })).toBe(
      "ws://127.0.0.1:9999/ws/jobs?token=secret-token&project_id=p1",
    );
  });
});
