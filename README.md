# Caro Bot — Embryo Engine (gamevh.net)

Bot chơi Caro (Gomoku+) tự động trên gamevh.net, dùng engine **Embryo (Caro6)** — engine hiểu luật Caro Việt Nam (5 quân không bị chặn 2 đầu mới thắng) từ bên trong, nên **không cần lớp Python chặn quân**.

## Chạy thủ công

```bash
pip install -r requirements.txt
export GAMEVH_USER=ngan2
export GAMEVH_PASS=your_password
python3 embryo_caro_bot.py
```

Engine Embryo sẽ tự tải về từ GitHub ở lần chạy đầu.

## Chạy bằng GitHub Actions

Workflow `.github/workflows/caro-bot.yml` tự cài thư viện và chạy bot.
Cần cấu hình 2 secrets trong repo (Settings → Secrets → Actions):

- `GAMEVH_USER` — tài khoản gamevh.net
- `GAMEVH_PASS` — mật khẩu

## Cấu hình

| Biến môi trường | Mặc định | Ý nghĩa |
|---|---|---|
| `GAMEVH_USER` | ngan2 | Tài khoản gamevh.net |
| `GAMEVH_PASS` | nhat123456 | Mật khẩu |
| `BOT_RUNTIME_SECONDS` | 300 | Thời gian chạy (giây) |
