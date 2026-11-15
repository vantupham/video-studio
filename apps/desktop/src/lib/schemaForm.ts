import type { JSONSchemaObject, JSONSchemaProperty } from "./types";

export type FormValues = Record<string, unknown>;

/** Default values for a (possibly nested) schema. Secret fields are left empty. */
export function defaultsFromSchema(
  schema: JSONSchemaObject | JSONSchemaProperty | undefined,
): FormValues {
  const values: FormValues = {};
  for (const [key, prop] of Object.entries(schema?.properties ?? {})) {
    if (isSecret(prop)) continue;
    if (prop.type === "object" && prop.properties) values[key] = defaultsFromSchema(prop);
    else if (prop.default !== undefined) values[key] = prop.default;
    else if (prop.type === "boolean") values[key] = false;
  }
  return values;
}

export type FieldWidget =
  "select" | "checkbox" | "number" | "text" | "password" | "textarea" | "object" | "combo";

export function isSecret(prop: JSONSchemaProperty): boolean {
  return Boolean(prop["x-secret"]) || prop.format === "password";
}

export function widgetFor(prop: JSONSchemaProperty): FieldWidget {
  if (isSecret(prop)) return "password";
  if (prop.type === "object" && prop.properties) return "object";
  if (prop.enum && prop.enum.length > 0) return "select";
  if (prop.type === "boolean") return "checkbox";
  if (prop.type === "integer" || prop.type === "number") return "number";
  if (prop["x-source"]) return "combo";
  if (prop["x-multiline"]) return "textarea";
  return "text";
}

export function enumOptions(prop: JSONSchemaProperty): { value: string | number; label: string }[] {
  const labels = prop["x-enumLabels"];
  return (prop.enum ?? []).map((value, i) => ({
    value,
    label: (Array.isArray(labels) ? labels[i] : labels?.[String(value)]) ?? String(value),
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

/** Required fields that are still empty. Secrets count as present when already stored. */
export function missingRequired(
  schema: JSONSchemaObject,
  values: FormValues,
  storedSecrets: Record<string, string | null> = {},
): string[] {
  const missing: string[] = [];
  for (const key of schema.required ?? []) {
    const prop = schema.properties?.[key];
    if (!prop) continue;
    const v = values[key];
    if (v === CLEAR_SECRET) {
      missing.push(prop.title ?? key);
      continue;
    }
    const empty = v === undefined || v === null || v === "";
    if (empty && !(isSecret(prop) && storedSecrets[key])) missing.push(prop.title ?? key);
  }
  return missing;
}

/** Form value meaning "delete the stored secret" (sent to the API as ""). */
export const CLEAR_SECRET = "\u0000clear";

/** Payload for the API: drop empty secret inputs so stored secrets are kept. */
export function toPayload(schema: JSONSchemaObject, values: FormValues): FormValues {
  const out: FormValues = {};
  for (const [key, v] of Object.entries(values)) {
    const prop = schema.properties?.[key];
    if (!prop) continue;
    if (isSecret(prop) && v === CLEAR_SECRET) {
      out[key] = "";
      continue;
    }
    if (isSecret(prop) && (v === undefined || v === "")) continue;
    if (v === undefined) continue;
    out[key] = v;
  }
  return out;
}
