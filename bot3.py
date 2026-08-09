#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════════════════════════╗
║  BOT CARO JAX24 - NATIVE 15x19 + OPEN-FOUR GUARD v4.3       ║
║  Engine: JAX24 StandardCaro via Wine                                   ║
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
JAX_TIMEOUT = 2000  # milliseconds per move
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
    """Install Wine automatically on a Linux GitHub runner when needed."""
    if os.name == "nt":
        return None
    found = detect_wine_binary()
    if found:
        return found
    if not shutil.which("apt-get"):
        print("[JAX] Wine not found and apt-get is unavailable")
        return None
    sudo = ["sudo"] if shutil.which("sudo") else []
    print("[JAX] Installing wine64 (one-time runner setup)...")
    try:
        subprocess.run(
            sudo + ["apt-get", "update", "-qq"], check=True,
            stdout=subprocess.DEVNULL)
        subprocess.run(
            sudo + ["apt-get", "install", "-y", "--no-install-recommends", "wine64"],
            check=True, stdout=subprocess.DEVNULL)
    except Exception as e:
        print(f"[JAX] Wine installation failed: {e}")
        return None
    return detect_wine_binary()


def tune_jax_config() -> bool:
    """Make JAX less random and less eager to stop in tactical positions."""
    config_path = JAX_DIR / "config.toml"
    if not config_path.exists():
        return False
    try:
        text = config_path.read_text(encoding="utf-8")
        replacements = {
            r"(?m)^move_temperature\s*=.*$": "move_temperature = 0.05",
            r"(?m)^fast_search_visits_threshold\s*=.*$": "fast_search_visits_threshold = 1000",
            r"(?m)^fast_search_best_action_visits_proportion_threshold\s*=.*$":
                "fast_search_best_action_visits_proportion_threshold = 0.99",
            r"(?m)^fast_search_win_rate_threshold\s*=.*$":
                "fast_search_win_rate_threshold = 0.995",
            r"(?m)^fast_search_draw_rate_threshold\s*=.*$":
                "fast_search_draw_rate_threshold = 0.98",
        }
        for pattern, replacement in replacements.items():
            text = re.sub(pattern, replacement, text)
        config_path.write_text(text, encoding="utf-8")
        return True
    except Exception as e:
        print(f"[JAX] Config tuning failed: {e}")
        return False


def auto_download_jax() -> Optional[str]:
    """Download the official Gomocup JAX24 CPU package."""
    binary_path = JAX_DIR / JAX_BINARY
    required = [
        binary_path,
        JAX_DIR / "config.toml",
        JAX_DIR / "onnxruntime.dll",
        JAX_DIR / "StandardCaro" / "dbt-128-2-111224-hardswish-se-329463400-329463400.onnx",
    ]
    if all(path.exists() for path in required):
        tune_jax_config()
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
        tune_jax_config()
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

    def __init__(self, timeout_turn=2000, board_size=15, rule=8, width=None, height=None):
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
        self._synced = False
        self._expected_history_len = -1

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
            if upper.startswith("DEBUG") or upper.startswith("MESSAGE"):
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

    @staticmethod
    def _timeout_for_position(stone_count: int) -> int:
        """Spend more time when the board becomes tactically complicated."""
        if stone_count < 12:
            return 2000
        if stone_count < 30:
            return 4000
        return 7000

    def invalidate_sync(self):
        """Force the next request to send a complete BOARD snapshot."""
        self._synced = False
        self._expected_history_len = -1

    def _command(self) -> List[str]:
        if os.name == "nt":
            return [str(Path(self.binary).resolve())]
        wine = self.wine or detect_wine_binary()
        if not wine:
            raise FileNotFoundError("wine64/wine was not found")
        return [wine, str(Path(self.binary).resolve())]

    def start_game(self, my_symbol=1, width=None, height=None) -> bool:
        with self.lock:
            if width:
                self.width = int(width)
            if height:
                self.height = int(height)
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

                # JAX24 intentionally implements the misspelled RECSTART command.
                self._send(f"RECSTART {self.width},{self.height}")
                status = self._read_status(timeout=60.0)
                if not status or not status.upper().startswith("OK"):
                    log.warning(f"[JAX] RECSTART failed: {status!r}")
                    self.stop()
                    return False
                self._send_info()
                self._initialized = True
                self.invalidate_sync()
                log.info(
                    f"[JAX] Started native {self.width}x{self.height}, "
                    f"rule={self.rule} (StandardCaro)")
                return True
            except Exception as e:
                log.error(f"[JAX] Start error: {e}")
                self.stop()
                return False

    def restart_game(self) -> bool:
        return self.start_game(
            my_symbol=self.my_side, width=self.width, height=self.height)

    def get_move(self, board_history: list, my_side: int) -> Optional[Tuple[int, int]]:
        with self.lock:
            try:
                if not self._initialized or not self.proc or self.proc.poll() is not None:
                    return None
                self.my_side = my_side
                self.timeout_turn = self._timeout_for_position(len(board_history))
                self._send_info()

                # Preserve JAX's MCTS tree with TURN whenever exactly one new
                # opponent move was appended since the previous engine move.
                use_turn = (
                    self._synced and board_history and
                    len(board_history) == self._expected_history_len + 1
                )
                if use_turn:
                    last_x, last_y, last_symbol = board_history[-1]
                    if last_symbol == self.my_side:
                        # A table snapshot may have rebuilt history in scan order.
                        use_turn = False
                    else:
                        self._send(f"TURN {int(last_x)},{int(last_y)}")
                        log.info(
                            f"[JAX] TURN {int(last_x)},{int(last_y)}; "
                            f"stones={len(board_history)} timeout={self.timeout_turn}ms")

                if not use_turn:
                    self._send("BOARD")
                    for x, y, sym in board_history:
                        color = 1 if sym == self.my_side else 2
                        self._send(f"{int(x)},{int(y)},{color}")
                    self._send("DONE")
                    log.info(
                        f"[JAX] BOARD resync; stones={len(board_history)} "
                        f"timeout={self.timeout_turn}ms")

                move = self._read_move(
                    timeout=max(4.0, self.timeout_turn / 1000.0 + 3.0))
                if move is None:
                    log.warning("[JAX] Timed out or returned no coordinate")
                    self.invalidate_sync()
                    return None
                x, y = move
                if not (0 <= x < self.width and 0 <= y < self.height):
                    log.warning(f"[JAX] Invalid move outside {self.width}x{self.height}: {move}")
                    self.invalidate_sync()
                    return None
                self._synced = True
                self._expected_history_len = len(board_history) + 1
                return move
            except Exception as e:
                log.warning(f"[JAX] get_move error: {e}")
                self.invalidate_sync()
                return None

    def stop(self):
        proc = self.proc
        if proc:
            try:
                self._send("END")
            except Exception:
                pass
            try:
                proc.wait(timeout=2)
            except Exception:
                try:
                    proc.terminate()
                    proc.wait(timeout=3)
                except Exception:
                    try:
                        proc.kill()
                    except Exception:
                        pass
        self.proc = None
        self._initialized = False
        self.invalidate_sync()


# ======================== CONSTANTS & CONFIG ========================
WS_URL = "wss://gamevh.net/ws/gameServer"
GAME_URL = "https://gamevh.net/play/caro/0"
USER = os.environ.get("CARO_USER", "")
PASSWD = os.environ.get("CARO_PASSWD", "")
if not USER or not PASSWD:
    print("[BOT] Thiếu CARO_USER / CARO_PASSWD (GitHub Secrets) - thoát")
    sys.exit(1)
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

    def _line_length_after(self, x: int, y: int, symbol: int,
                           dx: int, dy: int) -> int:
        """Count a contiguous line if symbol were placed at an empty cell."""
        total = 1
        for direction in (1, -1):
            nx, ny = x + dx * direction, y + dy * direction
            while (0 <= nx < self.width and 0 <= ny < self.height
                   and self.grid[ny][nx] == symbol):
                total += 1
                nx += dx * direction
                ny += dy * direction
        return total

    def would_make_five(self, x: int, y: int, symbol: int) -> bool:
        if self.get(x, y) != EMPTY:
            return False
        return any(
            self._line_length_after(x, y, symbol, dx, dy) >= 5
            for dx, dy in ((1, 0), (0, 1), (1, 1), (1, -1))
        )

    def immediate_winning_moves(self, symbol: int) -> List[Tuple[int, int]]:
        result = []
        for y in range(self.height):
            for x in range(self.width):
                if self.would_make_five(x, y, symbol):
                    result.append((x, y))
        return result

    def fork_creating_moves(self, symbol: int) -> List[Tuple[int, int]]:
        """Moves that create at least two distinct wins on the next turn."""
        forks = []
        for y in range(self.height):
            for x in range(self.width):
                if self.grid[y][x] != EMPTY:
                    continue
                self.grid[y][x] = symbol
                try:
                    next_wins = self.immediate_winning_moves(symbol)
                finally:
                    self.grid[y][x] = EMPTY
                if len(next_wins) >= 2:
                    forks.append((x, y))
        return forks

    def forced_tactical_move(self, my_symbol: int,
                             opponent_symbol: int) -> Tuple[Optional[str], Optional[Tuple[int, int]]]:
        """Handle wins, direct blocks and open-four prevention before JAX."""
        wins = self.immediate_winning_moves(my_symbol)
        if wins:
            return "WIN", self._prefer_tactical_cell(wins)
        blocks = self.immediate_winning_moves(opponent_symbol)
        if blocks:
            return "BLOCK", self._prefer_tactical_cell(blocks)

        # An open three can become an open four with two winning endpoints.
        # Once that happens, blocking only one endpoint is already too late.
        opponent_forks = self.fork_creating_moves(opponent_symbol)
        if opponent_forks:
            return "PREVENT_FOUR", self._prefer_tactical_cell(opponent_forks)

        own_forks = self.fork_creating_moves(my_symbol)
        if own_forks:
            return "CREATE_FORK", self._prefer_tactical_cell(own_forks)
        return None, None

    def _prefer_tactical_cell(self, cells: List[Tuple[int, int]]) -> Tuple[int, int]:
        if self.history:
            anchor_x, anchor_y = self.history[-1][0], self.history[-1][1]
        else:
            anchor_x, anchor_y = self.width // 2, self.height // 2
        return min(
            cells,
            key=lambda cell: (
                abs(cell[0] - anchor_x) + abs(cell[1] - anchor_y),
                abs(cell[0] - self.width // 2) + abs(cell[1] - self.height // 2),
            )
        )

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
        self._moving = False; self._last_move_xy = None; self._move_sent_at = None
        
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
                log.info(f"[JAX] OK! Rule={JAX_RULE} native {self.board.width}x{self.board.height}")
            else: self.ag_available = False; log.warning("[JAX] Start failed!")
            return self.ag_available
        except Exception as e: log.error(f"[JAX] Init error: {e}"); self.ag_available = False; return False

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
        # Keep pending_move=True until the server echoes our MOVE. A table
        # snapshot can arrive before that echo and still say it is our turn.
        self.pending_move = True
        self._last_move_xy = None
        self._move_sent_at = None
        try:
            start = time.time()
            x, y = -1, -1

            tactical_reason, tactical_move = self.board.forced_tactical_move(
                self.my_symbol, self.opponent_symbol)
            if tactical_move is not None:
                x, y = tactical_move
                log.warning(
                    f"[TACTICAL] {tactical_reason} bắt buộc tại ({x},{y}); "
                    "ưu tiên hơn nước JAX")
                # JAX did not produce this move, so its internal tree is stale.
                if self.ag:
                    self.ag.invalidate_sync()
            elif self.ag_available:
                try:
                    history = list(self.board.history)
                    
                    move = await asyncio.get_event_loop().run_in_executor(
                        None, 
                        lambda: self.ag.get_move(history, self.my_symbol)
                    )
                    
                    if (move and 0 <= move[0] < self.board.width and 0 <= move[1] < self.board.height
                        and self.board.get(*move) == EMPTY):
                        x, y = move; self.ag_moves += 1
                    else:
                        self.ag_errors += 1
                        log.warning(f"[JAX] Nước không hợp lệ: {move}; BOARD resync và thử lại một lần")
                        self.ag.invalidate_sync()
                        retry_move = await asyncio.get_event_loop().run_in_executor(
                            None,
                            lambda: self.ag.get_move(history, self.my_symbol)
                        )
                        if (retry_move and 0 <= retry_move[0] < self.board.width
                                and 0 <= retry_move[1] < self.board.height
                                and self.board.get(*retry_move) == EMPTY):
                            x, y = retry_move
                            self.ag_moves += 1
                            log.info(f"[JAX] Resync retry OK: {retry_move}")
                        else:
                            log.warning(f"[JAX] Resync retry failed: {retry_move}; dùng fallback")
                            if history:
                                lx, ly = history[-1][0], history[-1][1]
                            else:
                                lx, ly = 7, 9
                            x, y = self.board.get_empty_near(lx, ly)
                            self.ag_fallback_count += 1
                            self.ag.start_game(
                                my_symbol=self.my_symbol,
                                width=self.board.width,
                                height=self.board.height)
                except Exception as e:
                    self.ag_errors += 1; log.warning(f"[JAX] Error: {e}")
                    try: self.ag.stop(); self.ag = None; self.ag_available = False
                    except Exception: pass
                    if history:
                        lx, ly = history[-1][0], history[-1][1]
                    else:
                        lx, ly = 7, 9
                    x, y = self.board.get_empty_near(lx, ly)
                    self.ag_fallback_count += 1
            else:
                history = self.board.history
                if history:
                    lx, ly = history[-1][0], history[-1][1]
                else:
                    lx, ly = 7, 9
                x, y = self.board.get_empty_near(lx, ly)
                
            elapsed = time.time() - start
            pos = self.board.xy_to_pos(x, y)
            log.info(f"MOVE ({x},{y}) took {elapsed:.2f}s [JAX]")
            await self.send(self.make_play(pos))
            self._last_move_xy = (x, y)
            self._move_sent_at = time.time()
            self.board.put(x, y, self.my_symbol)
        finally:
            self._moving = False
            if self._last_move_xy is None:
                self.pending_move = False
                self._move_sent_at = None

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
            # GET_TABLE_DATA_EX rebuilds history in scan order, so TURN cannot
            # safely reuse the previous MCTS tree after this snapshot.
            if self.ag:
                self.ag.invalidate_sync()
            if self.pending_move and self._last_move_xy is not None:
                px, py = self._last_move_xy
                if self.board.get(px, py) == self.my_symbol:
                    log.info(
                        f"[COORD] Snapshot confirmed sent move ({px},{py})")
                    self.pending_move = False
                    self._last_move_xy = None
                    self._move_sent_at = None
                elif current_player != self.slot and self._move_sent_at is not None:
                    # Turn already advanced, therefore the move was processed;
                    # wait no longer even if this snapshot omitted the echo.
                    log.warning(
                        f"[COORD] Turn advanced without matching MOVE echo; "
                        f"clearing pending sent={self._last_move_xy}")
                    self.pending_move = False
                    self._last_move_xy = None
                    self._move_sent_at = None
            
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
        self._moving = False; self._last_move_xy = None; self._move_sent_at = None
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
            self.ag.start_game(my_symbol=self.my_symbol, width=self.board.width, height=self.board.height)
        
        if self.slot < 0:
            await asyncio.sleep(0.5); await self.send(self.make_get_table())

    async def handle_turn(self, r: BinaryReader):
        sid = r.i8(); r.i16(); r.i16()
        if self.slot < 0: return
        if sid == self.slot and self.is_playing and self.running:
            if not self.pending_move and not self._moving:
                self.pending_move = True; await asyncio.sleep(0.2); await self.do_move()

    async def handle_move(self, r: BinaryReader):
        pos = r.i16(); symbol = r.i8()
        if not (0 <= pos < self.board.width * self.board.height):
            log.warning(
                f"[BOARD] Server sent invalid pos={pos} for "
                f"{self.board.width}x{self.board.height}")
            if self.ag:
                self.ag.invalidate_sync()
            return
        x, y = self.board.pos_to_xy(pos)
        log.info(f"[BOARD] Server MOVE pos={pos} -> ({x},{y}) symbol={symbol}")

        if symbol == self.my_symbol and self.pending_move and self._last_move_xy is not None:
            sent_xy = self._last_move_xy
            if sent_xy == (x, y):
                log.info(f"[COORD] Server confirmed exact sent move {sent_xy}")
            else:
                log.error(
                    f"[COORD] MISMATCH: sent={sent_xy}, server=({x},{y}), pos={pos}")
                if self.ag:
                    self.ag.invalidate_sync()
            self.pending_move = False
            self._last_move_xy = None
            self._move_sent_at = None

        current = self.board.get(x, y)
        if current == symbol:
            pass
        elif current != EMPTY and current != symbol:
            # The server is authoritative. Repair the local cell, but never
            # change my_symbol here: the side is determined only by self.slot.
            log.warning(
                f"[BOARD] Desync at ({x},{y}): local={current}, server={symbol}; "
                "repairing without swapping sides")
            self.board.undo(x, y)
            self.board.put(x, y, symbol)
            if self.ag:
                self.ag.invalidate_sync()
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
            self._move_sent_at = None
            await asyncio.sleep(0.5); await self.send(self.make_get_table())

    async def handle_gameover(self, r: BinaryReader):
        self.is_playing = False; self.pending_move = False
        self._last_move_xy = None; self._move_sent_at = None
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
        self._last_move_xy = None; self._move_sent_at = None
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
        log.info("BOT CARO JAX24 - NATIVE 15x19 + OPEN-FOUR GUARD v4.3")
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
    bin_path = auto_setup_jax()
    if bin_path: print(f"[SETUP] JAX24 ready: {os.path.basename(bin_path)}")
    else: print("[SETUP] No JAX24/Wine - bot uses fallback moves")
    
    try: asyncio.get_running_loop(); loop = asyncio.get_running_loop(); loop.create_task(_run_bot())
    except RuntimeError: asyncio.run(_run_bot())

async def _run_bot():
    try: bot = CaroBot(); await bot.run()
    except KeyboardInterrupt: log.info("[BOT] Stopped by user")
    except Exception as e: log.error(f"[BOT] Error: {e}", exc_info=True)

if __name__ == "__main__": main()
elif 'ipykernel' in sys.modules or 'google.colab' in sys.modules: main()