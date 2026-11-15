import { PUBLICATION_STATUS, VIDEO_STATUS_LABELS, VIDEO_STATUS_STYLES } from "../lib/labels";
import { Pill } from "./ui";

export function VideoStatusBadge({ status, label }: { status: string; label?: string }) {
  return (
    <Pill className={VIDEO_STATUS_STYLES[status] ?? "bg-slate-100 text-slate-700"}>
      {label ?? VIDEO_STATUS_LABELS[status] ?? status}
    </Pill>
  );
}

export function PublicationBadge({ status }: { status: string }) {
  const info = PUBLICATION_STATUS[status] ?? { label: status, style: "bg-slate-100" };
  return <Pill className={info.style}>{info.label}</Pill>;
}

export function PlatformIcon({ platform }: { platform: string }) {
  return platform === "youtube" ? (
    <span
      className="grid size-5 shrink-0 place-items-center rounded bg-red-600 text-[10px] font-bold text-white"
      title="YouTube"
    >
      ▶
    </span>
  ) : (
    <span
      className="grid size-5 shrink-0 place-items-center rounded bg-blue-600 text-[11px] font-bold text-white"
      title="Facebook"
    >
      f
    </span>
  );
}
