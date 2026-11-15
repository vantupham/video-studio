// TypeScript mirror of storyboard.schema.json (generated from
// backend/videostudio/pipeline/models.py). Keep in sync: tests/test_storyboard.py checks
// the visual types and field names.

export const VISUAL_TYPES = ["title_card", "text_card", "image", "stock", "solid"] as const;
export type VisualType = (typeof VISUAL_TYPES)[number];

export const ASPECTS = ["16:9", "9:16", "1:1"] as const;
export type Aspect = (typeof ASPECTS)[number];

export interface Visual {
  type: VisualType;
  title?: string | null;
  subtitle?: string | null;
  bullets?: string[];
  /** Image path relative to the data dir (uploaded / document image). */
  image?: string | null;
  /** Stock media search query. */
  query?: string | null;
  /** Background colour #RRGGBB. */
  color?: string | null;
}

export interface Overlay {
  type: "text" | "lower_third";
  text: string;
  position: "top" | "center" | "bottom";
}

export interface Scene {
  id: string;
  narration: string;
  visual: Visual;
  overlays?: Overlay[];
  /** "auto" follows the narration; a number is a fixed duration in seconds. */
  duration: number | "auto";
  source_block_ids?: string[];
}

export interface Storyboard {
  version: 1;
  title: string;
  language: string;
  style: string;
  aspect: Aspect;
  scenes: Scene[];
}
