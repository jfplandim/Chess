"""
Simulador de Partida de Teste (Demonstração Completa do Motor e do Log Oficial)
Chess Challenge III - ICEV 2026

Simula 8 lances completos (16 meios-lances):
- Lances 1 a 5 (meios-lances 1 a 10): Usa o Opening Book (FAQ Item 3)
- Lances 6 a 8 (meios-lances 11 a 16): Transição automática para a Busca Alpha-Beta + TT + PeSTO Eval
Ao final, exibe o arquivo de Log Oficial gerado exatamente como será entregue à banca (FAQ Item 9).
"""

import sys
import chess
from time_manager import TimeManager
from search import SearchEngine
from logger import MatchLogger
from main import print_board_visual, describe_move_pt


def run_simulation() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass

    print("==============================================================================")
    print("         SIMULAÇÃO DE PARTIDA OFICIAL - CHESS CHALLENGE III (ICEV 2026)       ")
    print("==============================================================================")

    board = chess.Board()
    engine = SearchEngine()
    # Simula com tempo rápido (2 min + 1s) para demonstração ágil no terminal
    tm_white = TimeManager(initial_minutes=2.0, increment_seconds=1.0)
    tm_black = TimeManager(initial_minutes=2.0, increment_seconds=1.0)
    logger = MatchLogger(team_color="BRANCAS (SIMULACAO)")

    total_half_moves = 16  # 8 lances das Brancas e 8 das Pretas

    for ply in range(total_half_moves):
        if board.is_game_over():
            break

        is_white = board.turn == chess.WHITE
        color_str = "BRANCAS" if is_white else "PRETAS"
        tm = tm_white if is_white else tm_black
        move_num = board.fullmove_number

        print(f"\n--- [Meio-Lance {ply + 1}/16 | Lance #{move_num} das {color_str}] ---")
        res = engine.search_best_move(
            board=board,
            time_manager=tm,
            max_depth=5,  # Profundidade 5 para simulação rápida
            use_book=True,
            use_tb=True,
            verbose=True,
        )

        desc = describe_move_pt(board, res.best_move)
        tm.register_move_time(res.elapsed_sec)

        logger.log_engine_move(
            move_number=move_num,
            turn_color=board.turn,
            uci_move=res.best_move.uci(),
            depth=res.depth,
            nodes=res.nodes,
            score_cp=res.score_cp,
            pv_moves=res.pv,
            elapsed_sec=res.elapsed_sec,
            source=res.source,
        )

        print(
            f"  >>> ANÚNCIO VOZ ALTA (UCI): {res.best_move.uci()} | "
            f"Tabuleiro: {desc} | Origem: [{res.source}]"
        )
        board.push(res.best_move)

    print("\n==============================================================================")
    print("                        POSIÇÃO APÓS 8 LANCES COMPLETOS                       ")
    print("==============================================================================")
    print_board_visual(board, perspective=chess.WHITE, last_move=board.peek())

    print("==============================================================================")
    print("       CONTEÚDO DO ARQUIVO DE LOG GERADO PARA A BANCA (FAQ ITEM 9)            ")
    print("==============================================================================")
    with open(logger.txt_mirror_path, "r", encoding="utf-8") as f:
        print(f.read())


if __name__ == "__main__":
    run_simulation()
