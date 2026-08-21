#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
PlayOK Reversi/Othello BOT — dùng engine Edax.

Dùng lại nguyên tầng mạng + logic sảnh/bàn của bot gomoku; chỉ khác phần luật.
Đọc từ https://www.playok.com/j/rv.js:

    f.ee = function(a, b) {                 // gửi nước
        a = [92, this.K, a];
        if (typeof b != "undefined") a.push(b);
        a.push(Math.floor((Date.now() - this.nb.A) / 100));
        this.send(a, null); };
    ... this.fa.ee(2, a + 8*b)              // bấm chuột lên bàn

  -> khung gửi [92, tid, 2, pos, thời_gian], pos = cột + 8*hàng.
     Số giữa: cờ tướng 1, gomoku 0, REVERSI 2.

    d = v % 8; e = Math.floor(v/8)%8; g = Math.floor(v/64)%2;   // cột, hàng, MÀU
    v == -1  -> BỎ LƯỢT

Bỏ lượt do SERVER tự chèn: `ee(2, ...)` chỉ được gọi từ ô hợp lệ, không có nút
"pass" nào trong client. Bot chỉ cần bỏ qua giá trị -1 khi dựng lại bàn cờ.

Thẻ 5 trong header gói 90 mang DANH SÁCH Ô HỢP LỆ (giống cờ tướng):
    f.te = function(a,b){ if (5==a[b]) { var c=a[b+1];
                          Ff(this.C, a.slice(b+2, b+2+c)); return b+2+c; } ... }
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
from edax import Edax

# ----------------------------------------------------------------------------
# 1) CẤU HÌNH
# ----------------------------------------------------------------------------
COOKIES = {
    "kt": os.environ.get("PLAYOK_KT", "cckn"),
    "kguest": "0",
    "ku": os.environ.get("PLAYOK_KU", "dcb10af4"),
    "ksession": os.environ.get(
        "PLAYOK_KSESSION", "bf1fc4171ce46cc0:nguyen066:e184"),
    "kbeta": "rv",          # rv = reversi (gm gomoku, xq cờ tướng)
    "kbexp": "0",
}

GAME_URL = WWW + "/en/reversi/"
CODE_SUBSCRIBE = 1713    # window.k2start: new Xa({Rf:1713}) trong rv.js
CODE_PING = 2
CODE_CHAT = 81
CODE_UI = 88             # cờ nút giao diện, KHÔNG phải lượt đi
CODE_GAME = 90           # [90, tid, độ_dài_header, turnSeat, ...]
CODE_HISTORY = 91        # [91, tid, ...toàn bộ nước đã đi]
CODE_MOVE = 92
CODE_NEW_TABLE = 71

SIZE = 8
AREA = SIZE * SIZE        # 64
MOVE_TAG = 2              # số giữa gói 92 dành riêng cho reversi
EMPTY, BLACK, WHITE = -1, 0, 1


def is_move_value(v):
    """-1 = BỎ LƯỢT. Nước thật = pos + 64*màu, nên nằm trong [0, 128)."""
    return isinstance(v, int) and 0 <= v < 2 * AREA


def val_to_xy(v):
    v %= AREA
    return v % SIZE, v // SIZE


def val_color(v):
    return (v // AREA) % 2


def xy_to_pos(x, y):
    return x + SIZE * y


def to_label(v):
    """Ef() trong rv.js: chữ HOA = đen (màu 0), chữ thường = trắng (màu 1)."""
    if v < 0:
        return "--"
    x, y = val_to_xy(v)
    base = ord("A") if val_color(v) == 0 else ord("a")
    return f"{chr(base + x)}{y + 1}"


DIRS = [(-1, -1), (0, -1), (1, -1), (-1, 0), (1, 0), (-1, 1), (0, 1), (1, 1)]


class Othello:
    """Bàn 8x8. Thế khởi đầu lấy đúng từ reset() của rv.js:
    C[3][3]=1, C[3][4]=0, C[4][3]=0, C[4][4]=1 (hàng, cột)."""

    def __init__(self):
        self.reset()

    def reset(self):
        self.g = [EMPTY] * AREA
        self.g[xy_to_pos(3, 3)] = WHITE
        self.g[xy_to_pos(4, 3)] = BLACK
        self.g[xy_to_pos(3, 4)] = BLACK
        self.g[xy_to_pos(4, 4)] = WHITE

    def flips(self, pos, color):
        """Các quân bị lật nếu đặt `color` vào `pos` (rỗng = nước không hợp lệ)."""
        if self.g[pos] != EMPTY:
            return []
        x0, y0 = val_to_xy(pos)
        out = []
        for dx, dy in DIRS:
            line, x, y = [], x0 + dx, y0 + dy
            while 0 <= x < SIZE and 0 <= y < SIZE:
                v = self.g[xy_to_pos(x, y)]
                if v == EMPTY:
                    break
                if v == color:
                    out.extend(line)
                    break
                line.append(xy_to_pos(x, y))
                x += dx
                y += dy
        return out

    def apply(self, pos, color):
        f = self.flips(pos, color)
        if not f:
            return False
        self.g[pos] = color
        for p in f:
            self.g[p] = color
        return True

    def legal(self, color):
        return [p for p in range(AREA) if self.flips(p, color)]

    def to_edax(self):
        """64 ký tự cho lệnh setboard của Edax: X đen, O trắng, - trống."""
        return "".join("-XO"[self.g[p] + 1] for p in range(AREA))

    def counts(self):
        return self.g.count(BLACK), self.g.count(WHITE)


def parse_header(i):
    """Đọc khối header gói 90 -> (turnSeat, ad, mode).

    Cấu trúc thật (hàm Fe() + Ae() trong gm.js), KHÔNG phải "mã trạng thái":
        i[2] = ĐỘ DÀI khối header
        i[3] = ghế tới lượt   (`0 < b[2] && (a.ia = b[3])`)
        i[5..3+i[2]) = chuỗi thẻ TLV:
            thẻ 1 -> 3 số (đồng hồ)     thẻ 2 -> 4 số (đồng hồ có gia giờ)
            thẻ 3 -> 2 số: cờ; bit 2 = ad (hoán ghế <-> màu quân)
            thẻ 5 -> số lượng + DANH SÁCH Ô HỢP LỆ (giống cờ tướng)
    """
    if len(i) < 4 or i[2] <= 0:
        return -1, None, []
    end = min(3 + i[2], len(i))
    turn, ad, legal = i[3], None, []
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
            # rv.js: c = a[b+1] rồi Ff(C, a.slice(b+2, b+2+c)) -> danh sách ô hợp lệ
            if c + 2 > end:
                break
            n = i[c + 1]
            legal = [v for v in i[c + 2:c + 2 + n] if isinstance(v, int)]
            c += 2 + n
        else:
            break
    return turn, ad, legal


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
        self.board = Othello()
        self.level = 18          # độ sâu Edax
        self.in_game = False
        self.thinking = False
        self.movetime = 3000
        self.ap = self.ge = None
        self.myname = None
        self.await_new_table = False
        self.ranks = None        # thang elo server gửi qua set_rank
        self.join_others = False # mặc định KHÔNG ngồi nhờ bàn người khác
        self.my_elo = None
        self.want_ttype = None   # hạn chế elo muốn đặt cho bàn tự tạo
        self.create_table = create_table
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
            print(f"   Mở:  https://www.playok.com/en/reversi/#{self.table}")
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

        if code == 25 and len(i) >= 4 and s and s[0] == self.myname:
            if self.my_elo != i[3]:
                self.my_elo = i[3]
                print(f"[bot] elo của tôi: {self.my_elo}")

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
                          f"(https://www.playok.com/en/reversi/#{i[1]})")
                self.seated = True
                if s[1] and s[2] and not self.in_game:
                    if time.time() - getattr(self, "_go_at", 0) > 5:
                        self._go_at = time.time()
                        opp = s[2] if s[1] == self.myname else s[1]
                        print(f"[bot] đủ 2 ghế (đối thủ {opp}) -> bấm bắt đầu")
                        threading.Thread(target=self._delayed_go,
                                         args=(i[1],), daemon=True).start()

        if code == 72 and len(i) >= 2 and i[1] == self.table:
            print(f"[bot] bàn #{i[1]} đã đóng -> sẽ tạo bàn mới")
            self.tables.pop(i[1], None)
            self.table = None
            self.seated = False
            self.in_game = False

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
                turn, ad, legal = parse_header(i)
                if ad is not None:
                    self.ad = ad
                self.legal = legal
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
                      f"ô_hợp_lệ={len(self.legal)} nước={self.n_moves()} "
                      f"({self.board.counts()[0]}-{self.board.counts()[1]})")

            elif code == CODE_HISTORY:
                self.moves = [v for v in i[2:] if isinstance(v, int)]
                self.rebuild()
                print(f"[bot] #91 đồng bộ lịch sử: {self.n_moves()} nước, "
                      f"tỉ số {self.board.counts()[0]}-{self.board.counts()[1]}")

            elif code == CODE_MOVE:
                # rv.js: f.Od = a => a[2]  (chỉ MỘT giá trị, khác gomoku)
                if len(i) >= 3 and isinstance(i[2], int):
                    v = i[2]
                    self.moves.append(v)
                    if v < 0:
                        print("[bot] ⤼ một bên BỎ LƯỢT (server tự chèn -1)")
                    else:
                        if not self.board.apply(v % AREA, val_color(v)):
                            print(f"[bot] ⚠️ nước {to_label(v)} không lật được "
                                  f"quân nào -> bàn cờ lệch, dựng lại từ đầu")
                            self.rebuild()
                        mine = (getattr(self, "_sent_pos", None) == v % AREA)
                        self._sent_pos = None
                        b, w = self.board.counts()
                        print(f"[bot] #92 nước {self.n_moves()}: {to_label(v)} "
                              f"(v={v}) — {'CỦA TÔI ✅' if mine else 'đối thủ'}"
                              f"  [{b}-{w}]")

            elif code == CODE_CHAT:
                if s:
                    print("[chat]", s[0])
        self.maybe_move()

    # ---------------- màu quân ----------------
    def my_color(self):
        """0 = đen (đi trước), 1 = trắng.

        Lấy từ chính client gomoku: khi đặt quân nó vẽ màu `Nb(ia)` với
        `Nb(a) = this.D.ad ? 1-a : a`, tức MÀU = GHẾ, đảo lại nếu cờ ad bật.
        Không suy theo chẵn/lẻ được: Othello có BỎ LƯỢT nên hai bên không
        nhất thiết đi luân phiên.
        """
        if self.ad is None:
            return self.my_seat
        return (1 - self.my_seat) if self.ad else self.my_seat

    def my_color_name(self):
        return "ĐEN" if self.my_color() == BLACK else "TRẮNG"

    def n_moves(self):
        return len([v for v in self.moves if is_move_value(v)])

    def our_turn(self):
        return self.in_game and self.turn_seat == self.my_seat

    def maybe_move(self):
        if self.thinking:
            return
        if not self.in_game:
            # Chỉ nhắc BẮT ĐẦU khi bàn đã đủ hai người. Bot tự tạo bàn rồi ngồi
            # chờ có thể một mình hàng chục phút; bắn [85] 4 giây một lần suốt
            # thời gian đó là vô nghĩa và dễ bị server chặn.
            info = self.tables.get(self.table) or []
            ready = len(info) >= 3 and bool(info[1]) and bool(info[2])
            if (ready and self.seated and self.table
                    and time.time() - getattr(self, "_last_go", 0) > 4):
                self._last_go = time.time()
                self.t.send_frame({"i": [85, self.table]})
            return
        if not self.seated or not self.our_turn():
            return
        self.thinking = True
        threading.Thread(target=self._move, daemon=True).start()

    def rebuild(self):
        """Dựng lại bàn cờ từ đầu theo lịch sử. -1 = bỏ lượt, bỏ qua."""
        self.board.reset()
        for v in self.moves:
            if is_move_value(v):
                self.board.apply(v % AREA, val_color(v))

    def _move(self):
        try:
            me = self.my_color()
            mine = self.board.legal(me)
            if self.legal and set(self.legal) != set(mine):
                print(f"[bot] ⚠️ ô hợp lệ của tôi {sorted(mine)} khác server "
                      f"{sorted(self.legal)} -> dựng lại bàn cờ")
                self.rebuild()
                mine = self.board.legal(me)
            if not mine:
                print("[bot] không còn nước -> chờ server cho bỏ lượt")
                return
            side = "X" if me == BLACK else "O"
            board = self.board.to_edax()
            pos = self.engine.best_move(board, side)
            if pos is None or pos not in mine:
                pos = (self.legal or mine)[0]
                print(f"[bot] engine không ra nước hợp lệ -> chọn {to_label(pos)}")
            used = int(time.time() * 100) % 10000
            n_before = self.n_moves()
            self._sent_pos = pos
            # ĐÚNG khung của rv.js: số 2 ở giữa (gomoku 0, cờ tướng 1)
            self.t.send_frame({"i": [CODE_MOVE, self.table, MOVE_TAG, pos, 0]})
            x, y = val_to_xy(pos)
            print(f"[bot] gửi {chr(65+x)}{y+1} pos={pos}")
            for _ in range(40):
                time.sleep(0.1)
                if self.n_moves() > n_before:
                    return
            if self.our_turn():
                print("[bot] ↻ chưa thấy server nhận, gửi lại 1 lần")
                self.t.send_frame({"i": [CODE_MOVE, self.table, MOVE_TAG, pos, 0]})
        except Exception as e:
            print("[bot] lỗi khi đi:", e)
        finally:
            self.thinking = False

    # ---------------- bàn ----------------
    def new_table(self):
        """Tự tạo bàn của mình. Chỉ chủ bàn mới đặt được hạn chế elo."""
        print("[bot] tạo bàn mới của mình"
              + (f" (sẽ đặt {self.want_ttype})" if self.want_ttype else ""))
        self.table = None
        self.seated = False
        self.await_new_table = True
        self.t.send_frame({"i": [CODE_NEW_TABLE]})

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
        labels = ttype_labels(self.ranks)
        idx = (8 if code == 2 else code - 2) if code > 1 else code >> 1
        need = labels[idx] if 0 <= idx < len(labels) else None
        if isinstance(need, int) and self.my_elo and self.my_elo < need:
            print(f"[bot] ⚠️ elo của bot là {self.my_elo}, THẤP HƠN ngưỡng "
                  f"{need} vừa đặt — chính bot có thể không ngồi được bàn "
                  f"của mình. Nếu 15s nữa vẫn chưa ngồi được thì hạ ttype.")
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
        # Ngồi nhờ bàn người khác thì bot KHÔNG phải chủ bàn, không đặt được
        # hạn chế elo (ttype) -> mặc định tắt, chỉ tự tạo bàn của mình.
        if not self.join_others:
            return False
        # Đang ngồi hoặc đang đánh thì TUYỆT ĐỐI không đi ngồi bàn khác.
        # Thiếu chốt này, sau khi vào lại kênh bot vừa đánh ở bàn cũ vừa gửi
        # [72]/[83] sang bàn mới -> self.my_seat và self.table bị ghi đè giữa
        # ván, ghế nhảy 0 <-> 1 và bot đánh nhầm màu.
        if self.seated or self.in_game:
            return True
        for tid, v in list(self.tables.items()):
            if tid in self.joined or len(v) < 3:
                continue
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
            self.new_table()

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
                    self.new_table()
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
    ap.add_argument("--level", type=int,
                    default=int(os.environ.get("EDAX_LEVEL", "18")),
                    help="độ sâu Edax (18 rất mạnh, 21+ chậm hơn nhiều)")
    ap.add_argument("--join-others", action="store_true",
                    help="cho phép ngồi nhờ bàn người khác (mặc định TẮT: chỉ "
                         "tự tạo bàn để áp được hạn chế elo)")
    ap.add_argument("--ttype", default=os.environ.get("PLAYOK_TTYPE", ""),
                    help="hạn chế bàn tự tạo: public | private | ngưỡng elo "
                         "(1200/1350/1500/1650/1800/1950/2100) | bậc 1-7")
    args = ap.parse_args()

    engine = Edax(level=args.level)
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
    bot.join_others = args.join_others
    try:
        bot.run(args.seconds)
    except KeyboardInterrupt:
        print("\n[bot] dừng")
    finally:
        transport.close()
        engine.stop()


if __name__ == "__main__":
    main()
