"""
Interface Universal de Xadrez (Protocolo UCI Padrão)
Chess Challenge III - ICEV 2026

Permite conectar o motor a interfaces gráficas externas (Arena, CuteChess, EnCroissant)
para testes automatizados e visualização de partidas, mantendo o registro de logs oficial.
"""

import sys
import chess
from time_manager import TimeManager
from search import SearchEngine
from logger import MatchLogger


def run_uci_loop() -> None:
    board = chess.Board()
    engine = SearchEngine()
    time_manager = TimeManager(initial_minutes=15.0, increment_seconds=10.0)
    logger = MatchLogger(team_color="UCI_MODE")

    while True:
        line = sys.stdin.readline()
        if not line:
            break
        line = line.strip()
        if not line:
            continue

        tokens = line.split()
        cmd = tokens[0]

        if cmd == "uci":
            print("id name ICEV_Chess_Engine_2026")
            print("id author Equipe ICEV 2026")
            print("uciok")
            sys.stdout.flush()

        elif cmd == "isready":
            print("readyok")
            sys.stdout.flush()

        elif cmd == "ucinewgame":
            board.reset()
            engine.clear_cache()
            logger.log_event("Novo jogo UCI iniciado (ucinewgame).")

        elif cmd == "position":
            if "startpos" in tokens:
                board.reset()
                moves_idx = tokens.index("moves") + 1 if "moves" in tokens else len(tokens)
            elif "fen" in tokens:
                fen_start = tokens.index("fen") + 1
                moves_idx = tokens.index("moves") if "moves" in tokens else len(tokens)
                fen_str = " ".join(tokens[fen_start:moves_idx])
                board.set_fen(fen_str)
                if "moves" in tokens:
                    moves_idx += 1
            else:
                moves_idx = len(tokens)

            for m_str in tokens[moves_idx:]:
                try:
                    board.push_uci(m_str)
                except ValueError:
                    break

        elif cmd == "go":
            # Extrai parâmetros de relógio do comando UCI 'go wtime ... btime ... winc ... binc ...'
            wtime = btime = None
            winc = binc = 10000
            movetime = None
            max_depth = 20

            i = 1
            while i < len(tokens):
                t = tokens[i]
                if t == "wtime" and i + 1 < len(tokens):
                    wtime = int(tokens[i + 1])
                    i += 2
                elif t == "btime" and i + 1 < len(tokens):
                    btime = int(tokens[i + 1])
                    i += 2
                elif t == "winc" and i + 1 < len(tokens):
                    winc = int(tokens[i + 1])
                    i += 2
                elif t == "binc" and i + 1 < len(tokens):
                    binc = int(tokens[i + 1])
                    i += 2
                elif t == "movetime" and i + 1 < len(tokens):
                    movetime = int(tokens[i + 1])
                    i += 2
                elif t == "depth" and i + 1 < len(tokens):
                    max_depth = int(tokens[i + 1])
                    i += 2
                else:
                    i += 1

            if movetime is not None:
                time_manager.remaining_time = (movetime / 1000.0) * 10.0
                time_manager.increment = 0.0
            elif board.turn == chess.WHITE and wtime is not None:
                time_manager.remaining_time = wtime / 1000.0
                time_manager.increment = winc / 1000.0
            elif board.turn == chess.BLACK and btime is not None:
                time_manager.remaining_time = btime / 1000.0
                time_manager.increment = binc / 1000.0

            res = engine.search_best_move(
                board=board,
                time_manager=time_manager,
                max_depth=max_depth,
                use_book=True,
                use_tb=True,
                verbose=False,
            )

            logger.log_engine_move(
                move_number=board.fullmove_number,
                turn_color=board.turn,
                uci_move=res.best_move.uci(),
                depth=res.depth,
                nodes=res.nodes,
                score_cp=res.score_cp,
                pv_moves=res.pv,
                elapsed_sec=res.elapsed_sec,
                source=res.source,
            )

            pv_str = " ".join(res.pv)
            print(
                f"info depth {res.depth} score cp {res.score_cp} nodes {res.nodes} "
                f"time {int(res.elapsed_sec * 1000)} pv {pv_str}"
            )
            print(f"bestmove {res.best_move.uci()}")
            sys.stdout.flush()

        elif cmd == "quit":
            break


if __name__ == "__main__":
    run_uci_loop()
