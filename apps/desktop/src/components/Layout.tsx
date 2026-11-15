import type { ReactNode } from "react";
import { NavLink } from "react-router";
import { useBackend } from "../lib/backendContext";

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

export function Layout({ children }: { children: ReactNode }) {
  const { api, wsConnected } = useBackend();
  return (
    <div className="flex h-screen bg-slate-50 text-slate-900">
      <aside className="flex w-56 shrink-0 flex-col border-r border-slate-200 bg-white">
        <div className="flex items-center gap-2 px-5 py-5">
          <span className="grid size-8 place-items-center rounded-lg bg-indigo-600 text-sm font-bold text-white">
            VS
          </span>
          <div>
            <p className="text-sm font-semibold">Video Studio</p>
            <p className="text-[11px] text-slate-500">Giai đoạn 1</p>
          </div>
        </div>
        <nav className="flex flex-col gap-1 px-3">
          <NavItem to="/">Dự án</NavItem>
          <NavItem to="/settings">Cài đặt</NavItem>
        </nav>
        <div className="mt-auto space-y-1 border-t border-slate-100 px-5 py-4 text-xs text-slate-500">
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
    </div>
  );
}
