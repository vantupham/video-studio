import type { Scene } from "./types";

export function newSceneId(existing: Scene[]): string {
  const ids = new Set(existing.map((s) => s.id));
  let n = existing.length + 1;
  while (ids.has(`s${n}`)) n += 1;
  return `s${n}`;
}
