import { useRef, useState } from "react";
import type { TimelineEntry } from "../lib/types";

const SEGMENT_COLORS = ["bg-indigo-500", "bg-sky-500", "bg-violet-500", "bg-teal-500"];

function fmt(t: number) {
  const m = Math.floor(t / 60);
  return `${m}:${String(Math.floor(t % 60)).padStart(2, "0")}`;
}

/** Video player with a clickable scene timeline (from the render's ``timeline``). */
export function VideoPlayer({
  src,
  poster,
  timeline,
  aspect,
  selectedScene,
  onSelectScene,
  sceneLabel,
}: {
  src: string;
  poster?: string;
  timeline: TimelineEntry[];
  aspect: string;
  selectedScene?: string | null;
  onSelectScene?: (sceneId: string | null) => void;
  sceneLabel?: (sceneId: string) => string;
}) {
  const ref = useRef<HTMLVideoElement>(null);
  const [time, setTime] = useState(0);
  const [duration, setDuration] = useState(0);
  const total = duration || timeline.at(-1)?.end || 0;
  const scenes = timeline.filter((t) => t.kind === "scene");

  const seek = (entry: TimelineEntry) => {
    const v = ref.current;
    if (v) {
      v.currentTime = entry.start + 0.05;
      setTime(entry.start);
    }
    onSelectScene?.(entry.kind === "scene" ? entry.scene_id : null);
  };

  const active = timeline.find((t) => time >= t.start && time < t.end);
  const portrait = aspect === "9:16";

  return (
    <div className="space-y-2">
      <div className={`overflow-hidden rounded-lg bg-black ${portrait ? "mx-auto max-w-xs" : ""}`}>
        <video
          ref={ref}
          key={src}
          src={src}
          poster={poster}
          controls
          className="w-full"
          onTimeUpdate={(e) => setTime(e.currentTarget.currentTime)}
          onLoadedMetadata={(e) => {
            setDuration(e.currentTarget.duration);
            setTime(e.currentTarget.currentTime);
          }}
          data-testid="review-video"
        />
      </div>
      {timeline.length > 0 && total > 0 && (
        <div>
          <div
            className="relative flex h-9 w-full overflow-hidden rounded-md bg-slate-200"
            role="list"
            aria-label="Dòng thời gian các cảnh"
          >
            {timeline.map((t, i) => {
              const sceneIndex = scenes.indexOf(t);
              const isSelected = t.kind === "scene" && selectedScene === t.scene_id;
              const label =
                t.kind === "intro" ? "Mở" : t.kind === "outro" ? "Kết" : String(sceneIndex + 1);
              return (
                <button
                  key={`${t.scene_id}-${i}`}
                  type="button"
                  role="listitem"
                  title={`${
                    t.kind === "scene"
                      ? `Cảnh ${sceneIndex + 1}${sceneLabel ? ` — ${sceneLabel(t.scene_id)}` : ""}`
                      : t.kind === "intro"
                        ? "Thẻ mở đầu"
                        : "Thẻ kết"
                  } (${fmt(t.start)}–${fmt(t.end)})`}
                  onClick={() => seek(t)}
                  className={`relative h-full border-r border-white/60 text-[11px] font-semibold text-white transition ${
                    t.kind === "scene"
                      ? SEGMENT_COLORS[sceneIndex % SEGMENT_COLORS.length]
                      : "bg-slate-500"
                  } ${isSelected ? "ring-3 ring-amber-400 ring-inset" : ""} ${
                    active === t ? "brightness-110" : "opacity-80 hover:opacity-100"
                  }`}
                  style={{ width: `${((t.end - t.start) / total) * 100}%` }}
                  data-testid="timeline-segment"
                >
                  <span className="truncate px-1">{label}</span>
                </button>
              );
            })}
            <div
              className="pointer-events-none absolute top-0 h-full w-0.5 bg-amber-300"
              style={{ left: `${Math.min(100, (time / total) * 100)}%` }}
            />
          </div>
          <p className="mt-1 text-xs text-slate-500">
            Bấm vào một cảnh trên dòng thời gian để tua tới và chỉnh sửa cảnh đó.
            {active?.kind === "scene" && ` Đang phát cảnh ${scenes.indexOf(active) + 1}.`}
          </p>
        </div>
      )}
    </div>
  );
}
