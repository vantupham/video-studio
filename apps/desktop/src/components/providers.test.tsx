import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  defaultsFromSchema,
  enumOptions,
  missingRequired,
  toPayload,
  widgetFor,
} from "../lib/schemaForm";
import type { Job, JSONSchemaObject } from "../lib/types";
import { JobSteps } from "./JobProgress";
import { SchemaForm } from "./SchemaForm";

afterEach(cleanup);

const providerSchema: JSONSchemaObject = {
  type: "object",
  required: ["api_key", "model"],
  properties: {
    api_key: { type: "string", title: "API key", "x-secret": true, format: "password" },
    model: { type: "string", title: "Model", default: "gpt-4o-mini", "x-source": "models" },
    json_mode: {
      type: "string",
      title: "Chế độ JSON",
      enum: ["json_schema", "json_object"],
      "x-enumLabels": { json_schema: "JSON Schema", json_object: "JSON object" },
      default: "json_schema",
    },
    advanced: {
      type: "object",
      title: "Nâng cao",
      properties: { timeout_s: { type: "number", title: "Thời gian chờ", default: 60 } },
    },
  },
};

describe("schemaForm helpers", () => {
  it("never pre-fills secrets and fills nested defaults", () => {
    expect(defaultsFromSchema(providerSchema)).toEqual({
      model: "gpt-4o-mini",
      json_mode: "json_schema",
      advanced: { timeout_s: 60 },
    });
  });

  it("supports enum labels as an object or an array", () => {
    expect(enumOptions(providerSchema.properties!.json_mode).map((o) => o.label)).toEqual([
      "JSON Schema",
      "JSON object",
    ]);
    expect(
      enumOptions({ enum: [1, 2], "x-enumLabels": ["Một", "Hai"] }).map((o) => o.label),
    ).toEqual(["Một", "Hai"]);
  });

  it("picks widgets for secrets, objects and provider-backed lists", () => {
    const p = providerSchema.properties!;
    expect(widgetFor(p.api_key)).toBe("password");
    expect(widgetFor(p.advanced)).toBe("object");
    expect(widgetFor(p.model)).toBe("combo");
  });

  it("treats a stored secret as present and drops empty secret inputs", () => {
    const values = { api_key: "", model: "gpt-4o" };
    expect(missingRequired(providerSchema, values)).toEqual(["API key"]);
    expect(missingRequired(providerSchema, values, { api_key: "••••abcd" })).toEqual([]);
    expect(toPayload(providerSchema, values)).toEqual({ model: "gpt-4o" });
    expect(toPayload(providerSchema, { ...values, api_key: "sk-new" })).toEqual({
      api_key: "sk-new",
      model: "gpt-4o",
    });
  });
});

describe("SchemaForm with provider schemas", () => {
  it("renders secrets as password inputs showing only the masked stored value", () => {
    const onChange = vi.fn();
    render(
      <SchemaForm
        schema={providerSchema}
        value={defaultsFromSchema(providerSchema)}
        onChange={onChange}
        storedSecrets={{ api_key: "••••abcd" }}
      />,
    );
    const input = screen.getByLabelText(/API key/) as HTMLInputElement;
    expect(input.type).toBe("password");
    expect(input.value).toBe("");
    expect(input.placeholder).toContain("••••abcd");

    fireEvent.change(input, { target: { value: "sk-test" } });
    expect(onChange).toHaveBeenLastCalledWith(expect.objectContaining({ api_key: "sk-test" }));

    fireEvent.change(screen.getByLabelText("Thời gian chờ"), { target: { value: "30" } });
    expect(onChange).toHaveBeenLastCalledWith(
      expect.objectContaining({ advanced: { timeout_s: 30 } }),
    );
  });

  it("shows loaded model options and a load button", () => {
    const onLoad = vi.fn();
    render(
      <SchemaForm
        schema={providerSchema}
        value={{ model: "gpt-4o" }}
        onChange={() => undefined}
        sourceOptions={{ models: [{ value: "gpt-4o", label: "GPT-4o" }] }}
        onLoadSource={onLoad}
      />,
    );
    expect(screen.getByRole("option", { name: "GPT-4o" })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Tải danh sách model" }));
    expect(onLoad).toHaveBeenCalledWith("models");
  });
});

describe("JobSteps", () => {
  const steps = [
    { id: "tts", label: "Giọng đọc", start: 0, end: 35 },
    { id: "render", label: "Dựng hình", start: 35, end: 80 },
    { id: "encode", label: "Xuất video", start: 80, end: 100 },
  ];
  const job = (status: Job["status"], progress: number): Job => ({
    id: "j1",
    type: "render",
    project_id: null,
    status,
    progress,
    message: "",
    payload: { steps },
    result: null,
    error: null,
    attempts: 1,
    cancel_requested: false,
    created_at: null,
    started_at: null,
    finished_at: null,
  });

  const states = () =>
    screen.getAllByRole("listitem").map((li) => li.getAttribute("data-state"));

  it("marks finished, active and pending steps", () => {
    render(<JobSteps job={job("running", 50)} />);
    expect(states()).toEqual(["done", "active", "pending"]);
  });

  it("marks the failing step and completes all on success", () => {
    const { rerender } = render(<JobSteps job={job("failed", 20)} />);
    expect(states()).toEqual(["failed", "pending", "pending"]);
    rerender(<JobSteps job={job("succeeded", 100)} />);
    expect(states()).toEqual(["done", "done", "done"]);
  });
});
