# 📖 Hướng Dẫn Chạy SteadyCut — Windows

> Đọc từng bước, làm đúng thứ tự. Không cần biết gì về máy tính cả!

---

## SteadyCut làm gì?

Bạn có nhiều clip video quay tay, bị rung lung tung.  
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
④ Xuất file XML → kéo vào Premiere Pro là xong 🎬
```

---

## BƯỚC 1 — Tải app về máy

Vào trang GitHub của dự án → tìm mục **Releases** (góc phải) → tải file:

```
SteadyCut-Windows-x64.zip
```

---

## BƯỚC 2 — Giải nén

Chuột phải vào file `.zip` vừa tải:

```
📦 SteadyCut-Windows-x64.zip
        │
        │  [Chuột phải]
        ▼
  ┌─────────────────────────┐
  │  Open                   │
  │  Extract All...   ◄─────┼── Bấm cái này
  │  Properties             │
  └─────────────────────────┘
        │
        ▼
  Chọn thư mục bất kỳ → bấm [Extract]
```

---

## BƯỚC 3 — Mở app

Vào thư mục vừa giải nén → bấm đúp vào **`SteadyCut.exe`**

Windows sẽ hỏi:

```
  ┌──────────────────────────────────────────┐
  │  Windows protected your PC               │
  │                                          │
  │  Microsoft Defender SmartScreen          │
  │  prevented an unrecognized app...        │
  │                                          │
  │            [More info] ◄── Bấm đây trước│
  └──────────────────────────────────────────┘

        Sau khi bấm "More info":

  ┌──────────────────────────────────────────┐
  │  Windows protected your PC               │
  │                                          │
  │  App: SteadyCut.exe                      │
  │  Publisher: Unknown publisher            │
  │                                          │
  │  [Don't run]    [Run anyway] ◄── Bấm đây│
  └──────────────────────────────────────────┘
```

> ✅ Bình thường thôi — Windows hay hỏi vậy với app mới chưa quen. Không phải virus.

---

## BƯỚC 4 — Chờ FFmpeg tải (chỉ lần đầu)

App mở ra → trình duyệt tự bật lên. Trên đầu trang sẽ có thanh vàng:

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
  │                                                             │
  │  Run Pipeline                                               │
  │                                                             │
  │  ┌─────────────────────────────────────────────────────┐   │
  │  │                                                     │   │
  │  │  RAW FOOTAGE DIRECTORY                              │   │
  │  │  ┌───────────────────────────────────────────────┐  │   │
  │  │  │ 📁  C:\Users\TenBan\Videos\ClipGoc            │  │   │
  │  │  └───────────────────────────────────────────────┘  │   │
  │  │     ↑ Nhập đường dẫn thư mục chứa clip gốc         │   │
  │  │                                                     │   │
  │  │  OUTPUT XML PATH                                    │   │
  │  │  ┌───────────────────────────────────────────────┐  │   │
  │  │  │ 📄  C:\Users\TenBan\Desktop\output.xml        │  │   │
  │  │  └───────────────────────────────────────────────┘  │   │
  │  │     ↑ Nhập đường dẫn nơi lưu file kết quả          │   │
  │  │                                                     │   │
  │  │  ▸ Advanced Settings    (không cần đụng vào)        │   │
  │  │  ─────────────────────────────────────────────────  │   │
  │  │  [          Run Pipeline          ]  ← Bấm nút này  │   │
  │  │                                                     │   │
  │  └─────────────────────────────────────────────────────┘   │
  └─────────────────────────────────────────────────────────────┘
```

### Cách tìm đường dẫn thư mục clip:

```
  Mở thư mục chứa clip trong File Explorer
         │
         ▼
  ┌──────────────────────────────────────────────┐
  │  ← →  📁 Videos > ClipGoc          🔍        │
  │  ──────────────────────────────────────────  │
  │  📁 ClipGoc                                  │
  │  ├── clip001.mp4                             │
  │  ├── clip002.mp4                             │
  │  └── clip003.mp4                             │
  └──────────────────────────────────────────────┘
         │
         │  Bấm vào thanh địa chỉ trên cùng (chỗ có chữ "ClipGoc")
         ▼
  ┌──────────────────────────────────────────────┐
  │  C:\Users\TenBan\Videos\ClipGoc    ◄── Copy  │
  └──────────────────────────────────────────────┘
         │
         │  Ctrl+A → Ctrl+C để copy → dán vào ô "Raw Footage Directory"
         ▼
  Xong!
```

### Đường dẫn output XML — ví dụ cụ thể:

Muốn lưu ra Desktop tên `output.xml` thì gõ:
```
C:\Users\TenBan\Desktop\output.xml
```
*(Thay `TenBan` bằng tên tài khoản Windows của bạn)*

---

## BƯỚC 6 — Chạy và chờ

Bấm **Run Pipeline**. Sẽ thấy hộp tiến trình xuất hiện:

```
  ┌─────────────────────────────────────────────────────────┐
  │  ● Generating Proxies...                      [Running] │
  │                                                         │
  │  ████████████░░░░░░░░░░░░░░░░░░░░░░░  35%              │
  │                                                         │
  │  ┌────────┐  ┌────────┐  ┌────────┐  ┌────────┐       │
  │  │ ⚙      │  │ 📊     │  │ 🎯     │  │ 🎬     │       │
  │  │Proxies │  │Motion  │  │ YOLO   │  │  XML   │       │
  │  └────────┘  └────────┘  └────────┘  └────────┘       │
  │    ↑ Đang chạy bước này                                │
  └─────────────────────────────────────────────────────────┘
```

4 bước chạy lần lượt. Tùy số lượng clip, chờ khoảng **2–10 phút**.

---

## BƯỚC 7 — Xong! Kéo XML vào Premiere

Khi thấy hộp xanh lá:

```
  ┌─────────────────────────────────────────────────────────┐
  │  ✓  Pipeline Complete                                   │
  │     Drag the XML file into Premiere Pro's Project Panel │
  │                                                         │
  │  C:\Users\TenBan\Desktop\output.xml   ◄── File này     │
  └─────────────────────────────────────────────────────────┘
```

Mở **Premiere Pro** → kéo file `output.xml` vào **Project Panel** (góc trái dưới) → xong! 🎉

---

## Màu sắc trong Premiere sau khi import

| Màu clip | Nghĩa |
|----------|-------|
| 🔵 Xanh dương | Clip có **1–2 người** |
| 🟠 Cam | Clip có **3 người trở lên** |
| 🌸 Hồng | Clip **không có người** (cảnh nền, B-roll) |

---

## ❓ Hay Gặp Vấn Đề

**Windows chặn app?**
→ Bấm "More info" → "Run anyway" (xem Bước 3)

**Thanh vàng FFmpeg mãi không biến mất?**
→ Kiểm tra internet → chờ thêm, file tải ~80MB

**Clip nào cũng màu hồng (BRolls) hết?**
→ Bấm **Advanced Settings** → đổi YOLO Model từ `nano` sang `yolov8s` hoặc `yolov8l`

**Hộp đỏ báo lỗi xuất hiện?**
```
  ┌──────────────────────────────────────┐
  │  ✕  Pipeline Failed                  │
  │     [nội dung lỗi ở đây]            │
  └──────────────────────────────────────┘
```
→ Kiểm tra lại đường dẫn thư mục clip có đúng không  
→ Xem log lỗi tại: nhấn `Windows + R` → gõ `%APPDATA%\SteadyCut\logs` → mở file `steadycut.log`

---

## ✅ Tóm Tắt Cực Ngắn

```
1️⃣  Tải SteadyCut-Windows-x64.zip
2️⃣  Giải nén → bấm đúp SteadyCut.exe → "More info" → "Run anyway"
3️⃣  Chờ thanh vàng FFmpeg biến mất (lần đầu ~2 phút)
4️⃣  Điền thư mục clip + đường dẫn output.xml
5️⃣  Bấm "Run Pipeline" → chờ xong
6️⃣  Kéo file XML vào Premiere Pro
```
