# Kết nối YouTube

Video Studio đăng video lên kênh YouTube **bằng ứng dụng Google của chính bạn**. Không có máy
chủ trung gian: ứng dụng nói chuyện trực tiếp với Google, token được mã hoá trong kho bí mật của
máy (DPAPI trên Windows), không bao giờ nằm trong cơ sở dữ liệu hay nhật ký.

Bạn cần làm một lần:

1. Tạo dự án Google Cloud và bật YouTube Data API v3.
2. Cấu hình màn hình xin quyền (OAuth consent screen).
3. Tạo OAuth Client ID loại **Ứng dụng máy tính (Desktop app)**.
4. Dán Client ID + Client secret vào Video Studio rồi bấm **Kết nối kênh YouTube**.

## 1. Tạo dự án và bật API

1. Mở <https://console.cloud.google.com/> và đăng nhập bằng tài khoản Google sở hữu kênh.
2. Thanh trên cùng → chọn dự án → **New project** (Dự án mới), đặt tên ví dụ `Video Studio`.
3. Vào **APIs & Services → Library**, tìm **YouTube Data API v3** → **Enable**.

## 2. Màn hình xin quyền (OAuth consent screen)

1. **APIs & Services → OAuth consent screen** (giao diện mới gọi là **Google Auth Platform**).
2. **User type: External** → điền tên ứng dụng, email hỗ trợ, email liên hệ.
3. Mục **Scopes** (Data access): thêm
   - `https://www.googleapis.com/auth/youtube.upload` — tải video lên;
   - `https://www.googleapis.com/auth/youtube.force-ssl` — đặt thumbnail, phụ đề, thêm vào
     danh sách phát.
4. Mục **Test users** (Audience): thêm email Google của bạn (tài khoản sở hữu kênh).
5. Để nguyên trạng thái **Testing** là dùng được ngay — xem các giới hạn ở dưới.

## 3. Tạo Client ID

1. **APIs & Services → Credentials → Create credentials → OAuth client ID**.
2. **Application type: Desktop app** (Ứng dụng máy tính), đặt tên bất kỳ → **Create**.
3. Sao chép **Client ID** (dạng `…apps.googleusercontent.com`) và **Client secret**
   (dạng `GOCSPX-…`).

Không cần khai báo redirect URI: ứng dụng loại Desktop cho phép chuyển hướng về
`http://127.0.0.1:<cổng ngẫu nhiên>/`. Video Studio mở một cổng tạm trên máy bạn, chỉ nhận đúng
một lần chuyển hướng, có kiểm tra `state` và **PKCE** (S256).

## 4. Kết nối trong Video Studio

1. **Cài đặt → Tài khoản mạng xã hội → YouTube**.
2. Dán **Client ID (OAuth, Ứng dụng máy tính)** và **Client secret** → **Lưu**. Sau khi lưu,
   secret chỉ hiện dạng `••••1234`; nút **Xoá** để xoá hẳn secret khỏi máy.
3. Giữ dấu chọn **Dự án Google đang ở chế độ Testing** nếu bạn chưa xuất bản ứng dụng (mục 2).
4. Bấm **Kết nối kênh YouTube** → trình duyệt mở trang đăng nhập Google → chọn tài khoản →
   Google cảnh báo *“Google hasn’t verified this app”* → **Continue** (đây là ứng dụng của chính
   bạn) → đồng ý các quyền. Trang trả về báo **Đã kết nối kênh …**, quay lại Video Studio.

Ngắt kết nối (biểu tượng thùng rác cạnh tài khoản) sẽ thu hồi refresh token ở Google và xoá
token khỏi máy.

## Giới hạn cần biết

- **Dự án chưa được Google xác minh → video bị khoá ở chế độ Riêng tư.** Video tải lên qua API
  từ dự án *chưa qua kiểm tra (audit)* của YouTube API Services sau ngày 28/7/2020 sẽ bị đặt
  **Riêng tư (private)**, kể cả khi bạn chọn Công khai. Video Studio cảnh báo trước khi đăng
  (mục *Kiểm tra trước khi đăng*) và sau khi đăng nếu YouTube đã đổi về riêng tư. Muốn đăng công
  khai tự động, hãy gửi yêu cầu kiểm tra:
  <https://support.google.com/youtube/contact/yt_api_form>. Trong lúc chờ, bạn có thể mở video
  trong YouTube Studio và đổi sang Công khai bằng tay.
- **Hạn mức (quota) 10 000 đơn vị/ngày** cho mỗi dự án Google (đặt lại lúc 0h giờ Thái Bình
  Dương, tức 14h–15h giờ Việt Nam). Một lần tải video tốn 1 600 đơn vị, thumbnail 50, phụ đề 400,
  thêm vào danh sách phát 50 → **khoảng 6 video/ngày** (khoảng 4 nếu kèm phụ đề + thumbnail).
  Khi hết hạn mức, lượt đăng chuyển sang *Chờ thử lại* và tự chạy lại sau giờ đặt lại.
- **Chế độ Testing: refresh token hết hạn sau 7 ngày.** Ứng dụng sẽ báo *Quyền truy cập sẽ hết
  hạn sau … ngày* và gửi thông báo khi cần **kết nối lại**. Xuất bản ứng dụng (**Publish app** →
  In production) để token không hết hạn; với các quyền YouTube, Google có thể yêu cầu xác minh.
- Thumbnail tuỳ chỉnh cần kênh đã **xác minh số điện thoại** (youtube.com/verify). Nếu chưa,
  video vẫn đăng, chỉ thumbnail bị bỏ qua kèm cảnh báo.
- Hẹn giờ: *Hẹn giờ trên nền tảng* tải video lên ngay ở chế độ riêng tư kèm `publishAt`, YouTube
  tự công khai đúng giờ (cần dự án đã được xác minh, xem trên). *Hẹn giờ (ứng dụng tự đăng)* thì
  Video Studio tải lên đúng giờ — máy phải bật (ứng dụng chạy dưới khay, giữ máy không ngủ,
  tuỳ chọn đánh thức máy bằng Task Scheduler).
- Video dài hơn 15 phút cần kênh đã xác minh.

## Kiểm thử với tài khoản thật (dành cho nhà phát triển)

Lấy refresh token một lần bằng chính Video Studio (kết nối kênh), hoặc bằng
[OAuth Playground](https://developers.google.com/oauthplayground/) (bánh răng → *Use your own
OAuth credentials*). Sau đó:

```bash
cd backend
YOUTUBE_CLIENT_ID=… YOUTUBE_CLIENT_SECRET=… YOUTUBE_REFRESH_TOKEN=… \
  uv run pytest tests/test_live_publishing.py -v
# thêm VIDEOSTUDIO_LIVE_YOUTUBE_UPLOAD=1 để tải một video thử 3 giây ở chế độ riêng tư
```
