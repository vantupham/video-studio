import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useParams } from "react-router";
import { JobProgress } from "../components/JobProgress";
import { ProviderChoicePicker } from "../components/ProviderChoicePicker";
import { RendersList } from "../components/RendersList";
import { SceneEditor } from "../components/SceneEditor";
import { Button, Card, ErrorBox } from "../components/ui";
import { useApi } from "../lib/backendContext";
import { newSceneId } from "../lib/storyboard";
import type {
  GenerationSettings,
  ProviderInstance,
  RenderRecord,
  Scene,
  Storyboard,
  StoryboardRecord,
} from "../lib/types";

type ActiveJob = {
  id: string;
  kind: "regen" | "tts" | "render";
  sceneId?: string;
};

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
        <div className="flex items-center justify-between">
          <Link to={`/projects/${projectId}`} className="text-sm text-indigo-700 hover:underline">
            ← Về dự án
          </Link>
          <Link
            to={`/videos/${storyboardId}`}
            className="text-sm font-medium text-indigo-700 hover:underline"
          >
            Duyệt &amp; đăng →
          </Link>
        </div>
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
