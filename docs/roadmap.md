# Lộ trình phát triển

## Giai đoạn 0 — Khung dự án (hiện tại)

- Monorepo: Tauri v2 + React/TS (UI tiếng Việt), backend FastAPI làm sidecar, gói `ir-schema`.
- Bảo mật cục bộ: chỉ nghe 127.0.0.1, cổng ngẫu nhiên, token mỗi lần chạy.
- SQLite (WAL) + Alembic, hàng đợi job bền vững, WebSocket tiến độ.
- Kiến trúc plugin + Content IR; adapter `script` và `markdown`.
- SecretStore (DPAPI), tiện ích FFmpeg (tìm binary, `probe`, phát hiện encoder phần cứng).
- CI: test Linux + Windows, build sidecar PyInstaller onedir, bộ cài NSIS (per-user).

## Giai đoạn 1 — Tạo video đầu tiên

- LLM viết kịch bản từ Content IR (chia cảnh, lời thoại, gợi ý hình ảnh).
- TTS tiếng Việt, lấy timing từng từ → phụ đề căn khớp (SRT/ASS, burn-in).
- 2 phong cách: **giải thích 16:9** và **video ngắn 9:16**.
- Render bằng Remotion (miễn phí cho cá nhân) hoặc FFmpeg; chọn encoder phần cứng nếu có.

## Giai đoạn 2 — Nhiều nguồn hơn, duyệt và đăng

- Adapter DOCX, PDF (+ OCR cho PDF dạng ảnh).
- Giao diện duyệt: sửa từng cảnh (lời, hình, thời lượng), render lại một phần.
- Đăng YouTube + Facebook Page, hẹn giờ đăng.
- Chạy nền ở khay hệ thống (tray), tự khởi động cùng Windows.

## Giai đoạn 3 — Website và quay màn hình

- Đăng nhập website và quay màn hình bằng Playwright dùng **Microsoft Edge đã cài sẵn**
  (`channel="msedge"`, không cần tải Chromium).
- Kịch bản thao tác dạng YAML ("recipes"), thông tin đăng nhập lưu bằng DPAPI, hỗ trợ OTP.
- Làm mờ trường nhạy cảm, phong cách hướng dẫn có zoom theo cú click.

## Giai đoạn 4 — Mở rộng

- Đăng TikTok / Instagram / LinkedIn / X.
- Mô hình chạy cục bộ: Whisper, TTS offline, kèm kiểm tra phần cứng.
- Avatar, tạo thumbnail, đa ngôn ngữ.
- Xuất / nhập dự án, tự động cập nhật ứng dụng.
