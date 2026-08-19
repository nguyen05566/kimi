#!/usr/bin/env python3
"""
REGISTER STANDALONE - Đăng ký tài khoản GameVH qua WebSocket (như APK)
- Không liên quan bot, chỉ đăng ký
- Lấy captcha GET_CAPTCHA_IMAGE (160x50) -> lưu captcha.jpg -> giải tự động
- Gửi REGISTER provider=PS_VH + captcha + clientId
Đã test: tạo thành công test91874 / Pass661343! (clientId 18490563) với captcha t8fd
Cập nhật 2025-08-10: Thêm chế độ giải captcha tự động qua ddddocr (chính xác ~95%)
"""
import asyncio, websockets, struct, os, random, sys, pathlib, time, re

WS_URL = "wss://gamevh.net/ws/gameServer"


def split_name_number(name):
    """Tách tên đầy đủ có số (vd 'nguyen7') -> (prefix='nguyen', num=7)."""
    m = re.search(r'^(.*?)(\d+)$', name)
    if m:
        return m.group(1), int(m.group(2))
    return name, None


def build_usernames(name, count=10):
    """Từ 1 tên (vd 'nguyen7') sinh danh sách count tên tăng dần: nguyen7..nguyen16."""
    prefix, num = split_name_number(name)
    if num is None:
        return [f"{prefix}{i}" for i in range(1, count + 1)]
    return [f"{prefix}{num + i}" for i in range(count)]



class Writer:
    def __init__(self): self.parts=[]
    def i8(self,v): self.parts.append(struct.pack('>b',v))
    def i32(self,v): self.parts.append(struct.pack('>i',v))
    def i64(self,v): self.parts.append(struct.pack('>q',v))
    def write_ascii(self,s):
        b=s.encode('ascii'); self.parts.append(struct.pack('>B', len(b))); self.parts.append(b)
    def write_string(self,s):
        b=s.encode('utf-16-be'); self.parts.append(struct.pack('>h', len(b)//2)); self.parts.append(b)
    def write_command(self,cmd):
        b=cmd.encode('ascii'); self.i8(-len(b)); self.parts.append(b)
    def build(self): return b''.join(self.parts)

def gen_user():
    prefix = os.environ.get("REGISTER_PREFIX", "test")
    return f"{prefix}{random.randint(10000,99999)}"

def gen_pass():
    return "nhat123456"  # mật khẩu cố định

def solve_captcha_auto(image_path):
    """Thử giải captcha tự động: ddddocr -> pytesseract -> None"""
    # 1. ddddocr (chính xác nhất)
    try:
        import ddddocr
        ocr = ddddocr.DdddOcr(show_ad=False)
        with open(image_path, 'rb') as f:
            res = ocr.classification(f.read())
        clean = ''.join(c for c in res if c.isalnum())
        if len(clean) >= 3:
            print(f"[OCR] ddddocr: {res!r} -> clean {clean!r}")
            return clean
    except Exception as e:
        print(f"[OCR] ddddocr fail: {e}")

    # 2. pytesseract fallback
    try:
        from PIL import Image
        import pytesseract
        im = Image.open(image_path)
        # thử nhiều config
        for config in ['--psm 8 -c tessedit_char_whitelist=ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789', '--psm 7']:
            txt = pytesseract.image_to_string(im, config=config).strip().replace(" ","").replace("\n","")
            clean = ''.join(c for c in txt if c.isalnum())
            if len(clean) >= 3:
                print(f"[OCR] pytesseract {config[:15]} -> {clean!r}")
                return clean
        # thử với upscale + threshold
        try:
            im2 = im.convert("L").resize((im.width*3, im.height*3), Image.LANCZOS)
            import PIL.ImageOps
            im2 = PIL.ImageOps.autocontrast(im2)
            im2 = im2.point(lambda x: 255 if x > 140 else 0, mode='1')
            txt = pytesseract.image_to_string(im2, config='--psm 8 -c tessedit_char_whitelist=ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789').strip()
            clean = ''.join(c for c in txt if c.isalnum())
            if len(clean) >=3:
                print(f"[OCR] pytesseract preprocessed -> {clean!r}")
                return clean
        except: pass
    except Exception as e:
        print(f"[OCR] pytesseract fail: {e}")

    return None

async def get_captcha(ws):
    w=Writer(); w.write_command("GET_CAPTCHA_IMAGE"); w.i32(160); w.i32(50)
    await ws.send(w.build())
    raw=await asyncio.wait_for(ws.recv(), timeout=8)
    cmd_len=1+len("GET_CAPTCHA_IMAGE")
    status=raw[cmd_len]
    if status != 0:
        print(f"[CAPTCHA] status={status} lạ")
    length=struct.unpack_from('>H', raw, cmd_len+1)[0]
    img=raw[cmd_len+1+2:cmd_len+1+2+length]
    clientId=struct.unpack_from('>q', raw, cmd_len+1+2+length)[0]
    return img, clientId

async def register_once(ws, user, pwd, captcha, clientId, provider="PS_VH", imei=None):
    if imei is None:
        imei="".join(random.choice("0123456789") for _ in range(15))
    w=Writer(); w.write_command("REGISTER")
    w.write_ascii(provider); w.write_ascii(user); w.write_string(pwd)
    w.write_ascii(captcha); w.i64(clientId); w.write_ascii(imei)
    await ws.send(w.build())
    raw=await asyncio.wait_for(ws.recv(), timeout=8)
    # Response: 01 53 (339) + status(1) + maybe string
    if len(raw) >=3 and raw[0]==0x01 and raw[1]==0x53:
        status=raw[2]
        if status==0:
            print(f"[REGISTER] OK {user}")
            return True, ""
        else:
            try:
                n=struct.unpack_from('>h', raw, 3)[0]
                msg=raw[5:5+n*2].decode('utf-16-be', errors='replace') if n>0 else f"status={status}"
            except:
                msg=raw[3:].hex()[:200]
            print(f"[REGISTER] FAIL {msg}")
            return False, msg
    print(f"[REGISTER] lạ {raw.hex()[:500]}")
    return False, "unknown"

async def register_single(user, pwd, captcha_arg=None, max_attempts=5):
    """Đăng ký 1 tài khoản, trả về (ok: bool, msg: str)."""
    # Nếu có captcha + clientId cũ, thử ngay
    if captcha_arg and os.path.exists("/tmp/captcha_clientId2.txt"):
        try:
            clientId = int(open("/tmp/captcha_clientId2.txt").read().strip())
            ws = await websockets.connect(WS_URL, additional_headers={"Origin":"https://gamevh.net","User-Agent":"Mozilla/5.0"}, max_size=2**20, ping_interval=None)
            ok, msg = await register_once(ws, user, pwd, captcha_arg, clientId)
            await ws.close()
            if ok:
                return True, ""
        except Exception as e:
            print(f"Thử captcha cũ fail: {e}")

    for attempt in range(1, max_attempts + 1):
        print(f"\n  --- {user}: lần thử {attempt}/{max_attempts} ---")
        ws = await websockets.connect(WS_URL, additional_headers={"Origin":"https://gamevh.net","User-Agent":"Mozilla/5.0"}, max_size=2**20, ping_interval=None)
        try:
            img, clientId = await get_captcha(ws)
            await ws.close()
        except Exception as e:
            print(f"  Lấy captcha fail: {e}")
            try: await ws.close()
            except: pass
            continue

        cap_path = "/home/user/captcha_register.jpg" if os.path.exists("/home/user") else "/tmp/captcha_register.jpg"
        for p in [cap_path, "/tmp/captcha_register.jpg"]:
            try: open(p, "wb").write(img)
            except: pass
        open("/tmp/captcha_clientId2.txt", "w").write(str(clientId))

        captcha = captcha_arg
        if not captcha:
            captcha = solve_captcha_auto(cap_path)
            if captcha:
                print(f"  [AUTO] captcha: {captcha!r}")
            else:
                print(f"  [AUTO] không giải được, đợi nhập tay 60s...")
                for _ in range(60):
                    if os.path.exists("/tmp/captcha_answer.txt"):
                        captcha = open("/tmp/captcha_answer.txt").read().strip()
                        if captcha: break
                    captcha = os.environ.get("REGISTER_CAPTCHA")
                    if captcha: break
                    await asyncio.sleep(1)
                if not captcha and not os.environ.get("GITHUB_ACTIONS"):
                    try:
                        captcha = input(f"  Nhập captcha trong {cap_path}: ").strip()
                    except: pass
                if not captcha:
                    print("  Chưa có captcha")
                    continue

        ws2 = await websockets.connect(WS_URL, additional_headers={"Origin":"https://gamevh.net","User-Agent":"Mozilla/5.0"}, max_size=2**20, ping_interval=None)
        ok, msg = await register_once(ws2, user, pwd, captcha, clientId)
        await ws2.close()
        if ok:
            return True, ""
        print(f"  Thất bại: {msg}, thử captcha mới")
        try: os.remove("/tmp/captcha_answer.txt")
        except: pass
        captcha_arg = None

    return False, "hết số lần thử"


async def main():
    # Tham số: tên cơ sở (vd nguyen7), số lượng (mặc định 10), mật khẩu cố định nhat123456
    base_name = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("REGISTER_USER") or gen_user()
    count = int(sys.argv[2]) if len(sys.argv) > 2 else int(os.environ.get("REGISTER_COUNT", "10"))
    pwd = sys.argv[3] if len(sys.argv) > 3 else os.environ.get("REGISTER_PASS") or gen_pass()
    captcha_arg = sys.argv[4] if len(sys.argv) > 4 else os.environ.get("REGISTER_CAPTCHA")

    usernames = build_usernames(base_name, count)

    print("=" * 50)
    print(f"ĐĂNG KÝ {count} TÀI KHOẢN LIÊN TIẾP")
    print(f"Tên cơ sở : {base_name}")
    print(f"Danh sách : {', '.join(usernames)}")
    print(f"Mật khẩu  : {pwd}")
    print("=" * 50)

    created = []
    for i, user in enumerate(usernames, 1):
        print(f"\n[{i}/{count}] Đang đăng ký {user} ...")
        ok, _ = await register_single(user, pwd, captcha_arg)
        if ok:
            print(f"[{i}/{count}] THÀNH CÔNG: {user}")
            created.append((user, pwd))
            captcha_arg = None  # captcha chỉ dùng 1 lần
        else:
            print(f"[{i}/{count}] THẤT BẠI: {user} (bỏ qua, tiếp tục)")
        await asyncio.sleep(1)  # tránh spam server

    print("\n" + "=" * 50)
    print(f"TỔNG KẾT: {len(created)}/{count} tài khoản thành công")
    for u, p in created:
        print(f"  {u} / {p}")
    if created:
        with open("/tmp/new_accounts.txt", "w") as f:
            for u, p in created:
                f.write(f"{u}\n{p}\n")
        print(f"Đã ghi /tmp/new_accounts.txt ({len(created)} dòng)")
    print("=" * 50)


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
