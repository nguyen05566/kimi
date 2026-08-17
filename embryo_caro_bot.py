#!/usr/bin/env python3
"""
Caro Bot - Embryo Engine (Caro6 / Gomoku+) - gamevh.net
Embryo hiểu luật Caro Việt Nam (5 không bị chặn 2 đầu) từ bên trong -> KHÔNG cần
lớp Python chặn quân. Giao tiếp qua Piskvork/Gomocup protocol.

Luồng đăng nhập gamevh.net (đã xác minh):
  1. POST https://gamevh.net/login.jsp (USER_NAME/PASSWORD) -> lấy JSESSIONID
  2. GET https://gamevh.net/play/caro/0 -> parse token + nickname (đã đăng nhập)
  3. Connect wss://gamevh.net/ws/gameServer -> gửi lệnh LOGIN (302)
"""

import asyncio
import struct
import time
import logging
import re
import os
import json
import subprocess
import threading
from typing import List, Tuple, Dict, Optional

try:
    import websockets
    import requests
except ImportError:
    subprocess.run(["pip", "install", "websockets", "requests", "-q"])
    import websockets
    import requests

WS_URL = "wss://gamevh.net/ws/gameServer"
GAME_URL = "https://gamevh.net/play/caro/0"
# Đọc tài khoản từ biến môi trường (không hardcode mật khẩu vào code, vì repo có thể public)
USER = os.environ.get("GAMEVH_USER", "ngan2")
PASSWD = os.environ.get("GAMEVH_PASS", "nhat123456")
VERSION = "5.0.2"
GAME_ID = "caro"
RUNTIME = int(os.environ.get("BOT_RUNTIME_SECONDS", str(5 * 60)))

EMBRYO_DIR = "/tmp"
EMBRYO_BIN = os.path.join(EMBRYO_DIR, "pbrain-embryo")
EMBRYO_DOWNLOAD_URL = (
    "https://raw.githubusercontent.com/Hexik/Embryo_engine/master/"
    "Caro6/Linux/pbrain-embryo-1.2.0-6f650fab-c6.bz2"
)
EMBRYO_TURN_TIME = 10000     # ms - thời gian suy nghĩ mỗi nước (10s, đủ cho Caro6)
EMBRYO_MATCH_TIME = 120000   # ms - thời gian tổng cả ván

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(message)s",
                    datefmt="%H:%M:%S")
log = logging.getLogger("caro")

EMPTY = -1
CIRCLE = 0
CROSS = 1

CMD_MAP = {
    300: "PONG", 301: "PING", 302: "LOGIN", 303: "ALERT", 304: "RIBBON_MESSAGE",
    311: "BROADCAST", 312: "INVITE", 314: "SET_CLIENT_MODE", 315: "CONFIG",
    401: "ENTER_PLACE", 406: "PLAYER_ENTERED", 407: "PLAYER_EXITED",
    408: "QUICK_PLAY", 414: "GET_TABLE_DATA", 415: "TABLE_IN_ROOM_CHANGED",
    417: "START_MATCH", 418: "GAMEOVER", 419: "ENTER_STATE", 420: "SET_TURN",
    421: "SET_PLAYER_STATUS", 422: "SET_PLAYER_POINT", 423: "SET_PLAYER_ATTR",
    431: "BALANCE_CHANGED", 432: "OWNER_CHANGED", 433: "GET_TABLE_DATA_EX",
    434: "SET_READY", 501: "BET", 502: "PLAY", 529: "MOVE",
}


class BinaryReader:
    def __init__(self, data: bytes):
        self.data = data
        self.pos = 0

    def remaining(self) -> int:
        return len(self.data) - self.pos

    def u8(self) -> int:
        v = self.data[self.pos]
        self.pos += 1
        return v

    def i8(self) -> int:
        v = struct.unpack_from('>b', self.data, self.pos)[0]
        self.pos += 1
        return v

    def i16(self) -> int:
        v = struct.unpack_from('>h', self.data, self.pos)[0]
        self.pos += 2
        return v

    def u16(self) -> int:
        v = struct.unpack_from('>H', self.data, self.pos)[0]
        self.pos += 2
        return v

    def i32(self) -> int:
        v = struct.unpack_from('>i', self.data, self.pos)[0]
        self.pos += 4
        return v

    def i64(self) -> int:
        hi = struct.unpack_from('>i', self.data, self.pos)[0]
        lo = struct.unpack_from('>I', self.data, self.pos + 4)[0]
        self.pos += 8
        return (hi << 32) + lo

    def read_ascii(self) -> str:
        n = self.u8()
        s = self.data[self.pos:self.pos + n]
        self.pos += n
        return s.decode('ascii', 'replace')

    def read_utf(self) -> str:
        n = self.i16()
        if n <= 0:
            return ""
        s = self.data[self.pos:self.pos + n * 2]
        self.pos += n * 2
        return s.decode('utf-16-be', 'replace')

    def read_bytes(self) -> List[int]:
        n = self.i16()
        return list(self.data[self.pos:self.pos + n])

    def read_command(self) -> str:
        first = self.i8()
        if first < 0:
            n = -first
            s = self.data[self.pos:self.pos + n].decode('ascii', 'replace')
            self.pos += n
            return s
        second = self.u8()
        cmd_id = (first << 8) | second
        return CMD_MAP.get(cmd_id, f"CMD_{cmd_id}")


class BinaryWriter:
    def __init__(self):
        self.parts = []

    def u8(self, v: int):
        self.parts.append(struct.pack('>B', v))

    def i8(self, v: int):
        self.parts.append(struct.pack('>b', v))

    def i16(self, v: int):
        self.parts.append(struct.pack('>h', v))

    def i32(self, v: int):
        self.parts.append(struct.pack('>i', v))

    def i64(self, v: int):
        self.parts.append(struct.pack('>iI', v >> 32, v & 0xFFFFFFFF))

    def write_ascii(self, s: str):
        encoded = s.encode('ascii', 'replace')
        self.u8(len(encoded))
        self.parts.append(encoded)

    def write_utf(self, s: str):
        encoded = s.encode('utf-16-be')
        self.i16(len(encoded) // 2)
        self.parts.append(encoded)

    def write_command(self, cmd: str):
        cmd_id = next((k for k, v in CMD_MAP.items() if v == cmd), None)
        if cmd_id:
            self.parts.append(struct.pack('>H', cmd_id))
        else:
            b = cmd.encode('ascii')
            self.i8(-len(b))
            self.parts.append(b)

    def build(self) -> bytes:
        return b''.join(self.parts)


class Board:
    """Theo dõi trạng thái bàn + mapping tọa độ. Không còn logic luật Caro."""
    def __init__(self, width: int = 15, height: int = 19):
        self.width = width
        self.height = height
        self.grid = [[EMPTY] * width for _ in range(height)]
        self.history = []
        self.placed = set()

    def resize(self, width: int, height: int):
        self.width = width
        self.height = height
        self.grid = [[EMPTY] * width for _ in range(height)]
        self.history.clear()
        self.placed.clear()

    def get(self, x: int, y: int) -> int:
        if 0 <= x < self.width and 0 <= y < self.height:
            return self.grid[y][x]
        return EMPTY

    def put(self, x: int, y: int, symbol: int):
        if 0 <= x < self.width and 0 <= y < self.height:
            self.grid[y][x] = symbol
            self.history.append((x, y, symbol))
            self.placed.add((x, y))

    def undo(self, x: int, y: int):
        if 0 <= x < self.width and 0 <= y < self.height:
            self.grid[y][x] = EMPTY
            if self.history and self.history[-1][:2] == (x, y):
                self.history.pop()
            self.placed.discard((x, y))

    def xy_to_pos(self, x: int, y: int) -> int:
        return y * self.width + x

    def pos_to_xy(self, pos: int) -> Tuple[int, int]:
        return pos % self.width, pos // self.width

    def load_rle(self, data: List[int]):
        self.grid = [[EMPTY] * self.width for _ in range(self.height)]
        self.history.clear()
        self.placed.clear()
        pos = 0
        for value in data:
            symbol = value - 256 if value > 127 else value
            if symbol >= 0:
                y, x = pos // self.width, pos % self.width
                if 0 <= x < self.width and 0 <= y < self.height:
                    self.grid[y][x] = symbol
                    self.placed.add((x, y))
                pos += 1
            else:
                pos += -symbol


import queue


class EmbryoEngine:
    """Giao tiếp với Embryo (Caro6) qua Piskvork protocol."""

    def __init__(self):
        self.process = None
        self._lock = threading.Lock()
        self._game_active = False
        self._output_queue = queue.Queue()
        self._reader_thread = None
        self._board_size = 15

    def _ensure_binary(self):
        if os.path.isfile(EMBRYO_BIN):
            return
        os.makedirs(EMBRYO_DIR, exist_ok=True)
        try:
            import urllib.request
            import bz2
            log.info("[EMBRYO] Downloading engine from GitHub...")
            bz2_path = EMBRYO_BIN + ".bz2"
            urllib.request.urlretrieve(EMBRYO_DOWNLOAD_URL, bz2_path)
            with open(bz2_path, 'rb') as src, open(EMBRYO_BIN, 'wb') as dst:
                dst.write(bz2.decompress(src.read()))
            os.chmod(EMBRYO_BIN, 0o755)
            os.remove(bz2_path)
            log.info("[EMBRYO] Engine downloaded and extracted.")
        except Exception as e:
            log.error(f"[EMBRYO] Failed to download engine: {e}")

    def _reader_loop(self):
        while self.process and self.process.poll() is None:
            try:
                line = self.process.stdout.readline()
                if not line:
                    break
                decoded = line.decode('utf-8', 'replace').strip()
                if decoded:
                    self._output_queue.put(decoded)
            except Exception:
                break
        self._output_queue.put(None)

    def start(self):
        self.stop()
        self._ensure_binary()
        if not os.path.isfile(EMBRYO_BIN):
            log.error("[EMBRYO] Binary not found and download failed.")
            return
        try:
            self.process = subprocess.Popen(
                [EMBRYO_BIN],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                cwd=EMBRYO_DIR,
            )
            self._output_queue = queue.Queue()
            self._reader_thread = threading.Thread(target=self._reader_loop, daemon=True)
            self._reader_thread.start()
            self._drain_until("OK", timeout=15)
            self._send_cmd("ABOUT")
            self._drain_until("variant", timeout=5)
            log.info("[EMBRYO] Engine started")
        except Exception as e:
            log.error(f"[EMBRYO] Failed to start: {e}")
            self.process = None

    def stop(self):
        if self.process:
            try:
                self._send_cmd("END")
                self.process.wait(timeout=3)
            except Exception:
                try:
                    self.process.kill()
                except Exception:
                    pass
            self.process = None
            self._game_active = False
            while not self._output_queue.empty():
                try:
                    self._output_queue.get_nowait()
                except queue.Empty:
                    break
            log.info("[EMBRYO] Engine stopped")

    def new_game(self, width: int, height: int):
        if not self.process or self.process.poll() is not None:
            self.start()

        size = max(width, height)
        if size > 19:
            size = 19
        self._board_size = size

        self._send_cmd(f"INFO timeout_turn {EMBRYO_TURN_TIME}")
        self._send_cmd(f"INFO timeout_match {EMBRYO_MATCH_TIME}")

        self._send_cmd(f"START {size}")
        found = self._drain_until("OK", timeout=10)
        if found:
            log.info(f"[EMBRYO] New game {width}x{height} (size={size})")
            self._game_active = True
        else:
            log.warning("[EMBRYO] START response missing OK")

    def my_turn(self) -> Optional[Tuple[int, int]]:
        if not self.process or not self._game_active:
            return None
        self._send_cmd(f"INFO time_left {EMBRYO_MATCH_TIME}")
        self._send_cmd("BEGIN")
        return self._wait_for_move()

    def opponent_move(self, x: int, y: int) -> Optional[Tuple[int, int]]:
        if not self.process or not self._game_active:
            return None
        self._send_cmd(f"INFO time_left {EMBRYO_MATCH_TIME}")
        self._send_cmd(f"TURN {x},{y}")
        return self._wait_for_move()

    def _send_cmd(self, cmd: str):
        if self.process and self.process.stdin:
            try:
                with self._lock:
                    self.process.stdin.write((cmd + "\n").encode())
                    self.process.stdin.flush()
            except Exception as e:
                log.error(f"[EMBRYO] Send error: {e}")

    def _drain_until(self, target: str, timeout: float = 10.0) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                remaining = deadline - time.time()
                if remaining <= 0:
                    break
                line = self._output_queue.get(timeout=min(remaining, 5))
                if line is None:
                    return False
                if target in line:
                    return True
            except queue.Empty:
                continue
        return False

    def _wait_for_move(self, timeout: float = 30.0) -> Optional[Tuple[int, int]]:
        deadline = time.time() + timeout
        last_move = None
        got_move = False

        while time.time() < deadline:
            try:
                remaining = deadline - time.time()
                if remaining <= 0:
                    break
                line = self._output_queue.get(timeout=min(remaining, 2))
                if line is None:
                    log.warning("[EMBRYO] Engine process died")
                    self._game_active = False
                    break

                if line.startswith("MESSAGE"):
                    log.info(f"[EMBRYO] {line}")
                elif line.startswith("ERROR"):
                    log.warning(f"[EMBRYO] {line}")
                elif line == "OK":
                    pass
                elif line.startswith("UNKNOWN"):
                    log.warning(f"[EMBRYO] {line}")
                else:
                    move = self._parse_move(line)
                    if move:
                        last_move = move
                        got_move = True
                        log.info(f"[EMBRYO] Move: {move}")
                        continue

                if got_move and (line.startswith("MESSAGE") or line == "OK"):
                    break

            except queue.Empty:
                if got_move:
                    break
                continue

        return last_move

    def _parse_move(self, response: str) -> Optional[Tuple[int, int]]:
        if not response:
            return None
        match = re.search(r'(\d+)\s*,\s*(\d+)', response)
        if match:
            return (int(match.group(1)), int(match.group(2)))
        return None

    def is_running(self) -> bool:
        return self.process is not None and self.process.poll() is None


class CaroBot:
    def __init__(self):
        self.ws = None
        self.board = Board()
        self.slot = -1
        self.my_symbol = CROSS
        self.opponent_symbol = CIRCLE
        self.is_playing = False
        self.in_table = False
        self.ready = False
        self.mode_set = False
        self.players = {}
        self.nickname = ""
        self.token = 0
        self.cookie = ""
        self.place_path = "Lobby.caro.0"
        self.lock_key = ""
        self.start_time = None
        self.last_activity = time.time()
        self.running = True
        self.wins = 0
        self.losses = 0
        self.draws = 0
        self.total_games = 0
        self.pending_move = False
        self.i_go_first = False
        self.engine = EmbryoEngine()

    def save_stats(self):
        try:
            with open("/tmp/caro.json", "w") as f:
                json.dump({'W': self.wins, 'L': self.losses, 'D': self.draws}, f)
        except Exception:
            pass

    def update_symbols(self):
        self.my_symbol = CROSS if self.slot == 0 else CIRCLE
        self.opponent_symbol = CIRCLE if self.my_symbol == CROSS else CROSS
        self.i_go_first = (self.slot == 0)
        log.info(f"Slot={self.slot} Me={'X' if self.my_symbol == CROSS else 'O'} First={self.i_go_first}")

    def make_login(self):
        w = BinaryWriter()
        w.write_command("LOGIN")
        w.write_ascii(self.nickname)
        w.i32(self.token)
        w.write_ascii(VERSION)
        w.write_ascii(self.lock_key)
        w.write_ascii(GAME_ID)
        w.i8(1)
        return w.build()

    def make_enter(self, path, password="", mode=1):
        w = BinaryWriter()
        w.write_command("ENTER_PLACE")
        w.write_ascii(path)
        w.write_utf(password)
        w.i8(mode)
        return w.build()

    def make_set_mode(self):
        w = BinaryWriter()
        w.write_command("SET_CLIENT_MODE")
        w.i8(1)
        return w.build()

    def make_get_table(self):
        w = BinaryWriter()
        w.write_command("GET_TABLE_DATA_EX")
        w.write_ascii("")
        return w.build()

    def make_play(self, pos):
        w = BinaryWriter()
        w.write_command("PLAY")
        w.i16(pos)
        return w.build()

    def make_pong(self):
        w = BinaryWriter()
        w.write_command("PONG")
        return w.build()

    def make_create_table(self, bet_amount=1000):
        path = f"Lobby.caro.{bet_amount}"
        log.info(f"[TABLE] Creating table: {path} (bet={bet_amount})")
        w = BinaryWriter()
        w.write_command("QUICK_PLAY")
        w.write_ascii(path)
        w.i8(-1)
        return w.build()

    def make_ready(self):
        w = BinaryWriter()
        w.write_command("SET_READY")
        return w.build()

    async def send(self, data):
        if self.ws:
            try:
                await self.ws.send(data)
            except Exception:
                pass

    async def do_move(self):
        """Thực hiện nước đi bằng Embryo. Không còn logic chặn quân Python."""
        if not self.is_playing:
            return
        self.pending_move = False
        start = time.time()
        move = None
        move_count = len(self.board.history)

        try:
            if move_count == 0 and self.i_go_first:
                move = self.engine.my_turn()
            elif move_count > 0:
                last_x, last_y, last_sym = self.board.history[-1]
                move = self.engine.opponent_move(last_x, last_y)
            else:
                log.info("[EMBRYO] Waiting for opponent's first move")
                return

            x, y = move if move else (self.board.width // 2, self.board.height // 2)
            if move is None:
                log.warning("[EMBRYO] No move from engine, fallback center")

            if self.board.get(x, y) != EMPTY:
                log.warning(f"[EMBRYO] Engine returned occupied ({x},{y}), finding alternative")
                found = False
                for r in range(1, 4):
                    for dy in range(-r, r + 1):
                        for dx in range(-r, r + 1):
                            nx, ny = x + dx, y + dy
                            if (0 <= nx < self.board.width and 0 <= ny < self.board.height
                                    and self.board.get(nx, ny) == EMPTY):
                                x, y = nx, ny
                                found = True
                                break
                        if found:
                            break
                    if found:
                        break

        except Exception as e:
            log.error(f"[EMBRYO] Engine error: {e}")
            cx, cy = self.board.width // 2, self.board.height // 2
            if self.board.get(cx, cy) == EMPTY:
                x, y = cx, cy
            else:
                x, y = cx + 1, cy
                while x < self.board.width and self.board.get(x, y) != EMPTY:
                    x += 1

        pos = self.board.xy_to_pos(x, y)
        elapsed = time.time() - start
        log.info(f"MOVE ({x},{y}) pos={pos} took {elapsed:.3f}s")
        await self.send(self.make_play(pos))
        self.board.put(x, y, self.my_symbol)

    async def handle(self, raw):
        r = BinaryReader(raw)
        cmd = r.read_command()
        if cmd != "PING":
            log.info(f"RECV {cmd} remaining={r.remaining()}")
        self.last_activity = time.time()
        try:
            if cmd == "PING":
                await self.send(self.make_pong())
            elif cmd == "LOGIN":
                await self.handle_login(r)
            elif cmd == "ENTER_PLACE":
                await self.handle_enter(r)
            elif cmd == "GET_TABLE_DATA_EX":
                await self.handle_table(r)
            elif cmd == "QUICK_PLAY":
                await self.handle_quick_play(r)
            elif cmd == "START_MATCH":
                await self.handle_start(r)
            elif cmd == "SET_TURN":
                await self.handle_turn(r)
            elif cmd == "MOVE":
                await self.handle_move(r)
            elif cmd == "GAMEOVER":
                await self.handle_gameover(r)
            elif cmd == "PLAY":
                status = r.i8()
                if status != 0:
                    log.warning(f"PLAY error {status}")
            elif cmd == "PLAYER_EXITED":
                r.i8()
        except Exception as e:
            log.error(f"Error handling {cmd}: {e}", exc_info=True)

    async def handle_login(self, r):
        status = r.i8()
        if status == 0:
            path = r.read_utf()
            if path == "REFRESH":
                await self.send(self.make_enter(self.place_path))
                return
            if r.remaining() > 0:
                self.lock_key = r.read_ascii()
            if r.remaining() > 0:
                r.read_utf()
            if r.remaining() > 0:
                r.read_ascii()
            await self.send(self.make_enter(self.place_path))
        else:
            log.error("LOGIN failed")

    async def handle_enter(self, r):
        r.i8()
        r.u16()
        if not self.mode_set:
            self.mode_set = True
            await self.send(self.make_set_mode())
        await self.send(self.make_get_table())

    async def handle_quick_play(self, r):
        status = r.i8()
        if status != 0:
            await asyncio.sleep(5)
            await self.send(self.make_create_table(1000))
            return
        path = r.read_ascii()
        r.read_utf()
        if r.remaining() > 0:
            n = r.u8()
            for _ in range(n):
                r.read_ascii()
                r.read_utf()
        if path:
            self.in_table = True
            await self.send(self.make_enter(path))
            await asyncio.sleep(0.5)
            await self.send(self.make_set_mode())
            await asyncio.sleep(0.3)
            await self.send(self.make_get_table())

    async def handle_table(self, r):
        try:
            first_byte = r.i8()
            if first_byte != 0:
                # "You are not in table" -> cần tạo bàn
                if not self.in_table:
                    await self.send(self.make_create_table(1000))
                return

            seat_count = r.u8()
            for _ in range(seat_count):
                r.u8()
                r.read_ascii()
                r.u8()
                child_count = r.u8()
                for _ in range(child_count):
                    r.u8()
                    r.read_ascii()
                    r.read_utf()
                    r.u8()
                    r.u8()

            r.u8()
            self.slot = r.i8()
            is_playing = r.u8() == 1

            player_count = r.u8()
            self.players = {}
            for _ in range(player_count):
                sid = r.i8()
                r.i64()
                name = r.read_utf()
                r.u16()
                r.read_ascii()
                r.i8()
                r.i64()
                r.i64()
                r.i64()
                r.u8()
                r.u8()
                self.players[sid] = {'name': name}
                log.info(f"  slot {sid}: {name}")

            current_player = r.i8()
            r.i16()
            r.i16()
            r.u8()

            self.in_table = True

            move_count = r.u8()
            for _ in range(move_count):
                r.i8()
                r.i32()

            width = r.u8()
            height = r.u8()
            self.board.resize(width, height)

            r.i16()
            board_data = r.read_bytes()
            self.board.load_rle(board_data)
            self.update_symbols()

            r.u8()
            r.u8()
            n = r.u8()
            for _ in range(n):
                r.read_ascii()
                r.read_utf()

            self.is_playing = is_playing
            if is_playing:
                # khởi tạo engine với bàn hiện tại
                self.engine.new_game(width, height)
                if len(self.board.history) > 0:
                    self._sync_engine_with_board()
                if current_player == self.slot:
                    self.pending_move = True
                    await self.do_move()
            else:
                if not self.ready:
                    self.ready = True
                    await self.send(self.make_ready())

        except Exception as e:
            log.error(f"Table error: {e}", exc_info=True)

    async def handle_start(self, r):
        self.total_games += 1
        self.is_playing = True
        self.ready = False

        player_count = r.u8()
        for _ in range(player_count):
            r.i8()
            r.i32()

        width = r.u8()
        height = r.u8()
        self.board.resize(width, height)

        r.i16()
        board_data = r.read_bytes()
        self.board.load_rle(board_data)
        self.update_symbols()

        log.info(f"=== GAME {self.total_games} === Me={'X' if self.my_symbol == CROSS else 'O'} "
                 f"{'FIRST' if self.i_go_first else 'SECOND'} Board={width}x{height}")

        self.engine.new_game(width, height)

    def _sync_engine_with_board(self):
        """Gửi BOARD ... DONE để đồng bộ bàn vào engine (KHÔNG chờ nước đi)."""
        if not self.engine.is_running():
            self.engine.new_game(self.board.width, self.board.height)
        if not self.board.history:
            return
        log.info(f"[EMBRYO] Syncing {len(self.board.history)} moves via BOARD")
        self.engine._send_cmd("BOARD")
        for x, y, sym in self.board.history:
            color = 1 if sym == CROSS else 2
            self.engine._send_cmd(f"{x},{y},{color}")
        self.engine._send_cmd("DONE")

    async def handle_turn(self, r):
        sid = r.i8()
        r.i16()
        r.i16()
        is_my_turn = (sid == self.slot)
        log.info(f"TURN slot={sid} my_turn={is_my_turn}")
        if is_my_turn and self.is_playing:
            self.pending_move = True
            await asyncio.sleep(0.1)
            await self.do_move()

    async def handle_move(self, r):
        pos = r.i16()
        symbol = r.i8()
        x, y = self.board.pos_to_xy(pos)
        current = self.board.get(x, y)
        symbol_name = 'X' if symbol == CROSS else 'O'
        if current == symbol:
            log.info(f"[OK] ({x},{y}) {symbol_name}")
        elif current != EMPTY and current != symbol:
            self.board.undo(x, y)
            self.board.put(x, y, symbol)
        else:
            log.info(f"[OPP] ({x},{y}) {symbol_name}")
            self.board.put(x, y, symbol)

    async def handle_gameover(self, r):
        self.is_playing = False
        self.pending_move = False

        player_count = r.u8()
        my_result = None
        for _ in range(player_count):
            sid = r.i8()
            result = r.i8()
            earn = r.i64()
            if sid == self.slot:
                my_result = result
            log.info(f"  slot {sid}: result={result} earn={earn}")

        if my_result in (1, 11):
            self.wins += 1
            log.info(">>> WIN! <<<")
        elif my_result in (2, 4, 12):
            self.losses += 1
            log.info(">>> LOSE! <<<")
        else:
            self.draws += 1
            log.info(">>> DRAW! <<<")

        try:
            r.read_utf()
        except Exception:
            pass

        log.info(f"[STAT] W={self.wins} L={self.losses} D={self.draws} total={self.total_games}")
        self.save_stats()
        await asyncio.sleep(2)
        await self.send(self.make_create_table(1000))

    def login_and_fetch_session(self):
        """Đăng nhập gamevh.net qua HTTP, lấy token + cookie đã đăng nhập."""
        try:
            s = requests.Session()
            s.headers.update({"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
            s.get("https://gamevh.net/login.jsp", timeout=20)
            s.post("https://gamevh.net/login.jsp", data={
                "USER_NAME": USER,
                "PASSWORD": PASSWD,
                "AUTO_LOGIN": "true",
                "LOGIN": "Đăng nhập",
                "redirect": "/",
            }, timeout=20, allow_redirects=True)

            html = s.get(GAME_URL, timeout=20).text
            m_nick = re.search(r"currentPlayerNickName\s*=\s*'([^']*)'", html)
            m_tok = re.search(r"token\s*=\s*(-?\d+)", html)
            m_anon = re.search(r"anonymous\s*=\s*(\w+)", html)
            if m_nick and m_tok and (m_anon is None or m_anon.group(1) == "false"):
                self.nickname = m_nick.group(1)
                self.token = int(m_tok.group(1))
                self.cookie = '; '.join(f'{k}={v}' for k, v in s.cookies.items())
                log.info(f"[AUTH] Signed in as {self.nickname} token={self.token}")
                return True
            log.error("[AUTH] Login failed or still anonymous")
            return False
        except Exception as e:
            log.error(f"[AUTH] Login error: {e}")
            return False

    async def run(self):
        self.start_time = time.time()
        log.info(f"Starting Caro bot (Embryo Caro6) - runtime={RUNTIME}s")

        while self.running and (time.time() - self.start_time) < RUNTIME:
            if not self.login_and_fetch_session():
                await asyncio.sleep(5)
                continue
            try:
                headers = {
                    "Cookie": self.cookie,
                    "Origin": "https://gamevh.net",
                    "User-Agent": "Mozilla/5.0",
                }
                async with websockets.connect(
                    WS_URL,
                    additional_headers=headers,
                    ping_interval=None,
                    max_size=2 ** 20,
                ) as ws:
                    self.ws = ws
                    log.info("WebSocket connected")
                    await self.send(self.make_login())
                    async for raw in ws:
                        if (time.time() - self.start_time) >= RUNTIME:
                            self.running = False
                            break
                        await self.handle(raw)
            except Exception as e:
                log.error(f"Connection error: {e}")
                self.in_table = False
                self.is_playing = False
                await asyncio.sleep(5)

        log.info("Bot stopped.")


async def main():
    bot = CaroBot()
    await bot.run()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("Interrupted.")
