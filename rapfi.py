#!/usr/bin/env python3
"""
rapfi.py — bọc engine Rapfi (giao thức Gomocup) cho bàn 15x15 luật standard.

Rapfi là engine mà gomocalc.com dùng (bản WebAssembly). Ở đây chạy bản Linux
gốc nên mạnh và nhanh hơn nhiều, không cần trình duyệt.
"""
import os
import re
import subprocess
import threading
import time
import urllib.request

ENGINE_DIR = "/tmp/rapfi"
BIN = os.path.join(ENGINE_DIR, "pbrain-rapfi-linux-clang-avx2")
URL = "https://github.com/dhbloo/rapfi/releases/download/250615/Rapfi-engine.7z"
NEEDED = ["pbrain-rapfi-linux-clang-avx2", "config.toml",
          "model210901.bin", "mix9svqstandard_bs15.bin.lz4"]


def ensure_engine(log=print):
    if all(os.path.exists(os.path.join(ENGINE_DIR, f)) for f in NEEDED):
        os.chmod(BIN, 0o755)
        return BIN
    log("[ENGINE] Chưa có Rapfi, đang tải...")
    os.makedirs(ENGINE_DIR, exist_ok=True)
    pkg = "/tmp/Rapfi-engine.7z"
    if not os.path.exists(pkg):
        urllib.request.urlretrieve(URL, pkg)
    try:
        import py7zr
    except ImportError:
        subprocess.run(["pip", "install", "-q", "py7zr"], check=True)
        import py7zr
    with py7zr.SevenZipFile(pkg) as z:
        z.extract(path=ENGINE_DIR, targets=NEEDED)
    os.chmod(BIN, 0o755)
    fix_config(log)
    log("[ENGINE] Đã cài Rapfi")
    return BIN


def fix_config(log=print):
    """Bỏ các mục weight trỏ tới file KHÔNG tải về.

    config.toml gốc khai 3 bộ trọng số (freestyle / standard / renju) nhưng ta
    chỉ tải bộ standard. Rapfi nạp bộ ĐẦU TIÊN lúc START, không thấy file thì
    báo `ERROR Evaluator mix9svq failed to initialized` rồi âm thầm tụt xuống
    hàm lượng giá cổ điển — engine vẫn chạy nên rất dễ bỏ sót, nhưng yếu hẳn
    (cùng thế cờ: eval -183 khi hỏng, -499 khi nạp đúng mạng nơ-ron).
    """
    import re as _re
    cfg = os.path.join(ENGINE_DIR, "config.toml")
    if not os.path.exists(cfg):
        return
    with open(cfg, encoding="utf8") as fh:
        s = fh.read()
    blocks = _re.split(r"(?=\[\[model\.evaluator\.weights\]\])", s)
    out, dropped = [], 0
    for b in blocks:
        if b.startswith("[[model.evaluator.weights]]"):
            files = _re.findall(r'weight_file\w*\s*=\s*"([^"]+)"', b)
            if files and not all(
                    os.path.exists(os.path.join(ENGINE_DIR, f)) for f in files):
                # giữ lại phần đuôi (mục [search]... nằm sau khối cuối)
                tail = b.split("\n")
                keep = [l for l in tail if l.startswith("[") and
                        not l.startswith("[[model.evaluator.weights]]")]
                if keep:
                    out.append("\n".join(tail[tail.index(keep[0]):]))
                dropped += 1
                continue
        out.append(b)
    if dropped:
        with open(cfg, "w", encoding="utf8") as fh:
            fh.write("".join(out))
        log(f"[ENGINE] Bỏ {dropped} mục weight thiếu file trong config.toml")
    return BIN


class Rapfi:
    """Giao thức Gomocup: START / INFO / BEGIN / TURN / BOARD ... DONE -> "x,y"."""

    def __init__(self, size=15, rule=1, turn_ms=3000, log=print):
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
        if not self._wait_for(lambda l: l.strip() in ("OK",) or l.startswith("ERROR"), 20):
            raise RuntimeError("Rapfi không phản hồi START")
        self._cmd(f"INFO rule {self.rule}")
        self._cmd(f"INFO timeout_turn {self.turn_ms}")
        self._cmd("INFO timeout_match 0")
        self.log(f"[ENGINE] Rapfi sẵn sàng ({self.size}x{self.size}, rule={self.rule})")
        return True

    def _reader(self):
        for line in self.proc.stdout:
            line = line.rstrip()
            with self._lock:
                self._lines.append(line)
                if "Eval " in line:
                    m = re.search(r"Eval\s+(-?\d+)", line)
                    if m:
                        self.last_eval = int(m.group(1))
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
        line = self._wait_for(self._is_move, timeout or (self.turn_ms / 1000 + 5))
        if not line:
            return None
        x, y = [int(v) for v in line.split(",")]
        return x, y

    def best_move(self, my_moves, opp_moves, timeout=None):
        """Nạp toàn bộ thế cờ rồi hỏi nước đi. Trả về (x, y)."""
        with self._lock:
            self._lines.clear()
        cmd = "BOARD"
        for (x, y) in my_moves:
            cmd += f"\n{x},{y},1"
        for (x, y) in opp_moves:
            cmd += f"\n{x},{y},2"
        # Gomocup yêu cầu xen kẽ theo thứ tự đi; dùng dạng liệt kê đơn giản:
        self._cmd(self._interleave(my_moves, opp_moves))
        line = self._wait_for(self._is_move, timeout or (self.turn_ms / 1000 + 5))
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
                if b: seq.append((b.pop(0), 2))
                if a: seq.append((a.pop(0), 1))
            else:
                if a: seq.append((a.pop(0), 1))
                if b: seq.append((b.pop(0), 2))
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
    e = Rapfi()
    e.start()
    print("khai cuộc:", e.best_move([], []))
    print("sau khi đối thủ đi giữa bàn:", e.best_move([], [(7, 7)]))
    print("thế 2-2:", e.best_move([(7, 8)], [(7, 7), (8, 8)]))
    e.stop()
