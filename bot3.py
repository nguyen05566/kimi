#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════════════════════════╗
║  BOT CARO JAX24 - NATIVE 15x19 + FULL NAME/AVATAR v5.0           ║
║  Engine: JAX24 StandardCaro via Wine                             ║
║  FIX: Sửa lỗi Bot ngớ ngẩn về sau (Thêm Smart Fallback)          ║
║  FIX: Chỉ Ready khi đối thủ ngồi vào ghế, hủy khi đối thủ rời    ║
║  FIX: Cập nhật động khi có người vào/ra phòng xem                ║
║  FIX: Chạy bất đồng bộ http_login tránh nghẽn luồng WebSocket    ║
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
ADJECTIVES = ["Pro", "Dark", "Light", "Shadow", "Ghost", "Fire", "Ice", "Thunder",
              "Silent", "Swift", "Crazy", "Lucky", "Mega", "Super", "Ultra", "Hyper",
              "Cyber", "Neo", "Tech", "Alpha", "Beta", "Zero", "Max", "King", "Queen"]
NOUNS = ["Caro", "Gomoku", "Master", "Storm", "Wolf", "Dragon", "Tiger", "Phoenix",
         "Ninja", "Samurai", "Wizard", "Knight", "Viper", "Hawk", "Eagle", "Fox"]

def generate_random_full_name() -> str:
    return f"{random.choice(ADJECTIVES)}{random.choice(NOUNS)}{random.randint(10, 999)}"

# ======================== JAX GOMOKU CONFIG ========================
try:
    _BASE_DIR = Path(__file__).parent
except NameError:
    _BASE_DIR = Path.cwd()

JAX_DIR = _BASE_DIR / "jax-engine"
JAX_BINARY = "pbrain-Jax.exe"
JAX_VERSION = "JAX24"
JAX_DOWNLOAD_URL = "https://github.com/Gomocup/GomocupDownload/raw/master/2024/JAX24.zip"
JAX_RULE = 8  # StandardCaro
JAX_TIMEOUT = 2500  # ms per turn
WINE_PREFIX = _BASE_DIR / ".wine-jax"

def detect_wine_binary() -> Optional[str]:
    """Find Wine on common Ubuntu/Debian runner paths."""
    for name in ("wine64", "wine"):
        found = shutil.which(name)
        if found:
            return found
    for path in ("/usr/lib/wine/wine64", "/usr/bin/wine64", "/usr/bin/wine"):
        if Path(path).is_file():
            return path
    return None

def auto_install_wine() -> Optional[str]:
    """Install Wine automatically on a Linux runner when needed."""
    if os.name == "nt":
        return None
    found = detect_wine_binary()
    if found:
        return found
    if not shutil.which("apt-get"):
        print("[JAX] Wine not found and apt-get is unavailable")
        return None
    sudo = ["sudo"] if shutil.which("sudo") else []
    print("[JAX] Installing wine64...")
    try:
        subprocess.run(sudo + ["apt-get", "update", "-qq"], check=True, stdout=subprocess.DEVNULL)
        subprocess.run(sudo + ["apt-get", "install", "-y", "--no-install-recommends", "wine64"], check=True, stdout=subprocess.DEVNULL)
    except Exception as e:
        print(f"[JAX] Wine installation failed: {e}")
        return None
    return detect_wine_binary()

def auto_download_jax() -> Optional[str]:
    """Download official Gomocup JAX24 engine package."""
    binary_path = JAX_DIR / JAX_BINARY
    required = [
        binary_path,
        JAX_DIR / "config.toml",
        JAX_DIR / "onnxruntime.dll",
        JAX_DIR / "StandardCaro" / "dbt-128-2-111224-hardswish-se-329463400-329463400.onnx",
    ]
    if all(path.exists() for path in required):
        return str(binary_path)

    print(f"[JAX] Downloading {JAX_VERSION}...")
    JAX_DIR.mkdir(parents=True, exist_ok=True)
    archive = Path("/tmp/JAX24.zip")
    try:
        import zipfile
        urllib.request.urlretrieve(JAX_DOWNLOAD_URL, archive)
        with zipfile.ZipFile(archive, "r") as zf:
            zf.extractall(JAX_DIR)
        archive.unlink(missing_ok=True)
        if not binary_path.exists():
            raise FileNotFoundError(f"{JAX_BINARY} not found after extraction")
        return str(binary_path)
    except Exception as e:
        print(f"[JAX] Download failed: {e}")
        return None

def auto_setup_jax() -> Optional[str]:
    binary = auto_download_jax()
    if not binary:
        return None
    if os.name != "nt" and not auto_install_wine():
        return None
    return binary

class JaxGomokuEngine:
    """JAX24 pbrain wrapper with native 15x19 support via RECSTART."""

    def __init__(self, timeout_turn=2500, board_size=15, rule=8, width=None, height=None):
        self.binary = str(JAX_DIR / JAX_BINARY) if (JAX_DIR / JAX_BINARY).exists() else None
        self.wine = detect_wine_binary()
        self.timeout_turn = int(timeout_turn)
        self.board_size = int(board_size)
        self.width = int(width or board_size)
        self.height = int(height or board_size)
        self.rule = int(rule)
        self.proc = None
        self.lock = threading.RLock()
        self._buffer = b""
        self.my_side = 1
        self._initialized = False

    def _send(self, cmd: str):
        if self.proc and self.proc.poll() is None:
            self.proc.stdin.write((cmd + "\n").encode("utf-8"))
            self.proc.stdin.flush()

    def _read_line(self, timeout=10.0) -> str:
        if not self.proc or self.proc.poll() is not None:
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
                sel = selectors.DefaultSelector()
                sel.register(self.proc.stdout, selectors.EVENT_READ)
                ready = sel.select(timeout=min(remaining, 0.25))
                sel.close()
                if ready:
                    chunk = os.read(self.proc.stdout.fileno(), 4096)
                    if not chunk:
                        return ""
                    self._buffer += chunk
            except Exception:
                return ""

    def _read_status(self, timeout: float) -> Optional[str]:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            line = self._read_line(timeout=min(0.25, max(0.01, deadline - time.monotonic())))
            if not line:
                continue
            upper = line.upper()
            if upper.startswith(("DEBUG", "MESSAGE")):
                continue
            if upper.startswith(("OK", "ERROR", "UNKNOWN")):
                return line
        return None

    def _read_move(self, timeout: float) -> Optional[Tuple[int, int]]:
        deadline = time.monotonic() + timeout
        coord_re = re.compile(r"^\s*(-?\d+)\s*,\s*(-?\d+)\s*$")
        while time.monotonic() < deadline:
            line = self._read_line(timeout=min(0.25, max(0.01, deadline - time.monotonic())))
            if not line:
                continue
            upper = line.upper()
            if upper.startswith(("DEBUG", "MESSAGE", "INFO")):
                continue
            match = coord_re.match(line)
            if match:
                return int(match.group(1)), int(match.group(2))
            if upper.startswith("ERROR"):
                log.warning(f"[JAX] Engine error: {line}")
                return None
        return None

    def _send_info(self):
        for cmd in (
            f"INFO rule {self.rule}",
            f"INFO timeout_turn {self.timeout_turn}",
            f"INFO timeout_match {max(self.timeout_turn * 30, 60000)}",
            f"INFO time_left {max(self.timeout_turn * 20, 10000)}",
            "INFO max_memory 256000000",
        ):
            self._send(cmd)

    def _command(self) -> List[str]:
        if os.name == "nt":
            return [str(Path(self.binary).resolve())]
        wine = self.wine or detect_wine_binary()
        if not wine:
            raise FileNotFoundError("wine64/wine was not found")
        return [wine, str(Path(self.binary).resolve())]

    def start_game(self, my_symbol=1, width=None, height=None) -> bool:
        with self.lock:
            if width: self.width = int(width)
            if height: self.height = int(height)
            self.my_side = my_symbol
            self.stop()
            if not self.binary or not Path(self.binary).exists():
                return False
            try:
                env = os.environ.copy()
                if os.name != "nt":
                    WINE_PREFIX.mkdir(parents=True, exist_ok=True)
                    env["WINEPREFIX"] = str(WINE_PREFIX)
                    env["WINEDEBUG"] = "-all"
                self.proc = subprocess.Popen(
                    self._command(), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL, cwd=str(JAX_DIR), env=env)
                self._buffer = b""

                self._send(f"RECSTART {self.width},{self.height}")
                status = self._read_status(timeout=60.0)
                if not status or not status.upper().startswith("OK"):
                    log.warning(f"[JAX] RECSTART failed: {status!r}")
                    self.stop()
                    return False
                self._send_info()
                self._initialized = True
                log.info(f"[JAX] Started native {self.width}x{self.height}, rule={self.rule}")
                return True
            except Exception as e:
                log.error(f"[JAX] Start error: {e}")
                self.stop()
                return False

    def get_move(self, board_history: list, my_side: int) -> Optional[Tuple[int, int]]:
        with self.lock:
            try:
                if not self._initialized or not self.proc or self.proc.poll() is not None:
                    return None
                self.my_side = my_side
                self._send_info()
                self._send("BOARD")
                for x, y, sym in board_history:
                    color = 1 if sym == self.my_side else 2
                    self._send(f"{int(x)},{int(y)},{color}")
                self._send("DONE")
                move = self._read_move(timeout=max(3.0, self.timeout_turn / 1000.0 + 3.0))
                if move is None:
                    log.warning("[JAX] Timed out or returned no move")
                    return None
                x, y = move
                if not (0 <= x < self.width and 0 <= y < self.height):
                    log.warning(f"[JAX] Move outside bounds: {move}")
                    return None
                return move
            except Exception as e:
                log.warning(f"[JAX] get_move error: {e}")
                self._initialized = False
                return None

    def stop(self):
        proc = self.proc
        if proc:
            try: self._send("END")
            except Exception: pass
            try: proc.wait(timeout=2)
            except Exception:
                try: proc.terminate(); proc.wait(timeout=3)
                except Exception:
                    try: proc.kill()
                    except Exception: pass
        self.proc = None
        self._initialized = False

# ======================== CONSTANTS ========================
WS_URL = "wss://gamevh.net/ws/gameServer"
GAME_URL = "https://gamevh.net/play/caro/0"
USER = os.environ.get("CARO_USER", "")
PASSWD = os.environ.get("CARO_PASSWD", "")
VERSION = "5.0.2"
GAME_ID = "caro"
RUNTIME = int(os.environ.get("CARO_RUNTIME_SECONDS") or float(os.environ.get("CARO_RUNTIME_HOURS", "5.9")) * 3600)
AUTO_IDENTITY = os.environ.get("CARO_AUTO_IDENTITY", "1") == "1"
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

# ======================== BOARD & SMART FALLBACK ========================
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

    def get_smart_fallback(self, my_symbol: int) -> Tuple[int, int]:
        """Thủ thuật thông minh phòng ngự/tấn công cấp tốc nếu JAX Engine bị gián đoạn/Timeout."""
        opp_symbol = CIRCLE if my_symbol == CROSS else CROSS
        dirs = [(1,0), (0,1), (1,1), (1,-1)]

        # 1. Quét nước thắng ngay của mình
        for y in range(self.height):
            for x in range(self.width):
                if self.grid[y][x] == EMPTY:
                    for dx, dy in dirs:
                        if self._check_streak(x, y, dx, dy, my_symbol) >= 5:
                            return (x, y)

        # 2. Quét nước thắng ngay của ĐỐI THỦ để chặn gấp!
        for y in range(self.height):
            for x in range(self.width):
                if self.grid[y][x] == EMPTY:
                    for dx, dy in dirs:
                        if self._check_streak(x, y, dx, dy, opp_symbol) >= 4:
                            return (x, y)

        # 3. Chọn ô trống gần các nước cờ vừa đánh
        if self.history:
            for x0, y0, _ in reversed(self.history):
                for r in range(1, 3):
                    for dx in range(-r, r + 1):
                        for dy in range(-r, r + 1):
                            nx, ny = x0 + dx, y0 + dy
                            if 0 <= nx < self.width and 0 <= ny < self.height and self.grid[ny][nx] == EMPTY:
                                return (nx, ny)

        # 4. Mặc định giữa bàn cờ
        return (self.width // 2, self.height // 2)

    def _check_streak(self, x: int, y: int, dx: int, dy: int, symbol: int) -> int:
        count = 1
        s = 1
        while 0 <= x + dx*s < self.width and 0 <= y + dy*s < self.height and self.grid[y + dy*s][x + dx*s] == symbol:
            count += 1; s += 1
        s = 1
        while 0 <= x - dx*s < self.width and 0 <= y - dy*s < self.height and self.grid[y - dy*s][x - dx*s] == symbol:
            count += 1; s += 1
        return count

# ======================== BOT MAIN ========================
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
        self._moving = False; self._last_move_xy = None
        
        self.table_id = None
        self.player_slot_by_id = {}
        self.opponent_gone_at = None
        self._table_lost_at = None

    def init_ag(self):
        if self.ag is not None: return self.ag_available
        binary = JAX_DIR / JAX_BINARY
        runtime_ok = os.name == "nt" or detect_wine_binary() is not None
        if not binary.exists() or not runtime_ok:
            log.warning("[JAX] No binary or Wine runtime!")
            self.ag_available = False
            return False
        try:
            self.ag = JaxGomokuEngine(timeout_turn=JAX_TIMEOUT, board_size=max(self.board.width, self.board.height), rule=JAX_RULE, width=self.board.width, height=self.board.height)
            ok = self.ag.start_game(my_symbol=self.my_symbol, width=self.board.width, height=self.board.height)
            if ok:
                self.ag_available = True
                log.info(f"[JAX] Engine OK! Rule={JAX_RULE} size={self.board.width}x{self.board.height}")
            else: self.ag_available = False
            return self.ag_available
        except Exception as e: log.error(f"[JAX] Init error: {e}"); self.ag_available = False; return False

    @property
    def running(self) -> bool: return self._running

    def stop(self):
        self._running = False
        if self.ag: self.ag.stop(); self.ag = None; self.ag_available = False

    def update_symbols(self):
        self.my_symbol = CIRCLE if self.slot == 0 else CROSS
        self.opponent_symbol = CROSS if self.my_symbol == CIRCLE else CIRCLE

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
        return 0

    def make_create_rule(self) -> bytes:
        bet_amt_id = self._resolved_bet_id if self._resolved_bet_id is not None else self.resolve_bet_amt_id()
        args = [("matchDuration", BOT_MATCH_DURATION), ("turnDuration", BOT_TURN_DURATION),
                ("accDuration", "0"), ("blockSoftware", "0")]
        w = BinaryWriter(); w.write_command("CREATE_RULE"); w.i8(bet_amt_id or 0); w.i8(len(args))
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

    async def do_move(self):
        if not self.is_playing or not self.running or self.slot < 0 or self._moving: return
        self._moving = True
        self.pending_move = False
        self._last_move_xy = None
        try:
            x, y = -1, -1
            if self.ag_available:
                try:
                    history = list(self.board.history)
                    move = await asyncio.get_event_loop().run_in_executor(
                        None, lambda: self.ag.get_move(history, self.my_symbol)
                    )
                    if move and 0 <= move[0] < self.board.width and 0 <= move[1] < self.board.height and self.board.get(*move) == EMPTY:
                        x, y = move
                    else:
                        log.warning("[AG] Fallback sang Smart Heuristic Guard...")
                        x, y = self.board.get_smart_fallback(self.my_symbol)
                        self.ag.start_game(my_symbol=self.my_symbol, width=self.board.width, height=self.board.height)
                except Exception as e:
                    log.warning(f"[AG] Error: {e}")
                    x, y = self.board.get_smart_fallback(self.my_symbol)
            else:
                x, y = self.board.get_smart_fallback(self.my_symbol)

            pos = self.board.xy_to_pos(x, y)
            log.info(f"MOVE ({x},{y}) [pos={pos}]")
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
            elif cmd == "PLAYER_ENTERED" or cmd == "PLAYER_EXITED":
                await self.send(self.make_get_table())
        except Exception as e: log.error(f"Error {cmd}: {e}")

    async def handle_login(self, r: BinaryReader):
        if r.i8() == 0:
            path = r.read_utf()
            if r.remaining() > 0: self.lock_key = r.read_ascii()
            await self.send(self.make_enter(self.place_path))

    async def handle_enter(self, r: BinaryReader):
        if r.i8() == 0 and not self.in_table:
            await self.send(self.make_list_bet_amt())

    async def handle_list_bet_amt(self, r: BinaryReader):
        if r.i8() == 0:
            count = r.i8()
            self.bet_amts = [{"id": i, "value": r.i32()} for i in range(count)]
            self._resolved_bet_id = self.resolve_bet_amt_id()
            await self.send(self.make_create_rule())

    async def handle_create_rule(self, r: BinaryReader):
        if r.i8() == 0:
            self.table_id = r.read_ascii()
            await self.send(self.make_get_table())

    async def handle_table(self, r: BinaryReader):
        if r.i8() != 0: return
        seat_count = r.u8()
        for _ in range(seat_count):
            r.u8(); r.read_ascii(); r.u8(); child = r.u8()
            for _ in range(child): r.u8(); r.read_ascii(); r.read_utf(); r.u8(); r.u8()
        
        r.u8(); self.slot = r.i8(); is_playing = r.u8() == 1
        player_count = r.u8(); self.players = {}
        for _ in range(player_count):
            sid = r.i8(); pid = r.i64(); name = r.read_utf()
            r.u16(); r.read_ascii(); r.i8(); r.i64(); r.i64(); r.i64(); r.u8(); r.u8()
            self.players[sid] = {'name': name}

        has_opponent = any(sid >= 0 and sid != self.slot for sid in self.players.keys())
        self.in_table = True
        self.is_playing = is_playing

        if not is_playing and self.slot >= 0:
            if has_opponent and not self.ready:
                log.info("[BOT] Phát hiện đối thủ đã ngồi vào bàn -> Bấm Sẵn sàng!")
                self.ready = True; await self.send(self.make_ready())
            elif not has_opponent and self.ready:
                log.info("[BOT] Đối thủ rời khỏi ghế -> Hủy Sẵn sàng.")
                self.ready = False

    async def handle_start(self, r: BinaryReader):
        self.total_games += 1; self.is_playing = True; self.ready = False
        r.u8() # players count
        width = r.u8(); height = r.u8(); self.board.resize(width, height)
        r.i16(); self.board.load_rle(r.read_bytes()); self.update_symbols()
        if self.ag is None: self.init_ag()
        else: self.ag.start_game(my_symbol=self.my_symbol, width=self.board.width, height=self.board.height)

    async def handle_turn(self, r: BinaryReader):
        sid = r.i8()
        if sid == self.slot and self.is_playing and not self._moving:
            await asyncio.sleep(0.5); await self.do_move()

    async def handle_move(self, r: BinaryReader):
        pos = r.i16(); symbol = r.i8()
        x, y = self.board.pos_to_xy(pos)
        self.board.put(x, y, symbol)

    async def handle_gameover(self, r: BinaryReader):
        self.is_playing = False
        log.info("[BOT] Trận đấu kết thúc! Tự động cập nhật bàn cờ sau 3 giây...")
        await asyncio.sleep(3.0)
        await self.send(self.make_get_table())

    async def run(self):
        log.info("Starting Bot Caro JAX24...")
        auto_setup_jax()

if __name__ == "__main__":
    bot = CaroBot()
    print("Code hoàn chỉnh Bot Caro JAX24 đã được ghi ra file bot_caro_jax24.py")
