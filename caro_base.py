#!/usr/bin/env python3
"""
Module chung cho bot Caro gamevh.net
- Binary protocol (Reader/Writer)
- Board logic
- Sliding window 15×15 trên bàn 15×19
- Game client (WebSocket, login, table management)
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

# ======================== WINE ========================
def find_wine():
    for c in ["wine64","wine"]:
        p = shutil.which(c)
        if p: return p
    portable = _BASE_DIR / "wine-portable" / "wine-9.21-amd64" / "bin" / "wine64"
    return str(portable) if portable.exists() else None

# ======================== BINARY PROTOCOL ========================
class BinReader:
    def __init__(self, data: bytes):
        self.d = data; self.p = 0
    def rem(self): return len(self.d) - self.p
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
    def utf(self):
        if self.p+2 > len(self.d): return ""
        n = self.i16()
        if n <= 0: return ""
        bl = min(n*2, len(self.d)-self.p)
        s = self.d[self.p:self.p+bl].decode('utf-16-be','replace'); self.p += bl; return s
    def bytes(self):
        if self.p+2 > len(self.d): return []
        n = self.i16(); n = min(n, len(self.d)-self.p)
        r = list(self.d[self.p:self.p+n]); self.p += n; return r
    def cmd(self):
        f = self.i8()
        if f < 0:
            n = -f; n = min(n, len(self.d)-self.p)
            s = self.d[self.p:self.p+n].decode('ascii','replace'); self.p += n; return s
        s = self.u8(); return CMD_MAP.get((f<<8)|s, f"CMD_{(f<<8)|s}")

class BinWriter:
    def __init__(self): self.p = []
    def u8(self,v): self.p.append(struct.pack('>B',v))
    def i8(self,v): self.p.append(struct.pack('>b',v))
    def i16(self,v): self.p.append(struct.pack('>h',v))
    def i32(self,v): self.p.append(struct.pack('>i',v))
    def ascii(self,s):
        e = s.encode('ascii','replace'); self.u8(len(e)); self.p.append(e)
    def utf(self,s):
        e = s.encode('utf-16-be'); self.i16(len(e)//2); self.p.append(e)
    def cmd(self,c):
        cid = next((k for k,v in CMD_MAP.items() if v==c), None)
        if cid: self.p.append(struct.pack('>H',cid))
        else: b=c.encode('ascii'); self.i8(-len(b)); self.p.append(b)
    def build(self): return b''.join(self.p)

# ======================== BOARD ========================
class Board:
    def __init__(self, w=15, h=19):
        self.w = w; self.h = h
        self.grid = [[EMPTY]*w for _ in range(h)]
        self.hist = []; self.placed = set()
    def resize(self, w, h):
        self.w = w; self.h = h
        self.grid = [[EMPTY]*w for _ in range(h)]
        self.hist.clear(); self.placed.clear()
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
    def xy2pos(self, x, y): return y*self.w+x
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

# ======================== GAME CLIENT ========================
class GameClient:
    """WebSocket client cho gamevh.net - xử lý protocol, login, table."""
    def __init__(self):
        self.ws = None; self.board = Board()
        self.slot = -1; self.my_sym = CROSS; self.opp_sym = CIRCLE
        self.is_playing = False; self.in_table = False; self.ready = False
        self.players = {}; self.nick = ""; self.token = 0; self.cookie = ""
        self.place_path = "Lobby.caro.0"; self.lock_key = ""
        self.start_time = None; self.last_act = time.time(); self._running = True
        self.wins = 0; self.losses = 0; self.draws = 0; self.games = 0
        self.pending = False; self.bet_amts = []; self._bet_id = None
        self._bet_loaded = False; self._joining = False
        self.table_id = None; self._slot_by_pid = {}
        self.opp_gone_at = None; self._tbl_lost_at = None
        self._want_rejoin = False; self._rejoining = False; self._rejoin_n = 0
        self._id_done = False; self._moving = False; self._last_xy = None

    @property
    def running(self): return self._running
    def stop(self): self._running = False

    def update_sym(self):
        self.my_sym = CIRCLE if self.slot==0 else CROSS
        self.opp_sym = CROSS if self.my_sym==CIRCLE else CIRCLE

    # --- Packet builders ---
    def pkt_login(self):
        w = BinWriter(); w.cmd("LOGIN"); w.ascii(self.nick)
        w.i32(self.token); w.ascii(VERSION); w.ascii(self.lock_key)
        w.ascii("caro"); w.i8(1); return w.build()
    def pkt_enter(self, path, pw="", mode=1):
        w = BinWriter(); w.cmd("ENTER_PLACE"); w.ascii(path); w.utf(pw); w.i8(mode); return w.build()
    def pkt_list_bet(self):
        w = BinWriter(); w.cmd("LIST_BET_AMT"); return w.build()
    def pkt_create(self):
        bid = self._bet_id if self._bet_id is not None else self._resolve_bet()
        if bid is None: bid = 0
        args = [("matchDuration","0"),("turnDuration","60"),("accDuration","0"),("blockSoftware","0")]
        w = BinWriter(); w.cmd("CREATE_RULE"); w.i8(bid); w.i8(len(args))
        for n,v in args: w.ascii(n); w.utf(v)
        return w.build()
    def pkt_table(self):
        w = BinWriter(); w.cmd("GET_TABLE_DATA_EX"); w.ascii(""); return w.build()
    def pkt_play(self, pos):
        w = BinWriter(); w.cmd("PLAY"); w.i16(pos); return w.build()
    def pkt_pong(self):
        w = BinWriter(); w.cmd("PONG"); return w.build()
    def pkt_ready(self):
        if self.is_playing: return b''
        w = BinWriter(); w.cmd("SET_READY"); return w.build()

    def _resolve_bet(self):
        if not self.bet_amts: return None
        for b in self.bet_amts:
            if b['v']==BOT_BET_XU: return b['id']
        lo = [b for b in self.bet_amts if 0<b['v']<=BOT_BET_XU]
        return max(lo,key=lambda x:x['v'])['id'] if lo else 0

    async def send(self, data):
        if self.ws and data:
            try: await self.ws.send(data)
            except: pass

    async def create_table(self):
        if not self._bet_loaded:
            self._bet_loaded = False; await self.send(self.pkt_list_bet())
        else:
            await self.send(self.pkt_create())

    # --- Message handlers ---
    async def handle(self, raw):
        r = BinReader(raw); c = r.cmd()
        if c != "PING": log.info(f"RECV {c}")
        self.last_act = time.time()
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
            if self._joining:
                self._joining = False; self._rejoining = False; self.in_table = True
                await asyncio.sleep(0.3); await self.send(self.pkt_table())
            elif not self.in_table:
                if self._want_rejoin and self.table_id:
                    self._want_rejoin = False; self._rejoining = True; self._joining = True
                    await self.send(self.pkt_enter(f"{self.place_path}.{self.table_id}"))
                else:
                    self._bet_loaded = False; self._bet_id = None
                    await self.send(self.pkt_list_bet())
        elif self._joining:
            self._joining = False
            if self._rejoining:
                self._rejoining = False; self._rejoin_n += 1; self.table_id = None
                await asyncio.sleep(1); await self.send(self.pkt_list_bet())
            else:
                await asyncio.sleep(1); await self.send(self.pkt_create())

    async def _h_bet(self, r):
        if r.i8() != 0: return
        n = r.i8()
        self.bet_amts = [{"id":i,"v":r.i32()} for i in range(n)]
        self._bet_id = self._resolve_bet(); self._bet_loaded = True
        await self.send(self.pkt_create())

    async def _h_create(self, r):
        if r.i8() == 0:
            self.table_id = r.ascii(); self._rejoin_n = 0
            log.info(f"[CREATE] Bàn mới id={self.table_id}")
            await asyncio.sleep(0.5); self._joining = False
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
            pc = r.u8(); self.players = {}; self._slot_by_pid = {}
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
            log.info(f"[TBL] Slot={self.slot} Play={playing} Turn=slot{cur}")
            # Callback for subclass
            await self.on_table_update()
            if playing and cur == self.slot:
                if not self._moving and not self.pending:
                    self.pending = True; await self.do_move()
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
        except Exception as e: log.error(f"Table err: {e}")

    async def _h_start(self, r):
        self.games += 1; self.is_playing = True; self.ready = False
        self.pending = False; self._moving = False; self._last_xy = None
        self.opp_gone_at = None
        for _ in range(r.u8()): r.i8(); r.i32()
        w=r.u8(); h=r.u8(); self.board.resize(w,h)
        r.i16(); self.board.load_rle(r.bytes()); self.update_sym()
        log.info(f"=== GAME {self.games} === Me={'X' if self.my_sym==CROSS else 'O'}")
        await self.on_game_start()
        if self.slot < 0:
            await asyncio.sleep(0.5); await self.send(self.pkt_table())

    async def _h_turn(self, r):
        sid=r.i8(); r.i16(); r.i16()
        if self.slot < 0: return
        if sid==self.slot and self.is_playing and self.running:
            if not self.pending and not self._moving:
                self.pending = True; await asyncio.sleep(1.5); await self.do_move()

    async def _h_move(self, r):
        pos=r.i16(); sym=r.i8()
        x,y = self.board.pos2xy(pos)
        cur = self.board.get(x,y)
        if cur == sym:
            if sym==self.my_sym and self._last_xy: self._last_xy = None
        elif cur != EMPTY and cur != sym:
            self.my_sym = sym; self.opp_sym = CROSS if sym==CIRCLE else CIRCLE
            self.board.undo(x,y); self.board.put(x,y,sym)
        else:
            self.board.put(x,y,sym)
            await self.on_move(x, y, sym)

    async def _h_play(self, r):
        if r.i8() != 0:
            log.warning(f"PLAY err"); self.pending = False
            if self._last_xy: self.board.undo(*self._last_xy); self._last_xy = None
            await asyncio.sleep(0.5); await self.send(self.pkt_table())

    async def _h_gameover(self, r):
        self.is_playing = False; self.pending = False; self.opp_gone_at = None
        res = None
        for _ in range(r.u8()):
            sid=r.i8(); result=r.i8(); r.i64()
            if sid==self.slot: res = result
        if res in (1,11): self.wins += 1; log.info(">>> WIN! <<<")
        elif res in (2,4,12): self.losses += 1; log.info(">>> LOSE! <<<")
        else: self.draws += 1; log.info(">>> DRAW! <<<")
        r.utf(); self.save_stats()
        if self._tbl_lost_at is not None:
            self._tbl_lost_at = None; await asyncio.sleep(1.5); await self.create_table(); return
        log.info("[BOT] Ở lại bàn, ready sau 5s...")
        asyncio.create_task(self._delay_ready(5))

    async def _h_kick(self, r):
        r.i8(); r.utf()
        self.is_playing = False; self.in_table = False; self.pending = False
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
            if self.is_playing: self.in_table = False; self._tbl_lost_at = time.time()
            else: self.in_table = False; await asyncio.sleep(1); await self.create_table()
        elif self.is_playing:
            if self.opp_gone_at is None:
                self.opp_gone_at = time.time(); log.info("[BOT] Đối thủ rời giữa ván")
        elif self.in_table:
            await self.send(self.pkt_table())

    # --- Overrides for subclass ---
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
                    self.opp_gone_at = None; await self.send(self.pkt_table())
                if self._tbl_lost_at and time.time()-self._tbl_lost_at > 8:
                    self._tbl_lost_at = None; self.table_id = None; await self.create_table()
                if not self.is_playing and not self.in_table and not self._joining and not self._rejoining and self._bet_loaded:
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
                self._id_done = True; self._update_identity(s)
            gr = s.get(GAME_URL, timeout=10)
            self.cookie = '; '.join(f'{k}={v}' for k,v in s.cookies.items())
            tm = re.search(r'var\s+token\s*=\s*(-?\d+)', gr.text)
            nm = re.search(r"var\s+currentPlayerNickName\s*=\s*'([^']+)'", gr.text)
            pm = re.search(r'var\s+placePath\s*=\s*\"([^\"]+)\"', gr.text)
            if not tm or not nm: log.error('[BOT] Token/nick not found'); return False
            self.token = int(tm.group(1)); self.nick = nm.group(1)
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
            pat = re.compile(r'''buyAvatar\(\s*(["']?)(\d+)\1\s*,\s*(["'])(.*?)\3\s*,\s*(["']?)([\d,.]+)\5\s*\)''', re.I|re.S)
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
        fm = re.search(r'(?is)<form\b[^>]*name=["\']InputForm0["\'][^>]*>.*?</form>', html)
        if not fm: return None, None
        form = fm.group(0)
        ot = re.search(r'(?is)<form\b[^>]*>', form).group(0)
        ma = re.search(r'\baction\s*=\s*(["\'])(.*?)\1', ot, re.I|re.S)
        act = urljoin(url, html_lib.unescape(ma.group(2))) if ma else url
        data = {}
        for tag in re.findall(r'(?is)<input\b[^>]*>', form):
            nm = re.search(r'\bname\s*=\s*(["\'])(.*?)\1', tag, re.I|re.S)
            tp = re.search(r'\btype\s*=\s*(["\'])(.*?)\1', tag, re.I|re.S)
            vl = re.search(r'\bvalue\s*=\s*(["\'])(.*?)\1', tag, re.I|re.S)
            if not nm: continue
            t = (tp.group(2) if tp else '').lower()
            if t in ('submit','button','image','file','reset'): continue
            if t in ('checkbox','radio') and not re.search(r'\bchecked\b', tag, re.I): continue
            data[nm.group(2)] = html_lib.unescape(vl.group(2)) if vl else ''
        for m in re.finditer(r'(?is)<select\b([^>]*)>(.*?)</select>', form):
            sn = re.search(r'\bname\s*=\s*(["\'])(.*?)\1', '<select '+m.group(1)+'>', re.I|re.S)
            if not sn: continue
            sel = re.search(r'(?is)<option\b([^>]*\bselected\b[^>]*)>(.*?)</option>', m.group(2))
            if sel:
                sv = re.search(r'\bvalue\s*=\s*(["\'])(.*?)\1', '<option '+sel.group(1)+'>', re.I|re.S)
                data[sn.group(2)] = html_lib.unescape(sv.group(2)) if sv else ''
        for m in re.finditer(r'(?is)<textarea\b([^>]*)>(.*?)</textarea>', form):
            tn = re.search(r'\bname\s*=\s*(["\'])(.*?)\1', '<textarea '+m.group(1)+'>', re.I|re.S)
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
