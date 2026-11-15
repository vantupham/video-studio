import { useEffect, useState } from "react";
import { useApi } from "../lib/backendContext";
import type { RenderDiff, RenderVersion, SceneDiff } from "../lib/types";
import { ErrorBox, Modal, Pill } from "./ui";

const CHANGE_STYLE: Record<SceneDiff["change"], { label: string; style: string }> = {
  added: { label: "Thêm mới", style: "bg-emerald-100 text-emerald-800" },
  removed: { label: "Đã xoá", style: "bg-rose-100 text-rose-800" },
  changed: { label: "Đã sửa", style: "bg-amber-100 text-amber-800" },
  same: { label: "Không đổi", style: "bg-slate-100 text-slate-600" },
};

const FIELD_LABELS: Record<string, string> = {
  narration: "lời thoại",
  duration: "thời lượng",
  visual: "hình ảnh",
  overlays: "chữ phủ",
};

function versionLabel(r: RenderVersion) {
  const kind = r.kind === "preview" ? "Xem nhanh" : r.kind === "variant" ? `Bản ${r.aspect}` : "";
  return r.version ? `Phiên bản ${r.version}` : `${kind} ${new Date(r.created_at).toLocaleString("vi-VN")}`;
}

function visualText(v: unknown): string {
  if (!v || typeof v !== "object") return "";
  const o = v as Record<string, unknown>;
  return [o.type, o.title, Array.isArray(o.bullets) ? o.bullets.join(" · ") : null, o.image, o.query]
    .filter(Boolean)
    .join(" | ");
}

function SceneRow({ d, index }: { d: SceneDiff; index: number }) {
  const before = d.before as Record<string, unknown> | null;
  const after = d.after as Record<string, unknown> | null;
  const info = CHANGE_STYLE[d.change];
  return (
    <li className="rounded-lg border border-slate-200 p-3" data-testid="diff-scene">
      <div className="mb-2 flex items-center gap-2 text-sm">
        <span className="font-medium">Cảnh {index + 1}</span>
        <span className="font-mono text-xs text-slate-400">{d.scene_id}</span>
        <Pill className={info.style}>{info.label}</Pill>
        {d.change === "changed" && (
          <span className="text-xs text-slate-500">
            ({d.fields.map((f) => FIELD_LABELS[f] ?? f).join(", ")})
          </span>
        )}
      </div>
      {d.change !== "same" && (
        <div className="grid gap-2 text-xs sm:grid-cols-2">
          <div className="rounded bg-rose-50 p-2">
            <p className="mb-1 font-medium text-rose-800">Trước</p>
            <p className="whitespace-pre-wrap text-slate-700">{String(before?.narration ?? "—")}</p>
            {d.fields.includes("visual") && before && (
              <p className="mt-1 text-slate-500">🖼 {visualText(before.visual)}</p>
            )}
            {d.fields.includes("duration") && before && (
              <p className="mt-1 text-slate-500">⏱ {String(before.duration)}</p>
            )}
          </div>
          <div className="rounded bg-emerald-50 p-2">
            <p className="mb-1 font-medium text-emerald-800">Sau</p>
            <p className="whitespace-pre-wrap text-slate-700">{String(after?.narration ?? "—")}</p>
            {d.fields.includes("visual") && after && (
              <p className="mt-1 text-slate-500">🖼 {visualText(after.visual)}</p>
            )}
            {d.fields.includes("duration") && after && (
              <p className="mt-1 text-slate-500">⏱ {String(after.duration)}</p>
            )}
          </div>
        </div>
      )}
    </li>
  );
}

export function CompareDialog({
  videoId,
  renders,
  initialA,
  initialB = "current",
  onClose,
}: {
  videoId: string;
  renders: RenderVersion[];
  initialA: string;
  initialB?: string;
  onClose: () => void;
}) {
  const api = useApi();
  const [a, setA] = useState(initialA);
  const [b, setB] = useState(initialB);
  const [diff, setDiff] = useState<RenderDiff | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [showSame, setShowSame] = useState(false);
  const options = renders.filter((r) => r.status === "done");

  useEffect(() => {
    setError(null);
    api
      .compareRenders(videoId, a, b)
      .then(setDiff)
      .catch((e: Error) => {
        setDiff(null);
        setError(e.message);
      });
  }, [api, videoId, a, b]);

  const select = (value: string, onChange: (v: string) => void, label: string, current: boolean) => (
    <select
      className="input"
      aria-label={label}
      value={value}
      onChange={(e) => onChange(e.target.value)}
    >
      {current && <option value="current">Nội dung hiện tại (chưa dựng)</option>}
      {options.map((r) => (
        <option key={r.id} value={r.id}>
          {versionLabel(r)}
        </option>
      ))}
    </select>
  );

  const scenes = diff?.scenes ?? [];
  return (
    <Modal title="So sánh phiên bản" onClose={onClose} wide>
      <div className="space-y-4">
        <div className="grid items-center gap-2 sm:grid-cols-[1fr_auto_1fr]">
          {select(a, setA, "Phiên bản A", false)}
          <span className="text-center text-slate-400">→</span>
          {select(b, setB, "Phiên bản B", true)}
        </div>
        <ErrorBox>{error}</ErrorBox>
        {diff && (
          <>
            <div className="flex flex-wrap gap-2 text-xs">
              {(["changed", "added", "removed", "same"] as const).map((k) => (
                <Pill key={k} className={CHANGE_STYLE[k].style}>
                  {CHANGE_STYLE[k].label}: {diff.summary[k]}
                </Pill>
              ))}
              {diff.reordered && <Pill className="bg-sky-100 text-sky-800">Đổi thứ tự cảnh</Pill>}
            </div>
            {diff.title_changed && (
              <p className="text-sm">
                Tiêu đề: <s className="text-rose-700">{diff.title.before}</s> →{" "}
                <span className="text-emerald-700">{diff.title.after}</span>
              </p>
            )}
            <label className="flex items-center gap-2 text-xs text-slate-600">
              <input
                type="checkbox"
                className="accent-indigo-600"
                checked={showSame}
                onChange={(e) => setShowSame(e.target.checked)}
              />
              Hiện cả các cảnh không đổi
            </label>
            <ol className="space-y-2">
              {scenes.map((d, i) =>
                showSame || d.change !== "same" ? <SceneRow key={d.scene_id} d={d} index={i} /> : null,
              )}
            </ol>
            {scenes.every((d) => d.change === "same") && !diff.title_changed && (
              <p className="text-sm text-slate-500">Hai phiên bản có nội dung giống nhau.</p>
            )}
          </>
        )}
      </div>
    </Modal>
  );
}
