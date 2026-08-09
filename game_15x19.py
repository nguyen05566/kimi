# -*- coding: utf-8 -*-
"""Board game 15x19 (Caro/Rule-8) for AlphaZero training"""
from __future__ import print_function
import numpy as np
class Board:
    def __init__(self,**kw):
        self.width=int(kw.get('width',15))
        self.height=int(kw.get('height',19))
        self.states={};self.n=int(kw.get('n_in_row',5));self.pl=[1,2]
    def iboard(self,sp=0):
        if self.width<self.n or self.height<self.n:raise Exception('board too small')
        self.cp=self.pl[sp];self.av=list(range(self.width*self.height));self.states={};self.lm=-1
    def mtl(self,m):return[m//self.width,m%self.width]
    def ltm(self,l):
        if len(l)!=2:return-1
        m=l[0]*self.width+l[1];return m if 0<=m<self.width*self.height else-1
    def cs(self):
        s=np.zeros((4,self.height,self.width))
        if self.states:
            mk,vk=zip(*self.states.items())
            mv=np.array(list(mk));pv=np.array(list(vk))
            mc=mv[pv==self.cp];mo=mv[pv!=self.cp]
            s[0][mc//self.width,mc%self.width]=1.0
            s[1][mo//self.width,mo%self.width]=1.0
            if self.lm>=0:s[2][self.lm//self.width,self.lm%self.width]=1.0
        if len(self.states)%2==0:s[3][:,:]=1.0
        return s[:,::-1,:]
    def dm(self,m):
        self.states[m]=self.cp;self.av.remove(m)
        self.cp=self.pl[0]if self.cp==self.pl[1]else self.pl[1];self.lm=m
    def haw(self):
        w,h,s,n=self.width,self.height,self.states,self.n
        mv=list(set(range(w*h))-set(self.av))
        if len(mv)<n*2-1:return False,-1
        for m in mv:
            y,x=m//w,m%w;p=s[m]
            if x<=w-n and all(s.get(i,-1)==p for i in range(m,m+n)):return True,p
            if y<=h-n and all(s.get(i,-1)==p for i in range(m,m+n*w,w)):return True,p
            if x<=w-n and y<=h-n and all(s.get(i,-1)==p for i in range(m,m+n*(w+1),w+1)):return True,p
            if x>=n-1 and y<=h-n and all(s.get(i,-1)==p for i in range(m,m+n*(w-1),w-1)):return True,p
        return False,-1
    def ge(self):
        win,wr=self.haw()
        if win:return True,wr
        elif not self.av:return True,-1
        return False,-1
    def gcp(self):return self.cp
class Game:
    def __init__(self,b,**kw):self.board=b
    def ssp(self,pl,sh=0,t=1e-3):
        self.board.iboard();sts,mp,cp=[],[],[]
        while True:
            m,ms=pl.get_action(self.board,temp=t,return_prob=1)
            sts.append(self.board.cs());mp.append(ms);cp.append(self.board.cp)
            self.board.dm(m);end,wr=self.board.ge()
            if end:
                wz=np.zeros(len(cp))
                if wr!=-1:
                    wz[np.array(cp)==wr]=1.0
                    wz[np.array(cp)!=wr]=-1.0
                pl.reset_player();return wr,zip(sts,mp,wz)