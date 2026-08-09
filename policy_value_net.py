# -*- coding: utf-8 -*-
"""Policy-Value Network (CNN) for 15x19 Caro board. Pure NumPy - no DL framework needed."""
import numpy as np
def softmax(x): probs=np.exp(x-np.max(x)); probs/=np.sum(probs); return probs
def relu(X): return np.maximum(X,0)
def conv_forward(X,W,b,stride=1,padding=1):
    nf,df,hf,wf=W.shape; W=W[:,:,::-1,::-1]
    nx,dx,hx,wx=X.shape; ho=(hx-hf+2*padding)//stride+1; wo=(wx-wf+2*padding)//stride+1
    Xc=im2col_indices(X,hf,wf,padding=padding,stride=stride); Wc=W.reshape(nf,-1)
    out=(np.dot(Wc,Xc).T+b).T; out=out.reshape(nf,ho,wo,nx); out=out.transpose(3,0,1,2); return out
def fc_forward(X,W,b): return np.dot(X,W)+b
def get_im2col_indices(xs,fh,fw,pad=1,stride=1):
    N,C,H,W=xs; oh=(H+2*pad-fh)//stride+1; ow=(W+2*pad-fw)//stride+1
    i0=np.tile(np.repeat(np.arange(fh),fw),C); i1=stride*np.repeat(np.arange(oh),ow)
    j0=np.tile(np.tile(np.arange(fw),fh*C)); j1=stride*np.tile(np.arange(ow),oh)
    i=i0.reshape(-1,1)+i1.reshape(1,-1); j=j0.reshape(-1,1)+j1.reshape(1,-1)
    k=np.repeat(np.arange(C),fh*fw).reshape(-1,1); return(k.astype(int),i.astype(int),j.astype(int))
def im2col_indices(x,fh,fw,pad=1,stride=1):
    p=pad; xp=np.pad(x,((0,0),(0,0),(p,p),(p,p)),mode='constant')
    k,i,j=get_im2col_indices(x.shape,fh,fw,pad,stride)
    cols=xp[:,k,i,j]; C=x.shape[1]; cols=cols.transpose(1,2,0).reshape(fh*fw*C,-1); return cols
def init_net_params(bw,bh):
    p=[]; p+=[np.random.randn(32,4,3,3)*0.1,np.zeros(32)]; p+=[np.random.randn(64,32,3,3)*0.1,np.zeros(64)]
    p+=[np.random.randn(128,64,3,3)*0.1,np.zeros(128)]; p+=[np.random.randn(4,128,1,1)*0.1,np.zeros(4)]
    p+=[np.random.randn(4*bw*bh,bw*bh)*0.01,np.zeros(bw*bh)]
    p+=[np.random.randn(2,128,1,1)*0.1,np.zeros(2)]; p+=[np.random.randn(2*bw*bh,64)*0.01,np.zeros(64)]
    p+=[np.random.randn(64,1)*0.01,np.zeros(1)]; return p
class PolicyValueNetNumpy:
    def __init__(self,bw,bh,params): self.bw=bw; self.bh=bh; self.params=params
    def policy_value_fn(self,board):
        legal=board.availables; cs=board.current_state()
        X=cs.reshape(-1,4,self.bw,self.bh)
        for i in[0,2,4]: X=relu(conv_forward(X,self.params[i],self.params[i+1]))
        Xp=relu(conv_forward(X,self.params[6],self.params[7],padding=0))
        Xp=fc_forward(Xp.flatten(),self.params[8],self.params[9]); ap=softmax(Xp)
        Xv=relu(conv_forward(X,self.params[10],self.params[11],padding=0))
        Xv=relu(fc_forward(Xv.flatten(),self.params[12],self.params[13]))
        v=np.tanh(fc_forward(Xv,self.params[14],self.params[15]))[0]
        return zip(legal,ap.flatten()[legal]),v
    def save_model(self,fp): np.save(fp,self.params,allow_pickle=True)