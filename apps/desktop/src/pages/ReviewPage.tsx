import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router";
import { VideoStatusBadge } from "../components/badges";
import { CompareDialog } from "../components/CompareDialog";
import { JobProgress } from "../components/JobProgress";
import { PublishPanel } from "../components/PublishPanel";
import { SceneEditor } from "../components/SceneEditor";
import { Button, Card, ErrorBox, Modal, Pill } from "../components/ui";
import { VideoPlayer } from "../components/VideoPlayer";
import { useApi, useAppEvents } from "../lib/backendContext";
import { formatBytes, formatDate } from "../lib/format";
import { EVENT_LABELS, VIDEO_STATUS_LABELS } from "../lib/labels";
import type {
  RenderVersion,
  Scene,
  Storyboard,
  StoryboardRecord,
  VideoAction,
  VideoDetail,
} from "../lib/types";

function versionName(r: RenderVersion) {
  if (r.kind === "preview") return "Xem nhanh";
  if (r.kind === "variant") return `Bản ${r.aspect}`;
  return r.version ? `Phiên bản ${r.version}` : "Bản dựng";
}

function ActionDialog({
  action,
  onClose,
  onSubmit,
}: {
  action: VideoAction;
  onClose: () => void;
  onSubmit: (note: string) => Promise<void>;
}) {
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const changes = action.id === "request_changes";
  return (
    <Modal
      title={action.label}
      onClose={onClose}
      footer={
        <>
          <Button variant="secondary" onClick={onClose}>
            Huỷ
          </Button>
          <Button
            variant={changes ? "danger" : "primary"}
            disabled={busy || (action.note_required && !note.trim())}
            onClick={async () => {
              setBusy(true);
              try {
                await onSubmit(note.trim());
              } catch (e) {
                setError((e as Error).message);
                setBusy(false);
              }
            }}
            data-testid="action-confirm"
          >
            {action.label}
          </Button>
        </>
      }
    >
      <div className="space-y-2">
        <label htmlFor="action-note" className="text-sm font-medium">
          {changes ? "Cần sửa gì?" : "Ghi chú (tuỳ chọn)"}
        </label>
        <textarea
          id="action-note"
          className="input min-h-28 w-full"
          autoFocus
          placeholder={
            changes ? "Ví dụ: Cảnh 2 đọc quá nhanh, thêm ví dụ về lá cây…" : "Ghi chú cho lịch sử"
          }
          value={note}
          onChange={(e) => setNote(e.target.value)}
        />
        <ErrorBox>{error}</ErrorBox>
      </div>
    </Modal>
  );
}

export function ReviewPage() {
  const { videoId = "" } = useParams();
  const api = useApi();
  const [params, setParams] = useSearchParams();
  const tab = params.get("tab") === "publish" ? "publish" : "review";
  const [video, setVideo] = useState<VideoDetail | null>(null);
  const [record, setRecord] = useState<StoryboardRecord | null>(null);
  const [draft, setDraft] = useState<Storyboard | null>(null);
  const [renderId, setRenderId] = useState<string | null>(null);
  const [sceneId, setSceneId] = useState<string | null>(null);
  const [jobId, setJobId] = useState<string | null>(null);
  const [action, setAction] = useState<VideoAction | null>(null);
  const [compare, setCompare] = useState<{ a: string; b: string } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const editorRef = useRef<HTMLDivElement>(null);

  const load = useCallback(
    () =>
      Promise.all([api.getVideo(videoId), api.getStoryboard(videoId)])
        .then(([v, rec]) => {
          setVideo(v);
          setRecord(rec);
          setDraft((d) => d ?? rec.data);
          setError(null);
        })
        .catch((e: Error) => setError(e.message)),
    [api, videoId],
  );

  useEffect(() => {
    void load();
  }, [load]);

  useAppEvents((e) => {
    if (e.type === "video" && e.id === videoId) void load();
    if (e.type === "publication" && e.storyboard_id === videoId) void load();
  });

  const playable = useMemo(
    () => (video?.renders ?? []).filter((r) => r.status === "done" && r.urls.video),
    [video],
  );
  const current =
    playable.find((r) => r.id === renderId) ??
    playable.find((r) => r.kind === "main") ??
    playable[0] ??
    null;

  const dirty = useMemo(
    () => record !== null && draft !== null && JSON.stringify(record.data) !== JSON.stringify(draft),
    [record, draft],
  );

  if (!video || !record || !draft) {
    return error ? <ErrorBox>{error}</ErrorBox> : <p className="text-sm text-slate-500">Đang tải…</p>;
  }

  const sceneIndex = sceneId ? draft.scenes.findIndex((s) => s.id === sceneId) : -1;
  const scene = sceneIndex >= 0 ? draft.scenes[sceneIndex] : null;
  const rendering = video.rendering || jobId !== null;
  const approvedRender = video.renders.find((r) => r.id === video.approved_render_id);

  const setScene = (i: number, s: Scene) =>
    setDraft({ ...draft, scenes: draft.scenes.map((x, j) => (j === i ? s : x)) });

  async function save(): Promise<boolean> {
    if (!dirty) return true;
    setSaving(true);
    try {
      const rec = await api.saveStoryboard(videoId, draft!);
      setRecord(rec);
      setDraft(rec.data);
      await load();
      return true;
    } catch (e) {
      setError((e as Error).message);
      return false;
    } finally {
      setSaving(false);
    }
  }

  async function rerender(aspect?: string) {
    setError(null);
    setNotice(null);
    if (!(await save())) return;
    try {
      const res = await api.render(videoId, false, aspect);
      setJobId(res.job.id);
      setRenderId(null);
    } catch (e) {
      setError((e as Error).message);
    }
  }

  async function runAction(a: VideoAction, note: string) {
    const v = await api.videoAction(videoId, a.id, note);
    setVideo(v);
    setAction(null);
    setNotice(
      a.id === "approve"
        ? "Đã duyệt — chuyển sang tab Đăng để lên lịch."
        : a.id === "request_changes"
          ? "Đã ghi yêu cầu sửa."
          : `Đã chuyển sang “${VIDEO_STATUS_LABELS[v.status]}”.`,
    );
  }

  const selectScene = (id: string | null) => {
    setSceneId(id);
    if (id) window.setTimeout(() => editorRef.current?.scrollIntoView({ behavior: "smooth" }), 50);
  };

  const lastChangeRequest = video.events.find((e) => e.action === "request_changes");

  return (
    <div className="space-y-5">
      <div>
        <div className="flex items-center justify-between text-sm">
          <Link to="/videos" className="text-indigo-700 hover:underline">
            ← Thư viện video
          </Link>
          <Link
            to={`/projects/${video.project_id}/storyboards/${video.id}`}
            className="text-indigo-700 hover:underline"
          >
            Mở trình chỉnh sửa đầy đủ →
          </Link>
        </div>
        <div className="mt-1 flex flex-wrap items-center gap-3">
          <h1 className="text-2xl font-bold">{video.title}</h1>
          <VideoStatusBadge status={video.status} label={video.status_label} />
        </div>
        <p className="text-sm text-slate-600">
          {video.project_name} · {video.style} · {video.aspect} · {video.scenes} cảnh
        </p>
      </div>

      <div className="flex gap-1 border-b border-slate-200" role="tablist">
        {(
          [
            ["review", "Duyệt"],
            ["publish", "Đăng"],
          ] as const
        ).map(([id, label]) => (
          <button
            key={id}
            role="tab"
            type="button"
            aria-selected={tab === id}
            onClick={() => setParams(id === "review" ? {} : { tab: id }, { replace: true })}
            className={`-mb-px border-b-2 px-4 py-2 text-sm font-medium ${
              tab === id
                ? "border-indigo-600 text-indigo-700"
                : "border-transparent text-slate-500 hover:text-slate-800"
            }`}
          >
            {label}
          </button>
        ))}
      </div>

      <ErrorBox>{error}</ErrorBox>
      {notice && (
        <p className="rounded-lg bg-emerald-50 px-4 py-2 text-sm text-emerald-800">✓ {notice}</p>
      )}

      {jobId && (
        <Card
          title="Đang dựng lại"
          actions={
            <Button variant="ghost" className="text-xs" onClick={() => setJobId(null)}>
              Ẩn
            </Button>
          }
        >
          <JobProgress
            jobId={jobId}
            onFinished={async () => {
              setJobId(null);
              setDraft(null);
              await load();
            }}
          />
        </Card>
      )}

      {tab === "publish" ? (
        <PublishPanel
          video={video}
          onRerender={(aspect) => {
            setParams({}, { replace: true });
            void rerender(aspect);
          }}
        />
      ) : (
        <>
          {video.outdated && !rendering && (
            <div className="flex flex-wrap items-center gap-3 rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-900">
              <span className="flex-1">
                Nội dung đã thay đổi sau lần dựng gần nhất. Hãy dựng lại để người duyệt xem đúng
                phiên bản mới (chỉ các cảnh thay đổi được dựng lại).
              </span>
              <Button onClick={() => rerender()} disabled={video.render_locked}>
                Dựng lại
              </Button>
            </div>
          )}
          {lastChangeRequest && video.status === "changes_requested" && (
            <div className="rounded-lg border border-amber-200 bg-white px-4 py-3 text-sm">
              <p className="font-medium text-amber-800">Yêu cầu sửa ({formatDate(lastChangeRequest.ts)}):</p>
              <p className="whitespace-pre-wrap text-slate-700">{lastChangeRequest.note}</p>
            </div>
          )}

          <div className="grid gap-5 lg:grid-cols-[1fr_300px]">
            <Card
              title={current ? versionName(current) : "Chưa có bản dựng"}
              actions={
                current && (
                  <span className="text-xs text-slate-500">
                    {current.duration_s?.toFixed(1)}s ·{" "}
                    {current.size_bytes ? formatBytes(current.size_bytes) : ""}
                  </span>
                )
              }
            >
              {current ? (
                <VideoPlayer
                  src={api.mediaUrl(current.urls.video)}
                  poster={current.urls.thumbnail ? api.mediaUrl(current.urls.thumbnail) : undefined}
                  timeline={current.timeline}
                  aspect={current.aspect}
                  selectedScene={sceneId}
                  onSelectScene={selectScene}
                  sceneLabel={(id) =>
                    (draft.scenes.find((s) => s.id === id)?.narration ?? "").slice(0, 60)
                  }
                />
              ) : (
                <div className="space-y-3 text-sm text-slate-600">
                  <p>{rendering ? "Đang dựng video…" : "Video chưa được dựng."}</p>
                  {!rendering && <Button onClick={() => rerender()}>Dựng video</Button>}
                </div>
              )}
            </Card>

            <div className="space-y-4">
              <Card title="Duyệt">
                <div className="space-y-2">
                  {video.actions.length === 0 && (
                    <p className="text-sm text-slate-500">
                      {video.status === "generating"
                        ? "Đang dựng — chờ xong để duyệt."
                        : video.status === "draft"
                          ? "Dựng video trước khi gửi duyệt."
                          : "Không có thao tác duyệt ở trạng thái này."}
                    </p>
                  )}
                  {video.actions.map((a) => (
                    <Button
                      key={a.id}
                      className="w-full"
                      variant={
                        a.id === "approve" ? "primary" : a.id === "request_changes" ? "danger" : "secondary"
                      }
                      disabled={a.id === "approve" && (video.outdated || rendering)}
                      onClick={() =>
                        a.note_required || a.id === "approve"
                          ? setAction(a)
                          : runAction(a, "").catch((e: Error) => setError(e.message))
                      }
                      data-testid={`action-${a.id}`}
                    >
                      {a.label}
                    </Button>
                  ))}
                  {video.outdated && video.actions.some((a) => a.id === "approve") && (
                    <p className="text-xs text-amber-700">Dựng lại nội dung mới trước khi duyệt.</p>
                  )}
                  {approvedRender && (
                    <p className="text-xs text-emerald-700">
                      ✓ Đã duyệt: {versionName(approvedRender)}
                    </p>
                  )}
                  {["approved", "scheduled", "published"].includes(video.status) && (
                    <Button
                      variant="secondary"
                      className="w-full"
                      onClick={() => setParams({ tab: "publish" }, { replace: true })}
                    >
                      Đăng / lên lịch →
                    </Button>
                  )}
                </div>
              </Card>

              <Card title={`Phiên bản (${playable.length})`}>
                <ul className="max-h-72 space-y-1.5 overflow-y-auto">
                  {playable.map((r) => (
                    <li
                      key={r.id}
                      className={`rounded-lg border px-2.5 py-2 text-xs ${
                        current?.id === r.id ? "border-indigo-300 bg-indigo-50" : "border-slate-200"
                      }`}
                      data-testid="version"
                    >
                      <div className="flex items-center gap-1.5">
                        <button
                          type="button"
                          className="font-medium text-slate-900 hover:text-indigo-700"
                          onClick={() => setRenderId(r.id)}
                        >
                          {versionName(r)}
                        </button>
                        {r.id === video.approved_render_id && (
                          <Pill className="bg-emerald-100 text-emerald-800">Đã duyệt</Pill>
                        )}
                        {r.current && <Pill className="bg-slate-100 text-slate-600">Mới nhất</Pill>}
                      </div>
                      <p className="text-slate-500">
                        {formatDate(r.created_at)} · {r.aspect} · {r.duration_s?.toFixed(1)}s
                      </p>
                      <div className="mt-1 flex gap-2">
                        <button
                          type="button"
                          className="text-indigo-700 hover:underline"
                          onClick={() => setRenderId(r.id)}
                        >
                          Xem
                        </button>
                        <button
                          type="button"
                          className="text-indigo-700 hover:underline"
                          onClick={() => {
                            const prev = playable.find(
                              (x) => x.kind === "main" && x.version === (r.version ?? 0) - 1,
                            );
                            setCompare({ a: prev?.id ?? r.id, b: prev ? r.id : "current" });
                          }}
                          data-testid="compare-version"
                        >
                          So sánh
                        </button>
                      </div>
                    </li>
                  ))}
                </ul>
              </Card>
            </div>
          </div>

          <div ref={editorRef} className="scroll-mt-4">
            <Card
              title={
                scene ? `Sửa cảnh ${sceneIndex + 1}` : "Sửa cảnh"
              }
              actions={
                <>
                  {dirty && (
                    <Button variant="ghost" className="text-xs" onClick={() => setDraft(record.data)}>
                      Bỏ thay đổi
                    </Button>
                  )}
                  <Button
                    variant="secondary"
                    className="py-1 text-xs"
                    disabled={!dirty || saving}
                    onClick={save}
                  >
                    {saving ? "Đang lưu…" : dirty ? "Lưu" : "Đã lưu"}
                  </Button>
                  <Button
                    className="py-1 text-xs"
                    disabled={rendering || video.render_locked || (!dirty && !video.outdated)}
                    onClick={() => rerender()}
                    data-testid="save-rerender"
                  >
                    Lưu &amp; dựng lại
                  </Button>
                </>
              }
            >
              {video.render_locked && (
                <p className="mb-3 text-sm text-amber-700">
                  Video đang được lên lịch / đăng — huỷ bài đăng trước khi sửa và dựng lại.
                </p>
              )}
              {scene ? (
                <ol>
                  <SceneEditor
                    key={scene.id}
                    scene={scene}
                    index={sceneIndex}
                    total={draft.scenes.length}
                    record={record}
                    imageUrl={
                      record.image_urls[scene.id] ? api.mediaUrl(record.image_urls[scene.id]) : null
                    }
                    busy={rendering}
                    onChange={(s) => setScene(sceneIndex, s)}
                    onMove={(delta) => {
                      const scenes = [...draft.scenes];
                      const [s] = scenes.splice(sceneIndex, 1);
                      scenes.splice(sceneIndex + delta, 0, s);
                      setDraft({ ...draft, scenes });
                    }}
                    onDelete={() => {
                      setDraft({ ...draft, scenes: draft.scenes.filter((_, j) => j !== sceneIndex) });
                      setSceneId(null);
                    }}
                    onRegenerate={async (instruction) => {
                      if (!(await save())) return;
                      try {
                        const job = await api.regenerateScene(videoId, scene.id, instruction);
                        setJobId(job.id);
                      } catch (e) {
                        setError((e as Error).message);
                      }
                    }}
                    onUploadImage={async (file) => {
                      try {
                        const asset = await api.uploadAsset(file, "image", video.project_id);
                        setScene(sceneIndex, {
                          ...scene,
                          visual: { ...scene.visual, type: "image", image: asset.rel_path },
                        });
                        setRecord({
                          ...record,
                          image_urls: { ...record.image_urls, [scene.id]: asset.url },
                        });
                      } catch (e) {
                        setError((e as Error).message);
                      }
                    }}
                  />
                </ol>
              ) : (
                <div className="flex flex-wrap gap-1.5">
                  <p className="mb-1 w-full text-sm text-slate-500">
                    Chọn một cảnh trên dòng thời gian hoặc ở đây để sửa:
                  </p>
                  {draft.scenes.map((s, i) => (
                    <button
                      key={s.id}
                      type="button"
                      className="max-w-56 truncate rounded-lg border border-slate-200 px-2.5 py-1 text-left text-xs hover:border-indigo-300"
                      onClick={() => selectScene(s.id)}
                    >
                      <b>{i + 1}.</b> {s.narration.slice(0, 50)}
                    </button>
                  ))}
                </div>
              )}
            </Card>
          </div>

          <Card title="Lịch sử">
            <ol className="space-y-2" data-testid="history">
              {video.events.map((e) => (
                <li key={e.id} className="flex gap-3 text-sm">
                  <span className="w-36 shrink-0 text-xs text-slate-500">{formatDate(e.ts)}</span>
                  <div className="flex-1">
                    <p>
                      <span className="font-medium">{EVENT_LABELS[e.action] ?? e.action}</span>
                      {e.to_status && e.from_status !== e.to_status && (
                        <span className="text-slate-500">
                          {" "}
                          → {VIDEO_STATUS_LABELS[e.to_status] ?? e.to_status}
                        </span>
                      )}
                      <span className="text-xs text-slate-400">
                        {" "}
                        · {e.actor === "system" ? "hệ thống" : "bạn"}
                      </span>
                    </p>
                    {e.note && <p className="whitespace-pre-wrap text-slate-600">{e.note}</p>}
                  </div>
                </li>
              ))}
            </ol>
          </Card>
        </>
      )}

      {action && (
        <ActionDialog action={action} onClose={() => setAction(null)} onSubmit={(n) => runAction(action, n)} />
      )}
      {compare && (
        <CompareDialog
          videoId={videoId}
          renders={video.renders}
          initialA={compare.a}
          initialB={compare.b}
          onClose={() => setCompare(null)}
        />
      )}
    </div>
  );
}
