#!/usr/bin/env python3
"""
REGISTER STANDALONE - Đăng ký tài khoản GameVH qua WebSocket (như APK)
- Không liên quan bot, chỉ đăng ký
- Lấy captcha GET_CAPTCHA_IMAGE (160x50) -> lưu captcha.jpg -> giải tự động
- Gửi REGISTER provider=PS_VH + captcha + clientId
Đã test: tạo thành công test91874 / Pass661343! (clientId 18490563) với captcha t8fd
Cập nhật 2025-08-10: Thêm chế độ giải captcha tự động qua ddddocr (chính xác ~95%)
"""
import asyncio, websockets, struct, os, random, sys, pathlib, time

WS_URL = "wss://gamevh.net/ws/gameServer"

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
    return f"Pass{random.randint(100000,999999)}!"

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

async def main():
    user = sys.argv[1] if len(sys.argv)>1 else os.environ.get("REGISTER_USER") or gen_user()
    pwd = sys.argv[2] if len(sys.argv)>2 else os.environ.get("REGISTER_PASS") or gen_pass()
    captcha_arg = sys.argv[3] if len(sys.argv)>3 else os.environ.get("REGISTER_CAPTCHA")

    print(f"=== REGISTER STANDALONE (auto captcha) ===")
    print(f"User: {user}  Pass: {pwd}")

    # Nếu đã có captcha + clientId từ trước, thử luôn
    if captcha_arg and os.path.exists("/tmp/captcha_clientId2.txt"):
        try:
            clientId=int(open("/tmp/captcha_clientId2.txt").read().strip())
            print(f"Dùng clientId cũ {clientId} + captcha {captcha_arg}")
            ws=await websockets.connect(WS_URL, additional_headers={"Origin":"https://gamevh.net","User-Agent":"Mozilla/5.0"}, max_size=2**20, ping_interval=None)
            ok,msg=await register_once(ws, user, pwd, captcha_arg, clientId)
            await ws.close()
            print(f"Kết quả: {ok} {msg}")
            if ok:
                open("/tmp/new_account.txt","w").write(f"{user}\n{pwd}\n")
                print(f"Đã ghi /tmp/new_account.txt")
                return
        except Exception as e:
            print(f"Thử captcha cũ fail: {e}")

    # Vòng thử đăng ký với captcha tự động (tối đa 5 lần)
    for attempt in range(1,6):
        print(f"\n--- Lần thử {attempt}/5 ---")
        ws=await websockets.connect(WS_URL, additional_headers={"Origin":"https://gamevh.net","User-Agent":"Mozilla/5.0"}, max_size=2**20, ping_interval=None)
        try:
            img, clientId = await get_captcha(ws)
            await ws.close()
        except Exception as e:
            print(f"Lấy captcha fail: {e}")
            await ws.close()
            continue

        # Lưu ảnh
        cap_path="/home/user/captcha_register.jpg" if os.path.exists("/home/user") else "/tmp/captcha_register.jpg"
        for p in [cap_path, "/tmp/captcha_register.jpg"]:
            try: open(p,"wb").write(img)
            except: pass
        print(f"Đã lưu captcha {cap_path} (160x50, {len(img)} bytes, clientId={clientId})")
        open("/tmp/captcha_clientId2.txt","w").write(str(clientId))

        # Giải captcha
        captcha=captcha_arg
        if not captcha:
            captcha=solve_captcha_auto(cap_path)
            if captcha:
                print(f"[AUTO] Giải captcha tự động: {captcha!r}")
            else:
                print(f"[AUTO] Không giải được, đợi nhập tay 60s...")
                # Đợi file answer hoặc env
                for _ in range(60):
                    if os.path.exists("/tmp/captcha_answer.txt"):
                        captcha=open("/tmp/captcha_answer.txt").read().strip()
                        if captcha: break
                    captcha=os.environ.get("REGISTER_CAPTCHA")
                    if captcha: break
                    await asyncio.sleep(1)
                if not captcha:
                    # Hỏi trực tiếp nếu chạy local
                    if not os.environ.get("GITHUB_ACTIONS"):
                        try:
                            captcha=input(f"Nhập captcha bạn thấy trong {cap_path}: ").strip()
                        except: pass
                if not captcha:
                    print("Chưa có captcha, thử OCR lại hoặc lấy captcha mới")
                    continue

        print(f"Dùng captcha={captcha!r} clientId={clientId}")
        # Gửi REGISTER trên kết nối mới (theo APK, mỗi REGISTER là kết nối mới sau khi đóng captcha)
        ws2=await websockets.connect(WS_URL, additional_headers={"Origin":"https://gamevh.net","User-Agent":"Mozilla/5.0"}, max_size=2**20, ping_interval=None)
        ok,msg=await register_once(ws2, user, pwd, captcha, clientId)
        await ws2.close()
        if ok:
            print(f"\n=== THÀNH CÔNG ===")
            print(f"User: {user}")
            print(f"Pass: {pwd}")
            print(f"Đã lưu /tmp/new_account.txt")
            open("/tmp/new_account.txt","w").write(f"{user}\n{pwd}\n")
            # Verify login
            try:
                import requests, re
                s=requests.Session()
                s.get('https://gamevh.net/login.jsp', timeout=10)
                r=s.post('https://gamevh.net/login.jsp', timeout=10, data={'redirect':'/','USER_NAME':user,'PASSWORD':pwd,'AUTO_LOGIN':'true','LOGIN':'Đăng nhập'}, allow_redirects=True)
                print(f"Verify login: {r.url} {'OK' if 'login.jsp' not in r.url else 'FAIL'}")
            except Exception as e:
                print(f"Verify fail {e}")
            return
        else:
            print(f"Thất bại: {msg}, thử lại với captcha mới")
            # Xóa file answer để lần sau lấy mới
            try: os.remove("/tmp/captcha_answer.txt")
            except: pass
            # Nếu captcha do người nhập sai, cho phép nhập lại
            captcha_arg=None

    print(f"\n=== THẤT BẠI SAU 5 LẦN ===")

if __name__=="__main__":
    import asyncio
    asyncio.run(main())
