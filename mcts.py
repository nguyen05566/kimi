# -*- coding: utf-8 -*-
"""MCTS in AlphaZero style for 15x19 Caro board"""
import numpy as np, copy
def softmax(x): probs=np.exp(x-np.max(x)); probs/=np.sum(probs); return probs
class TreeNode:
    def __init__(s,p,pr): s._p=p; s._c={}; s._nv=0; s._Q=0; s._u=0; s._P=pr
    def expand(s,ap):
        for a,pr in ap:
            if a not in s._c: s._c[a]=TreeNode(s,pr)
    def select(s,cpu): return max(s._c.items(),key=lambda x:x[1].gv(cpu))
    def update(s,lv): s._nv+=1; s._Q+=1.0*(lv-s._Q)/s._nv
    def urec(s,lv):
        if s._p: s._p.urec(-lv)
        s.update(lv)
    def gv(s,cpu): s._u=cpu*s._P*np.sqrt(s._p._nv)/(1+s._nv); return s._Q+s._u
    def il(s): return s._c=={}
    def ir(s): return s._p is None
class MCTS:
    def __init__(s,pvf,cpu=5,np_val=400): s._rt=TreeNode(None,1.0); s._pol=pvf; s._cpu=cpu; s._np=np_val
    def _po(s,st):
        n=s._rt
        while not n.il(): a,n=n.select(s._cpu); st.dm(a)
        ap,lv=s._pol(st); end,wr=st.ge()
        if not end: n.expand(ap)
        else: lv=0.0 if wr==-1 else(1.0 if wr==st.gcp() else-1.0)
        n.urec(-lv)
    def gmp(s,st,t=1e-3):
        for _ in range(s._np): sc=copy.deepcopy(st); s._po(sc)
        av=[(a,nd._nv)for a,nd in s._rt._c.items()]
        ac,vs=zip(*av); ap=softmax(1.0/t*np.log(np.array(vs)+1e-10)); return ac,ap
    def uwm(s,lm):
        if lm in s._rt._c: s._rt=s._rt._c[lm]; s._rt._p=None
        else: s._rt=TreeNode(None,1.0)
class MCTSPlayer:
    def __init__(s,pvf,c_puct=5,n_playout=400,is_selfplay=0):
        s.mcts=MCTS(pvf,c_puct,n_playout); s._sp=is_selfplay
    def set_player_ind(s,p): s.player=p
    def rp(s): s.mcts.uwm(-1)
    def get_action(s,board,temp=1e-3,return_prob=0):
        mv=board.av; mp=np.zeros(board.width*board.height)
        if len(mv)>0:
            ac,pr=s.mcts.gmp(board,temp); mp[list(ac)]=pr
            if s._sp:
                m=np.random.choice(ac,p=0.75*pr+0.25*np.random.dirichlet(0.3*np.ones(len(pr))))
                s.mcts.uwm(m)
            else: m=np.random.choice(ac,p=pr); s.mcts.uwm(-1)
            return(m,mp)if return_prob else m
        return -1