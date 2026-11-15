# Nhà cung cấp AI (LLM, giọng đọc, phụ đề, hình ảnh)

Video Studio không gắn chặt với một dịch vụ nào. Mỗi **chức năng** có thể có nhiều **nhà cung
cấp** (provider), bạn tự thêm, cấu hình, kiểm tra và chọn cái nào làm **mặc định**:

| Chức năng | Dùng để | Nhà cung cấp có sẵn |
|-----------|---------|---------------------|
| LLM | viết kịch bản + storyboard từ tài liệu, viết lại một cảnh | OpenAI, Anthropic Claude, Google Gemini, Ollama (chạy trên máy), OpenAI-compatible (tuỳ chỉnh), Chế độ demo |
| Giọng đọc (TTS) | đọc lời thuyết minh | Microsoft Edge TTS (miễn phí, *không chính thức*), Azure AI Speech, Google Cloud TTS, ElevenLabs, FPT.AI, OpenAI TTS, Piper (offline), Giọng giả lập |
| Căn thời gian phụ đề | khi giọng đọc không trả về mốc thời gian từng từ | Ước lượng theo âm tiết (offline), faster-whisper (offline), giả lập |
| Kho hình ảnh | ảnh minh hoạ cho cảnh kiểu "stock" | Pexels, Ảnh giả lập |

Lần chạy đầu tiên ứng dụng tự tạo sẵn: **Chế độ demo** (LLM, mặc định), **Edge TTS miễn phí**
(giọng đọc, mặc định), **Giọng giả lập**, **Ước lượng theo âm tiết** và **Ảnh giả lập** — nên
có thể tạo video ngay mà không cần khoá API nào.

## Quản lý trong ứng dụng

**Cài đặt → Nhà cung cấp**:

- **+ Thêm** → chọn loại → điền form (khoá API là ô mật khẩu) → **Kiểm tra kết nối** →
  **Thêm nhà cung cấp**. Có thể tạo nhiều bản của cùng một loại (ví dụ hai tài khoản OpenAI,
  hai cấu hình Ollama với model khác nhau).
- **Đặt mặc định**: có hiệu lực ngay cho lần tạo tiếp theo, không cần khởi động lại.
- **Sửa**: ô khoá API để trống = giữ khoá cũ; nhập giá trị mới = thay; xoá trắng rồi lưu bằng
  API (`""`) = xoá khoá.
- Với giọng đọc: **Tải danh sách giọng**, **▶ Nghe thử**, **Lưu làm giọng mặc định**.
- Với LLM đã lưu: **Tải danh sách model** ngay trong form sửa.

Mỗi dự án / mỗi lần **Tạo video** có thể chọn riêng nhà cung cấp LLM + model và nhà cung cấp
giọng đọc + giọng; để "Mặc định" thì dùng cài đặt chung. Storyboard ghi lại lựa chọn lúc tạo,
và có thể đổi giọng đọc trong trình chỉnh sửa storyboard (mục "Cài đặt dựng").

Thứ tự chọn nhà cung cấp khi chạy: lựa chọn của storyboard → của dự án → mặc định của chức
năng. Model / giọng: lựa chọn đã chọn → cấu hình của nhà cung cấp.

### Bảo mật khoá API

- Khoá chỉ được lưu trong **kho bí mật của hệ điều hành** (Windows: mã hoá DPAPI theo tài khoản
  người dùng, mỗi khoá một file trong `%LOCALAPPDATA%\VideoStudio\secrets\`). SQLite chỉ lưu
  các trường không bí mật.
- API không bao giờ trả lại khoá — chỉ dạng che `••••1234`. Khoá không được ghi vào log;
  thông báo lỗi từ dịch vụ bên ngoài được lọc bỏ mọi chuỗi trông giống khoá.
- Xoá nhà cung cấp sẽ xoá luôn các khoá của nó.

## Hướng dẫn từng nhà cung cấp

Trường có dấu \* là bắt buộc. Giá chỉ mang tính tham khảo và thay đổi thường xuyên — luôn xem
trang giá chính thức.

### LLM

**OpenAI** — `api_key`\*, `model`\* (mặc định `gpt-4o-mini`), `base_url`, `organization`,
`temperature`, `timeout_s`.
Lấy khoá tại <https://platform.openai.com/api-keys> (cần nạp tiền trả trước). Dùng Structured
Outputs (`response_format: json_schema`) nên kịch bản luôn đúng cấu trúc. Model nhỏ
(`gpt-4o-mini`, `gpt-4.1-mini`…) rẻ và đủ tốt cho tiếng Việt: một video 1–2 phút thường chỉ tốn
khoảng vài trăm đồng đến một nghìn đồng. Giá: <https://openai.com/api/pricing>.

**Anthropic Claude** — `api_key`\*, `model`\* (mặc định `claude-sonnet-4-5`), `max_tokens`,
`temperature`. Khoá tại <https://console.anthropic.com/settings/keys>. Kết quả JSON được ép qua
"tool use". Haiku rẻ hơn Sonnet nhiều lần. Giá: <https://www.anthropic.com/pricing>.

**Google Gemini** — `api_key`\*, `model`\* (mặc định `gemini-2.5-flash`). Khoá tại
<https://aistudio.google.com/app/apikey>. Có hạn mức miễn phí hằng ngày (đủ để thử); dữ liệu ở
gói miễn phí có thể được Google dùng để cải thiện sản phẩm.

**Ollama (chạy trên máy)** — `base_url`\* (mặc định `http://localhost:11434`), `model`\*
(mặc định `qwen2.5:7b`). Miễn phí, không gửi dữ liệu ra ngoài. Cài
<https://ollama.com/download>, rồi `ollama pull qwen2.5:7b`. Cần GPU/RAM đủ mạnh (model 7B cần
khoảng 8 GB RAM); chất lượng tiếng Việt kém hơn các dịch vụ đám mây.

**OpenAI-compatible (tuỳ chỉnh)** — `base_url`\*, `model`\*, `api_key`, `json_mode`
(`json_schema` / `json_object` / `none`). Dùng cho OpenRouter, Groq, DeepSeek, Together, LM
Studio, vLLM… Nếu dịch vụ không hỗ trợ `json_schema`, chọn `json_object` hoặc `none` (ứng dụng
vẫn kiểm tra JSON và tự sửa một lần).

**Chế độ demo** — không gọi mạng, tạo kịch bản bằng cách trích các phần của tài liệu. Dùng để
thử quy trình hoặc chạy test.

> Mọi LLM đều được gọi kèm JSON Schema của kịch bản; kết quả được kiểm tra bằng Pydantic, nếu
> sai sẽ gửi lại lỗi cho model sửa **một lần**, sau đó báo lỗi rõ ràng.

### Giọng đọc (TTS)

**Microsoft Edge TTS (miễn phí, không chính thức)** — `default_voice` (mặc định
`vi-VN-HoaiMyNeural`; giọng nam `vi-VN-NamMinhNeural`), `rate`, `pitch`. Dùng dịch vụ đọc to
của trình duyệt Edge qua thư viện [edge-tts](https://github.com/rany2/edge-tts): chất lượng tốt,
có mốc thời gian từng từ, **không cần khoá**. Đây **không phải API chính thức** — Microsoft có
thể thay đổi hoặc chặn bất cứ lúc nào, không nên dùng cho mục đích thương mại; khi cần ổn định
hãy dùng Azure AI Speech (cùng bộ giọng).

**Azure AI Speech** — `api_key`\*, `region`\* (mặc định `southeastasia`), `endpoint`,
`default_voice`, `rate`. Tạo tài nguyên *Speech* trên
[Azure Portal](https://portal.azure.com/#create/Microsoft.CognitiveServicesSpeechServices),
lấy *Key 1* và *Region*. Gói F0 miễn phí 0,5 triệu ký tự/tháng; sau đó khoảng 15 USD / 1 triệu
ký tự (Neural). Bản REST hiện tại không trả mốc thời gian từng từ → phụ đề được căn bằng nhà
cung cấp "Căn thời gian phụ đề".

**Google Cloud Text-to-Speech** — `api_key`\*, `default_voice` (mặc định `vi-VN-Wavenet-A`),
`speaking_rate`. Bật *Cloud Text-to-Speech API* trong
[Google Cloud Console](https://console.cloud.google.com/apis/library/texttospeech.googleapis.com),
tạo *API key* (nên giới hạn key chỉ cho API này). Có hạn mức miễn phí hằng tháng (WaveNet ~1
triệu ký tự). Mốc thời gian từng từ lấy qua SSML `<mark>`.

**ElevenLabs** — `api_key`\*, `default_voice`\* (Voice ID), `model` (mặc định
`eleven_flash_v2_5`). Khoá tại <https://elevenlabs.io/app/settings/api-keys>. Giọng rất tự
nhiên, đa ngôn ngữ, trả mốc thời gian từng ký tự; gói miễn phí ~10 nghìn ký tự/tháng, gói trả
phí tính theo ký tự — đắt nhất trong danh sách.

**FPT.AI Text to Speech** — `api_key`\*, `default_voice` (`banmai`, `leminh`, `thuminh`,
`giahuy`, `myan`, `lannhi`, `linhsan`, `minhquang`), `speed` (−3…3). Khoá tại
<https://console.fpt.ai/>. Giọng Việt bản địa nhiều vùng miền; xử lý bất đồng bộ (ứng dụng tự
chờ file sẵn sàng). Không có mốc thời gian từng từ.

**OpenAI TTS** — `api_key`\*, `model` (mặc định `gpt-4o-mini-tts`), `default_voice` (mặc định
`nova`), `instructions` (mô tả cách đọc). Đọc được tiếng Việt nhưng giọng có âm hưởng nước
ngoài. Không có mốc thời gian từng từ.

**Piper (offline)** — `default_voice` (mặc định `vi_VN-vais1000-medium`), `length_scale`.
Chạy hoàn toàn trên máy, miễn phí. Cần cài phần mở rộng khi chạy từ mã nguồn:
`cd backend && uv sync --extra offline`. Giọng được tải về `models/piper/` trong thư mục dữ liệu
ở lần dùng đầu tiên. Bộ cài Windows hiện **không** kèm Piper (để giữ dung lượng nhỏ).

**Giọng giả lập** — tạo tiếng bíp có độ dài tương ứng lời thoại, dùng cho test / xem nhanh bố
cục mà không cần mạng.

Âm thanh được **lưu đệm** theo (nhà cung cấp, giọng, cấu hình ảnh hưởng tới âm thanh, ngôn ngữ,
văn bản) trong `cache/tts/`: sửa một cảnh chỉ đọc lại cảnh đó; đổi giọng sẽ đọc lại mọi cảnh.

### Căn thời gian phụ đề

Thứ tự: mốc thời gian từ chính TTS (Edge, ElevenLabs, Google) → nhà cung cấp căn thời gian mặc
định → ước lượng theo âm tiết.

**Ước lượng theo âm tiết (offline)** — chia thời lượng theo số âm tiết tiếng Việt (mỗi "từ"
cách nhau bằng dấu cách là một âm tiết), cộng thêm khoảng nghỉ ở dấu câu. Không cần cài gì.

**faster-whisper (offline)** — `model_size` (`tiny`…`large-v3`, mặc định `small`), `device`
(`auto`/`cpu`/`cuda`), `compute_type`. Nhận dạng lại âm thanh để lấy mốc thời gian thật rồi
khớp về văn bản gốc. Cài: `cd backend && uv sync --extra whisper`; model tải về
`models/whisper/` ở lần đầu (small ≈ 500 MB). Chậm trên CPU; bộ cài Windows hiện không kèm.

### Kho hình ảnh

**Pexels** — `api_key`\*, `locale`. Đăng ký miễn phí tại <https://www.pexels.com/api/>
(200 request/giờ). Ảnh dùng miễn phí, nên ghi nguồn. Dùng cho cảnh có kiểu hình ảnh
"Ảnh kho (stock)" với từ khoá (tiếng Anh cho kết quả tốt hơn).

**Ảnh giả lập** — ảnh nền gradient tạo trên máy.

## Chi phí ước tính cho một video 1 phút (~150 từ, ~1.000 ký tự)

| Cách dùng | Ước tính |
|-----------|----------|
| Demo + Edge TTS | 0 đ |
| Gemini Flash (hạn mức miễn phí) + Edge TTS | 0 đ |
| GPT-4o-mini + Azure Neural | ~1.000 đ trở xuống (miễn phí nếu còn trong gói F0) |
| Claude Sonnet + ElevenLabs | vài nghìn đến vài chục nghìn đồng tuỳ gói ElevenLabs |
| Ollama + Piper | 0 đ (dùng tài nguyên máy) |

Render bằng FFmpeg trên máy, không tốn phí.

## Viết plugin nhà cung cấp

Một plugin là file Python có hàm `register(registry)`. Ứng dụng nạp plugin từ:

1. Thư mục `plugins/` trong thư mục dữ liệu (`%LOCALAPPDATA%\VideoStudio\plugins\`):
   `<tên>.py` hoặc `<tên>/plugin.py`.
2. Gói Python có entry point nhóm `videostudio.plugins`:

   ```toml
   [project.entry-points."videostudio.plugins"]
   my_tts = "my_package.plugin:register"
   ```

Plugin lỗi chỉ được ghi log (xem **Cài đặt → Hệ thống**), không làm hỏng ứng dụng.

Các lớp cơ sở (`videostudio.plugins.base`):

| Lớp | Phải cài đặt |
|-----|--------------|
| `LLMProvider` | `config_schema()`, `test_connection(ctx)`, `complete(ctx, messages, *, model, json_schema, temperature, max_tokens)`; tuỳ chọn `list_models(ctx)` |
| `TTSProvider` | `config_schema()`, `test_connection(ctx)`, `list_voices(ctx, lang)`, `synthesize(ctx, text, *, voice, lang, out_path)` → `TTSAudio(path, duration_s, words)` |
| `AlignmentProvider` | `config_schema()`, `test_connection(ctx)`, `align(ctx, *, audio_path, text, lang, duration_s)` → `list[WordTiming]` |
| `MediaProvider` | `config_schema()`, `test_connection(ctx)`, `search(...)`, `download(...)` |

Thuộc tính lớp: `id` (duy nhất), `name` (tiếng Việt, hiển thị trong UI), `description`,
`docs_url`, `unofficial`, `requires_network`.

`config_schema()` trả về JSON Schema; UI tự sinh form:

- `enum` + `x-enumLabels` (mảng hoặc `{giá trị: nhãn}`) → ô chọn;
- `"x-secret": true` (dùng `secret_field("Tên")`) → ô mật khẩu, giá trị lưu trong SecretStore;
- `"x-source": "models"` / `"voices"` → nút tải danh sách từ `list_models` / `list_voices`;
- `"x-multiline": true` → ô nhiều dòng; `type: "object"` có `properties` → nhóm lồng nhau.

Trong code, đọc cấu hình bằng `ctx.get("ten_truong")` (khoá bí mật đã được giải mã sẵn trong
`ctx`, chỉ tồn tại trong bộ nhớ). Gọi HTTP bằng `videostudio.providers.errors.client()` và
`send()` để lỗi 401/403/429/timeout được chuyển thành thông báo tiếng Việt thống nhất.

Ví dụ hoàn chỉnh: [`docs/examples/http_tts_plugin.py`](examples/http_tts_plugin.py) — giọng
đọc gọi máy chủ TTS HTTP riêng, có trường token bí mật và danh sách giọng. Test
`backend/tests/test_example_plugin.py` nạp đúng file này qua thư mục plugin để đảm bảo ví dụ
luôn chạy được.
