# Video Studio

Ứng dụng **desktop cá nhân** (ưu tiên Windows) biến nội dung đầu vào — kịch bản, Markdown,
DOCX, PDF, website cần đăng nhập — thành **video có thuyết minh và phụ đề** theo nhiều phong
cách, cho phép xem lại / duyệt rồi tự động đăng lên mạng xã hội (YouTube, Facebook Page,
TikTok, Instagram…).

> **Trạng thái: Giai đoạn 2 (duyệt và đăng).** Nạp kịch bản, Markdown (kèm ảnh), **DOCX**,
> **PDF** (trang scan được OCR tiếng Việt bằng Tesseract kèm sẵn) → LLM viết kịch bản +
> storyboard → giọng đọc tiếng Việt + phụ đề căn khớp → render FFmpeg (16:9 / 9:16, crossfade,
> Ken Burns) → **duyệt** (dải cảnh bấm được, sửa từng cảnh + dựng lại một phần, yêu cầu sửa kèm
> ghi chú, lịch sử phiên bản + so sánh) → **đăng YouTube và Facebook Page** (ứng dụng OAuth của
> chính bạn, metadata gợi ý bằng AI, thumbnail, phụ đề, Reels) ngay hoặc **hẹn giờ**; ứng dụng
> chạy nền dưới khay hệ thống, tự khởi động cùng Windows. Nhiều **nhà cung cấp** cho mỗi chức
> năng (OpenAI, Claude, Gemini, Ollama, Edge TTS, Azure, Google, ElevenLabs, FPT.AI, Piper,
> Tesseract, Google Vision…); khoá API và token lưu bằng DPAPI.

- Kiến trúc chi tiết: [`docs/architecture.md`](docs/architecture.md)
- Nhà cung cấp AI, OCR, chi phí, viết plugin: [`docs/providers.md`](docs/providers.md)
- Kết nối YouTube: [`docs/publishing/youtube.md`](docs/publishing/youtube.md) · Facebook Page:
  [`docs/publishing/facebook.md`](docs/publishing/facebook.md)
- Lộ trình: [`docs/roadmap.md`](docs/roadmap.md)

## Dùng nhanh

1. **Dự án → Tạo dự án**, thêm nguồn (dán kịch bản; chọn file `.md`, `.docx`, `.pdf`; hoặc
   thư mục / zip / nhiều file cho Markdown kèm ảnh), bấm **Chạy phân tích**. PDF dạng ảnh được
   nhận dạng chữ (OCR) tự động.
2. Ở thẻ **Tạo video**: chọn kiểu (16:9 / 9:16), thời lượng, giọng văn, nhà cung cấp LLM và
   giọng đọc (▶ Nghe thử), nhạc nền → **Tạo kịch bản**.
3. Trong trình chỉnh sửa storyboard: sửa lời thoại, kiểu hình (thẻ tiêu đề, gạch đầu dòng, ảnh
   tải lên, ảnh kho, nền màu), thời lượng; thêm / xoá / đổi thứ tự cảnh; **↻ Tạo lại cảnh** với
   yêu cầu cho AI. Chỉ cảnh bị sửa mới đọc lại và dựng lại.
4. **Xem nhanh** (độ phân giải thấp) hoặc **Dựng video** — theo dõi tiến độ từng bước, xem
   video kèm phụ đề ngay trong app, tải MP4/SRT/VTT/ASS hoặc **Mở thư mục**.
5. **Duyệt & đăng** (hoặc **Thư viện video** → chọn video): xem video, bấm cảnh trên dải thời
   gian để nhảy tới, **Sửa cảnh** → **Dựng lại** (chỉ cảnh đổi), **Yêu cầu sửa** kèm ghi chú hoặc
   **Duyệt**. Phần **Lịch sử** ghi mọi thay đổi; danh sách phiên bản có nút **So sánh**.
6. Tab **Đăng**: chọn tài khoản YouTube / Trang Facebook, sửa tiêu đề / mô tả / thẻ cho từng
   nền tảng (**Gợi ý bằng AI**), thumbnail, chọn **Đăng ngay** hoặc hẹn giờ → **Kiểm tra** →
   **Đăng** / **Lên lịch đăng**. Theo dõi ở **Lịch đăng**; đăng xong có thông báo + link bài.

Kết nối tài khoản ở **Cài đặt → Tài khoản mạng xã hội** — bạn tạo ứng dụng Google / Meta của
riêng mình (miễn phí) theo hướng dẫn [YouTube](docs/publishing/youtube.md) /
[Facebook](docs/publishing/facebook.md). Muốn thử quy trình trước: **+ YouTube giả lập** /
**+ Facebook giả lập** (không đăng gì lên mạng).

Không có khoá API nào vẫn dùng được: mặc định là **Chế độ demo** (LLM giả lập) + **Edge TTS**
miễn phí. Thêm khoá ở **Cài đặt → Nhà cung cấp**.

## Kiến trúc tóm tắt

| Thành phần | Công nghệ |
|------------|-----------|
| Vỏ ứng dụng | Tauri v2 (Rust) + WebView2 |
| Giao diện | React + TypeScript + Vite + Tailwind, tiếng Việt |
| Backend | Python 3.11+ FastAPI, chạy như *sidecar*, đóng gói PyInstaller `--onedir` |
| Lưu trữ | SQLite (WAL) + SQLAlchemy 2 + Alembic |
| Job | Hàng đợi bền vững trong SQLite + worker trong tiến trình, tiến độ qua WebSocket |
| Bí mật | DPAPI trên Windows (file mã hoá), Fernet trên Linux/macOS khi dev |
| Mở rộng | Plugin + Content IR + Storyboard (JSON Schema trong `packages/ir-schema`) |
| Nhà cung cấp | LLM / TTS / căn phụ đề / ảnh — nhiều bản, chọn mặc định, gọi bằng httpx |
| Render | FFmpeg (thẻ vẽ bằng Pillow, phụ đề libass, font OFL Be Vietnam Pro), encoder phần cứng nếu có |

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
  — và `gstreamer1.0-libav` để trình phát video trong cửa sổ Tauri (WebKitGTK) giải mã được
  H.264 (Windows dùng WebView2, không cần)
- `ffmpeg` (có libass) trong `PATH` khi dev trên Linux/macOS để render video
  (`sudo apt install ffmpeg`); trên Windows `scripts\fetch-ffmpeg.ps1` tải sẵn
- `tesseract` để OCR PDF scan khi dev trên Linux/macOS (`sudo apt install tesseract-ocr`); trên
  Windows `scripts\fetch-tesseract.ps1` tải sẵn (cần 7-Zip)
- (Tuỳ chọn) giọng đọc / căn phụ đề offline: `cd backend && uv sync --extra offline` (Piper),
  `uv sync --extra whisper` (faster-whisper)

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

Test render thật (cả hai phong cách) chạy khi có `ffmpeg`; đặt `VIDEOSTUDIO_REQUIRE_FFMPEG=1`
để bắt buộc (CI làm vậy). Tương tự test OCR thật cần `tesseract`
(`VIDEOSTUDIO_REQUIRE_TESSERACT=1`). File DOCX / PDF / PDF scan dùng trong test được tạo lúc
chạy test (`tests/fixtures/make_fixtures.py`), không commit. Đăng YouTube / Facebook được test
bằng mock HTTP (respx); test với tài khoản thật: xem
[`docs/publishing/`](docs/publishing/youtube.md#kiểm-thử-với-tài-khoản-thật-dành-cho-nhà-phát-triển)
(`YOUTUBE_CLIENT_ID`, `YOUTUBE_CLIENT_SECRET`, `YOUTUBE_REFRESH_TOKEN`, `FACEBOOK_APP_ID`,
`FACEBOOK_APP_SECRET`, `FACEBOOK_PAGE_TOKEN`). Test gọi dịch vụ thật chỉ chạy khi có khoá trong biến môi trường
(`uv run pytest -m live`): `VIDEOSTUDIO_LIVE_EDGE=1`, `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`,
`GEMINI_API_KEY`, `OLLAMA_URL`, `AZURE_SPEECH_KEY` + `AZURE_SPEECH_REGION`,
`GOOGLE_TTS_API_KEY`, `ELEVENLABS_API_KEY` + `ELEVENLABS_VOICE_ID`, `FPT_AI_KEY`,
`PEXELS_API_KEY`.

Smoke test bản đóng gói (giống CI): `uv run python ../scripts/smoke_sidecar.py
<đường dẫn sidecar> --render --documents <thư mục fixture> --publish` — chạy sidecar, tạo
storyboard bằng LLM demo và render cả hai phong cách với giọng giả lập, nạp DOCX / PDF / PDF
scan (OCR), rồi duyệt và đăng lên tài khoản YouTube giả lập. Tạo thư mục fixture bằng
`uv run python tests/fixtures/make_fixtures.py <thư mục>`.

## Build bộ cài Windows

Trên máy Windows có đủ công cụ dev:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\build-installer.ps1
```

Script sẽ: build sidecar PyInstaller `--onedir` → chép vào
`apps\desktop\src-tauri\binaries\backend\`, tải FFmpeg vào `binaries\ffmpeg\` và Tesseract vào
`binaries\tesseract\`, smoke-test
sidecar, rồi `tauri build --bundles nsis`. Kết quả:
`apps\desktop\src-tauri\target\release\bundle\nsis\Video Studio_<phiên bản>_x64-setup.exe`.

Bộ cài cũng được CI build tự động (job `windows` trong `.github/workflows/ci.yml`) và tải lên
dưới dạng artifact **VideoStudio-windows-x64-setup**.

- Cài theo người dùng (`currentUser`), không cần quyền admin, vào
  `%LOCALAPPDATA%\Video Studio`.
- Dữ liệu ứng dụng nằm ở `%LOCALAPPDATA%\VideoStudio`.
- Đóng cửa sổ chỉ thu nhỏ xuống **khay hệ thống** (để lịch đăng vẫn chạy); thoát hẳn bằng menu
  khay → **Thoát**. Tắt / bật trong **Cài đặt → Hệ thống**, cùng tuỳ chọn **Khởi động cùng
  Windows**.
- **Bộ cài chưa được ký số**: Windows SmartScreen sẽ hiện "Windows protected your PC" — bấm
  **More info → Run anyway**. Ký số sẽ được bổ sung sau.

## Cấu trúc thư mục

```
video-studio/
├── apps/
│   └── desktop/              # Tauri v2 + React + TypeScript + Vite + Tailwind
│       ├── src/              # giao diện (Dự án, Chi tiết dự án, Storyboard, Cài đặt)
│       └── src-tauri/        # vỏ Rust: chạy sidecar, cấu hình bundle NSIS
│           └── binaries/     # sidecar + ffmpeg được script tạo ra (không commit)
├── backend/                  # FastAPI sidecar (pyproject.toml, uv.lock)
│   ├── videostudio/          # api/, db/ (Alembic), jobs/, plugins/, ir/, secretstore/, media/,
│   │                         # providers/ (llm, tts, alignment, media), pipeline/ (kịch bản,
│   │                         # TTS, phụ đề, renderer FFmpeg), styles_builtin/, assets/fonts/
│   ├── packaging/            # PyInstaller spec (onedir)
│   └── tests/                # pytest (có fixture Markdown tiếng Việt)
├── packages/
│   └── ir-schema/            # JSON Schema Content IR + Storyboard + kiểu TypeScript + ví dụ
├── scripts/                  # dev.sh / dev.ps1, build-sidecar, fetch-ffmpeg, build-installer…
├── docs/                     # kiến trúc, nhà cung cấp, lộ trình, plugin mẫu (examples/)
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
| `VIDEOSTUDIO_TESSERACT_DIR` | thư mục chứa `tesseract` (+ `tessdata/` đi kèm) |
| `VIDEOSTUDIO_NO_TRAY=1` | (app desktop) đóng cửa sổ là thoát, không xuống khay |
| `VIDEOSTUDIO_BACKEND_URL`, `VIDEOSTUDIO_BACKEND_TOKEN` | (dev) cho app Tauri dùng backend chạy sẵn |

## Lộ trình

1. **Giai đoạn 1 (đã xong):** nhiều nhà cung cấp LLM/TTS, kịch bản + storyboard sửa được,
   phụ đề căn khớp, 2 phong cách (giải thích 16:9, video ngắn 9:16), render FFmpeg.
2. **Giai đoạn 2 (đã xong):** DOCX/PDF + OCR, duyệt video + lịch sử phiên bản, crossfade / Ken
   Burns, đăng YouTube + Facebook Page có hẹn giờ, chạy ở khay hệ thống, tự khởi động.
3. **Giai đoạn 3:** đăng nhập website + quay màn hình bằng Playwright với Microsoft Edge đã cài
   (`channel="msedge"`), recipe YAML, thông tin đăng nhập lưu bằng DPAPI, OTP, làm mờ trường
   nhạy cảm, phong cách hướng dẫn zoom theo cú click.
4. **Giai đoạn 4:** TikTok/Instagram/LinkedIn/X, Whisper/TTS chạy cục bộ kèm kiểm tra phần
   cứng, video stock, avatar, đa ngôn ngữ, xuất/nhập dự án, tự động cập nhật.

Chi tiết: [`docs/roadmap.md`](docs/roadmap.md).

## Giấy phép

Dự án cá nhân. FFmpeg được tải về khi build và tuân theo giấy phép của FFmpeg (GPL/LGPL tuỳ
bản build). Font Be Vietnam Pro (`backend/videostudio/assets/fonts/`) theo giấy phép SIL Open
Font License 1.1 (`OFL.txt`). Edge TTS dùng dịch vụ không chính thức của Microsoft qua thư viện
`edge-tts` (LGPL-3.0).

**PyMuPDF** (đọc PDF) theo giấy phép **AGPL-3.0** (hoặc giấy phép thương mại của Artifex). Ứng
dụng cá nhân dùng nội bộ không bị ảnh hưởng, nhưng nếu phân phối Video Studio (kể cả bản cài)
cho người khác thì phải công khai mã nguồn theo AGPL, hoặc mua giấy phép thương mại, hoặc thay
adapter PDF bằng thư viện khác (ví dụ `pypdfium2`, Apache-2.0/BSD). Tesseract OCR (Apache-2.0)
và dữ liệu `tessdata_fast` (Apache-2.0) được tải về khi build; python-docx theo MIT.
