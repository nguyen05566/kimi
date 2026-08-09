#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════════════════════════╗
║  BOTTRAIN - AlphaZero Self-Learning Caro Bot                   ║
║  Board: 15x19 | Rule 8 | Pure NumPy CNN + MCTS                  ║
║  No external engine needed - trains itself on GitHub Actions    ║
╚══════════════════════════════════════════════════════════════════╝
"""
import subprocess, sys, os, importlib, urllib.request, json, time, struct
import logging, asyncio, random, threading, traceback
from pathlib import Path
from typing import Optional, Tuple, List

# ======================== LOGGING ========================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(message)s",
    datefmt="%H:%M:%S"
)
log = logging.getLogger("bottrain")

# ======================== INSTALL DEPS ========================
REQUIRED = ["websockets", "requests", "numpy"]
for pkg in REQUIRED:
    try:
        importlib.import_module(pkg)
    except ImportError:
        log.info(f"[SETUP] Installing {pkg}...")
        subprocess.run([sys.executable, "-m", "pip", "install", pkg, "-q", "--break-system-packages"], 
                       stderr=subprocess.DEVNULL)
        importlib.import_module(pkg)

import websockets, requests
import numpy as np

# ======================== LOCAL IMPORTS ========================
try:
    from game_15x19 import Board, Game
    from policy_value_net import PolicyValueNetNumpy, init_net_params, softmax
    from mcts import MCTSPlayer
except ImportError:
    sys.path.insert(0, str(Path(__file__).parent))
    from game_15x19 import Board, Game
    from policy_value_net import PolicyValueNetNumpy, init_net_params, softmax
    from mcts import MCTSPlayer

# ======================== CONFIG ========================
BASE_DIR = Path(__file__).parent
MODEL_PATH = BASE_DIR / "bottrain_model.npy"
DATA_PATH = BASE_DIR / "bottrain_data"

WS_URL = "wss://gamevh.net/ws/gameServer"
USER = os.environ.get("CARO_USER", "")
PASSWD = os.environ.get("CARO_PASSWD", "")
if not USER or not PASSWD:
    log.error("[BOTTRAIN] Missing CARO_USER/CARO_PASSWD env vars")
    sys.exit(1)

VERSION = "5.0.0"
GAME_ID = "caro"
RUNTIME = int(os.environ.get("CARO_RUNTIME_SECONDS") or 
              float(os.environ.get("CARO_RUNTIME_HOURS", "5.9")) * 3600)
BOT_BET_XU = 1000
EMPTY = -1; CIRCLE = 0; CROSS = 1

# NNUE/MCTS config
BOARD_WIDTH = 15
BOARD_HEIGHT = 19
N_IN_ROW = 5
MCTS_PLAYOUT = int(os.environ.get("MCTS_PLAYOUT", "200"))
MCTS_CPUCT = 5

# CMD MAP
CMD_MAP = {
    300: "PONG", 301: "PING", 302: "LOGIN", 303: "ALERT",
    401: "ENTER_PLACE", 402: "ENTER_CHILD_PLACE", 405: "CREATE_RULE",
    406: "PLAYER_ENTERED", 407: "PLAYER_EXITED", 410: "KICK_PLAYER",
    413: "LIST_BET_AMT", 414: "GET_TABLE_DATA", 417: "START_MATCH",
    418: "GAMEOVER", 419: "ENTER_STATE", 420: "SET_TURN",
    421: "SET_PLAYER_STATUS", 422: "SET_PLAYER_POINT",
    432: "OWNER_CHANGED", 433: "GET_TABLE_DATA_EX",
    434: "SET_READY", 501: "BET", 502: "PLAY",
    505: "CHAT", 518: "HIGHLIGHT", 529: "MOVE",
    533: "ASK_DRAW", 534: "SURRENDER", 535: "RETREAT",
}

# ======================== BINARY PROTOCOL ========================
class BinaryReader:
    def __init__(self, data: bytes):
        self.data = data; self.pos = 0
    def u8(self) -> int:
        if self.pos >= len(self.data): return 0
        v = self.data[self.pos]; self.pos += 1; return v
    def i16(self) -> int:
        if self.pos + 2 > len(self.data): return 0
        v = struct.unpack_from('>h', self.data, self.pos)[0]; self.pos += 2; return v
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
        first = self.i16()
        if first < 0:
            n = -first
            if self.pos + n > len(self.data): n = len(self.data) - self.pos
            s = self.data[self.pos:self.pos + n].decode('ascii', 'replace')
            self.pos += n; return s
        cmd_id = first & 0xFFFF
        return CMD_MAP.get(cmd_id, f"CMD_{cmd_id}")

class BinaryWriter:
    def __init__(self): self.parts = []
    def u8(self, v: int): self.parts.append(struct.pack('>B', v))
    def i16(self, v: int): self.parts.append(struct.pack('>h', v))
    def i32(self, v: int): self.parts.append(struct.pack('>i', v))
    def write_ascii(self, s: str):
        encoded = s.encode('ascii', 'replace'); self.u8(len(encoded)); self.parts.append(encoded)
    def write_utf(self, s: str):
        encoded = s.encode('utf-16-be'); self.i16(len(encoded) // 2); self.parts.append(encoded)
    def build(self) -> bytes: return b''.join(self.parts)

# ======================== BOT CLASS ========================
class CaroBotTrain:
    def __init__(self):
        self.ws = None
        self.board = Board(width=BOARD_WIDTH, height=BOARD_HEIGHT, n_in_row=N_IN_ROW)
        self.slot = -1; self.my_symbol = CROSS
        self.is_playing = False; self.in_table = False
        self.players = {}; self.nickname = ""
        self.token = 0; self.cookie = ""
        self.start_time = None
        self.last_activity = time.time()
        self._running = True
        self.wins = 0; self.losses = 0; self.draws = 0
        self.table_id = None
        self._moving = False
        
        # BOTTRAIN specific: load model
        self.net = None
        self.mcts_player = None
        self.load_or_init_model()

    def load_or_init_model(self):
        """Load trained model or initialize new one"""
        if MODEL_PATH.exists():
            try:
                net_params = np.load(MODEL_PATH, allow_pickle=True)
                log.info(f"[MODEL] Loaded from {MODEL_PATH}")
            except Exception as e:
                log.warning(f"[MODEL] Failed to load: {e}, initializing new")
                net_params = init_net_params(BOARD_WIDTH, BOARD_HEIGHT)
        else:
            log.info("[MODEL] No saved model, initializing random weights")
            net_params = init_net_params(BOARD_WIDTH, BOARD_HEIGHT)
        
        self.net = PolicyValueNetNumpy(BOARD_WIDTH, BOARD_HEIGHT, net_params)
        self.mcts_player = MCTSPlayer(
            self.net.policy_value_fn,
            c_puct=MCTS_CPUCT,
            n_playout=MCTS_PLAYOUT,
            is_selfplay=0
        )

    def init_board_for_game(self):
        """Reset board and MCTS for new game"""
        self.board.init_board()
        self.mcts_player.reset_player()

    def board_to_bot_coords(self, x: int, y: int) -> Tuple[int, int]:
        """Convert from gamevh coords (0-14, 0-18) to bot internal coords"""
        return x, y

    def bot_to_board_move(self, x: int, y: int) -> int:
        """Convert x,y to flat move index for internal board"""
        return y * self.board.width + x

    def get_ai_move(self, opponent_x: int, opponent_y: int) -> Optional[Tuple[int, int]]:
        """Get AI move using MCTS. Returns (x, y) or None."""
        try:
            if len(self.board.availables) <= 1:
                return None
            
            move = self.mcts_player.get_action(self.board, temp=1e-3, return_prob=0)
            if move is not None:
                loc = self.board.move_to_location(move)
                return loc[0], loc[1]
            return None
        except Exception as e:
            log.error(f"[MCTS] Error: {e}")
            traceback.print_exc()
            return None

    def apply_opponent_move(self, x: int, y: int):
        """Register opponent's move in internal board"""
        move = self.bot_to_board_move(x, y)
        if move in self.board.availables:
            self.board.do_move(move)

    def apply_my_move(self, x: int, y: int):
        """Register our move in internal board"""
        move = self.bot_to_board_move(x, y)
        if move in self.board.availables:
            self.board.do_move(move)

    async def http_login(self) -> bool:
        """Login via HTTP to get cookie + token"""
        try:
            session = requests.Session()
            resp = session.get("https://gamevh.net/signin", timeout=15)
            if resp.status_code != 200:
                log.error(f"[LOGIN] GET /signin failed: {resp.status_code}")
                return False
            
            cookies = dict(session.cookies)
            self.cookie = "; ".join(f"{k}={v}" for k, v in cookies.items())
            
            login_payload = {"username": USER, "password": PASSWD}
            headers = {
                "Content-Type": "application/json",
                "Cookie": self.cookie,
                "Origin": "https://gamevh.net",
                "Referer": "https://gamevh.net/signin"
            }
            resp2 = session.post("https://gamevh.net/api/signin", json=login_payload, headers=headers, timeout=15)
            if resp2.status_code != 200:
                log.error(f"[LOGIN] POST /api/signin failed: {resp2.status_code}")
                return False
            
            data = resp2.json()
            self.token = data.get("token", 0)
            self.nickname = data.get("displayName", data.get("username", "BOTTRAIN"))
            log.info(f"[LOGIN] OK - {self.nickname} (token={self.token})")
            return True
        except Exception as e:
            log.error(f"[LOGIN] Error: {e}")
            return False

    # ======================== WEBSOCKET SEND ========================
    def _build_msg(self, cmd: str, fields: list) -> bytes:
        w = BinaryWriter()
        w.i16(len(cmd))
        w.parts.append(cmd.encode('ascii'))
        for f in fields:
            if isinstance(f, int): w.i32(f)
            elif isinstance(f, str): w.write_utf(f)
            elif isinstance(f, bytes): w.parts.append(f)
        return w.build()

    async def _send(self, ws, cmd: str, *args):
        try:
            msg = self._build_msg(cmd, list(args))
            await ws.send(msg)
        except Exception as e:
            log.error(f"[SEND] {cmd} error: {e}")

    async def send_login(self):
        await self._send(self.ws, "LOGIN", self.token, self.cookie, "", VERSION, GAME_ID, False)

    async def send_enter_place(self):
        await self._send(self.ws, "ENTER_PLACE", "Lobby.caro.0")

    async def send_create_rule(self):
        await self._send(self.ws, "CREATE_RULE", "caro_8", "Caro Rule 8")

    async def send_enter_child_place(self, table_id: int):
        await self._send(self.ws, "ENTER_CHILD_PLACE", f"Lobby.caro.0/{table_id}")

    async def send_bet(self):
        await self._send(self.ws, "BET", self.table_id, BOT_BET_XU)

    async def send_ready(self):
        await self._send(self.ws, "SET_READY", True)

    async def send_play(self, x: int, y: int):
        pos = y * BOARD_WIDTH + x
        await self._send(self.ws, "PLAY", pos)
        self._moving = True

    async def send_surrender(self):
        await self._send(self.ws, "SURRENDER")

    # ======================== MESSAGE HANDLING ========================
    async def handle_message(self, raw: bytes):
        try:
            r = BinaryReader(raw)
            cmd = r.read_command()
            
            if cmd == "PONG": return
            elif cmd == "PING":
                await self._send(self.ws, "PONG")
            
            elif cmd == "LOGIN":
                ok = r.u8()
                reason = r.read_utf() if not ok else ""
                log.info(f"[LOGIN] {'OK' if ok else 'FAIL: ' + reason}")
                if ok:
                    await self.send_enter_place()
            
            elif cmd == "ENTER_PLACE":
                ok = r.u8()
                if ok:
                    log.info("[ENTER_PLACE] OK")
                    await self.send_create_rule()
            
            elif cmd == "CREATE_RULE":
                log.info("[CREATE_RULE] Rule registered")
            
            elif cmd == "ENTER_CHILD_PLACE":
                ok = r.u8()
                if ok:
                    log.info(f"[TABLE] Joined table {self.table_id}")
                    self.in_table = True
                    await self.send_bet()
                else:
                    log.warning(f"[TABLE] Failed to join table {self.table_id}")
                    self.table_id = None
            
            elif cmd == "LIST_BET_AMT":
                count = r.i16()
                amts = [r.i32() for _ in range(count)]
                if amts and self.table_id is not None:
                    await self.send_bet()
            
            elif cmd == "GET_TABLE_DATA" or cmd == "GET_TABLE_DATA_EX":
                self.table_id = r.i32()
                table_name = r.read_utf()
                slot_count = r.i16()
                slots = []
                for _ in range(slot_count):
                    slot_info = {
                        'id': r.i32(), 'slot': r.i16(),
                        'status': r.i16(), 'symbol': r.i16(),
                        'nickname': r.read_utf(),
                    }
                    slots.append(slot_info)
                
                if not self.in_table and self.table_id:
                    await self.send_enter_child_place(self.table_id)
            
            elif cmd == "PLAYER_ENTERED":
                player_id = r.i32()
                nickname = r.read_utf()
                slot_num = r.i16()
                log.info(f"[PLAYER] {nickname} joined slot {slot_num}")
            
            elif cmd == "PLAYER_EXITED":
                player_id = r.i32()
                log.info(f"[PLAYER] {player_id} left")
            
            elif cmd == "START_MATCH":
                self.slot = r.i16()
                self.my_symbol = r.i16()  # 0 = circle, 1 = cross
                board_width = r.i16()
                board_height = r.i16()
                log.info(f"[MATCH] START - slot={self.slot} symbol={self.my_symbol} board={board_width}x{board_height}")
                
                if board_width != self.board.width or board_height != self.board.height:
                    self.board = Board(width=board_width, height=board_height, n_in_row=N_IN_ROW)
                
                self.init_board_for_game()
                self.is_playing = True
                self.start_time = time.time()
            
            elif cmd == "SET_TURN":
                self.is_playing = True
            
            elif cmd == "MOVE":
                move_type = r.u8()
                x = r.u8()
                y = r.u8()
                symbol = r.u8()
                
                if symbol != self.my_symbol:
                    self.apply_opponent_move(x, y)
                    log.info(f"[MOVE] Opponent: ({x},{y})")
                    
                    ai_move = self.get_ai_move(x, y)
                    if ai_move:
                        ax, ay = ai_move
                        self.apply_my_move(ax, ay)
                        await self.send_play(ax, ay)
                        log.info(f"[MOVE] BOTTRAIN: ({ax},{ay})")
                else:
                    self.apply_my_move(x, y)
                    self._moving = False
                    log.info(f"[MOVE] Self: ({x},{y})")
            
            elif cmd == "GAMEOVER":
                result = r.u8()
                reason = r.read_utf()
                winner_slot = r.u8()
                
                if result == self.my_symbol:
                    self.wins += 1
                    log.info(f"[GAMEOVER] WIN! (W:{self.wins} L:{self.losses} D:{self.draws})")
                elif result == -1:
                    self.draws += 1
                    log.info(f"[GAMEOVER] DRAW (W:{self.wins} L:{self.losses} D:{self.draws})")
                else:
                    self.losses += 1
                    log.info(f"[GAMEOVER] LOSS (W:{self.wins} L:{self.losses} D:{self.draws})")
                
                self.is_playing = False
                self._moving = False
            
            elif cmd == "SURRENDER":
                log.info("[SURRENDER] Game surrendered")
                self.is_playing = False
                self._moving = False
            
            elif cmd == "ALERT":
                msg = r.read_utf()
                log.info(f"[ALERT] {msg}")
            
            elif cmd == "CHAT":
                pid = r.i32()
                nick = r.read_utf()
                msg = r.read_utf()
                log.info(f"[CHAT] {nick}: {msg}")
            
            else:
                log.debug(f"[CMD] Unknown: {cmd}")
                
        except Exception as e:
            log.error(f"[MSG] Parse error: {e}")
            traceback.print_exc()

    # ======================== MAIN LOOP ========================
    async def run(self):
        """Main bot loop"""
        log.info("=" * 60)
        log.info("BOTTRAIN - AlphaZero Self-Learning Caro Bot")
        log.info(f"Board: {BOARD_WIDTH}x{BOARD_HEIGHT} | Rule: 8 | MCTS: {MCTS_PLAYOUT} playouts")
        log.info("=" * 60)
        
        if not await self.http_login():
            log.error("Login failed, exiting")
            return
        
        self.start_time = time.time()
        
        while self._running:
            try:
                headers = {"Cookie": self.cookie, "Origin": "https://gamevh.net"}
                async with websockets.connect(WS_URL, extra_headers=headers) as ws:
                    self.ws = ws
                    await self.send_login()
                    
                    while self._running:
                        try:
                            msg = await asyncio.wait_for(ws.recv(), timeout=30)
                            if isinstance(msg, bytes):
                                await self.handle_message(msg)
                        except asyncio.TimeoutError:
                            elapsed = time.time() - self.start_time
                            if elapsed > RUNTIME:
                                log.info(f"[TIME] Runtime limit reached ({RUNTIME}s), exiting")
                                self._running = False
                                break
                            
            except websockets.ConnectionClosed:
                log.warning("[WS] Connection closed, reconnecting...")
                await asyncio.sleep(5)
            except Exception as e:
                log.error(f"[WS] Error: {e}")
                await asyncio.sleep(5)
        
        log.info(f"[BOTTRAIN] Session ended. Stats: W={self.wins} L={self.losses} D={self.draws}")
        log.info("[BOTTRAIN] Shutting down.")

if __name__ == "__main__":
    bot = CaroBotTrain()
    try:
        asyncio.run(bot.run())
    except KeyboardInterrupt:
        log.info("[BOTTRAIN] Interrupted by user")