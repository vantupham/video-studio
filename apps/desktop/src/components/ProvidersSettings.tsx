import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useApi } from "../lib/backendContext";
import { CAPABILITY_INFO } from "../lib/labels";
import { defaultsFromSchema, missingRequired, toPayload, type FormValues } from "../lib/schemaForm";
import type {
  Capability,
  ProviderInstance,
  ProviderType,
  TestResult,
  VoiceInfo,
} from "../lib/types";
import { SchemaForm, type FieldOption } from "./SchemaForm";
import { Button, Card, ErrorBox } from "./ui";

const CAPABILITY_ORDER: Capability[] = ["llm", "tts", "alignment", "media"];

function Badge({
  tone,
  children,
}: {
  tone: "indigo" | "amber" | "slate" | "rose";
  children: string;
}) {
  const tones = {
    indigo: "bg-indigo-100 text-indigo-800",
    amber: "bg-amber-100 text-amber-800",
    slate: "bg-slate-100 text-slate-700",
    rose: "bg-rose-100 text-rose-800",
  };
  return (
    <span className={`rounded-full px-2 py-0.5 text-[11px] font-medium ${tones[tone]}`}>
      {children}
    </span>
  );
}

function TestBadge({ result }: { result: TestResult | "pending" | undefined }) {
  if (!result) return null;
  if (result === "pending") {
    return <span className="text-xs text-slate-500">Đang kiểm tra…</span>;
  }
  return (
    <span
      role="status"
      className={`text-xs ${result.ok ? "text-emerald-700" : "text-rose-700"}`}
      data-testid="provider-test-result"
    >
      {result.ok ? "✓ " : "✗ "}
      {result.message}
    </span>
  );
}

function voiceLabel(v: VoiceInfo): string {
  const extra = [v.gender, v.lang].filter(Boolean).join(", ");
  return extra ? `${v.name} (${extra})` : v.name;
}

function voiceField(type: ProviderType | undefined): string | null {
  const entry = Object.entries(type?.config_schema.properties ?? {}).find(
    ([, p]) => p["x-source"] === "voices",
  );
  return entry ? entry[0] : null;
}

/** Voice list + “Nghe thử” for a saved TTS instance. */
export function VoicePicker({
  instance,
  type,
  onSaved,
}: {
  instance: ProviderInstance;
  type: ProviderType | undefined;
  onSaved: () => void;
}) {
  const api = useApi();
  const field = voiceField(type);
  const current = field ? String(instance.config[field] ?? "") : "";
  const [voices, setVoices] = useState<VoiceInfo[] | null>(null);
  const [voice, setVoice] = useState(current);
  const [error, setError] = useState<string | null>(null);
  const [sample, setSample] = useState<{ url: string; key: number } | null>(null);
  const [busy, setBusy] = useState(false);
  const audioRef = useRef<HTMLAudioElement>(null);

  async function load() {
    setError(null);
    try {
      const res = await api.providerVoices(instance.id, "vi");
      setVoices(res.voices);
      if (!voice && res.default_voice) setVoice(res.default_voice);
    } catch (e) {
      setError((e as Error).message);
    }
  }

  async function listen() {
    setBusy(true);
    setError(null);
    try {
      const res = await api.voiceSample(instance.id, voice || null);
      setSample({ url: api.mediaUrl(res.url), key: Date.now() });
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  useEffect(() => {
    if (sample) audioRef.current?.play().catch(() => undefined);
  }, [sample]);

  async function save() {
    if (!field) return;
    try {
      await api.updateProvider(instance.id, { config: { [field]: voice } });
      onSaved();
    } catch (e) {
      setError((e as Error).message);
    }
  }

  return (
    <div className="mt-3 space-y-2 rounded-lg bg-slate-50 p-3" data-testid="voice-picker">
      <div className="flex flex-wrap items-center gap-2">
        <label htmlFor={`voice-${instance.id}`} className="text-xs font-medium text-slate-600">
          Giọng
        </label>
        {voices ? (
          <select
            id={`voice-${instance.id}`}
            className="input min-w-56 flex-1 py-1.5"
            value={voice}
            onChange={(e) => setVoice(e.target.value)}
          >
            {voice && !voices.some((v) => v.id === voice) && <option value={voice}>{voice}</option>}
            {voices.map((v) => (
              <option key={v.id} value={v.id}>
                {voiceLabel(v)}
              </option>
            ))}
          </select>
        ) : (
          <input
            id={`voice-${instance.id}`}
            className="input min-w-56 flex-1 py-1.5"
            value={voice}
            placeholder="Mặc định"
            onChange={(e) => setVoice(e.target.value)}
          />
        )}
        {!voices && (
          <Button variant="secondary" className="py-1.5 text-xs" onClick={load}>
            Tải danh sách giọng
          </Button>
        )}
        <Button variant="secondary" className="py-1.5 text-xs" onClick={listen} disabled={busy}>
          {busy ? "Đang tạo…" : "▶ Nghe thử"}
        </Button>
        {field && voice !== current && (
          <Button className="py-1.5 text-xs" onClick={save}>
            Lưu làm giọng mặc định
          </Button>
        )}
      </div>
      {sample && (
        <audio
          key={sample.key}
          ref={audioRef}
          src={sample.url}
          controls
          className="h-8 w-full"
          data-testid="voice-sample"
        />
      )}
      {error && <p className="text-xs text-rose-700">{error}</p>}
    </div>
  );
}

interface EditorState {
  capability: Capability;
  instance: ProviderInstance | null;
}

function ProviderEditor({
  state,
  types,
  onClose,
  onSaved,
}: {
  state: EditorState;
  types: ProviderType[];
  onClose: () => void;
  onSaved: () => void;
}) {
  const api = useApi();
  const editing = state.instance;
  const choices = types.filter((t) => t.capability === state.capability);
  const [typeId, setTypeId] = useState(editing?.type ?? choices[0]?.id ?? "");
  const type = types.find((t) => t.id === typeId);
  const [name, setName] = useState(editing?.name ?? "");
  const [values, setValues] = useState<FormValues>(() =>
    editing ? { ...editing.config } : defaultsFromSchema(type?.config_schema),
  );
  const [makeDefault, setMakeDefault] = useState(false);
  const [test, setTest] = useState<TestResult | "pending">();
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [options, setOptions] = useState<Partial<Record<"models" | "voices", FieldOption[]>>>({});

  function pickType(id: string) {
    setTypeId(id);
    setValues(defaultsFromSchema(types.find((t) => t.id === id)?.config_schema));
    setTest(undefined);
    setOptions({});
  }

  const stored = editing?.secrets ?? {};
  const missing = type ? missingRequired(type.config_schema, values, stored) : [];

  async function runTest() {
    if (!type) return;
    setTest("pending");
    try {
      setTest(
        await api.testProviderConfig(type.id, toPayload(type.config_schema, values), editing?.id),
      );
    } catch (e) {
      setTest({ ok: false, message: (e as Error).message, details: {} });
    }
  }

  async function save() {
    if (!type) return;
    setSaving(true);
    setError(null);
    try {
      const config = toPayload(type.config_schema, values);
      if (editing) {
        await api.updateProvider(editing.id, {
          name: name.trim() || undefined,
          config,
        });
      } else {
        await api.createProvider({
          type: type.id,
          name: name.trim() || undefined,
          config,
          make_default: makeDefault,
        });
      }
      onSaved();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSaving(false);
    }
  }

  async function loadSource(source: "models" | "voices") {
    if (!editing) return;
    setError(null);
    try {
      if (source === "models") {
        const res = await api.providerModels(editing.id);
        setOptions((o) => ({
          ...o,
          models: res.models.map((m) => ({
            value: m.id,
            label: m.name ?? m.id,
          })),
        }));
      } else {
        const res = await api.providerVoices(editing.id, "vi");
        setOptions((o) => ({
          ...o,
          voices: res.voices.map((v) => ({
            value: v.id,
            label: voiceLabel(v),
          })),
        }));
      }
    } catch (e) {
      setError((e as Error).message);
    }
  }

  return (
    <div
      className="space-y-4 rounded-xl border border-indigo-200 bg-indigo-50/40 p-4"
      data-testid="provider-editor"
    >
      <div className="flex items-center justify-between">
        <h3 className="font-semibold text-slate-900">
          {editing ? `Sửa “${editing.name}”` : `Thêm ${CAPABILITY_INFO[state.capability].label}`}
        </h3>
        <Button variant="ghost" className="text-xs" onClick={onClose}>
          Đóng
        </Button>
      </div>

      {!editing && (
        <div className="grid gap-2 sm:grid-cols-2" role="radiogroup" aria-label="Loại nhà cung cấp">
          {choices.map((t) => (
            <button
              key={t.id}
              type="button"
              role="radio"
              aria-checked={t.id === typeId}
              onClick={() => pickType(t.id)}
              className={`rounded-lg border px-3 py-2 text-left text-sm ${
                t.id === typeId
                  ? "border-indigo-500 bg-white ring-2 ring-indigo-200"
                  : "border-slate-200 bg-white hover:border-slate-300"
              }`}
            >
              <span className="flex flex-wrap items-center gap-1.5 font-medium">
                {t.name}
                {t.is_mock && <Badge tone="slate">Demo</Badge>}
                {t.unofficial && <Badge tone="amber">Không chính thức</Badge>}
                {!t.requires_network && !t.is_mock && <Badge tone="indigo">Offline</Badge>}
              </span>
              <span className="mt-0.5 block text-xs text-slate-500">{t.description}</span>
            </button>
          ))}
        </div>
      )}

      {type && (
        <>
          {type.docs_url && (
            <p className="text-xs text-slate-600">
              Hướng dẫn lấy khoá:{" "}
              <a
                href={type.docs_url}
                target="_blank"
                rel="noreferrer"
                className="text-indigo-700 underline"
              >
                {type.docs_url}
              </a>
            </p>
          )}
          <div className="flex flex-col gap-1">
            <label htmlFor="provider-name" className="text-sm font-medium text-slate-800">
              Tên hiển thị
            </label>
            <input
              id="provider-name"
              className="input"
              placeholder={type.name}
              value={name}
              onChange={(e) => setName(e.target.value)}
            />
          </div>
          <SchemaForm
            key={type.id}
            idPrefix={`p-${type.id}`}
            schema={type.config_schema}
            value={values}
            onChange={setValues}
            storedSecrets={stored}
            sourceOptions={options}
            onLoadSource={editing ? loadSource : undefined}
            emptyText="Nhà cung cấp này không cần cấu hình."
          />
          {!editing && (
            <label className="flex items-center gap-2 text-sm">
              <input
                type="checkbox"
                className="size-4 accent-indigo-600"
                checked={makeDefault}
                onChange={(e) => setMakeDefault(e.target.checked)}
              />
              Đặt làm mặc định cho {CAPABILITY_INFO[state.capability].label}
            </label>
          )}
          <p className="text-xs text-slate-500">
            Khoá API được lưu trong kho bí mật của hệ điều hành (DPAPI trên Windows), không lưu
            trong cơ sở dữ liệu và không bao giờ hiển thị lại đầy đủ.
          </p>
          <ErrorBox>{error}</ErrorBox>
          {missing.length > 0 && (
            <p className="text-xs text-amber-700">Còn thiếu: {missing.join(", ")}</p>
          )}
          <div className="flex flex-wrap items-center gap-2">
            <Button variant="secondary" onClick={runTest} disabled={test === "pending"}>
              Kiểm tra kết nối
            </Button>
            <Button onClick={save} disabled={saving || missing.length > 0}>
              {saving ? "Đang lưu…" : editing ? "Lưu thay đổi" : "Thêm nhà cung cấp"}
            </Button>
            <TestBadge result={test} />
          </div>
        </>
      )}
    </div>
  );
}

export function ProvidersSettings() {
  const api = useApi();
  const [types, setTypes] = useState<ProviderType[]>([]);
  const [instances, setInstances] = useState<ProviderInstance[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [editor, setEditor] = useState<EditorState | null>(null);
  const [tests, setTests] = useState<Record<string, TestResult | "pending">>({});

  const reload = useCallback(
    () =>
      Promise.all([api.providerTypes(), api.listProviders()])
        .then(([t, list]) => {
          setTypes(t.types);
          setInstances(list.instances);
        })
        .catch((e: Error) => setError(e.message)),
    [api],
  );

  useEffect(() => {
    reload();
  }, [reload]);

  const typeById = useMemo(() => Object.fromEntries(types.map((t) => [t.id, t])), [types]);

  async function act(fn: () => Promise<unknown>) {
    setError(null);
    try {
      await fn();
      await reload();
    } catch (e) {
      setError((e as Error).message);
    }
  }

  async function testInstance(id: string) {
    setTests((t) => ({ ...t, [id]: "pending" }));
    try {
      const res = await api.testProvider(id);
      setTests((t) => ({ ...t, [id]: res }));
    } catch (e) {
      setTests((t) => ({
        ...t,
        [id]: { ok: false, message: (e as Error).message, details: {} },
      }));
    }
  }

  return (
    <div className="space-y-6">
      <p className="text-sm text-slate-600">
        Mỗi chức năng có thể có nhiều nhà cung cấp. Nhà cung cấp “mặc định” được dùng cho mọi dự án,
        trừ khi dự án chọn riêng. Đổi mặc định có hiệu lực ngay, không cần khởi động lại.
      </p>
      <ErrorBox>{error}</ErrorBox>
      {CAPABILITY_ORDER.map((cap) => {
        const list = instances.filter((i) => i.capability === cap);
        return (
          <Card
            key={cap}
            title={CAPABILITY_INFO[cap].label}
            actions={
              <Button
                variant="secondary"
                className="text-xs"
                onClick={() => setEditor({ capability: cap, instance: null })}
                data-testid={`add-provider-${cap}`}
              >
                + Thêm
              </Button>
            }
          >
            <div className="space-y-3" data-testid={`providers-${cap}`}>
              <p className="text-xs text-slate-500">{CAPABILITY_INFO[cap].hint}</p>
              {editor?.capability === cap && !editor.instance && (
                <ProviderEditor
                  state={editor}
                  types={types}
                  onClose={() => setEditor(null)}
                  onSaved={() => {
                    setEditor(null);
                    reload();
                  }}
                />
              )}
              {list.length === 0 && (
                <p className="text-sm text-slate-500">Chưa có nhà cung cấp nào.</p>
              )}
              <ul className="space-y-3">
                {list.map((inst) => (
                  <li
                    key={inst.id}
                    className={`rounded-lg border p-3 ${inst.is_default ? "border-indigo-300 bg-indigo-50/30" : "border-slate-200"}`}
                    data-testid="provider-instance"
                  >
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="font-medium text-slate-900">{inst.name}</span>
                      <span className="text-xs text-slate-500">{inst.type_name}</span>
                      {inst.is_default && <Badge tone="indigo">Mặc định</Badge>}
                      {inst.is_mock && <Badge tone="slate">Demo</Badge>}
                      {inst.unofficial && <Badge tone="amber">Không chính thức</Badge>}
                      {!inst.available && <Badge tone="rose">Thiếu plugin</Badge>}
                      <div className="ml-auto flex flex-wrap gap-1">
                        <Button
                          variant="ghost"
                          className="text-xs"
                          onClick={() => testInstance(inst.id)}
                        >
                          Kiểm tra kết nối
                        </Button>
                        {!inst.is_default && (
                          <Button
                            variant="ghost"
                            className="text-xs"
                            onClick={() => act(() => api.setDefaultProvider(inst.id))}
                          >
                            Đặt mặc định
                          </Button>
                        )}
                        <Button
                          variant="ghost"
                          className="text-xs"
                          onClick={() => setEditor({ capability: cap, instance: inst })}
                        >
                          Sửa
                        </Button>
                        <Button
                          variant="ghost"
                          className="text-xs text-rose-700"
                          onClick={() => {
                            if (window.confirm(`Xoá “${inst.name}” và các khoá đã lưu?`)) {
                              act(() => api.deleteProvider(inst.id));
                            }
                          }}
                        >
                          Xoá
                        </Button>
                      </div>
                    </div>
                    {Object.keys(inst.secrets).length > 0 && (
                      <p className="mt-1 text-xs text-slate-500">
                        {Object.entries(inst.secrets).map(([k, v]) => (
                          <span key={k} className="mr-3">
                            {typeById[inst.type]?.config_schema.properties?.[k]?.title ?? k}:{" "}
                            <code className="font-mono">{v ?? "(chưa nhập)"}</code>
                          </span>
                        ))}
                      </p>
                    )}
                    <div className="mt-1">
                      <TestBadge result={tests[inst.id]} />
                    </div>
                    {cap === "tts" && inst.available && (
                      <VoicePicker
                        key={JSON.stringify(inst.config)}
                        instance={inst}
                        type={typeById[inst.type]}
                        onSaved={reload}
                      />
                    )}
                    {editor?.instance?.id === inst.id && (
                      <div className="mt-3">
                        <ProviderEditor
                          state={editor}
                          types={types}
                          onClose={() => setEditor(null)}
                          onSaved={() => {
                            setEditor(null);
                            reload();
                          }}
                        />
                      </div>
                    )}
                  </li>
                ))}
              </ul>
            </div>
          </Card>
        );
      })}
    </div>
  );
}
