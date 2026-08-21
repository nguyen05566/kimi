#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
PlayOK Xiangqi BOT — một file duy nhất.

Kết nối PlayOK bằng cookie đăng nhập có sẵn, tham gia bàn, dùng engine Pikafish
(UCI) để tự động đánh cờ tướng.

CHỈ DÙNG CHO MỤC ĐÍCH HỌC TẬP / NGHIÊN CỨU. Tôn trọng ToS của PlayOK.

Yêu cầu:
    pip install websocket-client          # chỉ cần nếu dùng transport websocket
    # engine: tải Pikafish (xem download_engine.sh) hoặc trỏ --engine

Cách chạy:
    python3 bot.py --engine ./pikafish-avx2 --nnue ./pikafish.nnue \
        --color red --create --seconds 180
"""
import argparse
import json
import queue
import re
import ssl
import subprocess
import threading
import time
import urllib.request

import requests

# ----------------------------------------------------------------------------
# 1) CẤU HÌNH COOKIE (đăng nhập)
# ----------------------------------------------------------------------------
COOKIES = {
    "kt": "cckn",
    "kguest": "0",
    "ku": "dcb10af4",
    "ksession": "bf1fc4171ce46cc0:nguyen066:e184",
    "kbeta": "xq",
    "kbexp": "0",
}

HOST = "x.playok.com"
WWW = "https://www.playok.com"
GAME_URL = WWW + "/en/xiangqi/"
UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")

# Mã giao thức PlayOK (reverse-engineered từ /j/xq.js)
CODE_SUBSCRIBE = 1728    # đăng ký game (Sf)
CODE_PING = 2            # trả lời ping
CODE_CHAT = 81
# 88 = TRẠNG THÁI NÚT GIAO DIỆN (bật/tắt nút xin hoà, xin hoãn...).
# KHÔNG phải lượt đi! Bản cũ lấy lượt từ đây nên bot gửi nước lúc bàn còn
# đang chờ (state=4, turn=-1) và server im lặng bỏ hết.
CODE_UI = 88
CODE_GAME = 90           # [90, table, state, turnSeat, ..., clk0, clk1]
CODE_HISTORY = 91        # [91, table, ...packed] danh sách nước đã đi
CODE_MOVE = 92           # gửi [92, table, 1, packed, ts] / nhận [92, table, (âm...), packed]
CODE_REQUEST = 93        # [93, table, action] (1 gửi, 2 ok, 3 từ chối, 4 đầu hàng)
CODE_NEW_TABLE = 71      # tạo bàn mới

# Trạng thái ván trong gói 90 (kiểm chứng bằng ván thật)
ST_WAIT = 4              # bàn đang chờ người / chờ bấm bắt đầu
ST_SETUP = 7             # đang ghép cặp, chưa được đi
# GIẢI MÃ i[2] (kiểm chứng bằng ván thật, cả gomoku lẫn cờ tướng):
#     4          = bàn đang chờ, chưa vào ván
#     7          = ván ĐANG CHẠY nhưng lượt của ĐỐI THỦ
#     9 + n      = ván ĐANG CHẠY, lượt CỦA MÌNH, n = số nước hợp lệ
#                  (44 nước mở cuộc của đỏ -> 53; 43 -> 52; 32 -> 41 ...)
# Vì vậy KHÔNG được so state == 9. Ván đang chạy <=> turnSeat >= 0 và state != 4.
# Bản trước coi state 7 là "chưa chơi" nên cứ đến lượt đối thủ là tưởng
# ván kết thúc, rồi đến lượt mình lại tưởng ván mới -> xoá sạch bàn cờ,
# engine luôn nhìn thấy thế khai cuộc và đi lại đúng một nước.
ST_NOT_PLAYING = (ST_WAIT,)


def is_playing(state, turn):
    return turn >= 0 and state not in ST_NOT_PLAYING


def parse_legal(i):
    """Gói 90 lúc đang đánh kèm DANH SÁCH NƯỚC HỢP LỆ của bên tới lượt:
    [..., 5, n, <n nước packed>, ...]. Ván mở cuộc cho đúng 44 nước đỏ —
    khớp luật cờ tướng, và cũng xác nhận lại cách đóng gói nước đi.
    """
    if len(i) > 12 and i[10] == 5 and isinstance(i[11], int) and i[11] > 0:
        n = i[11]
        if len(i) >= 12 + n:
            return [v for v in i[12:12 + n] if isinstance(v, int) and v > 0]
    return []


# ----------------------------------------------------------------------------
# THIẾT LẬP BÀN — gói [82, tid, giá_trị] + [tên]
# (hàm `function V(a,b,c){a.send([82,a.K,c],[b])}` trong gm.js/xq.js)
# Server báo lại toàn bộ thiết lập bằng gói 89: [89, tid, v1, v2...] + [tên1...]
# CHỈ NGƯỜI TẠO BÀN (table operator) mới đổi được.
#
# Tên thiết lập đã đọc được từ client:
#   ttype  loại bàn / hạn chế elo      tg   thời gian ván
#   tm     thời gian cộng thêm          ud   cấm đi lại (no undo)
#   gtype  1 = tính elo, 0 = không      pro  luật swap2 (chỉ gomoku)
# ----------------------------------------------------------------------------
CODE_SETTING = 82
CODE_SETTINGS_STATE = 89


# Thang mặc định của gomoku (dùng khi chưa nhận được set_rank)
DEFAULT_TTYPE_LABELS = ["public", 1200, 1350, 1500, 1650, 1800, 1950, 2100,
                        "private"]


def ttype_labels(ranks=None):
    """Nhãn [public, m1..m7, private] dựng theo chuỗi `set_rank` của server.

    Client dựng: h = 0..6 -> Ra(1 + h//2) nếu h chẵn, h lẻ thì lấy trung bình
    hai mốc liền kề; Ra(n) = 1 + rank[n]. Chưa có set_rank thì dùng thang
    mặc định của gomoku.
    """
    if not ranks or len(ranks) < 5:
        return list(DEFAULT_TTYPE_LABELS)
    def ra(n):
        return 1 + ranks[n] if n < len(ranks) else None
    out = ["public"] + [None] * 7 + ["private"]
    for h in range(7):
        if h % 2 == 0:
            out[h + 1] = ra(1 + (h >> 1))
        else:
            a, b = ra(1 + (h >> 1)), ra((h >> 1) + 2)
            out[h + 1] = (a + b) // 2 if a and b else None
    return out


def ttype_code(choice, ranks=None):
    """Đổi lựa chọn của người dùng thành mã `ttype` gửi cho server.

    Công thức lấy nguyên từ trình đơn thả xuống trong client:
        V(a, "ttype", (a.j.$ != null && 0 < l) ? (8 <= l ? 2 : l + 2) : 2 * l)
    với `l` là thứ tự mục chọn: 0 = public, 1..7 = các mức elo, 8 = private.
    Suy ra: public = 0, private = 2, bảy mức elo = 3..9.

    Nhận: "public" | "private" | ngưỡng elo ("1350", "1350+") | bậc 1..7.
    """
    s = str(choice).strip().lower().rstrip("+")
    if s in ("", "public"):
        return 0
    if s == "private":
        return 2
    if not s.lstrip("-").isdigit():
        raise ValueError(f"không hiểu ttype {choice!r}")
    n = int(s)
    labels = ttype_labels(ranks)
    for idx in range(1, 8):                    # khớp đúng ngưỡng elo
        if isinstance(labels[idx], int) and labels[idx] == n:
            return idx + 2
    if 1 <= n <= 7:                            # số nhỏ = bậc 1..7
        return n + 2
    raise ValueError(f"ttype {choice!r} không có trong thang "
                     f"{[x for x in labels[1:8]]}")


def parse_set_rank(strings):
    """Bắt chuỗi `set_rank` trong gói text để biết ngưỡng elo THẬT của game này.

    Client: this.$ = value.split(" ").filter((c,d) => d%2 == 0).map(parseInt)
    """
    for k in range(len(strings) - 1):
        if strings[k] == "set_rank":
            try:
                parts = strings[k + 1].split(" ")
                return [int(v) for j, v in enumerate(parts) if j % 2 == 0]
            except Exception:
                return None
    return None


class PollingTransport:
    """Transport qua HTTP long-polling (fallback khi WS bị chặn).

    - POST /r/0  (data "1")   -> nhận id kênh
    - POST /r/{id} (long-poll) -> nhận frame (ngăn cách \n)
    - POST /w/{id} (data=frame) -> gửi frame

    QUAN TRỌNG (bản cũ sai chỗ này -> kênh chết sau ~30 giây):
    long-poll PHẢI chạy trong LUỒNG RIÊNG và luôn để chạy hết.
    Bản cũ gọi /r/ ngay trong vòng lặp chính với timeout 2 giây rồi CẮT
    kết nối giữa chừng, lặp đi lặp lại -> server huỷ kênh (/r/ trả 404,
    mọi /w/ sau đó trả 502). Nay: 1 luồng đọc + hàng đợi.
    """

    def __init__(self, host, port=443):
        # Ghi cả ":443" làm header Host thành "x.playok.com:443" -> nginx trả 502/404
        self.base = f"https://{host}" if port == 443 else f"https://{host}:{port}"
        self.closed = False
        self.dead = False
        self.last_send = 0.0
        self.last_frame = None
        self.q = queue.Queue()
        self.http = requests.Session()
        self.http.headers.update({
            "User-Agent": UA,
            "Origin": WWW,
            "Referer": WWW + "/",
        })
        self.j = None
        self._reader = None
        self._reader_started = False
        self._open_channel()
        # KHÔNG mở long-poll ngay: frame ĐẦU TIÊN bắt buộc phải là handshake.
        # Gọi /r/{id} trước khi gửi handshake -> server trả 404 và huỷ kênh.

    # ---------- kênh ----------
    def _open_channel(self):
        r = self.http.post(self.base + "/r/0", data="1", timeout=20)
        self.j = (r.text or "").strip() or ("X" + str(int(time.time() * 1000)))
        self.dead = False
        print(f"[net] polling session = {self.j}")

    def _start_reader(self):
        self._gen = getattr(self, "_gen", 0) + 1
        gen = self._gen

        def loop():
            while not self.closed and gen == self._gen:
                try:
                    r = self.http.post(self.base + "/r/" + self.j,
                                       data=None, timeout=45)
                except requests.exceptions.Timeout:
                    continue
                except Exception as e:
                    print(f"[net] lỗi đọc: {e}")
                    time.sleep(1)
                    continue
                if r.status_code == 404:
                    print("[net] /r/ 404 -> kênh đã bị server huỷ")
                    self.dead = True
                    return
                if r.status_code >= 500:
                    print(f"[net] /r/ {r.status_code}")
                    self.dead = True
                    return
                for line in (r.text or "").split("\n"):
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        self.q.put(json.loads(line))
                    except Exception:
                        pass

        self._reader = threading.Thread(target=loop, daemon=True)
        self._reader.start()

    def reopen(self):
        """Mở kênh mới khi kênh cũ chết (bị đá phiên, hết hạn...)."""
        self._gen = getattr(self, "_gen", 0) + 1     # bảo luồng cũ dừng
        with self.q.mutex:
            self.q.queue.clear()
        self.http = requests.Session()
        self.http.headers.update({
            "User-Agent": UA, "Origin": WWW, "Referer": WWW + "/"})
        self._reader_started = False
        self._open_channel()
        print(f"[net] mở lại kênh = {self.j}")
        return self.j

    # ---------- gửi ----------
    def send_frame(self, obj):
        # PHẢI nén sát: server trả 502 nếu JSON có khoảng trắng sau ":" / ","
        body = json.dumps(obj, separators=(",", ":"))
        self.last_send = time.time()
        self.last_frame = body
        try:
            r = self.http.post(self.base + "/w/" + self.j, data=body, timeout=20)
            if r.status_code < 400 and not self._reader_started:
                self._reader_started = True     # handshake đã qua -> bật đọc
                self._start_reader()
            if r.status_code >= 400:
                # In HẲN frame bị từ chối ra: 502 ở đây nghĩa là server không
                # nuốt gói này và kênh coi như hỏng.
                print(f"[net] /w/ {r.status_code} khi gửi {body}")
                if r.status_code in (404, 502):
                    self.dead = True
        except Exception as e:
            print(f"[net] lỗi gửi {body}: {e}")

    def start_keepalive(self):
        """Không gửi gì trong ~30s là server huỷ kênh."""
        def loop():
            while not self.closed:
                time.sleep(5)
                if not self.dead and time.time() - self.last_send > 25:
                    self.send_frame({"i": []})
        threading.Thread(target=loop, daemon=True).start()

    # ---------- nhận ----------
    def recv_frames(self, timeout=1.0):
        frames = []
        end = time.time() + timeout
        while True:
            try:
                frames.append(self.q.get(timeout=max(0.01, end - time.time())))
            except queue.Empty:
                break
            except ValueError:
                break
            if time.time() >= end:
                break
        return frames

    def close(self):
        self.closed = True
        self._gen = getattr(self, "_gen", 0) + 1


class WebSocketTransport:
    """Transport qua WebSocket (wss://x.playok.com:17003/ws/)."""

    def __init__(self, host, ports):
        import websocket
        self.ws = None
        for spec in ports:
            scheme, port = spec.split(":")
            url = f"{scheme}://{host}:{port}/ws/"
            try:
                self.ws = websocket.create_connection(
                    url, timeout=15,
                    sslopt={"cert_reqs": ssl.CERT_NONE},
                    header=[f"Origin: {WWW}"])
                print(f"[net] WS connected: {url}")
                return
            except Exception as e:  # noqa
                print(f"[net] WS {url} failed: {e}")
        raise ConnectionError("No WS port reachable")

    def send_frame(self, obj):
        self.ws.send(json.dumps(obj, separators=(",", ":")) + "\n")

    def recv_frames(self, timeout=35):
        frames = []
        try:
            self.ws.settimeout(timeout)
            data = self.ws.recv()
            for line in data.split("\n"):
                line = line.strip()
                if line:
                    try:
                        frames.append(json.loads(line))
                    except Exception:
                        pass
        except Exception:
            pass
        return frames

    def close(self):
        try:
            self.ws.close()
        except Exception:
            pass


# ----------------------------------------------------------------------------
# 2) ENGINE PIKAFISH (UCI)
# ----------------------------------------------------------------------------
class Pikafish:
    def __init__(self, path, nnue=None, threads=2, hash_mb=128):
        # Quyền +x hay bị mất khi workspace lưu/khôi phục -> tự cấp lại
        try:
            import os as _os
            _os.chmod(path, 0o755)
        except Exception:
            pass
        self.p = subprocess.Popen(
            [path], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, text=True, bufsize=1)
        self._lock = threading.Lock()
        self._cmd("uci")
        if nnue:
            self._cmd(f"setoption name EvalFile value {nnue}")
        self._cmd(f"setoption name Threads value {threads}")
        self._cmd(f"setoption name Hash value {hash_mb}")
        self._cmd("isready")
        self._until("readyok")

    def _cmd(self, s):
        self.p.stdin.write(s + "\n")
        self.p.stdin.flush()

    def _until(self, token, timeout=20):
        end = time.time() + timeout
        while time.time() < end:
            line = self.p.stdout.readline()
            if not line:
                break
            if line.startswith(token):
                return

    def best_move(self, fen, movetime_ms=900):
        with self._lock:
            self._cmd(f"position fen {fen}")
            self._cmd(f"go movetime {movetime_ms}")
            end = time.time() + movetime_ms / 1000.0 + 15
            while time.time() < end:
                line = self.p.stdout.readline()
                if not line:
                    break
                if line.startswith("bestmove"):
                    parts = line.split()
                    return parts[1] if len(parts) > 1 else None
        return None

    def close(self):
        try:
            self._cmd("quit")
            self.p.terminate()
        except Exception:
            pass


# ----------------------------------------------------------------------------
# 3) BÀN CỜ + FEN
# ----------------------------------------------------------------------------
BASE2LETTER = {0: "P", 4: "A", 8: "N", 12: "C", 16: "R", 20: "B", 24: "K"}

INITIAL = {}
_back = [17, 9, 21, 5, 25, 5, 21, 9, 17]      # đen (hàng 0)
_front = [16, 8, 20, 4, 24, 4, 20, 8, 16]      # đỏ (hàng 9)
for c, t in enumerate(_back):
    INITIAL[(0, c)] = t
for c, t in enumerate(_front):
    INITIAL[(9, c)] = t
INITIAL[(7, 1)] = INITIAL[(7, 7)] = 12          # pháo đỏ
INITIAL[(2, 1)] = INITIAL[(2, 7)] = 13          # pháo đen
for c in (0, 2, 4, 6, 8):
    INITIAL[(6, c)] = 0                          # tốt đỏ
    INITIAL[(3, c)] = 1                          # tốt đen


class Board:
    def __init__(self):
        self.g = dict(INITIAL)

    def reset(self):
        self.g = dict(INITIAL)

    def apply_move(self, fr_row, fr_col, to_row, to_col):
        p = self.g.pop((fr_row, fr_col), None)
        if p is not None:
            self.g.pop((to_row, to_col), None)
            self.g[(to_row, to_col)] = p

    def to_fen(self, side):
        ranks = []
        for r in range(10):
            empty = 0
            row = ""
            for c in range(9):
                t = self.g.get((r, c))
                if t is None:
                    empty += 1
                else:
                    if empty:
                        row += str(empty)
                        empty = 0
                    color = t & 1
                    letter = BASE2LETTER.get(t - color, "?")
                    row += letter if color == 0 else letter.lower()
            if empty:
                row += str(empty)
            ranks.append(row if row else "0")
        return "/".join(ranks) + f" {side} - - 0 1"


def decode_packed(n):
    """packed = ô_đến*100 + ô_đi, với ô = hàng*10 + cột (hàng 0 = phía ĐEN).

    Xác minh bằng nước đi THẬT bắt được khi xem trộm bàn người khác:
        7477 <- 'C2.5'  (pháo ô 77 -> ô 74)
        7697 <- 'H2+3'  (mã   ô 97 -> ô 76)
        8580 <- 'R9.4'  (xe   ô 80 -> ô 85)
    Bản gốc dùng packing hệ cơ số 9 nên server bỏ qua mọi nước.
    """
    n = abs(n)
    to, frm = divmod(n, 100)
    return frm // 10, frm % 10, to // 10, to % 10


def uci_to_packed(uci):
    fr_col = ord(uci[0]) - 97
    to_col = ord(uci[2]) - 97
    fr_row = 9 - int(uci[1])
    to_row = 9 - int(uci[3])
    return (to_row * 10 + to_col) * 100 + (fr_row * 10 + fr_col)


def packed_to_uci(n):
    fr, fc, tr, tc = decode_packed(n)
    return f"{chr(97 + fc)}{9 - fr}{chr(97 + tc)}{9 - tr}"


def extract_move(i):
    """Lấy số nước đi từ gói 92.

    Chiều GỬI  : [92, tid, 1, packed, thời_gian]
    Chiều NHẬN : [92, tid, packed]            -> nước thường
                 [92, tid, -57, 5646]         -> có ăn quân, số âm chen vào
    Nên: nếu có dạng gửi (i[2]==1 và đủ 5 phần tử) thì lấy i[3],
    còn lại lấy số DƯƠNG cuối cùng.
    """
    body = [v for v in i[2:] if isinstance(v, int)]
    if len(body) >= 3 and body[0] == 1:
        return body[1]
    for v in reversed(body):
        if v > 0:
            return v
    return None


# ----------------------------------------------------------------------------
# 4) BOT
# ----------------------------------------------------------------------------
class Bot:
    def __init__(self, transport, engine, color, create_table=False):
        self.t = transport
        self.engine = engine
        self.color = color
        self.board = Board()
        self.side = "w"
        self.state = 0          # trạng thái ván lấy từ gói 90
        self.turn_seat = -1     # ghế đang tới lượt, -1 = chưa của ai
        self.legal = []         # danh sách nước hợp lệ server gửi kèm gói 90
        self.red_seat = 0       # ghế cầm quân ĐỎ (suy từ lượt đi đầu tiên)
        self.nmoves = 0         # số nước đã đi trong ván hiện tại
        self.last_sent = None   # (packed, thời điểm) để gửi lại nếu server nuốt
        self.table = None   # số bàn thật lấy từ gói server, không hardcode
        self.tables = {}
        self.want_room = "hanoi"
        self.room_base = None
        self.my_seat = 0
        self.seated = False
        self.joined = set()
        self.in_game = False
        self.thinking = False
        self.ap = self.ge = None
        self.myname = None
        self.await_new_table = False
        self.ranks = None        # thang elo server gửi qua set_rank
        self.want_ttype = None   # hạn chế elo muốn đặt cho bàn tự tạo
        self.create_table = create_table
        self.movetime = 1500        # ms mỗi nước, chỉnh bằng --movetime
        self._lock = threading.Lock()

    def fetch_session(self):
        req = urllib.request.Request(GAME_URL)
        req.add_header("User-Agent", UA)
        req.add_header("Cookie", "; ".join(f"{k}={v}" for k, v in COOKIES.items()))
        html = ""
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                html = r.read().decode(errors="replace")
        except Exception as e:
            print(f"[bot] fetch session error: {e}")
        ap = re.search(r"window\.ap\s*=\s*(\d+)", html)
        ge = re.search(r"window\.ge\s*=\s*(\d+)", html)
        self.ap = ap.group(1) if ap else "0"
        self.ge = ge.group(1) if ge else "0"
        print(f"[bot] session ap={self.ap} ge={self.ge}")

    def login(self):
        ksession = COOKIES.get("ksession", "")
        nick_prefix = ksession.split(":")[0] if ksession else "guest"
        nick = f"{nick_prefix}+|{self.ap}|{self.ge}"
        self.t.send_frame({"i": [CODE_SUBSCRIBE],
                           "s": [nick, "en", "b", "", UA,
                                 f"/{int(time.time())}/1", "w", "1440x900 1",
                                 f"ref:{GAME_URL}", "ver:264"]})
        print(f"[bot] login sent as {nick_prefix}")

    def on_frame(self, obj):
        i = obj.get("i", [])
        s = obj.get("s", [])
        if not i:
            return
        code = i[0]
        if code == CODE_PING:
            self.t.send_frame({"i": [CODE_PING]})
            return

        # --- bàn MỚI DO BOT TỰ TẠO: nhận số bàn rồi ngồi ghế 0 ---
        # Các gói 88/89/90/91/73 đều mang số bàn ở i[1].
        # CHỈ tự ngồi khi chính bot vừa bấm [71] tạo bàn. Bản cũ ngồi mù ghế 0
        # với BẤT KỲ bàn nào -> khi [72] vào xem bàn người khác (ghế 0 đã có
        # người) thì frame [83,tid,0] bị server từ chối -> 502 -> chết kênh.
        if (code in (73, 88, 89, 90, 91) and len(i) >= 2
                and self.table in (None, 0) and getattr(self, "await_new_table", False)):
            self.await_new_table = False
            self.table = i[1]
            print("\n" + "=" * 58)
            print(f"   BÀN CỦA BOT:  #{self.table}")
            print(f"   Mở:  https://www.playok.com/en/xiangqi/#{self.table}")
            print(f"   Tài khoản bot: {self.myname or 'nguyen066'}")
            print("   LƯU Ý: đừng đăng nhập playok bằng chính tài khoản này,")
            print("          sẽ đá phiên của bot. Dùng tài khoản khác/khách.")
            print("=" * 58 + "\n")
            self.t.send_frame({"i": [83, self.table, 0]})
            self.my_seat = 0
            self.apply_settings()
            # KHÔNG chat: tài khoản mới bị server chặn chat ("as a new user you
            # cannot use chat yet") và frame chat bị từ chối bằng 502 -> CHẾT KÊNH.
        # 84 = có người vào bàn; nếu là người khác thì bấm bắt đầu [85]
        if code == 84 and s and self.table:
            who = s[0]
            if who and who != getattr(self, "myname", None):
                # Chỉ ghi nhận; chờ gói [70] báo họ ĐÃ NGỒI GHẾ rồi mới bấm.
                print(f"[bot] {who} vào phòng bàn (chờ họ ngồi ghế)")
            else:
                self.myname = who
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

        # Gói 32 = danh sách PHÒNG: "#100... tên (số người)\n#200... ..."
        # PlayOK chia nhiều phòng; bàn #4xx nằm ở phòng #400 thường vắng tanh
        # nên người chơi (và bạn) không thấy bot. Tự chuyển sang phòng ĐÔNG NHẤT.
        # --- theo dõi danh sách bàn để còn đi tìm chỗ chơi ---
        if code == 71 and len(i) >= 3:
            ni, ns = i[1], i[2]
            for k in range((len(i) - 3) // ni):
                g = i[3 + k * ni:3 + (k + 1) * ni]
                self.tables[g[0]] = s[k * ns:(k + 1) * ns]
        elif code == 70 and len(i) >= 5:
            self.tables[i[1]] = s
            # Tên mình nằm ở ghế nào -> ĐÓ là bàn của bot, bất kể bot tự tạo
            # hay server khôi phục ván cũ lúc đăng nhập lại.
            if len(s) >= 3 and self.myname and self.myname in (s[1], s[2]):
                if self.table != i[1]:
                    self.table = i[1]
                    self.joined.add(i[1])
                    print(f"[bot] server xếp tôi ở bàn #{i[1]} -> nhận bàn này")
                self.my_seat = 0 if s[1] == self.myname else 1
                if not self.seated:
                    print(f"[bot] ✅ đang ngồi bàn #{i[1]} ghế {self.my_seat} "
                          f"(https://www.playok.com/en/xiangqi/#{i[1]})")
                self.seated = True
                # Ghế 0 = ĐỎ (đi trước), ghế 1 = ĐEN. Bản gốc luôn tưởng mình
                # cầm đỏ nên khi ngồi ghế 1 thì our_turn() không bao giờ đúng
                # -> bot ngồi im, đối thủ chờ mỏi mắt.
                # Đủ hai ghế mà ván chưa chạy -> hẹn 3 giây rồi bấm bắt đầu.
                # Bấm ngay lúc đối thủ mới vào PHÒNG BÀN (gói 84) là vô ích vì
                # họ chưa ngồi; đợi quá 10 giây thì mất cửa sổ ghép cặp.
                if s[1] and s[2] and not self.in_game:
                    if time.time() - getattr(self, "_go_at", 0) > 5:
                        self._go_at = time.time()
                        opp = s[2] if s[1] == self.myname else s[1]
                        print(f"[bot] đủ 2 ghế (đối thủ {opp}) -> 3s nữa bấm bắt đầu")
                        threading.Thread(target=self._delayed_go,
                                         args=(i[1],), daemon=True).start()
                seat = 0 if s[1] == self.myname else 1
                new_color = "red" if seat == 0 else "black"
                if new_color != self.color:
                    print(f"[bot] tôi ngồi ghế {seat} -> cầm quân "
                          f"{'ĐỎ' if seat == 0 else 'ĐEN'}")
                self.color = new_color
                self.my_seat = seat

        if code == 32 and s and not getattr(self, "room_done", False):
            rooms = []
            for line in s[0].split("\n"):
                m = re.match(r"(#\d+\S*)\s+(.*?)\s*\((\d+)\)", line.strip())
                if m:
                    rooms.append((int(m.group(3)), m.group(1), m.group(2)))
            if rooms:
                rooms.sort(reverse=True)
                print("[bot] phòng: " + ", ".join(f"{nm}({c})={p}" for p, c, nm in rooms))
                self.room_done = True
                want = (getattr(self, "want_room", "") or "").lower()
                pick = None
                if want:
                    for p_, c_, nm_ in rooms:                     # khớp theo TÊN phòng
                        if want in nm_.lower() or want in c_.lower():
                            pick = (p_, c_, nm_)
                            break
                if pick is None:
                    pick = rooms[0]
                    if want:
                        print(f"[bot] không thấy phòng '{want}' -> dùng phòng đông nhất")
                n, code_, name = pick
                print(f"[bot] vào phòng {name} ({code_}, {n} người)")
                self.t.send_frame({"i": [20], "s": [f"/join {code_}"]})
                # Danh sách bàn cũ là của phòng CŨ. Không xoá thì bot đi ngồi
                # bàn ở phòng khác (đang ở hanoi #500 lại ngồi bàn #466 của
                # haiphong #400) - đúng như bạn phát hiện.
                self.tables.clear()
                self.joined.clear()
                if not self.seated:      # đang ngồi bàn thì giữ nguyên bàn
                    self.table = None
                    self.seated = False
                m2 = re.match(r"#(\d+)", code_)
                self.room_base = int(m2.group(1)) if m2 else None
                print(f"[bot] chỉ nhận bàn trong dải "
                      f"#{self.room_base}-#{self.room_base + 99}")
        if code in (85, 87, 88, 90, 91, 92, 95) and self.table and len(i) > 1 and i[1] == self.table:
            print(f"[raw] [{code}] {i} {s}")
        if code in (CODE_UI, CODE_GAME, CODE_HISTORY, CODE_MOVE, CODE_CHAT):
            self.handle_game(code, i, s)

    def handle_game(self, code, i, s):
        with self._lock:
            # Chỉ nghe gói của ĐÚNG bàn mình đang ngồi
            if code != CODE_CHAT and len(i) >= 2 and self.table not in (None, i[1]):
                return

            if code == CODE_GAME:
                # [90, tid, state, turnSeat, ...., clk0, clk1]
                # ĐÂY mới là nguồn sự thật về lượt đi. Gói 88 chỉ là cờ nút.
                if len(i) < 4:
                    return
                state, turn = i[2], i[3]
                was = self.in_game
                self.state = state
                self.turn_seat = turn
                self.in_game = is_playing(state, turn)
                self.legal = parse_legal(i)
                if self.in_game and not was:
                    # ván vừa bắt đầu -> dựng lại bàn cờ sạch.
                    # NHƯNG nếu vừa nhận gói 91 (khôi phục nước của ván đang
                    # dở) thì giữ nguyên, đừng xoá mất thế cờ.
                    if time.time() - getattr(self, "_hist_at", 0) > 5:
                        self.board.reset()
                        self.nmoves = 0
                    self.last_sent = None
                    if turn >= 0:
                        # bên đi trước là ĐỎ; nếu vào giữa ván thì suy theo
                        # số nước đã đi (chẵn = đến lượt đỏ).
                        self.red_seat = turn if self.nmoves % 2 == 0 else 1 - turn
                    self.color = "red" if self.my_seat == self.red_seat else "black"
                    print(f"[bot] ★ VÁN BẮT ĐẦU — tôi ghế {self.my_seat} "
                          f"cầm {'ĐỎ' if self.color == 'red' else 'ĐEN'}, "
                          f"ghế đi trước = {turn}")
                if was and not self.in_game:
                    print(f"[bot] ★ VÁN KẾT THÚC (state={state}) sau {self.nmoves} nước")
                    self.board.reset()
                    self.nmoves = 0
                    self.last_sent = None
                # Bên đi = chẵn/lẻ số nước, đây là chân lý của cờ tướng.
                self.side = "w" if self.nmoves % 2 == 0 else "b"
                print(f"[bot] #90 state={state} turn={turn} "
                      f"(ghế tôi {self.my_seat}) nước_hợp_lệ={len(self.legal)} "
                      f"clk={i[-2:]}")

            elif code == CODE_HISTORY:
                # [91, tid, packed, thời_gian, packed, thời_gian, ...]
                # số ÂM chen vào trước một nước = nước đó có ăn quân.
                # Bản cũ lấy "mọi số > 0" nên khi ván có đồng hồ khác 0 sẽ
                # nuốt luôn cả trường thời gian làm nước đi -> bàn cờ loạn.
                self.board.reset()
                self.nmoves = 0
                want_move = True
                for v in i[2:]:
                    if not isinstance(v, int) or v < 0:
                        continue
                    if want_move:
                        if v:
                            self.board.apply_move(*decode_packed(v))
                            self.nmoves += 1
                    want_move = not want_move
                self._hist_at = time.time()
                self.side = "w" if self.nmoves % 2 == 0 else "b"
                if self.nmoves:
                    print(f"[bot] #91 khôi phục {self.nmoves} nước")

            elif code == CODE_MOVE:
                mv = extract_move(i)
                if mv:
                    self.board.apply_move(*decode_packed(mv))
                    self.nmoves += 1
                    mine = (self.last_sent and self.last_sent[0] == mv)
                    self.last_sent = None
                    self.side = "w" if self.nmoves % 2 == 0 else "b"
                    print(f"[bot] #92 nước {self.nmoves}: {packed_to_uci(mv)} "
                          f"(packed {mv}) — {'CỦA TÔI ✅' if mine else 'đối thủ'}")

            elif code == CODE_UI:
                # chỉ ghi log, TUYỆT ĐỐI không suy ra lượt đi từ đây
                pass

            elif code == CODE_CHAT:
                if s:
                    print("[chat]", s[0])
        self.maybe_move()

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
        """Trễ 3 giây rồi bấm bắt đầu, nhắc thêm 2 lần trong cửa sổ ~10 giây."""
        time.sleep(3)
        for k in range(3):
            if self.in_game:
                return
            self.t.send_frame({"i": [85, tid]})
            print(f"[bot] bấm BẮT ĐẦU lần {k + 1} (bàn #{tid})")
            time.sleep(3)

    def pump(self, seconds):
        """Vừa chờ vừa ĐỌC GÓI. Dùng time.sleep() ở đây là tự bịt mắt mình:
        vòng lặp chính là nơi duy nhất nhận gói, ngủ trong đó thì self.tables
        không bao giờ cập nhật -> luôn tưởng là ngồi ghế thất bại."""
        end = time.time() + seconds
        while time.time() < end:
            for f in self.t.recv_frames(timeout=2):
                self.on_frame(f)
            time.sleep(0.1)

    def try_join_table(self):
        # Đang ngồi hoặc đang đánh thì TUYỆT ĐỐI không đi ngồi bàn khác.
        # Thiếu chốt này, sau khi vào lại kênh bot vừa đánh ở bàn cũ vừa gửi
        # [72]/[83] sang bàn mới -> self.my_seat và self.table bị ghi đè giữa
        # ván, ghế nhảy 0 <-> 1 và bot đánh nhầm màu.
        if self.seated or self.in_game:
            return True
        """Vào bàn người khác đang thiếu người: [72] vào bàn -> [83] ngồi ghế.

        Phải gửi [72] TRƯỚC, nếu không server bỏ qua [83] (đây là lý do các bản
        trước không bao giờ ngồi được vào bàn có sẵn).
        """
        base = getattr(self, "room_base", None)
        for tid, v in list(self.tables.items()):
            if tid in self.joined or len(v) < 3:
                continue
            if base is not None and not (base <= tid < base + 100):
                continue                      # bàn của phòng khác, bỏ qua
            p0, p1 = v[1], v[2]
            if self.myname in (p0, p1):
                continue
            if bool(p0) == bool(p1):        # cần đúng 1 ghế trống
                continue
            seat = 1 if p0 else 0
            self.joined.add(tid)
            who = p0 or p1
            print(f"[bot] vào bàn #{tid} của {who} ({v[0]}), ngồi ghế {seat}")
            # Đặt self.table TRƯỚC khi gửi [72]: nếu không, các gói 88/90/91
            # của bàn vừa xem sẽ kích hoạt nhánh "bàn mới do bot tạo".
            self.table = tid
            self.t.send_frame({"i": [72, tid]})
            self.pump(1.0)

            # NGỒI XONG BẤM BẮT ĐẦU NGAY. Vào bàn người khác mà chần chừ quá
            # ~10 giây không bấm là bị đá ra (bàn mình tạo thì không sao vì
            # đối thủ bấm hộ). Vì vậy: [83] -> [85] liền tay, xác nhận sau.
            self.my_seat = seat
            self.t.send_frame({"i": [83, tid, seat]})
            self.pump(0.6)
            self.t.send_frame({"i": [85, tid]})
            self.pump(2.0)

            cur = self.tables.get(tid, [])
            if self.myname not in cur:
                # thử nốt ghế còn lại, nhưng CHỈ khi nó thật sự trống
                other = 1 - seat
                occupied = cur[1 + other] if len(cur) >= 3 else ""
                if not occupied:
                    self.my_seat = other
                    self.t.send_frame({"i": [83, tid, other]})
                    self.pump(0.6)
                    self.t.send_frame({"i": [85, tid]})
                    self.pump(2.0)
                    cur = self.tables.get(tid, [])

            if self.myname in cur:
                self.my_seat = 0 if cur[1] == self.myname else 1
                print(f"[bot] ✅ đã ngồi bàn #{tid} ghế {self.my_seat}: {cur}")
                print(f"       https://www.playok.com/en/xiangqi/#{tid}")
                self.seated = True
                self.t.send_frame({"i": [85, tid]})   # bấm thêm lần nữa cho chắc
                return True

            print(f"[bot] không ngồi được bàn #{tid} ({cur}) -> rời, tìm bàn khác")
            self.t.send_frame({"i": [73, tid]})
            self.table = None
        return False

    def our_turn(self):
        """Chỉ đúng khi ván ĐANG ĐÁNH và lượt thuộc GHẾ của mình."""
        return self.in_game and self.turn_seat == self.my_seat

    def _diag(self):
        return (f"state={self.state} turn_seat={self.turn_seat} "
                f"my_seat={self.my_seat} side={self.side} color={self.color} "
                f"nmoves={self.nmoves} thinking={self.thinking}")

    def maybe_move(self):
        if self.thinking:
            return
        if not self.in_game:
            # ván chưa chạy nhưng đã ngồi -> nhắc server bắt đầu
            if self.seated and self.table and time.time() - getattr(self, "_last_go", 0) > 4:
                self._last_go = time.time()
                self.t.send_frame({"i": [85, self.table]})
            return
        if not self.seated:
            return          # đang xem ván người khác thì không đánh
        if not self.our_turn():
            return
        print(f"[bot] tới lượt tôi | {self._diag()}")
        self.thinking = True
        threading.Thread(target=self._move, daemon=True).start()

    def _move(self):
        try:
            fen = self.board.to_fen(self.side)
            print("[bot] FEN:", fen)
            t0 = time.time()
            mv = self.engine.best_move(fen, self.movetime)
            print("[bot] bestmove:", mv)
            if not (mv and mv not in ("(none)", "0000") and len(mv) >= 4):
                return
            packed = uci_to_packed(mv)
            if self.legal and packed not in self.legal:
                print(f"[bot] ⚠️ {mv} (packed {packed}) KHÔNG có trong "
                      f"{len(self.legal)} nước hợp lệ của server -> bàn cờ "
                      f"của bot đang lệch. Gửi thử nước hợp lệ đầu tiên.")
                packed = self.legal[0]
                mv = packed_to_uci(packed)
            used = int((time.time() - t0) * 100)
            n_before = self.nmoves
            self.last_sent = (packed, time.time())
            self.t.send_frame({"i": [CODE_MOVE, self.table, 1, packed, used]})
            print(f"[bot] gửi {mv} (packed {packed})")
            # server phát nước ngược về -> nmoves tăng. Không thấy thì gửi lại 1 lần.
            for _ in range(30):
                time.sleep(0.1)
                if self.nmoves > n_before:
                    return
            if self.our_turn() and self.nmoves == n_before:
                print("[bot] ↻ chưa thấy server nhận, gửi lại 1 lần")
                self.t.send_frame({"i": [CODE_MOVE, self.table, 1, packed, used]})
        except Exception as e:
            print("[bot] move error:", e)
        finally:
            self.thinking = False

    def run(self, seconds=180):
        self.fetch_session()
        print("[bot] connecting transport ...")
        if hasattr(self.t, "start_keepalive"):
            self.t.start_keepalive()
        self.login()
        # Chờ đủ lâu để server kịp gửi: danh sách bàn + (nếu có) BÀN CŨ mà nó
        # tự khôi phục cho mình. Trước đây bot vội tạo bàn mới trong khi server
        # đã xếp sẵn nó vào bàn đang có ván dở -> ngồi mà tưởng chưa ngồi.
        deadline0 = time.time() + 8
        while time.time() < deadline0:
            for f in self.t.recv_frames(timeout=1.0):
                self.on_frame(f)
            if self.seated:
                break
        if self.seated:
            print(f"[bot] server khôi phục sẵn bàn #{self.table} ghế {self.my_seat}"
                  f" -> dùng luôn, không tạo bàn mới")
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
                print(f"[bot] kênh chết (lần {fails}) -> chờ {wait}s rồi vào lại")
                time.sleep(wait)
                try:
                    self.fetch_session()      # lấy ge/ap mới
                    self.t.reopen()
                    self.login()
                    self.table = None
                    self.in_game = False
                    time.sleep(2)
                    self.await_new_table = True
                    self.t.send_frame({"i": [CODE_NEW_TABLE]})
                    print("[bot] đã vào lại, tạo bàn mới")
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
            if (not self.in_game and not self.seated
                    and time.time() - getattr(self, "_last_hunt", 0) > 20):
                self._last_hunt = time.time()
                self.try_join_table()
            frames = self.t.recv_frames()
            if frames:
                fails = 0
            for f in frames:
                self.on_frame(f)
            self.maybe_move()      # phòng khi gói 90 tới lúc đang nghĩ
            time.sleep(0.2)
        print("[bot] run finished")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--engine", default="/home/user/playok/Linux/pikafish-avx2")
    ap.add_argument("--nnue", default="/home/user/playok/pikafish.nnue")
    ap.add_argument("--color", choices=["red", "black"], default="red")
    ap.add_argument("--transport", choices=["auto", "ws", "polling"], default="auto")
    ap.add_argument("--room", default="hanoi",
                    help="tên phòng cố định (hanoi, haiphong, danang, cantho...)")
    ap.add_argument("--create", action="store_true", help="tạo bàn mới khi vào")
    ap.add_argument("--seconds", type=int, default=180)
    ap.add_argument("--movetime", type=int, default=1500,
                    help="thời gian engine nghĩ mỗi nước (mili giây)")
    ap.add_argument("--ttype", default=os.environ.get("PLAYOK_TTYPE", ""),
                    help="hạn chế bàn tự tạo: public | private | ngưỡng elo "
                         "(1200/1350/1500/1650/1800/1950/2100) | bậc 1-7")
    args = ap.parse_args()

    engine = Pikafish(args.engine, nnue=args.nnue)

    transport = None
    if args.transport in ("auto", "ws"):
        try:
            transport = WebSocketTransport(HOST, ["wss:17003", "wss:443"])
        except Exception as e:
            print("[net] WS unavailable:", e)
            transport = None
    if transport is None and args.transport in ("auto", "polling"):
        transport = PollingTransport(HOST, 443)

    bot = Bot(transport, engine, args.color, create_table=args.create)
    bot.want_room = args.room
    bot.movetime = args.movetime
    bot.want_ttype = args.ttype or None
    try:
        bot.run(args.seconds)
    except KeyboardInterrupt:
        print("\n[bot] stopped")
    finally:
        transport.close()
        engine.close()


if __name__ == "__main__":
    main()
