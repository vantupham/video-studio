import { useEffect, useState, type ReactNode } from "react";
import { NavLink, useNavigate } from "react-router";
import { useBackend } from "../lib/backendContext";
import { setTrayPaused } from "../lib/desktop";
import { formatDate } from "../lib/format";
import type { AppNotification } from "../lib/types";
import { MissedSchedulesDialog } from "./MissedSchedulesDialog";

function NavItem({ to, children }: { to: string; children: ReactNode }) {
  return (
    <NavLink
      to={to}
      end={to === "/"}
      className={({ isActive }) =>
        `flex items-center gap-2 rounded-lg px-3 py-2 text-sm font-medium ${
          isActive ? "bg-indigo-50 text-indigo-700" : "text-slate-600 hover:bg-slate-100"
        }`
      }
    >
      {children}
    </NavLink>
  );
}

const LEVEL_STYLE: Record<AppNotification["level"], string> = {
  info: "border-sky-200",
  success: "border-emerald-300",
  warning: "border-amber-300",
  error: "border-rose-300",
};
const LEVEL_ICON: Record<AppNotification["level"], string> = {
  info: "ℹ️",
  success: "✅",
  warning: "⚠️",
  error: "⛔",
};

function Toasts() {
  const { toasts, dismissToast } = useBackend();
  const navigate = useNavigate();
  return (
    <div className="pointer-events-none fixed right-4 bottom-4 z-40 flex w-80 flex-col gap-2">
      {toasts.map((t) => (
        <div
          key={t.id}
          role="status"
          className={`pointer-events-auto rounded-lg border-l-4 bg-white p-3 text-sm shadow-lg ${LEVEL_STYLE[t.level]}`}
          data-testid="toast"
        >
          <div className="flex items-start gap-2">
            <span>{LEVEL_ICON[t.level]}</span>
            <button
              type="button"
              className="flex-1 text-left"
              onClick={() => {
                if (t.link) navigate(t.link);
                dismissToast(t.id);
              }}
            >
              <p className="font-medium">{t.title}</p>
              {t.message && <p className="text-xs text-slate-600">{t.message}</p>}
            </button>
            <button
              type="button"
              aria-label="Đóng"
              className="text-slate-400 hover:text-slate-700"
              onClick={() => dismissToast(t.id)}
            >
              ✕
            </button>
          </div>
        </div>
      ))}
    </div>
  );
}

function NotificationsMenu() {
  const { notifications, markNotificationsRead } = useBackend();
  const [open, setOpen] = useState(false);
  const navigate = useNavigate();
  const unread = notifications.filter((n) => !n.read).length;
  return (
    <div className="relative">
      <button
        type="button"
        className="flex w-full items-center gap-2 rounded-lg px-3 py-2 text-sm font-medium text-slate-600 hover:bg-slate-100"
        onClick={() => {
          setOpen((o) => !o);
          if (!open && unread) markNotificationsRead();
        }}
        aria-expanded={open}
        data-testid="notifications-button"
      >
        Thông báo
        {unread > 0 && (
          <span className="ml-auto rounded-full bg-rose-600 px-1.5 text-[11px] font-semibold text-white">
            {unread}
          </span>
        )}
      </button>
      {open && (
        <div className="absolute bottom-0 left-full z-30 ml-2 max-h-96 w-80 overflow-y-auto rounded-xl border border-slate-200 bg-white p-2 shadow-xl">
          {notifications.length === 0 ? (
            <p className="p-3 text-sm text-slate-500">Chưa có thông báo.</p>
          ) : (
            <ul className="divide-y divide-slate-100">
              {notifications.map((n) => (
                <li key={n.id}>
                  <button
                    type="button"
                    className="w-full p-2 text-left text-sm hover:bg-slate-50"
                    onClick={() => {
                      if (n.link) navigate(n.link);
                      setOpen(false);
                    }}
                  >
                    <p className="font-medium">
                      {LEVEL_ICON[n.level]} {n.title}
                    </p>
                    {n.message && <p className="text-xs text-slate-600">{n.message}</p>}
                    <p className="text-[11px] text-slate-400">{formatDate(n.ts)}</p>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}

export function Layout({ children }: { children: ReactNode }) {
  const { api, wsConnected, queuePaused, setQueuePaused } = useBackend();

  useEffect(() => {
    void setTrayPaused(queuePaused);
  }, [queuePaused]);

  return (
    <div className="flex h-screen bg-slate-50 text-slate-900">
      <aside className="flex w-56 shrink-0 flex-col border-r border-slate-200 bg-white">
        <div className="flex items-center gap-2 px-5 py-5">
          <span className="grid size-8 place-items-center rounded-lg bg-indigo-600 text-sm font-bold text-white">
            VS
          </span>
          <div>
            <p className="text-sm font-semibold">Video Studio</p>
            <p className="text-[11px] text-slate-500">Giai đoạn 2</p>
          </div>
        </div>
        <nav className="flex flex-col gap-1 px-3">
          <NavItem to="/">Dự án</NavItem>
          <NavItem to="/videos">Thư viện video</NavItem>
          <NavItem to="/publications">Lịch đăng</NavItem>
          <NavItem to="/settings">Cài đặt</NavItem>
          <NotificationsMenu />
        </nav>
        <div className="mt-auto space-y-1 border-t border-slate-100 px-5 py-4 text-xs text-slate-500">
          {queuePaused && (
            <button
              type="button"
              className="mb-2 w-full rounded-lg bg-amber-100 px-2 py-1.5 text-left font-medium text-amber-900 hover:bg-amber-200"
              onClick={() => setQueuePaused(false)}
              data-testid="queue-paused"
            >
              ⏸ Hàng đợi đang tạm dừng — bấm để tiếp tục
            </button>
          )}
          <p className="flex items-center gap-2">
            <span className="size-2 rounded-full bg-emerald-500" /> Backend đang chạy
          </p>
          <p className="flex items-center gap-2">
            <span
              className={`size-2 rounded-full ${wsConnected ? "bg-emerald-500" : "bg-amber-500"}`}
            />
            {wsConnected ? "Cập nhật trực tiếp" : "Đang kết nối lại…"}
          </p>
          {api?.info.mode === "browser" && (
            <p className="text-amber-700">Chế độ trình duyệt (dev)</p>
          )}
        </div>
      </aside>
      <main className="flex-1 overflow-y-auto">
        <div className="mx-auto max-w-5xl px-8 py-8">{children}</div>
      </main>
      <Toasts />
      <MissedSchedulesDialog />
    </div>
  );
}
