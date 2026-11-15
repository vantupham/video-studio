import { useCallback, useEffect, useState } from "react";
import { PublicationsList } from "../components/PublishPanel";
import { Button, Card, ErrorBox } from "../components/ui";
import { useApi, useAppEvents, useBackend } from "../lib/backendContext";
import type { Publication } from "../lib/types";

const FILTERS: { id: string; label: string; status?: string }[] = [
  { id: "upcoming", label: "Sắp đăng", status: "queued,uploading,processing,scheduled,scheduled_remote,retry_wait,missed" },
  { id: "published", label: "Đã đăng", status: "published" },
  { id: "failed", label: "Lỗi", status: "failed" },
  { id: "all", label: "Tất cả" },
];

/** All publications across videos: the scheduling queue. */
export function PublicationsPage() {
  const api = useApi();
  const { queuePaused, setQueuePaused } = useBackend();
  const [filter, setFilter] = useState("upcoming");
  const [pubs, setPubs] = useState<Publication[]>([]);
  const [error, setError] = useState<string | null>(null);
  const status = FILTERS.find((f) => f.id === filter)?.status;

  const load = useCallback(
    () =>
      api
        .listPublications({ status })
        .then((list) => {
          setPubs(
            filter === "upcoming"
              ? [...list].sort((a, b) =>
                  (a.scheduled_at ?? a.created_at).localeCompare(b.scheduled_at ?? b.created_at),
                )
              : list,
          );
          setError(null);
        })
        .catch((e: Error) => setError(e.message)),
    [api, status, filter],
  );
  useEffect(() => {
    void load();
  }, [load]);
  useAppEvents((e) => {
    if (e.type === "publication") void load();
  });

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold">Lịch đăng</h1>
          <p className="text-sm text-slate-600">
            Mọi bài đăng đã lên lịch, đang tải lên hoặc đã đăng.
          </p>
        </div>
        <Button variant="secondary" onClick={() => setQueuePaused(!queuePaused)}>
          {queuePaused ? "▶ Tiếp tục hàng đợi" : "⏸ Tạm dừng hàng đợi"}
        </Button>
      </div>
      <div className="flex gap-1.5">
        {FILTERS.map((f) => (
          <button
            key={f.id}
            type="button"
            onClick={() => setFilter(f.id)}
            className={`rounded-full px-3 py-1 text-sm ${
              filter === f.id
                ? "bg-indigo-600 text-white"
                : "bg-white text-slate-700 ring-1 ring-slate-200 hover:bg-slate-50"
            }`}
          >
            {f.label}
          </button>
        ))}
      </div>
      <ErrorBox>{error}</ErrorBox>
      <Card>
        <PublicationsList pubs={pubs} onChanged={load} showVideo />
      </Card>
    </div>
  );
}
