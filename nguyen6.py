#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════════════════════════╗
║  BOT CARO EMBRYO - FULL NAME + AVATAR v3.0                     ║
║  Engine: Embryo Caro6 v1.2.3 (Linux Native)                    ║
║  FIX: Chỉ Ready khi đối thủ ngồi vào ghế, hủy khi đối thủ rời   ║
║  FIX: Cập nhật động khi có người vào/ra phòng xem             ║
║  FIX: Chạy bất đồng bộ http_login tránh nghẽn luồng WebSocket    ║
║  FIX: Sửa lỗi xung đột bộ đệm tiến trình con của AI            ║
╚══════════════════════════════════════════════════════════════════╝
"""
import subprocess, sys, os, importlib, urllib.request, json, time, struct
import re, logging, asyncio, random, threading, shutil, selectors, html as html_lib
from typing import List, Tuple, Dict, Optional
from pathlib import Path
from urllib.parse import urljoin

# ======================== LOGGING ========================
log = logging.getLogger("caro")
log.setLevel(logging.INFO)
if not log.handlers:
    h = logging.StreamHandler(sys.stdout)
    h.setFormatter(logging.Formatter("%(asctime)s %(message)s", datefmt="%H:%M:%S"))
    log.addHandler(h)

# ======================== SETUP & IMPORTS ========================
REQUIRED = ["websockets", "requests"]
for pkg in REQUIRED:
    try:
        importlib.import_module(pkg)
    except ImportError:
        print(f"[SETUP] Installing {pkg}...")
        subprocess.run([sys.executable, "-m", "pip", "install", pkg, "-q", "--break-system-packages"], stderr=subprocess.DEVNULL)
        importlib.import_module(pkg)

import websockets, requests

# ======================== SAFE IDENTITY CONFIG ========================
# Đây là TÊN ĐẦY ĐỦ (FULL_NAME), không phải tên đăng nhập.
ADJECTIVES = ["Pro", "Dark", "Light", "Shadow", "Ghost", "Fire", "Ice", "Thunder",
              "Silent", "Swift", "Crazy", "Lucky", "Mega", "Super", "Ultra", "Hyper",
              "Cyber", "Neo", "Tech", "Alpha", "Beta", "Zero", "Max", "King", "Queen"]
NOUNS = ["Caro", "Gomoku", "Master", "Storm", "Wolf", "Dragon", "Tiger", "Phoenix",
         "Ninja", "Samurai", "Wizard", "Knight", "Viper", "Hawk", "Eagle", "Fox"]

def generate_random_full_name() -> str:
    return f"{random.choice(ADJECTIVES)}{random.choice(NOUNS)}{random.randint(10, 999)}"

# ======================== ALPHA GOMOKU CONFIG ========================
try:
    _BASE_DIR = Path(__file__).parent
except NameError:
    _BASE_DIR = Path.cwd()

ENGINE_DIR = _BASE_DIR / "alphagomoku-engine"
AG_BINARY = "pbrain-katagomo_caro-15.exe"
AG_VERSION = "26"
AG_DOWNLOAD_URL = "http://download.gomocup.com/ai/KATAGOMO26.zip"
AG_RULE = 8  # Caro rule (Piskvork protocol)
AG_TIMEOUT = 2000  # 2 giây
AG_BOARD_SIZE = 15  # Katagomo chỉ hỗ trợ bàn vuông cố định

def auto_download_alphagomoku() -> Optional[str]:
    binary_path = ENGINE_DIR / AG_BINARY
    if binary_path.exists():
        try:
            binary_path.chmod(0o755)
        except Exception: pass
        return str(binary_path)
    log.info(f"[AG] Downloading Katagomo {AG_VERSION}...")
    ENGINE_DIR.mkdir(parents=True, exist_ok=True)
    try:
        import zipfile
        archive = Path("/tmp/katagomo26.zip")
        req = urllib.request.Request(AG_DOWNLOAD_URL, headers={
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"
        })
        with urllib.request.urlopen(req, timeout=120) as resp:
            archive.write_bytes(resp.read())
        with zipfile.ZipFile(archive, "r") as zf:
            zf.extractall(str(ENGINE_DIR))
        archive.unlink(missing_ok=True)
        for f in ENGINE_DIR.glob("*.exe"):
            try: f.chmod(0o755)
            except Exception: pass
        if binary_path.exists():
            return str(binary_path)
        for f in ENGINE_DIR.glob("pbrain-katagomo*.exe"):
            return str(f)
        return None
    except Exception as e:
        log.error(f"[AG] Download failed: {e}")
        return None

def detect_ag_binary() -> Optional[str]:
    if not ENGINE_DIR.exists(): return None
    # Katagomo 26: ưu tiên caro-15 vì gamevh.net chơi luật Caro
    for f in ENGINE_DIR.glob("pbrain-katagomo_caro-15.exe"):
        try: f.chmod(0o755)
        except Exception: pass
        return str(f)
    for f in ENGINE_DIR.glob("pbrain-katagomo_*-15.exe"):
        try: f.chmod(0o755)
        except Exception: pass
        return str(f)
    for f in ENGINE_DIR.glob("pbrain-katagomo*.exe"):
        try: f.chmod(0o755)
        except Exception: pass
        return str(f)
    return None



# ======================== COORDINATE SHIFTER (15×19 -> 15×15) ========================
class CoordinateShifter:
    """
    Dịch chuyển viewport 15×15 trên bàn 15×19 để engine Katagomo hiểu được.
    real: 15×19, engine: 15×15, offset_y ∈ [0, 4].
    """
    def __init__(self, real_w=15, real_h=19, engine_size=15):
        self.real_w = real_w
        self.real_h = real_h
        self.engine_size = engine_size
        self.max_offset = real_h - engine_size  # 4
        self.offset_y = 0
        self._last_history_len = 0

    def to_engine(self, x: int, y: int) -> Optional[Tuple[int, int]]:
        ey = y - self.offset_y
        if 0 <= x < self.engine_size and 0 <= ey < self.engine_size:
            return (x, ey)
        return None

    def from_engine(self, x: int, y: int) -> Tuple[int, int]:
        return (x, y + self.offset_y)

    def filter_history(self, history: list) -> list:
        """Lọc và dịch các nước nằm trong viewport hiện tại."""
        result = []
        for x, y, sym in history:
            eng = self.to_engine(x, y)
            if eng:
                result.append((eng[0], eng[1], sym))
        return result

    def compute_offset(self, history: list, my_side: int, opp_side: int) -> int:
        """
        Chọn offset_y tối ưu.
        - Ưu tiên viewport chứa nước gần đây nhất.
        - Nếu có nước thắng/chặn ngoài viewport -> buộc đổi.
        - Nếu bounding box ≤ 15 -> căn giữa.
        - Ngược lại -> chọn viewport chứa nhiều quân nhất (ưu tiên quân gần đây).
        """
        if not history:
            return 2  # Mặc định căn giữa 19 hàng: offset=2 (hàng 2..16)

        occupied_y = [y for _, y, _ in history]
        min_y, max_y = min(occupied_y), max(occupied_y)

        # Nếu tất cả nằm trong 15 hàng liên tiếp -> căn giữa vùng đó
        if max_y - min_y < self.engine_size:
            # Căn giữa sao cho min_y càng gần đầu viewport càng tốt
            ideal = max(0, min(min_y, self.max_offset))
            # Điều chỉnh để max_y nằm trong viewport
            while ideal > 0 and max_y - ideal >= self.engine_size:
                ideal -= 1
            while ideal < self.max_offset and min_y - ideal < 0:
                ideal += 1
            return ideal

        # Ván đã lan rộng >15 hàng -> chọn viewport tối ưu
        best_k = self.offset_y
        best_score = -1

        for k in range(self.max_offset + 1):
            score = 0
            for i, (x, y, sym) in enumerate(history):
                ey = y - k
                if 0 <= ey < self.engine_size:
                    # Quân gần đây có trọng số cao hơn
                    weight = (i + 1) ** 2
                    score += weight
            if score > best_score:
                best_score = score
                best_k = k

        # Kiểm tra xem nước gần nhất có nằm trong viewport không
        last_y = history[-1][1]
        if not (self.offset_y <= last_y < self.offset_y + self.engine_size):
            # Nước gần nhất nằm ngoài viewport hiện tại -> buộc đổi
            for k in range(self.max_offset + 1):
                if k <= last_y < k + self.engine_size:
                    return k
            # Không tìm được (không thể xảy ra vì max_offset=4 và last_y≤18)
            return best_k

        return best_k

    def needs_rebuild(self, history: list) -> bool:
        """Kiểm tra xem viewport hiện tại có chứa nước gần nhất không."""
        if not history:
            return False
        last_x, last_y, _ = history[-1]
        eng = self.to_engine(last_x, last_y)
        return eng is None

# ======================== LOCAL FALLBACK ENGINE (15×19) ========================
class LocalCaroEngine:
    """Engine đơn giản bằng Python để xử lý bàn 15×19 khi Katagomo (15×15) không đủ."""

    DIRECTIONS = [(1, 0), (0, 1), (1, 1), (1, -1)]

    def __init__(self, width=15, height=19):
        self.width = width
        self.height = height

    def _in_bounds(self, x, y):
        return 0 <= x < self.width and 0 <= y < self.height

    def _count_line(self, grid, x, y, dx, dy, symbol):
        """Đếm số quân liên tiếp từ (x,y) theo hướng (dx,dy), bao gồm (x,y)."""
        count = 0
        cx, cy = x, y
        while self._in_bounds(cx, cy) and grid[cy][cx] == symbol:
            count += 1
            cx += dx
            cy += dy
        # Kiểm tra ô trống ở cuối
        open_end = self._in_bounds(cx, cy) and grid[cy][cx] == EMPTY
        return count, open_end

    def find_winning_move(self, grid, symbol):
        """Tìm nước đi thắng ngay (tạo 5 liên tiếp)."""
        for y in range(self.height):
            for x in range(self.width):
                if grid[y][x] != EMPTY:
                    continue
                for dx, dy in self.DIRECTIONS:
                    # Đếm 2 phía
                    c1, _ = self._count_line(grid, x + dx, y + dy, dx, dy, symbol)
                    c2, _ = self._count_line(grid, x - dx, y - dy, -dx, -dy, symbol)
                    if c1 + c2 + 1 >= 5:
                        return (x, y)
        return None

    def find_blocking_move(self, grid, my_symbol, opp_symbol):
        """Tìm nước chặn đối thủ thắng (đối thủ có 4 mở hoặc 4 chặn 1 đầu)."""
        best = None
        best_score = 0
        for y in range(self.height):
            for x in range(self.width):
                if grid[y][x] != EMPTY:
                    continue
                score = 0
                for dx, dy in self.DIRECTIONS:
                    c1, open1 = self._count_line(grid, x + dx, y + dy, dx, dy, opp_symbol)
                    c2, open2 = self._count_line(grid, x - dx, y - dy, -dx, -dy, opp_symbol)
                    total = c1 + c2
                    # Đối thủ có 4 mở 2 đầu -> chặn ngay (ưu tiên cao nhất)
                    if total >= 4 and open1 and open2:
                        return (x, y)  # chặn ngay lập tức
                    # Đối thủ có 4 chặt 1 đầu -> cũng cần chặn
                    if total >= 4 and (open1 or open2):
                        score += 1000
                    # Đối thủ có 3 mở 2 đầu -> điểm cao
                    if total == 3 and open1 and open2:
                        score += 100
                    elif total == 3 and (open1 or open2):
                        score += 10
                    # Tạo 4 cho mình
                    mc1, mopen1 = self._count_line(grid, x + dx, y + dy, dx, dy, my_symbol)
                    mc2, mopen2 = self._count_line(grid, x - dx, y - dy, -dx, -dy, my_symbol)
                    mtotal = mc1 + mc2
                    if mtotal >= 4 and (mopen1 or mopen2):
                        score += 500
                    if mtotal == 3 and mopen1 and mopen2:
                        score += 50
                if score > best_score:
                    best_score = score
                    best = (x, y)
        return best

    def get_move(self, board_history: list, my_side: int, board_width: int, board_height: int) -> Optional[Tuple[int, int]]:
        """Trả về nước đi tốt nhất từ engine local."""
        self.width = board_width
        self.height = board_height
        grid = [[EMPTY] * board_width for _ in range(board_height)]
        for x, y, sym in board_history:
            if 0 <= x < board_width and 0 <= y < board_height:
                grid[y][x] = sym

        opp = CROSS if my_side == CIRCLE else CIRCLE

        # 1. Thắng ngay
        win = self.find_winning_move(grid, my_side)
        if win:
            log.info(f"[Local] Win-in-1: {win}")
            return win

        # 2. Chặn thua
        block = self.find_blocking_move(grid, my_side, opp)
        if block:
            log.info(f"[Local] Block: {block}")
            return block

        # 3. Đánh gần nước cuối cùng hoặc center
        if board_history:
            lx, ly = board_history[-1][0], board_history[-1][1]
            # Tìm ô trống gần nhất
            for r in range(1, max(board_width, board_height)):
                for dx in range(-r, r + 1):
                    for dy in range(-r, r + 1):
                        tx, ty = lx + dx, ly + dy
                        if self._in_bounds(tx, ty) and grid[ty][tx] == EMPTY:
                            return (tx, ty)
        # 4. Center
        return (board_width // 2, board_height // 2)

# ======================== ENGINE WRAPPER ========================
class AlphaGomokuEngine:
    def __init__(self, timeout_turn=2000, board_size=15, rule=8):
        self.binary = detect_ag_binary()
        self.timeout_turn = timeout_turn
        self.board_size = board_size
        self.rule = rule
        self.proc = None
        self.lock = threading.RLock()
        self._buffer = b""
        self.my_side = 1
        self._initialized = False
        self._katagomo_warned = False
        self.shifter = None

    def _send(self, cmd: str):
        with self.lock:
            if self.proc and self.proc.poll() is None:
                try:
                    self.proc.stdin.write((cmd + "\n").encode("utf-8"))
                    self.proc.stdin.flush()
                except Exception:
                    pass

    def _read_line(self, timeout=10.0) -> str:
        with self.lock:
            if not self.proc or self.proc.poll() is None:
                return ""
            deadline = time.monotonic() + timeout
            while True:
                idx = self._buffer.find(b"\n")
                if idx >= 0:
                    line_bytes = self._buffer[:idx].strip()
                    self._buffer = self._buffer[idx + 1:]
                    return line_bytes.decode("utf-8", errors="replace")
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return ""
                try:
                    import select
                    rlist, _, _ = select.select([self.proc.stdout], [], [], min(remaining, 0.5))
                    if rlist:
                        chunk = os.read(self.proc.stdout.fileno(), 4096)
                        if not chunk:
                            return ""
                        self._buffer += chunk
                except Exception:
                    return ""

    def start_game(self, my_symbol=1) -> bool:
        with self.lock:
            self._synced = False
            self._buffer = b""
            if self.proc and self.proc.poll() is None:
                self._send("RESTART")
                for _ in range(5):
                    if self._read_line(timeout=0.5).upper() == "OK":
                        break
                self._send(f"INFO rule {self.rule}")
                self._send(f"INFO timeout_turn {self.timeout_turn}")
                self._send("INFO ponder 1")
                # Katagomo không hỗ trợ RECTSTART, dùng START với board_size
                self._send(f"START {self.board_size}")
                for _ in range(5):
                    if self._read_line(timeout=0.5).upper() == "OK":
                        break
                self.my_side = my_symbol
                self._initialized = True
                return True
            self.stop()
            if not self.binary:
                return False
            try:
                is_exe = self.binary.lower().endswith('.exe')
                cmd = ["wine", self.binary] if is_exe else [self.binary]
                env = os.environ.copy()
                env["WINEDEBUG"] = "-all"
                self.proc = subprocess.Popen(
                    cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL, cwd=str(ENGINE_DIR),
                    env=env, bufsize=0
                )
                self._buffer = b""
                self.my_side = my_symbol
                self._send(f"INFO rule {self.rule}")
                self._send(f"INFO timeout_turn {self.timeout_turn}")
                self._send("INFO ponder 1")
                self._send(f"START {self.board_size}")
                for _ in range(10):
                    line = self._read_line(timeout=1.0)
                    if line.upper() == "OK":
                        break
                self._initialized = True
                return True
            except Exception as e:
                log.error(f"[AG] Start error: {e}")
                self._initialized = False
                return False

    def restart_game(self) -> bool:
        with self.lock:
            if not self._initialized or not self.proc or self.proc.poll() is not None:
                return False
            self._buffer = b""
            self._send("RESTART")
            for _ in range(5):
                line = self._read_line(timeout=2.0)
                if line.upper() == "OK":
                    log.info("[AG] RESTART successful")
                    self._send(f"INFO rule {self.rule}")
                    self._send(f"INFO timeout_turn {self.timeout_turn}")
                    return True
            # Nếu RESTART fail, thử START lại
            self._send(f"START {self.board_size}")
            for _ in range(5):
                line = self._read_line(timeout=2.0)
                if line.upper() == "OK":
                    return True
            return True

    def get_move(self, board_history: list, my_side: int, board_width=15, board_height=19,
                 shifter: Optional['CoordinateShifter'] = None) -> Optional[Tuple[int, int]]:
        with self.lock:
            try:
                if not self._initialized or not self.proc or self.proc.poll() is not None:
                    return None
                self.shifter = shifter
                if board_height > self.board_size and not self._katagomo_warned:
                    log.info(f"[AG] Katagomo viewport={self.board_size}x{self.board_size} on server {board_width}x{board_height}. "
                             f"Using coordinate shifter (offset_y={shifter.offset_y if shifter else 0}).")
                    self._katagomo_warned = True
                self._send(f"INFO timeout_turn {self.timeout_turn}")
                self._send(f"INFO time_left {self.timeout_turn * 20}")

                if shifter:
                    filtered_history = shifter.filter_history(board_history)
                    # Nếu viewport đổi (nước gần nhất nằm ngoài viewport cũ) -> phải gửi BOARD
                    needs_board = (not getattr(self, '_synced', False)
                                   or len(filtered_history) != getattr(self, '_expected_history_len', -1) + 1
                                   or shifter.needs_rebuild(board_history))
                else:
                    filtered_history = [(x, y, sym) for (x, y, sym) in board_history
                                        if 0 <= x < self.board_size and 0 <= y < self.board_size]
                    needs_board = (not getattr(self, '_synced', False)
                                   or len(filtered_history) != getattr(self, '_expected_history_len', -1) + 1
                                   or len(board_history) != len(filtered_history))

                if not needs_board and filtered_history:
                    last_x, last_y, _ = filtered_history[-1]
                    self._send(f"TURN {last_x},{last_y}")
                else:
                    self._send("BOARD")
                    for (x, y, sym) in filtered_history:
                        c = 1 if sym == self.my_side else 2
                        self._send(f"{x},{y},{c}")
                    self._send("DONE")

                for _ in range(300):
                    line = self._read_line(timeout=1)
                    if not line:
                        continue
                    if line.startswith("MESSAGE") or line.startswith("ERROR") or line.startswith("DEBUG"):
                        continue
                    if "," in line:
                        parts = line.split(",")
                        if len(parts) == 2:
                            self._synced = True
                            self._expected_history_len = len(filtered_history) + 1
                            mx, my = int(parts[0].strip()), int(parts[1].strip())
                            # Dịch ngược từ engine về bàn thực
                            if shifter:
                                mx, my = shifter.from_engine(mx, my)
                            # Clamp vào biên bàn thực
                            mx = max(0, min(mx, board_width - 1))
                            my = max(0, min(my, board_height - 1))
                            return mx, my
                return None
            except Exception as e:
                log.warning(f"[AG] get_move error: {e}")
                self._synced = False
                return None

    def stop(self):
        with self.lock:
            if self.proc:
                try:
                    self._send("END")
                except Exception:
                    pass
                try:
                    self.proc.terminate()
                    self.proc.wait(2)
                except Exception:
                    try:
                        self.proc.kill()
                    except Exception:
                        pass
                self.proc = None
            self._initialized = False
            self._buffer = b""

# ======================== CONSTANTS & CONFIG ========================
WS_URL = "wss://gamevh.net/ws/gameServer"
GAME_URL = "https://gamevh.net/play/caro/0"
# === CẤU HÌNH TRỰC TIẾP - KHÔNG CẦN SECRETS ===
# Đã hardcode theo yêu cầu - ai xem repo sẽ thấy mật khẩu
CARO_USER_DIRECT = "nguyen6"
CARO_PASSWD_DIRECT = "nhat123456"
# Ưu tiên Secrets nếu có, fallback về hardcode
USER = os.environ.get("CARO_USER1") or os.environ.get("CARO_USER") or CARO_USER_DIRECT
PASSWD = os.environ.get("CARO_PASSWD1") or os.environ.get("CARO_PASSWD") or CARO_PASSWD_DIRECT
# Nếu muốn chỉ dùng hardcode:
# USER = "nguyen3"
# PASSWD = "nhat123456"

VERSION = "5.0.2"
GAME_ID = "caro"
RUNTIME = int(os.environ.get("CARO_RUNTIME_SECONDS") or
              float(os.environ.get("CARO_RUNTIME_HOURS", "5.9")) * 3600)
AUTO_IDENTITY = os.environ.get("CARO_AUTO_IDENTITY", "1") == "1"
IDENTITY_TEST_ONLY = os.environ.get("CARO_IDENTITY_TEST_ONLY", "0") == "1"
BOT_BET_XU = 1000
BOT_MATCH_DURATION = '0'
BOT_TURN_DURATION = '60'
EMPTY = -1
CIRCLE = 0
CROSS = 1

CMD_MAP = {
    300: "PONG", 301: "PING", 302: "LOGIN", 303: "ALERT", 304: "RIBBON_MESSAGE",
    311: "BROADCAST", 312: "INVITE", 314: "SET_CLIENT_MODE", 315: "CONFIG",
    401: "ENTER_PLACE", 402: "ENTER_CHILD_PLACE", 405: "CREATE_RULE",
    406: "PLAYER_ENTERED", 407: "PLAYER_EXITED", 410: "KICK_PLAYER",
    413: "LIST_BET_AMT", 414: "GET_TABLE_DATA", 417: "START_MATCH",
    418: "GAMEOVER", 419: "ENTER_STATE", 420: "SET_TURN",
    421: "SET_PLAYER_STATUS", 422: "SET_PLAYER_POINT", 423: "SET_PLAYER_ATTR",
    431: "BALANCE_CHANGED", 432: "OWNER_CHANGED", 433: "GET_TABLE_DATA_EX",
    434: "SET_READY", 501: "BET", 502: "PLAY", 505: "CHAT", 518: "HIGHLIGHT",
    529: "MOVE", 533: "ASK_DRAW", 534: "SURRENDER", 535: "RETREAT",
}

# ======================== BINARY PROTOCOL ========================
class BinaryReader:
    def __init__(self, data: bytes):
        self.data = data; self.pos = 0
    def remaining(self) -> int: return len(self.data) - self.pos
    def u8(self) -> int:
        if self.pos >= len(self.data): return 0
        v = self.data[self.pos]; self.pos += 1; return v
    def i8(self) -> int:
        if self.pos >= len(self.data): return 0
        v = struct.unpack_from('>b', self.data, self.pos)[0]; self.pos += 1; return v
    def i16(self) -> int:
        if self.pos + 2 > len(self.data): return 0
        v = struct.unpack_from('>h', self.data, self.pos)[0]; self.pos += 2; return v
    def u16(self) -> int:
        if self.pos + 2 > len(self.data): return 0
        v = struct.unpack_from('>H', self.data, self.pos)[0]; self.pos += 2; return v
    def i32(self) -> int:
        if self.pos + 4 > len(self.data): return 0
        v = struct.unpack_from('>i', self.data, self.pos)[0]; self.pos += 4; return v
    def i64(self) -> int:
        if self.pos + 8 > len(self.data): return 0
        hi = struct.unpack_from('>i', self.data, self.pos)[0]
        lo = struct.unpack_from('>I', self.data, self.pos + 4)[0]
        self.pos += 8; return (hi << 32) + lo
    def read_ascii(self) -> str:
        if self.pos >= len(self.data): return ""
        n = self.u8()
        if self.pos + n > len(self.data): n = len(self.data) - self.pos
        s = self.data[self.pos:self.pos + n].decode('ascii', 'replace')
        self.pos += n; return s
    def read_utf(self) -> str:
        if self.pos + 2 > len(self.data): return ""
        n = self.i16()
        if n <= 0: return ""
        byte_len = n * 2
        if self.pos + byte_len > len(self.data): byte_len = len(self.data) - self.pos
        s = self.data[self.pos:self.pos + byte_len].decode('utf-16-be', 'replace')
        self.pos += byte_len; return s
    def read_bytes(self) -> List[int]:
        if self.pos + 2 > len(self.data): return []
        n = self.i16()
        if self.pos + n > len(self.data): n = len(self.data) - self.pos
        result = list(self.data[self.pos:self.pos + n])
        self.pos += n; return result
    def read_command(self) -> str:
        first = self.i8()
        if first < 0:
            n = -first
            if self.pos + n > len(self.data): n = len(self.data) - self.pos
            s = self.data[self.pos:self.pos + n].decode('ascii', 'replace')
            self.pos += n; return s
        second = self.u8()
        cmd_id = (first << 8) | second
        return CMD_MAP.get(cmd_id, f"CMD_{cmd_id}")

class BinaryWriter:
    def __init__(self): self.parts = []
    def u8(self, v: int): self.parts.append(struct.pack('>B', v))
    def i8(self, v: int): self.parts.append(struct.pack('>b', v))
    def i16(self, v: int): self.parts.append(struct.pack('>h', v))
    def i32(self, v: int): self.parts.append(struct.pack('>i', v))
    def write_ascii(self, s: str):
        encoded = s.encode('ascii', 'replace'); self.u8(len(encoded)); self.parts.append(encoded)
    def write_utf(self, s: str):
        encoded = s.encode('utf-16-be'); self.i16(len(encoded) // 2); self.parts.append(encoded)
    def write_command(self, cmd: str):
        cmd_id = next((k for k, v in CMD_MAP.items() if v == cmd), None)
        if cmd_id: self.parts.append(struct.pack('>H', cmd_id))
        else:
            b = cmd.encode('ascii'); self.i8(-len(b)); self.parts.append(b)
    def build(self) -> bytes: return b''.join(self.parts)

# ======================== BOARD ========================
class Board:
    def __init__(self, width: int = 15, height: int = 19):
        self.width = width; self.height = height
        self.grid = [[EMPTY] * width for _ in range(height)]
        self.history = []; self.placed = set()

    def resize(self, width: int, height: int):
        self.width = width; self.height = height
        self.grid = [[EMPTY] * width for _ in range(height)]
        self.history.clear(); self.placed.clear()

    def get(self, x: int, y: int) -> int:
        if 0 <= x < self.width and 0 <= y < self.height: return self.grid[y][x]
        return EMPTY

    def put(self, x: int, y: int, symbol: int):
        if self.get(x, y) == EMPTY and 0 <= x < self.width and 0 <= y < self.height:
            self.grid[y][x] = symbol; self.history.append((x, y, symbol)); self.placed.add((x, y))

    def undo(self, x: int, y: int):
        if 0 <= x < self.width and 0 <= y < self.height:
            self.grid[y][x] = EMPTY
            if self.history and self.history[-1][:2] == (x, y): self.history.pop()
            self.placed.discard((x, y))

    def xy_to_pos(self, x: int, y: int) -> int: return y * self.width + x
    def pos_to_xy(self, pos: int) -> tuple: return pos % self.width, pos // self.width

    def load_rle(self, data: List[int]):
        self.grid = [[EMPTY] * self.width for _ in range(self.height)]
        self.history.clear(); self.placed.clear()
        pos = 0
        for value in data:
            symbol = value - 256 if value > 127 else value
            if symbol >= 0:
                y, x = pos // self.width, pos % self.width
                if 0 <= x < self.width and 0 <= y < self.height:
                    self.grid[y][x] = symbol; self.placed.add((x, y))
                pos += 1
            else: pos += -symbol
        for y in range(self.height):
            for x in range(self.width):
                s = self.grid[y][x]
                if s >= 0: self.history.append((x, y, s))

    def get_empty_near_center(self) -> tuple:
        cx, cy = self.width // 2, self.height // 2
        for r in range(max(self.width, self.height)):
            for dx in range(-r, r + 1):
                for dy in range(-r, r + 1):
                    x, y = cx + dx, cy + dy
                    if 0 <= x < self.width and 0 <= y < self.height and self.grid[y][x] == EMPTY:
                        return (x, y)
        return (0, 0)

    def get_empty_near(self, x0: int, y0: int) -> tuple:
        for r in range(10):
            for dx in range(-r, r + 1):
                for dy in range(-r, r + 1):
                    x, y = x0 + dx, y0 + dy
                    if 0 <= x < self.width and 0 <= y < self.height:
                        if self.grid[y][x] == EMPTY:
                            return (x, y)
        return self.get_empty_near_center()

# ======================== BOT ========================
class CaroBot:
    def __init__(self):
        self.ws = None; self.board = Board(width=15, height=19)
        self.slot = -1; self.my_symbol = CROSS; self.opponent_symbol = CIRCLE
        self.is_playing = False; self.in_table = False; self.ready = False
        self.players = {}; self.nickname = ""; self.token = 0; self.cookie = ""
        self.place_path = "Lobby.caro.0"; self.lock_key = ""
        self.start_time = None; self.last_activity = time.time(); self._running = True
        self.wins = 0; self.losses = 0; self.draws = 0; self.total_games = 0
        self.pending_move = False
        self.bet_amts = []; self._resolved_bet_id = None
        self._bet_amts_loaded = False; self._joining_table = False
        
        self.ag = None; self.ag_available = False
        self.ag_moves = 0; self.ag_errors = 0; self.ag_fallback_count = 0
        self._moving = False; self._last_move_xy = None
        self.local_engine = LocalCaroEngine(width=15, height=19)
        self.shifter = None
        
        self.table_id = None
        self.player_slot_by_id = {}
        self.opponent_gone_at = None
        self._table_lost_at = None
        self._want_rejoin = False; self._rejoining = False; self._rejoin_attempts = 0

        # Chỉ cập nhật FULL_NAME/avatar một lần mỗi lần khởi động tiến trình.
        self._identity_attempted = False
        self.identity_result = {}

    def init_ag(self):
        if self.ag is not None: return self.ag_available
        binary = detect_ag_binary()
        if not binary:
            binary = auto_download_alphagomoku()
        if not binary:
            log.warning("[AG] No binary found!")
            self.ag_available = False
            return False
        try:
            self.ag = AlphaGomokuEngine(timeout_turn=AG_TIMEOUT, board_size=AG_BOARD_SIZE, rule=AG_RULE)
            self.ag.binary = binary
            ok = self.ag.start_game(my_symbol=self.my_symbol)
            if ok:
                self.ag_available = True
                log.info(f"[AG] Katagomo v{AG_VERSION} OK! Rule={AG_RULE}, Board={AG_BOARD_SIZE}x{AG_BOARD_SIZE}")
            else:
                self.ag_available = False
                log.warning("[AG] Start failed!")
            return self.ag_available
        except Exception as e:
            log.error(f"[AG] Init error: {e}")
            self.ag_available = False
            return False

    @property
    def running(self) -> bool: return self._running

    def stop(self):
        self._running = False
        if self.ag: self.ag.stop(); self.ag = None; self.ag_available = False

    def save_stats(self):
        try:
            with open("/tmp/caro_ag_stats.json", "w") as f:
                json.dump({'W': self.wins, 'L': self.losses, 'D': self.draws, 'G': self.total_games}, f)
        except Exception: pass

    def update_symbols(self):
        self.my_symbol = CIRCLE if self.slot == 0 else CROSS
        self.opponent_symbol = CROSS if self.my_symbol == CIRCLE else CIRCLE
        log.info(f"Slot={self.slot} Me={'X' if self.my_symbol == CROSS else 'O'}")

    def make_login(self) -> bytes:
        w = BinaryWriter(); w.write_command("LOGIN"); w.write_ascii(self.nickname)
        w.i32(self.token); w.write_ascii(VERSION); w.write_ascii(self.lock_key)
        w.write_ascii(GAME_ID); w.i8(1); return w.build()

    def make_enter(self, path: str, pw: str = "", mode: int = 1) -> bytes:
        w = BinaryWriter(); w.write_command("ENTER_PLACE"); w.write_ascii(path)
        w.write_utf(pw); w.i8(mode); return w.build()

    def make_list_bet_amt(self) -> bytes:
        w = BinaryWriter(); w.write_command("LIST_BET_AMT"); return w.build()

    def resolve_bet_amt_id(self) -> Optional[int]:
        if not self.bet_amts: return None
        for ba in self.bet_amts:
            if ba['value'] == BOT_BET_XU: return ba['id']
        lower = [ba for ba in self.bet_amts if 0 < ba['value'] <= BOT_BET_XU]
        if lower: return max(lower, key=lambda x: x['value'])['id']
        return 0

    def make_create_rule(self) -> bytes:
        bet_amt_id = self._resolved_bet_id if self._resolved_bet_id is not None else self.resolve_bet_amt_id()
        if bet_amt_id is None: bet_amt_id = 0
        args = [("matchDuration", BOT_MATCH_DURATION), ("turnDuration", BOT_TURN_DURATION),
                ("accDuration", "0"), ("blockSoftware", "0")]
        w = BinaryWriter(); w.write_command("CREATE_RULE"); w.i8(bet_amt_id); w.i8(len(args))
        for name, val in args: w.write_ascii(name); w.write_utf(val)
        return w.build()

    def make_get_table(self) -> bytes:
        w = BinaryWriter(); w.write_command("GET_TABLE_DATA_EX"); w.write_ascii(""); return w.build()

    def make_play(self, pos: int) -> bytes:
        w = BinaryWriter(); w.write_command("PLAY"); w.i16(pos); return w.build()

    def make_pong(self) -> bytes:
        w = BinaryWriter(); w.write_command("PONG"); return w.build()

    def make_ready(self) -> bytes:
        if self.is_playing: return b''
        w = BinaryWriter(); w.write_command("SET_READY"); return w.build()

    async def send(self, data: bytes):
        if self.ws and data:
            try: await self.ws.send(data)
            except Exception: pass

    async def create_new_table(self):
        if not self._bet_amts_loaded:
            self._bet_amts_loaded = False
            await self.send(self.make_list_bet_amt())
        else:
            await self.send(self.make_create_rule())

    async def do_move(self):
        if not self.is_playing or not self.running or self.slot < 0: return
        if self._moving:
            log.warning("[BOT] do_move đang chạy -> bỏ qua")
            return
        self._moving = True
        self.pending_move = False
        self._last_move_xy = None
        try:
            start = time.time()
            x, y = -1, -1
            source = "unknown"
            history = list(self.board.history)
            bw, bh = self.board.width, self.board.height
            opp = CROSS if self.my_symbol == CIRCLE else CIRCLE

            # === TẦNG 1: Local Win/Block (toàn bàn 15×19) ===
            local_move = self.local_engine.get_move(history, self.my_symbol, bw, bh)

            # Kiểm tra xem local_move có phải win-in-1 hoặc block không
            local_is_critical = False
            if local_move:
                tx, ty = local_move
                # Kiểm tra nếu đánh local_move sẽ thắng
                test_grid = [row[:] for row in self.board.grid]
                if 0 <= tx < bw and 0 <= ty < bh and test_grid[ty][tx] == EMPTY:
                    test_grid[ty][tx] = self.my_symbol
                    # Đếm 4 hướng
                    for dx, dy in [(1,0),(0,1),(1,1),(1,-1)]:
                        c = 1
                        for s in [1, -1]:
                            cx, cy = tx + dx*s, ty + dy*s
                            while 0 <= cx < bw and 0 <= cy < bh and test_grid[cy][cx] == self.my_symbol:
                                c += 1
                                cx += dx*s
                                cy += dy*s
                        if c >= 5:
                            local_is_critical = True
                            break
                    if not local_is_critical:
                        # Kiểm tra block: đối thủ có 4 mở hoặc 4 chặn 1 đầu không
                        for dx, dy in [(1,0),(0,1),(1,1),(1,-1)]:
                            c = 0
                            open_ends = 0
                            for s in [1, -1]:
                                cx, cy = tx + dx*s, ty + dy*s
                                while 0 <= cx < bw and 0 <= cy < bh and self.board.grid[cy][cx] == opp:
                                    c += 1
                                    cx += dx*s
                                    cy += dy*s
                                if 0 <= cx < bw and 0 <= cy < bh and self.board.grid[cy][cx] == EMPTY:
                                    open_ends += 1
                            if c >= 4 and open_ends >= 1:
                                local_is_critical = True
                                break

            # === TẦNG 2: Katagomo với Coordinate Shifter ===
            katagomo_usable = False
            if self.ag_available:
                # Tạo shifter nếu chưa có
                if self.shifter is None:
                    self.shifter = CoordinateShifter(real_w=bw, real_h=bh, engine_size=AG_BOARD_SIZE)
                # Tính offset tối ưu
                new_offset = self.shifter.compute_offset(history, self.my_symbol, opp)
                if new_offset != self.shifter.offset_y:
                    log.info(f"[Shifter] Viewport offset_y: {self.shifter.offset_y} -> {new_offset}")
                    self.shifter.offset_y = new_offset
                    # Offset đổi -> engine cần rebuild (BOARD thay vì TURN)
                    if self.ag:
                        self.ag._synced = False

                # Kiểm tra xem viewport có chứa nước gần nhất không
                if not self.shifter.needs_rebuild(history):
                    katagomo_usable = True
                else:
                    # Thử đổi offset để chứa nước gần nhất
                    last_x, last_y, _ = history[-1]
                    for k in range(self.shifter.max_offset + 1):
                        if k <= last_y < k + AG_BOARD_SIZE:
                            self.shifter.offset_y = k
                            katagomo_usable = True
                            log.info(f"[Shifter] Emergency shift to offset_y={k} to cover last move ({last_x},{last_y})")
                            if self.ag:
                                self.ag._synced = False
                            break

                # Nếu ván đã lan rộng quá 15 hàng (bounding box > 15) -> Katagomo không đủ
                if history:
                    ys = [y for _, y, _ in history]
                    if max(ys) - min(ys) >= AG_BOARD_SIZE:
                        katagomo_usable = False
                        log.info("[Shifter] Board span >= 15 rows -> disabling Katagomo, using local engine")

            # Gọi Katagomo nếu usable
            if katagomo_usable:
                try:
                    move = await asyncio.get_event_loop().run_in_executor(
                        None,
                        lambda: self.ag.get_move(history, self.my_symbol, bw, bh, self.shifter)
                    )
                    if (move and 0 <= move[0] < bw and 0 <= move[1] < bh
                        and self.board.get(*move) == EMPTY):
                        x, y = move
                        source = f"katagomo(oy={self.shifter.offset_y})"
                        self.ag_moves += 1
                    else:
                        self.ag_errors += 1
                        log.warning(f"[AG] Invalid move: {move}, fallback local")
                        x, y = local_move if local_move else self.board.get_empty_near_center()
                        source = "local_fallback"
                        self.ag_fallback_count += 1
                        try:
                            self.ag.start_game(my_symbol=self.my_symbol)
                            self.shifter = None
                        except Exception:
                            pass
                except Exception as e:
                    self.ag_errors += 1
                    log.warning(f"[AG] Error: {e}")
                    try:
                        self.ag.stop()
                        self.ag = None
                        self.ag_available = False
                    except Exception:
                        pass
                    x, y = local_move if local_move else self.board.get_empty_near_center()
                    source = "local_fallback"
                    self.ag_fallback_count += 1
            else:
                # Katagomo không usable -> dùng local hoàn toàn
                x, y = local_move if local_move else self.board.get_empty_near_center()
                source = "local"

            # Nếu local tìm được critical (win/block) mà Katagomo trả về nước khác -> ưu tiên local
            if local_is_critical and local_move and (x, y) != local_move:
                log.info(f"[Pipeline] Local found critical {local_move}, overriding Katagomo {x,y}")
                x, y = local_move
                source = "local_override"

            # Final safety check
            if not (0 <= x < bw and 0 <= y < bh) or self.board.get(x, y) != EMPTY:
                x, y = self.board.get_empty_near_center()
                source = "center_fallback"

            elapsed = time.time() - start
            pos = self.board.xy_to_pos(x, y)
            log.info(f"MOVE ({x},{y}) took {elapsed:.2f}s [{source}]")
            await self.send(self.make_play(pos))
            self._last_move_xy = (x, y)
            self.board.put(x, y, self.my_symbol)
        finally:
            self._moving = False


    async def handle(self, raw: bytes):
        r = BinaryReader(raw)
        cmd = r.read_command()
        if cmd != "PING": log.info(f"RECV {cmd}")
        self.last_activity = time.time()
        try:
            if cmd == "PING": await self.send(self.make_pong())
            elif cmd == "LOGIN": await self.handle_login(r)
            elif cmd == "ENTER_PLACE": await self.handle_enter(r)
            elif cmd == "LIST_BET_AMT": await self.handle_list_bet_amt(r)
            elif cmd == "CREATE_RULE": await self.handle_create_rule(r)
            elif cmd == "GET_TABLE_DATA_EX": await self.handle_table(r)
            elif cmd == "START_MATCH": await self.handle_start(r)
            elif cmd == "SET_TURN": await self.handle_turn(r)
            elif cmd == "MOVE": await self.handle_move(r)
            elif cmd == "GAMEOVER": await self.handle_gameover(r)
            elif cmd == "PLAY": await self.handle_play(r)
            elif cmd == "KICK_PLAYER": await self.handle_kick(r)
            elif cmd == "PLAYER_ENTERED": await self.handle_player_enter(r)
            elif cmd == "PLAYER_EXITED": await self.handle_player_exit(r)
        except Exception as e: log.error(f"Error {cmd}: {e}", exc_info=True)

    async def handle_login(self, r: BinaryReader):
        status = r.i8()
        if status == 0:
            path = r.read_utf()
            if path == "REFRESH":
                login_ok = await asyncio.get_event_loop().run_in_executor(None, self.http_login)
                if login_ok: await self.send(self.make_login())
                return
            if r.remaining() > 0: self.lock_key = r.read_ascii()
            await self.send(self.make_enter(self.place_path))
        else:
            log.error(f"LOGIN failed")

    async def handle_enter(self, r: BinaryReader):
        status = r.i8()
        if status == 0:
            if self._joining_table:
                self._joining_table = False; self._rejoining = False
                self.in_table = True
                await asyncio.sleep(0.3); await self.send(self.make_get_table())
            elif not self.in_table:
                if self._want_rejoin and self.table_id:
                    self._want_rejoin = False; self._rejoining = True; self._joining_table = True
                    path = f"{self.place_path}.{self.table_id}"
                    log.info(f"[BOT] Thử vào lại bàn cũ: {path}")
                    await self.send(self.make_enter(path))
                else:
                    self._bet_amts_loaded = False; self._resolved_bet_id = None
                    await self.send(self.make_list_bet_amt())
        else:
            if self._joining_table:
                self._joining_table = False
                if self._rejoining:
                    self._rejoining = False; self._rejoin_attempts += 1; self.table_id = None
                    await asyncio.sleep(1); await self.send(self.make_list_bet_amt())
                else:
                    await asyncio.sleep(1); await self.send(self.make_create_rule())

    async def handle_list_bet_amt(self, r: BinaryReader):
        status = r.i8()
        if status != 0: return
        count = r.i8()
        self.bet_amts = [{"id": i, "value": r.i32()} for i in range(count)]
        self._resolved_bet_id = self.resolve_bet_amt_id()
        self._bet_amts_loaded = True
        await self.send(self.make_create_rule())

    async def handle_create_rule(self, r: BinaryReader):
        status = r.i8()
        if status == 0:
            table_id = r.read_ascii()
            self.table_id = table_id; self._rejoin_attempts = 0
            log.info(f"[CREATE_RULE] Bàn mới! id={table_id}")
            await asyncio.sleep(0.5); self._joining_table = False
            await self.send(self.make_get_table())
        else:
            self._joining_table = False

    async def handle_table(self, r: BinaryReader):
        try:
            first_byte = r.i8()
            if first_byte != 0:
                if "not in table" in r.read_utf().lower():
                    self.in_table = False; self.table_id = None
                    await self.create_new_table()
                return
            
            seat_count = r.u8()
            for _ in range(seat_count):
                r.u8(); r.read_ascii(); r.u8(); child_count = r.u8()
                for _ in range(child_count): r.u8(); r.read_ascii(); r.read_utf(); r.u8(); r.u8()
            
            r.u8(); self.slot = r.i8(); is_playing = r.u8() == 1
            player_count = r.u8(); self.players = {}
            self.player_slot_by_id = {}
            
            for _ in range(player_count):
                sid = r.i8(); pid = r.i64(); name = r.read_utf()
                r.u16(); r.read_ascii(); r.i8(); r.i64(); r.i64(); r.i64(); r.u8(); r.u8()
                self.players[sid] = {'name': name}
                self.player_slot_by_id[pid] = sid
            
            current_player = r.i8(); r.i16(); r.i16(); r.u8()
            self.in_table = True
            
            move_count = r.u8()
            for _ in range(move_count): r.i8(); r.i32()
            
            width = r.u8(); height = r.u8(); self.board.resize(width, height)
            r.i16(); self.board.load_rle(r.read_bytes()); self.update_symbols()
            
            r.u8(); r.u8(); n = r.u8()
            for _ in range(n): r.read_ascii(); r.read_utf()
            
            # --- KIỂM TRA ĐỐI THỦ THỰC SỰ NGỒI GHẾ ---
            has_opponent = any(sid >= 0 and sid != self.slot for sid in self.players.keys())
            
            self.is_playing = is_playing
            log.info(f"[TABLE] Slot={self.slot} Playing={is_playing} Turn=slot{current_player}")
            
            if is_playing and current_player == self.slot:
                if not self._moving and not self.pending_move:
                    self.pending_move = True; await self.do_move()
            elif not is_playing and self.slot >= 0:
                if has_opponent:
                    if not self.ready:
                        log.info("[BOT] Phát hiện đối thủ thực sự đã ngồi vào ghế. Bấm Sẵn sàng!")
                        self.ready = True; await self.send(self.make_ready())
                else:
                    if self.ready:
                        log.info("[BOT] Không có đối thủ ngồi ở ghế đối diện (chỉ có người xem hoặc bàn trống). Hủy Sẵn sàng.")
                    self.ready = False
            elif not is_playing and self.slot < 0:
                self.in_table = False; self.table_id = None
                await asyncio.sleep(1); await self.send(self.make_list_bet_amt())
            
            self._rejoining = False
        except Exception as e: log.error(f"Table error: {e}")

    async def handle_start(self, r: BinaryReader):
        self.total_games += 1; self.is_playing = True; self.ready = False; self.pending_move = False
        self._moving = False; self._last_move_xy = None
        self.opponent_gone_at = None
        
        player_count = r.u8()
        for i in range(player_count):
            r.i8(); r.i32()
        
        width = r.u8(); height = r.u8(); self.board.resize(width, height)
        r.i16(); self.board.load_rle(r.read_bytes()); self.update_symbols()
        
        log.info(f"=== GAME {self.total_games} === Me={'X' if self.my_symbol == CROSS else 'O'}")
        
        if self.ag is None:
            self.init_ag()
        else:
            self.ag.start_game(my_symbol=self.my_symbol)
        self.shifter = None
        
        if self.slot < 0:
            await asyncio.sleep(0.5); await self.send(self.make_get_table())

    async def handle_turn(self, r: BinaryReader):
        sid = r.i8(); r.i16(); r.i16()
        if self.slot < 0: return
        if sid == self.slot and self.is_playing and self.running:
            if not self.pending_move and not self._moving:
                self.pending_move = True; await asyncio.sleep(1.5); await self.do_move()

    async def handle_move(self, r: BinaryReader):
        pos = r.i16(); symbol = r.i8()
        x, y = self.board.pos_to_xy(pos)
        current = self.board.get(x, y)
        if current == symbol:
            if symbol == self.my_symbol and self._last_move_xy is not None:
                self._last_move_xy = None
        elif current != EMPTY and current != symbol:
            self.my_symbol = symbol
            self.opponent_symbol = CROSS if symbol == CIRCLE else CIRCLE
            self.board.undo(x, y); self.board.put(x, y, symbol)
        else:
            self.board.put(x, y, symbol)

    async def handle_play(self, r: BinaryReader):
        status = r.i8()
        if status != 0:
            log.warning(f"PLAY error {status}")
            self.pending_move = False
            if self._last_move_xy:
                self.board.undo(*self._last_move_xy)
                self._last_move_xy = None
            await asyncio.sleep(0.5); await self.send(self.make_get_table())

    async def handle_gameover(self, r: BinaryReader):
        self.is_playing = False; self.pending_move = False
        self.opponent_gone_at = None
        player_count = r.u8(); my_result = None
        for _ in range(player_count):
            sid = r.i8(); result = r.i8(); r.i64()
            if sid == self.slot: my_result = result
        
        if my_result in (1, 11): self.wins += 1; log.info(">>> WIN! <<<")
        elif my_result in (2, 4, 12): self.losses += 1; log.info(">>> LOSE! <<<")
        else: self.draws += 1; log.info(">>> DRAW! <<<")
        
        r.read_utf()
        self.save_stats()
        
        if self._table_lost_at is not None:
            self._table_lost_at = None
            await asyncio.sleep(1.5); await self.create_new_table()
            return
        
        log.info("[BOT] Ở lại bàn, sẽ sẵn sàng sau 5 giây...")
        asyncio.create_task(self._delay_ready(5.0))

    async def handle_kick(self, r: BinaryReader):
        r.i8(); r.read_utf()
        self.is_playing = False; self.in_table = False; self.pending_move = False
        self.table_id = None
        await asyncio.sleep(1); await self.create_new_table()

    async def _delay_ready(self, delay: float):
        await asyncio.sleep(delay)
        if not self.is_playing and self.in_table:
            # Sẽ gửi yêu cầu bàn cờ để kiểm tra và cập nhật ready đồng bộ thay vì ép buộc gửi ready
            await self.send(self.make_get_table())

    async def handle_player_enter(self, r: BinaryReader):
        place_level = r.i8()
        pid = r.i64(); name = r.read_utf()
        if r.remaining() >= 36:
            r.i64(); r.i64(); r.read_ascii(); r.i32(); r.i32(); r.i8(); r.i64(); r.i8()
            
        if place_level < 4: return
        log.info(f"[BOT] Phát hiện {name} vào bàn cờ. Đang cập nhật trạng thái bàn...")
        await self.send(self.make_get_table())

    async def handle_player_exit(self, r: BinaryReader):
        place_level = r.i8()
        pid = r.i64() if r.remaining() >= 8 else -1
        if place_level < 4: return
        
        slot = self.player_slot_by_id.get(pid) if pid >= 0 else None
        if pid >= 0: self.player_slot_by_id.pop(pid, None)
        
        if slot is not None and slot == self.slot:
            if self.is_playing:
                self.in_table = False; self._table_lost_at = time.time()
            else:
                self.in_table = False; await asyncio.sleep(1); await self.create_new_table()
        elif self.is_playing:
            if self.opponent_gone_at is None:
                self.opponent_gone_at = time.time()
                log.info("[BOT] Đối thủ rời giữa ván -> ở lại bàn, chờ GAMEOVER")
        elif self.in_table:
            log.info("[BOT] Phát hiện có người rời bàn. Đang cập nhật lại trạng thái...")
            await self.send(self.make_get_table())

    async def watchdog(self):
        while self.running:
            try: await asyncio.sleep(10)
            except asyncio.CancelledError: return
            if not self.running: return
            
            if self.start_time and time.time() - self.start_time > RUNTIME:
                self.save_stats(); self.stop(); return
            
            if not self.ws or self.ws.close_code is not None: continue
            
            try:
                if (self.opponent_gone_at is not None and self.is_playing
                    and time.time() - self.opponent_gone_at > 15):
                    self.opponent_gone_at = None
                    await self.send(self.make_get_table())
                
                if (self._table_lost_at is not None
                    and time.time() - self._table_lost_at > 8):
                    self._table_lost_at = None; self.table_id = None
                    await self.create_new_table()
                
                if (not self.is_playing and not self.in_table and not self._joining_table
                    and not self._rejoining and self._bet_amts_loaded):
                    await self.send(self.make_create_rule())
            except Exception: pass

    @staticmethod
    def _html_attr(tag: str, name: str) -> str:
        m = re.search(rf'\b{name}\s*=\s*(["\'])(.*?)\1', tag, re.I | re.S)
        return html_lib.unescape(m.group(2)) if m else ""

    def _read_profile_form(self, page_text: str, page_url: str):
        """Đọc form hồ sơ và giữ nguyên mọi trường hiện có."""
        form_match = re.search(
            r'(?is)<form\b[^>]*name=["\']InputForm0["\'][^>]*>.*?</form>',
            page_text)
        if not form_match:
            return None, None
        form = form_match.group(0)
        open_tag = re.search(r'(?is)<form\b[^>]*>', form).group(0)
        action = urljoin(page_url, self._html_attr(open_tag, 'action'))
        data = {}

        for tag in re.findall(r'(?is)<input\b[^>]*>', form):
            name = self._html_attr(tag, 'name')
            input_type = self._html_attr(tag, 'type').lower()
            if not name or input_type in ('submit', 'button', 'image', 'file', 'reset'):
                continue
            if input_type in ('checkbox', 'radio') and not re.search(r'\bchecked\b', tag, re.I):
                continue
            data[name] = self._html_attr(tag, 'value')

        for match in re.finditer(r'(?is)<select\b([^>]*)>(.*?)</select>', form):
            name = self._html_attr('<select ' + match.group(1) + '>', 'name')
            if not name:
                continue
            selected = re.search(
                r'(?is)<option\b([^>]*\bselected\b[^>]*)>(.*?)</option>',
                match.group(2))
            if selected:
                data[name] = self._html_attr('<option ' + selected.group(1) + '>', 'value')

        for match in re.finditer(r'(?is)<textarea\b([^>]*)>(.*?)</textarea>', form):
            name = self._html_attr('<textarea ' + match.group(1) + '>', 'name')
            if name:
                data[name] = html_lib.unescape(match.group(2)).strip()
        return action, data

    def update_random_full_name(self, session: requests.Session) -> Dict:
        """Đổi FULL_NAME mà không chạm tới endpoint đổi tên đăng nhập."""
        edit_url = 'https://gamevh.net/com/ftl/game/profile/update_profile.jsp'
        new_name = generate_random_full_name()
        page = session.get(edit_url, timeout=15, allow_redirects=True)
        action, data = self._read_profile_form(page.text, page.url)
        if not action or data is None:
            log.warning('[Identity] Không đọc được form FULL_NAME')
            return {'ok': False, 'new_full_name': new_name, 'error': 'form_not_found'}

        old_name = data.get('FULL_NAME', '')
        data['FULL_NAME'] = new_name
        data['OLD_PASSWORD'] = PASSWD
        data['SAVE'] = '\uf046'
        response = session.post(
            action, timeout=20, data=data,
            headers={'Origin': 'https://gamevh.net', 'Referer': page.url,
                     'Content-Type': 'application/x-www-form-urlencoded'},
            allow_redirects=True)

        verify_page = session.get(edit_url, timeout=15, allow_redirects=True)
        _, verify_data = self._read_profile_form(verify_page.text, verify_page.url)
        verified_name = (verify_data or {}).get('FULL_NAME')
        ok = verified_name == new_name
        if ok:
            log.info(f'[Identity] FULL_NAME: {old_name!r} -> {new_name!r}')
        else:
            log.warning(
                f'[Identity] FULL_NAME verify failed: expected={new_name!r}, '
                f'actual={verified_name!r}, HTTP={response.status_code}')
        return {
            'ok': ok, 'old_full_name': old_name, 'new_full_name': new_name,
            'verified_full_name': verified_name, 'http_status': response.status_code
        }

    @staticmethod
    def _extract_profile_balance(page_text: str) -> Optional[int]:
        m = re.search(
            r'(?is)<div\s+class=["\'][^"\']*\bchipBalance\b[^"\']*["\'][^>]*>(.*?)</div>',
            page_text)
        if not m:
            return None
        digits = re.sub(r'[^0-9-]', '', html_lib.unescape(re.sub(r'<[^>]+>', '', m.group(1))))
        return int(digits) if digits and digits != '-' else None

    @staticmethod
    def _extract_profile_avatar(page_text: str) -> Optional[int]:
        m = re.search(r'/avatar/builtin(\d+)\.(?:webp|png|jpg)', page_text, re.I)
        return int(m.group(1)) if m else None

    def _load_avatar_catalog(self, session: requests.Session) -> List[Dict]:
        catalog = []
        seen = set()
        pattern = re.compile(
            r'''buyAvatar\(\s*(["']?)(\d+)\1\s*,\s*(["'])(.*?)\3\s*,\s*(["']?)([\d,.]+)\5\s*\)''',
            re.I | re.S)
        for category in range(1, 7):
            url = ('https://gamevh.net/com/ftl/game/profile/'
                   f'avatar_by_category.jsp?excludeLayout=true&category_id={category}')
            page = session.get(url, timeout=15)
            for match in pattern.finditer(page.text):
                avatar_id = int(match.group(2))
                if avatar_id in seen:
                    continue
                seen.add(avatar_id)
                cost = int(re.sub(r'[^0-9]', '', match.group(6)) or '0')
                catalog.append({
                    'id': avatar_id,
                    'name': html_lib.unescape(match.group(4)),
                    'cost': cost,
                    'category': category
                })
        return catalog

    def update_random_avatar(self, session: requests.Session) -> Dict:
        """Chọn avatar ngẫu nhiên từ catalog sống; có thể phát sinh phí xu."""
        profile_url = 'https://gamevh.net/com/ftl/game/profile/player_profile.jsp'
        before_page = session.get(profile_url, timeout=15)
        old_avatar = self._extract_profile_avatar(before_page.text)
        balance_before = self._extract_profile_balance(before_page.text)
        catalog = self._load_avatar_catalog(session)
        choices = [item for item in catalog if item['id'] != old_avatar]
        if not choices:
            log.warning('[Identity] Không tải được catalog avatar')
            return {'ok': False, 'error': 'avatar_catalog_empty'}

        selected = random.choice(choices)
        update_url = (
            'https://gamevh.net/com/ftl/game/profile/update_avatar.jsp'
            f"?pk={selected['id']}&redirect=/")
        response = session.post(
            update_url, timeout=20,
            headers={'Origin': 'https://gamevh.net',
                     'Referer': 'https://gamevh.net/com/ftl/game/profile/avatar.jsp'},
            allow_redirects=True)

        after_page = session.get(profile_url, timeout=15)
        new_avatar = self._extract_profile_avatar(after_page.text)
        balance_after = self._extract_profile_balance(after_page.text)
        ok = new_avatar == selected['id']
        if ok:
            log.info(
                f"[Identity] Avatar: builtin{old_avatar} -> builtin{new_avatar}; "
                f"giá niêm yết={selected['cost']} xu; số dư={balance_before}->{balance_after}")
        else:
            log.warning(
                f"[Identity] Avatar verify failed: expected=builtin{selected['id']}, "
                f"actual=builtin{new_avatar}, HTTP={response.status_code}")
        return {
            'ok': ok, 'old_avatar': old_avatar, 'new_avatar': new_avatar,
            'selected_avatar': selected, 'balance_before': balance_before,
            'balance_after': balance_after, 'http_status': response.status_code
        }

    def update_profile_identity(self, session: requests.Session) -> Dict:
        log.info('[Identity] Updating FULL_NAME + avatar (không đổi tên đăng nhập)...')
        result = {
            'full_name': self.update_random_full_name(session),
            'avatar': self.update_random_avatar(session)
        }
        self.identity_result = result
        return result

    def http_login(self) -> bool:
        try:
            session = requests.Session()
            ua = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/139.0 Safari/537.36")
            session.headers.update({
                'User-Agent': ua,
                'Accept-Language': 'vi-VN,vi;q=0.9,en;q=0.7'
            })
            session.get('https://gamevh.net/login.jsp', timeout=10)
            resp = session.post(
                'https://gamevh.net/login.jsp', timeout=10,
                data={'redirect': '/', 'USER_NAME': USER, 'PASSWORD': PASSWD,
                      'AUTO_LOGIN': 'true', 'LOGIN': 'Đăng nhập'},
                headers={'Origin': 'https://gamevh.net',
                         'Referer': 'https://gamevh.net/login.jsp',
                         'Content-Type': 'application/x-www-form-urlencoded'},
                allow_redirects=True)
            if 'login.jsp' in resp.url:
                log.error(f'[BOT] HTTP login failed: {resp.url}')
                return False

            if AUTO_IDENTITY and not self._identity_attempted:
                self._identity_attempted = True
                self.update_profile_identity(session)

            game_resp = session.get(GAME_URL, timeout=10)
            self.cookie = '; '.join(f'{k}={v}' for k, v in session.cookies.items())
            page_html = game_resp.text

            tm = re.search(r'var\s+token\s*=\s*(-?\d+)', page_html)
            if not tm:
                log.error('[BOT] Token not found')
                return False
            self.token = int(tm.group(1))

            nm = re.search(r"var\s+currentPlayerNickName\s*=\s*'([^']+)'", page_html)
            if not nm:
                log.error('[BOT] currentPlayerNickName not found')
                return False
            self.nickname = nm.group(1)

            pm = re.search(r'var\s+placePath\s*=\s*\"([^\"]+)\"', page_html)
            if pm:
                self.place_path = pm.group(1)

            if self.nickname == USER:
                log.info(f'[Identity] Tên đăng nhập giữ nguyên: {self.nickname}')
            else:
                log.warning(
                    f'[Identity] Server nickname={self.nickname!r} khác CARO_USER={USER!r}')
            log.info(f'[BOT] Login OK: {self.nickname}')
            return True
        except Exception as e:
            log.error(f'[BOT] Login error: {e}', exc_info=True)
            return False

    async def connect_ws(self) -> bool:
        try:
            self.ws = await websockets.connect(WS_URL,
                additional_headers={"Cookie": self.cookie, "Origin": "https://gamevh.net",
                                    "User-Agent": "Mozilla/5.0"},
                max_size=2**20, ping_interval=None)
            return True
        except Exception as e: log.error(f"[BOT] WS connect error: {e}"); return False

    async def run_ws(self):
        if not await self.connect_ws(): return
        await self.send(self.make_login())
        wd_task = asyncio.create_task(self.watchdog())
        try:
            async for raw in self.ws:
                if not self.running: break
                if isinstance(raw, bytes): await self.handle(raw)
        except websockets.exceptions.ConnectionClosed as e:
            log.warning(f"[BOT] WS closed: {e.code}")
        except Exception as e: log.error(f"[BOT] WS error: {e}")
        finally:
            wd_task.cancel()
            try: await wd_task
            except Exception: pass
            self.save_stats()
            if self.ws and self.ws.close_code is None:
                try: await self.ws.close()
                except Exception: pass

    async def run(self):
        self.start_time = time.time(); self._running = True
        log.info(f"{'='*50}")
        log.info("BOT CARO EMBRYO - FULL_NAME + AVATAR v3.0")
        log.info(f"{'='*50}")
        
        retry_count = 0
        while self.running:
            if time.time() - self.start_time > RUNTIME: break
            
            was_in_table = self.in_table or self.is_playing
            self._want_rejoin = (was_in_table and self.table_id is not None and self._rejoin_attempts < 2)
            
            self.is_playing = False; self.pending_move = False
            self.in_table = False; self.ready = False
            self.board = Board(width=15, height=19); self.players.clear()
            self.bet_amts = []; self._resolved_bet_id = None
            self._bet_amts_loaded = False; self._joining_table = False
            self.opponent_gone_at = None; self._table_lost_at = None
            
            if self.ag: self.ag.stop(); self.ag = None; self.ag_available = False
            
            # Một lần đăng nhập mỗi chu kỳ để tránh giới hạn/brute-force.
            login_ok = await asyncio.get_event_loop().run_in_executor(None, self.http_login)
            if not login_ok:
                retry_count += 1
                retry_delay = min(30 * (2 ** (retry_count - 1)), 300)
                remaining = RUNTIME - (time.time() - self.start_time)
                if remaining <= 0:
                    break
                retry_delay = min(retry_delay, remaining)
                log.warning(f'[BOT] Login thất bại; thử lại sau {retry_delay:.0f}s')
                await asyncio.sleep(retry_delay)
                continue

            retry_count = 0
            if IDENTITY_TEST_ONLY:
                # Chế độ kiểm tra: cập nhật + xác minh hồ sơ, không kết nối
                # WebSocket, không vào phòng, không đặt cược/chơi game.
                remaining = RUNTIME - (time.time() - self.start_time)
                log.info(
                    f'[TEST] Identity test only; không chạy game. '
                    f'Chờ hết {max(0, remaining):.1f}s...')
                if remaining > 0:
                    await asyncio.sleep(remaining)
                self.stop()
                break

            await self.run_ws()
            
            if not (self.in_table or self.is_playing):
                self.table_id = None
            
            self.save_stats()
            if self.ag: self.ag.stop(); self.ag = None

def main():
    bin_path = auto_download_alphagomoku()
    if bin_path: print(f"[SETUP] Katagomo ready: {os.path.basename(bin_path)}")
    else: print("[SETUP] No Katagomo - using local engine only")
    
    try: asyncio.get_running_loop(); loop = asyncio.get_running_loop(); loop.create_task(_run_bot())
    except RuntimeError: asyncio.run(_run_bot())

async def _run_bot():
    try: bot = CaroBot(); await bot.run()
    except KeyboardInterrupt: log.info("[BOT] Stopped by user")
    except Exception as e: log.error(f"[BOT] Error: {e}", exc_info=True)

if __name__ == "__main__": main()
elif 'ipykernel' in sys.modules or 'google.colab' in sys.modules: main()
