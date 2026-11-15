import { VISUAL_TYPES } from "@ir/storyboard";
import { useState } from "react";
import { useApi } from "../lib/backendContext";
import type { Scene, StoryboardRecord, VisualType } from "../lib/types";
import { Button } from "./ui";

const VISUAL_LABELS: Record<VisualType, string> = {
  title_card: "Thẻ tiêu đề",
  text_card: "Thẻ chữ (gạch đầu dòng)",
  image: "Hình ảnh",
  stock: "Ảnh kho (stock)",
  solid: "Nền màu",
};




export function SceneEditor({
  scene,
  index,
  total,
  record,
  imageUrl,
  busy,
  onChange,
  onMove,
  onDelete,
  onRegenerate,
  onUploadImage,
}: {
  scene: Scene;
  index: number;
  total: number;
  record: StoryboardRecord;
  imageUrl: string | null;
  busy: boolean;
  onChange: (s: Scene) => void;
  onMove: (delta: number) => void;
  onDelete: () => void;
  onRegenerate: (instruction: string) => void;
  onUploadImage: (file: File) => void;
}) {
  const api = useApi();
  const [instruction, setInstruction] = useState("");
  const asset = record.assets[scene.id];
  const savedScene = record.data.scenes.find((s) => s.id === scene.id);
  const audioStale = !savedScene || savedScene.narration !== scene.narration;
  const v = scene.visual;
  const setVisual = (patch: Partial<Scene["visual"]>) =>
    onChange({ ...scene, visual: { ...v, ...patch } });
  const words = scene.narration.trim() ? scene.narration.trim().split(/\s+/).length : 0;

  return (
    <li className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm" data-testid="scene">
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <span className="grid size-7 place-items-center rounded-full bg-indigo-600 text-xs font-bold text-white">
          {index + 1}
        </span>
        <span className="font-mono text-xs text-slate-400">{scene.id}</span>
        <span className="text-xs text-slate-500">
          {words} từ · ~{Math.max(1, Math.round(words / 2.6))} giây
        </span>
        <div className="ml-auto flex gap-1">
          <Button
            variant="ghost"
            className="px-2 text-xs"
            disabled={index === 0}
            onClick={() => onMove(-1)}
            aria-label="Lên"
          >
            ↑
          </Button>
          <Button
            variant="ghost"
            className="px-2 text-xs"
            disabled={index === total - 1}
            onClick={() => onMove(1)}
            aria-label="Xuống"
          >
            ↓
          </Button>
          <Button
            variant="ghost"
            className="text-xs text-rose-700"
            disabled={total <= 1}
            onClick={onDelete}
          >
            Xoá cảnh
          </Button>
        </div>
      </div>

      <div className="grid gap-4 lg:grid-cols-[1fr_280px]">
        <div className="space-y-3">
          <div className="flex flex-col gap-1">
            <label htmlFor={`narr-${scene.id}`} className="text-sm font-medium">
              Lời thoại
            </label>
            <textarea
              id={`narr-${scene.id}`}
              className="input min-h-24"
              value={scene.narration}
              onChange={(e) => onChange({ ...scene, narration: e.target.value })}
            />
          </div>
          {asset && !audioStale ? (
            <div className="flex items-center gap-2">
              <audio controls src={api.mediaUrl(asset.audio_url)} className="h-8 flex-1" />
              <span className="text-xs whitespace-nowrap text-slate-500">
                {asset.duration_s.toFixed(1)}s · {asset.voice ?? asset.provider}
              </span>
            </div>
          ) : (
            <p className="text-xs text-slate-400">
              {asset ? "Lời thoại đã đổi — giọng đọc sẽ được tạo lại." : "Chưa có giọng đọc."}
            </p>
          )}
          <div className="flex gap-2">
            <input
              aria-label={`Yêu cầu tạo lại cảnh ${index + 1}`}
              className="input flex-1 py-1.5"
              placeholder="Yêu cầu cho AI, ví dụ: viết ngắn gọn hơn, thêm ví dụ…"
              value={instruction}
              onChange={(e) => setInstruction(e.target.value)}
            />
            <Button
              variant="secondary"
              className="py-1.5 text-xs whitespace-nowrap"
              disabled={busy}
              onClick={() => onRegenerate(instruction)}
            >
              ↻ Tạo lại cảnh
            </Button>
          </div>
        </div>

        <div className="space-y-2 rounded-lg bg-slate-50 p-3">
          <div className="flex flex-col gap-1">
            <label htmlFor={`vt-${scene.id}`} className="text-xs font-medium text-slate-600">
              Kiểu hình ảnh
            </label>
            <select
              id={`vt-${scene.id}`}
              className="input py-1.5"
              value={v.type}
              onChange={(e) => setVisual({ type: e.target.value as VisualType })}
            >
              {VISUAL_TYPES.map((t) => (
                <option key={t} value={t}>
                  {VISUAL_LABELS[t]}
                </option>
              ))}
            </select>
          </div>
          <input
            aria-label="Tiêu đề trên màn hình"
            className="input w-full py-1.5"
            placeholder="Tiêu đề trên màn hình"
            value={v.title ?? ""}
            onChange={(e) => setVisual({ title: e.target.value || null })}
          />
          {v.type === "title_card" && (
            <input
              aria-label="Phụ đề tiêu đề"
              className="input w-full py-1.5"
              placeholder="Dòng phụ"
              value={v.subtitle ?? ""}
              onChange={(e) => setVisual({ subtitle: e.target.value || null })}
            />
          )}
          {(v.type === "text_card" || v.type === "image" || v.type === "stock") && (
            <textarea
              aria-label="Gạch đầu dòng"
              className="input min-h-16 w-full py-1.5 text-xs"
              placeholder="Mỗi dòng một ý (tối đa 8)"
              value={(v.bullets ?? []).join("\n")}
              onChange={(e) =>
                setVisual({
                  bullets: e.target.value
                    .split("\n")
                    .map((b) => b.trimStart())
                    .slice(0, 8),
                })
              }
            />
          )}
          {v.type === "stock" && (
            <input
              aria-label="Từ khoá ảnh"
              className="input w-full py-1.5"
              placeholder="Từ khoá ảnh (tiếng Anh)"
              value={v.query ?? ""}
              onChange={(e) => setVisual({ query: e.target.value || null })}
            />
          )}
          {v.type === "image" && (
            <div className="space-y-1">
              {imageUrl && v.image && (
                <img src={imageUrl} alt="" className="max-h-28 w-full rounded object-contain" />
              )}
              <input
                type="file"
                aria-label="Chọn ảnh"
                accept=".png,.jpg,.jpeg,.webp"
                className="w-full text-xs file:mr-2 file:rounded file:border-0 file:bg-white file:px-2 file:py-1"
                onChange={(e) => e.target.files?.[0] && onUploadImage(e.target.files[0])}
              />
            </div>
          )}
          {v.type === "solid" && (
            <input
              type="color"
              aria-label="Màu nền"
              value={v.color ?? "#1e293b"}
              onChange={(e) => setVisual({ color: e.target.value })}
            />
          )}
          <div className="flex items-center gap-2 text-xs">
            <label className="flex items-center gap-1.5">
              <input
                type="checkbox"
                className="accent-indigo-600"
                checked={scene.duration === "auto"}
                onChange={(e) =>
                  onChange({
                    ...scene,
                    duration: e.target.checked ? "auto" : Math.max(2, asset?.duration_s ?? 5),
                  })
                }
              />
              Thời lượng tự động
            </label>
            {scene.duration !== "auto" && (
              <input
                type="number"
                aria-label="Thời lượng (giây)"
                className="input w-20 py-1"
                min={0.5}
                max={600}
                step={0.5}
                value={scene.duration}
                onChange={(e) => onChange({ ...scene, duration: Number(e.target.value) || 1 })}
              />
            )}
          </div>
        </div>
      </div>
    </li>
  );
}
