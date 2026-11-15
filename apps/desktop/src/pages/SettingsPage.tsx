import { useEffect, useState, type ReactNode } from "react";
import { JobProgress } from "../components/JobProgress";
import { Button, Card, ErrorBox } from "../components/ui";
import { useApi, useBackend } from "../lib/backendContext";
import type { SystemInfo } from "../lib/types";

function Row({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="grid grid-cols-[180px_1fr] gap-3 py-2 text-sm">
      <dt className="text-slate-500">{label}</dt>
      <dd className="break-all text-slate-900">{children}</dd>
    </div>
  );
}

function YesNo({ ok, yes, no }: { ok: boolean; yes: string; no: string }) {
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-xs font-medium ${
        ok ? "bg-emerald-100 text-emerald-800" : "bg-rose-100 text-rose-800"
      }`}
    >
      <span className={`size-1.5 rounded-full ${ok ? "bg-emerald-500" : "bg-rose-500"}`} />
      {ok ? yes : no}
    </span>
  );
}

export function SettingsPage() {
  const api = useApi();
  const { wsConnected } = useBackend();
  const [info, setInfo] = useState<SystemInfo | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [secretValue, setSecretValue] = useState("Mật khẩu thử nghiệm 123");
  const [stored, setStored] = useState<string | null>(null);
  const [secretResult, setSecretResult] = useState<{ ok: boolean; read_back: string | null } | null>(
    null,
  );
  const [demoJobId, setDemoJobId] = useState<string | null>(null);

  useEffect(() => {
    api
      .systemInfo()
      .then(setInfo)
      .catch((e: Error) => setError(e.message));
    api
      .getSelftestSecret()
      .then((r) => setStored(r.value))
      .catch(() => undefined);
  }, [api]);

  async function testSecret() {
    setError(null);
    try {
      const res = await api.selftestSecret(secretValue);
      setSecretResult(res);
      setStored(res.read_back);
    } catch (e) {
      setError((e as Error).message);
    }
  }

  async function runDemo() {
    const job = await api.demoJob(6, 12);
    setDemoJobId(job.id);
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold">Cài đặt</h1>
        <p className="text-sm text-slate-600">Thông tin hệ thống và công cụ kiểm tra.</p>
      </div>
      <ErrorBox>{error}</ErrorBox>

      <Card title="Hệ thống">
        {!info ? (
          <p className="text-sm text-slate-500">Đang tải…</p>
        ) : (
          <dl className="divide-y divide-slate-100">
            <Row label="Trạng thái backend">
              <YesNo ok yes={`Đang chạy · v${info.version}`} no="" />
            </Row>
            <Row label="Kết nối">
              {api.info.baseUrl} ({api.info.mode === "tauri" ? "sidecar của ứng dụng" : "trình duyệt / dev"})
              {" · "}
              WebSocket: {wsConnected ? "đã kết nối" : "đang kết nối lại"}
            </Row>
            <Row label="Thư mục dữ liệu">
              <code className="rounded bg-slate-100 px-1.5 py-0.5 text-xs">{info.data_dir}</code>
            </Row>
            <Row label="Cơ sở dữ liệu">
              <code className="rounded bg-slate-100 px-1.5 py-0.5 text-xs">{info.db_path}</code>
            </Row>
            <Row label="FFmpeg">
              <div className="space-y-1">
                <YesNo ok={info.ffmpeg.found} yes="Đã tìm thấy" no="Không tìm thấy" />
                {info.ffmpeg.found && (
                  <p className="text-xs text-slate-500">
                    {info.ffmpeg.ffmpeg} (nguồn: {info.ffmpeg.source})
                    <br />
                    {info.ffmpeg.version}
                  </p>
                )}
              </div>
            </Row>
            <Row label="Kho bí mật">{info.secret_store}</Row>
            <Row label="Hệ điều hành">{info.platform}</Row>
            <Row label="Python">
              {info.python}
              {info.frozen ? " (đóng gói PyInstaller)" : ""}
              {info.dev ? " · chế độ dev" : ""}
            </Row>
            <Row label="Loại job">{info.job_types.join(", ")}</Row>
          </dl>
        )}
      </Card>

      <Card title="Kiểm tra kho bí mật (SecretStore)">
        <div className="space-y-3">
          <p className="text-sm text-slate-600">
            Lưu một giá trị giả rồi đọc lại. Trên Windows giá trị được mã hoá bằng DPAPI theo tài
            khoản người dùng.
          </p>
          <div className="flex flex-wrap gap-2">
            <input
              aria-label="Giá trị bí mật thử nghiệm"
              className="input min-w-64 flex-1"
              value={secretValue}
              onChange={(e) => setSecretValue(e.target.value)}
            />
            <Button onClick={testSecret} disabled={!secretValue}>
              Lưu &amp; đọc lại
            </Button>
          </div>
          {secretResult && (
            <p className={`text-sm ${secretResult.ok ? "text-emerald-700" : "text-rose-700"}`}>
              {secretResult.ok ? "✓ Thành công" : "✗ Không khớp"} — đọc lại được: “
              {secretResult.read_back}”
            </p>
          )}
          <p className="text-xs text-slate-500">
            Giá trị đang lưu: {stored === null ? "(chưa có)" : `“${stored}”`}
          </p>
        </div>
      </Card>

      <Card title="Kiểm tra hàng đợi job" actions={<Button onClick={runDemo}>Chạy job demo</Button>}>
        {demoJobId ? (
          <JobProgress key={demoJobId} jobId={demoJobId} />
        ) : (
          <p className="text-sm text-slate-500">
            Job demo chạy khoảng 6 giây và báo tiến độ trực tiếp qua WebSocket.
          </p>
        )}
      </Card>
    </div>
  );
}
