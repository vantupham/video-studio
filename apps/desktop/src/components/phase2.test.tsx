import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { MemoryRouter } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { ApiClient } from "../lib/api";
import { BackendContext, type BackendContextValue } from "../lib/backendContext";
import { CLEAR_SECRET, missingRequired, toPayload } from "../lib/schemaForm";
import type { JSONSchemaObject, Publication, TimelineEntry } from "../lib/types";
import { MissedSchedulesDialog } from "./MissedSchedulesDialog";
import { PublicationsList } from "./PublishPanel";
import { SchemaForm } from "./SchemaForm";
import { VideoPlayer } from "./VideoPlayer";

afterEach(cleanup);

function withBackend(api: Partial<ApiClient>, children: ReactNode) {
  const value: BackendContextValue = {
    status: "ready",
    error: null,
    attempts: 1,
    api: { mediaUrl: (p: string) => `http://x${p}`, ...api } as ApiClient,
    wsConnected: true,
    jobs: {},
    logs: {},
    upsertJob: () => undefined,
    retry: () => undefined,
    subscribe: () => () => undefined,
    notifications: [],
    markNotificationsRead: () => undefined,
    toasts: [],
    dismissToast: () => undefined,
    queuePaused: false,
    setQueuePaused: async () => undefined,
  };
  return (
    <MemoryRouter>
      <BackendContext.Provider value={value}>{children}</BackendContext.Provider>
    </MemoryRouter>
  );
}

const secretSchema: JSONSchemaObject = {
  type: "object",
  required: ["api_key"],
  properties: { api_key: { type: "string", title: "API key", "x-secret": true } },
};

describe("clearing a stored secret", () => {
  it("offers a clear button only when a secret is stored and sends an empty value", () => {
    const onChange = vi.fn();
    const { rerender } = render(
      <SchemaForm schema={secretSchema} value={{}} onChange={onChange} storedSecrets={{}} />,
    );
    expect(screen.queryByText("Xoá khoá")).toBeNull();
    rerender(
      <SchemaForm
        schema={secretSchema}
        value={{}}
        onChange={onChange}
        storedSecrets={{ api_key: "••••1234" }}
      />,
    );
    fireEvent.click(screen.getByText("Xoá khoá"));
    expect(onChange).toHaveBeenLastCalledWith({ api_key: CLEAR_SECRET });

    rerender(
      <SchemaForm
        schema={secretSchema}
        value={{ api_key: CLEAR_SECRET }}
        onChange={onChange}
        storedSecrets={{ api_key: "••••1234" }}
      />,
    );
    expect(screen.getByPlaceholderText(/sẽ bị xoá/)).toBeTruthy();
    fireEvent.click(screen.getByText("Hoàn tác"));
    expect(onChange).toHaveBeenLastCalledWith({ api_key: "" });

    expect(toPayload(secretSchema, { api_key: CLEAR_SECRET })).toEqual({ api_key: "" });
    expect(toPayload(secretSchema, { api_key: "" })).toEqual({});
    expect(missingRequired(secretSchema, { api_key: CLEAR_SECRET }, { api_key: "••••1234" })).toEqual(
      ["API key"],
    );
  });
});

describe("VideoPlayer timeline", () => {
  const timeline: TimelineEntry[] = [
    { scene_id: "intro", kind: "intro", start: 0, end: 2 },
    { scene_id: "s1", kind: "scene", start: 2, end: 8 },
    { scene_id: "s2", kind: "scene", start: 8, end: 12 },
    { scene_id: "outro", kind: "outro", start: 12, end: 14 },
  ];

  it("renders one proportional segment per scene and selects scenes on click", () => {
    const onSelect = vi.fn();
    render(
      <VideoPlayer src="/v.mp4" timeline={timeline} aspect="16:9" onSelectScene={onSelect} />,
    );
    const segments = screen.getAllByTestId("timeline-segment");
    expect(segments.map((s) => s.textContent)).toEqual(["Mở", "1", "2", "Kết"]);
    expect(parseFloat(segments[1].style.width)).toBeCloseTo((6 / 14) * 100, 2);
    fireEvent.click(segments[2]);
    expect(onSelect).toHaveBeenLastCalledWith("s2");
    const video = screen.getByTestId("review-video") as HTMLVideoElement;
    expect(video.currentTime).toBeCloseTo(8.05);
    fireEvent.click(segments[0]);
    expect(onSelect).toHaveBeenLastCalledWith(null);
  });
});

const basePub: Publication = {
  id: "pub1",
  storyboard_id: "v1",
  video_title: "Quang hợp",
  render_id: "r1",
  account_id: "a1",
  account_name: "Kênh thử",
  publisher_id: "mock_youtube",
  platform: "youtube",
  mock: true,
  status: "published",
  meta: { title: "Quang hợp ở thực vật", aspect: "16:9" },
  schedule_mode: "local",
  scheduled_at: "2026-11-15T10:00:00Z",
  next_attempt_at: null,
  attempts: 1,
  progress: 1,
  remote_id: "x",
  remote_url: "/mock-posts/x?k=1",
  warnings: [],
  job_id: null,
  error: null,
  created_at: "2026-11-15T09:00:00Z",
  updated_at: "2026-11-15T10:00:00Z",
  published_at: "2026-11-15T10:00:05Z",
  resumable: false,
};

describe("PublicationsList", () => {
  it("shows mock posts with a link and offers retry for failures", async () => {
    const retry = vi.fn().mockResolvedValue(basePub);
    const onChanged = vi.fn();
    render(
      withBackend(
        { retryPublication: retry },
        <PublicationsList
          pubs={[
            basePub,
            { ...basePub, id: "pub2", status: "failed", error: "Mất mạng", remote_url: null },
          ]}
          onChanged={onChanged}
        />,
      ),
    );
    expect(screen.getAllByText("GIẢ LẬP")).toHaveLength(2);
    expect(screen.getByText("Đã đăng")).toBeTruthy();
    expect(screen.getAllByTestId("open-post")).toHaveLength(1);
    expect(screen.getByText("Mất mạng")).toBeTruthy();
    fireEvent.click(screen.getByText("Thử lại"));
    await waitFor(() => expect(onChanged).toHaveBeenCalled());
    expect(retry).toHaveBeenCalledWith("pub2");
  });
});

describe("MissedSchedulesDialog", () => {
  it("lists missed publications and publishes them now", async () => {
    const missed = { ...basePub, status: "missed" as const, remote_url: null };
    const resolve = vi.fn().mockResolvedValue([]);
    render(
      withBackend(
        { missedSchedules: vi.fn().mockResolvedValue([missed]), resolveMissed: resolve },
        <MissedSchedulesDialog />,
      ),
    );
    expect(await screen.findByText("Đã lỡ 1 lịch đăng")).toBeTruthy();
    expect(screen.getByTestId("missed-list").textContent).toContain("Quang hợp");
    fireEvent.click(screen.getByTestId("missed-publish-now"));
    await waitFor(() => expect(resolve).toHaveBeenCalledWith(["pub1"], "publish_now", undefined));
    await waitFor(() => expect(screen.queryByText("Đã lỡ 1 lịch đăng")).toBeNull());
  });

  it("stays hidden when nothing was missed", async () => {
    const list = vi.fn().mockResolvedValue([]);
    render(withBackend({ missedSchedules: list }, <MissedSchedulesDialog />));
    await waitFor(() => expect(list).toHaveBeenCalled());
    expect(screen.queryByRole("dialog")).toBeNull();
  });
});
