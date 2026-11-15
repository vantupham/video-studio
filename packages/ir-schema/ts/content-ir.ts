// TypeScript mirror of content-ir.schema.json (keep in sync with the backend Pydantic models).

export type SourceType = "script" | "markdown" | "docx" | "pdf" | "website" | "text" | "other";

export interface IRSource {
  type: SourceType;
  uri: string;
  lang?: string;
}

interface BlockCommon {
  id?: string | null;
  meta?: Record<string, unknown>;
}

export interface ParagraphBlock extends BlockCommon {
  kind: "paragraph";
  text: string;
}

export interface HeadingBlock extends BlockCommon {
  kind: "heading";
  text: string;
  level: number;
}

export interface ImageBlock extends BlockCommon {
  kind: "image";
  src: string;
  alt?: string;
  caption?: string | null;
}

export interface TableBlock extends BlockCommon {
  kind: "table";
  header?: string[];
  rows?: string[][];
  caption?: string | null;
}

export interface ListItem {
  text: string;
  items?: ListItem[];
}

export interface ListBlock extends BlockCommon {
  kind: "list";
  ordered?: boolean;
  items?: ListItem[];
}

export interface CodeBlock extends BlockCommon {
  kind: "code";
  code: string;
  language?: string | null;
}

export interface ScreenRecordingBlock extends BlockCommon {
  kind: "screen_recording";
  url?: string | null;
  recipe?: string | null;
  path?: string | null;
  duration_s?: number | null;
}

export type Block =
  | ParagraphBlock
  | HeadingBlock
  | ImageBlock
  | TableBlock
  | ListBlock
  | CodeBlock
  | ScreenRecordingBlock;

export type BlockKind = Block["kind"];

export const BLOCK_KINDS: readonly BlockKind[] = [
  "paragraph",
  "heading",
  "image",
  "table",
  "list",
  "code",
  "screen_recording",
] as const;

export interface Section {
  id: string;
  title?: string;
  level?: number;
  blocks: Block[];
}

export interface ContentIR {
  schema_version: "1.0";
  title: string;
  source: IRSource;
  metadata?: Record<string, unknown>;
  sections: Section[];
  created_at?: string;
}
