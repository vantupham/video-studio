import type { JSONSchemaObject, JSONSchemaProperty } from "./types";

export type FormValues = Record<string, unknown>;

export function defaultsFromSchema(schema: JSONSchemaObject | undefined): FormValues {
  const values: FormValues = {};
  for (const [key, prop] of Object.entries(schema?.properties ?? {})) {
    if (prop.default !== undefined) values[key] = prop.default;
    else if (prop.type === "boolean") values[key] = false;
  }
  return values;
}

export type FieldWidget = "select" | "checkbox" | "number" | "text";

export function widgetFor(prop: JSONSchemaProperty): FieldWidget {
  if (prop.enum && prop.enum.length > 0) return "select";
  if (prop.type === "boolean") return "checkbox";
  if (prop.type === "integer" || prop.type === "number") return "number";
  return "text";
}

export function enumOptions(prop: JSONSchemaProperty): { value: string | number; label: string }[] {
  return (prop.enum ?? []).map((value, i) => ({
    value,
    label: prop["x-enumLabels"]?.[i] ?? String(value),
  }));
}

/** Convert the raw string coming from an <input>/<select> back to the schema type. */
export function coerceValue(prop: JSONSchemaProperty, raw: string): unknown {
  if (prop.type === "integer") {
    const n = Number.parseInt(raw, 10);
    return Number.isNaN(n) ? undefined : n;
  }
  if (prop.type === "number") {
    const n = Number.parseFloat(raw);
    return Number.isNaN(n) ? undefined : n;
  }
  return raw;
}
