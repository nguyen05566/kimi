#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
embryo.py — bọc engine Embryo (Hexik) bản Gomoku/Standard cho bàn 15x15.

Embryo là engine Gomocup (pbrain-) kiểu alpha-beta (dựa trên Stockfish), CPU,
Linux native, ~1MB. Bản "Gomoku/Standard" (= đúng 5, KHÔNG overline, KHÔNG cấm
nước) khớp ĐÚNG luật PlayOK. Nhanh (~1000 node/ms).

LƯU Ý về thời gian (đã test kỹ — Embryo quản lý giờ rất kỳ):
  - `INFO timeout_turn` đơn lẻ -> BỊ BỎ QUA (chạy >15s ở thế yên tĩnh -> hết giờ).
  - `INFO time_left` đơn lẻ     -> search quá ít (~22 node, rất yếu).
  - CHỈ khi gửi CẢ `timeout_turn` LẪN `time_left` thì Embryo mới dùng thời gian
    đúng: search sâu dần theo movetime, trả trong ~movetime/2, depth 14-28.
Wrapper gửi cả timeout_turn (ở start) + time_left (mỗi nước) + max_node (nắp
an toàn) để vừa mạnh vừa không bao giờ vượt giờ.

Cùng interface với rapfi.Rapfi / alphagomoku.AlphaGomoku.
"""
import bz2
import os
import re
import subprocess
import threading
import time
import urllib.request

ENGINE_DIR = "/tmp/embryo"
BIN = os.path.join(ENGINE_DIR, "pbrain-embryo")
URL = ("https://raw.githubusercontent.com/Hexik/Embryo_engine/master/"
       "Gomoku/Linux/pbrain-embryo-1.1.1-04d52e8e-s.bz2")


def ensure_engine(log=print):
    if os.path.exists(BIN) and os.access(BIN, os.X_OK):
        return BIN
    log("[ENGINE] Chưa có Embryo, đang tải...")
    os.makedirs(ENGINE_DIR, exist_ok=True)
    bz2_path = BIN + ".bz2"
    req = urllib.request.Request(URL, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=120) as r, open(bz2_path, "wb") as f:
        f.write(r.read())
    with open(bz2_path, "rb") as s, open(BIN, "wb") as d:
        d.write(bz2.decompress(s.read()))
    os.remove(bz2_path)
    os.chmod(BIN, 0o755)
    log("[ENGINE] Đã cài Embryo (Gomoku/Standard, Linux)")
    return BIN


class Embryo:
    """Giao thức Gomocup: START / INFO max_node / BOARD ... DONE -> "x,y"."""

    def __init__(self, size=15, rule=1, turn_ms=3000, log=print):
        # rule bị bỏ qua: Embryo bản Gomoku đã cố định luật Standard.
        self.size, self.turn_ms, self.log = size, turn_ms, log
        self.proc = None
        self.last_eval = None
        self._lines = []
        self._lock = threading.Lock()

    def start(self):
        ensure_engine(self.log)
        self.proc = subprocess.Popen(
            [BIN], cwd=ENGINE_DIR, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, text=True, bufsize=1)
        threading.Thread(target=self._reader, daemon=True).start()
        self._cmd(f"START {self.size}")
        if not self._wait_for(lambda l: l.strip() in ("OK",) or l.startswith("ERROR"), 20):
            raise RuntimeError("Embryo không phản hồi START")
        # Embryo chỉ quản lý thời gian đúng khi CẢ timeout_turn LẪN time_left
        # cùng được gửi (đơn lẻ: timeout_turn bị bỏ qua -> >15s; time_left đơn lẻ
        # -> search quá ít ~22 node). Gửi cả hai + max_node làm nắp an toàn.
        self._cmd(f"INFO timeout_turn {self.turn_ms}")
        self.log(f"[ENGINE] Embryo sẵn sàng ({self.size}x{self.size}, Gomoku/Standard)")
        return True

    def _reader(self):
        for line in self.proc.stdout:
            line = line.rstrip()
            with self._lock:
                self._lines.append(line)
                # MESSAGE depth 5-2 ev +M5 n 2053 n/ms 256 tm 8 pv ...
                if line.startswith("MESSAGE"):
                    m = re.search(r"\bev\s+(\S+)", line)
                    if m:
                        tok = m.group(1)
                        mm = re.fullmatch(r"([+-])M(\d+)", tok)
                        if mm:                       # chiếu tướng: +M5 / -M3
                            self.last_eval = 100000 * (1 if mm.group(1) == "+" else -1)
                        else:
                            try:
                                self.last_eval = float(tok)
                            except ValueError:
                                pass
                if line.startswith("ERROR"):
                    self.log("[ENGINE] " + line)

    def _cmd(self, text):
        if self.proc and self.proc.poll() is None:
            self.proc.stdin.write(text + "\n")
            self.proc.stdin.flush()

    def _wait_for(self, pred, timeout):
        end = time.time() + timeout
        idx = 0
        while time.time() < end:
            with self._lock:
                cur = list(self._lines[idx:])
                idx = len(self._lines)
            for l in cur:
                if pred(l):
                    return l
            time.sleep(0.02)
        return None

    @staticmethod
    def _is_move(line):
        p = line.strip().split(",")
        return len(p) == 2 and all(x.strip().lstrip("-").isdigit() for x in p)

    def best_move_ordered(self, ordered, timeout=None):
        """ordered = [(x, y, who)] theo ĐÚNG thứ tự đã đi, who: 1=mình, 2=đối thủ."""
        with self._lock:
            self._lines.clear()
        self.last_eval = None
        # Embryo dùng thời gian đúng khi có timeout_turn (đã gửi ở start) + time_left.
        # Thêm max_node làm nắp an toàn (dù time_left đã chặn). Test: ~movetime/2,
        # depth 14-28, 1.6-3.2 triệu node -> mạnh và không vượt giờ.
        self._cmd(f"INFO time_left {self.turn_ms}")
        self._cmd(f"INFO max_node {max(20000, int(self.turn_ms * 800))}")
        cmd = "BOARD"
        for (x, y, who) in ordered:
            cmd += f"\n{x},{y},{who}"
        self._cmd(cmd + "\nDONE")
        line = self._wait_for(self._is_move, timeout or (self.turn_ms / 1000 + 8))
        if not line:
            return None
        x, y = [int(v) for v in line.split(",")]
        return x, y

    def best_move(self, my_moves, opp_moves, timeout=None):
        with self._lock:
            self._lines.clear()
        self._cmd(self._interleave(my_moves, opp_moves))
        line = self._wait_for(self._is_move, timeout or (self.turn_ms / 1000 + 8))
        if not line:
            return None
        x, y = [int(v) for v in line.split(",")]
        return x, y

    @staticmethod
    def _interleave(my_moves, opp_moves):
        seq = []
        a, b = list(my_moves), list(opp_moves)
        first_is_opp = len(b) > len(a)
        while a or b:
            if first_is_opp:
                if b:
                    seq.append((b.pop(0), 2))
                if a:
                    seq.append((a.pop(0), 1))
            else:
                if a:
                    seq.append((a.pop(0), 1))
                if b:
                    seq.append((b.pop(0), 2))
        out = "BOARD"
        for (pos, who) in seq:
            out += f"\n{pos[0]},{pos[1]},{who}"
        return out + "\nDONE"

    def stop(self):
        try:
            self._cmd("END")
            time.sleep(0.2)
            if self.proc:
                self.proc.terminate()
        except Exception:
            pass


if __name__ == "__main__":
    e = Embryo(turn_ms=2000)
    e.start()
    print("khai cuộc (đen):", e.best_move_ordered([]))
    print("trắng đáp (7,7):", e.best_move_ordered([(7, 7, 2)]), "eval=", e.last_eval)
    e.stop()
