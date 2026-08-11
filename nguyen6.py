#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════════════════════════╗
║  BOT CARO JAX - nguyen6.py  v5.1                                ║
║  Engine: JAX Gomoku (pbrain-Jax.exe) - StandardCaro rule 8     ║
║  Protocol: Gomoku Cup PBrain (START/BOARD/DONE/TURN)           ║
║  Bàn gamevh: 15×19  |  Engine board: 15×15                     ║
║  Sliding Window 15×15 trượt trên bàn 15×19                    ║
║  Luật: 6+ liên tiếp thắng, 5 chặn 2 đầu không thắng            ║
║  Tích hợp đầy đủ: Binary protocol + Board + GameClient + JAX   ║
║                                                                  ║
║  DOWNLOAD JAX Gomoku:                                            ║
║  - Chính thức: http://download.gomocup.com/ai/JAX25.zip         ║
║  - Mirror   : https://github.com/Gomocup/GomocupDownload/       ║
║               raw/master/2024/JAX24.zip                          ║
║  - Tự động tải nếu chưa có (auto_download_jax)                    ║
║  - Engine: Kailong Jiang - JAX 2025                              ║
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

# ======================== SETUP ========================
REQUIRED = ["websockets", "requests"]
for pkg in REQUIRED:
    try:
        importlib.import_module(pkg)
    except ImportError:
        subprocess.run([sys.executable, "-m", "pip", "install", pkg, "-q", "--break-system-packages"],
                       stderr=subprocess.DEVNULL)
        importlib.import_module(pkg)
import websockets, requests

# ======================== IDENTITY ========================
ADJECTIVES = ["Pro","Dark","Light","Shadow","Ghost","Fire","Ice","Thunder",
              "Silent","Swift","Crazy","Lucky","Mega","Super","Ultra","Hyper",
              "Cyber","Neo","Tech","Alpha","Beta","Zero","Max","King","Queen"]
NOUNS = ["Caro","Gomoku","Master","Storm","Wolf","Dragon","Tiger","Phoenix",
         "Ninja","Samurai","Wizard","Knight","Viper","Hawk","Eagle","Fox"]
def random_full_name():
    return f"{random.choice(ADJECTIVES)}{random.choice(NOUNS)}{random.randint(10,999)}"

# ======================== CONSTANTS ========================
try: _BASE_DIR = Path(__file__).parent
except NameError: _BASE_DIR = Path.cwd()

GAMEVH_W, GAMEVH_H = 15, 19
EMPTY, CIRCLE, CROSS = -1, 0, 1
WS_URL = "wss://gamevh.net/ws/gameServer"
GAME_URL = "https://gamevh.net/play/caro/0"
USER = os.environ.get("CARO_USER1") or os.environ.get("CARO_USER") or "nguyen6"
PASSWD = os.environ.get("CARO_PASSWD1") or os.environ.get("CARO_PASSWD") or "nhat123456"
VERSION = "5.0.2"
RUNTIME = int(os.environ.get("CARO_RUNTIME_SECONDS") or float(os.environ.get("CARO_RUNTIME_HOURS","5.9"))*3600)
BOT_BET_XU = 1000

CMD_MAP = {
    300:"PONG",301:"PING",302:"LOGIN",303:"ALERT",304:"RIBBON_MESSAGE",
    311:"BROADCAST",312:"INVITE",314:"SET_CLIENT_MODE",315:"CONFIG",
    401:"ENTER_PLACE",402:"ENTER_CHILD_PLACE",405:"CREATE_RULE",
    406:"PLAYER_ENTERED",407:"PLAYER_EXITED",410:"KICK_PLAYER",
    413:"LIST_BET_AMT",414:"GET_TABLE_DATA",417:"START_MATCH",
    418:"GAMEOVER",419:"ENTER_STATE",420:"SET_TURN",
    421:"SET_PLAYER_STATUS",422:"SET_PLAYER_POINT",423:"SET_PLAYER_ATTR",
    431:"BALANCE_CHANGED",432:"OWNER_CHANGED",433:"GET_TABLE_DATA_EX",
    434:"SET_READY",501:"BET",502:"PLAY",505:"CHAT",518:"HIGHLIGHT",
    529:"MOVE",533:"ASK_DRAW",534:"SURRENDER",535:"RETREAT",
}

# ======================== JAX ENGINE CONFIG ========================
ENGINE_DIR = _BASE_DIR / "jax-engine" / "JAX25"
ENGINE_BIN = "pbrain-Jax.exe"
ENGINE_RULE = 8  # StandardCaro: 6 thắng, 5 chặn 2 đầu không thắng
ENGINE_TIMEOUT = 2000  # ms
ENGINE_BOARD = 15
# --- Download JAX Gomoku ---
# Nguồn chính thức: Gomocup download page
# http://download.gomocup.com/ai/JAX25.zip  (JAX 2025 - Kailong Jiang)
# Mirror GitHub: https://github.com/Gomocup/GomocupDownload/raw/master/2024/JAX24.zip
JAX_DOWNLOAD_URL = "http://download.gomocup.com/ai/JAX25.zip"
JAX_DOWNLOAD_MIRROR = "https://github.com/Gomocup/GomocupDownload/raw/master/2024/JAX24.zip"
JAX_DOWNLOAD_FALLBACK = "http://download.gomocup.com/ai/JAX24.zip"
JAX_VERSION = "2025"

# ======================== WINE ========================
def find_wine():
    for c in ["wine64","wine"]:
        p = shutil.which(c)
        if p: return p
    portable = _BASE_DIR / "wine-portable" / "wine-9.21-amd64" / "bin" / "wine64"
    return str(portable) if portable.exists() else None

def find_jax_binary() -> Optional[str]:
    """Tìm binary JAX, hỗ trợ nhiều vị trí."""
    candidates = [
        ENGINE_DIR / ENGINE_BIN,
        _BASE_DIR / "jax-engine" / ENGINE_BIN,
        _BASE_DIR / ENGINE_BIN,
        Path("/tmp/jax-engine/JAX25") / ENGINE_BIN,
    ]
    for p in candidates:
        if p.exists():
            return str(p)
    # glob fallback
    for pattern in ["jax-engine/**/pbrain-Jax*", "jax-engine/**/pbrain-jax*", "**/pbrain-Jax.exe"]:
        for f in _BASE_DIR.glob(pattern):
            if f.is_file():
                return str(f)
    return str(ENGINE_DIR / ENGINE_BIN) if (ENGINE_DIR / ENGINE_BIN).exists() else None

def auto_download_jax() -> Optional[str]:
    """Tự động tải JAX gomoku nếu chưa có.
    - Nguồn chính: http://download.gomocup.com/ai/JAX25.zip (JAX 2025)
    - Mirror: GitHub GomocupDownload
    - Giải nén vào ENGINE_DIR, cấp quyền thực thi.
    Trả về đường dẫn binary hoặc None.
    """
    binary_path = ENGINE_DIR / ENGINE_BIN
    if binary_path.exists():
        try: binary_path.chmod(0o755)
        except Exception: pass
        return str(binary_path)
    # thử tìm ở vị trí khác trước khi tải
    found = find_jax_binary()
    if found and Path(found).exists():
        return found

    log.info(f"[JAX] Downloading JAX {JAX_VERSION} ...")
    log.info(f"[JAX] Primary: {JAX_DOWNLOAD_URL}")
    ENGINE_DIR.mkdir(parents=True, exist_ok=True)
    # thư mục tạm giải nén
    tmp_base = _BASE_DIR / "jax-engine"
    tmp_base.mkdir(parents=True, exist_ok=True)
    archive = Path("/tmp/jax25.zip")
    try:
        import zipfile
        downloaded = False
        for url in [JAX_DOWNLOAD_URL, JAX_DOWNLOAD_MIRROR, JAX_DOWNLOAD_FALLBACK]:
            try:
                log.info(f"[JAX] -> {url}")
                req = urllib.request.Request(url, headers={
                    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36",
                    "Accept": "*/*"
                })
                with urllib.request.urlopen(req, timeout=180) as resp:
                    data = resp.read()
                    if len(data) < 10000:
                        raise ValueError(f"File too small: {len(data)} bytes")
                    archive.write_bytes(data)
                log.info(f"[JAX] Tải xong {len(data)//1024}KB")
                downloaded = True
                break
            except Exception as e:
                log.warning(f"[JAX] Download fail {url}: {e}")
                continue
        if not downloaded or not archive.exists():
            log.error("[JAX] Không tải được file zip từ bất kỳ nguồn nào")
            return None

        # Giải nén
        log.info(f"[JAX] Giải nén {archive} -> {tmp_base}")
        with zipfile.ZipFile(archive, "r") as zf:
            # liệt kê để debug
            namelist = zf.namelist()
            log.info(f"[JAX] Zip contains {len(namelist)} files: {namelist[:8]}")
            zf.extractall(str(tmp_base))

        # Tìm binary sau khi giải nén (đệ quy)
        candidates = list(tmp_base.rglob("pbrain-Jax*")) + list(tmp_base.rglob("pbrain-jax*")) + list(tmp_base.rglob("JAX*"))
        # lọc file thực thi
        exe_candidates = [p for p in candidates if p.is_file() and p.suffix.lower() in (".exe","") and "jax" in p.name.lower()]
        if not exe_candidates:
            # thử tìm bất kỳ pbrain*.exe
            exe_candidates = list(tmp_base.rglob("pbrain*.exe"))

        target = None
        for cand in exe_candidates:
            # ưu tiên JAX
            if "jax" in cand.name.lower():
                target = cand
                break
        if not target and exe_candidates:
            target = exe_candidates[0]

        if target and target.exists():
            log.info(f"[JAX] Found binary trong zip: {target}")
            # copy về vị trí chuẩn ENGINE_DIR / ENGINE_BIN
            try:
                ENGINE_DIR.mkdir(parents=True, exist_ok=True)
                if target.resolve() != binary_path.resolve():
                    import shutil as _sh
                    _sh.copy2(str(target), str(binary_path))
                    log.info(f"[JAX] Copy -> {binary_path}")
                binary_path.chmod(0o755)
                try: target.chmod(0o755)
                except: pass
            except Exception as e:
                log.warning(f"[JAX] Copy/chmod fail: {e}")
            # dọn zip
            try: archive.unlink(missing_ok=True)
            except: pass
            if binary_path.exists():
                log.info(f"[JAX] Download OK: {binary_path} ({binary_path.stat().st_size//1024}KB)")
                return str(binary_path)
            return str(target)

        # không tìm thấy binary cụ thể, thử find lại toàn bộ
        fallback = find_jax_binary()
        if fallback and Path(fallback).exists():
            log.info(f"[JAX] Fallback found: {fallback}")
            try: archive.unlink(missing_ok=True)
            except: pass
            return fallback

        log.error(f"[JAX] Giải nén xong nhưng không tìm thấy {ENGINE_BIN} trong {tmp_base}")
        try: archive.unlink(missing_ok=True)
        except: pass
        return None
    except Exception as e:
        log.error(f"[JAX] Download/extract failed: {e}", exc_info=True)
        return None

def ensure_jax_engine() -> Optional[str]:
    """Đảm bảo JAX engine tồn tại, tự tải nếu thiếu. Trả về path hoặc None."""
    p = find_jax_binary()
    if p and Path(p).exists():
        return p
    return auto_download_jax()

# ======================== BINARY PROTOCOL ========================
class BinReader:
    def __init__(self, data: bytes):
        self.d = data; self.p = 0
    def rem(self): return len(self.d) - self.p
    def remaining(self): return len(self.d) - self.p
    def u8(self):
        if self.p >= len(self.d): return 0
        v = self.d[self.p]; self.p += 1; return v
    def i8(self):
        if self.p >= len(self.d): return 0
        v = struct.unpack_from('>b', self.d, self.p)[0]; self.p += 1; return v
    def i16(self):
        if self.p+2 > len(self.d): return 0
        v = struct.unpack_from('>h', self.d, self.p)[0]; self.p += 2; return v
    def u16(self):
        if self.p+2 > len(self.d): return 0
        v = struct.unpack_from('>H', self.d, self.p)[0]; self.p += 2; return v
    def i32(self):
        if self.p+4 > len(self.d): return 0
        v = struct.unpack_from('>i', self.d, self.p)[0]; self.p += 4; return v
    def i64(self):
        if self.p+8 > len(self.d): return 0
        hi = struct.unpack_from('>i', self.d, self.p)[0]
        lo = struct.unpack_from('>I', self.d, self.p+4)[0]
        self.p += 8; return (hi<<32)+lo
    def ascii(self):
        if self.p >= len(self.d): return ""
        n = self.u8(); n = min(n, len(self.d)-self.p)
        s = self.d[self.p:self.p+n].decode('ascii','replace'); self.p += n; return s
    def read_ascii(self): return self.ascii()
    def utf(self):
        if self.p+2 > len(self.d): return ""
        n = self.i16()
        if n <= 0: return ""
        bl = min(n*2, len(self.d)-self.p)
        s = self.d[self.p:self.p+bl].decode('utf-16-be','replace'); self.p += bl; return s
    def read_utf(self): return self.utf()
    def bytes(self):
        if self.p+2 > len(self.d): return []
        n = self.i16(); n = min(n, len(self.d)-self.p)
        r = list(self.d[self.p:self.p+n]); self.p += n; return r
    def read_bytes(self): return self.bytes()
    def cmd(self):
        f = self.i8()
        if f < 0:
            n = -f; n = min(n, len(self.d)-self.p)
            s = self.d[self.p:self.p+n].decode('ascii','replace'); self.p += n; return s
        s = self.u8(); return CMD_MAP.get((f<<8)|s, f"CMD_{(f<<8)|s}")
    def read_command(self): return self.cmd()

class BinWriter:
    def __init__(self): self.p = []
    def u8(self,v): self.p.append(struct.pack('>B',v))
    def i8(self,v): self.p.append(struct.pack('>b',v))
    def i16(self,v): self.p.append(struct.pack('>h',v))
    def i32(self,v): self.p.append(struct.pack('>i',v))
    def ascii(self,s):
        e = s.encode('ascii','replace'); self.u8(len(e)); self.p.append(e)
    def write_ascii(self,s): self.ascii(s)
    def utf(self,s):
        e = s.encode('utf-16-be'); self.i16(len(e)//2); self.p.append(e)
    def write_utf(self,s): self.utf(s)
    def cmd(self,c):
        cid = next((k for k,v in CMD_MAP.items() if v==c), None)
        if cid: self.p.append(struct.pack('>H',cid))
        else: b=c.encode('ascii'); self.i8(-len(b)); self.p.append(b)
    def write_command(self,c): self.cmd(c)
    def build(self): return b''.join(self.p)

# Alias cho tương thích
BinaryReader = BinReader
BinaryWriter = BinWriter

# ======================== BOARD ========================
class Board:
    def __init__(self, w=15, h=19):
        self.w = w; self.h = h
        self.width = w; self.height = h
        self.grid = [[EMPTY]*w for _ in range(h)]
        self.hist = []; self.history = self.hist
        self.placed = set()
    def resize(self, w, h):
        self.w = w; self.h = h
        self.width = w; self.height = h
        self.grid = [[EMPTY]*w for _ in range(h)]
        self.hist.clear(); self.placed.clear()
        self.history = self.hist
    def get(self, x, y):
        if 0<=x<self.w and 0<=y<self.h: return self.grid[y][x]
        return EMPTY
    def put(self, x, y, s):
        if self.get(x,y)==EMPTY and 0<=x<self.w and 0<=y<self.h:
            self.grid[y][x]=s; self.hist.append((x,y,s)); self.placed.add((x,y))
    def undo(self, x, y):
        if 0<=x<self.w and 0<=y<self.h:
            self.grid[y][x]=EMPTY
            if self.hist and self.hist[-1][:2]==(x,y): self.hist.pop()
            self.placed.discard((x,y))
    def pos2xy(self, pos): return pos%self.w, pos//self.w
    def pos_to_xy(self, pos): return self.pos2xy(pos)
    def xy2pos(self, x, y): return y*self.w+x
    def xy_to_pos(self, x, y): return self.xy2pos(x,y)
    def load_rle(self, data):
        self.grid = [[EMPTY]*self.w for _ in range(self.h)]
        self.hist.clear(); self.placed.clear(); pos = 0
        for v in data:
            sym = v-256 if v>127 else v
            if sym >= 0:
                y,x = pos//self.w, pos%self.w
                if 0<=x<self.w and 0<=y<self.h:
                    self.grid[y][x]=sym; self.placed.add((x,y))
                pos += 1
            else: pos += -sym
        for y in range(self.h):
            for x in range(self.w):
                s = self.grid[y][x]
                if s >= 0: self.hist.append((x,y,s))
    def empty_near(self, x0, y0):
        for r in range(15):
            for dx in range(-r,r+1):
                for dy in range(-r,r+1):
                    x,y = x0+dx, y0+dy
                    if 0<=x<self.w and 0<=y<self.h and self.grid[y][x]==EMPTY:
                        return (x,y)
        return (self.w//2, self.h//2)
    def get_empty_near(self, x0, y0): return self.empty_near(x0, y0)
    def get_empty_near_center(self):
        return self.empty_near(self.w//2, self.h//2)

# ======================== SLIDING WINDOW ========================
class SlidingWindow:
    """Cửa sổ trượt 15×15 trên bàn 15×19.
    Theo dõi nước đi, dịch chuyển khi nước đi gần biên cửa sổ.
    Không reset giữa các ván - giữ context liên tục.
    """
    SIZE = 15
    MAX_OFF = GAMEVH_H - SIZE  # 4

    def __init__(self):
        self.off = 2  # y_offset: dòng đầu cửa sổ trong bàn gamevh
        self._ys = []  # lịch sử Y tất cả nước đi (không reset giữa ván)

    def reset(self):
        """Soft reset - chỉ xóa lịch sử, giữ offset."""
        self._ys.clear()

    def hard_reset(self):
        """Hard reset - về giữa."""
        self.off = 2; self._ys.clear()

    def _recalc(self):
        if not self._ys: return
        avg = sum(self._ys) / len(self._ys)
        new = max(0, min(self.MAX_OFF, int(round(avg - 7))))
        if new != self.off:
            log.info(f"[WIN] offset {self.off}→{new} (avg_y={avg:.1f}, n={len(self._ys)})")
            self.off = new

    def update(self, gamevh_y: int) -> bool:
        """Thêm nước đi, tính lại offset. Trả True nếu cửa sổ di chuyển."""
        self._ys.append(gamevh_y)
        old = self.off; self._recalc()
        return self.off != old

    def to_eng(self, gx, gy):
        """gamevh → engine"""
        return gx, gy - self.off

    def to_gvh(self, ex, ey):
        """engine → gamevh"""
        return ex, ey + self.off

    def in_window(self, gy):
        return 0 <= gy - self.off < self.SIZE

    def eng_y_valid(self, ey):
        return 0 <= ey < self.SIZE

    def range_str(self):
        return f"y={self.off}..{self.off+self.SIZE-1}"

# ======================== JAX ENGINE (PBrain) ========================
class JaxEngine:
    """Wrapper cho pbrain-Jax.exe qua Wine.
    Protocol: INFO / START / BOARD / DONE -> x,y
    Rule 8 = StandardCaro (Freestyle Gomoku với luật 5 chặn 2 đầu)
    """
    def __init__(self, timeout_turn=ENGINE_TIMEOUT, board_size=ENGINE_BOARD, rule=ENGINE_RULE):
        self.binary = find_jax_binary() or str(ENGINE_DIR / ENGINE_BIN)
        self.timeout_turn = timeout_turn
        self.board_size = board_size
        self.rule = rule
        self.wine = find_wine()
        self.proc = None
        self.sel = None
        self._selector = None
        self.buf = bytearray()
        self._buffer = self.buf
        self.win = SlidingWindow()
        self.ok = False
        self._initialized = False
        self.my = 1
        self.my_side = 1

    def start(self, my_sym=1):
        """Khởi động engine mới. Trả True nếu OK. Tự tải nếu thiếu."""
        self.stop()
        if not self.wine:
            log.warning("[JAX] Không tìm thấy wine! (cần wine để chạy .exe)")
            return False
        # Đảm bảo binary tồn tại, tự tải nếu thiếu
        resolved = ensure_jax_engine()
        if resolved:
            self.binary = resolved
        else:
            resolved = find_jax_binary()
            if resolved:
                self.binary = resolved
        if not Path(self.binary).exists():
            log.warning(f"[JAX] Binary không tồn tại: {self.binary}")
            log.warning(f"[JAX] Thử tải thủ công: JAX25.zip từ {JAX_DOWNLOAD_URL}")
            auto = auto_download_jax()
            if auto and Path(auto).exists():
                self.binary = auto
            else:
                return False
        import os as _os
        env = _os.environ.copy()
        env["WINEPREFIX"] = str(_BASE_DIR / ".wine")
        env["WINEDEBUG"] = "-all"
        try:
            self.proc = subprocess.Popen(
                [self.wine, self.binary],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                cwd=str(Path(self.binary).parent), env=env)
            self.buf = bytearray()
            self._buffer = self.buf
            self.my = my_sym
            self.my_side = my_sym
            self._init_sel()
            time.sleep(2)
            self._drain()
            self._send(f"INFO rule {self.rule}")
            self._send(f"START {self.board_size}")
            ok = False
            for _ in range(10):
                line = self._read(1.0)
                if line.upper() == "OK":
                    ok = True
                    break
            if not ok:
                log.warning("[JAX] START không trả OK, thử tiếp...")
            self._send(f"INFO timeout_turn {self.timeout_turn}")
            self._send("INFO timeout_match 1000000")
            self._send("INFO time_left 1000000")
            self._send("INFO ponder 1")
            time.sleep(0.1)
            self._drain()
            self.ok = True
            self._initialized = True
            log.info(f"[JAX] Started rule={self.rule} board={self.board_size} via {self.wine}")
            return True
        except Exception as e:
            log.error(f"[JAX] Start err: {e}")
            self.ok = False
            return False

    def start_game(self, my_symbol=1):
        """Tương thích API cũ (AlphaGomoku)."""
        if self.proc and self.proc.poll() is None and self._initialized:
            return self.restart()
        return self.start(my_symbol)

    def restart(self):
        """RESTART engine giữa các ván, giữ window offset nhưng clear history."""
        if not self.proc or self.proc.poll() is not None:
            return self.start(self.my)
        self.win.reset()
        try:
            self._send("RESTART")
            ok = False
            for _ in range(5):
                line = self._read(2.0)
                if line.upper() == "OK":
                    ok = True
                    break
            # Sau RESTART cần START lại
            self._send(f"INFO rule {self.rule}")
            self._send(f"START {self.board_size}")
            for _ in range(5):
                line = self._read(2.0)
                if line.upper() == "OK":
                    ok = True
                    break
            self._send(f"INFO timeout_turn {self.timeout_turn}")
            self._send("INFO ponder 1")
            time.sleep(0.1)
            self._drain()
            self.ok = True
            return True
        except Exception as e:
            log.warning(f"[JAX] RESTART err: {e}")
            return self.start(self.my)

    def restart_game(self):
        return self.restart()

    def get_move(self, hist: list, my: int) -> Optional[Tuple[int,int]]:
        """hist: [(x,y,sym)] tọa độ gamevh. Trả (x,y) gamevh hoặc None."""
        if not self.ok or not self.proc or self.proc.poll() is not None:
            log.warning("[JAX] Engine not ready")
            return None
        self._drain()
        self._send(f"INFO timeout_turn {self.timeout_turn}")
        self._send(f"INFO time_left {self.timeout_turn*20}")
        # Nếu hist rỗng -> engine đi trước, dùng BEGIN
        if not hist:
            self._send("BEGIN")
            deadline = time.monotonic() + (self.timeout_turn/1000.0) + 5.0
            while time.monotonic() < deadline:
                line = self._read(min(1.0, deadline-time.monotonic()))
                if not line:
                    continue
                if line.startswith(("MESSAGE","DEBUG","ERROR","UNKNOWN","INFO")):
                    continue
                if "," in line:
                    parts = line.split(",")
                    if len(parts) == 2:
                        try:
                            ex,ey = int(parts[0].strip()), int(parts[1].strip())
                        except:
                            continue
                        if 0<=ex<self.board_size and 0<=ey<self.board_size:
                            gx,gy = self.win.to_gvh(ex,ey)
                            if 0<=gx<GAMEVH_W and 0<=gy<GAMEVH_H:
                                return gx,gy
            return None

        # Gửi toàn bộ bàn qua BOARD (chuẩn PBM)
        self._send("BOARD")
        sent = 0
        for (x,y,sym) in hist:
            if not self.win.in_window(y):
                continue
            # sym mapping: engine 1 = mình, 2 = đối thủ
            c = 1 if sym == self.my else 2
            ex,ey = self.win.to_eng(x,y)
            if 0<=ex<self.board_size and 0<=ey<self.board_size:
                self._send(f"{ex},{ey},{c}")
                sent += 1
        self._send("DONE")

        deadline = time.monotonic() + (self.timeout_turn/1000.0) + 5.0
        while time.monotonic() < deadline:
            line = self._read(min(1.0, deadline-time.monotonic()))
            if not line:
                continue
            up = line.upper()
            if line.startswith(("MESSAGE","DEBUG","ERROR","UNKNOWN")):
                log.info(f"[JAX] {line}")
                continue
            if up.startswith("SUGGEST"):
                continue
            if "," in line:
                parts = line.split(",")
                if len(parts) == 2:
                    try:
                        ex,ey = int(parts[0].strip()), int(parts[1].strip())
                    except:
                        continue
                    if 0<=ex<self.board_size and 0<=ey<self.board_size:
                        gx,gy = self.win.to_gvh(ex,ey)
                        if 0<=gx<GAMEVH_W and 0<=gy<GAMEVH_H:
                            return gx,gy
                        else:
                            log.warning(f"[JAX] Move out of gvH: engine({ex},{ey}) -> gvh({gx},{gy}) off={self.win.off}")
                            continue
        log.warning(f"[JAX] Timeout sau {self.timeout_turn}ms, sent={sent}")
        return None

    def stop(self):
        if self.proc:
            try:
                self._send("END")
            except:
                pass
            try:
                self.proc.terminate()
                self.proc.wait(3)
            except:
                try:
                    self.proc.kill()
                except:
                    pass
            self.proc = None
            self.ok = False
            self._initialized = False
        self._close_sel()

    def _stop_unlocked(self):
        self.stop()

    def _init_sel(self):
        self._close_sel()
        if self.proc and self.proc.stdout:
            try:
                self.sel = selectors.DefaultSelector()
                self._selector = self.sel
                self.sel.register(self.proc.stdout, selectors.EVENT_READ)
            except:
                self.sel = None
                self._selector = None

    def _close_sel(self):
        if self.sel:
            try:
                self.sel.close()
            except:
                pass
            self.sel = None
        if self._selector and self._selector is not self.sel:
            try:
                self._selector.close()
            except:
                pass
            self._selector = None

    def _send(self, cmd):
        if self.proc and self.proc.poll() is None:
            try:
                self.proc.stdin.write((cmd+"\n").encode())
                self.proc.stdin.flush()
                # log.debug(f">> {cmd}")
            except:
                pass

    def _read(self, timeout=10.0):
        if not self.proc or self.proc.poll() is not None:
            return ""
        deadline = time.monotonic() + timeout
        while True:
            idx = self.buf.find(b"\n")
            if idx >= 0:
                line = bytes(self.buf[:idx]).strip()
                del self.buf[:idx+1]
                try:
                    decoded = line.decode("utf-8", errors="replace").strip()
                except:
                    decoded = ""
                if decoded:
                    # log.debug(f"<< {decoded}")
                    pass
                return decoded
            rem = deadline - time.monotonic()
            if rem <= 0:
                return ""
            try:
                if self.sel:
                    ready = self.sel.select(timeout=min(rem,1.0))
                else:
                    s = selectors.DefaultSelector()
                    s.register(self.proc.stdout, selectors.EVENT_READ)
                    ready = s.select(timeout=min(rem,1.0))
                    s.close()
                if ready:
                    chunk = os.read(self.proc.stdout.fileno(), 4096)
                    if not chunk:
                        return ""
                    self.buf.extend(chunk)
            except:
                return ""

    def _read_line(self, timeout=10.0):
        return self._read(timeout)

    def _drain(self):
        while self._read(0.01):
            pass
    def _drain_output(self):
        self._drain()

# Alias cho tương thích code cũ
AlphaGomokuEngine = JaxEngine

# ======================== GAME CLIENT ========================
class GameClient:
    """WebSocket client cho gamevh.net - xử lý protocol, login, table."""
    def __init__(self):
        self.ws = None; self.board = Board()
        self.slot = -1; self.my_sym = CROSS; self.opp_sym = CIRCLE
        self.my_symbol = self.my_sym; self.opponent_symbol = self.opp_sym
        self.is_playing = False; self.in_table = False; self.ready = False
        self.players = {}; self.nick = ""; self.nickname = ""
        self.token = 0; self.cookie = ""
        self.place_path = "Lobby.caro.0"; self.lock_key = ""
        self.start_time = None; self.last_act = time.time(); self._running = True
        self.last_activity = self.last_act
        self.wins = 0; self.losses = 0; self.draws = 0; self.games = 0
        self.total_games = 0
        self.pending = False; self.pending_move = False
        self.bet_amts = []; self._bet_id = None; self._resolved_bet_id = None
        self._bet_loaded = False; self._bet_amts_loaded = False
        self._joining = False; self._joining_table = False
        self.table_id = None; self._slot_by_pid = {}; self.player_slot_by_id = self._slot_by_pid
        self.opp_gone_at = None; self.opponent_gone_at = None
        self._tbl_lost_at = None; self._table_lost_at = None
        self._want_rejoin = False; self._rejoining = False; self._rejoin_n = 0; self._rejoin_attempts = 0
        self._id_done = False; self._identity_attempted = False
        self._moving = False; self._last_xy = None; self._last_move_xy = None
        self.identity_result = {}

    @property
    def running(self): return self._running
    def stop(self): self._running = False

    def update_sym(self):
        self.my_sym = CIRCLE if self.slot==0 else CROSS
        self.opp_sym = CROSS if self.my_sym==CIRCLE else CIRCLE
        self.my_symbol = self.my_sym; self.opponent_symbol = self.opp_sym

    def update_symbols(self): self.update_sym()

    # --- Packet builders ---
    def pkt_login(self):
        w = BinWriter(); w.cmd("LOGIN"); w.ascii(self.nick or self.nickname)
        w.i32(self.token); w.ascii(VERSION); w.ascii(self.lock_key)
        w.ascii("caro"); w.i8(1); return w.build()
    def make_login(self): return self.pkt_login()
    def pkt_enter(self, path, pw="", mode=1):
        w = BinWriter(); w.cmd("ENTER_PLACE"); w.ascii(path); w.utf(pw); w.i8(mode); return w.build()
    def make_enter(self, path, pw="", mode=1): return self.pkt_enter(path, pw, mode)
    def pkt_list_bet(self):
        w = BinWriter(); w.cmd("LIST_BET_AMT"); return w.build()
    def make_list_bet_amt(self): return self.pkt_list_bet()
    def pkt_create(self):
        bid = self._bet_id if self._bet_id is not None else self._resolve_bet()
        if bid is None: bid = 0
        args = [("matchDuration","0"),("turnDuration","60"),("accDuration","0"),("blockSoftware","0")]
        w = BinWriter(); w.cmd("CREATE_RULE"); w.i8(bid); w.i8(len(args))
        for n,v in args: w.ascii(n); w.utf(v)
        return w.build()
    def make_create_rule(self): return self.pkt_create()
    def pkt_table(self):
        w = BinWriter(); w.cmd("GET_TABLE_DATA_EX"); w.ascii(""); return w.build()
    def make_get_table(self): return self.pkt_table()
    def pkt_play(self, pos):
        w = BinWriter(); w.cmd("PLAY"); w.i16(pos); return w.build()
    def make_play(self, pos): return self.pkt_play(pos)
    def pkt_pong(self):
        w = BinWriter(); w.cmd("PONG"); return w.build()
    def make_pong(self): return self.pkt_pong()
    def pkt_ready(self):
        if self.is_playing: return b''
        w = BinWriter(); w.cmd("SET_READY"); return w.build()
    def make_ready(self): return self.pkt_ready()

    def _resolve_bet(self):
        if not self.bet_amts: return None
        for b in self.bet_amts:
            v = b.get('v', b.get('value'))
            if v==BOT_BET_XU: return b.get('id')
        lo = [b for b in self.bet_amts if 0 < b.get('v', b.get('value',0)) <= BOT_BET_XU]
        return max(lo,key=lambda x: x.get('v', x.get('value')))['id'] if lo else 0
    def resolve_bet_amt_id(self): return self._resolve_bet()

    async def send(self, data):
        if self.ws and data:
            try: await self.ws.send(data)
            except: pass

    async def create_table(self):
        await self.create_new_table()
    async def create_new_table(self):
        if not self._bet_loaded and not self._bet_amts_loaded:
            self._bet_loaded = False; await self.send(self.pkt_list_bet())
        else:
            await self.send(self.pkt_create())

    # --- Message handlers ---
    async def handle(self, raw):
        r = BinReader(raw); c = r.cmd()
        if c != "PING": log.info(f"RECV {c}")
        self.last_act = time.time(); self.last_activity = self.last_act
        try:
            if c=="PING": await self.send(self.pkt_pong())
            elif c=="LOGIN": await self._h_login(r)
            elif c=="ENTER_PLACE": await self._h_enter(r)
            elif c=="LIST_BET_AMT": await self._h_bet(r)
            elif c=="CREATE_RULE": await self._h_create(r)
            elif c=="GET_TABLE_DATA_EX": await self._h_table(r)
            elif c=="START_MATCH": await self._h_start(r)
            elif c=="SET_TURN": await self._h_turn(r)
            elif c=="MOVE": await self._h_move(r)
            elif c=="GAMEOVER": await self._h_gameover(r)
            elif c=="PLAY": await self._h_play(r)
            elif c=="KICK_PLAYER": await self._h_kick(r)
            elif c=="PLAYER_ENTERED": await self._h_penter(r)
            elif c=="PLAYER_EXITED": await self._h_pexit(r)
        except Exception as e: log.error(f"Err {c}: {e}", exc_info=True)

    async def _h_login(self, r):
        st = r.i8()
        if st == 0:
            p = r.utf()
            if p == "REFRESH":
                ok = await asyncio.get_event_loop().run_in_executor(None, self.http_login)
                if ok: await self.send(self.pkt_login())
                return
            if r.rem() > 0: self.lock_key = r.ascii()
            await self.send(self.pkt_enter(self.place_path))

    async def _h_enter(self, r):
        st = r.i8()
        if st == 0:
            if self._joining or self._joining_table:
                self._joining = False; self._joining_table=False
                self._rejoining = False; self.in_table = True
                await asyncio.sleep(0.3); await self.send(self.pkt_table())
            elif not self.in_table:
                if self._want_rejoin and self.table_id:
                    self._want_rejoin = False; self._rejoining = True; self._joining = True; self._joining_table=True
                    await self.send(self.pkt_enter(f"{self.place_path}.{self.table_id}"))
                else:
                    self._bet_loaded = False; self._bet_amts_loaded=False
                    self._bet_id = None; self._resolved_bet_id=None
                    await self.send(self.pkt_list_bet())
        elif self._joining or self._joining_table:
            self._joining = False; self._joining_table=False
            if self._rejoining:
                self._rejoining = False; self._rejoin_n += 1; self._rejoin_attempts+=1; self.table_id = None
                await asyncio.sleep(1); await self.send(self.pkt_list_bet())
            else:
                await asyncio.sleep(1); await self.send(self.pkt_create())

    async def _h_bet(self, r):
        if r.i8() != 0: return
        n = r.i8()
        self.bet_amts = [{"id":i,"value":r.i32(),"v":0} for i in range(n)]
        # r đã đọc qua, cần đọc lại? Thực tế loop trên đã đọc sai vì đọc tuần tự
        # Fix: đọc lại đúng - ở trên đã đọc i32 từng cái, nhưng dùng list comp gây lỗi double read
        # Làm lại đơn giản: đã đọc xong, gán v = value
        for b in self.bet_amts:
            b["v"] = b["value"]
        self._bet_id = self._resolve_bet(); self._resolved_bet_id=self._bet_id
        self._bet_loaded = True; self._bet_amts_loaded=True
        await self.send(self.pkt_create())

    async def _h_create(self, r):
        if r.i8() == 0:
            self.table_id = r.ascii(); self._rejoin_n = 0; self._rejoin_attempts=0
            log.info(f"[CREATE] Bàn mới id={self.table_id}")
            await asyncio.sleep(0.5); self._joining = False; self._joining_table=False
            await self.send(self.pkt_table())

    async def _h_table(self, r):
        try:
            fb = r.i8()
            if fb != 0:
                if "not in table" in r.utf().lower():
                    self.in_table = False; self.table_id = None; await self.create_table()
                return
            for _ in range(r.u8()):
                r.u8(); r.ascii(); r.u8()
                for _ in range(r.u8()): r.u8(); r.ascii(); r.utf(); r.u8(); r.u8()
            r.u8(); self.slot = r.i8(); playing = r.u8()==1
            pc = r.u8(); self.players = {}; self._slot_by_pid = {}; self.player_slot_by_id=self._slot_by_pid
            for _ in range(pc):
                sid=r.i8(); pid=r.i64(); name=r.utf()
                r.u16(); r.ascii(); r.i8(); r.i64(); r.i64(); r.i64(); r.u8(); r.u8()
                self.players[sid] = {'name':name}; self._slot_by_pid[pid] = sid
            cur = r.i8(); r.i16(); r.i16(); r.u8(); self.in_table = True
            for _ in range(r.u8()): r.i8(); r.i32()
            w=r.u8(); h=r.u8(); self.board.resize(w,h)
            r.i16(); self.board.load_rle(r.bytes()); self.update_sym()
            r.u8(); r.u8()
            for _ in range(r.u8()): r.ascii(); r.utf()
            has_opp = any(s>=0 and s!=self.slot for s in self.players)
            self.is_playing = playing
            log.info(f"[TBL] Slot={self.slot} Play={playing} Turn=slot{cur} Opp={'yes' if has_opp else 'no'}")
            # Callback cho subclass
            await self.on_table_update()
            if playing and cur == self.slot:
                if not self._moving and not self.pending and not self.pending_move:
                    self.pending = True; self.pending_move=True; await self.do_move()
            elif not playing and self.slot >= 0:
                if has_opp:
                    if not self.ready:
                        log.info("[BOT] Đối thủ ngồi ghế. Ready!")
                        self.ready = True; await self.send(self.pkt_ready())
                else: self.ready = False
            elif not playing and self.slot < 0:
                self.in_table = False; self.table_id = None
                await asyncio.sleep(1); await self.send(self.pkt_list_bet())
            self._rejoining = False
        except Exception as e: log.error(f"Table err: {e}", exc_info=True)

    async def _h_start(self, r):
        self.games += 1; self.total_games+=1; self.is_playing = True; self.ready = False
        self.pending = False; self.pending_move=False; self._moving = False; self._last_xy = None; self._last_move_xy=None
        self.opp_gone_at = None; self.opponent_gone_at=None
        for _ in range(r.u8()): r.i8(); r.i32()
        w=r.u8(); h=r.u8(); self.board.resize(w,h)
        r.i16(); self.board.load_rle(r.bytes()); self.update_sym()
        log.info(f"=== GAME {self.games} === Me={'X' if self.my_sym==CROSS else 'O'} Slot={self.slot}")
        await self.on_game_start()
        if self.slot < 0:
            await asyncio.sleep(0.5); await self.send(self.pkt_table())

    async def _h_turn(self, r):
        sid=r.i8(); r.i16(); r.i16()
        if self.slot < 0: return
        if sid==self.slot and self.is_playing and self.running:
            if not self.pending and not self.pending_move and not self._moving:
                self.pending = True; self.pending_move=True; await asyncio.sleep(1.2); await self.do_move()

    async def _h_move(self, r):
        pos=r.i16(); sym=r.i8()
        x,y = self.board.pos2xy(pos)
        cur = self.board.get(x,y)
        if cur == sym:
            if sym==self.my_sym and self._last_xy: self._last_xy = None; self._last_move_xy=None
        elif cur != EMPTY and cur != sym:
            self.my_sym = sym; self.opp_sym = CROSS if sym==CIRCLE else CIRCLE
            self.my_symbol=self.my_sym; self.opponent_symbol=self.opp_sym
            self.board.undo(x,y); self.board.put(x,y,sym)
        else:
            self.board.put(x,y,sym)
            await self.on_move(x, y, sym)

    async def _h_play(self, r):
        if r.i8() != 0:
            log.warning(f"PLAY err"); self.pending = False; self.pending_move=False
            if self._last_xy: self.board.undo(*self._last_xy); self._last_xy = None; self._last_move_xy=None
            await asyncio.sleep(0.5); await self.send(self.pkt_table())

    async def _h_gameover(self, r):
        self.is_playing = False; self.pending = False; self.pending_move=False; self.opp_gone_at = None; self.opponent_gone_at=None
        res = None
        for _ in range(r.u8()):
            sid=r.i8(); result=r.i8(); r.i64()
            if sid==self.slot: res = result
        if res in (1,11): self.wins += 1; log.info(">>> WIN! <<<")
        elif res in (2,4,12): self.losses += 1; log.info(">>> LOSE! <<<")
        else: self.draws += 1; log.info(">>> DRAW! <<<")
        r.utf(); self.save_stats()
        if self._tbl_lost_at is not None or self._table_lost_at is not None:
            self._tbl_lost_at = None; self._table_lost_at=None; await asyncio.sleep(1.5); await self.create_table(); return
        log.info("[BOT] Ở lại bàn, ready sau 5s...")
        asyncio.create_task(self._delay_ready(5))

    async def _h_kick(self, r):
        r.i8(); r.utf()
        self.is_playing = False; self.in_table = False; self.pending = False; self.pending_move=False
        self.table_id = None; await asyncio.sleep(1); await self.create_table()

    async def _delay_ready(self, d):
        await asyncio.sleep(d)
        if not self.is_playing and self.in_table: await self.send(self.pkt_table())

    async def _h_penter(self, r):
        lv=r.i8(); pid=r.i64(); name=r.utf()
        if r.rem()>=36: r.i64(); r.i64(); r.ascii(); r.i32(); r.i32(); r.i8(); r.i64(); r.i8()
        if lv < 4: return
        log.info(f"[BOT] {name} vào bàn"); await self.send(self.pkt_table())

    async def _h_pexit(self, r):
        lv=r.i8(); pid=r.i64() if r.rem()>=8 else -1
        if lv < 4: return
        slot = self._slot_by_pid.get(pid) if pid>=0 else None
        if pid>=0: self._slot_by_pid.pop(pid, None)
        if slot is not None and slot == self.slot:
            if self.is_playing: self.in_table = False; self._tbl_lost_at = time.time(); self._table_lost_at=self._tbl_lost_at
            else: self.in_table = False; await asyncio.sleep(1); await self.create_table()
        elif self.is_playing:
            if self.opp_gone_at is None:
                self.opp_gone_at = time.time(); self.opponent_gone_at=self.opp_gone_at; log.info("[BOT] Đối thủ rời giữa ván")
        elif self.in_table:
            await self.send(self.pkt_table())

    # --- Overrides cho subclass ---
    async def on_table_update(self): pass
    async def on_game_start(self): pass
    async def on_move(self, x, y, sym): pass
    async def do_move(self): pass

    # --- Watchdog ---
    async def watchdog(self):
        while self.running:
            try: await asyncio.sleep(10)
            except asyncio.CancelledError: return
            if not self.running: return
            if self.start_time and time.time()-self.start_time > RUNTIME:
                self.save_stats(); self.stop(); return
            if not self.ws or self.ws.close_code is not None: continue
            try:
                if self.opp_gone_at and self.is_playing and time.time()-self.opp_gone_at > 15:
                    self.opp_gone_at = None; self.opponent_gone_at=None; await self.send(self.pkt_table())
                if self._tbl_lost_at and time.time()-self._tbl_lost_at > 8:
                    self._tbl_lost_at = None; self._table_lost_at=None; self.table_id = None; await self.create_table()
                if not self.is_playing and not self.in_table and not self._joining and not self._joining_table and not self._rejoining and (self._bet_loaded or self._bet_amts_loaded):
                    await self.send(self.pkt_create())
            except: pass

    # --- HTTP Login ---
    def http_login(self):
        try:
            s = requests.Session()
            ua = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/139.0 Safari/537.36"
            s.headers.update({'User-Agent':ua,'Accept-Language':'vi-VN,vi;q=0.9,en;q=0.7'})
            s.get('https://gamevh.net/login.jsp', timeout=10)
            r = s.post('https://gamevh.net/login.jsp', timeout=10,
                data={'redirect':'/','USER_NAME':USER,'PASSWORD':PASSWD,'AUTO_LOGIN':'true','LOGIN':'Đăng nhập'},
                headers={'Origin':'https://gamevh.net','Referer':'https://gamevh.net/login.jsp',
                         'Content-Type':'application/x-www-form-urlencoded'}, allow_redirects=True)
            if 'login.jsp' in r.url: log.error('[BOT] Login fail'); return False
            if not self._id_done:
                self._id_done = True; self._identity_attempted=True
                self._update_identity(s)
            gr = s.get(GAME_URL, timeout=10)
            self.cookie = '; '.join(f'{k}={v}' for k,v in s.cookies.items())
            tm = re.search(r'var\s+token\s*=\s*(-?\d+)', gr.text)
            nm = re.search(r"var\s+currentPlayerNickName\s*=\s*'([^']+)'", gr.text)
            pm = re.search(r'var\s+placePath\s*=\s*\"([^\"]+)\"', gr.text)
            if not tm or not nm: log.error('[BOT] Token/nick not found'); return False
            self.token = int(tm.group(1)); self.nick = nm.group(1); self.nickname=self.nick
            if pm: self.place_path = pm.group(1)
            log.info(f'[BOT] Login OK: {self.nick}')
            return True
        except Exception as e: log.error(f'[BOT] Login err: {e}'); return False

    def _update_identity(self, s):
        try:
            eu = 'https://gamevh.net/com/ftl/game/profile/update_profile.jsp'
            nn = random_full_name()
            pg = s.get(eu, timeout=15, allow_redirects=True)
            act, data = self._read_form(pg.text, pg.url)
            if not act or not data: return
            old = data.get('FULL_NAME','')
            data['FULL_NAME'] = nn; data['OLD_PASSWORD'] = PASSWD; data['SAVE'] = '\uf046'
            s.post(act, timeout=20, data=data,
                headers={'Origin':'https://gamevh.net','Referer':pg.url,
                         'Content-Type':'application/x-www-form-urlencoded'}, allow_redirects=True)
            vp = s.get(eu, timeout=15, allow_redirects=True)
            _, vd = self._read_form(vp.text, vp.url)
            if (vd or {}).get('FULL_NAME') == nn:
                log.info(f'[ID] FULL_NAME: {old!r} → {nn!r}')
            pu = 'https://gamevh.net/com/ftl/game/profile/player_profile.jsp'
            bp = s.get(pu, timeout=15)
            oa = self._get_avatar(bp.text)
            cat = []
            seen = set()
            pat = re.compile(r'''buyAvatar\(\s*([\"']?)(\d+)\1\s*,\s*([\"'])(.*?)\3\s*,\s*([\"']?)([\d,.]+)\5\s*\)''', re.I|re.S)
            for ci in range(1,7):
                pg = s.get(f'https://gamevh.net/com/ftl/game/profile/avatar_by_category.jsp?excludeLayout=true&category_id={ci}', timeout=15)
                for m in pat.finditer(pg.text):
                    aid = int(m.group(2))
                    if aid in seen: continue
                    seen.add(aid)
                    cat.append({'id':aid,'name':html_lib.unescape(m.group(4)),'cost':int(re.sub(r'[^0-9]','',m.group(6)) or '0')})
            ch = [c for c in cat if c['id']!=oa]
            if not ch: return
            sel = random.choice(ch)
            s.post(f'https://gamevh.net/com/ftl/game/profile/update_avatar.jsp?pk={sel["id"]}&redirect=/',
                timeout=20, headers={'Origin':'https://gamevh.net','Referer':'https://gamevh.net/com/ftl/game/profile/avatar.jsp'},
                allow_redirects=True)
            ap = s.get(pu, timeout=15)
            na = self._get_avatar(ap.text)
            if na == sel['id']:
                log.info(f'[ID] Avatar: builtin{oa} → builtin{na}')
        except Exception as e: log.warning(f'[ID] Error: {e}')

    @staticmethod
    def _read_form(html, url):
        fm = re.search(r'(?is)<form\b[^>]*name=[\"\\\']InputForm0[\"\\\'][^>]*>.*?</form>', html)
        if not fm: return None, None
        form = fm.group(0)
        ot = re.search(r'(?is)<form\b[^>]*>', form).group(0)
        ma = re.search(r'\baction\s*=\s*([\"\\\'])(.*?)\1', ot, re.I|re.S)
        act = urljoin(url, html_lib.unescape(ma.group(2))) if ma else url
        data = {}
        for tag in re.findall(r'(?is)<input\b[^>]*>', form):
            nm = re.search(r'\bname\s*=\s*([\"\\\'])(.*?)\1', tag, re.I|re.S)
            tp = re.search(r'\btype\s*=\s*([\"\\\'])(.*?)\1', tag, re.I|re.S)
            vl = re.search(r'\bvalue\s*=\s*([\"\\\'])(.*?)\1', tag, re.I|re.S)
            if not nm: continue
            t = (tp.group(2) if tp else '').lower()
            if t in ('submit','button','image','file','reset'): continue
            if t in ('checkbox','radio') and not re.search(r'\bchecked\b', tag, re.I): continue
            data[nm.group(2)] = html_lib.unescape(vl.group(2)) if vl else ''
        for m in re.finditer(r'(?is)<select\b([^>]*)>(.*?)</select>', form):
            sn = re.search(r'\bname\s*=\s*([\"\\\'])(.*?)\1', '<select '+m.group(1)+'>', re.I|re.S)
            if not sn: continue
            sel = re.search(r'(?is)<option\b([^>]*\bselected\b[^>]*)>(.*?)</option>', m.group(2))
            if sel:
                sv = re.search(r'\bvalue\s*=\s*([\"\\\'])(.*?)\1', '<option '+sel.group(1)+'>', re.I|re.S)
                data[sn.group(2)] = html_lib.unescape(sv.group(2)) if sv else ''
        for m in re.finditer(r'(?is)<textarea\b([^>]*)>(.*?)</textarea>', form):
            tn = re.search(r'\bname\s*=\s*([\"\\\'])(.*?)\1', '<textarea '+m.group(1)+'>', re.I|re.S)
            if tn: data[tn.group(2)] = html_lib.unescape(m.group(2)).strip()
        return act, data

    @staticmethod
    def _get_avatar(html):
        m = re.search(r'/avatar/builtin(\d+)\.(?:webp|png|jpg)', html, re.I)
        return int(m.group(1)) if m else None

    def save_stats(self):
        try:
            with open("/tmp/caro_stats.json","w") as f:
                json.dump({'W':self.wins,'L':self.losses,'D':self.draws,'G':self.games}, f)
        except: pass

    # --- WebSocket ---
    async def connect_ws(self):
        try:
            self.ws = await websockets.connect(WS_URL,
                additional_headers={"Cookie":self.cookie,"Origin":"https://gamevh.net","User-Agent":"Mozilla/5.0"},
                max_size=2**20, ping_interval=None)
            return True
        except Exception as e: log.error(f"[WS] Connect err: {e}"); return False

    async def run_ws(self):
        log.info("[WS] Connecting...")
        if not await self.connect_ws(): return
        log.info(f"[WS] Connected (cookie={len(self.cookie)}b)")
        await self.send(self.pkt_login())
        n = 0; wd = asyncio.create_task(self.watchdog())
        try:
            async for raw in self.ws:
                if not self.running: break
                if isinstance(raw, bytes): n += 1; await self.handle(raw)
        except websockets.exceptions.ConnectionClosed as e:
            log.warning(f"[WS] Closed {e.code} after {n} msgs")
        except Exception as e: log.error(f"[WS] Err: {e}")
        finally:
            log.info(f"[WS] Exit after {n} msgs")
            wd.cancel()
            try: await wd
            except: pass
            self.save_stats()
            if self.ws and self.ws.close_code is None:
                try: await self.ws.close()
                except: pass

# ======================== BOT JAX (Kế thừa GameClient) ========================
class CaroBot(GameClient):
    """Bot Caro sử dụng JAX engine với Sliding Window"""
    def __init__(self):
        super().__init__()
        self.engine = None
        self.eng_ok = False
        self.eng_available = False
        self.ag_available = False
        self.ag = None
        self.eng_moves = 0; self.eng_errs = 0
        self.ag_moves = 0; self.ag_errors = 0; self.ag_fallback_count = 0

    def init_engine(self):
        if self.engine and self.eng_ok:
            return self.eng_ok
        self.engine = JaxEngine(timeout_turn=ENGINE_TIMEOUT, board_size=ENGINE_BOARD, rule=ENGINE_RULE)
        self.ag = self.engine
        self.eng_ok = self.engine.start(self.my_sym)
        self.eng_available = self.eng_ok
        self.ag_available = self.eng_ok
        if self.eng_ok:
            log.info(f"[JAX] OK! Window={self.engine.win.range_str()} Board={ENGINE_BOARD} Rule={ENGINE_RULE}")
        else:
            log.warning("[JAX] Khởi động thất bại -> sẽ fallback đánh gần nước cuối")
        return self.eng_ok

    async def on_table_update(self):
        if self.engine and hasattr(self.engine, 'win'):
            # Cập nhật toàn bộ lịch sử vào sliding window để tính offset
            for (x,y,sym) in self.board.hist:
                self.engine.win.update(y)
            log.info(f"[WIN] {self.engine.win.range_str()} (off={self.engine.win.off}) hist={len(self.board.hist)}")

    async def on_game_start(self):
        if not self.engine:
            self.init_engine()
        else:
            # ván mới: restart engine (RESTART + START)
            ok = self.engine.restart()
            if not ok:
                log.warning("[JAX] Restart fail, thử start lại")
                self.eng_ok = self.engine.start(self.my_sym)
            else:
                log.info(f"[JAX] RESTART OK Window={self.engine.win.range_str()}")

    async def on_move(self, x, y, sym):
        if self.engine and hasattr(self.engine, 'win'):
            shifted = self.engine.win.update(y)
            if shifted:
                log.info(f"[WIN] Shift on MOVE ({x},{y}) -> {self.engine.win.range_str()}")

    async def do_move(self):
        if not self.is_playing or not self.running or self.slot < 0:
            self.pending=False; self.pending_move=False
            return
        if self._moving:
            return
        self._moving = True; self.pending = False; self.pending_move=False; self._last_xy = None; self._last_move_xy=None
        try:
            t0 = time.time(); x,y = -1,-1
            use_engine = self.eng_ok and self.engine and self.engine.ok
            # Nếu chưa init -> thử init
            if not use_engine and not self.engine:
                use_engine = self.init_engine()
            elif not use_engine and self.engine and not self.engine.ok:
                # thử restart
                use_engine = self.engine.start(self.my_sym)
                self.eng_ok = use_engine

            if use_engine:
                try:
                    hist = list(self.board.hist)
                    # chạy engine trong executor để không block event loop
                    mv = await asyncio.get_event_loop().run_in_executor(
                        None, lambda: self.engine.get_move(hist, self.my_sym))
                    if mv and 0<=mv[0]<self.board.w and 0<=mv[1]<self.board.h and self.board.get(*mv)==EMPTY:
                        x,y = mv; self.eng_moves += 1; self.ag_moves+=1
                        log.info(f"[JAX] Engine: ({x},{y}) win={self.engine.win.range_str()}")
                    else:
                        self.eng_errs += 1; self.ag_errors+=1
                        log.warning(f"[JAX] Invalid: {mv}, fallback")
                        lx,ly = hist[-1][:2] if hist else (7,9)
                        x,y = self.board.empty_near(lx,ly)
                        self.ag_fallback_count+=1
                        # thử restart engine để ván sau ổn hơn
                        try: self.engine.restart()
                        except: pass
                except Exception as e:
                    self.eng_errs += 1; self.ag_errors+=1; log.warning(f"[JAX] Err: {e}", exc_info=True)
                    try:
                        self.engine.stop()
                    except: pass
                    self.eng_ok = False
                    lx,ly = self.board.hist[-1][:2] if self.board.hist else (7,9)
                    x,y = self.board.empty_near(lx,ly)
                    self.ag_fallback_count+=1
            else:
                lx,ly = self.board.hist[-1][:2] if self.board.hist else (7,9)
                x,y = self.board.empty_near(lx,ly)
                log.info(f"[BOT] Fallback (no engine): ({x},{y})")

            dt = time.time()-t0
            # Double check ô trống trước khi gửi
            if self.board.get(x,y) != EMPTY:
                log.warning(f"[BOT] Ô ({x},{y}) đã chiếm, tìm ô gần nhất")
                x,y = self.board.empty_near(x,y)
            log.info(f"MOVE ({x},{y}) {dt:.2f}s [JAX:{self.eng_moves} err:{self.eng_errs} win:{self.engine.win.range_str() if self.engine else 'N/A'}]")
            await self.send(self.pkt_play(self.board.xy2pos(x,y)))
            self._last_xy = (x,y); self._last_move_xy=(x,y); self.board.put(x,y,self.my_sym)
        finally:
            self._moving = False

    def stop_engine(self):
        if self.engine:
            try: self.engine.stop()
            except: pass
            self.engine = None; self.ag=None; self.eng_ok = False; self.eng_available=False; self.ag_available=False

# ======================== MAIN ========================
def main():
    # Tự động tải JAX nếu chưa có
    b = ensure_jax_engine() or find_jax_binary() or str(ENGINE_DIR / ENGINE_BIN)
    # Nếu vẫn chưa có, thử auto download lần nữa (hiển thị log)
    if not Path(b).exists():
        print(f"[!] JAX chưa có tại {b}, đang thử tải từ {JAX_DOWNLOAD_URL} ...")
        b2 = auto_download_jax()
        if b2 and Path(b2).exists():
            b = b2

    w = find_wine()
    if Path(b).exists() and w:
        print(f"[OK] JAX: {Path(b).name} via {w} | Rule={ENGINE_RULE} Board={ENGINE_BOARD} Timeout={ENGINE_TIMEOUT}ms")
        print(f"     Binary: {b}")
        print(f"     Version: JAX {JAX_VERSION} - Kailong Jiang")
        print(f"     Download: {JAX_DOWNLOAD_URL}")
        print(f"     Mirror  : {JAX_DOWNLOAD_MIRROR}")
        print(f"     Window: 15x15 sliding trên 15x19")
    elif Path(b).exists():
        print(f"[!] JAX found {b} nhưng thiếu wine ({w})")
        print(f"    Cài wine: sudo apt install wine64")
    else:
        print(f"[!] JAX not found: {b}")
        print(f"    Đã thử tải tự động từ:")
        print(f"     - {JAX_DOWNLOAD_URL}")
        print(f"     - {JAX_DOWNLOAD_MIRROR}")
        print(f"    Nếu mạng chặn, tải thủ công:")
        print(f"     wget -O /tmp/JAX25.zip {JAX_DOWNLOAD_URL}")
        print(f"     unzip /tmp/JAX25.zip -d jax-engine/")
        print(f"     Hoặc: wget -O /tmp/JAX24.zip {JAX_DOWNLOAD_MIRROR}")
        print(f"    Bot vẫn chạy fallback (đánh gần nước cuối) nếu không có engine")
    try:
        asyncio.get_running_loop().create_task(_run())
    except RuntimeError:
        asyncio.run(_run())

async def _run():
    try:
        bot = CaroBot()
        bot.start_time = time.time(); bot._running = True
        log.info("="*60)
        log.info("BOT CARO JAX v5.1 - StandardCaro + Sliding Window 15x15")
        log.info(f"Engine: JAX rule={ENGINE_RULE} board={ENGINE_BOARD} timeout={ENGINE_TIMEOUT}ms")
        log.info(f"Bàn gamevh: {GAMEVH_W}x{GAMEVH_H} | Cửa sổ engine: {SlidingWindow.SIZE}x{SlidingWindow.SIZE}")
        log.info("="*60)
        rc = 0
        while bot.running:
            if time.time()-bot.start_time > RUNTIME:
                log.info("[BOT] Hết RUNTIME, dừng")
                break
            was = bot.in_table or bot.is_playing
            bot._want_rejoin = was and bot.table_id is not None and bot._rejoin_n < 2
            bot.is_playing = False; bot.pending = False; bot.pending_move=False
            bot.in_table = False; bot.ready = False
            bot.board.__init__(); bot.players.clear()
            bot.bet_amts = []; bot._bet_id = None; bot._resolved_bet_id=None
            bot._bet_loaded = False; bot._bet_amts_loaded=False
            bot._joining = False; bot._joining_table=False
            bot.opp_gone_at = None; bot._tbl_lost_at = None
            bot.opponent_gone_at=None; bot._table_lost_at=None
            # Không stop engine giữa các lần reconnect WS, chỉ stop khi đổi vòng login chính
            # để giữ window context; nhưng _run vòng ngoài này là vòng login mới -> stop để sạch
            bot.stop_engine()
            ok = await asyncio.get_event_loop().run_in_executor(None, bot.http_login)
            if not ok:
                rc += 1; d = min(30*(2**(rc-1)),300)
                rem = RUNTIME-(time.time()-bot.start_time)
                if rem <= 0: break
                log.warning(f'[BOT] Login fail; retry {min(d,rem):.0f}s ({rc})')
                await asyncio.sleep(min(d,rem)); continue
            rc = 0
            await bot.run_ws()
            if not (bot.in_table or bot.is_playing): bot.table_id = None
            bot.save_stats(); bot.stop_engine()
            # nghỉ 1s trước vòng mới
            await asyncio.sleep(1)
    except KeyboardInterrupt: log.info("[BOT] Stopped by user")
    except Exception as e: log.error(f"[BOT] Fatal: {e}", exc_info=True)

if __name__ == "__main__":
    main()
elif 'ipykernel' in sys.modules or 'google.colab' in sys.modules:
    main()
