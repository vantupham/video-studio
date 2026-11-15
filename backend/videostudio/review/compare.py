"""Scene-level diff between two render snapshots (for the version history view)."""

from __future__ import annotations

from typing import Any

FIELDS = ("narration", "duration", "visual", "overlays")


def diff_snapshots(a: dict[str, Any] | None, b: dict[str, Any] | None) -> dict[str, Any]:
    a, b = a or {}, b or {}
    old = {sc["id"]: sc for sc in a.get("scenes", [])}
    new = {sc["id"]: sc for sc in b.get("scenes", [])}
    order = [sc["id"] for sc in b.get("scenes", [])]
    order += [sid for sid in old if sid not in new]
    scenes = []
    for sid in order:
        before, after = old.get(sid), new.get(sid)
        if before is None:
            change, fields = "added", list(FIELDS)
        elif after is None:
            change, fields = "removed", list(FIELDS)
        else:
            fields = [f for f in FIELDS if before.get(f) != after.get(f)]
            change = "changed" if fields else "same"
        scenes.append({"scene_id": sid, "change": change, "fields": fields,
                       "before": before, "after": after})
    old_order = [sc["id"] for sc in a.get("scenes", []) if sc["id"] in new]
    new_order = [sid for sid in order if sid in old]
    summary = {k: sum(1 for s in scenes if s["change"] == k)
               for k in ("added", "removed", "changed", "same")}
    return {"title_changed": a.get("title") != b.get("title"),
            "title": {"before": a.get("title"), "after": b.get("title")},
            "reordered": old_order != new_order, "summary": summary, "scenes": scenes}
