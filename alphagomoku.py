#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
alphagomoku.py — bọc engine AlphaGomoku (MaciejKozarzewski) cho bàn 15x15.

AlphaGomoku là engine Gomocup (tiền tố pbrain-), dùng MCTS + mạng nơ-ron tích
chập. Nói cùng giao thức START/INFO/BOARD...DONE/TURN/BEGIN/END như Rapfi nên
dùng thay rapfi.py 1-1 (cùng interface: start/best_move_ordered/best_move/stop
/last_eval). Bản CPU (pbrain-AlphaGomoku) chạy được trên GitHub Actions, không
cần GPU.

MÃ LUẬT (đã test thực tế trên bản 5.9.3):
    0 = freestyle  -> 5+ đều thắng (overline 6+ cũng thắng), KHÔNG cấm nước
    1 = standard   -> ĐÚNG 5 mới thắng (overline 6+ KHÔNG thắng), KHÔNG cấm nước
    4 = renju      -> đúng 5, CẤM nước với ĐEN (3-3, 4-4, overline)

PlayOK gomoku (đọc gm.js: điều kiện thắng là `5 == 1+số_đứng_trước+số_đứng_sau`)
đòi ĐÚNG 5, KHÔNG tính overline, KHÔNG cấm nước -> khớp ĐÚNG rule=1 (standard).
Đừng dùng rule=0 (engine sẽ coi overline là thắng, nhưng PlayOK không công nhận)
hay rule=4 (cấm nước đen vô lý ở PlayOK).
"""
import os
import re
import subprocess
import threading
import time
import urllib.request
import zipfile

ENGINE_DIR = "/tmp/alphagomoku"
BIN = os.path.join(ENGINE_DIR, "pbrain-AlphaGomoku")
URL = ("https://github.com/MaciejKozarzewski/AlphaGomoku/releases/"
       "download/v5.9.3/AlphaGomoku_linux.zip")


def ensure_engine(log=print):
    """Tải AlphaGomoku (bản CPU + config + mạng + mở bộ swap2) về ENGINE_DIR."""
    if os.path.exists(BIN) and os.access(BIN, os.X_OK):
        return BIN
    log("[ENGINE] Chưa có AlphaGomoku, đang tải...")
    os.makedirs(ENGINE_DIR, exist_ok=True)
    pkg = "/tmp/AlphaGomoku_linux.zip"
    if not os.path.exists(pkg):
        urllib.request.urlretrieve(URL, pkg)
    with zipfile.ZipFile(pkg) as z:
        names = z.namelist()
        # Lấy tất cả TRỪ 2 bản GPU (cuda/opencl ~46MB) cho nhẹ.
        targets = [n for n in names
                   if "cuda" not in n.lower() and "opencl" not in n.lower()]
        z.extractall(ENGINE_DIR, members=targets)
    os.chmod(BIN, 0o755)
    log("[ENGINE] Đã cài AlphaGomoku (CPU)")
    return BIN


class AlphaGomoku:
    """Giao thức Gomocup: START / INFO / BOARD ... DONE -> "x,y".

    Cùng interface với rapfi.Rapfi để playok_gm.py đổi engine 1 dòng.
    """

    def __init__(self, size=15, rule=0, turn_ms=3000, log=print):
        self.size, self.rule, self.turn_ms, self.log = size, rule, turn_ms, log
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
        if not self._wait_for(
                lambda l: l.strip() in ("OK",) or l.startswith("ERROR"), 20):
            raise RuntimeError("AlphaGomoku không phản hồi START")
        self._cmd(f"INFO rule {self.rule}")
        self._cmd(f"INFO timeout_turn {self.turn_ms}")
        self._cmd("INFO timeout_match 0")
        self.log(f"[ENGINE] AlphaGomoku sẵn sàng "
                 f"({self.size}x{self.size}, rule={self.rule})")
        return True

    def _reader(self):
        for line in self.proc.stdout:
            line = line.rstrip()
            with self._lock:
                self._lines.append(line)
                # Dòng: MESSAGE depth 1-1 ev W5 winrate 99.9 drawrate 0.0 ...
                if line.startswith("MESSAGE"):
                    m = re.search(r"winrate\s+(-?\d+(?:\.\d+)?)", line)
                    if m:
                        wr = float(m.group(1))
                        if wr > 1.0:
                            wr /= 100.0          # thường là thang 0..100
                        # quy ra eval từ góc nhìn bên đang đi: dương = có lợi
                        self.last_eval = (wr - 0.5) * 2000.0
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
        """Nạp toàn bộ thế cờ rồi hỏi nước đi. Trả về (x, y)."""
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
        """Dựng lệnh BOARD với đúng thứ tự luân phiên (1 = mình, 2 = đối thủ)."""
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
    e = AlphaGomoku(rule=0)
    e.start()
    print("khai cuộc (đen):", e.best_move_ordered([]))
    print("sau khi đối thủ đi giữa bàn (trắng):", e.best_move_ordered([(7, 7, 2)]))
    print("last_eval:", e.last_eval)
    e.stop()
