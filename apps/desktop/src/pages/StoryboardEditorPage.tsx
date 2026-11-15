import { VISUAL_TYPES } from "@ir/storyboard";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useParams } from "react-router";
import { JobProgress } from "../components/JobProgress";
import { ProviderChoicePicker } from "../components/ProviderChoicePicker";
import { RendersList } from "../components/RendersList";
import { Button, Card, ErrorBox } from "../components/ui";
import { useApi } from "../lib/backendContext";
import type {
  GenerationSettings,
  ProviderInstance,
  RenderRecord,
  Scene,
  Storyboard,
  StoryboardRecord,
  VisualType,
} from "../lib/types";

const VISUAL_LABELS: Record<VisualType, string> = {
  title_card: "Thẻ tiêu đề",
  text_card: "Thẻ chữ (gạch đầu dòng)",
  image: "Hình ảnh",
  stock: "Ảnh kho (stock)",
  solid: "Nền màu",
};

type ActiveJob = {
  id: string;
  kind: "regen" | "tts" | "render";
  sceneId?: string;
};

function newSceneId(existing: Scene[]): string {
  const ids = new Set(existing.map((s) => s.id));
  let n = existing.length + 1;
  while (ids.has(`s${n}`)) n += 1;
  return `s${n}`;
}

function SceneEditor({
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

export function StoryboardEditorPage() {
  const { projectId = "", storyboardId = "" } = useParams();
  const api = useApi();
  const [record, setRecord] = useState<StoryboardRecord | null>(null);
  const [draft, setDraft] = useState<Storyboard | null>(null);
  const [settings, setSettings] = useState<GenerationSettings | null>(null);
  const [instances, setInstances] = useState<ProviderInstance[]>([]);
  const [renders, setRenders] = useState<RenderRecord[]>([]);
  const [job, setJob] = useState<ActiveJob | null>(null);
  const [running, setRunning] = useState(false);
  const progressRef = useRef<HTMLDivElement>(null);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);

  const load = useCallback(
    () =>
      api.getStoryboard(storyboardId).then((rec) => {
        setRecord(rec);
        setDraft(rec.data);
        setSettings(rec.settings);
        return rec;
      }),
    [api, storyboardId],
  );

  const loadRenders = useCallback(
    () =>
      api
        .listRenders(projectId)
        .then((list) => setRenders(list.filter((r) => r.storyboard_id === storyboardId)))
        .catch(() => undefined),
    [api, projectId, storyboardId],
  );

  useEffect(() => {
    load().catch((e: Error) => setError(e.message));
    loadRenders();
    api
      .listProviders()
      .then((r) => setInstances(r.instances))
      .catch(() => undefined);
  }, [api, load, loadRenders]);

  useEffect(() => {
    if (job) progressRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
  }, [job]);

  const dirty = useMemo(
    () =>
      record !== null &&
      draft !== null &&
      (JSON.stringify(record.data) !== JSON.stringify(draft) ||
        JSON.stringify(record.settings) !== JSON.stringify(settings)),
    [record, draft, settings],
  );

  async function save(): Promise<boolean> {
    if (!draft) return false;
    setSaving(true);
    setError(null);
    try {
      const rec = await api.saveStoryboard(storyboardId, draft, settings ?? undefined);
      setRecord(rec);
      setDraft(rec.data);
      setSettings(rec.settings);
      setNotice("Đã lưu");
      return true;
    } catch (e) {
      setError((e as Error).message);
      return false;
    } finally {
      setSaving(false);
    }
  }

  async function start(
    kind: ActiveJob["kind"],
    fn: () => Promise<{ id: string }>,
    sceneId?: string,
  ) {
    if (dirty && !(await save())) return;
    setError(null);
    setNotice(null);
    try {
      const j = await fn();
      setJob({ id: j.id, kind, sceneId });
      setRunning(true);
    } catch (e) {
      setError((e as Error).message);
    }
  }

  const onJobFinished = useCallback(async () => {
    setRunning(false);
    const finished = await api.getJob(job?.id ?? "").catch(() => null);
    await load().catch(() => undefined);
    await loadRenders();
    if (finished?.status === "succeeded" && job?.kind === "regen") setNotice("Đã tạo lại cảnh");
  }, [api, job, load, loadRenders]);

  if (!record || !draft || !settings) {
    return error ? (
      <ErrorBox>{error}</ErrorBox>
    ) : (
      <p className="text-sm text-slate-500">Đang tải…</p>
    );
  }

  const setScene = (i: number, s: Scene) =>
    setDraft({
      ...draft,
      scenes: draft.scenes.map((x, j) => (j === i ? s : x)),
    });
  const move = (i: number, delta: number) => {
    const scenes = [...draft.scenes];
    const [s] = scenes.splice(i, 1);
    scenes.splice(i + delta, 0, s);
    setDraft({ ...draft, scenes });
  };
  const addScene = () =>
    setDraft({
      ...draft,
      scenes: [
        ...draft.scenes,
        {
          id: newSceneId(draft.scenes),
          narration: "",
          visual: { type: "text_card", title: null, bullets: [] },
          overlays: [],
          duration: "auto",
          source_block_ids: [],
        },
      ],
    });

  async function uploadImage(i: number, file: File) {
    try {
      const asset = await api.uploadAsset(file, "image", projectId);
      const scene = draft!.scenes[i];
      setScene(i, {
        ...scene,
        visual: { ...scene.visual, type: "image", image: asset.rel_path },
      });
      setRecord((r) => (r ? { ...r, image_urls: { ...r.image_urls, [scene.id]: asset.url } } : r));
    } catch (e) {
      setError((e as Error).message);
    }
  }

  const totalWords = draft.scenes.reduce(
    (n, s) => n + (s.narration.trim() ? s.narration.trim().split(/\s+/).length : 0),
    0,
  );

  return (
    <div className="space-y-6">
      <div>
        <Link to={`/projects/${projectId}`} className="text-sm text-indigo-700 hover:underline">
          ← Về dự án
        </Link>
        <input
          aria-label="Tiêu đề video"
          className="mt-1 w-full rounded-lg border border-transparent bg-transparent px-1 text-2xl font-bold hover:border-slate-200 focus:border-indigo-400 focus:outline-none"
          value={draft.title}
          onChange={(e) => setDraft({ ...draft, title: e.target.value })}
        />
        <p className="px-1 text-sm text-slate-600">
          {draft.style} · {draft.aspect} · {draft.scenes.length} cảnh · {totalWords} từ (~
          {Math.round(totalWords / 2.6)} giây)
        </p>
      </div>

      <div className="sticky top-0 z-10 -mx-2 flex flex-wrap items-center gap-2 rounded-xl border border-slate-200 bg-white/95 p-3 shadow-sm backdrop-blur">
        <Button onClick={save} disabled={!dirty || saving} data-testid="save-storyboard">
          {saving ? "Đang lưu…" : dirty ? "Lưu thay đổi" : "Đã lưu"}
        </Button>
        <Button
          variant="secondary"
          disabled={running}
          onClick={() => start("tts", () => api.synthesize(storyboardId))}
        >
          Tạo giọng đọc
        </Button>
        <Button
          variant="secondary"
          disabled={running}
          onClick={() => start("render", async () => (await api.render(storyboardId, true)).job)}
        >
          Xem nhanh (độ phân giải thấp)
        </Button>
        <Button
          disabled={running}
          onClick={() => start("render", async () => (await api.render(storyboardId, false)).job)}
          data-testid="render-button"
        >
          Dựng video
        </Button>
        {notice && !dirty && <span className="text-sm text-emerald-700">✓ {notice}</span>}
      </div>

      <ErrorBox>{error}</ErrorBox>

      {job && (
        <div ref={progressRef} className="scroll-mt-24">
          <Card
            title="Tiến trình"
            actions={
              <Button variant="ghost" className="text-xs" onClick={() => setJob(null)}>
                Ẩn
              </Button>
            }
          >
            <JobProgress key={job.id} jobId={job.id} onFinished={onJobFinished} />
          </Card>
        </div>
      )}

      <Card title="Cài đặt dựng">
        <div className="space-y-3">
          <ProviderChoicePicker
            kind="tts"
            instances={instances}
            value={settings.tts}
            onChange={(tts) => setSettings({ ...settings, tts })}
          />
          <div className="flex flex-wrap gap-4 text-sm">
            <label className="flex items-center gap-2">
              <input
                type="checkbox"
                className="size-4 accent-indigo-600"
                checked={settings.captions}
                onChange={(e) => setSettings({ ...settings, captions: e.target.checked })}
              />
              Phụ đề
            </label>
            <label className="flex items-center gap-2">
              <input
                type="checkbox"
                className="size-4 accent-indigo-600"
                checked={settings.burn_subtitles}
                onChange={(e) => setSettings({ ...settings, burn_subtitles: e.target.checked })}
              />
              Gắn phụ đề vào hình
            </label>
          </div>
          <p className="text-xs text-slate-500">
            Đổi giọng đọc sẽ tạo lại âm thanh cho mọi cảnh; các cảnh không đổi được lấy từ bộ nhớ
            đệm.
          </p>
        </div>
      </Card>

      <ol className="space-y-4">
        {draft.scenes.map((scene, i) => (
          <SceneEditor
            key={scene.id}
            scene={scene}
            index={i}
            total={draft.scenes.length}
            record={record}
            imageUrl={
              record.image_urls[scene.id] ? api.mediaUrl(record.image_urls[scene.id]) : null
            }
            busy={running}
            onChange={(s) => setScene(i, s)}
            onMove={(d) => move(i, d)}
            onDelete={() =>
              setDraft({
                ...draft,
                scenes: draft.scenes.filter((_, j) => j !== i),
              })
            }
            onRegenerate={(instruction) =>
              start(
                "regen",
                () => api.regenerateScene(storyboardId, scene.id, instruction),
                scene.id,
              )
            }
            onUploadImage={(f) => uploadImage(i, f)}
          />
        ))}
      </ol>
      <Button variant="secondary" onClick={addScene}>
        + Thêm cảnh
      </Button>

      <Card title={`Video đã dựng (${renders.length})`}>
        <RendersList renders={renders} onDeleted={loadRenders} />
      </Card>
    </div>
  );
}
