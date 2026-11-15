import { useCallback, useEffect, useState, type FormEvent } from "react";
import { Link, useNavigate } from "react-router";
import { Button, Card, ErrorBox } from "../components/ui";
import { useApi } from "../lib/backendContext";
import { formatDate } from "../lib/format";
import type { Project } from "../lib/types";

export function ProjectsPage() {
  const api = useApi();
  const navigate = useNavigate();
  const [projects, setProjects] = useState<Project[] | null>(null);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(() => {
    api
      .listProjects()
      .then(setProjects)
      .catch((e: Error) => setError(e.message));
  }, [api]);

  useEffect(load, [load]);

  async function onCreate(e: FormEvent) {
    e.preventDefault();
    if (!name.trim()) return;
    setBusy(true);
    setError(null);
    try {
      const project = await api.createProject(name.trim(), description.trim());
      navigate(`/projects/${project.id}`);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function onDelete(project: Project) {
    if (!window.confirm(`Xoá dự án “${project.name}” và toàn bộ dữ liệu của nó?`)) return;
    await api.deleteProject(project.id).catch((e: Error) => setError(e.message));
    load();
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold">Dự án</h1>
        <p className="text-sm text-slate-600">
          Mỗi dự án chứa các nguồn nội dung và kết quả phân tích (Content IR) để dựng video.
        </p>
      </div>

      <Card title="Tạo dự án mới">
        <form onSubmit={onCreate} className="grid gap-3 sm:grid-cols-[1fr_1.5fr_auto] sm:items-end">
          <div className="flex flex-col gap-1">
            <label htmlFor="project-name" className="text-sm font-medium">
              Tên dự án
            </label>
            <input
              id="project-name"
              className="input"
              placeholder="Ví dụ: Hướng dẫn sử dụng phần mềm"
              value={name}
              onChange={(e) => setName(e.target.value)}
            />
          </div>
          <div className="flex flex-col gap-1">
            <label htmlFor="project-desc" className="text-sm font-medium">
              Mô tả (không bắt buộc)
            </label>
            <input
              id="project-desc"
              className="input"
              placeholder="Ghi chú ngắn về video"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
            />
          </div>
          <Button type="submit" disabled={busy || !name.trim()}>
            Tạo dự án
          </Button>
        </form>
      </Card>

      <ErrorBox>{error}</ErrorBox>

      {projects === null ? (
        <p className="text-sm text-slate-500">Đang tải…</p>
      ) : projects.length === 0 ? (
        <div className="rounded-xl border border-dashed border-slate-300 p-10 text-center text-slate-500">
          Chưa có dự án nào. Hãy tạo dự án đầu tiên ở trên.
        </div>
      ) : (
        <ul className="grid gap-4 sm:grid-cols-2">
          {projects.map((p) => (
            <li
              key={p.id}
              className="group rounded-xl border border-slate-200 bg-white p-5 shadow-sm transition hover:border-indigo-300 hover:shadow"
            >
              <Link to={`/projects/${p.id}`} className="block">
                <h3 className="font-semibold text-slate-900 group-hover:text-indigo-700">
                  {p.name}
                </h3>
                {p.description && <p className="mt-1 text-sm text-slate-600">{p.description}</p>}
                <p className="mt-3 text-xs text-slate-500">
                  {p.source_count} nguồn · {p.document_count} Content IR · cập nhật{" "}
                  {formatDate(p.updated_at)}
                </p>
              </Link>
              <div className="mt-3 flex justify-end">
                <Button variant="ghost" className="text-xs" onClick={() => onDelete(p)}>
                  Xoá
                </Button>
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
