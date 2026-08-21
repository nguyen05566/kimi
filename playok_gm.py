#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
PlayOK Gomoku BOT — dùng engine Rapfi.

Dùng lại nguyên tầng mạng đã chạy ổn của bot cờ tướng (bot_xq.PollingTransport):
long-poll luồng riêng, handshake trước, không chat, không ngồi ghế đã có người.

Khác biệt so với cờ tướng (đọc thẳng từ https://www.playok.com/j/gm.js):

    f.Wb = function(a, b) {            // hàm GỬI NƯỚC
        a = [92, this.K, a];
        if (typeof b != "undefined") a.push(b);
        a.push(Math.floor((Date.now() - this.ob.v) / 100));
        this.send(a, null);
    };
    f.zb = ... this.fa.Wb(0, x + 15*y) ...      // bấm chuột lên bàn cờ

  -> khung gửi đúng là  [92, tid, 0, pos, thời_gian]
     Cờ tướng là số 1 ở giữa, GOMOKU LÀ SỐ 0. Bản cũ gửi 1 nên server đọc
     phần tử [2] = 1 làm nước đi -> vô nghĩa -> bỏ qua im lặng. Đúng 1 chữ số.

    f.Vd = function(a) { for (b=2; b<a.length; b++) this.Ib.push(a[b]); }
  -> nhận: mọi phần tử từ index 2 của gói 92 đều là nước đi.

    f.Db = ... e = d%15; g = Math.floor(d/15)%15; d = Math.floor(d/225)%2 ...
  -> pos%15 = cột, (pos/15)%15 = hàng, (pos/225)%2 = MÀU QUÂN.
     Giá trị -1 = bỏ qua, >= 450 = thẻ chọn màu của luật swap2 (450 đen,
     900 trắng) chứ không phải nước đi.
"""
import argparse
import json
import os
import re
import threading
import time
import urllib.request

from playok_xq import (PollingTransport, WebSocketTransport, UA, WWW, HOST,
                    CODE_SETTING, CODE_SETTINGS_STATE, ttype_code,
                    ttype_labels, parse_set_rank)
from rapfi import Rapfi

# ----------------------------------------------------------------------------
# 1) CẤU HÌNH
# ----------------------------------------------------------------------------
COOKIES = {
    "kt": os.environ.get("PLAYOK_KT", "cckn"),
    "kguest": "0",
    "ku": os.environ.get("PLAYOK_KU", "dcb10af4"),
    "ksession": os.environ.get(
        "PLAYOK_KSESSION", "bf1fc4171ce46cc0:nguyen066:e184"),
    "kbeta": "gm",          # gm = gomoku (xq = cờ tướng)
    "kbexp": "0",
}

GAME_URL = WWW + "/en/gomoku/"
CODE_SUBSCRIBE = 1712    # window.k2start: new Wa({Tf:1712}) trong gm.js
CODE_PING = 2
CODE_CHAT = 81
CODE_UI = 88             # cờ nút giao diện, KHÔNG phải lượt đi
CODE_GAME = 90           # [90, tid, độ_dài_header, turnSeat, ...]
CODE_HISTORY = 91        # [91, tid, ...toàn bộ nước đã đi]
CODE_MOVE = 92
CODE_NEW_TABLE = 71

SIZE = 15
AREA = SIZE * SIZE       # 225


def is_move_value(v):
    """-1 = trống/bỏ qua; >= 450 = thẻ chọn màu luật swap2, không phải nước."""
    return isinstance(v, int) and 0 <= v < 450


def val_to_xy(v):
    v %= AREA
    return v % SIZE, v // SIZE


def xy_to_pos(x, y):
    return x + SIZE * y


def to_label(v):
    x, y = val_to_xy(v)
    return f"{chr(97 + x)}{SIZE - y}"       # zf() trong gm.js


# Thẻ chọn màu của luật swap2 (Te() trong gm.js: "black"->450, "white"->900)
PICK_BLACK = 450
PICK_WHITE = 900

# Giá trị Ab (chế độ swap2), đọc từ f.se() và f.zb():
#   0 = bình thường, cứ đặt quân
#   3 = BẮT BUỘC chọn màu, không được đặt quân (`3 != this.Ab` chặn click)
#   4 = chờ đối thủ chọn (bảng thông báo msg2)
#   5 = được chọn màu HOẶC đặt thêm quân
MODE_NORMAL, MODE_MUST_PICK, MODE_WAIT_PICK, MODE_MAY_PICK = 0, 3, 4, 5


def parse_header(i):
    """Đọc khối header gói 90 -> (turnSeat, ad, mode).

    Cấu trúc thật (hàm Fe() + Ae() trong gm.js), KHÔNG phải "mã trạng thái":
        i[2] = ĐỘ DÀI khối header
        i[3] = ghế tới lượt   (`0 < b[2] && (a.ia = b[3])`)
        i[5..3+i[2]) = chuỗi thẻ TLV:
            thẻ 1 -> 3 số (đồng hồ)     thẻ 2 -> 4 số (đồng hồ có gia giờ)
            thẻ 3 -> 2 số: cờ; bit 2 = ad (hoán ghế <-> màu quân)
            thẻ 5 -> 2 số: Ab, chế độ swap2   (cờ tướng dùng thẻ 5 cho việc khác)
    """
    if len(i) < 4 or i[2] <= 0:
        return -1, None, MODE_NORMAL
    end = min(3 + i[2], len(i))
    turn, ad, mode = i[3], None, MODE_NORMAL
    c = 5
    while c < end:
        tag = i[c]
        if tag in (1, 2):
            step = 4 if tag == 2 else 3
            if c + step > end:
                break
            c += step
        elif tag == 3:
            if c + 2 > end:
                break
            ad = bool(i[c + 1] & 2)
            c += 2
        elif tag == 5:
            if c + 2 > end:
                break
            mode = i[c + 1]
            c += 2
        else:
            break
    return turn, ad, mode


# ----------------------------------------------------------------------------
# 2) BOT
# ----------------------------------------------------------------------------
class Bot:
    def __init__(self, transport, engine, create_table=False):
        self.t = transport
        self.engine = engine
        self.moves = []          # danh sách giá trị thô theo đúng thứ tự đi
        self.table = None
        self.tables = {}
        self.want_room = ""
        self.room_base = None
        self.my_seat = 0
        self.seated = False
        self.joined = set()
        self.turn_seat = -1
        self.ad = None           # cờ hoán ghế <-> màu quân (thẻ 3 gói 90)
        self.mode = MODE_NORMAL  # chế độ swap2 (thẻ 5 gói 90)
        self.in_game = False
        self.thinking = False
        self.movetime = 3000
        self.ap = self.ge = None
        self.myname = None
        self.await_new_table = False
        self.ranks = None        # thang elo server gửi qua set_rank
        self.want_ttype = None   # hạn chế elo muốn đặt cho bàn tự tạo
        self.create_table = create_table
        self.avoid_swap2 = True
        self._lock = threading.Lock()

    # ---------------- phiên ----------------
    def fetch_session(self):
        req = urllib.request.Request(GAME_URL)
        req.add_header("User-Agent", UA)
        req.add_header("Cookie", "; ".join(f"{k}={v}" for k, v in COOKIES.items()))
        html = ""
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                html = r.read().decode(errors="replace")
        except Exception as e:
            print(f"[bot] lỗi lấy phiên: {e}")
        ap = re.search(r"window\.ap\s*=\s*(\d+)", html)
        ge = re.search(r"window\.ge\s*=\s*(\d+)", html)
        self.ap = ap.group(1) if ap else "0"
        self.ge = ge.group(1) if ge else "0"
        print(f"[bot] phiên ap={self.ap} ge={self.ge}")

    def login(self):
        ksession = COOKIES.get("ksession", "")
        prefix = ksession.split(":")[0] if ksession else "guest"
        nick = f"{prefix}+|{self.ap}|{self.ge}"
        self.t.send_frame({"i": [CODE_SUBSCRIBE],
                           "s": [nick, "en", "b", "", UA,
                                 f"/{int(time.time())}/1", "w", "1920x1080 1",
                                 f"ref:{GAME_URL}", "ver:264"]})
        print("[bot] gửi đăng nhập")

    # ---------------- nhận gói ----------------
    def on_frame(self, obj):
        i = obj.get("i", [])
        s = obj.get("s", [])
        if not i:
            return
        code = i[0]
        if code == -1 and s and str(s[0]).startswith("login"):
            # Server báo phiên này bị PHIÊN KHÁC cùng tài khoản đá ra.
            # PlayOK chỉ cho 1 phiên/tài khoản: nếu trình duyệt của bạn đang
            # mở playok bằng chính tài khoản này thì hai bên sẽ đá nhau vô tận.
            self.kicked_at = time.time()
            print("[bot] ⛔ BỊ ĐÁ: có phiên khác đang đăng nhập cùng tài khoản "
                  f"({s[0]}). Hãy thoát playok ở trình duyệt/điện thoại, "
                  "hoặc dùng tài khoản khác cho bot.")
            return
        if code == CODE_PING:
            self.t.send_frame({"i": [CODE_PING]})
            return

        if (code in (73, 88, 89, 90, 91) and len(i) >= 2
                and self.table in (None, 0) and self.await_new_table):
            self.await_new_table = False
            self.table = i[1]
            print("\n" + "=" * 58)
            print(f"   BÀN CỦA BOT:  #{self.table}")
            print(f"   Mở:  https://www.playok.com/en/gomoku/#{self.table}")
            print(f"   Tài khoản: {self.myname or '?'}")
            print("=" * 58 + "\n")
            self.t.send_frame({"i": [83, self.table, 0]})
            self.my_seat = 0
            self.apply_settings()

        if code == 84 and s and self.table:
            who = s[0]
            if who and who != self.myname:
                print(f"[bot] {who} vào phòng bàn (chờ họ ngồi ghế)")

        if s and self.ranks is None:
            r = parse_set_rank(s)
            if r:
                self.ranks = r
                print("[bot] thang elo của game này: "
                      + ", ".join(str(x) for x in ttype_labels(r)))

        if code == CODE_SETTINGS_STATE and len(i) >= 3 and s:
            # [89, tid, v1, v2...] + [tên1, tên2...] - server báo lại thiết lập
            for k, name in enumerate(s):
                if name == "ttype" and 2 + k < len(i):
                    v = i[2 + k]
                    lab = ttype_labels(self.ranks)
                    idx = (8 if v == 2 else v - 2) if v > 1 else v >> 1
                    who = lab[idx] if 0 <= idx < len(lab) else "?"
                    print(f"[bot] ✅ server xác nhận ttype={v} -> bàn dạng "
                          f"{who}{'+' if isinstance(who, int) else ''}")

        if code == 18 and s:
            self.myname = s[0]
            print(f"[bot] đăng nhập: {self.myname}")

        if code == 71 and len(i) >= 3:
            ni, ns = i[1], i[2]
            for k in range((len(i) - 3) // ni):
                g = i[3 + k * ni:3 + (k + 1) * ni]
                self.tables[g[0]] = s[k * ns:(k + 1) * ns]
        elif code == 70 and len(i) >= 5:
            self.tables[i[1]] = s
            if len(s) >= 3 and self.myname and self.myname in (s[1], s[2]):
                if self.table != i[1]:
                    self.table = i[1]
                    self.joined.add(i[1])
                    print(f"[bot] server xếp tôi ở bàn #{i[1]}")
                self.my_seat = 0 if s[1] == self.myname else 1
                if not self.seated:
                    print(f"[bot] ✅ đang ngồi bàn #{i[1]} ghế {self.my_seat} "
                          f"(https://www.playok.com/en/gomoku/#{i[1]})")
                self.seated = True
                if s[1] and s[2] and not self.in_game:
                    if time.time() - getattr(self, "_go_at", 0) > 5:
                        self._go_at = time.time()
                        opp = s[2] if s[1] == self.myname else s[1]
                        print(f"[bot] đủ 2 ghế (đối thủ {opp}) -> bấm bắt đầu")
                        threading.Thread(target=self._delayed_go,
                                         args=(i[1],), daemon=True).start()

        if code == 32 and s and not getattr(self, "room_done", False):
            self._pick_room(s[0])

        if code in (85, 87, 88, 90, 91, 92, 95) and self.table and len(i) > 1 \
                and i[1] == self.table:
            print(f"[raw] [{code}] {i} {s}")
        if code in (CODE_UI, CODE_GAME, CODE_HISTORY, CODE_MOVE, CODE_CHAT):
            self.handle_game(code, i, s)

    def _pick_room(self, text):
        rooms = []
        for line in text.split("\n"):
            m = re.match(r"(#\d+\S*)\s+(.*?)\s*\((\d+)\)", line.strip())
            if m:
                rooms.append((int(m.group(3)), m.group(1), m.group(2)))
        if not rooms:
            return
        rooms.sort(reverse=True)
        self.room_done = True
        print("[bot] phòng: " + ", ".join(f"{nm}({c})={p}" for p, c, nm in rooms))
        want = (self.want_room or "").lower()
        pick = None
        if want:
            for p_, c_, nm_ in rooms:
                if want in nm_.lower() or want in c_.lower():
                    pick = (p_, c_, nm_)
                    break
            if pick is None:
                print(f"[bot] không thấy phòng '{want}' -> dùng phòng đông nhất")
        if pick is None:
            pick = rooms[0]
        n, code_, name = pick
        print(f"[bot] vào phòng {name} ({code_}, {n} người)")
        self.t.send_frame({"i": [20], "s": [f"/join {code_}"]})
        self.tables.clear()
        self.joined.clear()
        if not self.seated:
            self.table = None
        m2 = re.match(r"#(\d+)", code_)
        self.room_base = int(m2.group(1)) if m2 else None

    # ---------------- ván cờ ----------------
    def handle_game(self, code, i, s):
        with self._lock:
            if code != CODE_CHAT and len(i) >= 2 and self.table not in (None, i[1]):
                return

            if code == CODE_GAME:
                # [90, tid, độ_dài_header, turnSeat, ...]
                # Đọc từ gm.js: b[2] là ĐỘ DÀI KHỐI HEADER, b[3] là ghế tới lượt
                # (`0 < b[2] && (a.ia = b[3])`). Không có "mã trạng thái" nào cả.
                if len(i) < 4:
                    return
                turn, ad, mode = parse_header(i)
                if ad is not None:
                    self.ad = ad
                self.mode = mode
                was = self.in_game
                self.turn_seat = turn
                self.in_game = turn >= 0
                if self.in_game and not was:
                    print(f"[bot] ★ VÁN BẮT ĐẦU — tôi ghế {self.my_seat}, "
                          f"ghế đi trước {turn}, đã có {self.n_moves()} nước")
                if was and not self.in_game:
                    print(f"[bot] ★ VÁN KẾT THÚC sau {len(self.moves)} nước")
                    self.moves = []
                print(f"[bot] #90 header={i[2]} turn={turn} "
                      f"(ghế tôi {self.my_seat}, quân {self.my_color_name()}) "
                      f"swap2={self.mode} nước={self.n_moves()}")

            elif code == CODE_HISTORY:
                # ue(a, b.slice(2), c) -> this.Ib = danh sách thô, KHÔNG phải cặp
                self.moves = [v for v in i[2:] if isinstance(v, int)]
                real = [v for v in self.moves if is_move_value(v)]
                print(f"[bot] #91 đồng bộ lịch sử: {len(real)} nước")

            elif code == CODE_MOVE:
                # Vd(): mọi phần tử từ index 2 đều là nước đi
                for v in i[2:]:
                    if not isinstance(v, int):
                        continue
                    self.moves.append(v)
                    if is_move_value(v):
                        n = len([x for x in self.moves if is_move_value(x)])
                        mine = (getattr(self, "_sent_pos", None) == v % AREA)
                        self._sent_pos = None
                        print(f"[bot] #92 nước {n}: {to_label(v)} (v={v}) — "
                              f"{'CỦA TÔI ✅' if mine else 'đối thủ'}")

            elif code == CODE_CHAT:
                if s:
                    print("[chat]", s[0])
        self.maybe_move()

    # ---------------- màu quân ----------------
    def my_color(self):
        """0 = đen (đi trước), 1 = trắng.

        Lấy từ chính client gomoku: khi đặt quân nó vẽ màu `Nb(ia)` với
        `Nb(a) = this.D.ad ? 1-a : a`, tức MÀU = GHẾ, đảo lại nếu cờ ad bật.
        Đây là cách duy nhất đáng tin: luật swap2 cho một người đặt 3 quân
        liên tiếp nên KHÔNG thể suy màu theo kiểu chẵn/lẻ luân phiên.
        """
        if self.ad is None:
            real = [v for v in self.moves if is_move_value(v)]
            return len(real) % 2          # tạm suy khi chưa nhận được cờ ad
        return (1 - self.my_seat) if self.ad else self.my_seat

    def my_color_name(self):
        return "ĐEN" if self.my_color() == 0 else "TRẮNG"

    def n_moves(self):
        return len([v for v in self.moves if is_move_value(v)])

    def our_turn(self):
        return self.in_game and self.turn_seat == self.my_seat

    def maybe_move(self):
        if self.thinking:
            return
        if not self.in_game:
            if self.seated and self.table and \
                    time.time() - getattr(self, "_last_go", 0) > 4:
                self._last_go = time.time()
                self.t.send_frame({"i": [85, self.table]})
            return
        if not self.seated or not self.our_turn():
            return
        if self.mode == MODE_WAIT_PICK:
            return                      # đang chờ đối thủ chọn màu
        self.thinking = True
        threading.Thread(target=self._move, daemon=True).start()

    def _ordered_for_engine(self, as_color=None):
        """[(x, y, ai)] theo đúng thứ tự, ai: 1 = mình, 2 = đối thủ.

        Chủ nhân mỗi nước lấy từ BIT MÀU trong chính giá trị server gửi
        (`Math.floor(d/225)%2` trong Db()), không suy đoán luân phiên.
        """
        me = self.my_color() if as_color is None else as_color
        out = []
        taken = set()
        for v in self.moves:
            if not is_move_value(v):
                continue
            x, y = val_to_xy(v)
            color = (v // AREA) % 2
            out.append((x, y, 1 if color == me else 2))
            taken.add(v % AREA)
        return out, taken

    # ---------------- luật swap2 ----------------
    def _color_counts(self):
        c = [0, 0]
        for v in self.moves:
            if is_move_value(v):
                c[(v // AREA) % 2] += 1
        return c

    def _pick_color(self):
        """Chọn đen hay trắng khi luật swap2 hỏi.

        Bên đi tiếp là bên đang ít quân hơn. Hỏi Rapfi chấm điểm thế cờ cho
        BÊN ĐI TIẾP: điểm dương thì nhận màu đó, âm thì nhận màu kia.
        """
        c0, c1 = self._color_counts()
        nxt = 0 if c0 <= c1 else 1
        ordered, _ = self._ordered_for_engine(as_color=nxt)
        self.engine.best_move_ordered(ordered)
        ev = self.engine.last_eval
        take = nxt if (ev is None or ev >= 0) else 1 - nxt
        token = PICK_BLACK if take == 0 else PICK_WHITE
        print(f"[bot] swap2: trên bàn {c0} đen / {c1} trắng, bên đi tiếp = "
              f"{'đen' if nxt == 0 else 'trắng'}, điểm {ev} -> nhận quân "
              f"{'ĐEN' if take == 0 else 'TRẮNG'} (thẻ {token})")
        self.t.send_frame({"i": [CODE_MOVE, self.table, 0, token, 0]})

    def _move(self):
        try:
            if self.mode in (MODE_MUST_PICK, MODE_MAY_PICK):
                # Ab == 3 thì client CHẶN đặt quân; gửi nước cờ lúc này sẽ bị
                # server bỏ qua và bot đứng hình cho tới khi hết giờ.
                self._pick_color()
                return
            ordered, taken = self._ordered_for_engine()
            print(f"[bot] tới lượt tôi (ghế {self.my_seat}), {len(ordered)} nước trên bàn")
            t0 = time.time()
            mv = self.engine.best_move_ordered(ordered)
            if not mv:
                print("[bot] engine không trả nước")
                return
            x, y = mv
            pos = xy_to_pos(x, y)
            if pos in taken:
                print(f"[bot] ⚠️ engine trả ô đã có quân ({x},{y}) -> bỏ qua")
                return
            used = int((time.time() - t0) * 100)
            n_before = len([v for v in self.moves if is_move_value(v)])
            self._sent_pos = pos
            # ĐÚNG khung của gm.js: số 0 ở giữa (cờ tướng mới là số 1)
            self.t.send_frame({"i": [CODE_MOVE, self.table, 0, pos, used]})
            print(f"[bot] gửi {to_label(pos)} ({x},{y}) pos={pos}")
            for _ in range(30):
                time.sleep(0.1)
                if len([v for v in self.moves if is_move_value(v)]) > n_before:
                    return
            if self.our_turn():
                print("[bot] ↻ chưa thấy server nhận, gửi lại 1 lần")
                self.t.send_frame({"i": [CODE_MOVE, self.table, 0, pos, used]})
        except Exception as e:
            print("[bot] lỗi khi đi:", e)
        finally:
            self.thinking = False

    # ---------------- bàn ----------------
    def apply_settings(self):
        """Đặt hạn chế elo cho bàn VỪA TỰ TẠO.

        Chỉ người tạo bàn mới đổi được thiết lập (server chat: "you are now the
        table operator - you can change settings"), nên chỉ gọi sau khi bot
        bấm [71] tạo bàn, không gọi khi ngồi nhờ bàn người khác.
        """
        if not self.want_ttype or not self.table:
            return
        try:
            code = ttype_code(self.want_ttype, self.ranks)
        except ValueError as e:
            print(f"[bot] bỏ qua thiết lập ttype: {e}")
            return
        self.t.send_frame({"i": [CODE_SETTING, self.table, code],
                           "s": ["ttype"]})
        print(f"[bot] đặt bàn #{self.table} -> {self.want_ttype} (ttype={code})")

    def _delayed_go(self, tid):
        time.sleep(3)
        for k in range(3):
            if self.in_game:
                return
            self.t.send_frame({"i": [85, tid]})
            print(f"[bot] bấm BẮT ĐẦU lần {k + 1} (bàn #{tid})")
            time.sleep(3)

    def pump(self, seconds):
        end = time.time() + seconds
        while time.time() < end:
            for f in self.t.recv_frames(timeout=0.3):
                self.on_frame(f)
            time.sleep(0.05)

    def try_join_table(self):
        # Đang ngồi hoặc đang đánh thì TUYỆT ĐỐI không đi ngồi bàn khác.
        # Thiếu chốt này, sau khi vào lại kênh bot vừa đánh ở bàn cũ vừa gửi
        # [72]/[83] sang bàn mới -> self.my_seat và self.table bị ghi đè giữa
        # ván, ghế nhảy 0 <-> 1 và bot đánh nhầm màu.
        if self.seated or self.in_game:
            return True
        for tid, v in list(self.tables.items()):
            if tid in self.joined or len(v) < 3:
                continue
            settings = (v[0] or "").lower()
            if self.avoid_swap2 and "sw" in settings:
                continue          # luật swap2 cần thủ tục chọn màu riêng
            p0, p1 = v[1], v[2]
            if self.myname in (p0, p1) or bool(p0) == bool(p1):
                continue
            seat = 1 if p0 else 0
            self.joined.add(tid)
            print(f"[bot] vào bàn #{tid} của {p0 or p1} ({v[0]}), ngồi ghế {seat}")
            self.table = tid
            self.t.send_frame({"i": [72, tid]})
            self.pump(1.0)
            self.my_seat = seat
            self.t.send_frame({"i": [83, tid, seat]})
            self.pump(0.6)
            self.t.send_frame({"i": [85, tid]})
            self.pump(2.0)
            cur = self.tables.get(tid, [])
            if self.myname in cur:
                self.my_seat = 0 if cur[1] == self.myname else 1
                print(f"[bot] ✅ đã ngồi bàn #{tid} ghế {self.my_seat}: {cur}")
                self.seated = True
                self.t.send_frame({"i": [85, tid]})
                return True
            print(f"[bot] không ngồi được bàn #{tid} ({cur}) -> tìm bàn khác")
            self.t.send_frame({"i": [73, tid]})
            self.table = None
        return False

    def run(self, seconds=600):
        self.fetch_session()
        if hasattr(self.t, "start_keepalive"):
            self.t.start_keepalive()
        self.login()
        deadline0 = time.time() + 8
        while time.time() < deadline0:
            for f in self.t.recv_frames(timeout=1.0):
                self.on_frame(f)
            if self.seated:
                break
        if self.seated:
            print(f"[bot] server khôi phục sẵn bàn #{self.table} -> dùng luôn")
            self.t.send_frame({"i": [85, self.table]})
        elif not self.try_join_table():
            print("[bot] không có bàn trống -> tự tạo bàn")
            self.await_new_table = True
            self.t.send_frame({"i": [CODE_NEW_TABLE]})

        end = time.time() + seconds
        fails = 0
        while time.time() < end:
            if getattr(self.t, "dead", False):
                fails += 1
                wait = min(30, 5 * fails)
                if time.time() - getattr(self, "kicked_at", 0) < 30:
                    wait = 60      # bị đá thì đừng vào lại ngay, sẽ đá qua đá lại
                    print("[bot] vừa bị đá -> chờ 60s cho phiên kia yên vị")
                print(f"[bot] kênh chết (lần {fails}) -> chờ {wait}s rồi vào lại")
                time.sleep(wait)
                try:
                    self.fetch_session()
                    self.t.reopen()
                    self.login()
                    self.table = None
                    self.seated = False
                    self.in_game = False
                    self.room_done = False
                    time.sleep(2)
                    # Chờ server khôi phục bàn cũ trước khi cho phép săn bàn,
                    # nếu không hai việc chạy song song sẽ giẫm chân nhau.
                    grace = time.time() + 8
                    while time.time() < grace and not self.seated:
                        for f in self.t.recv_frames(timeout=1.0):
                            self.on_frame(f)
                    self._hunt = time.time()
                except Exception as e:
                    print(f"[bot] vào lại thất bại: {e}")
                continue
            if not self.seated and time.time() - getattr(self, "_hunt", 0) > 20:
                self._hunt = time.time()
                if not self.try_join_table():
                    self.await_new_table = True
                    self.t.send_frame({"i": [CODE_NEW_TABLE]})
            frames = self.t.recv_frames()
            if frames:
                fails = 0
            for f in frames:
                self.on_frame(f)
            self.maybe_move()
            time.sleep(0.2)
        print("[bot] hết giờ")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--transport", choices=["auto", "ws", "polling"],
                    default=os.environ.get("PLAYOK_TRANSPORT", "polling"))
    ap.add_argument("--room", default=os.environ.get("PLAYOK_ROOM", ""),
                    help="tên/mã phòng; để trống = phòng đông nhất")
    ap.add_argument("--seconds", type=int,
                    default=int(os.environ.get("PLAYOK_SECONDS", "600")))
    ap.add_argument("--movetime", type=int,
                    default=int(os.environ.get("PLAYOK_MOVETIME", "3000")),
                    help="thời gian engine nghĩ mỗi nước (mili giây)")
    ap.add_argument("--allow-swap2", action="store_true",
                    help="cho phép vào cả bàn luật swap2 (mặc định né)")
    ap.add_argument("--ttype", default=os.environ.get("PLAYOK_TTYPE", ""),
                    help="hạn chế bàn tự tạo: public | private | ngưỡng elo "
                         "(1200/1350/1500/1650/1800/1950/2100) | bậc 1-7")
    args = ap.parse_args()

    engine = Rapfi(size=SIZE, rule=1, turn_ms=args.movetime)
    engine.start()

    transport = None
    if args.transport in ("auto", "ws"):
        try:
            transport = WebSocketTransport(HOST, ["wss:17003", "wss:443"])
        except Exception as e:
            print("[net] WS không dùng được:", e)
            transport = None
    if transport is None:
        transport = PollingTransport(HOST, 443)

    bot = Bot(transport, engine)
    bot.want_room = args.room
    bot.movetime = args.movetime
    bot.want_ttype = args.ttype or None
    bot.avoid_swap2 = not args.allow_swap2
    try:
        bot.run(args.seconds)
    except KeyboardInterrupt:
        print("\n[bot] dừng")
    finally:
        transport.close()
        engine.stop()


if __name__ == "__main__":
    main()
