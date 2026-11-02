# PM Mail Assistant: cài đặt trên máy mới

Trợ lý email cho PM dự án outsource Nhật: đọc mail từ Thunderbird, gom thread, tìm kiếm (từ khóa và ngữ nghĩa), hỏi đáp và dịch mail bằng model AI nội bộ.

Tài liệu này hướng dẫn dựng hệ thống từ đầu trên một máy Windows mới. Làm lần lượt từ mục 1 đến mục 9.

> **Cài toàn bộ trên chính máy có Thunderbird.** Collector chỉ gửi mail qua `http://` tới `127.0.0.1` hoặc `localhost`; gửi tới server ở máy khác phải dùng `https://`, mà hệ thống chưa có HTTPS. Mỗi người tự chạy một bản trên máy mình.

## 1. Cấu hình máy

Số đo ngày 2026-11-03 trên Intel i5-8500 (6 nhân), 15,8 GB RAM, Windows 11, không GPU, một người dùng với khoảng 1.000 mail; số cũ đo trên máy 7,7 GB RAM.

| | Tắt reranker (mặc định) | Bật reranker |
|---|---|---|
| RAM | Tối thiểu 8 GB (chạy được nhưng sát), khuyến nghị 16 GB | Tối thiểu 16 GB |
| CPU | x86-64, tối thiểu 4 nhân, khuyến nghị 6 nhân trở lên | Như cột bên, nhưng trên CPU rerank rất chậm (xem dưới) |
| GPU | Không cần | Không bắt buộc; chỉ GPU mới giúp rerank đủ nhanh (chưa thử) |
| Ổ đĩa trống | Tối thiểu 15 GB, khuyến nghị 30 GB, SSD | Thêm 2,2 GB cho model reranker |
| Hệ điều hành | Windows 10/11 64-bit, bật ảo hóa trong BIOS (Docker Desktop cần WSL2) | Như cột bên |

**RAM khi chạy:**

| Thành phần | Tắt reranker | Bật reranker |
|---|---|---|
| ml-service (model bge-m3) | 3,2 GB | 5,8 GB |
| PostgreSQL trong Docker | 0,6 GB, cộng phần nền của Docker Desktop và WSL | như bên |
| API, worker | khoảng 0,12 GB mỗi tiến trình | như bên |

Phần còn lại dành cho Windows, trình duyệt, Thunderbird. Máy 8 GB chỉ còn trống 0,5–1,6 GB khi chạy đủ, Windows đẩy một phần model ra file trang nên chậm.

**Ổ đĩa:**

| Phần | Dung lượng |
|---|---|
| Model bge-m3 (cache Hugging Face) | 4,3 GB |
| Model reranker `BAAI/bge-reranker-v2-m3` (chỉ khi bật) | 2,2 GB |
| Docker image | khoảng 3,4 GB |
| Thư viện Python (ml-service 0,9 GB, backend 0,2 GB) và Node (0,2 GB) | khoảng 1,3 GB |
| DB | khoảng 140 MB khi trống, cộng 50–100 MB mỗi 1.000 mail giả (mail thật dài hơn: dự kiến vài GB mỗi người) |

**Tốc độ trên CPU:**

- Embed (để tìm ngữ nghĩa): máy trên embed 1.050 đoạn ngắn trong khoảng 8 phút. Máy cũ (i5-1235U) khoảng 0,3 đoạn/giây với đoạn 800 ký tự. Lần đầu với hộp mail thật vài nghìn mail có thể mất vài giờ, chạy nền.
- Rerank: khoảng 1,1 giây mỗi đoạn dài. 40 đoạn mất 17–45 giây, có thể quá thời gian chờ 30 giây của hỏi đáp (khi đó hệ thống bỏ rerank, dùng thứ tự cũ). Trên bộ câu hỏi mẫu, rerank tăng MRR từ 0,71 lên 0,86. **Không khuyến nghị bật trên máy chỉ có CPU.**

Model AI nội bộ (dùng cho hỏi đáp và dịch) chạy trên máy riêng qua gateway kiểu 9router, không tính trong các số trên. Cấu hình máy đó tùy model bạn chọn.

## 2. Cài phần mềm

| Phần mềm | Dùng để | Ghi chú |
|---|---|---|
| [Git](https://git-scm.com/) | Lấy mã nguồn | |
| [Docker Desktop](https://www.docker.com/products/docker-desktop/) | Chạy PostgreSQL | Bật WSL2 khi cài. Mở Docker Desktop trước mỗi lần dùng |
| [uv](https://docs.astral.sh/uv/) | Python cho backend và ml-service | uv tự tải Python 3.12 |
| [Node.js](https://nodejs.org/) 22 LTS trở lên | Build giao diện web | Chỉ cần khi build |
| [Go](https://go.dev/) 1.27 trở lên | Build collector | Không cần nếu chép sẵn file `collector.exe` từ máy khác |
| Thunderbird | Nguồn mail | Hệ thống chỉ đọc profile, không ghi |

Cài uv bằng PowerShell:

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

Mở cửa sổ PowerShell mới rồi kiểm tra: `git --version`, `docker --version`, `uv --version`, `node --version`, `go version`.

## 3. Lấy mã nguồn

```powershell
git clone https://github.com/hieu-dkvt/EmailAssistant.git
cd EmailAssistant
```

Mọi lệnh bên dưới chạy từ thư mục này.

## 4. Cơ sở dữ liệu

Mở Docker Desktop, đợi báo "Engine running", rồi:

```powershell
docker compose up -d
docker compose ps        # đợi cột STATUS có "(healthy)"
uv run --directory backend alembic upgrade head
```

- Lần đầu Docker build image PostgreSQL có PGroonga và pgvector, mất vài phút.
- DB chỉ mở trên `127.0.0.1:15432`, không mở ra mạng. Mật khẩu mặc định là mật khẩu dev; muốn đổi thì xem mục 10.
- `alembic upgrade head` tạo bảng. Chạy lại lệnh này sau mỗi lần cập nhật mã nguồn.

## 5. Khóa bí mật và tài khoản

Khóa bí mật dùng để mã hóa API key của gateway AI. API và worker phải dùng cùng một khóa.

```powershell
uv run --directory backend python -m app.cli gen-secret-key
```

Lưu khóa vào biến môi trường của người dùng Windows (thay `<khóa>` bằng chuỗi vừa in):

```powershell
[Environment]::SetEnvironmentVariable("PMM_SECRET_KEY", "<khóa>", "User")
```

Đóng PowerShell, mở cửa sổ mới để biến có hiệu lực. Giữ khóa này cẩn thận: mất hoặc đổi khóa thì phải nhập lại API key ở Cài đặt AI.

Tạo tài khoản (vai trò `pm`) và đặt mật khẩu đăng nhập web:

```powershell
uv run --directory backend python -m app.cli create-user --email <email> --name "<tên>" --role pm
uv run --directory backend python -m app.cli set-password --email <email>
```

`create-user` in **token collector một lần duy nhất**: chép lại để dùng ở mục 8. Mất token thì tạo token mới bằng `... python -m app.cli create-token --email <email>`.

## 6. Build giao diện web

```powershell
npm --prefix web install
npm --prefix web run build
```

Build lại mỗi khi cập nhật mã nguồn. API phục vụ bản build ở `http://127.0.0.1:8000`.

## 7. Chạy các dịch vụ

Mỗi dịch vụ chạy trong một cửa sổ PowerShell riêng, để mở trong lúc dùng.

**Cửa sổ 1: ml-service** (tìm ngữ nghĩa, embed, hỏi đáp; dịch mail không cần):

```powershell
uv sync --directory ml-service
uv run --directory ml-service python -m app.run
```

- `uv sync` chỉ cần lần đầu và sau khi cập nhật; tải PyTorch, mất vài phút. Mạng chậm thì đặt `$env:UV_HTTP_TIMEOUT = "900"` trước.
- Lần chạy đầu tải model bge-m3 (khoảng 2,3 GB, cache 4,3 GB); các lần sau chạy offline.
- Sẵn sàng khi `http://127.0.0.1:8001/health` trả `"status":"ok"`.

**Cửa sổ 2: API** (`http://127.0.0.1:8000`):

```powershell
uv run --directory backend python -m app.run
```

**Cửa sổ 3: worker** (làm sạch, gom thread, gán dự án, embed, dịch tự động):

```powershell
uv run --directory backend python -m app.worker
```

Thiếu ml-service thì search ngữ nghĩa và hỏi đáp báo lỗi, các chức năng khác vẫn chạy.

## 8. Collector: đưa mail từ Thunderbird lên

Build collector (bỏ qua nếu đã có `collector\collector.exe`):

```powershell
$env:CGO_ENABLED = "0"
go build -C collector -o collector.exe ./cmd/collector
```

Mở trang cài đặt của collector:

```powershell
collector\collector.exe settings
```

Trên trang vừa mở:

1. Chọn profile Thunderbird và các folder cần đọc, bấm **Lưu**.
2. Nhập URL server `http://127.0.0.1:8000` và token ở mục 5, bấm **Lưu kết nối**.
3. Bấm **Đồng bộ**.

Collector chỉ đọc file mbox, không ghi gì vào Thunderbird hay mail server. Đồng bộ lại bằng nút Đồng bộ hoặc `collector\collector.exe sync`.

## 9. Dùng hệ thống

1. Mở `http://127.0.0.1:8000`, đăng nhập bằng email và mật khẩu ở mục 5.
2. Vào **Cài đặt AI**: nhập URL gateway nội bộ, API key và tên model cho từng tác vụ (phân tích câu hỏi, trả lời, dịch). Chưa nhập thì hỏi đáp và dịch báo `ai_not_configured`.
   - **Chỉ nhập gateway nội bộ.** Mail được gửi tới URL này ở dạng gốc; không dùng API bên ngoài (Anthropic, OpenRouter…). Màn hình cảnh báo khi URL là địa chỉ công cộng nhưng không chặn.
3. Vào **Dự án**: khai báo dự án và bộ lọc From, To, CC, Subject. Dịch tự động chỉ chạy cho dự án có domain khách, với mail từ mốc (lần đầu nhập domain khách lùi lại 14 ngày, cố định); mail cũ hơn dịch tay được. Chọn folder đích cho mail đã dịch ở cấu hình dự án (folder phải đã đồng bộ).

Xem số liệu tổng hợp: `uv run --directory backend python -m app.cli mail-stats`.

**Những lần sau:** mở Docker Desktop (DB tự chạy lại), rồi mở ba cửa sổ ở mục 7.

## 10. Tùy chọn

### Bật reranker

Chỉ nên bật khi máy đủ cấu hình ở mục 1. Hai nơi phải bật cùng lúc:

```powershell
# Cửa sổ ml-service (lần đầu tải thêm khoảng 2,2 GB)
$env:ML_RERANK_MODEL = "BAAI/bge-reranker-v2-m3"
uv run --directory ml-service python -m app.run

# Cửa sổ API
$env:PMM_RERANK = "1"
uv run --directory backend python -m app.run
```

Tắt: mở lại hai cửa sổ trên mà không đặt hai biến này. Lỗi hoặc quá 30 giây thì hỏi đáp tự bỏ rerank.

### Đổi mật khẩu DB

Tạo file `.env` cạnh `docker-compose.yml` (file này không được commit):

```
POSTGRES_PASSWORD=<mật khẩu>
APP_OWNER_PASSWORD=<mật khẩu>
APP_API_PASSWORD=<mật khẩu>
APP_WORKER_PASSWORD=<mật khẩu>
```

Mật khẩu chỉ áp dụng khi DB được tạo lần đầu (volume mới). Backend đọc chuỗi kết nối từ biến môi trường, đặt cho mọi cửa sổ chạy backend:

```powershell
[Environment]::SetEnvironmentVariable("DATABASE_URL_OWNER", "postgresql+psycopg://app_owner:<mật khẩu>@127.0.0.1:15432/mailassist", "User")
[Environment]::SetEnvironmentVariable("DATABASE_URL_API", "postgresql+psycopg://app_api:<mật khẩu>@127.0.0.1:15432/mailassist", "User")
[Environment]::SetEnvironmentVariable("DATABASE_URL_WORKER", "postgresql+psycopg://app_worker:<mật khẩu>@127.0.0.1:15432/mailassist", "User")
```

## 11. Cập nhật lên bản mới

Dừng API, worker, ml-service (Ctrl+C), rồi:

```powershell
git pull
uv run --directory backend alembic upgrade head
uv sync --directory ml-service
npm --prefix web install
npm --prefix web run build
```

Chạy lại ba cửa sổ ở mục 7. Bản mới có thay đổi collector thì build lại `collector.exe` (mục 8).

## 12. Chuyển sang máy khác

- **mbox của Thunderbird là bản gốc, DB chỉ là chỉ mục.** Trên máy mới chỉ cần cài theo tài liệu này và đồng bộ lại từ Thunderbird; không cần chép DB.
- Không chép `.env`, `note.txt` hay thư mục `samples/raw/` qua kênh không an toàn: chúng có mật khẩu hoặc mail thật.
- Khóa `PMM_SECRET_KEY` mới thì nhập lại API key ở Cài đặt AI.

## 13. Lỗi thường gặp

| Hiện tượng | Cách xử lý |
|---|---|
| `docker compose` báo không kết nối được | Mở Docker Desktop, đợi "Engine running" |
| `alembic` báo không kết nối được `127.0.0.1:15432` | DB chưa healthy: đợi `docker compose ps` báo `(healthy)` |
| `uv sync --directory ml-service` báo file đang bị dùng | Dừng ml-service trước: Windows khóa file của thư viện đang chạy |
| Tải model hoặc thư viện bị ngắt giữa chừng | Đặt `$env:UV_HTTP_TIMEOUT = "900"` rồi chạy lại; model tải tiếp từ cache |
| Hỏi đáp hoặc dịch báo `ai_not_configured` | Nhập gateway và tên model ở Cài đặt AI |
| Hỏi đáp hoặc search ngữ nghĩa báo `ml_unavailable` | Chưa chạy ml-service, hoặc ml-service chưa nạp xong model |
| Cài đặt AI báo thiếu khóa bí mật, nút Lưu bị khóa | Chưa đặt `PMM_SECRET_KEY` (mục 5), hoặc chưa mở cửa sổ PowerShell mới trước khi chạy API |
| Cổng 8000 hoặc 8001 đã bị dùng | Tắt tiến trình đang chiếm cổng, hoặc chạy API với `--port <cổng khác>` |

## Tài liệu khác

- Kiến trúc, nguyên tắc dữ liệu và bảo mật: [CLAUDE.md](CLAUDE.md).
- Kế hoạch và lộ trình: [email-assistant-plan.md](email-assistant-plan.md).
- Đặc tả từng chức năng: [docs/superpowers/specs/README.md](docs/superpowers/specs/README.md). Thay đổi theo ngày: [docs/CHANGELOG.md](docs/CHANGELOG.md).
