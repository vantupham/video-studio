import { useCallback, useEffect, useMemo, useState } from "react";
import { Link } from "react-router";
import { ApiError } from "../lib/api";
import { useApi, useAppEvents } from "../lib/backendContext";
import { openExternal } from "../lib/desktop";
import { formatDate, toLocalInput } from "../lib/format";
import { PLATFORM_LABELS } from "../lib/labels";
import type {
  MetadataSuggestion,
  Platform,
  PlatformMeta,
  Publication,
  PublishCheck,
  PublisherInfo,
  PublishIssue,
  PublishMeta,
  ScheduleInput,
  SocialAccount,
  VideoDetail,
} from "../lib/types";
import { PlatformIcon, PublicationBadge } from "./badges";
import { SchemaForm } from "./SchemaForm";
import { Button, Card, ErrorBox, Modal } from "./ui";

type MetaTab = "common" | Platform;

const ISSUE_STYLE: Record<PublishIssue["level"], string> = {
  error: "border-rose-200 bg-rose-50 text-rose-800",
  warning: "border-amber-200 bg-amber-50 text-amber-800",
  info: "border-sky-200 bg-sky-50 text-sky-800",
};
const ISSUE_ICON: Record<PublishIssue["level"], string> = { error: "⛔", warning: "⚠️", info: "ℹ️" };


function defaultScheduleTime(): string {
  const d = new Date(Date.now() + 60 * 60 * 1000);
  d.setMinutes(0, 0, 0);
  return toLocalInput(d);
}

const splitList = (s: string, sep: RegExp) =>
  s
    .split(sep)
    .map((x) => x.trim())
    .filter(Boolean);

function TextField({
  label,
  value,
  onChange,
  placeholder,
  multiline,
  hint,
  id,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  placeholder?: string;
  multiline?: boolean;
  hint?: string;
  id: string;
}) {
  return (
    <div className="flex flex-col gap-1">
      <label htmlFor={id} className="text-sm font-medium">
        {label}
      </label>
      {multiline ? (
        <textarea
          id={id}
          className="input min-h-28"
          value={value}
          placeholder={placeholder}
          onChange={(e) => onChange(e.target.value)}
        />
      ) : (
        <input
          id={id}
          className="input"
          value={value}
          placeholder={placeholder}
          onChange={(e) => onChange(e.target.value)}
        />
      )}
      {hint && <p className="text-xs text-slate-500">{hint}</p>}
    </div>
  );
}

function MetaEditor({
  meta,
  setMeta,
  publishers,
  videoId,
  videoTitle,
}: {
  meta: PublishMeta;
  setMeta: (m: PublishMeta) => void;
  publishers: PublisherInfo[];
  videoId: string;
  videoTitle: string;
}) {
  const api = useApi();
  const [tab, setTab] = useState<MetaTab>("common");
  const [suggestion, setSuggestion] = useState<MetadataSuggestion | null>(null);
  const [suggesting, setSuggesting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const tagsText = (tab === "common" ? meta.common.tags : meta[tab].tags).join(", ");

  const current: PlatformMeta | PublishMeta["common"] = tab === "common" ? meta.common : meta[tab];
  const update = (patch: Partial<PlatformMeta>) => {
    if (tab === "common") setMeta({ ...meta, common: { ...meta.common, ...patch } });
    else setMeta({ ...meta, [tab]: { ...meta[tab], ...patch } });
  };
  const publisher =
    tab === "common"
      ? undefined
      : (publishers.find((p) => p.platform === tab && !p.mock) ??
        publishers.find((p) => p.platform === tab));
  const c = publisher?.constraints;

  async function suggest() {
    setSuggesting(true);
    setError(null);
    try {
      setSuggestion(await api.suggestMetadata(videoId, tab === "common" ? "youtube" : tab));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSuggesting(false);
    }
  }

  const fallback = (field: "title" | "description") =>
    tab === "common"
      ? field === "title"
        ? videoTitle
        : ""
      : meta.common[field] || (field === "title" ? videoTitle : "");

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-1 border-b border-slate-200">
        {(["common", "youtube", "facebook"] as MetaTab[]).map((t) => (
          <button
            key={t}
            type="button"
            onClick={() => {
              setTab(t);
              setSuggestion(null);
            }}
            className={`-mb-px border-b-2 px-3 py-2 text-sm font-medium ${
              tab === t
                ? "border-indigo-600 text-indigo-700"
                : "border-transparent text-slate-500 hover:text-slate-800"
            }`}
          >
            {t === "common" ? "Chung" : PLATFORM_LABELS[t]}
          </button>
        ))}
        <Button
          variant="secondary"
          className="mb-1 ml-auto py-1 text-xs"
          onClick={suggest}
          disabled={suggesting}
          data-testid="suggest-metadata"
        >
          {suggesting ? "Đang gợi ý…" : "✨ Gợi ý bằng AI"}
        </Button>
      </div>
      {tab !== "common" && (
        <p className="text-xs text-slate-500">
          Để trống để dùng nội dung ở tab “Chung”. Hashtag chung được thêm vào cuối mô tả.
        </p>
      )}
      <ErrorBox>{error}</ErrorBox>
      {suggestion && (
        <div className="space-y-2 rounded-lg border border-indigo-200 bg-indigo-50 p-3 text-sm">
          <div className="flex items-center justify-between">
            <p className="font-medium text-indigo-900">Gợi ý từ AI — bấm để áp dụng</p>
            <button
              type="button"
              className="text-xs text-indigo-700 hover:underline"
              onClick={() => setSuggestion(null)}
            >
              Đóng
            </button>
          </div>
          <div className="flex flex-col gap-1">
            {suggestion.titles.map((t) => (
              <button
                key={t}
                type="button"
                className="rounded border border-indigo-200 bg-white px-2 py-1 text-left hover:border-indigo-400"
                onClick={() => update({ title: t })}
              >
                {t}
              </button>
            ))}
          </div>
          <div className="flex flex-wrap gap-2">
            <Button
              variant="secondary"
              className="py-1 text-xs"
              onClick={() => update({ description: suggestion.description })}
            >
              Dùng mô tả
            </Button>
            <Button
              variant="secondary"
              className="py-1 text-xs"
              onClick={() => update({ tags: suggestion.tags })}
            >
              Dùng thẻ ({suggestion.tags.length})
            </Button>
            <Button
              variant="secondary"
              className="py-1 text-xs"
              onClick={() =>
                setMeta({ ...meta, common: { ...meta.common, hashtags: suggestion.hashtags } })
              }
            >
              Dùng hashtag: {suggestion.hashtags.join(" ")}
            </Button>
            <Button
              className="py-1 text-xs"
              onClick={() => {
                const base = {
                  title: suggestion.titles[0] ?? "",
                  description: suggestion.description,
                  tags: suggestion.tags,
                };
                if (tab === "common")
                  setMeta({
                    ...meta,
                    common: { ...meta.common, ...base, hashtags: suggestion.hashtags },
                  });
                else
                  setMeta({
                    ...meta,
                    [tab]: { ...meta[tab], ...base },
                    common: { ...meta.common, hashtags: suggestion.hashtags },
                  });
                setSuggestion(null);
              }}
            >
              Áp dụng tất cả
            </Button>
          </div>
        </div>
      )}
      <TextField
        id={`meta-title-${tab}`}
        label="Tiêu đề"
        value={current.title}
        placeholder={fallback("title")}
        onChange={(title) => update({ title })}
        hint={
          c?.max_title_len
            ? `${current.title.length || fallback("title").length}/${c.max_title_len} ký tự`
            : undefined
        }
      />
      <TextField
        id={`meta-desc-${tab}`}
        label="Mô tả"
        multiline
        value={current.description}
        placeholder={fallback("description")}
        onChange={(description) => update({ description })}
        hint={c?.max_description_len ? `Tối đa ${c.max_description_len} ký tự` : undefined}
      />
      <TagsField
        key={tab}
        id={`meta-tags-${tab}`}
        initial={tagsText}
        onCommit={(tags) => update({ tags })}
        hint={c?.max_tags_chars ? `Tổng cộng tối đa ${c.max_tags_chars} ký tự (YouTube)` : undefined}
      />
      {tab === "common" && (
        <TagsField
          key="hashtags"
          id="meta-hashtags"
          label="Hashtag"
          initial={meta.common.hashtags.join(" ")}
          separator={/[\s,]+/}
          placeholder="#QuangHop #SinhHoc"
          onCommit={(hashtags) =>
            setMeta({
              ...meta,
              common: {
                ...meta.common,
                hashtags: hashtags.map((h) => (h.startsWith("#") ? h : `#${h}`)),
              },
            })
          }
        />
      )}
      {tab !== "common" && publisher && (
        <div className="rounded-lg bg-slate-50 p-3">
          <p className="mb-2 text-sm font-medium">Tuỳ chọn {PLATFORM_LABELS[tab]}</p>
          <SchemaForm
            idPrefix={`opt-${tab}`}
            schema={publisher.metadata_schema}
            value={{ ...defaultsOf(publisher), ...meta[tab].options }}
            onChange={(options) => update({ options })}
          />
          {c?.notes && c.notes.length > 0 && (
            <ul className="mt-2 list-disc space-y-0.5 pl-5 text-xs text-slate-500">
              {c.notes.map((n) => (
                <li key={n}>{n}</li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}

function defaultsOf(p: PublisherInfo): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const [k, prop] of Object.entries(p.metadata_schema.properties ?? {})) {
    if (prop.default !== undefined) out[k] = prop.default;
  }
  return out;
}

function TagsField({
  id,
  initial,
  onCommit,
  label = "Thẻ (tags)",
  separator = /,/,
  placeholder = "quang hợp, sinh học, lớp 6",
  hint,
}: {
  id: string;
  initial: string;
  onCommit: (list: string[]) => void;
  label?: string;
  separator?: RegExp;
  placeholder?: string;
  hint?: string;
}) {
  const [text, setText] = useState(initial);
  const [lastInitial, setLastInitial] = useState(initial);
  if (initial !== lastInitial) {
    setLastInitial(initial);
    if (splitList(text, separator).join("|") !== splitList(initial, separator).join("|")) {
      setText(initial);
    }
  }
  return (
    <TextField
      id={id}
      label={label}
      value={text}
      placeholder={placeholder}
      hint={hint}
      onChange={(v) => {
        setText(v);
        onCommit(splitList(v, separator));
      }}
    />
  );
}

function ThumbnailEditor({
  video,
  meta,
  setMeta,
  onSaved,
}: {
  video: VideoDetail;
  meta: PublishMeta;
  setMeta: (m: PublishMeta) => void;
  onSaved: (thumb: PublishMeta["thumbnail"]) => void;
}) {
  const api = useApi();
  const [url, setUrl] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const t = meta.thumbnail;
  const hasRender = Boolean(video.approved_render_id || video.latest_render_id);

  const generate = useCallback(
    async (patch: Partial<PublishMeta["thumbnail"]> = {}) => {
      if (!hasRender) return;
      setBusy(true);
      setError(null);
      try {
        const res = await api.thumbnail(video.id, patch);
        setUrl(res.url ? `${api.mediaUrl(res.url)}&t=${Date.now()}` : null);
        onSaved(res.thumbnail);
      } catch (e) {
        setError((e as Error).message);
      } finally {
        setBusy(false);
      }
    },
    [api, video.id, hasRender, onSaved],
  );

  useEffect(() => {
    // Generate once when the panel opens (deferred: the generator sets state).
    const timer = window.setTimeout(() => void generate(), 0);
    return () => window.clearTimeout(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [video.id]);

  const set = (patch: Partial<PublishMeta["thumbnail"]>) =>
    setMeta({ ...meta, thumbnail: { ...t, ...patch } });

  async function upload(file: File) {
    setBusy(true);
    setError(null);
    try {
      const res = await api.uploadThumbnail(video.id, file);
      setUrl(res.url ? `${api.mediaUrl(res.url)}&t=${Date.now()}` : null);
      onSaved(res.thumbnail);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="grid gap-4 md:grid-cols-[1fr_240px]">
      <div className="space-y-3">
        <div className="flex gap-3 text-sm">
          {(
            [
              ["auto", "Tự động từ video"],
              ["custom", "Ảnh của tôi"],
              ["none", "Không gửi"],
            ] as const
          ).map(([mode, label]) => (
            <label key={mode} className="flex items-center gap-1.5">
              <input
                type="radio"
                name="thumb-mode"
                className="accent-indigo-600"
                checked={t.mode === mode}
                disabled={mode === "custom" && !t.custom_path}
                onChange={() => {
                  set({ mode });
                  void generate({ mode });
                }}
              />
              {label}
            </label>
          ))}
        </div>
        {t.mode === "auto" && (
          <>
            <label className="flex flex-col gap-1 text-sm">
              <span>
                Khung hình tại giây{" "}
                <span className="font-mono">{t.frame_s ?? "tự chọn"}</span>
              </span>
              <input
                type="range"
                min={0}
                max={Math.max(1, Math.floor(video.duration_s ?? 10))}
                step={0.5}
                value={t.frame_s ?? 0}
                onChange={(e) => set({ frame_s: Number(e.target.value) })}
                aria-label="Thời điểm khung hình"
              />
            </label>
            <label className="flex items-center gap-2 text-sm">
              <input
                type="checkbox"
                className="accent-indigo-600"
                checked={t.show_title}
                onChange={(e) => set({ show_title: e.target.checked })}
              />
              Chèn tiêu đề lên ảnh
            </label>
            {t.show_title && (
              <input
                className="input"
                aria-label="Chữ trên ảnh thu nhỏ"
                placeholder={`Mặc định: ${video.title}`}
                value={t.text}
                onChange={(e) => set({ text: e.target.value })}
              />
            )}
          </>
        )}
        <div className="flex flex-wrap gap-2">
          {t.mode === "auto" && (
            <Button
              variant="secondary"
              className="text-xs"
              disabled={busy || !hasRender}
              onClick={() =>
                generate({ mode: "auto", frame_s: t.frame_s, show_title: t.show_title, text: t.text })
              }
            >
              {busy ? "Đang tạo…" : "Tạo lại ảnh"}
            </Button>
          )}
          <label className="inline-flex cursor-pointer items-center rounded-lg border border-slate-300 px-3 py-1.5 text-xs font-medium hover:bg-slate-50">
            Tải ảnh lên…
            <input
              type="file"
              accept=".png,.jpg,.jpeg,.webp"
              className="hidden"
              onChange={(e) => e.target.files?.[0] && upload(e.target.files[0])}
            />
          </label>
        </div>
        <ErrorBox>{error}</ErrorBox>
      </div>
      <div className="grid place-items-center rounded-lg bg-slate-100 p-2">
        {t.mode === "none" ? (
          <p className="text-xs text-slate-500">Nền tảng sẽ tự chọn ảnh.</p>
        ) : url ? (
          <img src={url} alt="Ảnh thu nhỏ" className="max-h-48 rounded" data-testid="thumbnail-preview" />
        ) : (
          <p className="text-xs text-slate-500">{hasRender ? "Đang tạo…" : "Chưa có bản dựng"}</p>
        )}
      </div>
    </div>
  );
}

function IssueList({
  issues,
  onFix,
}: {
  issues: PublishIssue[];
  onFix?: (issue: PublishIssue) => void;
}) {
  if (issues.length === 0)
    return <p className="text-xs text-emerald-700">✓ Đạt mọi yêu cầu của nền tảng.</p>;
  return (
    <ul className="space-y-1">
      {issues.map((i, n) => (
        <li
          key={`${i.code}-${n}`}
          className={`flex items-start gap-2 rounded border px-2 py-1.5 text-xs ${ISSUE_STYLE[i.level]}`}
          data-testid={`issue-${i.code}`}
        >
          <span>{ISSUE_ICON[i.level]}</span>
          <span className="flex-1">{i.message}</span>
          {i.fix && onFix && (
            <Button className="px-2 py-0.5 text-xs whitespace-nowrap" onClick={() => onFix(i)}>
              {i.fix.action === "rerender_aspect"
                ? `Dựng lại tỉ lệ ${i.fix.aspect}`
                : `Dùng bản ${i.fix.aspect}`}
            </Button>
          )}
        </li>
      ))}
    </ul>
  );
}

function RescheduleDialog({
  pub,
  onClose,
  onDone,
}: {
  pub: Publication;
  onClose: () => void;
  onDone: () => void;
}) {
  const api = useApi();
  const [at, setAt] = useState(
    pub.scheduled_at ? toLocalInput(new Date(pub.scheduled_at)) : defaultScheduleTime(),
  );
  const [error, setError] = useState<string | null>(null);
  return (
    <Modal
      title="Đổi lịch đăng"
      onClose={onClose}
      footer={
        <>
          <Button variant="secondary" onClick={onClose}>
            Huỷ
          </Button>
          <Button
            onClick={async () => {
              try {
                await api.reschedulePublication(pub.id, new Date(at).toISOString());
                onDone();
              } catch (e) {
                setError((e as Error).message);
              }
            }}
          >
            Lưu lịch mới
          </Button>
        </>
      }
    >
      <div className="space-y-3">
        <input
          type="datetime-local"
          className="input"
          aria-label="Thời điểm đăng mới"
          value={at}
          onChange={(e) => setAt(e.target.value)}
        />
        <ErrorBox>{error}</ErrorBox>
      </div>
    </Modal>
  );
}

export function PublicationsList({
  pubs,
  onChanged,
  showVideo = false,
}: {
  pubs: Publication[];
  onChanged: () => void;
  showVideo?: boolean;
}) {
  const api = useApi();
  const [error, setError] = useState<string | null>(null);
  const [rescheduling, setRescheduling] = useState<Publication | null>(null);
  const act = async (fn: () => Promise<unknown>) => {
    setError(null);
    try {
      await fn();
      onChanged();
    } catch (e) {
      setError((e as Error).message);
    }
  };
  if (pubs.length === 0) return <p className="text-sm text-slate-500">Chưa có bài đăng nào.</p>;
  return (
    <div className="space-y-2">
      <ErrorBox>{error}</ErrorBox>
      <ul className="divide-y divide-slate-100">
        {pubs.map((p) => {
          const active = ["queued", "uploading", "processing", "retry_wait"].includes(p.status);
          const url = p.remote_url
            ? p.remote_url.startsWith("http")
              ? p.remote_url
              : api.mediaUrl(p.remote_url)
            : null;
          return (
            <li key={p.id} className="space-y-1.5 py-3" data-testid="publication">
              <div className="flex flex-wrap items-center gap-2">
                <PlatformIcon platform={p.platform} />
                <span className="text-sm font-medium">{p.account_name ?? "—"}</span>
                {p.mock && (
                  <span className="rounded bg-slate-800 px-1.5 text-[10px] font-semibold text-white">
                    GIẢ LẬP
                  </span>
                )}
                <PublicationBadge status={p.status} />
                {showVideo && (
                  <Link to={`/videos/${p.storyboard_id}`} className="text-xs text-indigo-700 hover:underline">
                    {p.video_title}
                  </Link>
                )}
                <span className="ml-auto text-xs text-slate-500">
                  {p.schedule_mode === "now"
                    ? `Tạo ${formatDate(p.created_at)}`
                    : `Hẹn ${formatDate(p.scheduled_at)}${p.schedule_mode === "platform" ? " (trên nền tảng)" : ""}`}
                </span>
              </div>
              <p className="truncate text-xs text-slate-600">
                {String(p.meta.title ?? "")} · {String(p.meta.aspect ?? "")}
                {p.published_at && ` · đăng lúc ${formatDate(p.published_at)}`}
              </p>
              {(p.status === "uploading" || p.status === "processing") && (
                <div className="h-1.5 overflow-hidden rounded bg-slate-200">
                  <div
                    className="h-full bg-indigo-500 transition-all"
                    style={{ width: `${Math.round(p.progress * 100)}%` }}
                  />
                </div>
              )}
              {p.error && <p className="text-xs text-rose-700">{p.error}</p>}
              {p.status === "retry_wait" && p.next_attempt_at && (
                <p className="text-xs text-amber-700">
                  Sẽ thử lại lúc {formatDate(p.next_attempt_at)} (lần {p.attempts})
                  {p.resumable && " — tiếp tục từ phần đã tải lên"}
                </p>
              )}
              {p.warnings.map((w) => (
                <p key={w} className="text-xs text-amber-700">
                  ⚠️ {w}
                </p>
              ))}
              <div className="flex flex-wrap gap-2">
                {url && (
                  <Button
                    variant="secondary"
                    className="py-1 text-xs"
                    onClick={() => openExternal(url)}
                    data-testid="open-post"
                  >
                    Mở bài đăng ↗
                  </Button>
                )}
                {(p.status === "failed" || p.status === "missed") && (
                  <Button className="py-1 text-xs" onClick={() => act(() => api.retryPublication(p.id))}>
                    {p.status === "missed" ? "Đăng ngay" : "Thử lại"}
                  </Button>
                )}
                {(p.status === "scheduled" || p.status === "missed" || p.status === "failed") && (
                  <Button variant="secondary" className="py-1 text-xs" onClick={() => setRescheduling(p)}>
                    Đổi lịch
                  </Button>
                )}
                {(active || p.status === "scheduled" || p.status === "missed") && (
                  <Button
                    variant="ghost"
                    className="py-1 text-xs text-rose-700"
                    onClick={() => act(() => api.cancelPublication(p.id))}
                  >
                    Huỷ
                  </Button>
                )}
              </div>
            </li>
          );
        })}
      </ul>
      {rescheduling && (
        <RescheduleDialog
          pub={rescheduling}
          onClose={() => setRescheduling(null)}
          onDone={() => {
            setRescheduling(null);
            onChanged();
          }}
        />
      )}
    </div>
  );
}

export function PublishPanel({
  video,
  onRerender,
}: {
  video: VideoDetail;
  onRerender: (aspect: string) => void;
}) {
  const api = useApi();
  const [meta, setMeta] = useState<PublishMeta>(video.publish_meta);
  const [savedMeta, setSavedMeta] = useState(JSON.stringify(video.publish_meta));
  const [accounts, setAccounts] = useState<SocialAccount[]>([]);
  const [publishers, setPublishers] = useState<PublisherInfo[]>([]);
  const [selected, setSelected] = useState<string[]>([]);
  const [schedule, setSchedule] = useState<ScheduleInput["mode"]>("now");
  const [at, setAt] = useState(defaultScheduleTime());
  const [checks, setChecks] = useState<Record<string, PublishCheck>>({});
  const [issues, setIssues] = useState<Record<string, PublishIssue[]>>({});
  const [renderOverride, setRenderOverride] = useState<Record<string, string>>({});
  const [allowRepost, setAllowRepost] = useState(false);
  const [pubs, setPubs] = useState<Publication[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const approved = ["approved", "scheduled", "publishing", "published", "failed"].includes(
    video.status,
  );
  const dirty = JSON.stringify(meta) !== savedMeta;

  const loadPubs = useCallback(
    () => api.listPublications({ videoId: video.id }).then(setPubs).catch(() => undefined),
    [api, video.id],
  );

  useEffect(() => {
    api
      .socialAccounts()
      .then((list) => {
        setAccounts(list);
        setSelected((sel) => (sel.length ? sel : list.filter((a) => a.status === "connected").map((a) => a.id)));
      })
      .catch(() => undefined);
    api.publishers().then(setPublishers).catch(() => undefined);
    void loadPubs();
  }, [api, loadPubs]);

  useAppEvents((e) => {
    if (e.type === "publication" && e.storyboard_id === video.id) {
      setPubs((prev) => {
        const i = prev.findIndex((p) => p.id === e.id);
        const { type: _t, ...pub } = e;
        void _t;
        if (i < 0) return [pub as Publication, ...prev];
        const next = [...prev];
        next[i] = { ...next[i], ...pub };
        return next;
      });
    } else if (e.type === "publication.progress") {
      setPubs((prev) => prev.map((p) => (p.id === e.id ? { ...p, progress: e.progress } : p)));
    }
  });

  const scheduleInput = useMemo<ScheduleInput>(
    () => (schedule === "now" ? { mode: "now" } : { mode: schedule, at: new Date(at).toISOString() }),
    [schedule, at],
  );

  async function saveMeta(): Promise<boolean> {
    if (!dirty) return true;
    try {
      const saved = await api.savePublishMeta(video.id, meta);
      setMeta(saved);
      setSavedMeta(JSON.stringify(saved));
      return true;
    } catch (e) {
      setError((e as Error).message);
      return false;
    }
  }

  const onThumbSaved = useCallback((thumbnail: PublishMeta["thumbnail"]) => {
    setMeta((m) => ({ ...m, thumbnail }));
    setSavedMeta((s) => {
      const parsed = JSON.parse(s) as PublishMeta;
      return JSON.stringify({ ...parsed, thumbnail });
    });
  }, []);

  async function validate(): Promise<Record<string, PublishCheck> | null> {
    setError(null);
    setNotice(null);
    if (!(await saveMeta())) return null;
    const out: Record<string, PublishCheck> = {};
    try {
      for (const id of selected) {
        out[id] = await api.validatePublication(video.id, {
          account_id: id,
          schedule: scheduleInput,
          render_id: renderOverride[id],
        });
      }
    } catch (e) {
      setError((e as Error).message);
      return null;
    }
    setChecks(out);
    setIssues(Object.fromEntries(Object.entries(out).map(([k, v]) => [k, v.issues])));
    return out;
  }

  async function publish() {
    setBusy(true);
    try {
      const result = await validate();
      if (!result) return;
      const created = await api.createPublications(
        video.id,
        selected.map((id) => ({
          account_id: id,
          schedule: scheduleInput,
          render_id: renderOverride[id],
          allow_repost: allowRepost,
        })),
      );
      setNotice(
        schedule === "now"
          ? `Đã đưa ${created.length} bài vào hàng đợi đăng.`
          : `Đã lên lịch ${created.length} bài lúc ${new Date(at).toLocaleString("vi-VN")}.`,
      );
      setChecks({});
      setIssues({});
      await loadPubs();
    } catch (e) {
      if (e instanceof ApiError && e.detail && typeof e.detail === "object" && "issues" in e.detail) {
        setIssues((e.detail as { issues: Record<string, PublishIssue[]> }).issues);
      }
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  const fix = (accountId: string, issue: PublishIssue) => {
    if (!issue.fix) return;
    if (issue.fix.action === "rerender_aspect") {
      onRerender(issue.fix.aspect);
    } else {
      const variant = video.renders.find(
        (r) => r.aspect === issue.fix!.aspect && r.approved_content && r.status === "done",
      );
      if (variant) setRenderOverride((o) => ({ ...o, [accountId]: variant.id }));
      void validate();
    }
  };

  const hasDuplicate = Object.values(issues).some((list) => list.some((i) => i.code === "duplicate"));
  const connected = accounts.filter((a) => a.status === "connected" || a.status === "expired");

  return (
    <div className="space-y-6">
      {!approved && (
        <div className="rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-900">
          Video cần được <b>duyệt</b> trước khi đăng. Bạn vẫn có thể soạn tiêu đề, mô tả và ảnh thu
          nhỏ trước.
        </div>
      )}

      <Card
        title="Nội dung bài đăng"
        actions={
          <Button
            variant="secondary"
            className="py-1 text-xs"
            disabled={!dirty}
            onClick={async () => {
              if (await saveMeta()) setNotice("Đã lưu nội dung bài đăng");
            }}
          >
            {dirty ? "Lưu" : "Đã lưu"}
          </Button>
        }
      >
        <MetaEditor
          meta={meta}
          setMeta={setMeta}
          publishers={publishers}
          videoId={video.id}
          videoTitle={video.title}
        />
      </Card>

      <Card title="Ảnh thu nhỏ">
        <ThumbnailEditor video={video} meta={meta} setMeta={setMeta} onSaved={onThumbSaved} />
      </Card>

      <Card title="Đăng lên">
        {connected.length === 0 ? (
          <p className="text-sm text-slate-600">
            Chưa kết nối tài khoản nào.{" "}
            <Link to="/settings?tab=social" className="text-indigo-700 hover:underline">
              Kết nối YouTube / Facebook trong Cài đặt → Tài khoản mạng xã hội
            </Link>
            .
          </p>
        ) : (
          <div className="space-y-4">
            <ul className="space-y-2">
              {connected.map((a) => (
                <li key={a.id} className="rounded-lg border border-slate-200 p-3">
                  <label className="flex items-center gap-2 text-sm">
                    <input
                      type="checkbox"
                      className="size-4 accent-indigo-600"
                      checked={selected.includes(a.id)}
                      onChange={(e) =>
                        setSelected((s) =>
                          e.target.checked ? [...s, a.id] : s.filter((x) => x !== a.id),
                        )
                      }
                      data-testid={`account-${a.publisher_id}`}
                    />
                    <PlatformIcon platform={a.platform} />
                    <span className="font-medium">{a.name}</span>
                    {a.mock && (
                      <span className="rounded bg-slate-800 px-1.5 text-[10px] font-semibold text-white">
                        GIẢ LẬP
                      </span>
                    )}
                    {checks[a.id]?.render && (
                      <span className="ml-auto text-xs text-slate-500">
                        Bản dựng {checks[a.id].render!.aspect}
                      </span>
                    )}
                  </label>
                  {a.warning && <p className="mt-1 text-xs text-amber-700">⚠️ {a.warning}</p>}
                  {selected.includes(a.id) && issues[a.id] && (
                    <div className="mt-2">
                      <IssueList issues={issues[a.id]} onFix={(i) => fix(a.id, i)} />
                    </div>
                  )}
                </li>
              ))}
            </ul>

            <fieldset className="space-y-2">
              <legend className="text-sm font-medium">Thời điểm đăng</legend>
              <div className="flex flex-wrap gap-4 text-sm">
                {(
                  [
                    ["now", "Đăng ngay"],
                    ["local", "Hẹn giờ (ứng dụng tự đăng)"],
                    ["platform", "Hẹn giờ trên nền tảng"],
                  ] as const
                ).map(([mode, label]) => (
                  <label key={mode} className="flex items-center gap-1.5">
                    <input
                      type="radio"
                      name="schedule-mode"
                      className="accent-indigo-600"
                      checked={schedule === mode}
                      onChange={() => setSchedule(mode)}
                    />
                    {label}
                  </label>
                ))}
              </div>
              {schedule !== "now" && (
                <input
                  type="datetime-local"
                  className="input w-auto"
                  aria-label="Thời điểm đăng"
                  value={at}
                  onChange={(e) => setAt(e.target.value)}
                />
              )}
              <p className="text-xs text-slate-500">
                {schedule === "local"
                  ? "Ứng dụng cần đang chạy (có thể thu nhỏ xuống khay hệ thống) vào giờ hẹn. Nếu máy tắt, bạn sẽ được hỏi khi mở lại."
                  : schedule === "platform"
                    ? "Tải video lên ngay và để YouTube / Facebook tự công khai vào giờ hẹn (Facebook: 10 phút – 75 ngày)."
                    : "Bắt đầu tải lên ngay khi bấm Đăng."}
              </p>
            </fieldset>

            {hasDuplicate && (
              <label className="flex items-center gap-2 text-sm text-amber-800">
                <input
                  type="checkbox"
                  className="accent-amber-600"
                  checked={allowRepost}
                  onChange={(e) => setAllowRepost(e.target.checked)}
                />
                Tôi muốn đăng lại thành một bài mới
              </label>
            )}

            <ErrorBox>{error}</ErrorBox>
            {notice && <p className="text-sm text-emerald-700">✓ {notice}</p>}
            <div className="flex gap-2">
              <Button variant="secondary" disabled={busy || selected.length === 0} onClick={validate}>
                Kiểm tra
              </Button>
              <Button
                disabled={busy || selected.length === 0 || !approved}
                onClick={publish}
                data-testid="publish-button"
              >
                {busy ? "Đang xử lý…" : schedule === "now" ? "Đăng" : "Lên lịch đăng"}
              </Button>
            </div>
          </div>
        )}
      </Card>

      <Card title={`Bài đăng (${pubs.length})`}>
        <PublicationsList pubs={pubs} onChanged={loadPubs} />
      </Card>
    </div>
  );
}
