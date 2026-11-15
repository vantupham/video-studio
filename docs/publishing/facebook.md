# Kết nối Facebook Page

Video Studio đăng video và Reels lên **Trang Facebook (Page)** mà bạn quản trị, bằng **ứng dụng
Meta của chính bạn**. Không đăng được lên trang cá nhân (Meta đã bỏ quyền này). Token Trang được
mã hoá trong kho bí mật của máy (DPAPI trên Windows), không lưu trong cơ sở dữ liệu, không ghi
nhật ký.

## 1. Tạo ứng dụng Meta

1. Mở <https://developers.facebook.com/apps/> bằng tài khoản Facebook là **quản trị viên của
   Trang** (lần đầu cần đăng ký tài khoản nhà phát triển).
2. **Create app** → mục đích (use case) chọn **Other** / **Manage everything on your Page**
   (tên có thể khác tuỳ thời điểm) → loại **Business** → đặt tên, ví dụ `Video Studio`.
3. Trong ứng dụng, thêm sản phẩm **Facebook Login** (*Facebook Login for Business* cũng được).
4. **Facebook Login → Settings**:
   - bật **Client OAuth login** và **Embedded browser OAuth login**;
   - **Valid OAuth Redirect URIs**: `https://www.facebook.com/connect/login_success.html`
     (địa chỉ chuẩn dành cho ứng dụng desktop).
5. **App settings → Basic**: sao chép **App ID** và **App secret** (bấm *Show*).
6. Quyền cần dùng: `pages_show_list`, `pages_read_engagement`, `pages_manage_posts`. Ở chế độ
   Development chúng có sẵn cho người có vai trò trong ứng dụng, không cần App Review.

## 2. Kết nối trong Video Studio

1. **Cài đặt → Tài khoản mạng xã hội → Facebook Page**: dán **App ID** và **App secret** →
   **Lưu**. Secret chỉ hiện dạng `••••1234`; nút **Xoá** xoá hẳn khỏi máy.
2. Bấm **Kết nối Facebook Page**:
   - **Ứng dụng desktop:** một cửa sổ đăng nhập Facebook mở ra bên trong Video Studio. Đăng nhập,
     chọn Trang, đồng ý quyền; khi Facebook chuyển đến `login_success.html`, ứng dụng tự lấy mã và
     đóng cửa sổ.
   - **Chạy trong trình duyệt (chế độ dev):** trang đăng nhập mở ở thẻ mới; khi tới trang
     `login_success.html` (trang trắng), sao chép toàn bộ địa chỉ trên thanh địa chỉ và dán vào ô
     trong hộp thoại.
3. Chọn các Trang muốn kết nối → **Kết nối … Trang**. Mỗi Trang thành một tài khoản đăng riêng.

Ứng dụng đổi mã lấy token người dùng dài hạn (~60 ngày), rồi lấy **token Trang** (không hết hạn
khi lấy từ token dài hạn, cho tới khi bạn đổi mật khẩu, gỡ quyền hoặc mất vai trò quản trị).
Ngắt kết nối Trang cuối cùng sẽ thu hồi quyền của ứng dụng.

## Giới hạn cần biết

- **Chế độ Development:** ứng dụng chỉ đăng được lên Trang mà **chính bạn** (người có vai trò
  Admin/Developer/Tester trong ứng dụng) quản trị — đủ cho dùng cá nhân. Muốn người khác dùng ứng
  dụng của bạn thì cần chuyển sang **Live** và qua **App Review** + xác minh doanh nghiệp.
- **Video thường:** MP4/MOV, H.264/HEVC, tối đa 4 giờ / 10 GB; tải lên theo từng phần
  (chunked, tự tiếp tục khi lỗi mạng); phụ đề SRT tuỳ chọn.
- **Reels:** bắt buộc **9:16**, dài **3–90 giây**, tối đa 1 GB. Nếu video đã duyệt là 16:9, phần
  *Kiểm tra trước khi đăng* đề nghị **Dựng lại bản 9:16** từ cùng storyboard (không cần duyệt lại
  nội dung).
- **Hẹn giờ:** *Hẹn giờ trên nền tảng* tải video lên ngay và để Facebook tự đăng đúng giờ (máy
  tắt cũng không sao), lịch từ **10 phút đến 75 ngày** tới. *Hẹn giờ (ứng dụng tự đăng)* thì
  Video Studio tải lên đúng giờ — máy phải bật (ứng dụng chạy dưới khay, giữ máy không ngủ,
  tuỳ chọn đánh thức máy bằng Task Scheduler).
- Token Trang mất hiệu lực khi bạn đổi mật khẩu Facebook hoặc gỡ ứng dụng: lượt đăng báo *cần
  kết nối lại* và không thử lại vô ích.

## Kiểm thử với tài khoản thật (dành cho nhà phát triển)

Lấy token Trang bằng [Graph API Explorer](https://developers.facebook.com/tools/explorer/):
chọn ứng dụng của bạn → *Get Page Access Token* với các quyền ở trên → đổi sang token dài hạn
bằng [Access Token Debugger](https://developers.facebook.com/tools/debug/accesstoken/)
(*Extend Access Token*), rồi gọi `GET /me/accounts` để lấy token Trang. Sau đó:

```bash
cd backend
FACEBOOK_APP_ID=… FACEBOOK_APP_SECRET=… FACEBOOK_PAGE_TOKEN=… \
  uv run pytest tests/test_live_publishing.py -v
```

Test chỉ đọc (kiểm tra token là token Trang, có `pages_manage_posts`, đọc tên Trang), không đăng
bài.
