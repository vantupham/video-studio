import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { ContentIR } from "@ir/content-ir";
import { defaultsFromSchema } from "../lib/schemaForm";
import type { JSONSchemaObject } from "../lib/types";
import { IrPreview } from "./IrViewer";
import { SchemaForm } from "./SchemaForm";

afterEach(cleanup);

const schema: JSONSchemaObject = {
  type: "object",
  properties: {
    title: { type: "string", title: "Tiêu đề", default: "" },
    language: {
      type: "string",
      title: "Ngôn ngữ",
      enum: ["vi", "en"],
      "x-enumLabels": ["Tiếng Việt", "English"],
      default: "vi",
    },
    section_level: { type: "integer", title: "Cấp", enum: [1, 2, 3], default: 2 },
    include_code: { type: "boolean", title: "Giữ code", default: true },
  },
};

describe("SchemaForm", () => {
  it("computes defaults from the schema", () => {
    expect(defaultsFromSchema(schema)).toEqual({
      title: "",
      language: "vi",
      section_level: 2,
      include_code: true,
    });
  });

  it("renders widgets and coerces values to schema types", () => {
    const onChange = vi.fn();
    render(<SchemaForm schema={schema} value={defaultsFromSchema(schema)} onChange={onChange} />);
    expect(screen.getByRole("option", { name: "Tiếng Việt" })).toBeTruthy();

    fireEvent.change(screen.getByLabelText("Cấp"), { target: { value: "3" } });
    expect(onChange).toHaveBeenLastCalledWith(expect.objectContaining({ section_level: 3 }));

    fireEvent.click(screen.getByLabelText(/Giữ code/));
    expect(onChange).toHaveBeenLastCalledWith(expect.objectContaining({ include_code: false }));
  });
});

describe("IrPreview", () => {
  it("renders sections and block kinds in Vietnamese", () => {
    const ir: ContentIR = {
      schema_version: "1.0",
      title: "Bài học tiếng Việt",
      source: { type: "markdown", uri: "bai-hoc.md", lang: "vi" },
      sections: [
        {
          id: "s1",
          title: "Phần mở đầu",
          blocks: [
            { kind: "paragraph", text: "Xin chào các bạn." },
            { kind: "table", header: ["Cột A"], rows: [["Giá trị"]] },
            { kind: "list", ordered: true, items: [{ text: "Bước một" }] },
          ],
        },
      ],
    };
    render(<IrPreview ir={ir} />);
    expect(screen.getByText("Bài học tiếng Việt")).toBeTruthy();
    expect(screen.getByText("Phần mở đầu")).toBeTruthy();
    expect(screen.getByText("Xin chào các bạn.")).toBeTruthy();
    expect(screen.getByRole("columnheader", { name: "Cột A" })).toBeTruthy();
    expect(screen.getByText("Bước một")).toBeTruthy();
  });
});
