import { useState } from "react";
import type { Block, ContentIR, ListItem } from "@ir/content-ir";

const KIND_LABELS: Record<Block["kind"], string> = {
  paragraph: "Đoạn văn",
  heading: "Tiêu đề",
  image: "Hình ảnh",
  table: "Bảng",
  list: "Danh sách",
  code: "Code",
  screen_recording: "Quay màn hình",
};

function ListItems({ items, ordered }: { items: ListItem[]; ordered: boolean }) {
  const Tag = ordered ? "ol" : "ul";
  return (
    <Tag className={`${ordered ? "list-decimal" : "list-disc"} space-y-1 pl-6`}>
      {items.map((item, i) => (
        <li key={i}>
          {item.text}
          {item.items && item.items.length > 0 && (
            <ListItems items={item.items} ordered={ordered} />
          )}
        </li>
      ))}
    </Tag>
  );
}

function BlockView({ block }: { block: Block }) {
  switch (block.kind) {
    case "paragraph":
      return (
        <p
          className={`leading-relaxed text-slate-800 ${block.meta?.quote ? "border-l-4 border-slate-300 pl-3 italic text-slate-600" : ""}`}
        >
          {block.text}
        </p>
      );
    case "heading": {
      const size = block.level <= 1 ? "text-xl" : block.level === 2 ? "text-lg" : "text-base";
      return <p className={`${size} font-semibold text-slate-900`}>{block.text}</p>;
    }
    case "image":
      return (
        <figure className="flex items-center gap-3 rounded-lg border border-dashed border-slate-300 bg-slate-50 p-3">
          <span className="grid size-12 shrink-0 place-items-center rounded bg-slate-200 text-xl">
            🖼
          </span>
          <figcaption className="text-sm">
            <span className="font-medium text-slate-800">{block.alt || "(không có mô tả)"}</span>
            <span className="block font-mono text-xs text-slate-500">{block.src}</span>
            {block.caption && <span className="block text-slate-600">{block.caption}</span>}
          </figcaption>
        </figure>
      );
    case "table":
      return (
        <div className="overflow-x-auto">
          <table className="w-full border-collapse text-sm">
            {block.header && block.header.length > 0 && (
              <thead>
                <tr>
                  {block.header.map((h, i) => (
                    <th
                      key={i}
                      className="border border-slate-200 bg-slate-100 px-3 py-1.5 text-left font-semibold"
                    >
                      {h}
                    </th>
                  ))}
                </tr>
              </thead>
            )}
            <tbody>
              {(block.rows ?? []).map((row, r) => (
                <tr key={r}>
                  {row.map((cell, c) => (
                    <td key={c} className="border border-slate-200 px-3 py-1.5">
                      {cell}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      );
    case "list":
      return <ListItems items={block.items ?? []} ordered={Boolean(block.ordered)} />;
    case "code":
      return (
        <pre className="overflow-x-auto rounded-lg bg-slate-900 p-3 text-xs text-slate-100">
          {block.language && <span className="mb-1 block text-slate-400">{block.language}</span>}
          {block.code}
        </pre>
      );
    case "screen_recording":
      return (
        <p className="text-sm text-slate-600">
          Quay màn hình: {block.url ?? block.recipe ?? block.path ?? "(chưa cấu hình)"}
        </p>
      );
  }
}

export function IrPreview({ ir }: { ir: ContentIR }) {
  return (
    <article className="space-y-6">
      <header>
        <h3 className="text-2xl font-bold text-slate-900">{ir.title}</h3>
        <p className="mt-1 text-xs text-slate-500">
          Nguồn: {ir.source.type} · {ir.source.uri} · ngôn ngữ: {ir.source.lang ?? "vi"}
        </p>
      </header>
      {ir.sections.map((section, idx) => (
        <section key={section.id} className="rounded-lg border border-slate-200 p-4">
          <div className="mb-3 flex items-baseline gap-2">
            <span className="rounded bg-indigo-100 px-2 py-0.5 text-xs font-semibold text-indigo-800">
              Phần {idx + 1}
            </span>
            <h4 className="font-semibold text-slate-900">{section.title || "(không tiêu đề)"}</h4>
            <span className="ml-auto text-xs text-slate-500">{section.blocks.length} block</span>
          </div>
          <div className="space-y-3">
            {section.blocks.map((block, i) => (
              <div key={block.id ?? i} className="group relative">
                <span className="mb-0.5 block text-[10px] font-medium tracking-wide text-slate-400 uppercase">
                  {KIND_LABELS[block.kind]}
                </span>
                <BlockView block={block} />
              </div>
            ))}
          </div>
        </section>
      ))}
    </article>
  );
}

export function IrViewer({ ir }: { ir: ContentIR }) {
  const [tab, setTab] = useState<"preview" | "json">("preview");
  const tabClass = (t: string) =>
    `rounded-md px-3 py-1.5 text-sm font-medium ${tab === t ? "bg-white text-slate-900 shadow-sm" : "text-slate-600 hover:text-slate-900"}`;
  return (
    <div className="space-y-4">
      <div className="inline-flex rounded-lg bg-slate-100 p-1">
        <button type="button" className={tabClass("preview")} onClick={() => setTab("preview")}>
          Xem trước
        </button>
        <button type="button" className={tabClass("json")} onClick={() => setTab("json")}>
          JSON
        </button>
      </div>
      {tab === "preview" ? (
        <IrPreview ir={ir} />
      ) : (
        <pre className="max-h-[600px] overflow-auto rounded-lg bg-slate-900 p-4 text-xs leading-relaxed text-emerald-100">
          {JSON.stringify(ir, null, 2)}
        </pre>
      )}
    </div>
  );
}
