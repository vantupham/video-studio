import { useState } from "react";
import { useApi } from "../lib/backendContext";
import { formatBytes, formatDate } from "../lib/format";
import type { RenderRecord } from "../lib/types";
import { Button } from "./ui";

const STATUS: Record<RenderRecord["status"], { label: string; cls: string }> = {
  queued: { label: "Đang chờ", cls: "bg-slate-100 text-slate-700" },
  running: { label: "Đang dựng", cls: "bg-sky-100 text-sky-800" },
  done: { label: "Hoàn tất", cls: "bg-emerald-100 text-emerald-800" },
  failed: { label: "Lỗi", cls: "bg-rose-100 text-rose-800" },
  cancelled: { label: "Đã huỷ", cls: "bg-amber-100 text-amber-800" },
};

const OUTPUT_LABELS: Record<string, string> = {
  video: "Video MP4",
  srt: "Phụ đề SRT",
  vtt: "Phụ đề VTT",
  ass: "Phụ đề ASS",
  thumbnail: "Ảnh bìa",
};

function formatDuration(s: number | null): string {
  if (s === null) return "";
  const m = Math.floor(s / 60);
  const sec = Math.round(s % 60);
  return `${m}:${String(sec).padStart(2, "0")}`;
}

export function RenderPlayer({ render }: { render: RenderRecord }) {
  const api = useApi();
  const [opened, setOpened] = useState<string | null>(null);
  const vertical = render.aspect === "9:16";

  async function openFolder() {
    try {
      const res = await api.openRenderFolder(render.id);
      setOpened(res.opened ? null : res.path);
    } catch (e) {
      setOpened((e as Error).message);
    }
  }

  return (
    <div className="space-y-3" data-testid="render-player">
      <video
        key={render.id}
        controls
        preload="metadata"
        crossOrigin="anonymous"
        poster={render.urls.thumbnail ? api.mediaUrl(render.urls.thumbnail) : undefined}
        className={`rounded-lg bg-black ${vertical ? "mx-auto max-h-[70vh]" : "w-full"}`}
        src={api.mediaUrl(render.urls.video)}
      >
        {render.urls.vtt && (
          <track
            kind="subtitles"
            srcLang="vi"
            label="Tiếng Việt"
            src={api.mediaUrl(render.urls.vtt)}
            default={!render.settings.burned_subtitles}
          />
        )}
      </video>
      <div className="flex flex-wrap items-center gap-2">
        <Button variant="secondary" className="text-xs" onClick={openFolder}>
          Mở thư mục
        </Button>
        {Object.entries(render.urls).map(([k, url]) => (
          <a
            key={k}
            href={api.mediaUrl(url)}
            download
            className="rounded-lg px-2.5 py-1.5 text-xs text-indigo-700 hover:bg-indigo-50"
          >
            ⬇ {OUTPUT_LABELS[k] ?? k}
          </a>
        ))}
      </div>
      {opened && (
        <p className="text-xs text-slate-600">
          Thư mục: <code className="rounded bg-slate-100 px-1">{opened}</code>
        </p>
      )}
    </div>
  );
}

export function RendersList({
  renders,
  onDeleted,
}: {
  renders: RenderRecord[];
  onDeleted?: () => void;
}) {
  const api = useApi();
  const [selected, setSelected] = useState<string | null>(null);
  const done = renders.filter((r) => r.status === "done");
  const current = renders.find((r) => r.id === selected) ?? done[0];

  if (renders.length === 0) {
    return <p className="text-sm text-slate-500">Chưa có video nào được dựng.</p>;
  }

  return (
    <div className="space-y-4">
      {current && current.status === "done" && <RenderPlayer render={current} />}
      <ul className="divide-y divide-slate-100 text-sm">
        {renders.map((r) => (
          <li key={r.id} className="flex flex-wrap items-center gap-3 py-2">
            <span
              className={`rounded-full px-2 py-0.5 text-xs font-medium ${STATUS[r.status].cls}`}
            >
              {STATUS[r.status].label}
            </span>
            <span className="font-medium">
              {r.style} · {r.aspect}
              {r.settings.preview ? " · xem nhanh" : ""}
            </span>
            <span className="text-xs text-slate-500">
              {[
                r.settings.width && `${r.settings.width}×${r.settings.height}`,
                formatDuration(r.duration_s),
                r.size_bytes !== null && formatBytes(r.size_bytes),
                r.settings.encoder,
                formatDate(r.created_at),
              ]
                .filter(Boolean)
                .join(" · ")}
            </span>
            {r.error && <span className="w-full text-xs text-rose-700">{r.error}</span>}
            <div className="ml-auto flex gap-1">
              {r.status === "done" && r.id !== current?.id && (
                <Button variant="ghost" className="text-xs" onClick={() => setSelected(r.id)}>
                  Xem
                </Button>
              )}
              {r.status !== "running" && r.status !== "queued" && (
                <Button
                  variant="ghost"
                  className="text-xs"
                  onClick={async () => {
                    if (!window.confirm("Xoá bản xuất này và các tệp của nó?")) return;
                    await api.deleteRender(r.id).catch(() => undefined);
                    onDeleted?.();
                  }}
                >
                  Xoá
                </Button>
              )}
            </div>
          </li>
        ))}
      </ul>
    </div>
  );
}
