import type { Capability } from "./types";

export const JOB_TYPE_LABELS: Record<string, string> = {
  ingest: "Phân tích nội dung (ingest)",
  demo: "Job demo",
  script: "Viết kịch bản & storyboard",
  scene_regen: "Tạo lại cảnh",
  tts: "Tạo giọng đọc",
  render: "Dựng video",
  publish: "Đăng video",
};

export const CAPABILITY_INFO: Record<Capability, { label: string; hint: string }> = {
  llm: {
    label: "Mô hình ngôn ngữ (LLM)",
    hint: "Viết kịch bản và storyboard từ tài liệu.",
  },
  tts: { label: "Giọng đọc (TTS)", hint: "Đọc lời thuyết minh tiếng Việt." },
  alignment: {
    label: "Căn thời gian phụ đề",
    hint: "Dùng khi giọng đọc không trả về mốc thời gian từng từ.",
  },
  media: {
    label: "Kho hình ảnh",
    hint: "Tìm ảnh minh hoạ cho cảnh kiểu “stock”.",
  },
  ocr: {
    label: "Nhận dạng chữ (OCR)",
    hint: "Đọc chữ trong PDF scan / ảnh chụp (tiếng Việt có dấu).",
  },
};

export const VIDEO_STATUS_STYLES: Record<string, string> = {
  draft: "bg-slate-100 text-slate-700",
  generating: "bg-sky-100 text-sky-800",
  rendered: "bg-indigo-100 text-indigo-800",
  in_review: "bg-violet-100 text-violet-800",
  changes_requested: "bg-amber-100 text-amber-800",
  approved: "bg-emerald-100 text-emerald-800",
  scheduled: "bg-cyan-100 text-cyan-800",
  publishing: "bg-sky-100 text-sky-800",
  published: "bg-green-600 text-white",
  failed: "bg-rose-100 text-rose-800",
};

export const VIDEO_STATUS_LABELS: Record<string, string> = {
  draft: "Bản nháp",
  generating: "Đang dựng",
  rendered: "Đã dựng",
  in_review: "Đang duyệt",
  changes_requested: "Cần sửa",
  approved: "Đã duyệt",
  scheduled: "Đã lên lịch",
  publishing: "Đang đăng",
  published: "Đã đăng",
  failed: "Lỗi",
};

export const PUBLICATION_STATUS: Record<string, { label: string; style: string }> = {
  queued: { label: "Chờ đăng", style: "bg-slate-100 text-slate-700" },
  uploading: { label: "Đang tải lên", style: "bg-sky-100 text-sky-800" },
  processing: { label: "Nền tảng đang xử lý", style: "bg-sky-100 text-sky-800" },
  scheduled: { label: "Đã lên lịch (trong app)", style: "bg-cyan-100 text-cyan-800" },
  scheduled_remote: { label: "Đã lên lịch trên nền tảng", style: "bg-cyan-100 text-cyan-800" },
  published: { label: "Đã đăng", style: "bg-emerald-100 text-emerald-800" },
  failed: { label: "Thất bại", style: "bg-rose-100 text-rose-800" },
  missed: { label: "Lỡ lịch", style: "bg-amber-100 text-amber-800" },
  cancelled: { label: "Đã huỷ", style: "bg-slate-100 text-slate-500" },
  retry_wait: { label: "Chờ thử lại", style: "bg-amber-100 text-amber-800" },
};

export const PLATFORM_LABELS: Record<string, string> = {
  youtube: "YouTube",
  facebook: "Facebook Page",
};

export const EVENT_LABELS: Record<string, string> = {
  render_started: "Bắt đầu dựng",
  render_done: "Dựng xong",
  render_failed: "Dựng lỗi",
  render_interrupted: "Dựng bị gián đoạn",
  variant_rendered: "Dựng bản tỉ lệ khác",
  variant_failed: "Dựng bản tỉ lệ khác lỗi",
  submit: "Gửi duyệt",
  approve: "Duyệt",
  request_changes: "Yêu cầu sửa",
  reopen: "Mở lại để duyệt",
  publication_created: "Tạo bài đăng",
  publication_missed: "Lỡ lịch đăng",
  publication_scheduled: "Đã lên lịch",
  publication_publishing: "Đang đăng",
  publication_published: "Đã đăng",
  publication_failed: "Đăng thất bại",
  publication_approved: "Huỷ lịch — trở về Đã duyệt",
};
