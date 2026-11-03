"""
Benchmark rápido do motor (rode no notebook do torneio para conhecer a velocidade)
Chess Challenge III - ICEV 2026

    python tools/bench.py            # profundidade 9 em 8 posições (~1 min)
    python tools/bench.py --prof 10

Mostra profundidade, nós, tempo e nós/segundo. Use sempre com o notebook na tomada.
"""

import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import chess  # noqa: E402

from search import SearchEngine  # noqa: E402
from time_manager import TimeManager  # noqa: E402

POSICOES = [
    "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
    "r1bqkb1r/pppp1ppp/2n2n2/4p3/2B1P3/5N2/PPPP1PPP/RNBQK2R w KQkq - 4 4",
    "r3k2r/p1ppqpb1/bn2pnp1/3PN3/1p2P3/2N2Q1p/PPPBBPPP/R3K2R w KQkq - 0 1",
    "r2q1rk1/1p1nbppp/p2pbn2/4p3/4P3/1NN1BP2/PPPQ2PP/2KR1B1R w - - 4 11",
    "rnbq1rk1/ppp1ppbp/3p1np1/8/2PPP3/2N2N2/PP2BPPP/R1BQK2R b KQ - 3 6",
    "2rq1rk1/pb1nbppp/1p2pn2/2pp4/2PP4/1PN1PN2/PB2BPPP/2RQ1RK1 w - - 0 11",
    "8/2p5/3p4/KP5r/1R3p1k/8/4P1P1/8 w - - 0 1",
    "6k1/5pp1/p1r4p/1p1p4/3P4/P1P2P2/1P3KPP/4R3 w - - 0 30",
]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--prof", type=int, default=9)
    args = ap.parse_args()
    total_t = 0.0
    total_n = 0
    for fen in POSICOES:
        board = chess.Board(fen)
        engine = SearchEngine()
        tm = TimeManager()
        tm.set_fixed_time(10_000.0)
        tm.should_stop_iteration = lambda: False
        t0 = time.perf_counter()
        r = engine.search_best_move(board, tm, max_depth=args.prof, use_book=False, use_tb=False, verbose=False)
        dt = time.perf_counter() - t0
        total_t += dt
        total_n += r.nodes
        print(f"prof {r.depth:2d}  nós {r.nodes:8d}  tempo {dt:6.2f}s  {r.nodes / dt:7.0f} nós/s  lance {r.best_move.uci()}")
    print(f"TOTAL: {total_n} nós em {total_t:.1f}s = {total_n / total_t:.0f} nós/s")


if __name__ == "__main__":
    main()
