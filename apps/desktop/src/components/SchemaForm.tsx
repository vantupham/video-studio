import { coerceValue, enumOptions, widgetFor, type FormValues } from "../lib/schemaForm";
import type { JSONSchemaObject } from "../lib/types";

interface Props {
  schema: JSONSchemaObject;
  value: FormValues;
  onChange: (value: FormValues) => void;
  idPrefix?: string;
}

/** Renders a form for a flat JSON Schema object (adapter config_schema). */
export function SchemaForm({ schema, value, onChange, idPrefix = "cfg" }: Props) {
  const entries = Object.entries(schema.properties ?? {});
  if (entries.length === 0) {
    return <p className="text-sm text-slate-500">Adapter này không có tuỳ chọn.</p>;
  }
  const set = (key: string, v: unknown) => onChange({ ...value, [key]: v });

  return (
    <div className="grid gap-4 sm:grid-cols-2">
      {entries.map(([key, prop]) => {
        const id = `${idPrefix}-${key}`;
        const widget = widgetFor(prop);
        const label = prop.title ?? key;
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
        return (
          <div key={key} className="flex flex-col gap-1">
            <label htmlFor={id} className="text-sm font-medium text-slate-800">
              {label}
            </label>
            {widget === "select" ? (
              <select
                id={id}
                className="input"
                value={current === undefined ? "" : String(current)}
                onChange={(e) => set(key, coerceValue(prop, e.target.value))}
              >
                {enumOptions(prop).map((o) => (
                  <option key={String(o.value)} value={String(o.value)}>
                    {o.label}
                  </option>
                ))}
              </select>
            ) : (
              <input
                id={id}
                className="input"
                type={widget === "number" ? "number" : "text"}
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
