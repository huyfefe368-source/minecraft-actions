# Minecraft console

Console: https://mc-console.160.191.51.148.nip.io/?v=sync7

Tài khoản giữ nguyên; thông tin đăng nhập chỉ nằm tại `/root/minecraft-console-login.txt` trên VPS.

| Nhóm | Start | Restart | Stop |
|---|---|---|---|
| Job server | Tạo job và chạy Minecraft | Lưu, kết thúc job, tạo job mới | Lưu, kết thúc job, tắt 24/7 |
| Minecraft | Chạy Java trong job hiện tại | Tắt rồi chạy lại Java trong cùng job | Chỉ tắt Java; job và đồng bộ vẫn chạy |

Stop Minecraft vẫn tiêu thụ thời gian của job. Bật 24/7 để chuyển job sau khoảng 5 giờ 30 phút; tắt công tắc chỉ ngăn phiên kế tiếp. Mỗi lần chuyển job có gián đoạn lúc lưu/khôi phục.

## Đồng bộ hai chiều

Phạm vi: `server/` trong repo riêng tư `huyfefe368-source/minecraft-temporary-backups` ↔ thư mục `server/` của worker Actions. Thêm, sửa và xóa file/thư mục được truyền cả hai chiều. Thư mục rỗng dùng `.gitkeep` để Git lưu được. Không tạo archive mới.

Một vòng mới bắt đầu khoảng 5 giây sau vòng trước. Truyền dữ liệu, kiểm tra checksum, commit và push cần thêm thời gian; đây là đồng bộ liên tục theo đợt, không phải ổ đĩa dùng chung tức thời. Trong thử nghiệm, thay đổi nhỏ thường cần hàng chục giây; file lớn và khởi động Minecraft cần lâu hơn.

Thay đổi từ repo được áp dụng khi Minecraft đã dừng an toàn, rồi Minecraft chạy lại trong cùng job nếu trước đó đang chạy. Nếu Minecraft đã được Stop riêng, việc đồng bộ không tự bật lại nó. Thông số cấu hình từ repo được giữ nguyên; tunnel đọc cổng Minecraft đã cấu hình.

Hai bên cùng sửa một file: giữ bằng chứng hai phiên bản trong `conflicts/<snapshot>/` ở repo, mặc định chọn bản repo. Log và `.sync-conflicts/` ưu tiên bản Actions. World được xử lý nguyên thư mục, không trộn các region từ hai trạng thái; thay đổi world từ repo ưu tiên cả world đó và giữ bản Actions cũ khi có xung đột. Thay đổi phát sinh lúc đang truyền còn được giữ dưới `server/.sync-conflicts/` nếu cần.

Publisher chỉ xác nhận sau khi push Git thành công. Nếu remote vừa thay đổi hoặc push lỗi, snapshot còn trên VPS để thử lại. Giới hạn file đơn của GitHub vẫn là 100 MiB; bản lỗi được giữ trong `tree-rejected/`. Force cancel, VM mất đột ngột và lỗi mạng/ổ đĩa vẫn có thể mất thay đổi sau vòng đồng bộ gần nhất.

## Kiểm tra

24 kiểm tra qua trên VPS. Đã thử thực tế: tạo file/thư mục từ GitHub rồi quan sát file xuất hiện trong snapshot gửi lại từ Actions; log phát sinh trên Actions lên GitHub; xóa file/thư mục từ GitHub truyền xuống Actions; Stop, Start và Restart Minecraft giữ nguyên job 43868217325. File thử đã được xóa. Cơ chế Restart Job chờ bản lưu cuối trước khi tạo phiên mới.
