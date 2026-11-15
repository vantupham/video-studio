import { useCallback, useEffect, useState, type FormEvent } from "react";
import { useNavigate } from "react-router";
import { useApi } from "../lib/backendContext";
import type {
  AssetRecord,
  GenerationSettings,
  IRDocument,
  Job,
  ProviderInstance,
  StyleInfo,
} from "../lib/types";
import { JobProgress } from "./JobProgress";
import { ProviderChoicePicker } from "./ProviderChoicePicker";
import { Button, Card, ErrorBox } from "./ui";

const DURATIONS = [30, 45, 60, 90, 120, 180, 300];

export function GenerateVideoCard({
  projectId,
  documents,
}: {
  projectId: string;
  documents: Pick<IRDocument, "id" | "title">[];
}) {
  const api = useApi();
  const navigate = useNavigate();
  const [styles, setStyles] = useState<StyleInfo[]>([]);
  const [instances, setInstances] = useState<ProviderInstance[]>([]);
  const [music, setMusic] = useState<AssetRecord[]>([]);
  const [settings, setSettings] = useState<GenerationSettings | null>(null);
  const [chosenDoc, setDocumentId] = useState<string>(documents[0]?.id ?? "");
  const documentId = documents.some((d) => d.id === chosenDoc)
    ? chosenDoc
    : (documents[0]?.id ?? "");
  const [saveDefault, setSaveDefault] = useState(true);
  const [jobId, setJobId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    Promise.all([
      api.styles(),
      api.listProviders(),
      api.projectSettings(projectId),
      api.listAssets({ kind: "audio" }),
    ])
      .then(([s, p, ps, assets]) => {
        setStyles(s);
        setInstances(p.instances);
        setSettings(ps.effective);
        setMusic(assets);
      })
      .catch((e: Error) => setError(e.message));
  }, [api, projectId]);

  const onFinished = useCallback(async () => {
    if (!jobId) return;
    const job: Job = await api.getJob(jobId);
    const sid = job.result?.storyboard_id;
    if (job.status === "succeeded" && typeof sid === "string") {
      navigate(`/projects/${projectId}/storyboards/${sid}`);
    }
  }, [api, jobId, navigate, projectId]);

  if (!settings) {
    return (
      <Card title="Tạo video">
        {error ? <ErrorBox>{error}</ErrorBox> : <p className="text-sm text-slate-500">Đang tải…</p>}
      </Card>
    );
  }

  const set = <K extends keyof GenerationSettings>(key: K, v: GenerationSettings[K]) =>
    setSettings({ ...settings, [key]: v });
  const style = styles.find((s) => s.id === settings.style);

  async function uploadMusic(file: File | null) {
    if (!file) return;
    try {
      const asset = await api.uploadAsset(file, "audio");
      setMusic((m) => [asset, ...m]);
      set("music_asset_id", asset.id);
    } catch (e) {
      setError((e as Error).message);
    }
  }

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (!settings) return;
    setError(null);
    try {
      const job = await api.createStoryboard(projectId, {
        document_id: documentId || null,
        settings,
        save_as_project_default: saveDefault,
      });
      setJobId(job.id);
    } catch (err) {
      setError((err as Error).message);
    }
  }

  return (
    <Card title="Tạo video">
      <form onSubmit={submit} className="space-y-4" data-testid="generate-form">
        <ErrorBox>{error}</ErrorBox>
        <div className="grid gap-4 sm:grid-cols-3">
          <div className="flex flex-col gap-1">
            <label htmlFor="gen-doc" className="text-sm font-medium">
              Tài liệu
            </label>
            <select
              id="gen-doc"
              className="input"
              value={documentId}
              onChange={(e) => setDocumentId(e.target.value)}
            >
              {documents.map((d) => (
                <option key={d.id} value={d.id}>
                  {d.title}
                </option>
              ))}
            </select>
          </div>
          <div className="flex flex-col gap-1">
            <label htmlFor="gen-style" className="text-sm font-medium">
              Kiểu video
            </label>
            <select
              id="gen-style"
              className="input"
              value={settings.style}
              onChange={(e) =>
                setSettings({
                  ...settings,
                  style: e.target.value,
                  aspect: null,
                })
              }
            >
              {styles.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.name}
                </option>
              ))}
            </select>
            {style && <p className="text-xs text-slate-500">{style.description}</p>}
          </div>
          <div className="flex flex-col gap-1">
            <label htmlFor="gen-aspect" className="text-sm font-medium">
              Tỉ lệ khung hình
            </label>
            <select
              id="gen-aspect"
              className="input"
              value={settings.aspect ?? ""}
              onChange={(e) =>
                set("aspect", (e.target.value || null) as GenerationSettings["aspect"])
              }
            >
              <option value="">Theo kiểu ({style?.aspect ?? "?"})</option>
              <option value="16:9">16:9 (ngang)</option>
              <option value="9:16">9:16 (dọc, short)</option>
              <option value="1:1">1:1 (vuông)</option>
            </select>
          </div>
          <div className="flex flex-col gap-1">
            <label htmlFor="gen-duration" className="text-sm font-medium">
              Thời lượng mong muốn
            </label>
            <select
              id="gen-duration"
              className="input"
              value={settings.target_duration_s}
              onChange={(e) => set("target_duration_s", Number(e.target.value))}
            >
              {[...new Set([...DURATIONS, settings.target_duration_s])]
                .sort((a, b) => a - b)
                .map((d) => (
                  <option key={d} value={d}>
                    {d < 60
                      ? `${d} giây`
                      : `${Math.floor(d / 60)} phút${d % 60 ? ` ${d % 60} giây` : ""}`}
                  </option>
                ))}
            </select>
          </div>
          <div className="flex flex-col gap-1">
            <label htmlFor="gen-language" className="text-sm font-medium">
              Ngôn ngữ
            </label>
            <select
              id="gen-language"
              className="input"
              value={settings.language}
              onChange={(e) => set("language", e.target.value)}
            >
              <option value="vi">Tiếng Việt</option>
              <option value="en">English</option>
            </select>
          </div>
          <div className="flex flex-col gap-1">
            <label htmlFor="gen-tone" className="text-sm font-medium">
              Giọng văn
            </label>
            <input
              id="gen-tone"
              className="input"
              value={settings.tone}
              onChange={(e) => set("tone", e.target.value)}
            />
          </div>
        </div>

        <label className="flex items-start gap-3">
          <input
            type="checkbox"
            className="mt-1 size-4 accent-indigo-600"
            checked={settings.use_as_is}
            onChange={(e) => set("use_as_is", e.target.checked)}
          />
          <span>
            <span className="text-sm font-medium">Dùng nguyên văn tài liệu làm lời thoại</span>
            <span className="block text-xs text-slate-500">
              Không gọi LLM: mỗi phần của tài liệu thành một cảnh.
            </span>
          </span>
        </label>

        <fieldset className="space-y-3 rounded-lg border border-slate-200 p-4">
          <legend className="px-1 text-sm font-semibold text-slate-700">Nhà cung cấp</legend>
          {!settings.use_as_is && (
            <ProviderChoicePicker
              kind="llm"
              instances={instances}
              value={settings.llm}
              onChange={(v) => set("llm", v)}
            />
          )}
          <ProviderChoicePicker
            kind="tts"
            instances={instances}
            value={settings.tts}
            onChange={(v) => set("tts", v)}
          />
        </fieldset>

        <fieldset className="grid gap-4 rounded-lg border border-slate-200 p-4 sm:grid-cols-3">
          <legend className="px-1 text-sm font-semibold text-slate-700">Âm thanh & phụ đề</legend>
          <div className="flex flex-col gap-1">
            <label htmlFor="gen-music" className="text-sm font-medium">
              Nhạc nền
            </label>
            <select
              id="gen-music"
              className="input"
              value={settings.music_asset_id ?? ""}
              onChange={(e) => set("music_asset_id", e.target.value || null)}
            >
              <option value="">Không có</option>
              {music.map((m) => (
                <option key={m.id} value={m.id}>
                  {m.filename}
                </option>
              ))}
            </select>
            <input
              type="file"
              aria-label="Tải nhạc nền"
              accept=".mp3,.wav,.m4a,.ogg,.flac,.aac"
              className="text-xs file:mr-2 file:rounded file:border-0 file:bg-slate-100 file:px-2 file:py-1"
              onChange={(e) => uploadMusic(e.target.files?.[0] ?? null)}
            />
          </div>
          <div className="flex flex-col gap-1">
            <label htmlFor="gen-music-vol" className="text-sm font-medium">
              Âm lượng nhạc ({Math.round(settings.music_volume * 100)}%)
            </label>
            <input
              id="gen-music-vol"
              type="range"
              min={0}
              max={1}
              step={0.02}
              value={settings.music_volume}
              onChange={(e) => set("music_volume", Number(e.target.value))}
            />
            <p className="text-xs text-slate-500">Nhạc tự động nhỏ lại khi có lời thoại.</p>
          </div>
          <div className="flex flex-col gap-2 text-sm">
            <label className="flex items-center gap-2">
              <input
                type="checkbox"
                className="size-4 accent-indigo-600"
                checked={settings.captions}
                onChange={(e) => set("captions", e.target.checked)}
              />
              Tạo phụ đề (SRT/VTT/ASS)
            </label>
            <label className="flex items-center gap-2">
              <input
                type="checkbox"
                className="size-4 accent-indigo-600"
                checked={settings.burn_subtitles}
                disabled={!settings.captions}
                onChange={(e) => set("burn_subtitles", e.target.checked)}
              />
              Gắn phụ đề vào hình
            </label>
          </div>
        </fieldset>

        <div className="flex flex-wrap items-center justify-between gap-3">
          <label className="flex items-center gap-2 text-sm text-slate-600">
            <input
              type="checkbox"
              className="size-4 accent-indigo-600"
              checked={saveDefault}
              onChange={(e) => setSaveDefault(e.target.checked)}
            />
            Lưu làm cài đặt mặc định của dự án
          </label>
          <Button type="submit" disabled={!documentId}>
            Tạo kịch bản
          </Button>
        </div>
        {jobId && <JobProgress key={jobId} jobId={jobId} onFinished={onFinished} />}
      </form>
    </Card>
  );
}
