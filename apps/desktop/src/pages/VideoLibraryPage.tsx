import { useCallback, useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router";
import { PlatformIcon, PublicationBadge, VideoStatusBadge } from "../components/badges";
import { ErrorBox } from "../components/ui";
import { useApi, useAppEvents } from "../lib/backendContext";
import { formatDate } from "../lib/format";
import type { Project, VideoLibrary, VideoSummary } from "../lib/types";

const GROUPS: { id: string; label: string; statuses: string[] }[] = [
  { id: "", label: "Tất cả", statuses: [] },
  { id: "review", label: "Chờ duyệt", statuses: ["rendered", "in_review"] },
  { id: "changes", label: "Cần sửa", statuses: ["changes_requested"] },
  { id: "approved", label: "Đã duyệt", statuses: ["approved"] },
  { id: "scheduled", label: "Đã lên lịch", statuses: ["scheduled", "publishing"] },
  { id: "published", label: "Đã đăng", statuses: ["published"] },
  { id: "failed", label: "Lỗi", statuses: ["failed"] },
  { id: "draft", label: "Nháp / đang dựng", statuses: ["draft", "generating"] },
];

function formatDuration(s: number | null) {
  if (!s) return "—";
  const m = Math.floor(s / 60);
  return `${m}:${String(Math.round(s % 60)).padStart(2, "0")}`;
}

function VideoCard({ video }: { video: VideoSummary }) {
  const api = useApi();
  return (
    <Link
      to={`/videos/${video.id}`}
      className="group flex flex-col overflow-hidden rounded-xl border border-slate-200 bg-white shadow-sm transition hover:border-indigo-300 hover:shadow"
      data-testid="video-card"
    >
      <div className="relative aspect-video bg-slate-800">
        {video.thumbnail_url ? (
          <img
            src={api.mediaUrl(video.thumbnail_url)}
            alt=""
            className="size-full object-contain"
            loading="lazy"
          />
        ) : (
          <div className="grid size-full place-items-center text-xs text-slate-400">
            {video.rendering ? "Đang dựng…" : "Chưa dựng"}
          </div>
        )}
        <span className="absolute right-2 bottom-2 rounded bg-black/70 px-1.5 py-0.5 text-[11px] text-white">
          {video.aspect} · {formatDuration(video.duration_s)}
        </span>
        {video.outdated && (
          <span className="absolute top-2 left-2 rounded bg-amber-500 px-1.5 py-0.5 text-[11px] font-medium text-white">
            Có chỉnh sửa chưa dựng
          </span>
        )}
      </div>
      <div className="flex flex-1 flex-col gap-2 p-3">
        <div className="flex items-start justify-between gap-2">
          <p className="line-clamp-2 font-medium text-slate-900 group-hover:text-indigo-700">
            {video.title}
          </p>
          <VideoStatusBadge status={video.status} label={video.status_label} />
        </div>
        <p className="text-xs text-slate-500">
          {video.project_name} · {video.scenes} cảnh · cập nhật {formatDate(video.status_changed_at)}
        </p>
        {video.publications.length > 0 && (
          <ul className="mt-auto space-y-1">
            {video.publications.map((p) => (
              <li key={p.id} className="flex items-center gap-2 text-xs">
                <PlatformIcon platform={p.platform} />
                <PublicationBadge status={p.status} />
                {p.status === "scheduled" && p.scheduled_at && (
                  <span className="text-slate-500">{formatDate(p.scheduled_at)}</span>
                )}
              </li>
            ))}
          </ul>
        )}
      </div>
    </Link>
  );
}

export function VideoLibraryPage() {
  const api = useApi();
  const [params, setParams] = useSearchParams();
  const group = params.get("group") ?? "";
  const projectId = params.get("project") ?? "";
  const platform = params.get("platform") ?? "";
  const [q, setQ] = useState(params.get("q") ?? "");
  const [data, setData] = useState<VideoLibrary | null>(null);
  const [projects, setProjects] = useState<Project[]>([]);
  const [error, setError] = useState<string | null>(null);

  const statuses = GROUPS.find((g) => g.id === group)?.statuses ?? [];
  const statusKey = statuses.join(",");
  const query = params.get("q") ?? "";

  const load = useCallback(() => {
    api
      .listVideos({ status: statusKey || undefined, projectId: projectId || undefined,
        q: query || undefined, platform: platform || undefined })
      .then((d) => {
        setData(d);
        setError(null);
      })
      .catch((e: Error) => setError(e.message));
  }, [api, statusKey, projectId, query, platform]);

  useEffect(load, [load]);
  useEffect(() => {
    api.listProjects().then(setProjects).catch(() => undefined);
  }, [api]);
  useAppEvents((e) => {
    if (e.type === "video" || e.type === "publication") load();
  });

  const setParam = (key: string, value: string) => {
    const next = new URLSearchParams(params);
    if (value) next.set(key, value);
    else next.delete(key);
    setParams(next, { replace: true });
  };

  const countFor = (g: (typeof GROUPS)[number]) =>
    data
      ? g.statuses.length
        ? g.statuses.reduce((n, st) => n + (data.counts[st as keyof typeof data.counts] ?? 0), 0)
        : Object.values(data.counts).reduce((a, b) => a + b, 0)
      : 0;

  return (
    <div className="space-y-5">
      <div>
        <h1 className="text-2xl font-bold">Thư viện video</h1>
        <p className="text-sm text-slate-600">
          Mọi video của bạn: duyệt, sửa, lên lịch và đăng lên mạng xã hội.
        </p>
      </div>

      <div className="flex flex-wrap gap-1.5" role="tablist" aria-label="Lọc theo trạng thái">
        {GROUPS.map((g) => (
          <button
            key={g.id}
            type="button"
            role="tab"
            aria-selected={group === g.id}
            onClick={() => setParam("group", g.id)}
            className={`rounded-full px-3 py-1 text-sm ${
              group === g.id
                ? "bg-indigo-600 text-white"
                : "bg-white text-slate-700 ring-1 ring-slate-200 hover:bg-slate-50"
            }`}
          >
            {g.label}
            <span className={`ml-1.5 text-xs ${group === g.id ? "text-indigo-100" : "text-slate-400"}`}>
              {countFor(g)}
            </span>
          </button>
        ))}
      </div>

      <div className="flex flex-wrap gap-2">
        <form
          className="flex flex-1 gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            setParam("q", q.trim());
          }}
        >
          <input
            className="input flex-1"
            placeholder="Tìm theo tiêu đề hoặc dự án…"
            aria-label="Tìm video"
            value={q}
            onChange={(e) => setQ(e.target.value)}
          />
        </form>
        <select
          className="input w-auto"
          aria-label="Dự án"
          value={projectId}
          onChange={(e) => setParam("project", e.target.value)}
        >
          <option value="">Mọi dự án</option>
          {projects.map((p) => (
            <option key={p.id} value={p.id}>
              {p.name}
            </option>
          ))}
        </select>
        <select
          className="input w-auto"
          aria-label="Nền tảng"
          value={platform}
          onChange={(e) => setParam("platform", e.target.value)}
        >
          <option value="">Mọi nền tảng</option>
          <option value="youtube">Đã đăng / lên lịch YouTube</option>
          <option value="facebook">Đã đăng / lên lịch Facebook</option>
        </select>
      </div>

      <ErrorBox>{error}</ErrorBox>

      {data && data.items.length === 0 && (
        <div className="rounded-xl border border-dashed border-slate-300 p-10 text-center text-sm text-slate-500">
          Chưa có video nào khớp bộ lọc. Tạo video từ một{" "}
          <Link to="/" className="text-indigo-700 hover:underline">
            dự án
          </Link>
          .
        </div>
      )}
      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
        {data?.items.map((v) => <VideoCard key={v.id} video={v} />)}
      </div>
    </div>
  );
}
