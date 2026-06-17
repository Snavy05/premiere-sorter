# 📖 Hướng Dẫn Chạy SteadyCut — macOS (Apple Silicon)

> Đọc từng bước, làm đúng thứ tự. Không cần biết gì về máy tính cả!

---

## SteadyCut làm gì?

Bạn có nhiều clip video quay tay/gimbal, có đoạn rung đoạn ổn.
SteadyCut sẽ **tự làm hết** 4 việc này:

```
Clip gốc của bạn
      │
      ▼
① Tạo bản nhỏ hơn (proxy) để xử lý cho nhanh
      │
      ▼
② Phân tích từng clip — tìm đoạn ít rung nhất
      │
      ▼
③ Đếm số người trong clip → gắn nhãn màu
      │
      ▼
④ Xuất file XML → kéo vào Premiere Pro / DaVinci Resolve là xong 🎬
```

---

## BƯỚC 1 — Lấy app

App nằm trong file:

```
SteadyCut-macOS-arm64.zip
```

(Đã có sẵn trong ổ cứng / thư mục mình đưa bạn. Copy vào máy Mac của bạn trước.)

---

## BƯỚC 2 — Giải nén

Bấm **đúp** vào file `.zip` → máy Mac tự giải nén ra một app tên **`SteadyCut`**.

```
📦 SteadyCut-macOS-arm64.zip
        │  [Bấm đúp]
        ▼
   🎬 SteadyCut        ◄── đây là app
```

---

## BƯỚC 3 — Mở app (LẦN ĐẦU phải làm đúng cách)

⚠️ **Lần đầu KHÔNG bấm đúp.** Mac sẽ chặn vì app chưa có chữ ký Apple.

Làm thế này:

```
   🎬 SteadyCut
        │
        │  [Chuột phải]  (hoặc giữ Control rồi bấm)
        ▼
  ┌─────────────────────────┐
  │  Open            ◄──────┼── Bấm cái này
  │  Move to Bin            │
  │  Get Info               │
  └─────────────────────────┘
        │
        ▼
  ┌──────────────────────────────────────────┐
  │  "SteadyCut" cannot be opened because    │
  │  Apple cannot check it for malware.      │
  │                                          │
  │     [Move to Bin]      [Open] ◄── Bấm đây│
  └──────────────────────────────────────────┘
```

> ✅ Bình thường thôi — Mac hay hỏi vậy với app mới chưa có chữ ký. Không phải virus.
> **Chỉ cần làm cách này LẦN ĐẦU.** Sau đó bấm đúp mở như app bình thường.

*(Nếu vẫn không thấy nút Open: vào **System Settings → Privacy & Security**, kéo
xuống dưới sẽ thấy dòng nhắc về "SteadyCut" → bấm **Open Anyway**.)*

---

## BƯỚC 4 — Chờ FFmpeg tải (chỉ lần đầu)

App mở ra → trình duyệt (Safari/Chrome) tự bật lên. Trên đầu trang có thanh vàng:

```
  ┌────────────────────────────────────────────────────────┐
  │ ⟳  Setting up FFmpeg                                   │
  │    Downloading FFmpeg...                               │  ← Chờ cái này biến mất
  └────────────────────────────────────────────────────────┘
```

Khoảng 1–2 phút (tải ~80MB), thanh vàng tự biến mất. **Chỉ xảy ra lần đầu tiên thôi.**

---

## BƯỚC 5 — Điền thông tin

Đây là giao diện bạn thấy trong trình duyệt:

```
  ┌─────────────────────────────────────────────────────────────┐
  │ 🎬 SteadyCut                                   Pipeline v3  │
  ├─────────────────────────────────────────────────────────────┤
  │  Run Pipeline                                               │
  │  ┌─────────────────────────────────────────────────────┐   │
  │  │  RAW FOOTAGE DIRECTORY                              │   │
  │  │  ┌───────────────────────────────────────────────┐  │   │
  │  │  │ 📁  /Users/TenBan/Movies/ClipGoc             │  │   │
  │  │  └───────────────────────────────────────────────┘  │   │
  │  │     ↑ Đường dẫn thư mục chứa clip gốc              │   │
  │  │                                                     │   │
  │  │  OUTPUT XML PATH                                    │   │
  │  │  ┌───────────────────────────────────────────────┐  │   │
  │  │  │ 📄  /Users/TenBan/Desktop/output.xml         │  │   │
  │  │  └───────────────────────────────────────────────┘  │   │
  │  │     ↑ Nơi lưu file kết quả                         │   │
  │  │                                                     │   │
  │  │  ▸ Advanced Settings    (không cần đụng vào)        │   │
  │  │  [          Run Pipeline          ]  ← Bấm nút này  │   │
  │  └─────────────────────────────────────────────────────┘   │
  └─────────────────────────────────────────────────────────────┘
```

### Cách lấy đường dẫn thư mục clip (trên Mac):

```
  Mở thư mục chứa clip trong Finder
         │
         ▼
  Chuột phải vào thư mục "ClipGoc"
         │
         │  Giữ phím  ⌥ Option  → menu đổi thành:
         ▼
  ┌────────────────────────────────────────────────┐
  │  Copy "ClipGoc" as Pathname   ◄── Bấm cái này  │
  └────────────────────────────────────────────────┘
         │
         ▼
  Dán (⌘V) vào ô "Raw Footage Directory"
```

### Đường dẫn output XML — ví dụ:

Muốn lưu ra Desktop tên `output.xml` thì gõ:
```
/Users/TenBan/Desktop/output.xml
```
*(Thay `TenBan` bằng tên tài khoản Mac của bạn)*

---

## BƯỚC 6 — Chạy và chờ

Bấm **Run Pipeline**. Sẽ thấy hộp tiến trình:

```
  ┌─────────────────────────────────────────────────────────┐
  │  ● Generating Proxies...                      [Running] │
  │  ████████████░░░░░░░░░░░░░░░░░░░░░░░  35%              │
  │  ┌────────┐  ┌────────┐  ┌────────┐  ┌────────┐       │
  │  │Proxies │  │Motion  │  │ YOLO   │  │  XML   │       │
  │  └────────┘  └────────┘  └────────┘  └────────┘       │
  │    ↑ Đang chạy bước này                                │
  └─────────────────────────────────────────────────────────┘
```

4 bước chạy lần lượt. Tùy số lượng clip, chờ khoảng **2–10 phút**.
Khi xong sẽ có **bảng thời gian từng bước** — chụp màn hình lại nhé!

---

## BƯỚC 7 — Xong! Kéo XML vào Premiere / Resolve

Khi thấy hộp xanh lá:

```
  ┌─────────────────────────────────────────────────────────┐
  │  ✓  Pipeline Complete                                   │
  │     Drag the XML file into your editor                  │
  │  /Users/TenBan/Desktop/output.xml   ◄── File này        │
  └─────────────────────────────────────────────────────────┘
```

- **Premiere Pro:** kéo file `output.xml` vào **Project Panel** → xong! 🎉
- **DaVinci Resolve:** File → Import → Timeline → chọn `output.xml`.

---

## Màu sắc trong editor sau khi import

| Màu clip | Nghĩa |
|----------|-------|
| 🔵 Xanh dương | Clip có **1–2 người** |
| 🟠 Cam | Clip có **3 người trở lên** |
| 🌸 Hồng | Clip **không có người** (cảnh nền, B-roll) |

---

## ❓ Hay Gặp Vấn Đề

**Mac chặn app, không mở được?**
→ Chuột phải → **Open** → **Open** (xem Bước 3).
→ Hoặc: System Settings → Privacy & Security → **Open Anyway**.

**Thanh vàng FFmpeg mãi không biến mất?**
→ Kiểm tra internet → chờ thêm, file tải ~80MB.

**Clip nào cũng màu hồng (BRolls) hết?**
→ Bấm **Advanced Settings** → đổi YOLO Model từ `nano` sang `yolov8s` hoặc `yolov8l`.

**Hộp đỏ báo lỗi?**
→ Kiểm tra lại đường dẫn thư mục clip có đúng không.
→ Xem log lỗi tại: mở **Finder** → menu **Go → Go to Folder** (⌘⇧G) → gõ:
```
~/Library/Logs/SteadyCut
```
→ mở file `steadycut.log`.

---

## ✅ Tóm Tắt Cực Ngắn

```
1️⃣  Copy SteadyCut-macOS-arm64.zip vào máy → bấm đúp giải nén
2️⃣  Chuột phải app SteadyCut → Open → Open  (chỉ lần đầu)
3️⃣  Chờ thanh vàng FFmpeg biến mất (lần đầu ~2 phút)
4️⃣  Điền thư mục clip + đường dẫn output.xml
5️⃣  Bấm "Run Pipeline" → chờ xong → chụp bảng thời gian
6️⃣  Kéo file XML vào Premiere Pro / Resolve
```
