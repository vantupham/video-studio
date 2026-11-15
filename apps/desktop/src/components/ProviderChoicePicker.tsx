import { useEffect, useRef, useState } from "react";
import { useApi } from "../lib/backendContext";
import type { ProviderChoice, ProviderInstance } from "../lib/types";

/** Instance + model (LLM) or instance + voice (TTS) for one generation. Empty values
 * mean “use the default”. */
export function ProviderChoicePicker({
  kind,
  instances,
  value,
  onChange,
}: {
  kind: "llm" | "tts";
  instances: ProviderInstance[];
  value: ProviderChoice;
  onChange: (v: ProviderChoice) => void;
}) {
  const api = useApi();
  const list = instances.filter((i) => i.capability === kind);
  const defaultInst = list.find((i) => i.is_default);
  const effective = list.find((i) => i.id === value.instance_id) ?? defaultInst;
  const [loaded, setLoaded] = useState<{
    id: string;
    options: { id: string; label: string }[];
    error: string | null;
  } | null>(null);
  const [sample, setSample] = useState<{ id: string; url: string } | null>(null);
  const [sampleError, setSampleError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const audioRef = useRef<HTMLAudioElement>(null);
  const effectiveId = effective?.id;

  useEffect(() => {
    if (!effectiveId) return;
    let cancelled = false;
    const load: Promise<{ id: string; label: string }[]> =
      kind === "llm"
        ? api
            .providerModels(effectiveId)
            .then((r) => r.models.map((m) => ({ id: m.id, label: m.name ?? m.id })))
        : api.providerVoices(effectiveId, "vi").then((r) =>
            r.voices.map((v) => ({
              id: v.id,
              label: [v.name, v.gender].filter(Boolean).join(" · "),
            })),
          );
    load
      .then((options) => !cancelled && setLoaded({ id: effectiveId, options, error: null }))
      .catch(
        (e: Error) => !cancelled && setLoaded({ id: effectiveId, options: [], error: e.message }),
      );
    return () => {
      cancelled = true;
    };
  }, [api, kind, effectiveId]);

  useEffect(() => {
    if (sample) audioRef.current?.play().catch(() => undefined);
  }, [sample]);

  async function listen() {
    if (!effectiveId) return;
    setBusy(true);
    setSampleError(null);
    try {
      const res = await api.voiceSample(effectiveId, value.voice);
      setSample({
        id: effectiveId,
        url: `${api.mediaUrl(res.url)}&t=${Date.now()}`,
      });
    } catch (e) {
      setSampleError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  const sub = kind === "llm" ? "model" : "voice";
  const current = loaded && loaded.id === effectiveId ? loaded : null;
  const options = current?.options ?? [];
  const loadError = sampleError ?? current?.error ?? null;
  const sampleUrl = sample && sample.id === effectiveId ? sample.url : null;
  const subValue = (kind === "llm" ? value.model : value.voice) ?? "";
  const configured =
    kind === "llm"
      ? (effective?.config.model as string | undefined)
      : (Object.entries(effective?.config ?? {}).find(([k]) => k.includes("voice"))?.[1] as
          string | undefined);

  return (
    <div className="grid gap-3 sm:grid-cols-2">
      <div className="flex flex-col gap-1">
        <label htmlFor={`gen-${kind}`} className="text-sm font-medium">
          {kind === "llm" ? "Nhà cung cấp LLM" : "Nhà cung cấp giọng đọc"}
        </label>
        <select
          id={`gen-${kind}`}
          className="input"
          value={value.instance_id ?? ""}
          onChange={(e) =>
            onChange({
              instance_id: e.target.value || null,
              model: null,
              voice: null,
            })
          }
        >
          <option value="">Mặc định{defaultInst ? ` (${defaultInst.name})` : ""}</option>
          {list.map((i) => (
            <option key={i.id} value={i.id}>
              {i.name} — {i.type_name}
            </option>
          ))}
        </select>
      </div>
      <div className="flex flex-col gap-1">
        <label htmlFor={`gen-${kind}-${sub}`} className="text-sm font-medium">
          {kind === "llm" ? "Model" : "Giọng đọc"}
        </label>
        <div className="flex gap-2">
          {options.length > 0 ? (
            <select
              id={`gen-${kind}-${sub}`}
              className="input min-w-0 flex-1"
              value={subValue}
              onChange={(e) => onChange({ ...value, [sub]: e.target.value || null })}
            >
              <option value="">Theo cấu hình{configured ? ` (${configured})` : ""}</option>
              {subValue && !options.some((o) => o.id === subValue) && (
                <option value={subValue}>{subValue}</option>
              )}
              {options.map((o) => (
                <option key={o.id} value={o.id}>
                  {o.label}
                </option>
              ))}
            </select>
          ) : (
            <input
              id={`gen-${kind}-${sub}`}
              className="input min-w-0 flex-1"
              placeholder={configured ? `Theo cấu hình (${configured})` : "Theo cấu hình"}
              value={subValue}
              onChange={(e) => onChange({ ...value, [sub]: e.target.value || null })}
            />
          )}
          {kind === "tts" && (
            <button
              type="button"
              onClick={listen}
              disabled={busy || !effectiveId}
              className="rounded-lg border border-slate-300 px-2.5 text-xs whitespace-nowrap text-slate-700 hover:bg-slate-50 disabled:text-slate-400"
            >
              {busy ? "…" : "▶ Nghe thử"}
            </button>
          )}
        </div>
        {loadError && <p className="text-xs text-amber-700">{loadError}</p>}
        {sampleUrl && <audio ref={audioRef} src={sampleUrl} controls className="mt-1 h-8 w-full" />}
      </div>
    </div>
  );
}
