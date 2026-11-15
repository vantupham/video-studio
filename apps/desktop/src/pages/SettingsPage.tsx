import { useEffect, useState, type ReactNode } from "react";
import { useSearchParams } from "react-router";
import { BrandingSettings } from "../components/BrandingSettings";
import { JobProgress } from "../components/JobProgress";
import { ProvidersSettings } from "../components/ProvidersSettings";
import { SocialSettings } from "../components/SocialSettings";
import { Button, Card, ErrorBox } from "../components/ui";
import { useApi, useBackend } from "../lib/backendContext";
import { applyDesktopPrefs, isTauri } from "../lib/desktop";
import type { DesktopPrefs, SystemInfo } from "../lib/types";

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

type Tab = "providers" | "social" | "branding" | "system";

const TABS: { id: Tab; label: string }[] = [
  { id: "providers", label: "Nhà cung cấp" },
  { id: "social", label: "Tài khoản mạng xã hội" },
  { id: "branding", label: "Thương hiệu" },
  { id: "system", label: "Hệ thống" },
];

export function SettingsPage() {
  const [params, setParams] = useSearchParams();
  const tab = (TABS.find((t) => t.id === params.get("tab"))?.id ?? "providers") as Tab;
  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold">Cài đặt</h1>
        <p className="text-sm text-slate-600">
          Nhà cung cấp AI, tài khoản mạng xã hội, thương hiệu và hệ thống.
        </p>
      </div>
      <div className="inline-flex rounded-lg bg-slate-100 p-1" role="tablist">
        {TABS.map((t) => (
          <button
            key={t.id}
            type="button"
            role="tab"
            aria-selected={tab === t.id}
            onClick={() => setParams({ tab: t.id })}
            className={`rounded-md px-3 py-1.5 text-sm font-medium ${
              tab === t.id
                ? "bg-white text-slate-900 shadow-sm"
                : "text-slate-600 hover:text-slate-900"
            }`}
          >
            {t.label}
          </button>
        ))}
      </div>
      {tab === "providers" && <ProvidersSettings />}
      {tab === "social" && <SocialSettings />}
      {tab === "branding" && <BrandingSettings />}
      {tab === "system" && <SystemSettings />}
    </div>
  );
}

function SystemSettings() {
  const api = useApi();
  const { wsConnected } = useBackend();
  const [info, setInfo] = useState<SystemInfo | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [secretValue, setSecretValue] = useState("Mật khẩu thử nghiệm 123");
  const [stored, setStored] = useState<string | null>(null);
  const [secretResult, setSecretResult] = useState<{
    ok: boolean;
    read_back: string | null;
  } | null>(null);
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
      <ErrorBox>{error}</ErrorBox>

      <DesktopSettings />

      <Card title="Hệ thống">
        {!info ? (
          <p className="text-sm text-slate-500">Đang tải…</p>
        ) : (
          <dl className="divide-y divide-slate-100">
            <Row label="Trạng thái backend">
              <YesNo ok yes={`Đang chạy · v${info.version}`} no="" />
            </Row>
            <Row label="Kết nối">
              {api.info.baseUrl} (
              {api.info.mode === "tauri" ? "sidecar của ứng dụng" : "trình duyệt / dev"}){" · "}
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
            <Row label="Plugin">
              <div className="space-y-1">
                <p className="text-xs text-slate-600">
                  {Object.values(info.plugins.providers).flat().length} nhà cung cấp ·{" "}
                  {info.plugins.input_adapters.length} adapter · renderer:{" "}
                  {info.plugins.renderers.join(", ")}
                </p>
                {info.plugins.errors.map((e) => (
                  <p key={e.origin} className="text-xs text-rose-700">
                    Lỗi nạp {e.origin}: {e.error}
                  </p>
                ))}
              </div>
            </Row>
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

      <Card
        title="Kiểm tra hàng đợi job"
        actions={<Button onClick={runDemo}>Chạy job demo</Button>}
      >
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

const PREF_LABELS: { key: keyof DesktopPrefs; label: string; hint: string; desktopOnly?: boolean }[] = [
  {
    key: "close_to_tray",
    label: "Đóng cửa sổ thì thu nhỏ xuống khay hệ thống",
    hint: "Ứng dụng tiếp tục chạy nền để dựng video và đăng theo lịch. Thoát hẳn từ biểu tượng ở khay.",
    desktopOnly: true,
  },
  {
    key: "autostart",
    label: "Khởi động cùng Windows",
    hint: "Cần cho lịch đăng khi bạn hay tắt ứng dụng.",
    desktopOnly: true,
  },
  {
    key: "start_minimized",
    label: "Khi tự khởi động, chỉ chạy dưới khay (không mở cửa sổ)",
    hint: "",
    desktopOnly: true,
  },
  {
    key: "keep_awake",
    label: "Không cho máy ngủ khi đang dựng / đăng",
    hint: "Dùng SetThreadExecutionState của Windows; máy được phép ngủ lại khi xong.",
  },
  {
    key: "wake_for_schedule",
    label: "Đánh thức máy trước giờ đăng (Task Scheduler)",
    hint: "Tạo tác vụ VideoStudio\\WakeForPublish có quyền đánh thức máy. Cần bật “Allow wake timers” trong Power Options.",
  },
  {
    key: "notifications",
    label: "Hiện thông báo Windows khi đăng xong / lỗi",
    hint: "",
  },
];

function DesktopSettings() {
  const api = useApi();
  const { queuePaused, setQueuePaused } = useBackend();
  const [prefs, setPrefs] = useState<DesktopPrefs | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api
      .desktopPrefs()
      .then(setPrefs)
      .catch((e: Error) => setError(e.message));
  }, [api]);

  async function update(patch: Partial<DesktopPrefs>) {
    setError(null);
    try {
      const next = await api.saveDesktopPrefs(patch);
      setPrefs(next);
      await applyDesktopPrefs(next);
    } catch (e) {
      setError(String((e as Error).message ?? e));
    }
  }

  return (
    <Card
      title="Chạy nền & lịch đăng"
      actions={
        <Button variant="secondary" className="py-1 text-xs" onClick={() => setQueuePaused(!queuePaused)}>
          {queuePaused ? "▶ Tiếp tục hàng đợi" : "⏸ Tạm dừng hàng đợi"}
        </Button>
      }
    >
      {!prefs ? (
        <p className="text-sm text-slate-500">Đang tải…</p>
      ) : (
        <div className="space-y-3">
          {PREF_LABELS.map((p) => (
            <label key={p.key} className="flex items-start gap-2 text-sm">
              <input
                type="checkbox"
                className="mt-0.5 size-4 accent-indigo-600"
                checked={prefs[p.key]}
                onChange={(e) => update({ [p.key]: e.target.checked })}
                data-testid={`pref-${p.key}`}
              />
              <span>
                {p.label}
                {p.desktopOnly && !isTauri() && (
                  <span className="ml-1 text-xs text-slate-400">(chỉ trong ứng dụng desktop)</span>
                )}
                {p.hint && <span className="block text-xs text-slate-500">{p.hint}</span>}
              </span>
            </label>
          ))}
          <ErrorBox>{error}</ErrorBox>
        </div>
      )}
    </Card>
  );
}
