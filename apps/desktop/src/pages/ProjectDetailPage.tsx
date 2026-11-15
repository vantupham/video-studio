import { useCallback, useEffect, useMemo, useRef, useState, type FormEvent } from "react";
import { Link, useParams } from "react-router";
import { GenerateVideoCard } from "../components/GenerateVideoCard";
import { IrViewer } from "../components/IrViewer";
import { JobProgress } from "../components/JobProgress";
import { RendersList } from "../components/RendersList";
import { SchemaForm } from "../components/SchemaForm";
import { Button, Card, ErrorBox, StatusBadge } from "../components/ui";
import { ApiError } from "../lib/api";
import { useApi } from "../lib/backendContext";
import { formatBytes, formatDate } from "../lib/format";
import { JOB_TYPE_LABELS } from "../lib/labels";
import { defaultsFromSchema, type FormValues } from "../lib/schemaForm";
import type {
  Adapter,
  IRDocument,
  Job,
  ProjectDetail,
  RenderRecord,
  StoryboardSummary,
} from "../lib/types";

type InputMode = "text" | "file" | "folder";

const DOC_EXTS = [".md", ".markdown", ".txt", ".docx", ".pdf"];
const FILE_ACCEPT =
  ".md,.markdown,.txt,.docx,.pdf,.zip,.png,.jpg,.jpeg,.webp,.gif,.svg,text/markdown,text/plain";

function extOf(name: string): string {
  const dot = name.lastIndexOf(".");
  return dot >= 0 ? name.slice(dot).toLowerCase() : "";
}

function relPath(f: File): string {
  return (f as File & { webkitRelativePath?: string }).webkitRelativePath || f.name;
}

/** Main document of a multi-file pick: README/index first, then the first document. */
function guessMain(files: File[]): string {
  const docs = files.map(relPath).filter((p) => DOC_EXTS.includes(extOf(p)));
  const preferred = docs.find((p) => /(^|\/)(readme|index)\.(md|markdown)$/i.test(p));
  return preferred ?? [...docs].sort((a, b) => a.split("/").length - b.split("/").length)[0] ?? "";
}

const SAMPLE_SCRIPT = `Cảnh 1: Giới thiệu
Xin chào các bạn! Hôm nay mình sẽ hướng dẫn cách biến một tài liệu thành video có thuyết minh tiếng Việt.

Cảnh 2: Các bước thực hiện
Đầu tiên, dán kịch bản hoặc chọn file Markdown. Sau đó bấm “Chạy phân tích” để tạo Content IR.

Cảnh 3: Kết thúc
Cảm ơn các bạn đã theo dõi. Hẹn gặp lại!`;

function adapterForName(adapters: Adapter[], name: string): Adapter | undefined {
  const ext = extOf(name);
  if (ext === ".zip") return adapters.find((a) => a.id === "markdown");
  return adapters.find((a) => a.file_extensions.includes(ext));
}

export function ProjectDetailPage() {
  const { projectId = "" } = useParams();
  const api = useApi();
  const [project, setProject] = useState<ProjectDetail | null>(null);
  const [adapters, setAdapters] = useState<Adapter[]>([]);
  const [error, setError] = useState<string | null>(null);

  const [mode, setMode] = useState<InputMode>("text");
  const [text, setText] = useState("");
  const [files, setFiles] = useState<File[]>([]);
  const [mainPath, setMainPath] = useState("");
  const folderRef = useRef<HTMLInputElement>(null);
  const [sourceName, setSourceName] = useState("");
  const [adapterId, setAdapterId] = useState("script");
  const [config, setConfig] = useState<FormValues>({});
  const [adding, setAdding] = useState(false);

  const [jobId, setJobId] = useState<string | null>(null);
  const [history, setHistory] = useState<Job[]>([]);
  const [docId, setDocId] = useState<string | null>(null);
  const [doc, setDoc] = useState<IRDocument | null>(null);
  const [preview, setPreview] = useState<{
    id: string;
    content: string;
  } | null>(null);
  const [storyboards, setStoryboards] = useState<StoryboardSummary[]>([]);
  const [renders, setRenders] = useState<RenderRecord[]>([]);

  const adapter = useMemo(() => adapters.find((a) => a.id === adapterId), [adapters, adapterId]);

  const reload = useCallback(
    () =>
      Promise.all([
        api.getProject(projectId),
        api.listJobs({ projectId, limit: 8 }),
        api.listStoryboards(projectId),
        api.listRenders(projectId),
      ])
        .then(([p, jobs, sbs, rs]) => {
          setProject(p);
          setHistory(jobs);
          setStoryboards(sbs);
          setRenders(rs);
          return p;
        })
        .catch((e: Error) => {
          setError(e.message);
          return null;
        }),
    [api, projectId],
  );

  useEffect(() => {
    reload().then((p) => {
      if (p?.documents[0]) setDocId(p.documents[0].id);
    });
    api.listJobs({ projectId, limit: 1 }).then((jobs) => {
      const active = jobs.find(
        (j) => j.type === "ingest" && (j.status === "queued" || j.status === "running"),
      );
      if (active) setJobId(active.id);
    });
  }, [api, projectId, reload]);

  const selectAdapter = useCallback((id: string, list: Adapter[]) => {
    setAdapterId(id);
    setConfig(defaultsFromSchema(list.find((a) => a.id === id)?.config_schema));
  }, []);

  useEffect(() => {
    api
      .adapters()
      .then((list) => {
        setAdapters(list);
        selectAdapter("script", list);
      })
      .catch((e: Error) => setError(e.message));
  }, [api, selectAdapter]);

  useEffect(() => {
    if (!docId) return;
    api
      .getDocument(projectId, docId)
      .then(setDoc)
      .catch((e: Error) => setError(e.message));
  }, [api, projectId, docId]);

  useEffect(() => {
    folderRef.current?.setAttribute("webkitdirectory", "");
  }, [mode]);

  function chooseMode(next: InputMode) {
    setMode(next);
    setFiles([]);
    setMainPath("");
    if (next === "text") selectAdapter("script", adapters);
  }

  function onFiles(list: FileList | null) {
    const picked = Array.from(list ?? []);
    setFiles(picked);
    const main = picked.length === 1 ? picked[0].name : guessMain(picked);
    setMainPath(main);
    if (main) selectAdapter(adapterForName(adapters, main)?.id ?? "script", adapters);
    if (!sourceName && picked.length > 0) {
      const top = relPath(picked[0]).split("/");
      setSourceName(mode === "folder" && top.length > 1 ? top[0] : (main || picked[0].name));
    }
  }

  function onMainChange(path: string) {
    setMainPath(path);
    selectAdapter(adapterForName(adapters, path)?.id ?? "script", adapters);
  }

  async function onAddSource(e: FormEvent) {
    e.preventDefault();
    setAdding(true);
    setError(null);
    try {
      const bundle = mode === "folder" || files.length > 1;
      await api.addSource(projectId, {
        text: mode === "text" ? text : undefined,
        file: mode === "file" && !bundle ? files[0] : undefined,
        files: mode !== "text" && bundle
          ? files.map((f) => ({ file: f, path: relPath(f) }))
          : undefined,
        main: bundle && mainPath ? mainPath : undefined,
        name: sourceName.trim() || undefined,
        adapterId,
        config,
      });
      setText("");
      setFiles([]);
      setMainPath("");
      setSourceName("");
      await reload();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setAdding(false);
    }
  }

  async function runIngest(sourceIds?: string[]) {
    setError(null);
    try {
      const job = await api.ingest(projectId, sourceIds);
      setJobId(job.id);
      setHistory((h) => [job, ...h]);
    } catch (e) {
      setError((e as Error).message);
    }
  }

  const onJobFinished = useCallback(async () => {
    const p = await reload();
    if (p?.documents[0]) setDocId(p.documents[0].id);
  }, [reload]);

  async function togglePreview(sourceId: string) {
    if (preview?.id === sourceId) {
      setPreview(null);
      return;
    }
    try {
      const content = await api.sourceContent(projectId, sourceId);
      setPreview({ id: sourceId, content });
    } catch (e) {
      setPreview({
        id: sourceId,
        content:
          e instanceof ApiError && e.status === 415
            ? e.message
            : `Không xem trước được: ${(e as Error).message}`,
      });
    }
  }

  async function removeSource(sourceId: string) {
    if (!window.confirm("Xoá nguồn này?")) return;
    await api.deleteSource(projectId, sourceId).catch((e: Error) => setError(e.message));
    await reload();
  }

  if (!project) {
    return error ? (
      <ErrorBox>{error}</ErrorBox>
    ) : (
      <p className="text-sm text-slate-500">Đang tải…</p>
    );
  }

  const canAdd = mode === "text" ? text.trim().length > 0 : files.length > 0;
  const docChoices = files.map(relPath).filter((p) => DOC_EXTS.includes(extOf(p)));
  const images = files.length - docChoices.length;
  const tabClass = (m: InputMode) =>
    `rounded-md px-3 py-1.5 text-sm font-medium ${mode === m ? "bg-white text-slate-900 shadow-sm" : "text-slate-600 hover:text-slate-900"}`;

  return (
    <div className="space-y-6">
      <div>
        <Link to="/" className="text-sm text-indigo-700 hover:underline">
          ← Tất cả dự án
        </Link>
        <h1 className="mt-1 text-2xl font-bold">{project.name}</h1>
        {project.description && <p className="text-sm text-slate-600">{project.description}</p>}
      </div>

      <ErrorBox>{error}</ErrorBox>

      <Card title="Thêm nguồn nội dung">
        <form onSubmit={onAddSource} className="space-y-4">
          <div className="inline-flex rounded-lg bg-slate-100 p-1">
            <button type="button" className={tabClass("text")} onClick={() => chooseMode("text")}>
              Dán kịch bản
            </button>
            <button type="button" className={tabClass("file")} onClick={() => chooseMode("file")}>
              Chọn file
            </button>
            <button type="button" className={tabClass("folder")} onClick={() => chooseMode("folder")}>
              Chọn thư mục
            </button>
          </div>

          {mode === "text" ? (
            <div className="flex flex-col gap-1">
              <div className="flex items-center justify-between">
                <label htmlFor="source-text" className="text-sm font-medium">
                  Nội dung
                </label>
                <button
                  type="button"
                  className="text-xs text-indigo-700 hover:underline"
                  onClick={() => setText(SAMPLE_SCRIPT)}
                >
                  Dùng kịch bản mẫu
                </button>
              </div>
              <textarea
                id="source-text"
                className="input min-h-40 font-[inherit]"
                placeholder="Dán kịch bản tiếng Việt vào đây…"
                value={text}
                onChange={(e) => setText(e.target.value)}
              />
            </div>
          ) : (
            <div className="flex flex-col gap-1">
              <label htmlFor="source-file" className="text-sm font-medium">
                {mode === "folder" ? "Thư mục (Markdown + ảnh)" : "File"}
              </label>
              {mode === "folder" ? (
                <input
                  key="folder"
                  ref={folderRef}
                  id="source-file"
                  type="file"
                  multiple
                  className="text-sm file:mr-3 file:rounded-lg file:border-0 file:bg-indigo-50 file:px-3 file:py-2 file:text-sm file:font-medium file:text-indigo-700 hover:file:bg-indigo-100"
                  onChange={(e) => onFiles(e.target.files)}
                />
              ) : (
                <input
                  key="file"
                  id="source-file"
                  type="file"
                  multiple
                  accept={FILE_ACCEPT}
                  className="text-sm file:mr-3 file:rounded-lg file:border-0 file:bg-indigo-50 file:px-3 file:py-2 file:text-sm file:font-medium file:text-indigo-700 hover:file:bg-indigo-100"
                  onChange={(e) => onFiles(e.target.files)}
                />
              )}
              <p className="text-xs text-slate-500">
                {mode === "folder"
                  ? "Chọn thư mục chứa file .md và ảnh; đường dẫn ảnh tương đối trong Markdown được giữ nguyên."
                  : "Markdown, TXT, Word (.docx), PDF (cả bản scan — OCR tiếng Việt), hoặc .zip gồm Markdown + ảnh. Chọn nhiều file để gửi Markdown kèm ảnh."}
              </p>
              {files.length === 1 && (
                <p className="text-xs text-slate-500">
                  {files[0].name} · {formatBytes(files[0].size)}
                </p>
              )}
              {files.length > 1 && (
                <div className="flex flex-wrap items-center gap-2 text-xs text-slate-600">
                  <span>
                    {files.length} file ({docChoices.length} tài liệu, {images} ảnh/khác) ·{" "}
                    {formatBytes(files.reduce((n, f) => n + f.size, 0))}
                  </span>
                  {docChoices.length > 0 && (
                    <label className="flex items-center gap-1">
                      Tài liệu chính:
                      <select
                        className="input w-auto py-1 text-xs"
                        value={mainPath}
                        onChange={(e) => onMainChange(e.target.value)}
                        aria-label="Tài liệu chính"
                      >
                        {docChoices.map((p) => (
                          <option key={p} value={p}>
                            {p}
                          </option>
                        ))}
                      </select>
                    </label>
                  )}
                </div>
              )}
            </div>
          )}

          <div className="grid gap-4 sm:grid-cols-2">
            <div className="flex flex-col gap-1">
              <label htmlFor="source-name" className="text-sm font-medium">
                Tên nguồn (không bắt buộc)
              </label>
              <input
                id="source-name"
                className="input"
                value={sourceName}
                onChange={(e) => setSourceName(e.target.value)}
                placeholder="Ví dụ: Kịch bản tập 1"
              />
            </div>
            <div className="flex flex-col gap-1">
              <label htmlFor="source-adapter" className="text-sm font-medium">
                Bộ đọc nội dung (adapter)
              </label>
              <select
                id="source-adapter"
                className="input"
                value={adapterId}
                onChange={(e) => selectAdapter(e.target.value, adapters)}
              >
                {adapters.map((a) => (
                  <option key={a.id} value={a.id}>
                    {a.name}
                  </option>
                ))}
              </select>
              {adapter && <p className="text-xs text-slate-500">{adapter.description}</p>}
            </div>
          </div>

          {adapter && (
            <fieldset className="rounded-lg border border-slate-200 p-4">
              <legend className="px-1 text-sm font-semibold text-slate-700">
                Tuỳ chọn của adapter “{adapter.name}”
              </legend>
              <SchemaForm
                schema={adapter.config_schema}
                value={config}
                onChange={setConfig}
                emptyText="Adapter này không có tuỳ chọn."
              />
            </fieldset>
          )}

          <div className="flex justify-end">
            <Button type="submit" disabled={!canAdd || adding}>
              {adding ? "Đang thêm…" : "Thêm nguồn"}
            </Button>
          </div>
        </form>
      </Card>

      <Card
        title={`Nguồn nội dung (${project.sources.length})`}
        actions={
          <Button onClick={() => runIngest()} disabled={project.sources.length === 0}>
            Chạy phân tích (ingest)
          </Button>
        }
      >
        {project.sources.length === 0 ? (
          <p className="text-sm text-slate-500">Chưa có nguồn nào.</p>
        ) : (
          <ul className="divide-y divide-slate-100">
            {project.sources.map((s) => (
              <li key={s.id} className="py-3">
                <div className="flex flex-wrap items-center gap-3">
                  <span className="rounded bg-slate-100 px-2 py-0.5 text-xs font-medium text-slate-700">
                    {s.kind === "file" ? "File" : "Văn bản"}
                  </span>
                  <span className="font-medium">{s.name}</span>
                  <span className="text-xs text-slate-500">
                    {s.adapter_id} · {formatBytes(s.size_bytes)}
                    {s.attachments ? ` · ${s.attachments} tệp đính kèm` : ""} ·{" "}
                    {formatDate(s.created_at)}
                  </span>
                  <div className="ml-auto flex gap-1">
                    <Button variant="ghost" className="text-xs" onClick={() => togglePreview(s.id)}>
                      {preview?.id === s.id ? "Ẩn" : "Xem"}
                    </Button>
                    <Button variant="ghost" className="text-xs" onClick={() => runIngest([s.id])}>
                      Phân tích riêng
                    </Button>
                    <Button variant="ghost" className="text-xs" onClick={() => removeSource(s.id)}>
                      Xoá
                    </Button>
                  </div>
                </div>
                {preview?.id === s.id && (
                  <pre className="mt-2 max-h-60 overflow-auto rounded-lg bg-slate-50 p-3 text-xs whitespace-pre-wrap text-slate-700">
                    {preview.content}
                  </pre>
                )}
              </li>
            ))}
          </ul>
        )}
      </Card>

      {jobId && (
        <Card title="Tiến trình">
          <JobProgress key={jobId} jobId={jobId} onFinished={onJobFinished} />
        </Card>
      )}

      <Card title={`Kết quả Content IR (${project.documents.length})`}>
        {project.documents.length === 0 ? (
          <p className="text-sm text-slate-500">
            Chưa có kết quả. Thêm nguồn rồi bấm “Chạy phân tích”.
          </p>
        ) : (
          <div className="space-y-4">
            <div className="flex flex-wrap gap-2">
              {project.documents.map((d) => (
                <button
                  key={d.id}
                  type="button"
                  onClick={() => setDocId(d.id)}
                  className={`rounded-lg border px-3 py-2 text-left text-sm ${
                    d.id === docId
                      ? "border-indigo-400 bg-indigo-50"
                      : "border-slate-200 hover:border-slate-300"
                  }`}
                >
                  <span className="block font-medium">{d.title}</span>
                  <span className="block text-xs text-slate-500">
                    {d.adapter_id} · {d.stats.sections ?? 0} phần · {d.stats.blocks ?? 0} block ·{" "}
                    {formatDate(d.created_at)}
                  </span>
                </button>
              ))}
            </div>
            {doc && doc.id === docId && <IrViewer ir={doc.data} />}
          </div>
        )}
      </Card>

      {project.documents.length > 0 && (
        <GenerateVideoCard projectId={projectId} documents={project.documents} />
      )}

      {storyboards.length > 0 && (
        <Card title={`Storyboard (${storyboards.length})`}>
          <ul className="divide-y divide-slate-100" data-testid="storyboards">
            {storyboards.map((sb) => (
              <li key={sb.id} className="flex flex-wrap items-center gap-3 py-2 text-sm">
                <Link
                  to={`/projects/${projectId}/storyboards/${sb.id}`}
                  className="font-medium text-indigo-700 hover:underline"
                >
                  {sb.title}
                </Link>
                <span className="text-xs text-slate-500">
                  {sb.style} · {sb.aspect} · {sb.scenes} cảnh · {formatDate(sb.updated_at)}
                </span>
                <div className="ml-auto flex gap-1">
                  <Link
                    to={`/projects/${projectId}/storyboards/${sb.id}`}
                    className="rounded-lg px-3 py-1.5 text-xs font-medium text-slate-700 hover:bg-slate-100"
                  >
                    Mở trình chỉnh sửa
                  </Link>
                  <Link
                    to={`/videos/${sb.id}`}
                    className="rounded-lg px-3 py-1.5 text-xs font-medium text-indigo-700 hover:bg-indigo-50"
                  >
                    Duyệt &amp; đăng
                  </Link>
                  <Button
                    variant="ghost"
                    className="text-xs"
                    onClick={async () => {
                      if (!window.confirm("Xoá storyboard này?")) return;
                      await api.deleteStoryboard(sb.id).catch((e: Error) => setError(e.message));
                      await reload();
                    }}
                  >
                    Xoá
                  </Button>
                </div>
              </li>
            ))}
          </ul>
        </Card>
      )}

      {renders.length > 0 && (
        <Card title={`Video đã dựng (${renders.length})`}>
          <RendersList renders={renders} onDeleted={reload} />
        </Card>
      )}

      {history.length > 0 && (
        <Card title="Lịch sử job">
          <ul className="space-y-2 text-sm">
            {history.map((j) => (
              <li key={j.id} className="flex items-center gap-3">
                <StatusBadge status={j.status} />
                <span className="font-mono text-xs text-slate-500">{j.id.slice(0, 8)}</span>
                <span>{JOB_TYPE_LABELS[j.type] ?? j.type}</span>
                <span className="ml-auto text-xs text-slate-500">{formatDate(j.created_at)}</span>
              </li>
            ))}
          </ul>
        </Card>
      )}
    </div>
  );
}
