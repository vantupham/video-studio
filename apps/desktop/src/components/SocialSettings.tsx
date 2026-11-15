import { useCallback, useEffect, useState } from "react";
import { useApi } from "../lib/backendContext";
import { facebookLogin, isTauri, openExternal } from "../lib/desktop";
import { formatDate } from "../lib/format";
import { PLATFORM_LABELS } from "../lib/labels";
import type { OAuthFlow, Platform, SocialAccount, SocialAppConfig } from "../lib/types";
import { PlatformIcon } from "./badges";
import { Button, Card, ErrorBox, Modal } from "./ui";

const GUIDES: Record<Platform, string> = {
  youtube: "docs/publishing/youtube.md",
  facebook: "docs/publishing/facebook.md",
};

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

function AppConfigForm({ cfg, onSaved }: { cfg: SocialAppConfig; onSaved: () => void }) {
  const api = useApi();
  const [clientId, setClientId] = useState(cfg.client_id);
  const [secret, setSecret] = useState("");
  const [testing, setTesting] = useState(cfg.testing_mode ?? true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const yt = cfg.platform === "youtube";
  const dirty =
    clientId !== cfg.client_id || secret !== "" || (yt && testing !== (cfg.testing_mode ?? true));

  async function save() {
    setBusy(true);
    setError(null);
    try {
      await api.saveSocialApp(cfg.platform, {
        client_id: clientId,
        ...(secret ? { client_secret: secret } : {}),
        ...(yt ? { testing_mode: testing } : {}),
      });
      setSecret("");
      setSaved(true);
      onSaved();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function clearSecret() {
    setError(null);
    try {
      await api.clearSocialAppSecret(cfg.platform);
      onSaved();
    } catch (e) {
      setError((e as Error).message);
    }
  }

  return (
    <div className="space-y-3">
      <div className="grid gap-3 sm:grid-cols-2">
        <label className="flex flex-col gap-1 text-sm">
          <span className="font-medium">{yt ? "Client ID (OAuth, Ứng dụng máy tính)" : "App ID"}</span>
          <input
            className="input font-mono"
            value={clientId}
            spellCheck={false}
            placeholder={yt ? "1234…apps.googleusercontent.com" : "123456789012345"}
            onChange={(e) => {
              setClientId(e.target.value);
              setSaved(false);
            }}
            aria-label={`${PLATFORM_LABELS[cfg.platform]} client id`}
          />
        </label>
        <label className="flex flex-col gap-1 text-sm">
          <span className="font-medium">{yt ? "Client secret" : "App secret"}</span>
          <div className="flex gap-2">
            <input
              className="input flex-1 font-mono"
              type="password"
              autoComplete="off"
              value={secret}
              placeholder={
                cfg.client_secret ? `${cfg.client_secret} — đã lưu, để trống để giữ nguyên` : "Chưa nhập"
              }
              onChange={(e) => {
                setSecret(e.target.value);
                setSaved(false);
              }}
              aria-label={`${PLATFORM_LABELS[cfg.platform]} secret`}
            />
            {cfg.has_secret && (
              <button
                type="button"
                className="rounded-lg border border-rose-200 px-2.5 text-xs text-rose-700 hover:bg-rose-50"
                onClick={clearSecret}
              >
                Xoá khoá
              </button>
            )}
          </div>
        </label>
      </div>
      {yt && (
        <label className="flex items-start gap-2 text-sm">
          <input
            type="checkbox"
            className="mt-0.5 accent-indigo-600"
            checked={testing}
            onChange={(e) => setTesting(e.target.checked)}
          />
          <span>
            Dự án Google đang ở chế độ <b>Testing</b>
            <span className="block text-xs text-slate-500">
              Ở chế độ này refresh token hết hạn sau 7 ngày — ứng dụng sẽ nhắc bạn kết nối lại.
            </span>
          </span>
        </label>
      )}
      <p className="text-xs text-slate-500">
        Địa chỉ chuyển hướng: <code className="rounded bg-slate-100 px-1">{cfg.redirect}</code>
        {" · "}Hướng dẫn tạo ứng dụng: <code className="rounded bg-slate-100 px-1">{GUIDES[cfg.platform]}</code>
      </p>
      <ErrorBox>{error}</ErrorBox>
      <div className="flex items-center gap-3">
        <Button variant="secondary" disabled={!dirty || busy} onClick={save}>
          Lưu thông tin ứng dụng
        </Button>
        {saved && !dirty && <span className="text-sm text-emerald-700">✓ Đã lưu</span>}
        {cfg.configured ? (
          <span className="text-xs text-emerald-700">● Đã cấu hình</span>
        ) : (
          <span className="text-xs text-slate-500">○ Chưa cấu hình</span>
        )}
      </div>
    </div>
  );
}

function FacebookFlowDialog({
  flow: initial,
  onClose,
  onDone,
}: {
  flow: OAuthFlow;
  onClose: () => void;
  onDone: () => void;
}) {
  const api = useApi();
  const [flow, setFlow] = useState(initial);
  const [url, setUrl] = useState("");
  const [pages, setPages] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const complete = useCallback(
    async (redirect: string) => {
      setBusy(true);
      setError(null);
      try {
        const f = await api.completeFacebook(initial.id, redirect);
        setFlow(f);
        setPages(f.pages.map((p) => p.id));
      } catch (e) {
        setError((e as Error).message);
      } finally {
        setBusy(false);
      }
    },
    [api, initial.id],
  );

  useEffect(() => {
    if (!isTauri()) return;
    let cancelled = false;
    facebookLogin(initial.auth_url)
      .then((redirect) => {
        if (!cancelled) void complete(redirect);
      })
      .catch((e: unknown) => {
        if (!cancelled) setError(String(e));
      });
    return () => {
      cancelled = true;
    };
  }, [initial.auth_url, complete]);

  async function select() {
    setBusy(true);
    setError(null);
    try {
      await api.selectFacebookPages(initial.id, pages);
      onDone();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal
      title="Kết nối Facebook Page"
      onClose={onClose}
      footer={
        flow.status === "select_pages" ? (
          <Button disabled={busy || pages.length === 0} onClick={select}>
            Kết nối {pages.length} Trang
          </Button>
        ) : !isTauri() ? (
          <Button disabled={busy || !url.trim()} onClick={() => complete(url.trim())}>
            Tiếp tục
          </Button>
        ) : undefined
      }
    >
      <div className="space-y-3 text-sm">
        {flow.status === "select_pages" ? (
          <>
            <p>Chọn các Trang mà Video Studio được phép đăng video:</p>
            <ul className="space-y-1.5">
              {flow.pages.map((p) => (
                <li key={p.id}>
                  <label className="flex items-center gap-2 rounded-lg border border-slate-200 p-2">
                    <input
                      type="checkbox"
                      className="accent-indigo-600"
                      checked={pages.includes(p.id)}
                      onChange={(e) =>
                        setPages((s) => (e.target.checked ? [...s, p.id] : s.filter((x) => x !== p.id)))
                      }
                    />
                    {p.picture && <img src={p.picture} alt="" className="size-6 rounded" />}
                    <span className="font-medium">{p.name}</span>
                    <span className="text-xs text-slate-500">{p.category}</span>
                  </label>
                </li>
              ))}
            </ul>
            {flow.pages.length === 0 && (
              <p className="text-amber-700">
                Tài khoản này không quản trị Trang nào (hoặc chưa cấp quyền cho Trang).
              </p>
            )}
          </>
        ) : isTauri() ? (
          <p>Đăng nhập Facebook trong cửa sổ vừa mở. Cửa sổ sẽ tự đóng khi xong.</p>
        ) : (
          <>
            <ol className="list-decimal space-y-1 pl-5">
              <li>
                <button
                  type="button"
                  className="text-indigo-700 hover:underline"
                  onClick={() => openExternal(initial.auth_url)}
                >
                  Mở trang đăng nhập Facebook
                </button>{" "}
                và cấp quyền cho Trang của bạn.
              </li>
              <li>
                Khi trình duyệt chuyển tới trang <code>login_success.html</code>, sao chép toàn bộ
                địa chỉ (URL) trên thanh địa chỉ và dán vào đây:
              </li>
            </ol>
            <input
              className="input w-full font-mono text-xs"
              placeholder="https://www.facebook.com/connect/login_success.html?code=…"
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              aria-label="URL login_success"
            />
          </>
        )}
        <ErrorBox>{error}</ErrorBox>
      </div>
    </Modal>
  );
}

function AccountRow({ a, onChanged }: { a: SocialAccount; onChanged: () => void }) {
  const api = useApi();
  const [error, setError] = useState<string | null>(null);
  return (
    <li className="space-y-1 py-3" data-testid="social-account">
      <div className="flex flex-wrap items-center gap-2">
        <PlatformIcon platform={a.platform} />
        {a.avatar_url && <img src={a.avatar_url} alt="" className="size-6 rounded-full" />}
        <span className="font-medium">{a.name}</span>
        <span className="text-xs text-slate-500">{a.kind === "page" ? "Trang" : "Kênh"}</span>
        {a.mock && (
          <span className="rounded bg-slate-800 px-1.5 text-[10px] font-semibold text-white">
            GIẢ LẬP
          </span>
        )}
        <span
          className={`text-xs ${a.status === "connected" ? "text-emerald-700" : "text-rose-700"}`}
        >
          {a.status === "connected" ? "● Đã kết nối" : "● Hết hạn"}
        </span>
        <Button
          variant="ghost"
          className="ml-auto py-1 text-xs text-rose-700"
          onClick={async () => {
            if (!window.confirm(`Ngắt kết nối ${a.name}? Token sẽ bị xoá khỏi máy.`)) return;
            setError(null);
            try {
              await api.disconnectAccount(a.id);
              onChanged();
            } catch (e) {
              setError((e as Error).message);
            }
          }}
        >
          Ngắt kết nối
        </Button>
      </div>
      {a.token_expires_at && (
        <p className="text-xs text-slate-500">Quyền truy cập đến {formatDate(a.token_expires_at)}</p>
      )}
      {a.warning && <p className="text-xs text-amber-700">⚠️ {a.warning}</p>}
      <ErrorBox>{error}</ErrorBox>
    </li>
  );
}

export function SocialSettings() {
  const api = useApi();
  const [apps, setApps] = useState<SocialAppConfig[]>([]);
  const [accounts, setAccounts] = useState<SocialAccount[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [ytFlow, setYtFlow] = useState<OAuthFlow | null>(null);
  const [fbFlow, setFbFlow] = useState<OAuthFlow | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const load = useCallback(() => {
    api.socialApps().then(setApps).catch((e: Error) => setError(e.message));
    api.socialAccounts().then(setAccounts).catch(() => undefined);
  }, [api]);
  useEffect(load, [load]);

  async function connectYouTube() {
    setError(null);
    setNotice(null);
    try {
      let flow = await api.connectYouTube(true);
      setYtFlow(flow);
      for (let i = 0; i < 400 && (flow.status === "pending" || flow.status === "exchanging"); i++) {
        await sleep(1500);
        flow = await api.oauthFlow(flow.id);
        setYtFlow(flow);
      }
      if (flow.status === "done") {
        setNotice("Đã kết nối kênh YouTube.");
        setYtFlow(null);
        load();
      } else if (flow.status === "error") {
        setError(flow.error ?? "Kết nối thất bại");
        setYtFlow(null);
      }
    } catch (e) {
      setError((e as Error).message);
      setYtFlow(null);
    }
  }

  async function connectFacebook() {
    setError(null);
    setNotice(null);
    try {
      setFbFlow(await api.connectFacebook());
    } catch (e) {
      setError((e as Error).message);
    }
  }

  async function addMock(platform: Platform) {
    setError(null);
    try {
      await api.connectMockAccount(platform);
      load();
    } catch (e) {
      setError((e as Error).message);
    }
  }

  return (
    <div className="space-y-6">
      <div className="rounded-lg border border-sky-200 bg-sky-50 px-4 py-3 text-sm text-sky-900">
        Video Studio đăng bằng <b>ứng dụng Google / Meta của chính bạn</b>: bạn tạo ứng dụng, nhập
        Client ID và secret ở đây, rồi kết nối kênh / Trang. Mọi token được mã hoá trong kho bí mật
        của Windows (DPAPI) và không bao giờ rời khỏi máy.
      </div>
      <ErrorBox>{error}</ErrorBox>
      {notice && <p className="text-sm text-emerald-700">✓ {notice}</p>}

      <Card title="Tài khoản đã kết nối">
        {accounts.length === 0 ? (
          <p className="text-sm text-slate-500">Chưa kết nối tài khoản nào.</p>
        ) : (
          <ul className="divide-y divide-slate-100">
            {accounts.map((a) => (
              <AccountRow key={a.id} a={a} onChanged={load} />
            ))}
          </ul>
        )}
        <div className="mt-3 flex flex-wrap gap-2 border-t border-slate-100 pt-3">
          <Button variant="secondary" className="text-xs" onClick={() => addMock("youtube")} data-testid="add-mock-youtube">
            + YouTube giả lập
          </Button>
          <Button variant="secondary" className="text-xs" onClick={() => addMock("facebook")} data-testid="add-mock-facebook">
            + Facebook giả lập
          </Button>
          <span className="self-center text-xs text-slate-500">
            Tài khoản giả lập không đăng gì lên mạng — dùng để thử quy trình.
          </span>
        </div>
      </Card>

      {apps.map((cfg) => (
        <Card
          key={cfg.platform}
          title={
            <span className="flex items-center gap-2">
              <PlatformIcon platform={cfg.platform} /> {PLATFORM_LABELS[cfg.platform]}
            </span>
          }
          actions={
            cfg.platform === "youtube" ? (
              <Button disabled={!cfg.configured || ytFlow !== null} onClick={connectYouTube}>
                {ytFlow ? "Đang chờ đăng nhập…" : "Kết nối kênh YouTube"}
              </Button>
            ) : (
              <Button disabled={!cfg.configured} onClick={connectFacebook}>
                Kết nối Facebook Page
              </Button>
            )
          }
        >
          <AppConfigForm key={`${cfg.client_id}-${cfg.client_secret}`} cfg={cfg} onSaved={load} />
          {cfg.platform === "youtube" && ytFlow && (
            <div className="mt-3 rounded-lg bg-slate-50 p-3 text-sm">
              <p>
                Trình duyệt đã mở trang đăng nhập Google. Không thấy?{" "}
                <button
                  type="button"
                  className="text-indigo-700 hover:underline"
                  onClick={() => openExternal(ytFlow.auth_url)}
                >
                  Mở lại
                </button>
              </p>
              <p className="text-xs text-slate-500">
                Nếu Google báo “ứng dụng chưa được xác minh”, chọn Nâng cao → Tiếp tục (ứng dụng của
                chính bạn).
              </p>
            </div>
          )}
          {cfg.platform === "youtube" ? (
            <ul className="mt-3 list-disc space-y-0.5 pl-5 text-xs text-slate-500">
              <li>
                Dự án Google Cloud chưa qua kiểm duyệt: YouTube buộc video tải lên ở chế độ{" "}
                <b>Riêng tư</b>.
              </li>
              <li>Hạn mức mặc định 10.000 đơn vị/ngày ≈ 6 video tải lên mỗi ngày.</li>
            </ul>
          ) : (
            <ul className="mt-3 list-disc space-y-0.5 pl-5 text-xs text-slate-500">
              <li>
                Ứng dụng Meta ở chế độ Development vẫn đăng được lên các Trang mà <b>bạn quản trị</b>.
              </li>
              <li>Video dọc 9:16 dài 3–90 giây có thể đăng dạng Reels.</li>
            </ul>
          )}
        </Card>
      ))}

      {fbFlow && (
        <FacebookFlowDialog
          flow={fbFlow}
          onClose={() => setFbFlow(null)}
          onDone={() => {
            setFbFlow(null);
            setNotice("Đã kết nối Trang Facebook.");
            load();
          }}
        />
      )}
    </div>
  );
}
