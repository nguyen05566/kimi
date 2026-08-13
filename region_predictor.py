"""Bộ dự đoán khung thông minh dùng mạng nơ-ron (inference thuần numpy).
Dùng cho bot: thay thế _compute_origin rule-based.
Không cần torch/tensorflow.
"""
import os
import numpy as np
from nnlib import Net

BOARD_W = 15
BOARD_H = 19
ENGINE = 15
MAX_OY = BOARD_H - ENGINE  # 4

_HERE = os.path.dirname(os.path.abspath(__file__))


def build_feature(board):
    """board: (19,15) int, quy ước 1 = ta, 2 = địch, 0 = trống -> vector feature."""
    board = np.asarray(board, dtype=int)
    mine = (board == 1).astype(np.float64)
    opp = (board == 2).astype(np.float64)
    feat = []
    feat.append(mine.ravel())
    feat.append(opp.ravel())
    feat.append(mine.sum(axis=1) / ENGINE)
    feat.append(opp.sum(axis=1) / ENGINE)
    feat.append(np.array([mine.sum(), opp.sum()]) / (BOARD_W * BOARD_H))
    return np.concatenate(feat)




def board_history_to_matrix(board_history, my_symbol, width=15, height=19):
    """Chuyển board_history (list (x,y,sym)) + my_symbol -> ma trận (height,width) int:
    0 trống, 1 = ta (my_symbol), 2 = địch."""
    m = np.zeros((height, width), dtype=int)
    for x, y, sym in board_history:
        if 0 <= x < width and 0 <= y < height:
            m[y, x] = 1 if sym == my_symbol else 2
    return m


class RegionPredictor:
    def __init__(self, weights_path=None):
        if weights_path is None:
            weights_path = os.path.join(_HERE, "region_net.npz")
        self.net = Net.load(weights_path)

    def predict(self, board):
        """Trả (oy, center_y, probs). oy là vị trí khung 15x15 (0..4) trên trục Y."""
        f = build_feature(board)
        X = f[None, :].astype(np.float64)
        oy, reg, probs = self.net.predict(X)
        return int(oy[0]), float(reg[0]), probs[0]

    def compute_origin(self, board):
        """Giao diện giống _compute_origin: chọn origin (ox, oy) cho khung 15x15."""
        oy, center_y, probs = self.predict(board)
        # ox luôn 0 (bàn rộng 15)
        return 0, oy


if __name__ == "__main__":
    # Demo: tạo vài trạng thái và dự đoán
    from data_gen import rand_scenario, to_label
    rng = np.random.default_rng(7)
    pred = RegionPredictor("region_net.npz")
    print("=== Demo dự đoán khung ===")
    for i in range(5):
        board, cy = rand_scenario(rng)
        oy, cy_pred, probs = pred.predict(board)
        oy_true = to_label(cy)
        print(f"  #{i}: center_y={cy:.1f} -> oy_true={oy_true} oy_pred={oy} "
              f"cy_pred={cy_pred:.1f} probs={np.round(probs,2)}")
