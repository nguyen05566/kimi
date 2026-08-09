#!/usr/bin/env python3
"""BOTTRAIN - AlphaZero Self-Learning Caro Bot. Board: 15x19 | Rule 8 | Pure NumPy CNN + MCTS."""
import subprocess,sys,os,importlib,json,time,struct,logging,asyncio,random,traceback
from pathlib import Path

logging.basicConfig(level=logging.INFO,format="%(asctime)s %(message)s",datefmt="%H:%M:%S")
log=logging.getLogger("bottrain")

REQUIRED=["websockets","requests","numpy"]
for pkg in REQUIRED:
    try:importlib.import_module(pkg)
    except ImportError:
        log.info(f"[SETUP] Installing {pkg}...")
        subprocess.run([sys.executable,"-m","pip","install",pkg,"-q","--break-system-packages"],stderr=subprocess.DEVNULL)
        importlib.import_module(pkg)

import websockets,requests,numpy as np

try:
    from game_15x19 import Board as GBoard
    from policy_value_net import PolicyValueNetNumpy,init_net_params
    from mcts import MCTSPlayer
except ImportError:
    sys.path.insert(0,str(Path(__file__).parent))
    from game_15x19 import Board as GBoard
    from policy_value_net import PolicyValueNetNumpy,init_net_params
    from mcts import MCTSPlayer

BASE_DIR=Path(__file__).parent
MODEL_PATH=BASE_DIR/"bottrain_model.npy"
WS_URL="wss://gamevh.net/ws/gameServer"
USER=os.environ.get("CARO_USER","");PASSWD=os.environ.get("CARO_PASSWD","")
VERSION="5.0.0";GAME_ID="caro"
RUNTIME=int(os.environ.get("CARO_RUNTIME_SECONDS")or float(os.environ.get("CARO_RUNTIME_HOURS","5.9"))*3600)
BOT_BET_XU=1000;EMPTY=-1;CIRCLE=0;CROSS=1
BW=15;BH=19;N_ROW=5
MCTS_P=min(int(os.environ.get("MCTS_PLAYOUT","200")),400);MCTS_CPUCT=5

CMD_MAP={300:"PONG",301:"PING",302:"LOGIN",303:"ALERT",401:"ENTER_PLACE",402:"ENTER_CHILD_PLACE",405:"CREATE_RULE",406:"PLAYER_ENTERED",407:"PLAYER_EXITED",410:"KICK_PLAYER",413:"LIST_BET_AMT",414:"GET_TABLE_DATA",417:"START_MATCH",418:"GAMEOVER",419:"ENTER_STATE",420:"SET_TURN",421:"SET_PLAYER_STATUS",422:"SET_PLAYER_POINT",432:"OWNER_CHANGED",433:"GET_TABLE_DATA_EX",434:"SET_READY",501:"BET",502:"PLAY",505:"CHAT",518:"HIGHLIGHT",529:"MOVE",533:"ASK_DRAW",534:"SURRENDER",535:"RETREAT"}

class BR:
    def __init__(s,d):s.d=d;s.p=0
    def u8(s):
        if s.p>=len(s.d):return 0
        v=s.d[s.p];s.p+=1;return v
    def i16(s):
        if s.p+2>len(s.d):return 0
        v=struct.unpack_from('>h',s.d,s.p)[0];s.p+=2;return v
    def i32(s):
        if s.p+4>len(s.d):return 0
        v=struct.unpack_from('>i',s.d,s.p)[0];s.p+=4;return v
    def i64(s):
        if s.p+8>len(s.d):return 0
        hi=struct.unpack_from('>i',s.d,s.p)[0];lo=struct.unpack_from('>I',s.d,s.p+4)[0];s.p+=8;return(hi<<32)+lo
    def rascii(s):
        if s.p>=len(s.d):return""
        n=s.u8()
        if s.p+n>len(s.d):n=len(s.d)-s.p
        r=s.d[s.p:s.p+n].decode('ascii','replace');s.p+=n;return r
    def rutf(s):
        if s.p+2>len(s.d):return""
        n=s.i16()
        if n<=0:return""
        bl=n*2
        if s.p+bl>len(s.d):bl=len(s.d)-s.p
        r=s.d[s.p:s.p+bl].decode('utf-16-be','replace');s.p+=bl;return r
    def rbytes(s):
        if s.p+2>len(s.d):return[]
        n=s.i16()
        if s.p+n>len(s.d):n=len(s.d)-s.p
        r=list(s.d[s.p:s.p+n]);s.p+=n;return r
    def rcmd(s):
        f=s.i16()
        if f<0:
            n=-f
            if s.p+n>len(s.d):n=len(s.d)-s.p
            r=s.d[s.p:s.p+n].decode('ascii','replace');s.p+=n;return r
        cid=f&0xFFFF;return CMD_MAP.get(cid,f"CMD_{cid}")

class BW:
    def __init__(s):s.pt=[]
    def u8(s,v):s.pt.append(struct.pack('>B',v))
    def i16(s,v):s.pt.append(struct.pack('>h',v))
    def i32(s,v):s.pt.append(struct.pack('>i',v))
    def rascii(s,s2):e=s2.encode('ascii','replace');s.u8(len(e));s.pt.append(e)
    def rutf(s,s2):e=s2.encode('utf-16-be');s.i16(len(e)//2);s.pt.append(e)
    def build(s):return b''.join(s.pt)

class CaroBotTrain:
    def __init__(s):
        s.ws=None;s.board=GBoard(width=BW,height=BH,n_in_row=N_ROW)
        s.slot=-1;s.my_symbol=CROSS;s.is_playing=False;s.in_table=False
        s.players={};s.nickname="";s.token=0;s.cookie=""
        s.start_time=None;s.last_activity=time.time();s._running=True
        s.wins=0;s.losses=0;s.draws=0;s.table_id=None;s._moving=False
        s.net=None;s.mcts_player=None;s.load_model()
    def load_model(s):
        if MODEL_PATH.exists():
            try:params=np.load(MODEL_PATH,allow_pickle=True);log.info(f"[MODEL]Loaded")
            except:log.warning("[MODEL]Failed,init new");params=init_net_params(BW,BH)
        else:log.info("[MODEL]No model,init random");params=init_net_params(BW,BH)
        s.net=PolicyValueNetNumpy(BW,BH,params)
        s.mcts_player=MCTSPlayer(s.net.policy_value_fn,c_puct=MCTS_CPUCT,n_playout=MCTS_P,is_selfplay=0)
    def init_board(s):s.board.init_board();s.mcts_player.reset_player()
    def b2m(s,x,y):return y*s.board.width+x
    def ai_move(s,ox,oy):
        try:
            if len(s.board.availables)<=1:return None
            move=s.mcts_player.get_action(s.board,temp=1e-3,return_prob=0)
            if move is not None:loc=s.board.move_to_location(move);return loc[0],loc[1]
            return None
        except Exception as e:log.error(f"[MCTS]Error:{e}");traceback.print_exc();return None
    def apply_op(s,x,y):
        m=s.b2m(x,y)
        if m in s.board.availables:s.board.do_move(m)
    def apply_me(s,x,y):
        m=s.b2m(x,y)
        if m in s.board.availables:s.board.do_move(m)
    async def http_login(s):
        try:
            session=requests.Session();resp=session.get("https://gamevh.net/signin",timeout=15)
            if resp.status_code!=200:log.error(f"[LOGIN]GET failed:{resp.status_code}");return False
            ck=dict(session.cookies);s.cookie=";".join(f"{k}={v}"for k,v in ck.items())
            hd={"Content-Type":"application/json","Cookie":s.cookie,"Origin":"https://gamevh.net","Referer":"https://gamevh.net/signin"}
            r2=session.post("https://gamevh.net/api/signin",json={"username":USER,"password":PASSWD},headers=hd,timeout=15)
            if r2.status_code!=200:log.error(f"[LOGIN]POST failed:{r2.status_code}");return False
            d=r2.json();s.token=d.get("token",0);s.nickname=d.get("displayName",d.get("username","BOTTRAIN"))
            log.info(f"[LOGIN]OK-{s.nickname}(token={s.token})");return True
        except Exception as e:log.error(f"[LOGIN]Error:{e}");return False
    def _bmsg(s,cmd,flds):
        w=BW();w.i16(len(cmd));w.pt.append(cmd.encode('ascii'))
        for f in flds:
            if isinstance(f,int):w.i32(f)
            elif isinstance(f,str):w.rutf(f)
            elif isinstance(f,bytes):w.pt.append(f)
        return w.build()
    async def _send(s,ws,cmd,*args):
        try:msg=s._bmsg(cmd,list(args));await ws.send(msg)
        except Exception as e:log.error(f"[SEND]{cmd}error:{e}")
    async def send_login(s):await s._send(s.ws,"LOGIN",s.token,s.cookie,"",VERSION,GAME_ID,False)
    async def send_enter(s):await s._send(s.ws,"ENTER_PLACE","Lobby.caro.0")
    async def send_rule(s):await s._send(s.ws,"CREATE_RULE","caro_8","Caro Rule 8")
    async def send_join(s,tid):await s._send(s.ws,"ENTER_CHILD_PLACE",f"Lobby.caro.0/{tid}")
    async def send_bet(s):await s._send(s.ws,"BET",s.table_id,BOT_BET_XU)
    async def send_play(s,x,y):pos=y*BW+x;await s._send(s.ws,"PLAY",pos);s._moving=True
    async def handle_msg(s,raw):
        try:
            r=BR(raw);cmd=r.rcmd()
            if cmd=="PONG":return
            elif cmd=="PING":await s._send(s.ws,"PONG")
            elif cmd=="LOGIN":
                ok=r.u8()
                if ok:log.info("[LOGIN]OK");await s.send_enter()
                else:log.error(f"[LOGIN]FAIL:{r.rascii()}")
            elif cmd=="ENTER_PLACE":
                if r.u8():log.info("[ENTER_PLACE]OK");await s.send_rule()
            elif cmd=="CREATE_RULE":log.info("[CREATE_RULE]Registered")
            elif cmd=="ENTER_CHILD_PLACE":
                if r.u8():log.info(f"[TABLE]Joined{s.table_id}");s.in_table=True;await s.send_bet()
            elif cmd=="LIST_BET_AMT":
                n=r.i16();amts=[r.i32()for _ in range(n)]
                if amts and s.table_id is not None:await s.send_bet()
            elif cmd in("GET_TABLE_DATA","GET_TABLE_DATA_EX"):
                s.table_id=r.i32();name=r.rascii();nc=r.i16()
                for _ in range(nc):r.i32();r.i16();r.i16();r.i16();r.rascii()
                if not s.in_table and s.table_id:await s.send_join(s.table_id)
            elif cmd=="PLAYER_ENTERED":log.info(f"[PLAYER]{r.rascii()}joined");r.i32()
            elif cmd=="PLAYER_EXITED":log.info(f"[PLAYER]{r.i32()}left")
            elif cmd=="START_MATCH":
                s.slot=r.i16();s.my_symbol=r.i16();bw2=r.i16();bh2=r.i16()
                log.info(f"[MATCH]START slot={s.slot}sym={s.my_symbol}board={bw2}x{bh2}")
                if bw2!=s.board.width or bh2!=s.board.height:s.board=GBoard(width=bw2,height=bh2,n_in_row=N_ROW)
                s.init_board();s.is_playing=True;s.start_time=time.time()
            elif cmd=="SET_TURN":s.is_playing=True
            elif cmd=="MOVE":
                r.u8();x=r.u8();y=r.u8();sym=r.u8()
                if sym!=s.my_symbol:
                    s.apply_op(x,y);log.info(f"[MOVE]Op:({x},{y})")
                    m2=s.ai_move(x,y)
                    if m2:
                        ax,ay=m2;s.apply_me(ax,ay);await s.send_play(ax,ay)
                        log.info(f"[MOVE]BOTTRAIN:({ax},{ay})")
                else:s.apply_me(x,y);s._moving=False;log.info(f"[MOVE]Me:({x},{y})")
            elif cmd=="GAMEOVER":
                rs=r.u8();r.rascii();r.u8()
                if rs==s.my_symbol:s.wins+=1;log.info(f"[GAMEOVER]WIN!(W:{s.wins}L:{s.losses}D:{s.draws})")
                elif rs==-1:s.draws+=1;log.info(f"[GAMEOVER]DRAW")
                else:s.losses+=1;log.info(f"[GAMEOVER]LOSS")
                s.is_playing=False;s._moving=False
            elif cmd in("SURRENDER","RETREAT"):s.is_playing=False;s._moving=False
        except Exception as e:log.error(f"[MSG]Error:{e}")
    async def run(s):
        log.info("="*60);log.info("BOTTRAIN-AlphaZero Self-Learning Caro Bot")
        log.info(f"Board:{BW}x{BH}|Rule:8|MCTS:{MCTS_P}playouts");log.info("="*60)
        if not await s.http_login():log.error("Login failed");return
        s.start_time=time.time()
        while s._running:
            try:
                hd={"Cookie":s.cookie,"Origin":"https://gamevh.net"}
                async with websockets.connect(WS_URL,extra_headers=hd)as ws:
                    s.ws=ws;await s.send_login()
                    while s._running:
                        try:
                            msg=await asyncio.wait_for(ws.recv(),timeout=30)
                            if isinstance(msg,bytes):await s.handle_msg(msg)
                        except asyncio.TimeoutError:
                            if time.time()-s.start_time>RUNTIME:log.info(f"[TIME]Limit({RUNTIME}s)exiting");s._running=False;break
            except websockets.ConnectionClosed:log.warning("[WS]Closed,reconnecting...");await asyncio.sleep(5)
            except Exception as e:log.error(f"[WS]Error:{e}");await asyncio.sleep(5)
        log.info(f"[BOTTRAIN]Ended.W:{s.wins}L:{s.losses}D:{s.draws}")

if __name__=="__main__":
    bot=CaroBotTrain()
    try:asyncio.run(bot.run())
    except KeyboardInterrupt:log.info("[BOTTRAIN]Interrupted")