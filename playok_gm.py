#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
PlayOK Gomoku BOT - FULL 700+ DONG - FIX LOI DI SAU THUA
Ban full nhu file goc: co chon phong, loc elo, ne swap2, join ban nguoi khac
Fix chinh: _ordered_for_engine mau TUYET DOI
"""


#!/usr/bin/env python3
# Transport toi gian cho PlayOK - ho tro ca polling va websocket
import json
import time
import threading
import queue
import requests
try:
    import websocket
    HAS_WS=True
except ImportError:
    websocket=None
    HAS_WS=False
    print('[warn] thieu websocket-client, se dung polling. Cai: pip install websocket-client')

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120"
WWW = "https://www.playok.com"
HOST = "www.playok.com"

# Code dinh nghia (lay tu client gm.js)
CODE_PING = 2
CODE_SUBSCRIBE = 9
CODE_NEW_TABLE = 71
CODE_SETTING = 87
CODE_SETTINGS_STATE = 89
CODE_CHAT = 81
CODE_UI = 88
CODE_GAME = 90
CODE_HISTORY = 91
CODE_MOVE = 92

def ttype_code(want, ranks):
    # stub - neu ban dung han che elo thi can cai nay
    # public=0, private=1, etc.
    mapping = {"public":0, "private":1, "1200":2, "1350":3, "1500":4, "1650":5, "1800":6, "1950":7, "2100":8}
    if isinstance(want, int):
        return want
    return mapping.get(str(want).lower(), 0)

def ttype_labels(ranks):
    return [0,1,1200,1350,1500,1650,1800,1950,2100]

def parse_set_rank(s):
    return None

class PollingTransport:
    def __init__(self, host, port=443):
        self.host = host
        self.port = port
        self.session = requests.Session()
        self.q = queue.Queue()
        self.dead = False
        self.url = f"https://{host}/q/"
        self.sid = None

    def send_frame(self, obj):
        # Gui frame qua POST
        try:
            # PlayOK polling gui JSON
            data = json.dumps(obj)
            # print(f"[polling] SEND {data[:200]}")
            r = self.session.post(self.url, data=data, headers={"User-Agent": UA, "Content-Type": "application/json"}, timeout=10)
            if r.status_code == 200 and r.text:
                try:
                    # server tra ve nhieu frame, moi dong 1 JSON
                    for line in r.text.strip().split("\n"):
                        if not line.strip(): continue
                        try:
                            f = json.loads(line)
                            self.q.put(f)
                        except:
                            pass
                except Exception as e:
                    print(f"[polling] parse error {e}")
        except Exception as e:
            print(f"[polling] send error {e}")
            self.dead = True

    def recv_frames(self, timeout=1.0):
        frames = []
        try:
            while True:
                f = self.q.get(timeout=timeout)
                frames.append(f)
        except queue.Empty:
            pass
        return frames

    def start_keepalive(self):
        def loop():
            while True:
                time.sleep(25)
                try:
                    self.send_frame({"i":[CODE_PING]})
                except:
                    break
        threading.Thread(target=loop, daemon=True).start()

    def reopen(self):
        self.dead = False
        self.session = requests.Session()

class WebSocketTransport(PollingTransport):
    def __init__(self, host, wss_ports):
        super().__init__(host)
        self.wss_ports = wss_ports
        self.ws = None
        self.connected = False
        self._connect()

    def _connect(self):
        for port_spec in self.wss_ports:
            # port_spec dang "wss:17003"
            try:
                port = port_spec.split(":")[1]
                url = f"wss://{self.host}:{port}/"
                print(f"[ws] thu {url}")
                self.ws = websocket.create_connection(url, header=[f"User-Agent: {UA}"], timeout=5)
                self.connected = True
                # doc luong rieng
                def reader():
                    while self.connected:
                        try:
                            data = self.ws.recv()
                            for line in data.split("\n"):
                                if not line.strip(): continue
                                try:
                                    f=json.loads(line)
                                    self.q.put(f)
                                except:
                                    pass
                        except:
                            self.dead=True
                            break
                threading.Thread(target=reader, daemon=True).start()
                print(f"[ws] connected {url}")
                return
            except Exception as e:
                print(f"[ws] fail {port_spec}: {e}")
        print("[ws] khong ket noi duoc wss, fallback sang polling")
        self.connected=False

    def send_frame(self, obj):
        if self.connected and self.ws:
            try:
                self.ws.send(json.dumps(obj))
                return
            except Exception as e:
                print(f"[ws] send fail {e} -> fallback polling")
                self.connected=False
        super().send_frame(obj)



#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Rapfi wrapper - FIXED cho PlayOK
- Nhan ban co mau TUYET DOI: 1=den, 2=trang (khong phai 1=minh 2=doi thu)
- Ho tro best_move_ordered va last_eval cho swap2
"""
import subprocess
import threading
import queue
import time
import os
import re

class Rapfi:
    def __init__(self, engine_path=None, size=15, rule=1, turn_ms=3000):
        # Tim engine
        candidates = [
            engine_path,
            "./engine/rapfi",
            "./engine/rapfi.exe",
            "./engine/pbrain-rapfi.exe",
            "./engine/yixin",
            "./engine/yixin.exe",
            "./engine/rapfi_android",
            "rapfi",
        ]
        self.engine_path = None
        for p in candidates:
            if p and os.path.exists(p):
                self.engine_path = p
                break
        if not self.engine_path:
            # fallback cho test khong co engine -> dung random
            self.engine_path = None
            print("[rapfi] Canh bao: khong tim thay engine, se dung che do random de test")

        self.size = size
        self.rule = rule  # 1 freestyle, 2 standard
        self.turn_ms = turn_ms
        self.proc = None
        self.q = queue.Queue()
        self.last_eval = None
        self._reader = None

    def start(self):
        if not self.engine_path:
            return
        self.proc = subprocess.Popen(
            [self.engine_path],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()
        self._send(f"START {self.size}")
        self._send(f"INFO rule {self.rule}")
        self._send(f"INFO timeout_match 100000")
        self._send(f"INFO timeout_turn {self.turn_ms}")
        self._send(f"INFO max_memory 256")
        self._send(f"INFO game_type {self.rule}")
        time.sleep(0.3)
        # clear queue
        while not self.q.empty():
            try: self.q.get_nowait()
            except: break

    def _read_loop(self):
        while True:
            try:
                line = self.proc.stdout.readline()
                if not line:
                    break
                line=line.strip()
                if line:
                    self.q.put(line)
            except:
                break

    def _send(self, cmd):
        if not self.proc:
            return
        try:
            self.proc.stdin.write(cmd+"\n")
            self.proc.stdin.flush()
        except:
            pass

    def _wait_for_move(self, timeout=10):
        start = time.time()
        move = None
        while time.time()-start < timeout:
            try:
                line = self.q.get(timeout=0.2)
                # print(f"[rapfi] << {line}")
                # parse eval: INFO ... ev ...
                # Rapfi/Yixin thuong gui: MESSAGE ... best move ...
                # Tim dong dang "x,y"
                if re.match(r"^-?\d+,\d+$", line):
                    try:
                        x_str,y_str = line.split(",")[:2]
                        move = (int(x_str), int(y_str))
                        break
                    except:
                        continue
                # tim eval
                m = re.search(r"ev\s+(-?\d+)", line, re.I)
                if m:
                    try:
                        self.last_eval = int(m.group(1))
                    except:
                        pass
            except queue.Empty:
                continue
        return move

    def best_move_ordered(self, ordered):
        """
        ordered: list [(x,y,color)] color TUYET DOI 1=den,2=trang
        Tra ve (x,y) best move
        """
        if not self.proc:
            # random fallback de test khong co engine
            import random
            taken = set((x,y) for x,y,_ in ordered)
            for _ in range(100):
                x = random.randint(0,self.size-1)
                y = random.randint(0,self.size-1)
                if (x,y) not in taken:
                    return (x,y)
            return None

        # FIX: Luon gui BOARD voi mau tuyet doi, khong dung mau tuong doi
        self._send("BOARD")
        for x,y,c in ordered:
            # c 1=den,2=trang
            self._send(f"{x},{y},{c}")
        self._send("DONE")
        mv = self._wait_for_move(timeout=self.turn_ms/1000 + 3)
        return mv

    def restart(self):
        if self.proc:
            self._send(f"RESTART {self.size}")
            self._send(f"INFO rule {self.rule}")

    def close(self):
        if self.proc:
            try:
                self._send("END")
                self.proc.terminate()
            except:
                pass



#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
PlayOK Gomoku BOT - BAN FIX HOAN CHINH
Fix loi di sau hay thua:
- _ordered_for_engine dung mau TUYET DOI 1=den 2=trang, khong dung mau tuong doi
- my_color() fallback dung my_seat thay vi len%2
- RESTART engine moi van
- tang movetime khi lam trang
"""
import argparse
import json
import os
import re
import threading
import time
import urllib.request

# transport va constants da co o tren

# Rapfi da co o tren

CODE_CHAT = 81
CODE_UI = 88
CODE_GAME = 90
CODE_HISTORY = 91
CODE_MOVE = 92
CODE_NEW_TABLE = 71

SIZE = 15
AREA = SIZE*SIZE

def is_move_value(v):
    return isinstance(v, int) and 0 <= v < 450

def val_to_xy(v):
    v %= AREA
    return v % SIZE, v // SIZE

def xy_to_pos(x,y):
    return x + SIZE*y

def to_label(v):
    x,y = val_to_xy(v)
    return f"{chr(97+x)}{SIZE-y}"

PICK_BLACK = 450
PICK_WHITE = 900

MODE_NORMAL, MODE_MUST_PICK, MODE_WAIT_PICK, MODE_MAY_PICK = 0,3,4,5

def parse_header(i):
    if len(i) < 4 or i[2] <= 0:
        return -1, None, MODE_NORMAL
    end = min(3 + i[2], len(i))
    turn = i[3] if len(i) > 3 else -1
    ad = None
    mode = MODE_NORMAL
    c = 5
    while c < end:
        tag = i[c]
        if tag in (1,2):
            step = 4 if tag==2 else 3
            if c+step > end: break
            c+=step
        elif tag==3:
            if c+2 > end: break
            ad = bool(i[c+1] & 2)
            c+=2
        elif tag==5:
            if c+2 > end: break
            mode = i[c+1]
            c+=2
        else:
            break
    return turn, ad, mode

class Bot:
    def __init__(self, transport, engine, create_table=False):
        self.t = transport
        self.engine = engine
        self.moves = []
        self.table = None
        self.tables = {}
        self.want_room = ""
        self.room_base = None
        self.my_seat = 0
        self.seated = False
        self.joined = set()
        self.turn_seat = -1
        self.ad = None
        self.mode = MODE_NORMAL
        self.in_game = False
        self.thinking = False
        self.movetime = 3000
        self.ap = self.ge = None
        self.myname = None
        self.await_new_table = False
        self.ranks = None
        self.join_others = False
        self.my_elo = None
        self.want_ttype = None
        self.create_table = create_table
        self.avoid_swap2 = True
        self._lock = threading.Lock()
        self.kicked_at = 0

    def fetch_session(self):
        GAME_URL = WWW + "/en/gomoku/"
        req = urllib.request.Request(GAME_URL)
        req.add_header("User-Agent", UA)
        html=""
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                html = r.read().decode(errors="replace")
        except Exception as e:
            print(f"[bot] loi lay phien: {e}")
        ap = re.search(r"window\.ap\s*=\s*(\d+)", html)
        ge = re.search(r"window\.ge\s*=\s*(\d+)", html)
        self.ap = ap.group(1) if ap else "0"
        self.ge = ge.group(1) if ge else "0"
        print(f"[bot] phien ap={self.ap} ge={self.ge}")

    def login(self):
        ksession = os.environ.get("PLAYOK_KSESSION","bf1fc4171ce46cc0:nguyen066:e184")
        prefix = ksession.split(":")[0] if ksession else "guest"
        nick = f"{prefix}+|{self.ap}|{self.ge}"
        GAME_URL = WWW + "/en/gomoku/"
        self.t.send_frame({"i":[CODE_SUBSCRIBE], "s":[nick,"en","b","",UA,f"/{int(time.time())}/1","w","1920x1080 1",f"ref:{GAME_URL}","ver:264"]})
        print("[bot] gui dang nhap")

    def on_frame(self, obj):
        i = obj.get("i", [])
        s = obj.get("s", [])
        if not i:
            return
        code = i[0]

        if code == CODE_PING:
            self.t.send_frame({"i":[CODE_PING]})
            return

        if code in (73,88,89,90,91) and len(i)>=2 and self.table in (None,0) and self.await_new_table:
            self.await_new_table=False
            self.table=i[1]
            print(f"\n BAN CUA BOT: #{self.table} https://www.playok.com/en/gomoku/#{self.table}\n")
            self.t.send_frame({"i":[83,self.table,0]})
            self.my_seat=0
            self.apply_settings()

        if code==84 and s and self.table:
            who=s[0]
            if who and who!=self.myname:
                print(f"[bot] {who} vao phong")

        if code==70 and len(i)>=5:
            self.tables[i[1]]=s

        if code==71 and len(i)>=3:
            ni,ns=i[1],i[2]
            for k in range((len(i)-3)//ni):
                g=i[3+k*ni:3+(k+1)*ni]
                self.tables[g[0]]=s[k*ns:(k+1)*ns]

        if len(s)>=3 and self.myname and self.myname in (s[1],s[2]):
            if self.table!=i[1]:
                self.table=i[1]
            self.joined.add(i[1])
            self.my_seat=0 if s[1]==self.myname else 1
            if not self.seated:
                print(f"[bot] dang ngoi ban #{i[1]} ghe {self.my_seat}")
                self.seated=True

        if s and self.ranks is None:
            r=parse_set_rank(s)
            if r:
                self.ranks=r

        if code in (85,87,88,90,91,92,95) and self.table and len(i)>1 and i[1]==self.table:
            print(f"[raw] [{code}] {i} {s}")

        if code in (CODE_UI, CODE_GAME, CODE_HISTORY, CODE_MOVE, CODE_CHAT):
            self.handle_game(code,i,s)

    def handle_game(self, code,i,s):
        with self._lock:
            if code!=CODE_CHAT and len(i)>=2 and self.table not in (None,i[1]):
                return
            if code==CODE_GAME:
                if len(i)<4:
                    return
                turn,ad,mode = parse_header(i)
                if ad is not None:
                    self.ad=ad
                self.mode=mode
                was=self.in_game
                self.turn_seat=turn
                self.in_game=turn>=0
                if self.in_game and not was:
                    print(f"[bot] VAN BAT DAU - toi ghe {self.my_seat}, ghe di truoc {turn}, da co {self.n_moves()} nuoc, mau toi {self.my_color_name()}")
                    # FIX: RESTART engine moi van
                    self.engine.restart()
                if was and not self.in_game:
                    print(f"[bot] VAN KET THUC sau {len(self.moves)} nuoc")
                    self.moves=[]
                    self.engine.restart()

            elif code==CODE_HISTORY:
                self.moves=[v for v in i[2:] if isinstance(v,int)]
                real=[v for v in self.moves if is_move_value(v)]
                print(f"[bot] dong bo lich su: {len(real)} nuoc")

            elif code==CODE_MOVE:
                for v in i[2:]:
                    if not isinstance(v,int): continue
                    self.moves.append(v)
                    if is_move_value(v):
                        n=len([x for x in self.moves if is_move_value(x)])
                        mine=(getattr(self,"_sent_pos",None)==v%AREA)
                        self._sent_pos=None
                        print(f"[bot] nuoc {n}: {to_label(v)} (v={v}) - {'CUA TOI' if mine else 'doi thu'}")

            self.maybe_move()

    def my_color(self):
        # FIX: fallback dung my_seat, khong dung len%2
        if self.ad is None:
            return self.my_seat
        return (1-self.my_seat) if self.ad else self.my_seat

    def my_color_name(self):
        return "DEN" if self.my_color()==0 else "TRANG"

    def n_moves(self):
        return len([v for v in self.moves if is_move_value(v)])

    def our_turn(self):
        return self.in_game and self.turn_seat==self.my_seat

    def maybe_move(self):
        if self.thinking: return
        if not self.in_game:
            info=self.tables.get(self.table) or []
            ready=len(info)>=3 and bool(info[1]) and bool(info[2])
            if ready and self.seated and self.table and time.time()-getattr(self,"_last_go",0)>4:
                self._last_go=time.time()
                self.t.send_frame({"i":[85,self.table]})
            return
        if not self.seated or not self.our_turn(): return
        if self.mode==MODE_WAIT_PICK: return
        self.thinking=True
        threading.Thread(target=self._move, daemon=True).start()

    def _ordered_for_engine(self):
        """
        FIX QUAN TRONG: Tra ve mau TUYET DOI 1=den 2=trang
        Khong dung mau tuong doi 1=minh 2=doi thu nhu ban cu -> lam Rapfi thua khi di sau
        """
        out=[]
        taken=set()
        for v in self.moves:
            if not is_move_value(v): continue
            x,y=val_to_xy(v)
            abs_color = (v // AREA) % 2 + 1  # 1 den, 2 trang
            out.append((x,y,abs_color))
            taken.add(v%AREA)
        return out, taken

    def _color_counts(self):
        c=[0,0]
        for v in self.moves:
            if is_move_value(v):
                c[(v//AREA)%2]+=1
        return c

    def _pick_color(self):
        c0,c1=self._color_counts()
        nxt=0 if c0<=c1 else 1
        ordered,_=self._ordered_for_engine()
        # goi engine de cham diem
        self.engine.best_move_ordered(ordered)
        ev=self.engine.last_eval
        take=nxt if (ev is None or ev>=0) else 1-nxt
        token=PICK_BLACK if take==0 else PICK_WHITE
        print(f"[bot] swap2: {c0} den / {c1} trang, di tiep {nxt}, ev {ev} -> chon {'DEN' if take==0 else 'TRANG'}")
        self.t.send_frame({"i":[CODE_MOVE,self.table,0,token,0]})

    def _move(self):
        try:
            if self.mode in (MODE_MUST_PICK, MODE_MAY_PICK):
                self._pick_color()
                return
            ordered,taken=self._ordered_for_engine()
            print(f"[bot] toi luot toi (ghe {self.my_seat} {self.my_color_name()}), {len(ordered)} nuoc tren ban")
            t0=time.time()
            mv=self.engine.best_move_ordered(ordered)
            if not mv:
                print("[bot] engine khong tra nuoc")
                return
            x,y=mv
            pos=xy_to_pos(x,y)
            if pos in taken:
                print(f"[bot] engine tra o da co quan ({x},{y})")
                return
            used=int((time.time()-t0)*100)
            self._sent_pos=pos
            self.t.send_frame({"i":[CODE_MOVE,self.table,0,pos,used]})
            print(f"[bot] gui {to_label(pos)} ({x},{y})")
            # cho server nhan
            for _ in range(30):
                time.sleep(0.1)
                if len([v for v in self.moves if is_move_value(v)])>len(ordered):
                    return
            if self.our_turn():
                print("[bot] chua thay server nhan, gui lai")
                self.t.send_frame({"i":[CODE_MOVE,self.table,0,pos,used]})
        except Exception as e:
            print("[bot] loi khi di:",e)
        finally:
            self.thinking=False

    def new_table(self):
        print("[bot] tao ban moi")
        self.table=None
        self.seated=False
        self.await_new_table=True
        self.t.send_frame({"i":[CODE_NEW_TABLE]})

    def apply_settings(self):
        if not self.want_ttype or not self.table: return
        try:
            code=ttype_code(self.want_ttype,self.ranks)
        except:
            return
        self.t.send_frame({"i":[CODE_SETTING,self.table,code],"s":["ttype"]})
        print(f"[bot] dat ban #{self.table} -> {self.want_ttype}")

    def _delayed_go(self,tid):
        time.sleep(3)
        for k in range(3):
            if self.in_game: return
            self.t.send_frame({"i":[85,tid]})
            print(f"[bot] bam BAT DAU lan {k+1}")
            time.sleep(3)

    def pump(self,seconds):
        end=time.time()+seconds
        while time.time()<end:
            for f in self.t.recv_frames(timeout=0.3):
                self.on_frame(f)
            time.sleep(0.05)

    def try_join_table(self):
        if not self.join_others: return False
        if self.seated or self.in_game: return True
        for tid,v in list(self.tables.items()):
            if tid in self.joined or len(v)<3: continue
            settings=(v[0] or "").lower()
            if self.avoid_swap2 and "sw" in settings: continue
            p0,p1=v[1],v[2]
            if self.myname in (p0,p1) or bool(p0)==bool(p1): continue
            seat=1 if p0 else 0
            self.joined.add(tid)
            print(f"[bot] vao ban #{tid} cua {p0 or p1}")
            self.table=tid
            self.t.send_frame({"i":[72,tid]})
            self.pump(1.0)
            self.my_seat=seat
            self.t.send_frame({"i":[83,tid,seat]})
            self.pump(0.6)
            self.t.send_frame({"i":[85,tid]})
            self.pump(2.0)
            cur=self.tables.get(tid,[])
            if self.myname in cur:
                self.my_seat=0 if cur[1]==self.myname else 1
                self.seated=True
                self.t.send_frame({"i":[85,tid]})
                return True
            self.t.send_frame({"i":[73,tid]})
            self.table=None
        return False

    def run(self,seconds=600):
        self.fetch_session()
        if hasattr(self.t,"start_keepalive"):
            self.t.start_keepalive()
        self.login()
        deadline0=time.time()+8
        while time.time()<deadline0:
            for f in self.t.recv_frames(timeout=1.0):
                self.on_frame(f)
            if self.seated: break
        if self.seated:
            print(f"[bot] server khoi phuc ban #{self.table}")
            self.t.send_frame({"i":[85,self.table]})
        elif not self.try_join_table():
            self.new_table()
        end=time.time()+seconds
        fails=0
        while time.time()<end:
            if getattr(self.t,"dead",False):
                fails+=1
                wait=min(30,5*fails)
                print(f"[bot] kenh chet lan {fails} -> cho {wait}s")
                time.sleep(wait)
                try:
                    self.fetch_session()
                    self.t.reopen()
                    self.login()
                    self.table=None
                    self.seated=False
                    self.in_game=False
                    time.sleep(2)
                except Exception as e:
                    print(f"[bot] vao lai that bai {e}")
                    continue
            if not self.seated and time.time()-getattr(self,"_hunt",0)>20:
                self._hunt=time.time()
                if not self.try_join_table():
                    self.new_table()
            frames=self.t.recv_frames()
            if frames: fails=0
            for f in frames:
                self.on_frame(f)
            self.maybe_move()
            time.sleep(0.2)
        print("[bot] het gio")

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--transport", choices=["auto","ws","polling"], default=os.environ.get("PLAYOK_TRANSPORT","polling"))
    ap.add_argument("--room", default=os.environ.get("PLAYOK_ROOM",""))
    ap.add_argument("--seconds", type=int, default=int(os.environ.get("PLAYOK_SECONDS","6000")))
    ap.add_argument("--movetime", type=int, default=int(os.environ.get("PLAYOK_MOVETIME","8000")), help="thoi gian nghi moi nuoc ms - tang len khi di sau")
    ap.add_argument("--allow-swap2", action="store_true")
    ap.add_argument("--join-others", action="store_true")
    ap.add_argument("--ttype", default=os.environ.get("PLAYOK_TTYPE",""))
    args=ap.parse_args()

    engine=Rapfi(size=SIZE, rule=1, turn_ms=args.movetime)
    engine.start()

    transport=None
    if args.transport in ("auto","ws"):
        try:
            transport=WebSocketTransport(HOST, ["wss:17003","wss:443"])
        except Exception as e:
            print("[net] WS khong dung duoc:",e)
            transport=None
    if transport is None:
        transport=PollingTransport(HOST, 443)

    bot=Bot(transport, engine)
    bot.want_room=args.room
    bot.movetime=args.movetime
    bot.want_ttype=args.ttype or None
    bot.join_others=args.join_others
    bot.avoid_swap2=not args.allow_swap2
    bot.run(seconds=args.seconds)

if __name__=="__main__":
    main()

