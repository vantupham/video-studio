import type { Capability } from "./types";

export const JOB_TYPE_LABELS: Record<string, string> = {
  ingest: "Phân tích nội dung (ingest)",
  demo: "Job demo",
  script: "Viết kịch bản & storyboard",
  scene_regen: "Tạo lại cảnh",
  tts: "Tạo giọng đọc",
  render: "Dựng video",
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
};
