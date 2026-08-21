#!/bin/bash
# Đồng bộ bot sang repo. Dùng script này thay vì copy tay: trước đây copy đè
# bot_xq.py lên playok_xq.py đã XOÁ MẤT phần đọc biến môi trường và làm
# workflow cờ tướng crash ngay khi khởi động (NameError: name 'os').
set -e
SRC=/home/user/playok-bot
DST=/home/user/repo
cp "$SRC/bot_xq.py"  "$DST/playok_xq.py"
cp "$SRC/rapfi.py"   "$DST/rapfi.py"
cp "$SRC/PROTOCOL.md" "$DST/PLAYOK_PROTOCOL.md"
sed 's/^from bot_xq import (/from playok_xq import (/' "$SRC/bot_gm.py" > "$DST/playok_gm.py"
cd "$DST"
python3 - <<'PY'
import subprocess, sys
for f in ("playok_xq.py", "playok_gm.py", "rapfi.py"):
    r = subprocess.run([sys.executable, f, "--help"], capture_output=True, text=True) \
        if f != "rapfi.py" else None
    if r is not None and r.returncode != 0:
        print(f"[SYNC] {f} HỎNG:\n{r.stderr}"); sys.exit(1)
    print(f"[SYNC] {f} chạy được")
PY
