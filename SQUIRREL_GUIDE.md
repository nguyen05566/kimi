# Hướng dẫn giữ Engine SQUIRREL24 (đã fix v3.1)

Bạn đã fix `botsqui.py` sang Embryo (RECTSTART native) ở commit `6af54e2` – khuyến nghị giữ.
Nếu **vẫn muốn dùng SQUIRREL24** (thích lối đánh Squirrel), hãy dùng file `botsqui_squirrel_fixed.py` đã vá.

## 1. Tại sao Embryo được khuyến nghị?
- **GameVH là 15x19 chữ nhật.** Embryo hỗ trợ `RECTSTART 15,19` native → thấy toàn bàn, không mất 4 hàng.
- **Squirrel chỉ hỗ trợ `START 15` vuông.** Phải dùng proxy trượt cửa sổ 15x15 trên 15x19 → dù fix tối ưu, vẫn mất quân khi bàn trải đều y=0..18 (19 hàng nhưng chỉ thấy 15).
- Nếu 2 quân quan trọng ở y=0 và y=18, không cửa sổ nào chứa cả 2 → Squirrel luôn thua thế dàn trải.

## 2. Đã fix gì cho Squirrel v3.1?
- **`_calculate_offset` tối ưu:** duyệt `offset 0..4` chọn cửa sổ chứa **nhiều quân nhất**, tie-break gần `last_y`. Log warning khi mất quân.
- **`BEGIN` về tâm 15x19:** `offset=2` → engine `(7,7)` → virtual `(7,9)` thay vì `(7,7)` lệch 2 dòng.
- **`RESTART` gửi lại `INFO rule/timeout/ponder`** như bot2.
- **`cwd=str(ENGINE_DIR)`** để wine tìm thấy `libgfortran-5.dll` + 7 DLL khác.
- **`detect_ag_binary` đa pattern + `rglob`** phòng zip giải nén khác cấu trúc.
- Giữ `FULL BOARD` + fallback `get_empty_near(last_y)` như bot2.

## 3. Cách dùng

### Giữ Embryo (khuyến nghị, hiện tại)
```bash
python3 botsqui.py  # ← đã là Embryo FIXED v3.1
```

### Chuyển sang Squirrel đã fix
```bash
cp botsqui_squirrel_fixed.py botsqui.py
# hoặc sửa .github/workflows/caro-bot.yml:
run: python3 botsqui_squirrel_fixed.py
```

Test local:
```bash
CARO_USER=... CARO_PASSWD=... python3 botsqui_squirrel_fixed.py
# chỉ test identity:
CARO_IDENTITY_TEST_ONLY=1 python3 botsqui_squirrel_fixed.py
# debug sâu:
CARO_DEBUG=1 python3 botsqui_squirrel_fixed.py
```

## 4. So sánh nhanh

| Engine | Bàn 15x19 | Protocol | Mất quân? | Tâm đầu |
|--------|-----------|----------|-----------|---------|
| Embryo (bot2) | native | RECTSTART 15,19 | Không | (7,9) chuẩn |
| Squirrel v3.0 lỗi | proxy last_y | START 15 | Có (offset chỉ last_y) | (7,7) lệch |
| Squirrel v3.1 fixed | proxy optimal | START 15 + BEGIN offset2 | Giảm thiểu, vẫn mất khi spread 19 | (7,9) chuẩn |

## 5. File trong repo
- `botsqui.py` – Embryo FIXED (đang chạy)
- `botsqui_squirrel_fixed.py` – Squirrel FIXED v3.1 (dự phòng)
- `BAO_CAO_DOI_CHIEU_bot2_vs_botsqui.md` – báo cáo chi tiết 962 dòng diff
- `SQUIRREL_GUIDE.md` – file này

