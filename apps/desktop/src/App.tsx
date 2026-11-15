import { HashRouter, Route, Routes } from "react-router";
import { BackendProvider } from "./components/BackendProvider";
import { Layout } from "./components/Layout";
import { Button } from "./components/ui";
import { useBackend } from "./lib/backendContext";
import { ProjectDetailPage } from "./pages/ProjectDetailPage";
import { ProjectsPage } from "./pages/ProjectsPage";
import { SettingsPage } from "./pages/SettingsPage";

function Gate() {
  const { status, error, attempts, retry } = useBackend();
  if (status === "connecting") {
    return (
      <div className="grid h-screen place-items-center bg-slate-50">
        <div className="text-center">
          <div className="mx-auto mb-4 size-10 animate-spin rounded-full border-4 border-indigo-200 border-t-indigo-600" />
          <p className="font-medium text-slate-800">Đang khởi động backend…</p>
          <p className="text-xs text-slate-500">Lần thử {attempts}</p>
        </div>
      </div>
    );
  }
  if (status === "error") {
    return (
      <div className="grid h-screen place-items-center bg-slate-50 p-6">
        <div className="max-w-md space-y-3 rounded-xl border border-rose-200 bg-white p-6 text-center shadow-sm">
          <p className="text-lg font-semibold text-rose-700">Không kết nối được backend</p>
          <p className="text-sm break-words text-slate-600">{error}</p>
          <p className="text-xs text-slate-500">
            Chế độ trình duyệt: hãy chạy backend bằng <code>scripts/dev.sh --web</code> (hoặc
            dev.ps1 -Web).
          </p>
          <Button onClick={retry}>Thử lại</Button>
        </div>
      </div>
    );
  }
  return (
    <Layout>
      <Routes>
        <Route path="/" element={<ProjectsPage />} />
        <Route path="/projects/:projectId" element={<ProjectDetailPage />} />
        <Route path="/settings" element={<SettingsPage />} />
      </Routes>
    </Layout>
  );
}

export default function App() {
  return (
    <HashRouter>
      <BackendProvider>
        <Gate />
      </BackendProvider>
    </HashRouter>
  );
}
