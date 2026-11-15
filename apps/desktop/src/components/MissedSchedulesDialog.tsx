import { useEffect, useState } from "react";
import { useApi, useAppEvents } from "../lib/backendContext";
import { formatDate, toLocalInput } from "../lib/format";
import type { Publication } from "../lib/types";
import { PlatformIcon } from "./badges";
import { Button, ErrorBox, Modal } from "./ui";

/** Shown at startup when scheduled publications were missed while the app was not running. */
export function MissedSchedulesDialog() {
  const api = useApi();
  const [missed, setMissed] = useState<Publication[]>([]);
  const [dismissed, setDismissed] = useState(false);
  const [mode, setMode] = useState<"choose" | "reschedule">("choose");
  const [at, setAt] = useState(() => toLocalInput(new Date(Date.now() + 30 * 60 * 1000)));
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = () => api.missedSchedules().then(setMissed).catch(() => undefined);
  useEffect(() => {
    void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [api]);
  useAppEvents((e) => {
    if (e.type === "publication" && e.status === "missed") {
      setDismissed(false);
      void load();
    }
  });

  if (dismissed || missed.length === 0) return null;

  async function resolve(action: "publish_now" | "reschedule" | "skip") {
    setBusy(true);
    setError(null);
    try {
      await api.resolveMissed(
        missed.map((p) => p.id),
        action,
        action === "reschedule" ? new Date(at).toISOString() : undefined,
      );
      setMissed([]);
      setMode("choose");
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal
      title={`Đã lỡ ${missed.length} lịch đăng`}
      onClose={() => setDismissed(true)}
      footer={
        mode === "choose" ? (
          <>
            <Button variant="ghost" disabled={busy} onClick={() => resolve("skip")} data-testid="missed-skip">
              Bỏ qua
            </Button>
            <Button variant="secondary" disabled={busy} onClick={() => setMode("reschedule")}>
              Hẹn lại…
            </Button>
            <Button disabled={busy} onClick={() => resolve("publish_now")} data-testid="missed-publish-now">
              Đăng ngay
            </Button>
          </>
        ) : (
          <>
            <Button variant="secondary" onClick={() => setMode("choose")}>
              Quay lại
            </Button>
            <Button disabled={busy} onClick={() => resolve("reschedule")}>
              Lưu lịch mới
            </Button>
          </>
        )
      }
    >
      <div className="space-y-3 text-sm">
        <p className="text-slate-600">
          Ứng dụng không chạy vào giờ hẹn nên các bài sau chưa được đăng. Bạn muốn làm gì?
        </p>
        <ul className="space-y-1.5" data-testid="missed-list">
          {missed.map((p) => (
            <li key={p.id} className="flex items-center gap-2 rounded-lg bg-slate-50 px-3 py-2">
              <PlatformIcon platform={p.platform} />
              <span className="flex-1 truncate font-medium">{p.video_title}</span>
              <span className="text-xs text-slate-500">
                {p.account_name} · hẹn {formatDate(p.scheduled_at)}
              </span>
            </li>
          ))}
        </ul>
        {mode === "reschedule" && (
          <label className="flex flex-col gap-1">
            <span className="font-medium">Thời điểm đăng mới</span>
            <input
              type="datetime-local"
              className="input w-auto"
              value={at}
              onChange={(e) => setAt(e.target.value)}
            />
          </label>
        )}
        <ErrorBox>{error}</ErrorBox>
      </div>
    </Modal>
  );
}
