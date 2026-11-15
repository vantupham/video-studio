# Lộ trình phát triển

## Giai đoạn 0 — Khung dự án (đã xong)

- Monorepo: Tauri v2 + React/TS (UI tiếng Việt), backend FastAPI làm sidecar, gói `ir-schema`.
- Bảo mật cục bộ: chỉ nghe 127.0.0.1, cổng ngẫu nhiên, token mỗi lần chạy.
- SQLite (WAL) + Alembic, hàng đợi job bền vững, WebSocket tiến độ.
- Kiến trúc plugin + Content IR; adapter `script` và `markdown`.
- SecretStore (DPAPI), tiện ích FFmpeg (tìm binary, `probe`, phát hiện encoder phần cứng).
- CI: test Linux + Windows, build sidecar PyInstaller onedir, bộ cài NSIS (per-user).

## Giai đoạn 1 — Tạo video đầu tiên (đã xong)

- Hệ thống nhà cung cấp: LLM (OpenAI, Anthropic, Gemini, Ollama, OpenAI-compatible, demo),
  TTS (Edge TTS, Azure, Google, ElevenLabs, FPT.AI, OpenAI, Piper, giả lập), căn phụ đề
  (âm tiết, faster-whisper), ảnh (Pexels). Nhiều bản / chức năng, mặc định đổi được ngay, dự án
  chọn riêng; khoá API chỉ trong SecretStore, API trả dạng `••••1234`. Plugin bên thứ ba.
- LLM viết kịch bản + storyboard từ Content IR (JSON Schema, kiểm tra Pydantic, tự sửa một
  lần); prompt Jinja theo phong cách, người dùng ghi đè được; tuỳ chọn dùng nguyên văn.
- Trình chỉnh sửa storyboard: sửa lời, hình, thời lượng, thêm/xoá/đổi thứ tự, viết lại một cảnh
  bằng AI; chỉ cảnh bị sửa mới đọc lại / dựng lại (cache âm thanh + đoạn video theo nội dung).
- Phụ đề SRT/VTT/ASS căn khớp (timing từ TTS → căn thời gian → ước lượng âm tiết), tô sáng
  từng từ cho video ngắn; burn-in bằng libass.
- Renderer FFmpeg (interface `Renderer`): thẻ vẽ bằng Pillow, font OFL, nhạc nền tự nhỏ lại khi
  có lời, logo + màn hình mở đầu/kết thúc, encoder phần cứng → lùi về libx264, xem nhanh độ
  phân giải thấp. 2 phong cách: **giải thích 16:9** và **video ngắn 9:16**.
- UI: Cài đặt → Nhà cung cấp / Thương hiệu, thẻ Tạo video, trình chỉnh sửa storyboard, tiến độ
  từng bước, trình phát video kèm phụ đề, Mở thư mục.

## Giai đoạn 2 — Nhiều nguồn hơn, duyệt và đăng (hiện tại, đã xong)

- Adapter **DOCX** (python-docx) và **PDF** (PyMuPDF) — trang scan được **OCR từng trang**;
  chức năng nhà cung cấp mới **OCR** (Tesseract kèm trong bộ cài Windows với mô hình tiếng Việt,
  Google Cloud Vision, Azure AI Vision). Markdown kèm ảnh: thư mục, zip hoặc nhiều file.
- **Duyệt video**: vòng đời Bản nháp → Đã dựng → Chờ duyệt ⇄ Cần sửa → Đã duyệt → Đã lên lịch →
  Đang đăng → Đã đăng / Lỗi, có lịch sử. Màn Duyệt: trình phát với dải cảnh bấm được, sửa từng
  cảnh + dựng lại một phần, Duyệt / Yêu cầu sửa kèm ghi chú, lịch sử phiên bản + so sánh.
  Trang **Thư viện video** có bộ lọc.
- **Đăng YouTube + Facebook Page** bằng ứng dụng của chính người dùng (OAuth PKCE loopback /
  Facebook Login trong cửa sổ app), upload resumable / chunked, Reels, thumbnail, phụ đề, danh
  sách phát, metadata theo nền tảng có gợi ý bằng AI, kiểm tra trước khi đăng + dựng lại đúng
  tỉ lệ, thử lại / tiếp tục / theo dõi trạng thái, link bài đăng, thông báo.
- **Hẹn giờ và chạy nền**: lịch đăng (tại máy hoặc trên nền tảng), khay hệ thống (mở / tạm dừng
  hàng đợi / thoát), đóng cửa sổ xuống khay, tự khởi động cùng Windows, giữ máy không ngủ khi
  dựng / đăng, hỏi lại các lịch bị lỡ khi mở app, tuỳ chọn task đánh thức máy.
- Chuyển cảnh **crossfade** và hiệu ứng **Ken Burns** cho ảnh; nút xoá khoá API đã lưu.

Chưa làm (dời sang giai đoạn sau): video stock, thêm phong cách, kèm Piper / faster-whisper
trong bộ cài.

## Giai đoạn 3 — Website và quay màn hình

- Đăng nhập website và quay màn hình bằng Playwright dùng **Microsoft Edge đã cài sẵn**
  (`channel="msedge"`, không cần tải Chromium).
- Kịch bản thao tác dạng YAML ("recipes"), thông tin đăng nhập lưu bằng DPAPI, hỗ trợ OTP.
- Làm mờ trường nhạy cảm, phong cách hướng dẫn có zoom theo cú click.

## Giai đoạn 4 — Mở rộng

- Đăng TikTok / Instagram / LinkedIn / X.
- Mô hình chạy cục bộ: Whisper, TTS offline, kèm kiểm tra phần cứng.
- Video stock, thêm phong cách, kèm Piper / faster-whisper trong bộ cài (tải khi cần).
- Avatar, đa ngôn ngữ.
- Xuất / nhập dự án, tự động cập nhật ứng dụng.
