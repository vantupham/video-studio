import { useCallback, useEffect, useMemo, useState, type FormEvent } from "react";
import { Link, useParams } from "react-router";
import { IrViewer } from "../components/IrViewer";
import { JobProgress } from "../components/JobProgress";
import { SchemaForm } from "../components/SchemaForm";
import { Button, Card, ErrorBox, StatusBadge } from "../components/ui";
import { useApi } from "../lib/backendContext";
import { formatBytes, formatDate } from "../lib/format";
import { defaultsFromSchema, type FormValues } from "../lib/schemaForm";
import type { Adapter, IRDocument, Job, ProjectDetail } from "../lib/types";

type InputMode = "text" | "file";

const SAMPLE_SCRIPT = `Cảnh 1: Giới thiệu
Xin chào các bạn! Hôm nay mình sẽ hướng dẫn cách biến một tài liệu thành video có thuyết minh tiếng Việt.

Cảnh 2: Các bước thực hiện
Đầu tiên, dán kịch bản hoặc chọn file Markdown. Sau đó bấm “Chạy phân tích” để tạo Content IR.

Cảnh 3: Kết thúc
Cảm ơn các bạn đã theo dõi. Hẹn gặp lại!`;

function adapterForFile(adapters: Adapter[], file: File): Adapter | undefined {
  const dot = file.name.lastIndexOf(".");
  const ext = dot >= 0 ? file.name.slice(dot).toLowerCase() : "";
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
  const [file, setFile] = useState<File | null>(null);
  const [sourceName, setSourceName] = useState("");
  const [adapterId, setAdapterId] = useState("script");
  const [config, setConfig] = useState<FormValues>({});
  const [adding, setAdding] = useState(false);

  const [jobId, setJobId] = useState<string | null>(null);
  const [history, setHistory] = useState<Job[]>([]);
  const [docId, setDocId] = useState<string | null>(null);
  const [doc, setDoc] = useState<IRDocument | null>(null);
  const [preview, setPreview] = useState<{ id: string; content: string } | null>(null);

  const adapter = useMemo(() => adapters.find((a) => a.id === adapterId), [adapters, adapterId]);

  const reload = useCallback(
    () =>
      Promise.all([api.getProject(projectId), api.listJobs({ projectId, limit: 8 })])
        .then(([p, jobs]) => {
          setProject(p);
          setHistory(jobs);
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
      const active = jobs.find((j) => j.status === "queued" || j.status === "running");
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

  function chooseMode(next: InputMode) {
    setMode(next);
    if (next === "text") selectAdapter("script", adapters);
    else if (file) selectAdapter(adapterForFile(adapters, file)?.id ?? "script", adapters);
  }

  function onFile(f: File | null) {
    setFile(f);
    if (f) {
      selectAdapter(adapterForFile(adapters, f)?.id ?? "script", adapters);
      if (!sourceName) setSourceName(f.name);
    }
  }

  async function onAddSource(e: FormEvent) {
    e.preventDefault();
    setAdding(true);
    setError(null);
    try {
      await api.addSource(projectId, {
        text: mode === "text" ? text : undefined,
        file: mode === "file" ? (file ?? undefined) : undefined,
        name: sourceName.trim() || undefined,
        adapterId,
        config,
      });
      setText("");
      setFile(null);
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
    const content = await api.sourceContent(projectId, sourceId);
    setPreview({ id: sourceId, content });
  }

  async function removeSource(sourceId: string) {
    if (!window.confirm("Xoá nguồn này?")) return;
    await api.deleteSource(projectId, sourceId).catch((e: Error) => setError(e.message));
    await reload();
  }

  if (!project) {
    return error ? <ErrorBox>{error}</ErrorBox> : <p className="text-sm text-slate-500">Đang tải…</p>;
  }

  const canAdd = mode === "text" ? text.trim().length > 0 : file !== null;
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
              Chọn file (.md / .txt)
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
                File
              </label>
              <input
                id="source-file"
                type="file"
                accept=".md,.markdown,.txt,text/markdown,text/plain"
                className="text-sm file:mr-3 file:rounded-lg file:border-0 file:bg-indigo-50 file:px-3 file:py-2 file:text-sm file:font-medium file:text-indigo-700 hover:file:bg-indigo-100"
                onChange={(e) => onFile(e.target.files?.[0] ?? null)}
              />
              {file && (
                <p className="text-xs text-slate-500">
                  {file.name} · {formatBytes(file.size)}
                </p>
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
              <SchemaForm schema={adapter.config_schema} value={config} onChange={setConfig} />
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
                    {s.adapter_id} · {formatBytes(s.size_bytes)} · {formatDate(s.created_at)}
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

      {history.length > 0 && (
        <Card title="Lịch sử job">
          <ul className="space-y-2 text-sm">
            {history.map((j) => (
              <li key={j.id} className="flex items-center gap-3">
                <StatusBadge status={j.status} />
                <span className="font-mono text-xs text-slate-500">{j.id.slice(0, 8)}</span>
                <span>{j.type}</span>
                <span className="ml-auto text-xs text-slate-500">{formatDate(j.created_at)}</span>
              </li>
            ))}
          </ul>
        </Card>
      )}
    </div>
  );
}
