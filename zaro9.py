#!/usr/bin/env python3
"""
zaro9 - Xiangqi Bot (gamevh.net) - engine Pikafish
Chuyển từ caro sang cờ tướng, dùng chung mã nguồn với 77.py.
Tài khoản: nguyen9
"""

import struct
import threading
import time
import sys
import os
import requests
import re
import subprocess
import signal
import atexit
import tempfile

# ==================== TÀI KHOẢN (KHÔNG CẦN COOKIE) ====================
# Đăng nhập trực tiếp bằng username/password giống các bot nguyen1..nguyen6
CARO_USER_DIRECT = "nguyen9"
CARO_PASSWD_DIRECT = "nhat123456"

def _clean_env(val, default):
    if val and str(val).strip():
        return str(val).strip()
    return default

USER = _clean_env(os.environ.get("ZARO9_USER"), CARO_USER_DIRECT)
PASSWD = _clean_env(os.environ.get("ZARO9_PASSWD"), CARO_PASSWD_DIRECT)

# Cookie sẽ được tạo tự động sau khi đăng nhập (không hardcode nữa)
COOKIE = ""

_venv_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'venv', 'lib')
for _py_ver in ['python3.12', 'python3.13', 'python3.11']:
    _candidate = os.path.join(_venv_path, _py_ver, 'site-packages')
    if os.path.isdir(_candidate):
        sys.path.insert(0, _candidate)
        break

WS_URL = "wss://gamevh.net/ws/gameServer"
LOGIN_URL = "https://gamevh.net/login.jsp"
GAME_URL = "https://gamevh.net/play/xiangqi/0"
CURRENT_PLAYER_NICKNAME = USER
CURRENT_PLAYER_ID = 0
TOKEN = 0
GAME_ID = 'xiangqi'
PLACE_PATH = 'Lobby.xiangqi.0'

BOT_BET_XU = 5000
BOT_USE_CREATE_TABLE = True
BOT_MATCH_DURATION = '10'
BOT_TURN_DURATION = '60'
BOT_ACC_DURATION = '0'
BOT_BLOCK_SOFTWARE = '0'

def fetch_session_info():
    """Đăng nhập bằng USER/PASSWD (không dùng cookie hardcode) và lấy token/nickname/playerId."""
    global COOKIE, TOKEN, CURRENT_PLAYER_NICKNAME, CURRENT_PLAYER_ID, PLACE_PATH
    try:
        session = requests.Session()
        ua = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/139.0 Safari/537.36")
        session.headers.update({
            "User-Agent": ua,
            "Accept-Language": "vi-VN,vi;q=0.9,en;q=0.7",
        })

        # B1: mở trang login để lấy JSESSIONID
        session.get(LOGIN_URL, timeout=20)

        # B2: POST đăng nhập
        resp = session.post(
            LOGIN_URL, timeout=20,
            data={"redirect": "/", "USER_NAME": USER, "PASSWORD": PASSWD,
                  "AUTO_LOGIN": "true", "LOGIN": "Đăng nhập"},
            headers={"Origin": "https://gamevh.net",
                     "Referer": LOGIN_URL,
                     "Content-Type": "application/x-www-form-urlencoded"},
            allow_redirects=True)
        if "login.jsp" in resp.url:
            print(f"[SESSION] Đăng nhập thất bại (sai tài khoản/mật khẩu?): {resp.url}")
            return False

        # B3: vào trang game để lấy token / nickname / playerId
        game_resp = session.get(GAME_URL, timeout=20)
        page_html = game_resp.text

        tm = re.search(r"var\s+token\s*=\s*(-?\d+)", page_html)
        if not tm:
            print("[SESSION] Không tìm thấy token")
            return False
        TOKEN = int(tm.group(1))

        nm = re.search(r"var\s+currentPlayerNickName\s*=\s*[\"']([^\"']+)[\"']", page_html)
        if not nm:
            print("[SESSION] Không tìm thấy currentPlayerNickName")
            return False
        CURRENT_PLAYER_NICKNAME = nm.group(1).strip()

        pid = re.search(r"var\s+currentPlayerId\s*=\s*(\d+)", page_html)
        if pid:
            CURRENT_PLAYER_ID = int(pid.group(1))

        pm = re.search(r"var\s+placePath\s*=\s*[\"']([^\"']+)[\"']", page_html)
        if pm:
            PLACE_PATH = pm.group(1)

        # B4: dựng cookie từ session vừa đăng nhập
        COOKIE = "; ".join(f"{k}={v}" for k, v in session.cookies.items())

        if CURRENT_PLAYER_NICKNAME != USER:
            print(f"[SESSION] Cảnh báo: nickname server={CURRENT_PLAYER_NICKNAME!r} khác USER={USER!r}")
        print(f"[SESSION] Login OK | Token: {TOKEN} | NickName: {CURRENT_PLAYER_NICKNAME} | PlayerID: {CURRENT_PLAYER_ID}")
        return True
    except Exception as e:
        print(f"[SESSION] Lỗi đăng nhập: {e}")
        return False

CMD_NAMES = {
    300: "PONG", 301: "PING", 302: "LOGIN", 303: "ALERT",
    311: "BROADCAST", 314: "SET_CLIENT_MODE", 315: "CONFIG",
    331: "CHAT.SEND", 335: "CHAT.MSG",
    401: "ENTER_PLACE", 405: "CREATE_RULE", 406: "PLAYER_ENTERED", 407: "PLAYER_EXITED",
    408: "QUICK_PLAY", 412: "LIST_ZONE_ROOM", 413: "LIST_BET_AMT",
    414: "GET_TABLE_DATA", 416: "SLOT_IN_TABLE_CHANGED",
    417: "START_MATCH", 418: "GAMEOVER", 419: "ENTER_STATE",
    420: "SET_TURN", 434: "SET_READY",
    502: "PLAY", 529: "MOVE", 533: "ASK_DRAW", 534: "SURRENDER", 601: "LOGIN_EX",
}

class Conn:
    def pack(self, cmd, data=b''):
        result = bytearray()
        if isinstance(cmd, str):
            cmd_bytes = cmd.encode('ascii')
            result.append((-len(cmd_bytes)) & 0xFF)
            result.extend(cmd_bytes)
        elif isinstance(cmd, int):
            result.extend(struct.pack('>H', cmd))
        result.extend(data)
        return bytes(result)
    def pack_byte(self, value): return struct.pack('>b', value)
    def pack_int(self, value): return struct.pack('>i', value)
    def pack_ascii(self, value):
        encoded = value.encode('ascii')[:255]
        return struct.pack('>b', len(encoded)) + encoded
    def pack_string(self, value):
        encoded = value.encode('utf-16-be')
        return struct.pack('>h', len(encoded) // 2) + encoded

class InboundMessage:
    def __init__(self, data):
        self.data = bytes(data)
        self.offset = 0
        self.command = self._parse_command()
    def _parse_command(self):
        length = self.read_byte()
        if length < 0:
            cmd = self.data[self.offset:self.offset + (-length)].decode('ascii', errors='replace')
            self.offset += (-length)
            return cmd
        else:
            next_byte = self.data[self.offset] & 0xFF
            self.offset += 1
            return CMD_NAMES.get((length << 8) | next_byte, str((length << 8) | next_byte))
    def read_byte(self):
        val = struct.unpack_from('>b', self.data, self.offset)[0]
        self.offset += 1
        return val
    def read_short(self):
        val = struct.unpack_from('>h', self.data, self.offset)[0]
        self.offset += 2
        return val
    def read_int(self):
        val = struct.unpack_from('>i', self.data, self.offset)[0]
        self.offset += 4
        return val
    def read_long(self):
        val = struct.unpack_from('>q', self.data, self.offset)[0]
        self.offset += 8
        return val
    def read_ascii(self):
        length = self.read_byte()
        if length < 0: length += 256
        s = self.data[self.offset:self.offset + length].decode('ascii', errors='replace')
        self.offset += length
        return s
    def read_string(self):
        char_count = self.read_short()
        s = self.data[self.offset:self.offset + char_count * 2].decode('utf-16-be', errors='replace')
        self.offset += char_count * 2
        return s

STANDARD_PAWN_POSITIONS = set()
for _c in [0, 2, 4, 6, 8]:
    STANDARD_PAWN_POSITIONS.add(6 * 9 + _c)
    STANDARD_PAWN_POSITIONS.add(3 * 9 + _c)

class XiangqiBoardTracker:
    INITIAL_FEN = "rnbakabnr/9/1c5c1/p1p1p1p1p/9/9/P1P1P1P1P/1C5C1/9/RNBAKABNR w"
    def __init__(self): self.reset()
    def reset(self):
        self.fen = self.INITIAL_FEN
        self.move_history = []
        self.my_slot_id = -1
        self.first_turn_slot_id = 0
        self.is_my_turn = False
        self.is_playing = False
        self.is_red = None
    def pos_to_engine_move(self, source_pos, target_pos):
        s_col, s_row = source_pos % 9, source_pos // 9
        t_col, t_row = target_pos % 9, target_pos // 9
        if not self.is_red:
            s_row, t_row = 9 - s_row, 9 - t_row
        return f"{chr(ord('a') + s_col)}{s_row}{chr(ord('a') + t_col)}{t_row}"
    def engine_move_to_pos(self, engine_move):
        s_col, s_rank = ord(engine_move[0]) - ord('a'), int(engine_move[1])
        t_col, t_rank = ord(engine_move[2]) - ord('a'), int(engine_move[3])
        s_row, t_row = s_rank, t_rank
        if not self.is_red:
            s_row, t_row = 9 - s_row, 9 - t_row
        return s_row * 9 + s_col, t_row * 9 + t_col
    def get_current_fen(self):
        side = 'w' if len(self.move_history) % 2 == 0 else 'b'
        board_fen = self.fen.split(' ')[0] if ' ' in self.fen else self.fen
        return f"{board_fen} {side}", self.move_history
    def set_my_slot(self, slot_id, first_turn_slot_id):
        self.my_slot_id = slot_id
        self.first_turn_slot_id = first_turn_slot_id
        self.is_red = (self.my_slot_id == first_turn_slot_id)

class TrendAnalyzer:
    """Bộ não phân tích dữ liệu RAM: Hỗ trợ quét kép Sát cục (Mate) và Điểm số xu hướng (CP)"""
    def __init__(self):
        self.pv_ram_cache = {}
        self.info_regex = re.compile(r"info .* score cp (-?\d+) .* pv (.+)")
        self.mate_regex = re.compile(r"info .* score mate (-?\d+) .* pv (.+)")

    def clear(self):
        self.pv_ram_cache.clear()

    def parse_line(self, line_str):
        # 1. Quét thế trận sát cục (Mate) trước để tránh Bot đi vòng vo khi sắp thắng
        mate_match = self.mate_regex.search(line_str)
        if mate_match:
            mate_score = int(mate_match.group(1))
            pv_line = mate_match.group(2).split()
            if pv_line:
                first_move = pv_line[0]
                self.pv_ram_cache[first_move] = {
                    "current_score": 99999 if mate_score > 0 else -99999,
                    "mate_in": mate_score,
                    "pv_chain": pv_line
                }
                return

        # 2. Nếu không có sát cục, tiến hành phân tích điểm cp thông thường
        match = self.info_regex.search(line_str)
        if match:
            score = int(match.group(1))
            pv_line = match.group(2).split()
            if len(pv_line) >= 3:
                first_move = pv_line[0]
                self.pv_ram_cache[first_move] = {
                    "current_score": score,
                    "mate_in": None,
                    "pv_chain": pv_line
                }

    def select_best_trend_move(self):
        if not self.pv_ram_cache:
            return None

        # ƯU TIÊN TUYỆT ĐỐI: Có nhánh báo sát cục thắng (mate dương), xuất quân dứt điểm ngay!
        for move, data in self.pv_ram_cache.items():
            if data["mate_in"] is not None and data["mate_in"] > 0:
                print(f"[RAM-MATE] 🔥 Phát hiện nhánh sát cục tuyệt đối! Dứt điểm ngay: {move}")
                return move

        best_move = None
        avg_score = sum(d["current_score"] for d in self.pv_ram_cache.values()) / len(self.pv_ram_cache)
        is_negative = avg_score < 0

        if is_negative:
            # THẾ YẾU (ĐIỂM ÂM): Chọn nhánh có điểm âm thấp nhất (giảm thiểu suy thoái)
            max_recovery = -999999
            for move, data in self.pv_ram_cache.items():
                recovery_rate = data["current_score"] 
                if recovery_rate > max_recovery:
                    max_recovery = recovery_rate
                    best_move = move
            print(f"[RAM-LEARN] Đang lép vế ({int(avg_score)}). Ép chọn nước phòng thủ tốt nhất: {best_move}")
        else:
            # THẾ MẠNH (ĐIỂM DƯƠNG): Chọn nhánh có tốc độ bứt phá điểm cao nhất
            max_growth = -999999
            for move, data in self.pv_ram_cache.items():
                growth_rate = data["current_score"]
                if growth_rate > max_growth:
                    max_growth = growth_rate
                    best_move = move
            print(f"[RAM-LEARN] Đang ưu thế (+{int(avg_score)}). Ép chọn nước tăng điểm tốt nhất: {best_move}")

        return best_move

class PikafishBot:
    def __init__(self):
        self.conn = Conn()
        self.board = XiangqiBoardTracker()
        self.trend_analyzer = TrendAnalyzer()  
        self.engine = None
        self.ws = None
        self.connected = False
        self.logged_in = False
        self.in_game = False
        self._joining_table = False
        self._last_quick_play_time = 0
        self._QUICK_PLAY_INTERVAL = 10
        self.bet_amts = []
        self._resolved_bet_id = None
        self._bet_amts_loaded = False
        self.fixed_pawn_positions = set()
        self.last_action_timestamp = time.time()
        self.last_recv_timestamp = time.time()
        self._table_path = None          # nhớ bàn đang ngồi để quay lại sau khi rớt mạng
        self._table_path_ts = 0.0
        self._reconnect_streak = 0       # số lần rớt liên tiếp -> giãn thời gian thử lại
        self._connected_since = 0.0
        self._enter_fail_at = 0.0        # lúc ENTER_PLACE bị từ chối (để dò bàn "ma")
        self._latest_bestmove = None
        self._mate_status = None
        self._mate_regex = re.compile(r"score mate (-?\d+)")
        self._init_engine()

    def _init_engine(self):
        possible_paths = [
            os.path.expanduser("~/pikafish"),
            os.path.expanduser("~/Android/pikafish-armv8"),
            "/data/data/com.termux/files/home/pikafish",
            "./pikafish"
        ]
        pikafish_path = next((p for p in possible_paths if os.path.isfile(p) and os.access(p, os.X_OK)), None)
        if not pikafish_path: return

        try:
            self._engine_proc = subprocess.Popen(
                [pikafish_path], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1
            )
            
            def consume_stderr(proc):
                try:
                    while proc.poll() is None:
                        if not proc.stderr.readline(): break
                except: pass
            threading.Thread(target=consume_stderr, args=(self._engine_proc,), daemon=True).start()

            def consume_stdout_and_filter(proc):
                try:
                    while proc.poll() is None:
                        line = proc.stdout.readline()
                        if not line: break
                        line_str = line.strip()
                        
                        self.trend_analyzer.parse_line(line_str)
                        
                        if "score mate" in line_str:
                            match = self._mate_regex.search(line_str)
                            if match:
                                val = int(match.group(1))
                                if val > 0: self._mate_status = f"WIN_IN_{val}"
                                elif val < 0: self._mate_status = f"LOSE_IN_{abs(val)}"
                        
                        if line_str.startswith("bestmove"):
                            self._latest_bestmove = line_str
                except: pass
            threading.Thread(target=consume_stdout_and_filter, args=(self._engine_proc,), daemon=True).start()

            self._fsf_cmd("uci")
            # Chừa ít nhất 1 nhân cho luồng WebSocket, nếu không pong/heartbeat bị trễ
            # và server cắt kết nối ngay giữa ván.
            _threads = max(1, min(4, (os.cpu_count() or 2) - 1))
            self._fsf_cmd(f"setoption name Threads value {_threads}")
            self._fsf_cmd("setoption name Hash value 256")
            
            # Khóa cứng MultiPV = 3 để RAM phản xạ mượt và nhanh hơn 5
            self._fsf_cmd("setoption name MultiPV value 3")
            
            time.sleep(1)
            
            nnue_path = os.path.expanduser("~/pikafish.nnue")
            if not os.path.isfile(nnue_path):
                nnue_path = os.path.join(os.path.dirname(pikafish_path), "pikafish.nnue")
            if os.path.isfile(nnue_path):
                self._fsf_cmd(f"setoption name EvalFile value {nnue_path}")
                self._fsf_cmd("setoption name UseNNUE value true")
            else:
                self._fsf_cmd("setoption name UseNNUE value false")
            self._fsf_cmd("isready")
            self.engine = True
            print(f"[ENGINE] ✅ Sẵn sàng hoạt động với MultiPV=3 nâng cấp bộ não chống ngáo sát cục.")
        except Exception as e:
            print(f"[ENGINE] ❌ Lỗi khởi tạo: {e}")

    def _fsf_cmd(self, text):
        if getattr(self, '_engine_proc', None) and self._engine_proc.poll() is None:
            self._engine_proc.stdin.write(text + "\n")
            self._engine_proc.stdin.flush()

    def get_best_move(self, fen, moves, fixed_positions=None):
        try:
            if not getattr(self, '_engine_proc', None) or self._engine_proc.poll() is not None: return None
            self.trend_analyzer.clear() 
            
            if fixed_positions: return self._get_move_avoiding_fixed(fen, moves, fixed_positions)
            pos_cmd = f"position fen {fen}"
            if moves: pos_cmd += " moves " + " ".join(moves)
            self._fsf_cmd(pos_cmd)
            
            # ÉP THỜI GIAN: Cho Engine chạy đúng 1.2 giây để lấy đủ dữ liệu chuỗi PV
            self._fsf_cmd("go movetime 3200")
            # movetime 3200ms mà timeout 3s -> luôn bị "stop" trước khi engine trả lời
            return self._read_bestmove(timeout=4.2)
        except Exception as e: print(f"[ENGINE] Lỗi tính toán: {e}")
        return None

    def _read_bestmove(self, timeout=3):
        _go_start = time.time()
        self._latest_bestmove = None 
        self._mate_status = None     
        
        while True:
            if self._engine_proc.poll() is not None: return None
            if self._latest_bestmove:
                return self._latest_bestmove
            
            if time.time() - _go_start > timeout:
                self._fsf_cmd("stop")
                time.sleep(0.1)
                if self._latest_bestmove:
                    return self._latest_bestmove
                break
            time.sleep(0.02) # Phản xạ luồng đọc siêu tốc
        return None

    def _get_move_avoiding_fixed(self, fen, moves, fixed_positions):
        pos_cmd = f"position fen {fen}"
        if moves: pos_cmd += " moves " + " ".join(moves)
        self._fsf_cmd(pos_cmd)
        
        self._latest_bestmove = None
        self._mate_status = None
        self._fsf_cmd("go movetime 1200")
        
        _wait_start = time.time()
        while time.time() - _wait_start < 2:
            if self._latest_bestmove: break
            time.sleep(0.05)
            
        self._fsf_cmd("stop")
        time.sleep(0.1)
        
        if self._latest_bestmove:
            return self._latest_bestmove
        return None

    def connect(self):
        import websocket
        self.connected = False
        self.ws = websocket.WebSocketApp(
            WS_URL, cookie=COOKIE,
            on_open=self._on_open, on_message=self._on_message,
            on_error=self._on_error, on_close=self._on_close,
            header={"Origin": "https://gamevh.net"}
        )
        # ping_timeout=10 cũ khiến engine ăn hết CPU 3.2s/nước -> pong trả chậm -> tự ngắt
        # giữa ván. Bỏ pong-timeout, thay bằng kiểm tra liveness theo dữ liệu nhận được.
        self.ws_thread = threading.Thread(
            target=lambda: self.ws.run_forever(ping_interval=30, ping_timeout=None),
            daemon=True)
        self.ws_thread.start()
        for _ in range(25):
            if self.connected: break
            time.sleep(0.2)
        return self.connected

    def _on_open(self, ws):
        self.connected = True
        self.last_action_timestamp = time.time()
        self.last_recv_timestamp = time.time()
        self._connected_since = time.time()
        self._send_login()

    def _on_message(self, ws, message):
        self.last_recv_timestamp = time.time()
        if isinstance(message, bytes): self._handle_binary_message(message)
    def _on_error(self, ws, error):
        print(f"[WS] ❌ Lỗi kết nối: {type(error).__name__}: {error}")

    def _on_close(self, ws, code, msg):
        if self.board.is_playing:
            print(f"[WS] ⚠️ MẤT KẾT NỐI GIỮA VÁN (code={code}, msg={msg}) -> mất bàn, sẽ phải tạo bàn mới")
        else:
            print(f"[WS] Đóng kết nối (code={code}, msg={msg})")
        # Nếu vừa kết nối đã đứt ngay (<60s) thì coi là rớt liên tiếp -> cần giãn nhịp,
        # tránh đăng nhập dồn dập tạo ra hàng loạt phiên/bàn rác trên server.
        if self._connected_since and time.time() - self._connected_since < 60:
            self._reconnect_streak += 1
        else:
            self._reconnect_streak = 0
        self.connected = False
        self.logged_in = False
        self.in_game = False
        self._joining_table = False
        self._bet_amts_loaded = False
        self._resolved_bet_id = None
        self.bet_amts = []
        self.fixed_pawn_positions = set()
        self.board.reset()

    def send_message(self, cmd, data=b''):
        if self.ws and self.connected:
            try: self.ws.send(self.conn.pack(cmd, data), opcode=0x2)
            except: pass

    def _send_login(self):
        data = bytearray()
        data.extend(self.conn.pack_ascii(CURRENT_PLAYER_NICKNAME))
        data.extend(self.conn.pack_int(TOKEN))
        data.extend(self.conn.pack_ascii("5.0.2"))
        data.extend(self.conn.pack_ascii(""))
        data.extend(self.conn.pack_ascii(GAME_ID))
        data.extend(self.conn.pack_byte(1))
        self.send_message("LOGIN", bytes(data))

    def send_enter_place(self, path=None, mode=1):
        data = bytearray()
        data.extend(self.conn.pack_ascii(path or PLACE_PATH))
        data.extend(self.conn.pack_string(""))
        data.extend(self.conn.pack_byte(mode))
        self.send_message("ENTER_PLACE", bytes(data))

    def send_list_bet_amt(self): self.send_message("LIST_BET_AMT")

    def resolve_bet_amt_id(self):
        if not self.bet_amts: return None
        for ba in self.bet_amts:
            if ba['value'] == BOT_BET_XU: return ba['id']
        lower = [ba for ba in self.bet_amts if 0 < ba['value'] <= BOT_BET_XU]
        if lower: return max(lower, key=lambda x: x['value'])['id']
        return 0

    def send_create_table(self):
        now = time.time()
        if now - self._last_quick_play_time < self._QUICK_PLAY_INTERVAL: return
        self._last_quick_play_time = now
        bet_amt_id = self._resolved_bet_id if self._resolved_bet_id is not None else self.resolve_bet_amt_id()
        if bet_amt_id is None: return
        args = [
            ("matchDuration", str(BOT_MATCH_DURATION)),
            ("turnDuration", str(BOT_TURN_DURATION)),
            ("accDuration", str(BOT_ACC_DURATION)),
            ("blockSoftware", str(BOT_BLOCK_SOFTWARE)),
        ]
        data = bytearray()
        data.extend(self.conn.pack_byte(bet_amt_id))       
        data.extend(self.conn.pack_byte(len(args)))        
        for arg_name, arg_value in args:
            data.extend(self.conn.pack_ascii(arg_name))    
            data.extend(self.conn.pack_string(arg_value))  
        self.send_message("CREATE_RULE", bytes(data))

    def send_quick_play(self, room_id="", bet_amt_id=-1):
        now = time.time()
        if now - self._last_quick_play_time < self._QUICK_PLAY_INTERVAL: return
        self._last_quick_play_time = now
        data = bytearray()
        data.extend(self.conn.pack_ascii(room_id))
        data.extend(self.conn.pack_byte(bet_amt_id))
        self.send_message("QUICK_PLAY", bytes(data))

    def send_play(self, source_pos, target_pos):
        data = bytearray()
        data.extend(self.conn.pack_byte(source_pos))
        data.extend(self.conn.pack_byte(target_pos))
        self.send_message("PLAY", bytes(data))

    def send_ready(self, is_ready=1):
        if self.board.is_playing: return
        print("[GAME] ⏳ Gửi trạng thái READY...")
        data = bytearray()
        data.extend(self.conn.pack_byte(is_ready))
        self.send_message("SET_READY", bytes(data))

    def _handle_binary_message(self, data):
        try:
            msg = InboundMessage(data)
            cmd = msg.command
            if cmd == "PING": self.send_message("PONG")
            elif cmd == "LOGIN": self._handle_login_response(msg)
            elif cmd == "ENTER_PLACE": self._handle_enter_place_response(msg)
            elif cmd == "QUICK_PLAY": self._handle_quick_play_response(msg)
            elif cmd == "LIST_BET_AMT": self._handle_list_bet_amt_response(msg)
            elif cmd == "CREATE_RULE": self._handle_create_rule_response(msg)
            elif cmd == "SLOT_IN_TABLE_CHANGED": self._handle_slot_changed(msg)
            elif cmd == "START_MATCH": self._handle_start_match(msg)
            elif cmd == "MOVE": self._handle_move(msg)
            elif cmd == "PLAY" or cmd == "502": self._handle_play_response(msg)
            elif cmd == "SET_TURN": self._handle_set_turn(msg)
            elif cmd == "GAMEOVER": self._handle_gameover(msg)
            elif cmd == "ALERT":
                try: print(f"[SERVER] ALERT: {msg.read_string()}")
                except Exception: pass
        except Exception as e: print(f"[RECV ERROR] {e}")

    def _handle_login_response(self, msg):
        if msg.read_byte() == 0:
            self.logged_in = True
            path = msg.read_string()
            if path == 'REFRESH':
                fetch_session_info()
                self._send_login()
                return
            self.send_enter_place()

    def _handle_enter_place_response(self, msg):
        status = msg.read_byte()
        if status != 0:
            # Chỉ coi là "vào bàn lỗi" khi CHÍNH bot đang xin vào bàn; các gói 401 khác
            # (server đẩy về khi người khác ra/vào) thì bỏ qua, tuyệt đối không đụng
            # vào in_game kẻo bot đang ngồi bàn lại tưởng mình đã ra ngoài.
            if self._joining_table:
                # status=-1 hầu hết là "đã ở sẵn trong bàn đó rồi" (server tự xếp chỗ
                # cho người tạo bàn). Nếu vội đặt in_game=False rồi tạo bàn khác thì sẽ
                # đẻ ra bàn rác trong khi bot vẫn đang ngồi bàn cũ. Vì vậy: coi như đã
                # ở trong bàn, bấm Sẵn sàng, và chỉ bỏ bàn nếu 60s nữa vẫn không có gì.
                print(f"[TABLE] ENTER_PLACE trả status={status} -> coi như đã ở trong bàn, bấm Sẵn sàng")
                self._joining_table = False
                self.in_game = True
                self._enter_fail_at = time.time()
                threading.Thread(
                    target=lambda: (time.sleep(3.0), self.send_ready(1)), daemon=True).start()
            return
        if True:
            if self._joining_table:
                self._joining_table = False
                self.in_game = True
                self._enter_fail_at = 0.0
                self.last_action_timestamp = time.time()
                def delay_initial_ready():
                    time.sleep(3.0)  
                    self.send_ready(1)
                threading.Thread(target=delay_initial_ready, daemon=True).start()
            elif not self.in_game:
                # Vừa vào sảnh: nếu vẫn còn bàn cũ (rớt mạng lúc chờ/giữa 2 ván) thì
                # quay lại ngồi bàn cũ thay vì bỏ bàn tạo bàn mới.
                if self._table_path and time.time() - self._table_path_ts < 180:
                    print(f"[TABLE] Thử ngồi lại bàn cũ: {self._table_path}")
                    self.in_game = True
                    self._joining_table = True
                    path = self._table_path
                    threading.Thread(
                        target=lambda: (time.sleep(0.5), self.send_enter_place(path=path, mode=1)),
                        daemon=True).start()
                    return
                self._bet_amts_loaded = False
                self._resolved_bet_id = None
                self.send_list_bet_amt()
            else:
                self.in_game = False
                self._joining_table = False
                self.board.reset()

    def _handle_quick_play_response(self, msg):
        if msg.read_byte() == 0:
            self.in_game = True  
            self._joining_table = True
            table_path = msg.read_ascii()
            self._table_path = table_path; self._table_path_ts = time.time()
            def async_join():
                time.sleep(0.5)
                self.send_enter_place(path=table_path, mode=1)
            threading.Thread(target=async_join, daemon=True).start()

    def _handle_list_bet_amt_response(self, msg):
        if msg.read_byte() != 0: return
        count = msg.read_byte()
        self.bet_amts = [{"id": i, "value": msg.read_int()} for i in range(count)]
        self._resolved_bet_id = self.resolve_bet_amt_id()
        self._bet_amts_loaded = True

    def _handle_create_rule_response(self, msg):
        if msg.read_byte() == 0:
            self.in_game = True  
            self._joining_table = True
            table_path = msg.read_ascii()
            self._table_path = table_path; self._table_path_ts = time.time()
            def async_join():
                time.sleep(0.5)
                self.send_enter_place(path=table_path, mode=1)
            threading.Thread(target=async_join, daemon=True).start()
        else: self._joining_table = False

    def _handle_slot_changed(self, msg):
        try:
            _ = msg.read_string()
            slot_id = msg.read_byte()
            msg.read_long(); msg.read_long(); msg.read_byte(); msg.read_short(); msg.read_ascii(); msg.read_byte(); msg.read_byte()
            player_id = msg.read_long()
            if player_id == CURRENT_PLAYER_ID: 
                self.board.my_slot_id = slot_id
            else:
                if player_id > 0 and not self.board.is_playing:
                    def delay_ready_on_player():
                        time.sleep(3.0)  
                        self.send_ready(1)
                    threading.Thread(target=delay_ready_on_player, daemon=True).start()
        except: pass

    def _handle_start_match(self, msg):
        print(f"[GAME] 🎮 Trận chiến bắt đầu!")
        self._reconnect_streak = 0
        self._enter_fail_at = 0.0
        self.board.reset()
        self.fixed_pawn_positions.clear()
        self.board.is_playing = True
        self.in_game = True
        self._joining_table = False
        self.last_action_timestamp = time.time()

        try:
            player_count = msg.read_byte()
            for _ in range(player_count): msg.read_byte(); msg.read_int()
            piece_count = msg.read_byte()
            board_pieces = []
            for _ in range(piece_count):
                raw_sid = msg.read_byte(); raw_face = msg.read_byte(); pos = msg.read_byte(); is_open = msg.read_byte()
                board_pieces.append((self._decode_piece_id(raw_sid), self._decode_piece_id(raw_face), pos, is_open))

            msg.read_byte(); mystery_count = msg.read_byte()
            for _ in range(mystery_count): msg.read_byte()
            msg.read_byte(); msg.read_byte()

            first_turn_slot_id = msg.read_byte()
            my_slot_id = msg.read_byte()
            if my_slot_id < 0 or my_slot_id == 255:
                my_slot_id = self.board.my_slot_id if self.board.my_slot_id >= 0 else first_turn_slot_id

            self.board.set_my_slot(my_slot_id, first_turn_slot_id)

            for sid, face, position, is_open in board_pieces:
                piece_type = int(face[1]) if len(face) > 1 else 0
                if piece_type == 7 and position not in STANDARD_PAWN_POSITIONS:
                    self.fixed_pawn_positions.add(position)

            self.board.fen = self._build_fen_from_pieces(board_pieces)
            if my_slot_id == first_turn_slot_id:
                self.board.is_my_turn = True
                threading.Thread(target=self._make_auto_move, daemon=True).start()
        except Exception as e: print(f"[START_MATCH ERROR] {e}")

    def _build_fen_from_pieces(self, pieces):
        board = [['.' for _ in range(9)] for _ in range(10)]
        for sid, face, position, is_open in pieces:
            if position < 0 or position >= 90: continue
            game_row, col = position // 9, position % 9
            fen_row = 9 - game_row
            color = face[0]
            piece_type = int(face[1]) if len(face) > 1 else 0
            type_to_fen = {1: 'k', 2: 'a', 3: 'b', 4: 'r', 5: 'c', 6: 'n', 7: 'p'}
            fen_char = type_to_fen.get(piece_type, '?')
            if color == 'r': fen_char = fen_char.upper()
            board[fen_row][col] = fen_char
        fen_rows = []
        for row in board:
            fen_row = ""
            empty = 0
            for cell in row:
                if cell == '.': empty += 1
                else:
                    if empty > 0: fen_row += str(empty); empty = 0
                    fen_row += cell
            if empty > 0: fen_row += str(empty)
            fen_rows.append(fen_row)
        return '/'.join(fen_rows) + ' w'

    def _handle_move(self, msg):
        try:
            source_pos = msg.read_byte()
            target_pos = msg.read_byte()
            engine_move = self.board.pos_to_engine_move(source_pos, target_pos)
            self.last_action_timestamp = time.time()
            if not self.board.move_history or self.board.move_history[-1] != engine_move:
                self.board.move_history.append(engine_move)
        except Exception as e: print(f"[MOVE ERROR] {e}")

    def _handle_play_response(self, msg):
        if msg.read_byte() != 0:
            if self.board.move_history: self.board.move_history.pop()
            self.board.is_my_turn = True

    def _handle_set_turn(self, msg):
        try:
            slot_id = msg.read_byte()
            if slot_id != -1 and self.board.is_playing:  
                was_my_turn = self.board.is_my_turn
                self.board.is_my_turn = (slot_id == self.board.my_slot_id)
                self.last_action_timestamp = time.time()
                if self.board.is_my_turn and not was_my_turn:
                    threading.Thread(target=self._make_auto_move, daemon=True).start()
        except: pass

    def _handle_gameover(self, msg):
        print("[GAME] 🏁 Trận đấu kết thúc.")
        self.fixed_pawn_positions.clear()
        self.board.reset()
        self.board.is_playing = False
        self.board.is_my_turn = False
        self.in_game = True  
        self._joining_table = False
        self.last_action_timestamp = time.time()
        
        if getattr(self, '_engine_proc', None) and self._engine_proc.poll() is None:
            self._fsf_cmd("ucinewgame")
            self._fsf_cmd("isready")

        def delay_ready():
            time.sleep(3.0)
            self.send_ready(1)
        threading.Thread(target=delay_ready, daemon=True).start()

    def _make_auto_move(self):
        if not self.board.is_my_turn or not self.board.is_playing: return
        
        if not getattr(self, '_engine_proc', None) or self._engine_proc.poll() is not None:
            self._init_engine()
            if not self.engine: return

        fen, moves = self.board.get_current_fen()
        fixed = self.fixed_pawn_positions if self.fixed_pawn_positions else None
        
        raw_bestmove_line = self.get_best_move(fen, moves, fixed_positions=fixed)
        if not raw_bestmove_line: return

        parts = raw_bestmove_line.split()
        if len(parts) < 2: return
        best_move = parts[1]

        # ÁP DỤNG BỘ LỌC TỐI ƯU XU HƯỚNG/SÁT CỤC TỪ RAM
        trend_move = self.trend_analyzer.select_best_trend_move()
        if trend_move and best_move not in ["(none)", "0000"]:
            print(f"[RAM-LEARN] 🧠 Thay thế '{best_move}' bằng nước đi tối ưu: '{trend_move}'")
            best_move = trend_move

        # XỬ LÝ KỊCH BẢN KHI HẾT NƯỚC ĐI CỜ TÀN
        if best_move in ["(none)", "0000"]:
            print("\n[HỆ THỐNG TÀN CUỘC] ⚠️ Pikafish báo: bestmove (none) - Hết nước hợp lệ.")
            self.board.is_my_turn = False
            return

        # Gửi nước đi hợp lệ lên hệ thống GameVH
        if best_move:
            try:
                source_pos, target_pos = self.board.engine_move_to_pos(best_move)
                time.sleep(0.3)  # Giả lập thao tác chuột nhẹ nhàng
                if self.board.is_my_turn and self.board.is_playing:
                    print(f"-> Hành động: Xuất quân: {best_move}")
                    self.send_play(source_pos, target_pos)
            except Exception as e: print(f"[BOT ERROR] Dịch tọa độ lỗi: {e}")

    def _decode_piece_id(self, encoded_id):
        color = 'r'
        if encoded_id < 0: encoded_id = -encoded_id; color = 'b'
        return f"{color}{encoded_id >> 3}{'' if (encoded_id & 7) == 0 else (encoded_id & 7)}"

    def start_keep_alive(self):
        def keep_alive_loop():
            while self.connected:
                time.sleep(10)
                if self.connected: self.send_message("PING")
        threading.Thread(target=keep_alive_loop, daemon=True).start()

    def run(self):
        print("[BOT] Khởi chạy hệ thống giám sát tự động...")
        while True:
            try:
                now_ts = time.time()
                # (a) Không nhận được BẤT KỲ gói nào trong 120s -> kết nối đã chết thật
                if self.connected and now_ts - self.last_recv_timestamp > 120:
                    print("[WS] Không nhận dữ liệu 120s -> coi như chết, kết nối lại")
                    if self.ws: self.ws.close()
                    time.sleep(2)
                # (b) Đang trong ván mà 300s không có nước đi nào -> mới cắt (trước là 180s,
                #     dễ cắt nhầm khi đối thủ suy nghĩ lâu và làm mất luôn cái bàn)
                elif self.connected and self.board.is_playing:
                    if now_ts - self.last_action_timestamp > 300:
                        print("[WS] Ván treo 300s không có nước đi -> kết nối lại")
                        if self.ws: self.ws.close()
                        time.sleep(2)

                if not self.connected:
                    if self._reconnect_streak >= 3:
                        print("[BOT] ⚠️ Bị ngắt kết nối liên tục ngay sau khi đăng nhập.")
                        print(f"[BOT] ⚠️ Nhiều khả năng tài khoản {USER} đang được ĐĂNG NHẬP Ở NƠI KHÁC "
                              "(GitHub Actions, máy khác, điện thoại...). Server chỉ cho 1 phiên nên hai bên đá nhau.")
                    if self._reconnect_streak > 0:
                        delay = min(60, 5 * (2 ** min(self._reconnect_streak - 1, 4)))
                        print(f"[WS] Rớt liên tiếp lần {self._reconnect_streak} -> chờ {delay}s rồi đăng nhập lại")
                        time.sleep(delay)
                    if not fetch_session_info():
                        time.sleep(5); continue
                    self.logged_in = False
                    self.in_game = False
                    self._joining_table = False
                    self._bet_amts_loaded = False
                    self._resolved_bet_id = None
                    self.bet_amts = []
                    self.fixed_pawn_positions = set()
                    self.board.reset()
                    if not self.connect():
                        time.sleep(5); continue
                    self.start_keep_alive()
                    time.sleep(2)

                # Sau ENTER_PLACE lỗi: nếu 60s trôi qua mà không vào ván nào thì
                # có lẽ bot KHÔNG thực sự ở trong bàn -> nhả cờ để tạo bàn mới.
                if (self._enter_fail_at and self.in_game and not self.board.is_playing
                        and time.time() - self._enter_fail_at > 60):
                    print("[TABLE] Chờ 60s không vào được ván nào -> bỏ bàn cũ, tạo bàn mới")
                    self._enter_fail_at = 0.0
                    self.in_game = False
                    self._table_path = None

                if (self.connected and self.logged_in and not self.in_game
                        and not self._joining_table):
                    now = time.time()
                    if now - self._last_quick_play_time >= self._QUICK_PLAY_INTERVAL:
                        if BOT_USE_CREATE_TABLE:
                            if not self._bet_amts_loaded: self.send_list_bet_amt()
                            else: self.send_create_table()
                        else: self.send_quick_play()
                time.sleep(3)
            except KeyboardInterrupt: break
            except: time.sleep(5)

    def cleanup(self):
        proc = getattr(self, '_engine_proc', None)
        if proc:
            try: 
                if proc.poll() is None:
                    proc.stdin.write("quit\n"); proc.stdin.flush(); proc.wait(timeout=2)
            except:
                try: proc.terminate()
                except: pass
        if self.ws:
            try: self.ws.close()
            except: pass

def acquire_single_instance_lock():
    """Chặn chạy 2 tiến trình bot cùng tài khoản trên cùng máy.

    Server gamevh chỉ cho 1 phiên/tài khoản: phiên mới đăng nhập sẽ ĐÁ phiên cũ ra
    (WebSocket bị đóng code 1000). Hai bot cùng chạy sẽ đá nhau vô tận, cứ vài giây
    lại mất bàn và tạo bàn mới -> đúng triệu chứng "chơi được một lúc lại thoát ra".
    """
    try:
        import fcntl
        path = os.path.join(tempfile.gettempdir(), f"xiangqi_bot_{USER}.lock")
        f = open(path, "w")
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            print(f"[BOT] ❌ Đã có một bot khác đang chạy với tài khoản {USER} (khoá: {path}).")
            print("[BOT] Thoát để tránh hai phiên đá nhau. Hãy tắt bot kia trước.")
            sys.exit(1)
        f.write(str(os.getpid())); f.flush()
        atexit.register(lambda: (fcntl.flock(f, fcntl.LOCK_UN), f.close()))
        return f
    except ImportError:
        return None

if __name__ == "__main__":
    _lock = acquire_single_instance_lock()
    bot = PikafishBot()
    def signal_handler(sig, frame): bot.cleanup(); sys.exit(0)
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    try: bot.run()
    finally: bot.cleanup()
