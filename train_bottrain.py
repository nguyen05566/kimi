#!/usr/bin/env python3
"""train_bottrain.py - Self-play training for BOTTRAIN on CI. No GPU needed - pure NumPy."""
import os,sys,time,random; from pathlib import Path; import numpy as np
sys.path.insert(0,str(Path(__file__).parent))
from game_15x19 import Board,Game; from policy_value_net import PolicyValueNetNumpy,init_net_params; from mcts import MCTSPlayer
BW,BH,N=15,19,5; MODEL=Path(__file__).parent/"bottrain_model.npy"; DATA=Path(__file__).parent/"bottrain_data"; DATA.mkdir(exist_ok=True)
NG=int(os.environ.get("TRAIN_GAMES","50")); NP=int(os.environ.get("TRAIN_PLAYOUT","200")); EPOCHS=5; BS=512
print("="*60); print("BOTTRAIN Self-Play Training"); print(f"Board:{BW}x{BH}|Games:{NG}|Playouts:{NP}"); print("="*60)
if MODEL.exists(): params=np.load(MODEL,allow_pickle=True); print(f"[MODEL] Loaded from {MODEL}")
else: print("[MODEL] New random weights"); params=init_net_params(BW,BH)
net=PolicyValueNetNumpy(BW,BH,params); board=Board(width=BW,height=BH,n_in_row=N); game=Game(board); ad=[]
print(f"\n[TRAIN] Starting {NG} self-play games..."); t0=time.time()
for i in range(NG):
    mcts=MCTSPlayer(net.policy_value_fn,c_puct=5,n_playout=NP,is_selfplay=1)
    w,pd=game.start_self_play(mcts,temp=1.0)
    for s,mp,z in pd: ad.append((s,mp,z))
    if(i+1)%10==0:
        e=time.time()-t0; at=e/(i+1); r=at*(NG-i-1)
        print(f"  Game{i+1}/{NG}|Winner:{w}|Time:{e:.0f}s|ETA:{r:.0f}s")
t1=time.time(); print(f"\n[DONE]{NG}games in{t1-t0:.0f}s({NG/(t1-t0)*3600:.0f}g/h)"); print(f"[DATA]{len(ad)}examples")
ts=time.strftime("%Y%m%d_%H%M%S"); df=DATA/f"selfplay_{ts}.npz"
sa=np.array([d[0]for d in ad]); pa=np.array([d[1]for d in ad]); za=np.array([d[2]for d in ad])
np.savez_compressed(df,states=sa,probs=pa,zs=za); print(f"[DATA]Saved to{df}({df.stat().st_size/1024:.0f}KB)")
print(f"\n[TRAIN]Training on{len(ad)}examples,{EPOCHS}epochs...")
asl,pl,zl=[sa],[pa],[za]
for f in sorted(DATA.glob("selfplay_*.npz"))[-20:]:
    if f.name==f"selfplay_{ts}.npz":continue
    d=np.load(f); asl.append(d['states']); pl.append(d['probs']); zl.append(d['zs'])
X=np.concatenate(asl,axis=0); Yp=np.concatenate(pl,axis=0); Yz=np.concatenate(zl,axis=0)
nx=len(X); print(f"[TRAIN]Total examples:{nx}")
if nx>=100:
    for ep in range(EPOCHS):
        idx=np.random.permutation(nx); tl=0; btc=0
        for st in range(0,nx,BS):
            bi=idx[st:st+BS]; bx=X[bi]; byp=Yp[bi]; byz=Yz[bi]; loss=0
            for j in range(len(bx)):
                s4=bx[j]; b=Board(width=BW,height=BH,n_in_row=N); b.init_board()
                cc,oc=s4[0],s4[1]
                for y in range(BH):
                    for x in range(BW):
                        if cc[x,y]>0.5: b.states[y*BW+x]=b.current_player; b.availables.remove(y*BW+x)
                        elif oc[x,y]>0.5: op=1 if b.current_player==2 else 2; b.states[y*BW+x]=op; b.availables.remove(y*BW+x)
                ap,v=net.policy_value_fn(b); tz=byz[j]; loss+=(v-tz)**2
                pdict=dict(ap); tp=byp[j]
                for mi,tp_v in enumerate(tp):
                    if tp_v>1e-10: pp=max(pdict.get(mi,1e-10),1e-10); loss-=tp_v*np.log(pp)
            tl+=loss/len(bx); btc+=1
        print(f"  Epoch{ep+1}/{EPOCHS}|Loss:{tl/max(btc,1):.4f}")
np.save(MODEL,net.params,allow_pickle=True); print(f"\n[MODEL]Saved to{MODEL}"); print("[DONE]!")