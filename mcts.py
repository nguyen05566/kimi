# -*- coding: utf-8 -*-
"""MCTS in AlphaZero style for 15x19 Caro board"""
import numpy as np, copy
def softmax(x): probs=np.exp(x-np.max(x)); probs/=np.sum(probs); return probs
class TreeNode:
    def __init__(self,parent,prior_p): self._parent=parent; self._children={}; self._n_visits=0; self._Q=0; self._u=0; self._P=prior_p
    def expand(self,priors):
        for a,prob in priors:
            if a not in self._children: self._children[a]=TreeNode(self,prob)
    def select(self,cp): return max(self._children.items(),key=lambda x:x[1].get_value(cp))
    def update(self,lv): self._n_visits+=1; self._Q+=1.0*(lv-self._Q)/self._n_visits
    def update_recursive(self,lv):
        if self._parent: self._parent.update_recursive(-lv)
        self.update(lv)
    def get_value(self,cp): self._u=cp*self._P*np.sqrt(self._parent._n_visits)/(1+self._n_visits); return self._Q+self._u
    def is_leaf(self): return self._children=={}
    def is_root(self): return self._parent is None
class MCTS:
    def __init__(self,pvf,cp=5,n_playout=400): self._root=TreeNode(None,1.0); self._policy=pvf; self._cp=cp; self._np=n_playout
    def _playout(self,state):
        node=self._root
        while True:
            if node.is_leaf(): break
            action,node=node.select(self._cp); state.do_move(action)
        ap,lv=self._policy(state); end,winner=state.game_end()
        if not end: node.expand(ap)
        else: lv=0.0 if winner==-1 else (1.0 if winner==state.get_current_player() else -1.0)
        node.update_recursive(-lv)
    def get_move_probs(self,state,temp=1e-3):
        for _ in range(self._np):
            sc=copy.deepcopy(state); self._playout(sc)
        av=[(a,n._n_visits)for a,n in self._root._children.items()]
        acts,visits=zip(*av); ap=softmax(1.0/temp*np.log(np.array(visits)+1e-10)); return acts,ap
    def update_with_move(self,lm):
        if lm in self._root._children: self._root=self._root._children[lm]; self._root._parent=None
        else: self._root=TreeNode(None,1.0)
class MCTSPlayer:
    def __init__(self,pvf,cp=5,n_playout=400,is_selfplay=0):
        self.mcts=MCTS(pvf,cp,n_playout); self._sp=is_selfplay
    def set_player_ind(self,p): self.player=p
    def reset_player(self): self.mcts.update_with_move(-1)
    def get_action(self,board,temp=1e-3,return_prob=0):
        moves=board.availables; mp=np.zeros(board.width*board.height)
        if len(moves)>0:
            acts,probs=self.mcts.get_move_probs(board,temp); mp[list(acts)]=probs
            if self._sp:
                move=np.random.choice(acts,p=0.75*probs+0.25*np.random.dirichlet(0.3*np.ones(len(probs))))
                self.mcts.update_with_move(move)
            else: move=np.random.choice(acts,p=probs); self.mcts.update_with_move(-1)
            return(move,mp)if return_prob else move
        return-1
    def __str__(self): return"MCTS {}".format(self.player)