#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
edax.py — bọc engine Othello Edax (https://github.com/abulmo/edax-reversi).

Edax là engine Othello mã nguồn mở mạnh nhất nhì hiện nay (được dùng để kiểm
chứng bài báo "Othello is Solved"). Không có nhị phân Linux dựng sẵn nên ta tự
biên dịch — chỉ mất khoảng 2 giây vì toàn bộ nguồn gộp vào một file all.c.

Giao thức chữ rất đơn giản:
    setboard <64 ký tự> <X|O>     X = đen, O = trắng, - = ô trống
    go                            -> in ra "Edax plays D3"
Thứ tự 64 ký tự là index = hàng*8 + cột, hàng 0 = hàng "1" — TRÙNG KHỚP với
cách playok đánh số ô (pos = cột + 8*hàng), nên khỏi phải đổi toạ độ.
"""
import os
import re
import shutil
import subprocess
import tarfile
import threading
import time
import urllib.request

ENGINE_DIR = os.environ.get("EDAX_DIR", "/tmp/edax")
SRC_URL = "https://github.com/abulmo/edax-reversi/archive/refs/tags/v4.6.tar.gz"
EVAL_URL = ("https://github.com/abulmo/edax-reversi/releases/download/"
            "v4.4/eval.7z")
# Thứ tự ưu tiên kiến trúc; máy không đủ lệnh mở rộng thì tụt xuống bản chung.
ARCHS = ["x86-64-v3", "x86-64-v2", "x86-64"]


def _bin_path():
    for a in ARCHS:
        p = os.path.join(ENGINE_DIR, "bin", f"lEdax-{a}")
        if os.path.exists(p):
            return p
    return None


def ensure_engine(log=print):
    """Tải nguồn + biên dịch + tải eval.dat nếu chưa có. Trả về đường dẫn binary."""
    b = _bin_path()
    if b and os.path.exists(os.path.join(ENGINE_DIR, "data", "eval.dat")):
        os.chmod(b, 0o755)
        return b

    os.makedirs(ENGINE_DIR, exist_ok=True)
    src = os.path.join(ENGINE_DIR, "src")
    if not os.path.exists(os.path.join(src, "all.c")):
        log("[EDAX] tải mã nguồn...")
        tgz = os.path.join(ENGINE_DIR, "edax.tar.gz")
        urllib.request.urlretrieve(SRC_URL, tgz)
        with tarfile.open(tgz) as t:
            t.extractall(ENGINE_DIR)
        root = next(os.path.join(ENGINE_DIR, d) for d in os.listdir(ENGINE_DIR)
                    if d.startswith("edax-reversi-"))
        for name in os.listdir(root):
            dst = os.path.join(ENGINE_DIR, name)
            if not os.path.exists(dst):
                shutil.move(os.path.join(root, name), dst)

    os.makedirs(os.path.join(ENGINE_DIR, "bin"), exist_ok=True)
    comp = "gcc" if shutil.which("gcc") else "clang"
    for arch in ARCHS:
        log(f"[EDAX] biên dịch ARCH={arch} COMP={comp} ...")
        r = subprocess.run(
            ["make", "build", f"ARCH={arch}", f"COMP={comp}", "OS=linux",
             f"CC={comp}"],
            cwd=src, capture_output=True, text=True)
        p = os.path.join(ENGINE_DIR, "bin", f"lEdax-{arch}")
        if r.returncode == 0 and os.path.exists(p):
            os.chmod(p, 0o755)
            # Máy build được chưa chắc chạy được (thiếu lệnh mở rộng CPU).
            t = subprocess.run([p, "-v"], capture_output=True, cwd=ENGINE_DIR)
            if t.returncode in (0, 1):
                log(f"[EDAX] dùng {os.path.basename(p)}")
                break
            log(f"[EDAX] {arch} biên dịch được nhưng không chạy -> thử bản thấp hơn")
            os.remove(p)
        else:
            log(f"[EDAX] {arch} biên dịch hỏng: {(r.stderr or '')[-200:]}")
    else:
        raise RuntimeError("không biên dịch được Edax")

    data = os.path.join(ENGINE_DIR, "data")
    if not os.path.exists(os.path.join(data, "eval.dat")):
        log("[EDAX] tải eval.dat (7 MB)...")
        pkg = os.path.join(ENGINE_DIR, "eval.7z")
        if not os.path.exists(pkg):
            urllib.request.urlretrieve(EVAL_URL, pkg)
        try:
            import py7zr
        except ImportError:
            subprocess.run(["pip", "install", "-q", "py7zr"], check=True)
            import py7zr
        with py7zr.SevenZipFile(pkg) as z:
            z.extractall(ENGINE_DIR)
    return _bin_path()


class Edax:
    """Mỗi nước: setboard thế cờ hiện tại rồi `go`, đọc dòng 'Edax plays XX'."""

    #: level 21 đã rất mạnh; 60 là chơi hoàn hảo nhưng cực chậm ở giữa ván.
    def __init__(self, level=18, log=print):
        self.level = level
        self.log = log
        self.proc = None
        self._lines = []
        self._lock = threading.Lock()

    def start(self):
        binp = ensure_engine(self.log)
        self.proc = subprocess.Popen(
            [binp, "-level", str(self.level)], cwd=ENGINE_DIR,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, text=True, bufsize=1)
        threading.Thread(target=self._reader, daemon=True).start()
        time.sleep(0.5)
        self.log(f"[EDAX] sẵn sàng (level {self.level})")
        return True

    def _reader(self):
        for line in self.proc.stdout:
            with self._lock:
                self._lines.append(line.rstrip())

    def _cmd(self, text):
        if self.proc and self.proc.poll() is None:
            self.proc.stdin.write(text + "\n")
            self.proc.stdin.flush()

    def _wait(self, pred, timeout):
        end = time.time() + timeout
        idx = 0
        while time.time() < end:
            with self._lock:
                cur, idx = list(self._lines[idx:]), len(self._lines)
            for l in cur:
                if pred(l):
                    return l
            time.sleep(0.02)
        return None

    def best_move(self, board_str, side_char, timeout=None):
        """board_str: 64 ký tự '-XO'; side_char: 'X' (đen) hoặc 'O' (trắng).

        Trả về chỉ số ô 0..63 (= cột + 8*hàng), hoặc None nếu hết nước / hết ván.
        """
        assert len(board_str) == 64, len(board_str)
        with self._lock:
            self._lines.clear()
        self._cmd(f"setboard {board_str} {side_char}")
        time.sleep(0.05)
        self._cmd("go")
        line = self._wait(
            lambda l: "Edax plays" in l or "Game Over" in l or "cannot move" in l,
            timeout or (self.level * 2 + 20))
        if not line or "Edax plays" not in line:
            self.log(f"[EDAX] không ra nước: {line!r}")
            return None
        m = re.search(r"Edax plays\s+([A-Ha-h])\s*([1-8])", line)
        if not m:
            return None
        col = ord(m.group(1).upper()) - 65
        row = int(m.group(2)) - 1
        return col + 8 * row

    def stop(self):
        try:
            self._cmd("quit")
            time.sleep(0.2)
            if self.proc:
                self.proc.terminate()
        except Exception:
            pass


if __name__ == "__main__":
    e = Edax(level=12)
    e.start()
    start = "-" * 27 + "OX" + "-" * 6 + "XO" + "-" * 27
    print("thế khởi đầu, đen đi:", e.best_move(start, "X"))
    print("sau D3, trắng đi   :", e.best_move(
        "-" * 19 + "X" + "-" * 7 + "XX" + "-" * 6 + "XO" + "-" * 27, "O"))
    e.stop()
