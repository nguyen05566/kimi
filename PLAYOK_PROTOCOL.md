# Giao thức playok.com (gomoku) — ghi chép dịch ngược

Nguồn: `https://www.playok.com/j/gm.js?264` (đã minify). Mọi mục dưới đây đều **đã kiểm chứng bằng kết nối thật**
với tài khoản `nguyen066`, trừ chỗ nào ghi rõ "chưa xác minh".

## 1. Vào được client

Trang `/en/gomoku/` mặc định chỉ là trang giới thiệu. Nút Play chạy `gstart('gm')`:

```js
document.cookie = 'kbeta=gm;path=/';
window.location.reload();       // cùng URL, server trả trang game khi thấy cookie
```

Nên muốn lấy trang game: GET `https://www.playok.com/en/gomoku/` kèm cookie `kbeta=gm`.

Trang game nhúng 2 số **đổi mỗi lần tải**:

```html
window.ge = 16932484;
window.ap = 3928609259;
```

## 2. Kênh truyền

```js
host  = window.k2hconnect || location.hostname.replace("www.", "x.")   // x.playok.com
ports = window.k2hcons   || ["wss:17003", "wss:443", "https:443"]
```

| Kênh | Địa chỉ | Thực tế |
|---|---|---|
| WebSocket | `wss://x.playok.com:17003/ws/` | ❌ nginx trả **502** từ sandbox |
| WebSocket | `wss://x.playok.com:443/ws/` | ❌ 502 |
| Long-poll | `https://x.playok.com` | ✅ **chạy tốt** |

Long-poll (hàm `Xe`/`ef`/`cf` trong gm.js):

```
POST /r/0        body="1"     -> trả về channel id (chuỗi số)
POST /w/{chan}   body=<json>  -> GỬI (nhiều gói nối bằng "\n")
POST /r/{chan}   body=rỗng    -> NHẬN (long-poll, trả nhiều dòng json)
```

Cứ 30 giây không gửi gì thì đẩy `{"i":[]}` để giữ kết nối.
Nhận `i[0] == 1` thì phải trả lời `{"i":[2]}` (ping/pong), nếu không server ngắt.

## 3. Định dạng gói tin

```js
bf = function(a,b){ return '{"i":['+a.join()+']' + (b.length ? ',"s":['+b.join()+']' : '') + '}' }
```

Tức JSON: `{"i":[<các số>], "s":["<các chuỗi>"]}` — `s` bỏ đi nếu rỗng.

## 4. Gói bắt tay (bắt buộc gửi ngay sau khi mở kênh)

```json
{"i":[1712], "s":[
   "<ksession_phần_đầu>+|<ap>|<ge>",
   "en", "b", "<cookie kroom, thường rỗng>", "<user agent>",
   "/1/0", "w", "1920x1080 1",
   "ref:https://www.playok.com/en/gomoku/", "ver:264"
]}
```

- `1712` lấy từ `k2start: new Wa({Tf:1712})`
- Phần đầu ksession = `ksession.split(':')[0]`, cộng thêm `+`
- Đăng nhập thành công server trả `{"i":[18,0,1],"s":["<tên>","en","","gm"]}`

## 5. Bảng mã lệnh

### Gửi đi (client → server)

| Mã | Ý nghĩa | Dạng | Trạng thái |
|---|---|---|---|
| 20 | chat sảnh | `[20]` + `[text]` | từ mã nguồn |
| 71 | **tạo bàn mới** | `[71]` | ✅ đã chạy thật |
| 73 | **rời bàn** | `[73, tableId]` | ✅ đã chạy thật |
| 81 | chat trong bàn | `[81, tableId]` + `[text]` | từ mã nguồn |
| 83 | **ngồi ghế** | `[83, tableId, seat]` | ✅ đã chạy thật |
| 84 | rời ghế / đá khỏi ghế | `[84, tableId, seat]` | từ mã nguồn |
| 85 | (nút ở bảng thống kê bàn) | `[85, tableId]` | chưa rõ |
| 92 | **đánh nước** | `[92, tableId, pos, (tuỳ chọn), <thời gian đã dùng /100s>]` | chưa xác minh |
| 93 | hành động trong ván (xin hoà/đầu hàng/undo?) | `[93, tableId, x, ..., time]` | chưa rõ |
| 94/95/96 | mời chơi / danh sách / tuỳ chọn | | chưa rõ |
| 2 | trả lời ping | `[2]` | ✅ |

### Nhận về (server → client)

| Mã | Ý nghĩa | Ví dụ thật |
|---|---|---|
| 1 | ping (phải trả `[2]`) | |
| 18 | đăng nhập OK | `[18,0,1]` + `["nguyen066","en","","gm"]` |
| 25 | thông tin người chơi | `[25,0,109,1111]` + `["tdg3181g"]` (1111 = elo) |
| 27 | danh sách người trong sảnh | `[27,3,0,16,114,1677,...]` |
| 31 | thiết lập giao diện | `set_selfname`, `set_rank`… |
| 33 | elo của mình | `[33,1187]` |
| 51 | bảng chữ đa ngôn ngữ | |
| 70 | **trạng thái một bàn** | `[70,112,0,1,0]` + `["5m","nguyen066",""]` |
| 71 | danh sách toàn bộ bàn | `[71,4,3,103,0,0,1,...]` |
| 72 | bàn đóng | `[72,103]` |
| 90 | **trạng thái ván của BÀN MÌNH** | `[90,112,4,-1,8,3,1,1,2,0,0]` |
| 91 | kèm sau 90 cho bàn mình | `[91,112]` |

Gói `70`: `[70, tableId, cờ, ghế0_có_người, ghế1_có_người]` + `[thời gian, tên0, tên1]`.
Gói `90`/`91` **chỉ gửi cho bàn mà mình vừa mở** → dùng để biết id bàn của mình sau khi gửi `[71]`.

## 6. Toạ độ bàn cờ

Bàn **15×15** (không phải 14×14 — 14×14 là số ô vuông, còn quân đặt trên **225 giao điểm**).

Từ `gm.js`:

```js
f.reset = function(){ for(var a=0; 15>a; a++) for(var b=0; 15>b; b++) this.D[a][b] = -1; }
var e = d % 15, g = Math.floor(d/15) % 15, d = Math.floor(d/225) % 2;   // cột, hàng, màu
b.push(a.charCodeAt(0) - 97 + 15*(15 - parseInt(a.substring(1),10)) + 225*d);
```

- `pos = cột + 15*hàng`  (0…224)
- `+225` nếu là quân trắng
- Ký hiệu chữ: cột `a`–`o`, hàng `1`–`15` đếm **từ trên xuống**
- Luật: **đúng 5 quân** mới thắng (standard, không phải freestyle)

## 7. Engine

gomocalc.com **không có API** — nó chạy **Rapfi biên dịch WebAssembly ngay trong trình duyệt**
(`self["Rapfi"]({...})`, worker `engine-warpper.worker.js`, trọng số `rapfi.data`).
Không có gì để gọi từ xa; thay vào đó chạy Rapfi bản Linux tại chỗ.

Rapfi nói **giao thức Gomocup** giống hệt Embryo:

```
START 15            -> OK
INFO rule 1         (1 = standard, đúng 5 quân)
INFO timeout_turn 3000
BEGIN               -> "7,7"
TURN 7,8            -> "6,9"
BOARD ... DONE
```

Bản dùng: `Rapfi-engine.7z` release `250615`, file `pbrain-rapfi-linux-clang-avx2`
cùng `config.toml`, `model210901.bin`, `mix9svqstandard_bs15.bin.lz4`.

---

## Cập nhật 2026-08-21 — GIẢI XONG bot cờ tướng (nước đi đã lên bàn thật)

### 1. Gói 90 — nguồn sự thật DUY NHẤT về lượt đi

```
[90, tid, state, turnSeat, ..., 5, n, <n nước hợp lệ>, ..., clk0, clk1]
```

Ý nghĩa `state` (kiểm chứng bằng ván thật, cả gomoku lẫn cờ tướng):

| state | nghĩa |
|---|---|
| `4`  | bàn đang chờ, chưa vào ván (`turnSeat = -1`) |
| `7`  | ván ĐANG CHẠY, lượt của ĐỐI THỦ |
| `9 + n` | ván ĐANG CHẠY, lượt CỦA MÌNH, `n` = số nước hợp lệ |

Ví dụ thật: mở cuộc quân đỏ có đúng **44** nước hợp lệ → `state = 53`.
Sau `P7+1` của đối thủ còn 43 → `52`; 32 → `41`; 39 → `48`; 49 → `58`.

→ **Không bao giờ so `state == 9`.** Quy tắc đúng:
`đang chơi ⇔ turnSeat >= 0 và state != 4`; `tới lượt mình ⇔ turnSeat == ghế của mình`.

**Gói 88 KHÔNG phải lượt đi** — chỉ là cờ bật/tắt nút giao diện
(`[88, tid, 71, 10, 0]`, `[88, tid, 79, 3, 0]`...). Lấy lượt từ đây là
nguyên nhân bot gửi nước lúc bàn còn đang chờ và server nuốt im lặng.

### 2. Danh sách nước hợp lệ (quà tặng miễn phí của server)

Trong gói 90 lúc tới lượt mình: `i[10] == 5`, `i[11] == n`, rồi `n` số packed.
Giải mã ra đúng 44 nước mở cuộc chuẩn của cờ tướng (`a3a4, c3c4, ..., b2b3,
b2b4, ..., h2e2`). Dùng để **tự kiểm tra bàn cờ của bot có lệch không**
trước khi gửi nước.

### 3. Gói 91 — lịch sử nước đi

```
[91, tid, packed, thời_gian, packed, thời_gian, ...]
```
Số **âm** chen vào trước một nước = nước đó có ăn quân (chỉ là gợi ý hiển thị).
Phải đọc theo **cặp (nước, thời gian)** — lấy "mọi số > 0" là sai, sẽ nuốt
luôn trường thời gian làm nước đi khi ván có đồng hồ khác 0.

Đã dựng lại trọn vẹn 1 ván 85 nước thật từ gói 91 và ra đúng thế cờ chiếu bí.

### 4. Gói 92 — nước đi

- Gửi:  `[92, tid, 1, packed, thời_gian_1/100s]`
- Nhận: `[92, tid, packed]` hoặc `[92, tid, -<mã ăn quân>, packed]`
  kèm `s = ['C2.5']` (ký hiệu cờ tướng) → dùng để đối chiếu.

`packed = ô_đến*100 + ô_đi`, `ô = hàng*10 + cột`, hàng 0 = phía ĐEN.

### 5. Những cái GIẾT KÊNH long-poll (đều trả 502 rồi /r/ 404 vĩnh viễn)

1. **Gọi `/r/{id}` trước khi gửi frame handshake** → 404 ngay lập tức.
   Chỉ được mở long-poll SAU khi `/w/` handshake trả 200.
2. **Cắt long-poll giữa chừng** (gọi `/r/` với timeout 2 giây trong vòng lặp
   chính rồi huỷ, lặp đi lặp lại) → server huỷ kênh sau ~30 giây.
   Bắt buộc: long-poll chạy **luồng riêng**, timeout ~45s, để chạy hết.
3. **Chat bằng tài khoản mới** (`[81, tid] + ["..."]`) — server trả
   "as a new user you cannot use chat yet" rồi đá kênh.
4. **Ngồi vào ghế đã có người** (`[83, tid, 0]` khi ghế 0 bận) → 502.
   Phải đọc gói 70 xem ghế nào trống trước.
5. JSON có khoảng trắng (`separators=(",", ":")` là bắt buộc).

### 6. Nhận ra "bàn của mình"

Đừng đoán theo gói 88/90/91 đầu tiên nhìn thấy. Cách đúng: gói
`[70, tid, ...] + ['20m', 'ten0', 'ten1']` — tên mình nằm ở ghế nào thì
đó là bàn và ghế của mình. Cách này còn bắt được cả trường hợp server
**tự khôi phục bàn cũ** khi đăng nhập lại (bot cũ vội tạo bàn mới).

---

## Cập nhật 2026-08-21 (lần 2) — GOMOKU chạy được + ĐÍNH CHÍNH gói 90

### 0. ĐÍNH CHÍNH: `i[2]` của gói 90 KHÔNG phải "mã trạng thái"

Hôm qua tôi ghi "state = 9 + số nước hợp lệ". **Sai.** Đọc thẳng dispatcher
`xb()` trong `gm.js`:

```js
case 90:
  if (b.length < b[2] + 4) break;
  ...
  0 < b[2] && (a.ia = b[3]);          // a.ia = GHẾ TỚI LƯỢT
  d = 3 + b[2];
  for (c = 5; c < d;) { e = 5 > b[c] ? Fe(a,b,c,d) : a.Ae(b,c); ... }
```

Sự thật:

```
[90, tid, ĐỘ_DÀI_HEADER, turnSeat, ?, <chuỗi thẻ TLV>, sốCột, <ids>, <giá trị/người chơi>]
```

- `i[2]` = **độ dài khối header**, không phải trạng thái. Nó bằng `9 + n`
  ở cờ tướng chỉ vì danh sách `n` nước hợp lệ nằm trong header — trùng hợp.
- `i[3]` = **ghế tới lượt**, đây mới là thứ duy nhất cần đọc.
- Header là chuỗi thẻ TLV, quét từ chỉ số 5 tới `3 + i[2]`:

| Thẻ | Số ô | Nội dung |
|---|---|---|
| 1 | 3 | đồng hồ |
| 2 | 4 | đồng hồ có gia giờ |
| 3 | 2 | cờ; **bit 2 = `ad`** (hoán ghế ↔ màu quân) |
| ≥5 | tuỳ game | `a.Ae()` riêng từng game — cờ tướng: danh sách nước hợp lệ; gomoku: chế độ swap2 |

Bot cờ tướng vẫn chạy đúng vì `turnSeat >= 0` là điều kiện tương đương,
nhưng cách đọc trong tài liệu cũ là ăn may chứ không phải hiểu đúng.

### 1. Gomoku — gửi nước (lỗi khiến bot cũ câm suốt)

```js
f.Wb = function(a, b) {
    a = [92, this.K, a];
    if (typeof b != "undefined") a.push(b);
    a.push(Math.floor((Date.now() - this.ob.v) / 100));
    this.send(a, null);
};
f.zb = ... this.fa.Wb(0, x + 15*y) ...        // bấm chuột lên bàn
```

→ **`[92, tid, 0, pos, thời_gian]`**. Cờ tướng là `1` ở giữa, **gomoku là `0`**.
Bản cũ gửi `1` nên server đọc phần tử `[2] = 1` làm nước đi → vô nghĩa → bỏ qua
im lặng, không báo lỗi gì. Sai đúng một chữ số.

### 2. Gomoku — nhận nước và mã hoá ô

```js
f.Vd = function(a) { for (b=2; b<a.length; b++) this.Ib.push(a[b]); }   // gói 92
f.Db = ... e = d%15; g = Math.floor(d/15)%15; d = Math.floor(d/225)%2 ...
```

- Mọi phần tử từ chỉ số 2 của gói 92 đều là nước đi.
- `v % 15` = cột, `(v / 15) % 15` = hàng, **`(v / 225) % 2` = MÀU QUÂN**.
- `v == -1` bỏ qua; `v >= 450` là thẻ chọn màu của swap2, không phải nước.
- Gói 91 của gomoku là **danh sách phẳng**, không phải cặp (nước, thời gian)
  như cờ tướng.

Bit màu quan trọng: swap2 cho một người đặt 3 quân liên tiếp nên **không**
suy được chủ nhân nước đi theo kiểu luân phiên. Màu của mình lấy theo client:
`màu = ad ? 1 - ghế : ghế`.

### 3. Gomoku — luật swap2

Thẻ 5 trong header gói 90 mang `Ab` (`f.Ae = function(a,b){return 5==a[b] ?
(Bf(this.D, a.slice(b+1,b+2)), b+2) : b}`):

| `Ab` | Nghĩa |
|---|---|
| 0 | bình thường, cứ đặt quân |
| 3 | **bắt buộc chọn màu**, client chặn đặt quân (`3 != this.Ab`) |
| 4 | chờ đối thủ chọn |
| 5 | được chọn màu **hoặc** đặt thêm quân |

Chọn màu = gửi nước với giá trị đặc biệt: **450 = ĐEN, 900 = TRẮNG**
(`Wb(0,450)` / `Wb(0,900)`). Không xử lý `Ab == 3` thì bot đứng hình tới hết giờ.

### 4. Bẫy của engine Rapfi (không liên quan playok nhưng mất giờ)

`config.toml` khai 3 bộ trọng số (freestyle / standard / renju) nhưng bản tải
về chỉ có bộ standard. Rapfi nạp bộ ĐẦU TIÊN lúc `START`, không thấy file thì in
`ERROR Evaluator mix9svq failed to initialized` rồi **âm thầm tụt xuống hàm
lượng giá cổ điển** — engine vẫn chạy nên rất dễ bỏ sót, nhưng yếu hẳn.
Cùng một thế cờ: eval `-183` khi hỏng, `-499` khi nạp đúng mạng nơ-ron.
`rapfi.py` nay tự xoá các mục weight thiếu file.

---

## Cập nhật 2026-08-21 (lần 3) — Thiết lập bàn: chỉ chơi với người 1350+

### Khung gói

```js
function V(a, b, c) { a.send([82, a.K, c], [b]); }   // [82, tid, giá_trị] + [tên]
```

Server báo lại toàn bộ thiết lập bằng **gói 89**:
`[89, tid, v1, v2, ...] + ["tên1", "tên2", ...]` (hàm `we()` ghép theo thứ tự).

Tên thiết lập đọc được từ client:

| Tên | Ý nghĩa |
|---|---|
| `ttype` | loại bàn / hạn chế elo |
| `tg` | thời gian ván | 
| `tm` | thời gian cộng thêm |
| `ud` | cấm đi lại (no undo) |
| `gtype` | 1 = tính elo, 0 = không tính (ô "non-rated (x)") |
| `pro` | luật swap2 (chỉ gomoku) |

**Chỉ người tạo bàn** đổi được (server chat: *"you are now the table operator -
you can change settings and boot users"*). Ngồi nhờ bàn người khác thì không.

### Mã `ttype`

```js
a.na = u(e, "select", { onchange: function() {
    var l = this.selectedIndex;
    V(a, "ttype", null != a.j.$ && 0 < l ? (8 <= l ? 2 : l + 2) : 2 * l);
}}, g);
```

`l` là thứ tự mục chọn: 0 = public, 1..7 = bảy mức elo, 8 = private. Suy ra:

| Mục chọn | `l` | **`ttype` gửi đi** |
|---|---|---|
| public | 0 | **0** |
| 1200+ | 1 | **3** |
| **1350+** | 2 | **4** |
| 1500+ | 3 | **5** |
| 1650+ | 4 | **6** |
| 1800+ | 5 | **7** |
| 1950+ | 6 | **8** |
| 2100+ | 7 | **9** |
| private | 8 | **2** |

Chiều ngược lại (`we()`): `index = (e > 1) ? (e == 2 ? 8 : e - 2) : (e >> 1)` —
đã kiểm tra khớp cả hai chiều cho cả 9 mục.

Đã chạy thật: gửi `{"i":[82,tid,4],"s":["ttype"]}` → server trả gói 89 với
`ttype = 4`, tức bàn thành **1350+**.

### Bảy ngưỡng elo lấy từ đâu

Không phải hằng số. Server gửi chuỗi cấu hình `set_rank`, client đọc:

```js
"set_rank" == a && (this.$ = b.split(" ")
                     .filter(function(c,d){ return 0 == d % 2 })
                     .map(function(c){ return parseInt(c,10) }))
function Ra(a,b){ return a.$ ? 1 + a.$[b] : 0 }
```

rồi dựng bảy nhãn: `h = 0..6`, `h` chẵn → `Ra(1 + h/2)`, `h` lẻ → trung bình hai
mốc liền kề. Với gomoku ra đúng 1200 / 1350 / 1500 / 1650 / 1800 / 1950 / 2100.
Game khác có thể khác số, nên bot bắt `set_rank` lúc đăng nhập và tự dựng thang.

### Bẫy kèm theo (tự gây ra, đã sửa)

Bot đang ngồi đánh mà vẫn chạy vòng săn bàn: nó gửi `[72]`/`[83]`/`[85]` sang
bàn khác giữa ván → `self.table` và `self.my_seat` bị ghi đè, **ghế nhảy 0 ↔ 1
giữa ván** nên bot đánh nhầm màu; nặng hơn là `[85]` gửi tới bàn không còn hợp lệ
trả **502 → chết kênh** (quan sát 3 lần trong một phiên). Sửa: `try_join_table()`
thoát ngay nếu `seated` hoặc `in_game`, và sau khi vào lại kênh phải chờ ~8 giây
cho server khôi phục bàn cũ rồi mới cho phép săn bàn.

### Bổ sung: gói 70 mang sẵn `ttype` của bàn

```
[70, tid, ttype, cờ_ghế0, cờ_ghế1] + [thời_gian, tên0, tên1]
```

Kiểm chứng: đặt bàn của bot thành 1350+ (`ttype=4`) rồi soi bằng **phiên khách
riêng biệt** — khách thấy `[70, 101, 4, 1, 0] + ['5m', 'nguyen066', '']`.
Đối chiếu các bàn khác trong cùng sảnh: `0` (public), `3` (1200+), `5` (1500+)
— khớp đúng bảng mã `ttype`. Nhờ vậy có thể lọc bàn theo mức elo ngay từ danh
sách sảnh mà không cần vào từng bàn.

### Vì sao bot nên TỰ TẠO BÀN

Chỉ chủ bàn mới đổi được thiết lập. Ngồi nhờ bàn người khác thì bot phải chấp
nhận mức của họ (thường là `public`). Muốn "chỉ chơi với người 1350+" thì bot
phải tự tạo bàn — nên mặc định `--join-others` đã TẮT.

Kèm theo hai điều chỉnh bắt buộc khi bot ngồi một mình chờ khách:
- chỉ bắn `[85]` (bắt đầu) khi bàn đã đủ hai người, không thì cứ 4 giây một gói
  suốt hàng chục phút;
- nhận gói `72` (bàn đóng) thì dựng bàn mới, không thì bot ngồi với số bàn đã
  chết.

---

## Khảo sát 2026-08-21 — Reversi/Othello (https://www.playok.com/vi/reversi/)

Đã bóc `https://www.playok.com/j/rv.js?264`. Giao thức **giống hệt** hai game kia,
chỉ khác vài con số:

| | cờ tướng | gomoku | **reversi** |
|---|---|---|---|
| cookie | `kbeta=xq` | `kbeta=gm` | **`kbeta=rv`** |
| mã handshake | 1728 | 1712 | **1713** |
| số giữa gói 92 | 1 | 0 | **2** |
| mã ô | `ô_đến*100+ô_đi` | `x + 15*y` | **`x + 8*y`** |

```js
f.ee = function(a, b) {                    // giống Wb của gomoku
    a = [92, this.K, a];
    if (typeof b != "undefined") a.push(b);
    a.push(Math.floor((Date.now() - this.nb.A) / 100));
    this.send(a, null);
};
... this.fa.ee(2, a + 8*b)                 // chỗ bấm chuột lên bàn
window.k2start = function(){ new Xa({Of:Gf, Rf:1713, Ff:2, ig:"thcol0nl"}) };
```

Giải mã giá trị nhận về (hàm `Fb` + `Ef`):

```js
d = v % 8;                    // cột
e = Math.floor(v/8) % 8;      // hàng
g = Math.floor(v/64) % 2;     // MÀU:  0 = ĐEN (đi trước), 1 = TRẮNG
v == -1                       // BỎ LƯỢT (pass) — Othello có nước bỏ lượt
Ef(v) -> "D3" (màu 0, chữ HOA) hoặc "d3" (màu 1, chữ thường)
```

Thế khởi đầu trong `reset()`: `C[3][3]=1, C[3][4]=0, C[4][3]=0, C[4][4]=1`
→ d4/e5 trắng, e4/d5 đen — đúng luật Othello chuẩn.

Gói 92 nhận về chỉ mang **một** giá trị ở chỉ số 2 (`f.Od = a => a[2]`),
khác gomoku (đẩy mọi phần tử từ chỉ số 2).

Client tự tính nước hợp lệ bằng hàm `Df()`, nhưng server vẫn gửi danh sách ô
hợp lệ xuống (`Ff(a,b){a.rb=b}`) — nhiều khả năng nằm ở thẻ ≥5 trong header
gói 90 như cờ tướng, cần xác minh khi chạy thật.

### Engine

- **Edax** (C, GPL, `abulmo/edax-reversi`, v4.6) — chuẩn mực mã nguồn mở, dùng
  cả trong bài báo *"Othello is Solved"*. **Đã build và chạy thử tại đây**:
  `make build ARCH=x86-64-v3 COMP=gcc CC=gcc` mất **~2 giây**, binary 574 KB;
  `eval.dat` 7 MB tải từ release v4.4. Giao thức chữ đơn giản:
  `setboard <64 ký tự> X` → `go` → in `Edax plays D3`. Ký tự: `X` đen, `O` trắng,
  `-` trống — khớp thẳng với màu 0/1 của playok.
- **Egaroucid** (C++, `Nyanyan/Egaroucid`) — tác giả công bố mạnh hơn Edax một
  chút; bản console chạy Linux nhưng **phải tự build**, không có sẵn nhị phân.

Kết luận: làm bot reversi hoàn toàn khả thi, và còn NHẸ hơn hai bot kia
(Edax 574 KB + 7 MB eval, so với Pikafish 53 MB NNUE).
