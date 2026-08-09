# -*- coding: utf-8 -*-
"""
Board game 15x19 (Caro/Rule-8) for AlphaZero training
"""
from __future__ import print_function
import numpy as np

class Board(object):
    def __init__(self, **kwargs):
        self.width = int(kwargs.get('width', 15)); self.height = int(kwargs.get('height', 19))
        self.states = {}; self.n_in_row = int(kwargs.get('n_in_row', 5)); self.players = [1, 2]
    def init_board(self, start_player=0):
        if self.width < self.n_in_row or self.height < self.n_in_row: raise Exception('board too small')
        self.current_player = self.players[start_player]; self.availables = list(range(self.width*self.height)); self.states = {}; self.last_move = -1
    def move_to_location(self, move): return [move//self.width, move%self.width]
    def location_to_move(self, loc):
        if len(loc)!=2: return -1
        m=loc[0]*self.width+loc[1]; return m if m in range(self.width*self.height) else -1
    def current_state(self):
        s=np.zeros((4,self.height,self.width))
        if self.states:
            moves,players=np.array(list(zip(*self.states.items())))
            mc=moves[players==self.current_player]; mo=moves[players!=self.current_player]
            s[0][mc//self.width,mc%self.width]=1.0; s[1][mo//self.width,mo%self.width]=1.0
            if self.last_move>=0: s[2][self.last_move//self.width,self.last_move%self.width]=1.0
        if len(self.states)%2==0: s[3][:,:]=1.0
        return s[:,::-1,:]
    def do_move(self, move):
        self.states[move]=self.current_player; self.availables.remove(move)
        self.current_player=self.players[0] if self.current_player==self.players[1] else self.players[1]; self.last_move=move
    def has_a_winner(self):
        w,h,s,n=self.width,self.height,self.states,self.n_in_row
        moved=list(set(range(w*h))-set(self.availables))
        if len(moved)<n*2-1: return False,-1
        for m in moved:
            y,x=m//w,m%w; p=s[m]
            if x in range(w-n+1) and len(set(s.get(i,-1) for i in range(m,m+n)))==1: return True,p
            if y in range(h-n+1) and len(set(s.get(i,-1) for i in range(m,m+n*w,w)))==1: return True,p
            if x in range(w-n+1) and y in range(h-n+1) and len(set(s.get(i,-1) for i in range(m,m+n*(w+1),w+1)))==1: return True,p
            if x in range(n-1,w) and y in range(h-n+1) and len(set(s.get(i,-1) for i in range(m,m+n*(w-1),w-1)))==1: return True,p
        return False,-1
    def game_end(self):
        win,winner=self.has_a_winner()
        if win: return True,winner
        elif not self.availables: return True,-1
        return False,-1
    def get_current_player(self): return self.current_player

class Game(object):
    def __init__(self,board,**kwargs): self.board=board
    def start_self_play(self,player,is_shown=0,temp=1e-3):
        self.board.init_board();p1,p2=self.board.players;states,mcts_probs,cp=[],[],[]
        while True:
            move,move_probs=player.get_action(self.board,temp=temp,return_prob=1)
            states.append(self.board.current_state());mcts_probs.append(move_probs);cp.append(self.board.current_player)
            self.board.do_move(move);end,winner=self.board.game_end()
            if end:
                wz=np.zeros(len(cp))
                if winner!=-1:
                    wz[np.array(cp)==winner]=1.0;wz[np.array(cp)!=winner]=-1.0
                player.reset_player();return winner,zip(states,mcts_probs,wz)