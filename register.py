#!/usr/bin/env python3
"""
REGISTER STANDALONE - Đăng ký tài khoản GameVH qua WebSocket (như APK)
- Không liên quan bot, chỉ đăng ký
- Lấy captcha GET_CAPTCHA_IMAGE (160x50) -> lưu captcha.jpg -> nhập tay hoặc OCR
- Gửi REGISTER provider=PS_VH + captcha + clientId
Đã test: tạo thành công test91874 / Pass661343! (clientId 18490563)
"""
import asyncio, websockets, struct, os, random, sys, pathlib

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

async def get_captcha(ws):
    w=Writer(); w.write_command("GET_CAPTCHA_IMAGE"); w.i32(160); w.i32(50)
    await ws.send(w.build())
    raw=await asyncio.wait_for(ws.recv(), timeout=8)
    # raw = command(1+17) + status(1) + len(2) + img + clientId(8)
    cmd_len=1+len("GET_CAPTCHA_IMAGE")
    status=raw[cmd_len]
    if status != 0:
        print(f"[CAPTCHA] status={status} lạ")
    length=struct.unpack_from('>H', raw, cmd_len+1)[0]
    img=raw[cmd_len+1+2:cmd_len+1+2+length]
    clientId=struct.unpack_from('>q', raw, cmd_len+1+2+length)[0]
    return img, clientId

async def register_once(user, pwd, captcha, clientId, provider="PS_VH", imei=None):
    if imei is None:
        imei="".join(random.choice("0123456789") for _ in range(15))
    ws=await websockets.connect(WS_URL, additional_headers={"Origin":"https://gamevh.net","User-Agent":"Mozilla/5.0"}, max_size=2**20, ping_interval=None)
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
            await ws.close()
            return True, ""
        else:
            # đọc error string
            try:
                n=struct.unpack_from('>h', raw, 3)[0]
                msg=raw[5:5+n*2].decode('utf-16-be', errors='replace') if n>0 else f"status={status}"
            except:
                msg=raw[3:].hex()[:200]
            print(f"[REGISTER] FAIL {msg}")
            await ws.close()
            return False, msg
    print(f"[REGISTER] lạ {raw.hex()[:500]}")
    await ws.close()
    return False, "unknown"

async def main():
    # Args: python3 register.py [user] [pass] [captcha]  hoặc env
    user = sys.argv[1] if len(sys.argv)>1 else os.environ.get("REGISTER_USER") or gen_user()
    pwd = sys.argv[2] if len(sys.argv)>2 else os.environ.get("REGISTER_PASS") or gen_pass()
    captcha_arg = sys.argv[3] if len(sys.argv)>3 else os.environ.get("REGISTER_CAPTCHA")

    print(f"=== REGISTER STANDALONE ===")
    print(f"User: {user}  Pass: {pwd}")

    # Nếu đã có captcha + clientId từ trước (dùng lại), thử luôn
    if captcha_arg and os.path.exists("/tmp/captcha_clientId2.txt"):
        try:
            clientId=int(open("/tmp/captcha_clientId2.txt").read().strip())
            print(f"Dùng clientId cũ {clientId} + captcha {captcha_arg}")
            ok,msg=await register_once(user,pwd,captcha_arg,clientId)
            print(f"Kết quả: {ok} {msg}")
            if ok:
                open("/tmp/new_account.txt","w").write(f"{user}\n{pwd}\n")
                print(f"Đã ghi /tmp/new_account.txt")
                return
        except Exception as e:
            print(f"Thử captcha cũ fail: {e}")

    # Lấy captcha mới
    ws=await websockets.connect(WS_URL, additional_headers={"Origin":"https://gamevh.net","User-Agent":"Mozilla/5.0"}, max_size=2**20, ping_interval=None)
    print("Đang lấy captcha...")
    w=Writer(); w.write_command("GET_CAPTCHA_IMAGE"); w.i32(160); w.i32(50)
    await ws.send(w.build())
    raw=await asyncio.wait_for(ws.recv(), timeout=8)
    cmd_len=1+len("GET_CAPTCHA_IMAGE")
    status=raw[cmd_len]
    length=struct.unpack_from('>H', raw, cmd_len+1)[0]
    img=raw[cmd_len+1+2:cmd_len+1+2+length]
    clientId=struct.unpack_from('>q', raw, cmd_len+1+2+length)[0]
    await ws.close()
    cap_path="/home/user/captcha_register.jpg" if os.path.exists("/home/user") else "/tmp/captcha_register.jpg"
    # thử lưu cả 2 chỗ
    for p in [cap_path, "/tmp/captcha_register.jpg"]:
        try: open(p,"wb").write(img)
        except: pass
    print(f"Đã lưu captcha {cap_path} (160x50, {len(img)} bytes, clientId={clientId})")
    open("/tmp/captcha_clientId2.txt","w").write(str(clientId))
    # Thử OCR
    captcha=captcha_arg
    if not captcha:
        try:
            from PIL import Image
            import pytesseract
            # upscale 2x cho dễ OCR
            im=Image.open(cap_path)
            im2=im.resize((im.width*2, im.height*2), Image.NEAREST)
            captcha=pytesseract.image_to_string(im2, config='--psm 8 -c tessedit_char_whitelist=ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789').strip().replace(" ","")[:6]
            print(f"OCR thử: {captcha!r}")
        except Exception as e:
            print(f"OCR chưa có: {e}")

    if not captcha or len(captcha)<3:
        # Đợi input tay
        if os.environ.get("GITHUB_ACTIONS")=="true":
            print("::warning:: Đang chạy trên GitHub Actions - cần nhập captcha qua workflow_dispatch input REGISTER_CAPTCHA hoặc đợi file /tmp/captcha_answer.txt")
            # Đợi file 90s
            for _ in range(90):
                if os.path.exists("/tmp/captcha_answer.txt"):
                    captcha=open("/tmp/captcha_answer.txt").read().strip()
                    if captcha: break
                # cũng check env
                captcha=os.environ.get("REGISTER_CAPTCHA")
                if captcha: break
                await asyncio.sleep(1)
        else:
            # Local workspace: hỏi trực tiếp
            try:
                captcha=input(f"Nhập captcha bạn thấy trong {cap_path} (hoặc để trống để thử OCR lại): ").strip()
            except: pass

    if not captcha:
        print("Chưa có captcha, thoát. Hãy chạy: python3 register.py <user> <pass> <captcha>")
        print(f"Ảnh ở {cap_path}, clientId={clientId}")
        return

    print(f"Dùng captcha={captcha!r} clientId={clientId}")
    ok,msg=await register_once(user,pwd,captcha,clientId)
    if ok:
        print(f"\n=== THÀNH CÔNG ===")
        print(f"User: {user}")
        print(f"Pass: {pwd}")
        print(f"Đã lưu /tmp/new_account.txt")
        open("/tmp/new_account.txt","w").write(f"{user}\n{pwd}\n")
        # Thử login HTTP để verify
        try:
            import requests, re
            s=requests.Session()
            s.get('https://gamevh.net/login.jsp', timeout=10)
            r=s.post('https://gamevh.net/login.jsp', timeout=10, data={'redirect':'/','USER_NAME':user,'PASSWORD':pwd,'AUTO_LOGIN':'true','LOGIN':'Đăng nhập'}, allow_redirects=True)
            print(f"Verify login: {r.url} {'OK' if 'login.jsp' not in r.url else 'FAIL'}")
        except Exception as e:
            print(f"Verify fail {e}")
    else:
        print(f"\n=== THẤT BẠI: {msg} ===")
        print("Hãy lấy captcha mới và thử lại")

if __name__=="__main__":
    import asyncio
    asyncio.run(main())
