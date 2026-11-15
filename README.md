# Video Studio

Ứng dụng **desktop cá nhân** (ưu tiên Windows) biến nội dung đầu vào — kịch bản, Markdown,
DOCX, PDF, website cần đăng nhập — thành **video có thuyết minh và phụ đề** theo nhiều phong
cách, cho phép xem lại / duyệt rồi tự động đăng lên mạng xã hội (YouTube, Facebook Page,
TikTok, Instagram…).

> **Trạng thái: Giai đoạn 0 (khung dự án).** Đã có: quản lý dự án, thêm nguồn (dán kịch bản
> hoặc file `.md`/`.txt`), phân tích thành Content IR qua hàng đợi job với tiến độ trực tiếp,
> trang cài đặt (thư mục dữ liệu, FFmpeg, kiểm tra kho bí mật). Chưa tạo video — xem
> [lộ trình](#lộ-trình).

- Kiến trúc chi tiết: [`docs/architecture.md`](docs/architecture.md)
- Lộ trình: [`docs/roadmap.md`](docs/roadmap.md)

## Kiến trúc tóm tắt

| Thành phần | Công nghệ |
|------------|-----------|
| Vỏ ứng dụng | Tauri v2 (Rust) + WebView2 |
| Giao diện | React + TypeScript + Vite + Tailwind, tiếng Việt |
| Backend | Python 3.11+ FastAPI, chạy như *sidecar*, đóng gói PyInstaller `--onedir` |
| Lưu trữ | SQLite (WAL) + SQLAlchemy 2 + Alembic |
| Job | Hàng đợi bền vững trong SQLite + worker trong tiến trình, tiến độ qua WebSocket |
| Bí mật | DPAPI trên Windows (file mã hoá), Fernet trên Linux/macOS khi dev |
| Mở rộng | Plugin + Content IR (JSON Schema trong `packages/ir-schema`) |

Backend chỉ nghe `127.0.0.1` trên cổng ngẫu nhiên; mỗi lần mở app, Tauri tạo một token ngẫu
nhiên và mọi request phải gửi kèm token này. Tauri khởi chạy sidecar khi mở app và tắt nó khi
thoát (nếu app bị kill, backend tự thoát nhờ watchdog).

## Yêu cầu

**Người dùng:** Windows 10 22H2 hoặc Windows 11, 64-bit (x64). WebView2 thường đã có sẵn; nếu
chưa, bộ cài sẽ tự tải.

**Phát triển:**

- [Node.js LTS](https://nodejs.org/) (22.x)
- [Rust stable](https://rustup.rs/) — trên Windows cần thêm *Visual Studio Build Tools*
  (workload "Desktop development with C++")
- Python 3.11+ và [uv](https://docs.astral.sh/uv/) (uv tự cài Python nếu thiếu)
- Linux: các gói hệ thống của Tauri, ví dụ Ubuntu/Debian:
  `sudo apt install libwebkit2gtk-4.1-dev libsoup-3.0-dev librsvg2-dev libayatana-appindicator3-dev build-essential pkg-config libssl-dev`
- (Tuỳ chọn) `ffmpeg` trong `PATH` khi dev trên Linux/macOS

## Chạy ở chế độ phát triển

Cài phụ thuộc lần đầu:

```powershell
# Windows
powershell -ExecutionPolicy Bypass -File scripts\setup.ps1
```

```bash
# Linux / macOS
scripts/setup.sh
```

Chạy backend (tự reload khi sửa code) + cửa sổ Tauri:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\dev.ps1
```

```bash
scripts/dev.sh
```

Chỉ chạy giao diện trong **trình duyệt** (không cần Rust) — mở <http://localhost:1420>:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\dev.ps1 -Web
```

```bash
scripts/dev.sh --web
```

Ở chế độ dev, backend chạy tại `http://127.0.0.1:8765` với token `dev-token` và lưu dữ liệu
trong `.dev-data/` của repo (đổi bằng `VIDEOSTUDIO_DATA_DIR`). Có thể tự chạy backend:

```bash
cd backend
uv sync
VIDEOSTUDIO_TOKEN=dev-token uv run python -m videostudio --port 8765 --reload
# rồi trong apps/desktop: npm run dev   (giao diện đọc VITE_BACKEND_URL / VITE_BACKEND_TOKEN,
# mặc định http://127.0.0.1:8765 và dev-token; hoặc thêm ?backend=…&token=… vào URL)
```

Tài liệu API tương tác: <http://127.0.0.1:8765/docs> (các request cần header
`Authorization: Bearer dev-token`).

## Kiểm thử

```bash
scripts/check.sh            # chạy tất cả, giống CI Linux
# hoặc từng phần:
cd backend && uv run ruff check . && uv run pytest
cd apps/desktop && npm run typecheck && npm run lint && npm test && npm run build
cd apps/desktop/src-tauri && cargo check
```

## Build bộ cài Windows

Trên máy Windows có đủ công cụ dev:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\build-installer.ps1
```

Script sẽ: build sidecar PyInstaller `--onedir` → chép vào
`apps\desktop\src-tauri\binaries\backend\`, tải FFmpeg vào `binaries\ffmpeg\`, smoke-test
sidecar, rồi `tauri build --bundles nsis`. Kết quả:
`apps\desktop\src-tauri\target\release\bundle\nsis\Video Studio_<phiên bản>_x64-setup.exe`.

Bộ cài cũng được CI build tự động (job `windows` trong `.github/workflows/ci.yml`) và tải lên
dưới dạng artifact **VideoStudio-windows-x64-setup**.

- Cài theo người dùng (`currentUser`), không cần quyền admin, vào
  `%LOCALAPPDATA%\Video Studio`.
- Dữ liệu ứng dụng nằm ở `%LOCALAPPDATA%\VideoStudio`.
- **Bộ cài chưa được ký số**: Windows SmartScreen sẽ hiện "Windows protected your PC" — bấm
  **More info → Run anyway**. Ký số sẽ được bổ sung sau.

## Cấu trúc thư mục

```
video-studio/
├── apps/
│   └── desktop/              # Tauri v2 + React + TypeScript + Vite + Tailwind
│       ├── src/              # giao diện (trang Dự án, Chi tiết dự án, Cài đặt)
│       └── src-tauri/        # vỏ Rust: chạy sidecar, cấu hình bundle NSIS
│           └── binaries/     # sidecar + ffmpeg được script tạo ra (không commit)
├── backend/                  # FastAPI sidecar (pyproject.toml, uv.lock)
│   ├── videostudio/          # api/, db/ (Alembic), jobs/, plugins/, ir/, secretstore/, media/
│   ├── packaging/            # PyInstaller spec (onedir)
│   └── tests/                # pytest (có fixture Markdown tiếng Việt)
├── packages/
│   └── ir-schema/            # JSON Schema Content IR + kiểu TypeScript + ví dụ
├── scripts/                  # dev.sh / dev.ps1, build-sidecar, fetch-ffmpeg, build-installer…
├── docs/                     # kiến trúc, lộ trình
└── .github/workflows/        # CI: Linux (test) + Windows (test, sidecar, bộ cài NSIS)
```

## Biến môi trường

| Biến | Ý nghĩa |
|------|---------|
| `VIDEOSTUDIO_DATA_DIR` | đổi thư mục dữ liệu (mặc định `%LOCALAPPDATA%\VideoStudio`) |
| `VIDEOSTUDIO_TOKEN` | token API của backend (Tauri tự đặt mỗi lần chạy) |
| `VIDEOSTUDIO_DEV=1` | cho phép token `dev-token`, mở CORS cho localhost |
| `VIDEOSTUDIO_WORKERS` | số worker xử lý job (mặc định 2) |
| `VIDEOSTUDIO_FFMPEG_DIR` | thư mục chứa `ffmpeg`/`ffprobe` |
| `VIDEOSTUDIO_BACKEND_URL`, `VIDEOSTUDIO_BACKEND_TOKEN` | (dev) cho app Tauri dùng backend chạy sẵn |

## Lộ trình

1. **Giai đoạn 1:** LLM viết kịch bản, TTS tiếng Việt, phụ đề căn khớp, 2 phong cách
   (giải thích 16:9, video ngắn 9:16), render bằng Remotion (miễn phí cho cá nhân) hoặc FFmpeg.
2. **Giai đoạn 2:** DOCX/PDF + OCR, giao diện duyệt sửa từng cảnh và render lại một phần,
   đăng YouTube + Facebook Page có hẹn giờ, chạy ở khay hệ thống, tự khởi động.
3. **Giai đoạn 3:** đăng nhập website + quay màn hình bằng Playwright với Microsoft Edge đã cài
   (`channel="msedge"`), recipe YAML, thông tin đăng nhập lưu bằng DPAPI, OTP, làm mờ trường
   nhạy cảm, phong cách hướng dẫn zoom theo cú click.
4. **Giai đoạn 4:** TikTok/Instagram/LinkedIn/X, Whisper/TTS chạy cục bộ kèm kiểm tra phần
   cứng, avatar, thumbnail, đa ngôn ngữ, xuất/nhập dự án, tự động cập nhật.

Chi tiết: [`docs/roadmap.md`](docs/roadmap.md).

## Giấy phép

Dự án cá nhân. FFmpeg được tải về khi build và tuân theo giấy phép của FFmpeg (GPL/LGPL tuỳ
bản build).
