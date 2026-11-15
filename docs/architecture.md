# Kiến trúc Video Studio

Video Studio là ứng dụng **desktop cá nhân, một người dùng, ưu tiên Windows**. Ứng dụng nhận
nội dung đầu vào (kịch bản, Markdown, DOCX, PDF, website cần đăng nhập…), biến thành video có
thuyết minh và phụ đề theo nhiều phong cách, cho phép xem lại / duyệt, rồi tự động đăng lên
mạng xã hội (YouTube, Facebook Page, TikTok, Instagram…).

## Tổng quan

```
┌───────────────────────── Tauri v2 (Rust) ─────────────────────────┐
│  WebView2: React + TypeScript + Vite + Tailwind (giao diện tiếng Việt)│
│        │  HTTP + WebSocket tới 127.0.0.1:<cổng ngẫu nhiên>          │
│        │  header  Authorization: Bearer <token mỗi lần chạy>        │
│        ▼                                                            │
│  Sidecar: videostudio-backend.exe (Python FastAPI, PyInstaller onedir)│
│        ├── SQLite (WAL) + SQLAlchemy 2 + Alembic                     │
│        ├── Hàng đợi job bền vững (bảng jobs) + worker trong tiến trình│
│        ├── Plugin: InputAdapter / LLM / TTS / Renderer / Publisher   │
│        ├── SecretStore (DPAPI trên Windows)                          │
│        └── FFmpeg (đóng gói kèm hoặc lấy từ PATH)                     │
└────────────────────────────────────────────────────────────────────┘
```

### Vỏ ứng dụng (Tauri v2)

- `apps/desktop/src-tauri/src/lib.rs` khởi chạy sidecar khi mở app và **kill khi thoát**
  (`RunEvent::Exit`).
- Token ngẫu nhiên 48 ký tự được tạo mỗi lần chạy và truyền cho backend qua biến môi trường
  `VIDEOSTUDIO_TOKEN` (không qua dòng lệnh để không lộ trong danh sách tiến trình).
- Backend tự bind một cổng trống trên `127.0.0.1` (`--port 0`) và in `VIDEOSTUDIO_PORT=<cổng>`
  ra stdout; Rust đọc dòng này rồi trả `{ base_url, token }` cho UI qua lệnh `backend_info`.
  Cách này không có race condition khi chọn cổng.
- Nếu Tauri bị kill đột ngột, backend tự thoát nhờ watchdog theo dõi `VIDEOSTUDIO_PARENT_PID`.
- Chế độ dev: đặt `VIDEOSTUDIO_BACKEND_URL` (+ `VIDEOSTUDIO_BACKEND_TOKEN`) để dùng backend
  chạy riêng có auto-reload thay vì sidecar.

### Đóng gói sidecar (PyInstaller `--onedir`)

Không dùng `--onefile` vì file onefile giải nén vào `%TEMP%` mỗi lần chạy, chậm và dễ bị
antivirus báo nhầm. Cách đóng gói đã chọn:

1. `scripts/build-sidecar.ps1` build `backend/packaging/videostudio-backend.spec` →
   `backend/dist/videostudio-backend/{videostudio-backend.exe, _internal/}`.
2. Thư mục đó được chép vào `apps/desktop/src-tauri/binaries/backend/`.
3. `tauri.conf.json` khai báo **resources** `"binaries/backend/": "backend/"` (và
   `"binaries/ffmpeg/": "ffmpeg/"`), nên bộ cài NSIS đặt chúng vào
   `<thư mục cài>\backend\` và `<thư mục cài>\ffmpeg\`.
4. Rust tìm `resource_dir()/backend/videostudio-backend.exe` và chạy bằng
   `std::process::Command` (cờ `CREATE_NO_WINDOW` để không hiện cửa sổ console).

Không dùng `externalBin` vì nó chỉ chép một file exe (cần hậu tố target-triple) chứ không chép
thư mục `_internal`. Bản build onedir là **một tiến trình duy nhất**, nên kill tiến trình là đủ.
Spec bật chế độ UTF-8 của Python (`X utf8`), Rust cũng đặt `PYTHONUTF8=1`.

CI (`.github/workflows/ci.yml`, job `windows`) build sidecar, chạy smoke test
(`scripts/smoke_sidecar.py`), tải FFmpeg, build bộ cài NSIS, rồi **cài thử im lặng** và kiểm tra
app thật sự khởi chạy / dừng sidecar (`scripts/verify-installer.ps1`).

### Bộ cài Windows

- NSIS, `installMode: currentUser` (không cần quyền admin), cài vào
  `%LOCALAPPDATA%\Video Studio`.
- WebView2: `downloadBootstrapper` (tải khi cần; Windows 10 22H2/11 thường đã có sẵn).
- Chưa ký số → Windows SmartScreen sẽ cảnh báo lần đầu.

## Lưu trữ

Thư mục dữ liệu: `%LOCALAPPDATA%\VideoStudio` (Windows), theo `platformdirs` trên Linux/macOS,
có thể đổi bằng biến môi trường `VIDEOSTUDIO_DATA_DIR`.

```
VideoStudio/
  app.db            SQLite (WAL), migration bằng Alembic khi khởi động
  projects/<id>/    sources/<source_id>/<file>, ir/<doc_id>.json
  styles/  recipes/  plugins/  cache/  models/  logs/  secrets/
```

Bảng: `projects`, `sources`, `ir_documents`, `jobs`, `job_logs`.

## Hàng đợi job

- Bảng `jobs` với trạng thái `queued | running | succeeded | failed | cancelled`, tiến độ 0–100,
  thông điệp, kết quả, lỗi, số lần chạy; log chi tiết trong `job_logs`.
- Worker là các thread trong tiến trình (mặc định 2, `VIDEOSTUDIO_WORKERS`), mỗi thread có event
  loop asyncio riêng nên plugin có thể viết `async`.
- Khởi động lại: job còn `running` (app chết giữa chừng) được đưa về `queued`.
- Huỷ: job đang chờ bị huỷ ngay; job đang chạy nhận yêu cầu huỷ qua `ctx.check_cancelled()`.
- Tiến độ được đẩy tới UI qua WebSocket `/ws/jobs?token=…` (trình duyệt không gửi được header
  khi mở WebSocket nên token đi qua query string).
- Loại job: `ingest` (adapter → Content IR), `script` (LLM → storyboard), `scene_regen`
  (viết lại một cảnh), `tts` (đọc các cảnh), `render` (dựng video) và `demo`.
- Job nhiều bước ghi danh sách bước vào `payload.steps` (`{id, label, start, end}` theo %)
  để UI hiển thị bước nào đang chạy. Lỗi từ nhà cung cấp được ghi thành thông báo tiếng Việt
  ngắn gọn (không traceback, không chứa khoá).

## Plugin và Content IR

Mọi nguồn nội dung được chuẩn hoá thành **Content IR** (`packages/ir-schema/`):

```
ContentIR { schema_version, title, source{type, uri, lang}, metadata, sections[] }
Section   { id, title, level, blocks[] }
Block.kind ∈ paragraph | heading | image | table | list | code | screen_recording
```

JSON Schema là nguồn chuẩn; backend có model Pydantic tương ứng, UI có kiểu TypeScript; test
đảm bảo ba nơi khớp nhau.

Điểm mở rộng (`backend/videostudio/plugins/base.py`):

| Giao diện           | Phương thức chính                                          | Hiện có |
|---------------------|------------------------------------------------------------|---------|
| `InputAdapter`      | `id`, `can_handle(source)`, `config_schema()`, `ingest()`   | `script`, `markdown` |
| `LLMProvider`       | `complete()`, `list_models()`, `test_connection()`          | 6 (xem `docs/providers.md`) |
| `TTSProvider`       | `list_voices()`, `synthesize()` → audio + timing từng từ    | 8 |
| `AlignmentProvider` | `align()` → timing từng từ                                  | 3 |
| `MediaProvider`     | `search()`, `download()`                                    | 2 |
| `Renderer`          | `render(plan, ctx)` → `RenderResult`                        | `ffmpeg` |
| `Publisher`         | `constraints()`, `publish()`, `status()`                    | chỉ interface |

`config_schema()` trả về JSON Schema để UI tự sinh form cấu hình.

Plugin được nạp từ: (1) registry dựng sẵn, (2) Python entry point nhóm `videostudio.plugins`,
(3) thư mục `plugins/` trong data dir (`<tên>.py` hoặc `<tên>/plugin.py`). Mỗi plugin có hàm
`register(registry)`; plugin lỗi chỉ bị ghi log, không làm hỏng ứng dụng.

## Nhà cung cấp (providers)

`backend/videostudio/providers/` — chi tiết cho người dùng ở [`providers.md`](providers.md).

- **Loại** nhà cung cấp là lớp Python (đăng ký trong plugin registry); **bản** (instance) là
  một dòng trong bảng `provider_instances` (`capability`, `type`, `name`, `config` không bí
  mật, `is_default`) + các khoá trong SecretStore với tên `provider:<id>:<trường>`.
- `ProviderService`: CRUD, kiểm tra, đặt mặc định (một mặc định / chức năng), dựng
  `ProviderContext` (cấu hình + khoá đã giải mã, chỉ trong bộ nhớ) và `resolve(cap, *lựa chọn)`
  — storyboard → dự án → mặc định. Lần chạy đầu tạo sẵn các nhà cung cấp không cần khoá.
- Trường bí mật đánh dấu `x-secret` trong `config_schema()`; khi sửa, giá trị bỏ trống hoặc
  dạng `••••…` giữ nguyên khoá cũ. API chỉ trả dạng che. Thông báo lỗi upstream được lọc bỏ
  chuỗi giống khoá.
- HTTP dùng `httpx` qua `errors.send()` → `ProviderAuthError` (401/403), `ProviderQuotaError`
  (402/429/quota), `ProviderTimeoutError`, `ProviderUnavailableError`…
- LLM luôn trả JSON theo schema (`llm_json.complete_json`): schema được đưa vào prompt + chế độ
  JSON của từng dịch vụ, kiểm tra bằng Pydantic, sửa lại một lần với danh sách lỗi.

## Pipeline tạo video

`backend/videostudio/pipeline/`:

```
Content IR ──script_writer──▶ Storyboard ──tts_step──▶ âm thanh + timing từng từ
                (LLM/Jinja)    (sửa được)    (cache)          │
                                                              ▼
      outputs/<tên>.mp4/.srt/.vtt/.ass/.jpg ◀──renderer_ffmpeg── subtitles + cards
```

- **Storyboard** (`models.py`, JSON Schema `packages/ir-schema/storyboard.schema.json`, kiểu TS
  `ts/storyboard.ts`): `{version, title, language, style, aspect, scenes[]}`, mỗi cảnh
  `{id, narration, visual{type: title_card|text_card|image|stock|solid, …}, overlays,
  duration: số giây | "auto"}`. Test giữ ba nơi khớp nhau.
- **Phong cách** (`styles_builtin/<id>/style.yaml` + `prompts/{system,script,scene}.j2`):
  khung hình, màu, font, cỡ chữ, chế độ phụ đề, nhịp đọc. Người dùng ghi đè bằng thư mục
  `styles/<id>/` trong data dir (file nào có thì dùng file đó, `style.yaml` được trộn).
- **TTS**: cache theo SHA-256 của (nhà cung cấp, giọng, cấu hình ảnh hưởng âm thanh, ngôn ngữ,
  văn bản) trong `cache/tts/`. Timing: từ TTS → `AlignmentProvider` mặc định → ước lượng âm
  tiết; luôn được khớp lại vào từ gốc (`snap_to_text`).
- **Phụ đề**: cắt dòng theo độ rộng và dấu câu; 16:9 dạng dòng, 9:16 tô sáng từng từ (ASS
  `\c` + phóng nhẹ). SRT ghi UTF-8 có BOM (Windows Media Player / Premiere đọc đúng dấu).
- **Renderer FFmpeg**: mỗi cảnh = một đoạn video câm (thẻ PNG vẽ bằng Pillow, libx264
  ultrafast, cache theo nội dung trong `work/segments`) + WAV 48 kHz; nối bằng concat demuxer;
  lần cuối: burn phụ đề (`subtitles=…:fontsdir=…`), logo, nhạc nền với `sidechaincompress`
  (tự nhỏ lại khi có lời), thử encoder phần cứng (NVENC/QSV/AMF) rồi lùi về libx264. Chế độ
  xem nhanh: nửa độ phân giải, ≤ 24 fps, chỉ libx264. Huỷ job sẽ dừng tiến trình ffmpeg.
- Sửa một cảnh chỉ xoá âm thanh của cảnh đó; các cảnh khác dùng lại cache âm thanh và đoạn
  video.

## Tiếng Việt

- UTF-8 ở mọi nơi (đọc/ghi file, JSON `ensure_ascii=False`, Python UTF-8 mode).
- Văn bản được chuẩn hoá Unicode **NFC** (file từ macOS/Word hay ở dạng NFD).
- File văn bản: UTF-8 (có/không BOM), UTF-16 có BOM, dự phòng cp1258.
- Dùng `pathlib`; test dùng đường dẫn có dấu cách và dấu tiếng Việt.

## Bí mật (SecretStore)

- Interface `SecretStore` (`set/get/delete/list_names`).
- Windows: mã hoá bằng **DPAPI** (`CryptProtectData`, gắn với tài khoản Windows) và lưu mỗi bí
  mật thành một file trong `secrets/` — Credential Manager giới hạn ~2.5 KB, không đủ cho token
  OAuth.
- Linux/macOS (dev/test): mã hoá Fernet, khoá lưu trong file quyền 600.

## FFmpeg

`backend/videostudio/media/ffmpeg.py`: tìm `ffmpeg`/`ffprobe` theo thứ tự
`VIDEOSTUDIO_FFMPEG_DIR` → thư mục `ffmpeg/` cạnh bản cài → `<data dir>/bin` → `PATH`;
`probe()` trả JSON của ffprobe; `detect_h264_encoders()` / `pick_h264_encoder()` phát hiện
NVENC / QSV / AMF (thử encode 1 frame) và lùi về `libx264`.

## API

Mọi request cần header `Authorization: Bearer <token>` (hoặc `X-VideoStudio-Token`).

| Method | Đường dẫn | Mô tả |
|--------|-----------|-------|
| GET | `/health` | kiểm tra sống |
| GET | `/system/info` | data dir, ffmpeg, secret store, plugin… |
| GET | `/adapters` | danh sách adapter kèm `config_schema` |
| GET | `/ir/schema` | JSON Schema của Content IR |
| GET/POST | `/projects` | liệt kê / tạo dự án |
| GET/PATCH/DELETE | `/projects/{id}` | chi tiết / sửa / xoá |
| GET/POST | `/projects/{id}/sources` | liệt kê / thêm nguồn (multipart: `file` hoặc `text`, `adapter_id`, `config`) |
| GET | `/projects/{id}/sources/{sid}/content` | nội dung nguồn |
| DELETE | `/projects/{id}/sources/{sid}` | xoá nguồn |
| POST | `/projects/{id}/ingest` | tạo job ingest (`{source_ids?}`) |
| GET | `/projects/{id}/documents[/{doc_id}]` | Content IR đã tạo |
| GET | `/jobs`, `/jobs/{id}` | danh sách / chi tiết job (kèm log) |
| POST | `/jobs/{id}/cancel`, `/jobs/demo` | huỷ job / tạo job demo |
| GET/POST | `/secrets/selftest` | kiểm tra SecretStore |
| WS | `/ws/jobs?token=…` | luồng sự kiện job |
| GET | `/providers/types` | các loại nhà cung cấp kèm `config_schema` |
| GET/POST | `/providers` | liệt kê bản (`{instances, defaults}`) / tạo |
| GET/PATCH/DELETE | `/providers/{id}` | chi tiết / sửa / xoá (khoá trả dạng `••••1234`) |
| POST | `/providers/test`, `/providers/{id}/test` | kiểm tra cấu hình chưa lưu / đã lưu |
| POST | `/providers/{id}/default` | đặt mặc định |
| GET | `/providers/{id}/models`, `/providers/{id}/voices?lang=` | danh sách model / giọng |
| POST | `/providers/{id}/sample` | đọc thử (`{text?, voice?, lang?}` → `{url}`) |
| GET | `/styles`, `/styles/{id}`, `/storyboard/schema`, `/generation/defaults` | phong cách, schema, mặc định |
| GET/PUT | `/projects/{id}/settings` | cài đặt tạo video của dự án |
| POST/GET | `/projects/{id}/storyboards` | tạo (job `script`) / liệt kê |
| GET/PUT/DELETE | `/storyboards/{id}` | xem / sửa (`{data, settings}`) / xoá |
| POST | `/storyboards/{id}/scenes/{scene}/regenerate` | viết lại một cảnh (`{instruction}`) |
| POST | `/storyboards/{id}/tts`, `/storyboards/{id}/render` | đọc các cảnh / dựng (`{preview}`) |
| GET | `/projects/{id}/renders`, `/renders/{id}` | bản xuất (kèm URL phát) |
| DELETE | `/renders/{id}` | xoá bản xuất và file |
| POST | `/renders/{id}/open-folder` | mở thư mục chứa video |
| POST/GET/DELETE | `/assets` | ảnh / logo / nhạc tải lên |
| GET/PUT | `/settings/branding` | logo, mở đầu / kết thúc |
| GET | `/media/{đường dẫn}?k=…` | phát file trong data dir (hỗ trợ Range) — khoá `k` suy ra từ token, chỉ thư mục `projects/`, `cache/`, `assets/` |
