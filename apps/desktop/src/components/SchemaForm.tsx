import { useState } from "react";
import {
  CLEAR_SECRET,
  coerceValue,
  enumOptions,
  widgetFor,
  type FormValues,
} from "../lib/schemaForm";
import type { JSONSchemaObject, JSONSchemaProperty } from "../lib/types";

export interface FieldOption {
  value: string;
  label: string;
}

interface Props {
  schema: JSONSchemaObject | JSONSchemaProperty;
  value: FormValues;
  onChange: (value: FormValues) => void;
  idPrefix?: string;
  /** Masked secrets already stored (••••1234): shown as placeholder, input stays empty. */
  storedSecrets?: Record<string, string | null>;
  /** Options for fields with ``x-source`` (models / voices). */
  sourceOptions?: Partial<Record<"models" | "voices", FieldOption[]>>;
  /** Called when the user asks to (re)load options for an ``x-source`` field. */
  onLoadSource?: (source: "models" | "voices") => void;
  emptyText?: string;
}

function SecretInput({
  id,
  value,
  stored,
  onChange,
}: {
  id: string;
  value: string;
  stored?: string | null;
  onChange: (v: string) => void;
}) {
  const [show, setShow] = useState(false);
  const clearing = value === CLEAR_SECRET;
  return (
    <div className="flex gap-2">
      <input
        id={id}
        className={`input flex-1 font-mono ${clearing ? "bg-rose-50" : ""}`}
        type={show ? "text" : "password"}
        autoComplete="off"
        spellCheck={false}
        placeholder={
          clearing
            ? "Khoá đã lưu sẽ bị xoá khi bấm Lưu"
            : stored
              ? `${stored} — đã lưu, để trống để giữ nguyên`
              : "Chưa nhập"
        }
        value={clearing ? "" : value}
        onChange={(e) => onChange(e.target.value)}
      />
      <button
        type="button"
        className="rounded-lg border border-slate-300 px-2.5 text-xs text-slate-600 hover:bg-slate-50"
        onClick={() => setShow((s) => !s)}
        aria-label={show ? "Ẩn" : "Hiện"}
      >
        {show ? "Ẩn" : "Hiện"}
      </button>
      {stored && (
        <button
          type="button"
          className={`rounded-lg border px-2.5 text-xs ${
            clearing
              ? "border-slate-300 text-slate-600 hover:bg-slate-50"
              : "border-rose-200 text-rose-700 hover:bg-rose-50"
          }`}
          onClick={() => onChange(clearing ? "" : CLEAR_SECRET)}
          title={clearing ? "Giữ lại khoá đã lưu" : "Xoá khoá đã lưu khỏi kho bí mật"}
        >
          {clearing ? "Hoàn tác" : "Xoá khoá"}
        </button>
      )}
    </div>
  );
}

/** Renders a form from a JSON Schema object (adapter / provider config_schema). Supports
 * enums (with labels), secrets (password inputs), nested objects, multiline text and
 * option lists loaded from the provider (models, voices). */
export function SchemaForm({
  schema,
  value,
  onChange,
  idPrefix = "cfg",
  storedSecrets = {},
  sourceOptions = {},
  onLoadSource,
  emptyText = "Không có tuỳ chọn.",
}: Props) {
  const entries = Object.entries(schema.properties ?? {});
  if (entries.length === 0) {
    return <p className="text-sm text-slate-500">{emptyText}</p>;
  }
  const required = new Set(schema.required ?? []);
  const set = (key: string, v: unknown) => onChange({ ...value, [key]: v });

  return (
    <div className="grid gap-4 sm:grid-cols-2">
      {entries.map(([key, prop]) => {
        const id = `${idPrefix}-${key}`;
        const widget = widgetFor(prop);
        const label = (
          <>
            {prop.title ?? key}
            {required.has(key) && <span className="text-rose-600"> *</span>}
          </>
        );
        const current = value[key];
        if (widget === "checkbox") {
          return (
            <label key={key} htmlFor={id} className="flex items-start gap-3 sm:col-span-2">
              <input
                id={id}
                type="checkbox"
                className="mt-1 size-4 accent-indigo-600"
                checked={Boolean(current)}
                onChange={(e) => set(key, e.target.checked)}
              />
              <span>
                <span className="text-sm font-medium text-slate-800">{label}</span>
                {prop.description && (
                  <span className="block text-xs text-slate-500">{prop.description}</span>
                )}
              </span>
            </label>
          );
        }
        if (widget === "object") {
          return (
            <fieldset key={key} className="rounded-lg border border-slate-200 p-3 sm:col-span-2">
              <legend className="px-1 text-sm font-semibold text-slate-700">{label}</legend>
              <SchemaForm
                schema={prop}
                idPrefix={id}
                value={(current as FormValues) ?? {}}
                onChange={(v) => set(key, v)}
              />
            </fieldset>
          );
        }
        const source = prop["x-source"];
        const options = source ? sourceOptions[source] : undefined;
        return (
          <div
            key={key}
            className={`flex flex-col gap-1 ${widget === "textarea" || widget === "password" ? "sm:col-span-2" : ""}`}
          >
            <label htmlFor={id} className="text-sm font-medium text-slate-800">
              {label}
            </label>
            {widget === "select" ? (
              <select
                id={id}
                className="input"
                value={current === undefined || current === null ? "" : String(current)}
                onChange={(e) => set(key, coerceValue(prop, e.target.value))}
              >
                {!required.has(key) && prop.default === undefined && <option value="">—</option>}
                {enumOptions(prop).map((o) => (
                  <option key={String(o.value)} value={String(o.value)}>
                    {o.label}
                  </option>
                ))}
              </select>
            ) : widget === "password" ? (
              <SecretInput
                id={id}
                value={typeof current === "string" ? current : ""}
                stored={storedSecrets[key]}
                onChange={(v) => set(key, v)}
              />
            ) : widget === "textarea" ? (
              <textarea
                id={id}
                className="input min-h-20"
                value={current === undefined || current === null ? "" : String(current)}
                onChange={(e) => set(key, e.target.value)}
              />
            ) : widget === "combo" ? (
              <div className="flex gap-2">
                {options && options.length > 0 ? (
                  <select
                    id={id}
                    className="input flex-1"
                    value={current === undefined || current === null ? "" : String(current)}
                    onChange={(e) => set(key, e.target.value)}
                  >
                    {current !== undefined &&
                      current !== "" &&
                      !options.some((o) => o.value === current) && (
                        <option value={String(current)}>{String(current)}</option>
                      )}
                    {options.map((o) => (
                      <option key={o.value} value={o.value}>
                        {o.label}
                      </option>
                    ))}
                  </select>
                ) : (
                  <input
                    id={id}
                    className="input flex-1"
                    value={current === undefined || current === null ? "" : String(current)}
                    onChange={(e) => set(key, e.target.value)}
                  />
                )}
                {onLoadSource && source && (
                  <button
                    type="button"
                    className="rounded-lg border border-slate-300 px-2.5 text-xs whitespace-nowrap text-slate-700 hover:bg-slate-50"
                    onClick={() => onLoadSource(source)}
                  >
                    {source === "models" ? "Tải danh sách model" : "Tải danh sách giọng"}
                  </button>
                )}
              </div>
            ) : (
              <input
                id={id}
                className="input"
                type={widget === "number" ? "number" : "text"}
                step={prop.type === "number" ? "any" : undefined}
                min={prop.minimum}
                max={prop.maximum}
                value={current === undefined || current === null ? "" : String(current)}
                onChange={(e) => set(key, coerceValue(prop, e.target.value))}
              />
            )}
            {prop.description && <p className="text-xs text-slate-500">{prop.description}</p>}
          </div>
        );
      })}
    </div>
  );
}
