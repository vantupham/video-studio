import { useEffect, useState } from "react";
import { useApi } from "../lib/backendContext";
import type { Branding } from "../lib/types";
import { Button, Card, ErrorBox } from "./ui";

const POSITIONS: { value: Branding["logo_position"]; label: string }[] = [
  { value: "top-left", label: "Trên trái" },
  { value: "top-right", label: "Trên phải" },
  { value: "bottom-left", label: "Dưới trái" },
  { value: "bottom-right", label: "Dưới phải" },
];

export function BrandingSettings() {
  const api = useApi();
  const [value, setValue] = useState<Branding | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    api
      .getBranding()
      .then(setValue)
      .catch((e: Error) => setError(e.message));
  }, [api]);

  if (!value)
    return error ? (
      <ErrorBox>{error}</ErrorBox>
    ) : (
      <p className="text-sm text-slate-500">Đang tải…</p>
    );

  const set = <K extends keyof Branding>(key: K, v: Branding[K]) => {
    setSaved(false);
    setValue({ ...value, [key]: v });
  };

  async function upload(file: File | null) {
    if (!file) return;
    setError(null);
    try {
      const asset = await api.uploadAsset(file, "logo");
      setValue((v) =>
        v
          ? {
              ...v,
              logo_asset_id: asset.id,
              logo_url: asset.url,
              enabled: true,
            }
          : v,
      );
      setSaved(false);
    } catch (e) {
      setError((e as Error).message);
    }
  }

  async function save() {
    if (!value) return;
    setError(null);
    try {
      setValue(await api.saveBranding(value));
      setSaved(true);
    } catch (e) {
      setError((e as Error).message);
    }
  }

  return (
    <Card title="Thương hiệu">
      <div className="space-y-4">
        <p className="text-sm text-slate-600">
          Logo và lời mở đầu/kết thúc được thêm vào mọi video khi bật.
        </p>
        <ErrorBox>{error}</ErrorBox>
        <label className="flex items-center gap-2 text-sm font-medium">
          <input
            type="checkbox"
            className="size-4 accent-indigo-600"
            checked={value.enabled}
            onChange={(e) => set("enabled", e.target.checked)}
          />
          Bật thương hiệu
        </label>
        <div className="flex flex-wrap items-center gap-4">
          {value.logo_url ? (
            <img
              src={api.mediaUrl(value.logo_url)}
              alt="Logo"
              className="h-16 max-w-40 rounded border border-slate-200 bg-[repeating-conic-gradient(#f1f5f9_0_25%,#fff_0_50%)] bg-[length:16px_16px] object-contain p-1"
            />
          ) : (
            <span className="text-sm text-slate-500">Chưa có logo</span>
          )}
          <label className="text-sm">
            <span className="sr-only">Tải logo</span>
            <input
              type="file"
              accept=".png,.jpg,.jpeg,.webp"
              className="text-sm file:mr-3 file:rounded-lg file:border-0 file:bg-indigo-50 file:px-3 file:py-2 file:text-sm file:font-medium file:text-indigo-700"
              onChange={(e) => upload(e.target.files?.[0] ?? null)}
            />
          </label>
          {value.logo_asset_id && (
            <Button variant="ghost" className="text-xs" onClick={() => set("logo_asset_id", null)}>
              Bỏ logo
            </Button>
          )}
        </div>
        <div className="grid gap-4 sm:grid-cols-3">
          <div className="flex flex-col gap-1">
            <label htmlFor="logo-pos" className="text-sm font-medium">
              Vị trí logo
            </label>
            <select
              id="logo-pos"
              className="input"
              value={value.logo_position}
              onChange={(e) => set("logo_position", e.target.value as Branding["logo_position"])}
            >
              {POSITIONS.map((p) => (
                <option key={p.value} value={p.value}>
                  {p.label}
                </option>
              ))}
            </select>
          </div>
          <div className="flex flex-col gap-1">
            <label htmlFor="logo-scale" className="text-sm font-medium">
              Kích thước ({Math.round(value.logo_scale * 100)}% chiều rộng)
            </label>
            <input
              id="logo-scale"
              type="range"
              min={0.04}
              max={0.3}
              step={0.01}
              value={value.logo_scale}
              onChange={(e) => set("logo_scale", Number(e.target.value))}
            />
          </div>
          <div className="flex flex-col gap-1">
            <label htmlFor="logo-opacity" className="text-sm font-medium">
              Độ đậm ({Math.round(value.logo_opacity * 100)}%)
            </label>
            <input
              id="logo-opacity"
              type="range"
              min={0.1}
              max={1}
              step={0.05}
              value={value.logo_opacity}
              onChange={(e) => set("logo_opacity", Number(e.target.value))}
            />
          </div>
        </div>
        <div className="grid gap-4 sm:grid-cols-3">
          <div className="flex flex-col gap-1">
            <label htmlFor="intro-text" className="text-sm font-medium">
              Màn hình mở đầu
            </label>
            <input
              id="intro-text"
              className="input"
              placeholder="Để trống nếu không dùng"
              value={value.intro_text}
              onChange={(e) => set("intro_text", e.target.value)}
            />
          </div>
          <div className="flex flex-col gap-1">
            <label htmlFor="outro-text" className="text-sm font-medium">
              Màn hình kết thúc
            </label>
            <input
              id="outro-text"
              className="input"
              placeholder="Ví dụ: Cảm ơn đã theo dõi!"
              value={value.outro_text}
              onChange={(e) => set("outro_text", e.target.value)}
            />
          </div>
          <div className="flex flex-col gap-1">
            <label htmlFor="card-seconds" className="text-sm font-medium">
              Thời lượng mỗi màn (giây)
            </label>
            <input
              id="card-seconds"
              type="number"
              className="input"
              min={1}
              max={10}
              step={0.5}
              value={value.card_seconds}
              onChange={(e) => set("card_seconds", Number(e.target.value))}
            />
          </div>
        </div>
        <div className="flex items-center gap-3">
          <Button onClick={save}>Lưu</Button>
          {saved && <span className="text-sm text-emerald-700">✓ Đã lưu</span>}
        </div>
      </div>
    </Card>
  );
}
