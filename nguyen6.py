#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════════════════════════╗
║  BOT CARO JAX v5.1                                               ║
║  Engine: JAX StandardCaro (rule 8)                               ║
║  Cửa sổ trượt 15×15 trên bàn 15×19                              ║
║  Luật: 6+ liên tiếp thắng, 5 chặn 2 đầu không thắng            ║
╚══════════════════════════════════════════════════════════════════╝
"""
import asyncio, os, time, logging, selectors
from typing import Optional, Tuple, List
from pathlib import Path
from caro_base import (
    log, find_wine, _BASE_DIR, GameClient, SlidingWindow,
    GAMEVH_W, GAMEVH_H, EMPTY, CROSS, CIRCLE, RUNTIME
)

# ======================== CONFIG ========================
ENGINE_DIR = _BASE_DIR / "jax-engine" / "JAX25"
ENGINE_BIN = "pbrain-Jax.exe"
ENGINE_RULE = 8  # StandardCaro
ENGINE_TIMEOUT = 2000
ENGINE_BOARD = 15

# ======================== ENGINE ========================
class JaxEngine:
    def __init__(self):
        self.binary = str(ENGINE_DIR / ENGINE_BIN)
        self.wine = find_wine()
        self.proc = None; self.sel = None
        self.buf = bytearray()
        self.win = SlidingWindow()
        self.ok = False; self.my = 1

    def start(self, my_sym=1):
        self.stop()
        if not self.wine: log.warning("[JAX] No wine!"); return False
        import subprocess, os as _os
        env = _os.environ.copy()
        env["WINEPREFIX"] = str(_BASE_DIR / ".wine"); env["WINEDEBUG"] = "-all"
        try:
            self.proc = subprocess.Popen(
                [self.wine, self.binary],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                cwd=str(ENGINE_DIR), env=env)
            self.buf = bytearray(); self.my = my_sym
            self._init_sel()
            time.sleep(2); self._drain()
            self._send(f"INFO rule {ENGINE_RULE}")
            self._send(f"START {ENGINE_BOARD}")
            for _ in range(10):
                if self._read(1.0).upper() == "OK": break
            self._send(f"INFO timeout_turn {ENGINE_TIMEOUT}")
            self._send("INFO ponder 1")
            time.sleep(0.1); self._drain()
            self.ok = True
            log.info(f"[JAX] Started rule={ENGINE_RULE} board={ENGINE_BOARD}")
            return True
        except Exception as e:
            log.error(f"[JAX] Start err: {e}"); self.ok = False; return False

    def restart(self):
        if not self.proc or self.proc.poll() is not None: return False
        self.win.reset()
        self._send("RESTART")
        for _ in range(5):
            if self._read(2.0).upper() == "OK": break
        self._send(f"INFO rule {ENGINE_RULE}")
        self._send(f"START {ENGINE_BOARD}")
        for _ in range(5):
            if self._read(2.0).upper() == "OK": return True
        return True

    def get_move(self, hist: list, my: int) -> Optional[Tuple[int,int]]:
        """hist: [(x,y,sym)] tọa độ gamevh. Trả (x,y) gamevh."""
        if not self.ok or not self.proc or self.proc.poll() is not None:
            return None
        self._drain()
        self._send(f"INFO timeout_turn {ENGINE_TIMEOUT}")
        self._send(f"INFO time_left {ENGINE_TIMEOUT*20}")

        # Gửi toàn bộ bàn qua BOARD
        self._send("BOARD")
        sent = 0
        for (x,y,sym) in hist:
            if not self.win.in_window(y): continue
            c = 1 if sym==self.my else 2
            ex,ey = self.win.to_eng(x,y)
            if 0<=ex<ENGINE_BOARD and 0<=ey<ENGINE_BOARD:
                self._send(f"{ex},{ey},{c}"); sent += 1
        self._send("DONE")

        deadline = time.monotonic() + (ENGINE_TIMEOUT/1000.0) + 5.0
        while time.monotonic() < deadline:
            line = self._read(min(1.0, deadline-time.monotonic()))
            if not line: continue
            if line.startswith(("MESSAGE","DEBUG","ERROR")): continue
            if "," in line:
                parts = line.split(",")
                if len(parts) == 2:
                    try: ex,ey = int(parts[0].strip()), int(parts[1].strip())
                    except: continue
                    if 0<=ex<ENGINE_BOARD and 0<=ey<ENGINE_BOARD:
                        gx,gy = self.win.to_gvh(ex,ey)
                        if 0<=gx<GAMEVH_W and 0<=gy<GAMEVH_H:
                            return gx,gy
        return None

    def stop(self):
        if self.proc:
            try: self._send("END")
            except: pass
            try: self.proc.terminate(); self.proc.wait(3)
            except:
                try: self.proc.kill()
                except: pass
            self.proc = None; self.ok = False
        self._close_sel()

    def _init_sel(self):
        self._close_sel()
        if self.proc and self.proc.stdout:
            try:
                self.sel = selectors.DefaultSelector()
                self.sel.register(self.proc.stdout, selectors.EVENT_READ)
            except: self.sel = None

    def _close_sel(self):
        if self.sel:
            try: self.sel.close()
            except: pass
            self.sel = None

    def _send(self, cmd):
        if self.proc and self.proc.poll() is None:
            try:
                self.proc.stdin.write((cmd+"\n").encode())
                self.proc.stdin.flush()
            except: pass

    def _read(self, timeout=10.0):
        if not self.proc or self.proc.poll() is not None: return ""
        deadline = time.monotonic() + timeout
        while True:
            idx = self.buf.find(b"\n")
            if idx >= 0:
                line = bytes(self.buf[:idx]).strip()
                del self.buf[:idx+1]
                return line.decode("utf-8", errors="replace")
            rem = deadline - time.monotonic()
            if rem <= 0: return ""
            try:
                if self.sel: ready = self.sel.select(timeout=min(rem,1.0))
                else:
                    s = selectors.DefaultSelector()
                    s.register(self.proc.stdout, selectors.EVENT_READ)
                    ready = s.select(timeout=min(rem,1.0)); s.close()
                if ready:
                    chunk = os.read(self.proc.stdout.fileno(), 4096)
                    if not chunk: return ""
                    self.buf.extend(chunk)
            except: return ""

    def _drain(self):
        while self._read(0.01): pass

# ======================== BOT ========================
class CaroBot(GameClient):
    def __init__(self):
        super().__init__()
        self.engine = None; self.eng_ok = False
        self.eng_moves = 0; self.eng_errs = 0

    def init_engine(self):
        if self.engine: return self.eng_ok
        self.engine = JaxEngine()
        self.eng_ok = self.engine.start(self.my_sym)
        if self.eng_ok: log.info(f"[JAX] OK! Window={self.engine.win.range_str()}")
        return self.eng_ok

    async def on_table_update(self):
        if self.engine and hasattr(self.engine, 'win'):
            for (x,y,sym) in self.board.hist:
                self.engine.win.update(y)
            log.info(f"[WIN] {self.engine.win.range_str()} (off={self.engine.win.off})")

    async def on_game_start(self):
        if not self.engine:
            self.init_engine()
        else:
            self.engine.restart()

    async def on_move(self, x, y, sym):
        if self.engine and hasattr(self.engine, 'win'):
            shifted = self.engine.win.update(y)
            if shifted:
                log.info(f"[WIN] Shift on MOVE ({x},{y})")

    async def do_move(self):
        if not self.is_playing or not self.running or self.slot < 0: return
        if self._moving: return
        self._moving = True; self.pending = False; self._last_xy = None
        try:
            t0 = time.time(); x,y = -1,-1
            if self.eng_ok:
                try:
                    hist = list(self.board.hist)
                    mv = await asyncio.get_event_loop().run_in_executor(
                        None, lambda: self.engine.get_move(hist, self.my_sym))
                    if mv and 0<=mv[0]<self.board.w and 0<=mv[1]<self.board.h and self.board.get(*mv)==EMPTY:
                        x,y = mv; self.eng_moves += 1
                    else:
                        self.eng_errs += 1
                        log.warning(f"[JAX] Invalid: {mv}, fallback")
                        lx,ly = hist[-1][:2] if hist else (7,9)
                        x,y = self.board.empty_near(lx,ly)
                        self.engine.restart()
                except Exception as e:
                    self.eng_errs += 1; log.warning(f"[JAX] Err: {e}")
                    try: self.engine.stop(); self.engine = None; self.eng_ok = False
                    except: pass
                    lx,ly = self.board.hist[-1][:2] if self.board.hist else (7,9)
                    x,y = self.board.empty_near(lx,ly)
            else:
                lx,ly = self.board.hist[-1][:2] if self.board.hist else (7,9)
                x,y = self.board.empty_near(lx,ly)
            dt = time.time()-t0
            log.info(f"MOVE ({x},{y}) {dt:.2f}s [JAX]")
            await self.send(self.pkt_play(self.board.xy2pos(x,y)))
            self._last_xy = (x,y); self.board.put(x,y,self.my_sym)
        finally: self._moving = False

    def stop_engine(self):
        if self.engine: self.engine.stop(); self.engine = None; self.eng_ok = False

# ======================== MAIN ========================
def main():
    b = str(ENGINE_DIR / ENGINE_BIN)
    w = find_wine()
    if Path(b).exists() and w:
        print(f"[OK] JAX: {ENGINE_BIN} via {w}")
    else:
        print(f"[!] JAX not found: {b}")
    try: asyncio.get_running_loop().create_task(_run())
    except RuntimeError: asyncio.run(_run())

async def _run():
    try:
        bot = CaroBot()
        bot.start_time = time.time(); bot._running = True
        log.info("="*50)
        log.info("BOT CARO JAX v5.1 - StandardCaro + Sliding Window")
        log.info("="*50)
        rc = 0
        while bot.running:
            if time.time()-bot.start_time > RUNTIME: break
            was = bot.in_table or bot.is_playing
            bot._want_rejoin = was and bot.table_id is not None and bot._rejoin_n < 2
            bot.is_playing = False; bot.pending = False
            bot.in_table = False; bot.ready = False
            bot.board.__init__(); bot.players.clear()
            bot.bet_amts = []; bot._bet_id = None
            bot._bet_loaded = False; bot._joining = False
            bot.opp_gone_at = None; bot._tbl_lost_at = None
            bot.stop_engine()
            ok = await asyncio.get_event_loop().run_in_executor(None, bot.http_login)
            if not ok:
                rc += 1; d = min(30*(2**(rc-1)),300)
                rem = RUNTIME-(time.time()-bot.start_time)
                if rem <= 0: break
                log.warning(f'[BOT] Login fail; retry {min(d,rem):.0f}s')
                await asyncio.sleep(min(d,rem)); continue
            rc = 0
            await bot.run_ws()
            if not (bot.in_table or bot.is_playing): bot.table_id = None
            bot.save_stats(); bot.stop_engine()
    except KeyboardInterrupt: log.info("[BOT] Stopped")
    except Exception as e: log.error(f"[BOT] Fatal: {e}", exc_info=True)

if __name__ == "__main__": main()
