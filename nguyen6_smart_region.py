"""Smart sliding-window launcher for nguyen6.py.

Keeps the existing AlphaGomoku engine wrapper intact, but replaces the
region selector with a full 15x19 -> 15x15 adaptive scorer before starting
nguyen6.main().
"""
from __future__ import annotations

from typing import Dict, List, Tuple
import nguyen6


def _line_length(stones: Dict[Tuple[int, int], int], x: int, y: int, dx: int, dy: int, sym: int) -> int:
    """Length of the contiguous line through (x,y), including both sides."""
    total = 1
    nx, ny = x + dx, y + dy
    while stones.get((nx, ny)) == sym:
        total += 1
        nx += dx
        ny += dy
    nx, ny = x - dx, y - dy
    while stones.get((nx, ny)) == sym:
        total += 1
        nx -= dx
        ny -= dy
    return total


def _smart_compute_origin(self, board_history: list) -> Tuple[int, int]:
    """Choose the best vertical 15x15 window on the 15x19 board.

    Scoring considers the whole position, recent activity, last-move safety
    margin, and whether important 3+/4+ lines would be cut outside the window.
    The current window receives hysteresis so it is kept when it is nearly as
    good as the new winner, avoiding needless RESTART/BOARD cycles.
    """
    max_oy = max(0, self.board_height - self.ENGINE_SIZE)
    if not board_history or max_oy == 0:
        return 0, 0

    stones = {(x, y): sym for x, y, sym in board_history}
    recent = board_history[-8:]
    total_recent_weight = sum(1.8 ** i for i in range(len(recent)))
    last_x, last_y, _ = board_history[-1]
    directions = ((1, 0), (0, 1), (1, 1), (1, -1))

    # Mark stones that participate in a meaningful threat/chain. These are
    # much more expensive to cut than an isolated old stone.
    critical: Dict[Tuple[int, int], int] = {}
    for x, y, sym in board_history:
        best = 1
        for dx, dy in directions:
            best = max(best, _line_length(stones, x, y, dx, dy, sym))
        if best >= 3:
            critical[(x, y)] = best

    scores: Dict[int, float] = {}
    for oy in range(max_oy + 1):
        bottom = oy + self.ENGINE_SIZE - 1
        score = 0.0

        # Keep the most recent battle inside the window. Newer moves have
        # exponentially larger influence than old moves.
        for i, (x, y, _sym) in enumerate(recent):
            w = 1.8 ** i
            if oy <= y <= bottom:
                score += 42.0 * w
            else:
                score -= 55.0 * w

        # The last move should normally sit at least two rows from a window
        # edge. This leaves AlphaGomoku room to see continuations.
        if oy <= last_y <= bottom:
            rel = last_y - oy
            margin = min(rel, bottom - last_y)
            score += 130.0
            if margin >= 2:
                score += 45.0
            else:
                score -= 80.0
        else:
            score -= 300.0

        # Preserve the important historical position. Cutting a 3/4/5-chain
        # is substantially worse than cutting an isolated old stone.
        for (x, y), chain_len in critical.items():
            if not (oy <= y <= bottom):
                score -= 180.0 * (chain_len - 2)

        # Small penalty for dropping ordinary stones outside the region.
        outside = 0
        for x, y, _sym in board_history:
            if not (oy <= y <= bottom):
                outside += 1
        score -= outside * 3.0

        # Prefer a centered recent battle when scores are otherwise close.
        recent_cy = sum(y * (1.8 ** i) for i, (_x, y, _s) in enumerate(recent)) / total_recent_weight
        center = oy + (self.ENGINE_SIZE - 1) / 2.0
        score -= abs(center - recent_cy) * 2.5

        scores[oy] = score

    best_oy = max(scores, key=scores.get)

    # Hysteresis: do not slide for a tiny score improvement. This avoids
    # repeatedly restarting the engine when the battle oscillates around a
    # window boundary.
    current = self._committed_oy
    if current is not None and 0 <= current <= max_oy:
        current_score = scores[current]
        best_score = scores[best_oy]
        if best_oy != current and best_score < current_score + 35.0:
            best_oy = current

    # Hard edge protection: if the latest move is at the top/bottom, the
    # window must follow it regardless of historical score.
    if last_y <= 1:
        best_oy = 0
    elif last_y >= self.board_height - 2:
        best_oy = max_oy

    log = getattr(nguyen6, "log", None)
    if log:
        log.info(
            "[AG][SMART-REGION] scores="
            + ", ".join(f"oy={oy}:{scores[oy]:.1f}" for oy in range(max_oy + 1))
            + f" -> selected oy={best_oy} critical={len(critical)}"
        )
    return 0, best_oy


# Patch only the region decision. Mapping, RESTART safety, engine I/O and
# server protocol remain exactly as implemented in nguyen6.py.
nguyen6.AlphaGomokuEngine._compute_origin = _smart_compute_origin

if __name__ == "__main__":
    nguyen6.main()
