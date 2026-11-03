"""
Extrai posições QUIETAS rotuladas com o resultado da partida, a partir de um PGN,
para o Texel Tuning (tools/texel.py).

    python tools/extrair_pgn.py partidas.pgn dados_texel.txt --por-partida 8

Critérios: a partir do 16º meio-lance, sem xeque, o lance jogado a seguir não é
captura/promoção, e a quiescência do nosso motor concorda com a avaliação estática
(diferença <= 30 cp), ou seja, não há trocas pendentes.
"""

import argparse
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import chess  # noqa: E402
import chess.pgn  # noqa: E402

from evaluation import evaluate  # noqa: E402
from search import SearchEngine, INF  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("pgn")
    ap.add_argument("saida")
    ap.add_argument("--por-partida", type=int, default=8)
    ap.add_argument("--max-partidas", type=int, default=10**9)
    ap.add_argument("--seed", type=int, default=1)
    args = ap.parse_args()

    rng = random.Random(args.seed)
    eng = SearchEngine(tt_bits=16)
    eng.hard_deadline = 0
    results_map = {"1-0": "1.0", "0-1": "0.0", "1/2-1/2": "0.5"}
    n_games = n_pos = 0
    with open(args.pgn, encoding="utf-8", errors="replace") as f, open(args.saida, "a") as out:
        while n_games < args.max_partidas:
            game = chess.pgn.read_game(f)
            if game is None:
                break
            res = results_map.get(game.headers.get("Result", "*"))
            if res is None:
                continue
            n_games += 1
            board = game.board()
            candidates = []
            moves = list(game.mainline_moves())
            for ply, move in enumerate(moves):
                if ply >= 16 and not board.is_check() and not board.is_capture(move) and move.promotion is None:
                    candidates.append(board.fen())
                board.push(move)
            rng.shuffle(candidates)
            taken = 0
            for fen in candidates:
                if taken >= args.por_partida:
                    break
                b = chess.Board(fen)
                static = evaluate(b)
                eng._prepare_root(b)
                q = eng._qsearch(-INF, INF, 0)
                if abs(q - static) > 30:
                    continue
                out.write(f"{fen};{res}\n")
                taken += 1
                n_pos += 1
            if n_games % 500 == 0:
                print(f"\rpartidas {n_games}  posições {n_pos}", end="", flush=True)
    print(f"\rpartidas {n_games}  posições {n_pos}")


if __name__ == "__main__":
    main()
