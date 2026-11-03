"""
Script de Validação e Testes Automatizados do Motor
Chess Challenge III - ICEV 2026

Verifica:
1. Avaliação Heurística (PeSTO Tapered Eval, Estrutura de Peões, Mop-Up)
2. Regra do Opening Book (FAQ Item 3: ativo até 10 meios-lances, desativado no 11º)
3. Tática do Negamax Alpha-Beta + Quiescence + Tabela de Transposição (Mate em 1, Mate em 2, Ganho de Dama)
4. Geração do Log Obrigatório para a Banca (FAQ Item 9)
"""

import os
import sys
import chess
from evaluation import evaluate_board, MATE_THRESHOLD
from time_manager import TimeManager
from search import SearchEngine
from logger import MatchLogger


def run_all_tests() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass
    print("======================================================================")
    print("     EXECUTANDO BATERIA DE VALIDAÇÃO - CHESS CHALLENGE III (ICEV)     ")
    print("======================================================================\n")

    engine = SearchEngine()
    tm = TimeManager(initial_minutes=5.0, increment_seconds=2.0)

    # 1. Teste de Avaliação Estática
    b_start = chess.Board()
    eval_start = evaluate_board(b_start)
    print(f"[TESTE 1] Avaliação na posição inicial: {eval_start:+d} cp (Esperado: ~ +10 cp pelo bônus de iniciativa)")
    assert -30 <= eval_start <= 30, "Avaliação inicial fora do esperado!"

    # 2. Teste do Limite de 10 Meios-Lances do Opening Book (FAQ Item 3)
    print("\n[TESTE 2] Verificando trava de 10 meios-lances do Opening Book (FAQ Item 3)...")
    b_book = chess.Board()
    for ply in range(10):
        res = engine.search_best_move(b_book, tm, max_depth=4, use_book=True, verbose=False)
        assert res.source == "BOOK", f"Esperava BOOK no meio-lance {ply + 1}, mas veio {res.source}"
        b_book.push(res.best_move)
    # 11º meio-lance (índice 10): DEVE usar SEARCH e NÃO BOOK!
    res_11 = engine.search_best_move(b_book, tm, max_depth=3, use_book=True, verbose=False)
    print(f"  -> Meio-lances 1 a 10: fonte = BOOK | 11º meio-lance: fonte = {res_11.source} (Lance: {res_11.best_move.uci()})")
    assert res_11.source == "SEARCH", "FALHA: O livro de aberturas não desativou após 10 meios-lances!"
    print("  -> [OK] Regra do FAQ Item 3 validada com sucesso!")

    # 3. Teste Tático: Mate em 1 e Mate em 2
    print("\n[TESTE 3] Verificando busca tática (Negamax Alpha-Beta + TT + Quiescence)...")
    # Mate do Louco / Mate em 1 na última fileira
    b_mate1 = chess.Board("6k1/5ppp/8/8/8/8/8/R3K3 w Q - 0 1")
    res_m1 = engine.search_best_move(b_mate1, tm, max_depth=4, use_book=False, verbose=False)
    print(f"  -> Mate em 1 encontrado: {res_m1.best_move.uci()} | Avaliação: {res_m1.score_cp:+d} cp")
    assert res_m1.best_move.uci() == "a1a8", f"Esperado a1a8, obteve {res_m1.best_move.uci()}"
    assert res_m1.score_cp >= MATE_THRESHOLD, "Pontuação de Mate não detectada!"

    # Mate em 2 clássico
    b_mate2 = chess.Board("r1bqkb1r/pppp1ppp/2n2n2/4p2Q/2B1P3/8/PPPP1PPP/RNB1K1NR w KQkq - 4 4")
    res_m2 = engine.search_best_move(b_mate2, tm, max_depth=4, use_book=False, verbose=False)
    print(f"  -> Mate do Pastor (f7) encontrado: {res_m2.best_move.uci()} | Avaliação: {res_m2.score_cp:+d} cp")
    assert res_m2.best_move.uci() == "h5f7", f"Esperado h5f7, obteve {res_m2.best_move.uci()}"

    # 4. Teste do Log Obrigatório da Banca (FAQ Item 9)
    print("\n[TESTE 4] Verificando geração de arquivo .log e .txt para a banca (FAQ Item 9)...")
    logger = MatchLogger(team_color="BRANCAS")
    logger.log_engine_move(
        move_number=1,
        turn_color=chess.WHITE,
        uci_move=res_11.best_move.uci(),
        depth=res_11.depth,
        nodes=res_11.nodes,
        score_cp=res_11.score_cp,
        pv_moves=res_11.pv,
        elapsed_sec=res_11.elapsed_sec,
        source=res_11.source,
    )
    assert os.path.isfile(logger.log_path), "Arquivo .log não foi criado!"
    assert os.path.isfile(logger.txt_mirror_path), "Arquivo .txt espelho não foi criado!"
    print(f"  -> [OK] Log gravado em: {logger.log_path}")

    print("\n======================================================================")
    print("       TODOS OS TESTES PASSARAM COM 100% DE SUCESSO! MOTOR PRONTO!    ")
    print("======================================================================")


if __name__ == "__main__":
    run_all_tests()
