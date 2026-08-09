#!/usr/bin/env python3
"""BOTTRAIN"""
import subprocess,sys,os,importlib,json,time,struct,logging,asyncio,random,threading,traceback,re
from pathlib import Path
from typing import Optional,Tuple,List
logging.basicConfig(level=logging.INFO,format="%(asctime)s %(message)s",datefmt="%H:%M:%S")
log=logging.getLogger("bottrain")
for pkg in["websockets","requests","numpy"]:
    try:importlib.import_module(pkg)
    except:
        log.info(f"[SETUP] Installing {pkg}...")
        subprocess.run([sys.executable,"-m","pip","install",pkg,"-q","--break-system-packages"],stderr=subprocess.DEVNULL)
        importlib.import_module(pkg)
import websockets,requests
import numpy as np
try:
    from game_15x19 import Board,Game
    from policy_value_net import PolicyValueNetNumpy,init_net_params,softmax
    from mcts import MCTSPlayer
except:
    sys.path.insert(0,str(Path(__file__).parent))
    from game_15x19 import Board,Game
    from policy_value_net import PolicyValueNetNumpy,init_net_params,softmax
    from mcts import MCTSPlayer