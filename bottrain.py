#!/usr/bin/env python3
"""BOTTRAIN - AlphaZero Self-Learning Caro Bot (15x19, Rule 8)"""
import subprocess,sys,os,importlib,json,time,struct,logging,asyncio,random,traceback,re
from pathlib import Path
from typing import Optional,Tuple,List
logging.basicConfig(level=logging.INFO,format="%(asctime)s %(message)s",datefmt="%H:%M:%S")
log=logging.getLogger("bottrain")
for pkg in["websockets","requests","numpy"]:
 try:importlib.import_module(pkg)
 except ImportError:
  log.info(f"[SETUP] Installing {pkg}...")
  subprocess.run([sys.executable,"-m","pip","install",pkg,"-q","--break-system-packages"],stderr=subprocess.DEVNULL)
  importlib.import_module(pkg)
import websockets,requests
import numpy as np
try:
 from game_15x19 import Board,Game
 from policy_value_net import PolicyValueNetNumpy,init_net_params,softmax
 from mcts import MCTSPlayer
except ImportError:
 sys.path.insert(0,str(Path(__file__).parent))
 from game_15x19 import Board,Game
 from policy_value_net import PolicyValueNetNumpy,init_net_params,softmax
 from mcts import MCTSPlayer
BASE_DIR=Path(__file__).parent
MODEL_PATH=BASE_DIR/"bottrain_model.npy"
WS_URL="wss://gamevh.net/ws/gameServer"
USER=os.environ.get("CARO_USER","")
PASSWD=os.environ.get("CARO_PASSWD","")
if not USER or not PASSWD:log.error("Missing CARO_USER/CARO_PASSWD");sys.exit(1)
VERSION="5.0.0";GAME_ID="caro"
RUNTIME=int(os.environ.get("CARO_RUNTIME_SECONDS")or float(os.environ.get("CARO_RUNTIME_HOURS","5.9"))*3600)
EMPTY=-1;CIRCLE=0;CROSS=1
BOARD_WIDTH=15;BOARD_HEIGHT=19;N_IN_ROW=5
MCTS_PLAYOUT=int(os.environ.get("MCTS_PLAYOUT","200"));MCTS_CPUCT=5
CMD_MAP={300:"PONG",301:"PING",302:"LOGIN",303:"ALERT",401:"ENTER_PLACE",402:"ENTER_CHILD_PLACE",405:"CREATE_RULE",406:"PLAYER_ENTERED",407:"PLAYER_EXITED",410:"KICK_PLAYER",413:"LIST_BET_AMT",414:"GET_TABLE_DATA",417:"START_MATCH",418:"GAMEOVER",419:"ENTER_STATE",420:"SET_TURN",421:"SET_PLAYER_STATUS",422:"SET_PLAYER_POINT",432:"OWNER_CHANGED",433:"GET_TABLE_DATA_EX",434:"SET_READY",501:"BET",502:"PLAY",505:"CHAT",518:"HIGHLIGHT",529:"MOVE",533:"ASK_DRAW",534:"SURRENDER",535:"RETREAT"}
class BinaryReader:
 def __init__(self,data:bytes):self.data=data;self.pos=0
 def u8(self)->int:
  if self.pos>=len(self.data):return 0
  v=self.data[self.pos];self.pos+=1;return v
 def i8(self)->int:
  if self.pos>=len(self.data):return 0
  v=struct.unpack_from('>b',self.data,self.pos)[0];self.pos+=1;return v
 def i16(self)->int:
  if self.pos+2>len(self.data):return 0
  v=struct.unpack_from('>h',self.data,self.pos)[0];self.pos+=2;return v
 def i32(self)->int:
  if self.pos+4>len(self.data):return 0
  v=struct.unpack_from('>i',self.data,self.pos)[0];self.pos+=4;return v
 def remaining(self)->int:return len(self.data)-self.pos
 def read_ascii(self)->str:
  if self.pos>=len(self.data):return""
  n=self.u8()
  if self.pos+n>len(self.data):n=len(self.data)-self.pos
  s=self.data[self.pos:self.pos+n].decode('ascii','replace');self.pos+=n;return s
 def read_utf(self)->str:
  if self.pos+2>len(self.data):return""
  n=self.i16()
  if n<=0:return""
  bl=n*2
  if self.pos+bl>len(self.data):bl=len(self.data)-self.pos
  s=self.data[self.pos:self.pos+bl].decode('utf-16-be','replace');self.pos+=bl;return s
 def read_command(self)->str:
  first=self.i8()
  if first<0:
   n=-first
   if self.pos+n>len(self.data):n=len(self.data)-self.pos
   s=self.data[self.pos:self.pos+n].decode('ascii','replace');self.pos+=n;return s
  second=self.u8();cmd_id=(first<<8)|second
  return CMD_MAP.get(cmd_id,f"CMD_{cmd_id}")
class BinaryWriter:
 def __init__(self):self.parts=[]
 def u8(self,v:int):self.parts.append(struct.pack('>B',v))
 def i8(self,v:int):self.parts.append(struct.pack('>b',v))
 def i16(self,v:int):self.parts.append(struct.pack('>h',v))
 def i32(self,v:int):self.parts.append(struct.pack('>i',v))
 def write_ascii(self,s:str):
  enc=s.encode('ascii','replace');self.u8(len(enc));self.parts.append(enc)
 def write_utf(self,s:str):
  enc=s.encode('utf-16-be');self.i16(len(enc)//2);self.parts.append(enc)
 def write_command(self,cmd:str):
  cid=next((k for k,v in CMD_MAP.items()if v==cmd),None)
  if cid:self.parts.append(struct.pack('>H',cid))
  else:
   b=cmd.encode('ascii');self.i8(-len(b));self.parts.append(b)
 def build(self)->bytes:return b''.join(self.parts)
class CaroBotTrain:
 def __init__(self):
  self.ws=None;self.board=Board(width=BOARD_WIDTH,height=BOARD_HEIGHT,n_in_row=N_IN_ROW)
  self.slot=-1;self.my_symbol=CROSS;self.is_playing=False;self.in_table=False
  self.players={};self.nickname="";self.token=0;self.cookie=""
  self.start_time=None;self.last_activity=time.time();self._running=True
  self.wins=0;self.losses=0;self.draws=0;self.table_id=None;self._moving=False
  self.net=None;self.mcts_player=None;self.load_or_init_model()
 def load_or_init_model(self):
  if MODEL_PATH.exists():
   try:
    net_params=np.load(MODEL_PATH,allow_pickle=True)
    log.info(f"[MODEL] Loaded from {MODEL_PATH}")
   except:log.warning("[MODEL] Load failed, new random weights");net_params=init_net_params(BOARD_WIDTH,BOARD_HEIGHT)
  else:log.info("[MODEL] No saved model, random weights");net_params=init_net_params(BOARD_WIDTH,BOARD_HEIGHT)
  self.net=PolicyValueNetNumpy(BOARD_WIDTH,BOARD_HEIGHT,net_params)
  self.mcts_player=MCTSPlayer(self.net.policy_value_fn,c_puct=MCTS_CPUCT,n_playout=MCTS_PLAYOUT,is_selfplay=0)
 def init_board_for_game(self):self.board.init_board();self.mcts_player.reset_player()
 def bot_to_board_move(self,x,y):return y*self.board.width+x
 def get_ai_move(self)->Optional[Tuple[int,int]]:
  try:
   if len(self.board.availables)<=1:return None
   move=self.mcts_player.get_action(self.board,temp=1e-3,return_prob=0)
   if move is not None:loc=self.board.move_to_location(move);return loc[0],loc[1]
   return None
  except Exception as e:log.error(f"[MCTS] {e}");return None
 def apply_opponent_move(self,x,y):
  m=self.bot_to_board_move(x,y)
  if m in self.board.availables:self.board.do_move(m)
 def apply_my_move(self,x,y):
  m=self.bot_to_board_move(x,y)
  if m in self.board.availables:self.board.do_move(m)
 async def http_login(self)->bool:
  try:
   session=requests.Session()
   ua="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/139.0 Safari/537.36"
   session.headers.update({'User-Agent':ua,'Accept-Language':'vi-VN,vi;q=0.9,en;q=0.7'})
   session.get("https://gamevh.net/login.jsp",timeout=10)
   resp=session.post("https://gamevh.net/login.jsp",timeout=10,
    data={'redirect':'/','USER_NAME':USER,'PASSWORD':PASSWD,'AUTO_LOGIN':'true','LOGIN':'Dang nhap'},
    headers={'Origin':'https://gamevh.net','Referer':'https://gamevh.net/login.jsp','Content-Type':'application/x-www-form-urlencoded'},
    allow_redirects=True)
   if'login.jsp'in resp.url:log.error("[LOGIN] Login failed");return False
   game_resp=session.get("https://gamevh.net/play/caro/0",timeout=10)
   self.cookie="; ".join(f"{k}={v}"for k,v in session.cookies.items())
   page_html=game_resp.text
   tm=re.search(r'var\s+token\s*=\s*(-?\d+)',page_html)
   if not tm:log.error("[LOGIN] Token not found");return False
   self.token=int(tm.group(1))
   nm=re.search(r"var\s+currentPlayerNickName\s*=\s*'([^']+)'",page_html)
   self.nickname=nm.group(1)if nm else"BOTTRAIN"
   log.info(f"[LOGIN] OK - {self.nickname} (token={self.token})")
   return True
  except Exception as e:log.error(f"[LOGIN] {e}");return False
 def make_login(self)->bytes:
  w=BinaryWriter();w.write_command("LOGIN");w.write_ascii(self.nickname);w.i32(self.token);w.write_ascii(VERSION);w.write_ascii("");w.write_ascii(GAME_ID);w.i8(1);return w.build()
 def make_enter(self,path:str)->bytes:
  w=BinaryWriter();w.write_command("ENTER_PLACE");w.write_ascii(path);w.write_utf("");w.i8(1);return w.build()
 def make_play(self,pos:int)->bytes:
  w=BinaryWriter();w.write_command("PLAY");w.i16(pos);return w.build()
 def make_pong(self)->bytes:
  w=BinaryWriter();w.write_command("PONG");return w.build()
 def make_create_rule(self)->bytes:
  w=BinaryWriter();w.write_command("CREATE_RULE");w.i8(0);w.i8(2);w.write_ascii("matchDuration");w.write_utf("0");w.write_ascii("turnDuration");w.write_utf("60");return w.build()
 def make_get_table(self)->bytes:
  w=BinaryWriter();w.write_command("GET_TABLE_DATA_EX");w.write_ascii("");return w.build()
 def make_ready(self)->bytes:
  w=BinaryWriter();w.write_command("SET_READY");return w.build()
 async def send(self,data:bytes):
  if self.ws and data:
   try:await self.ws.send(data)
   except:pass
 async def handle_message(self,raw:bytes):
  try:
   r=BinaryReader(raw);cmd=r.read_command()
   if cmd=="PONG":return
   elif cmd=="PING":await self.send(self.make_pong())
   elif cmd=="LOGIN":
    ok=r.i8()
    if ok==0:log.info("[LOGIN] WS OK");await self.send(self.make_enter("Lobby.caro.0"))
    else:log.error("[LOGIN] WS FAILED")
   elif cmd=="ENTER_PLACE":
    if r.i8()==0:log.info("[PLACE] OK");await self.send(self.make_create_rule())
   elif cmd=="CREATE_RULE":
    if r.i8()==0:tid=r.read_ascii();self.table_id=tid;log.info(f"[RULE] Table {tid}");await asyncio.sleep(0.5);await self.send(self.make_get_table())
   elif cmd=="GET_TABLE_DATA_EX":
    if not self.in_table:self.in_table=True;await self.send(self.make_ready())
   elif cmd=="PLAYER_ENTERED":r.i32();nick=r.read_utf();s=r.i16();log.info(f"[+] {nick} slot={s}")
   elif cmd=="PLAYER_EXITED":pid=r.i32();log.info(f"[-] {pid} left")
   elif cmd=="START_MATCH":
    self.slot=r.i16();self.my_symbol=r.i16();bw=r.i16();bh=r.i16()
    log.info(f"[MATCH] slot={self.slot} sym={self.my_symbol} {bw}x{bh}")
    if bw!=self.board.width or bh!=self.board.height:self.board=Board(width=bw,height=bh,n_in_row=N_IN_ROW)
    self.init_board_for_game();self.is_playing=True;self.start_time=time.time()
   elif cmd=="SET_TURN":
    self.is_playing=True
    if len(self.board.availables)<BOARD_WIDTH*BOARD_HEIGHT:
     log.info("[TURN] My turn - thinking...")
     ai=self.get_ai_move()
     if ai:
      ax,ay=ai;self.apply_my_move(ax,ay);await self.send(self.make_play(ay*BOARD_WIDTH+ax))
      log.info(f"[MOVE] BOT ({ax},{ay})")
   elif cmd=="MOVE":
    mt=r.u8();x=r.u8();y=r.u8();sym=r.u8()
    if sym!=self.my_symbol:
     self.apply_opponent_move(x,y);log.info(f"[MOVE] Opp ({x},{y})")
    else:self.apply_my_move(x,y);self._moving=False;log.info(f"[MOVE] Me ({x},{y})")
   elif cmd=="GAMEOVER":
    res=r.u8();r.read_utf();r.u8()
    if res==self.my_symbol:self.wins+=1;log.info(f"[WIN] W{self.wins}/L{self.losses}/D{self.draws}")
    elif res==-1:self.draws+=1;log.info("[DRAW]")
    else:self.losses+=1;log.info("[LOSS]")
    self.is_playing=False;self._moving=False
   elif cmd=="SURRENDER":self.is_playing=False;self._moving=False
   else:log.info(f"[CMD] {cmd}")
  except Exception as e:log.error(f"[MSG] {e}");traceback.print_exc()
 async def run(self):
  log.info(f"BOTTRAIN {BOARD_WIDTH}x{BOARD_HEIGHT} MCTS={MCTS_PLAYOUT}")
  if not await self.http_login():return
  self.start_time=time.time()
  while self._running:
   try:
    async with websockets.connect(WS_URL)as ws:
     self.ws=ws;await self.send(self.make_login())
     while self._running:
      try:
       msg=await asyncio.wait_for(ws.recv(),timeout=30)
       if isinstance(msg,bytes):await self.handle_message(msg)
      except asyncio.TimeoutError:
       if time.time()-self.start_time>RUNTIME:self._running=False;break
   except websockets.ConnectionClosed:log.warning("[WS] Reconnecting...");await asyncio.sleep(5)
   except Exception as e:log.error(f"[WS] {e}");await asyncio.sleep(5)
  log.info(f"[END] W{self.wins}/L{self.losses}/D{self.draws}")
if __name__=="__main__":
 bot=CaroBotTrain()
 try:asyncio.run(bot.run())
 except KeyboardInterrupt:log.info("Interrupted")