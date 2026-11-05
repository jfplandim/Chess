"""
Script de Validação e Testes Automatizados do Motor
Chess Challenge III - ICEV 2026

Rode antes de cada partida:  python test_engine.py

Verifica:
 1. Avaliação: posição inicial ~equilibrada e SIMETRIA (Brancas x Pretas espelhadas)
 2. Livro de aberturas: ativo até 10 meios-lances e desligado no 11º (FAQ Item 3)
 3. Geração de lances em estágios == lances legais da python-chess (sem perder/duplicar)
 4. SEE (Static Exchange Evaluation) em trocas simples
 5. Táticas: mates em 1 e 2, sacrifício de Dama com mate (WAC.001), garfo de Cavalo
 6. Empates: repetição, material insuficiente, afogamento
 7. Gestão de tempo: nunca orça mais que o relógio (15+10, 25+0, Armageddon, emergência)
 8. A busca SEMPRE devolve lance legal (posições aleatórias, pouco tempo)
 9. Pondering: inicia/para rápido e nunca roda junto com a busca principal
10. Log obrigatório da banca (FAQ Item 9)
11. Contempt contado a partir da nossa cor (Armageddon, FAQ Item 12)
12. Motor do torneio: Stockfish com a nossa gestão de tempo (livro, desempenho Baixo/Médio/Ultra,
    limite rígido, táticas, lances legais, ponder e reabertura se o processo cair). Sem Stockfish
    instalado, só avisa.
"""

import os
import random
import sys
import threading
import time

import chess

from evaluation import evaluate, evaluate_board, MATE_THRESHOLD
from time_manager import TimeManager
from search import SearchEngine, see_ge
from logger import MatchLogger


def fixed_tm(seconds: float) -> TimeManager:
    tm = TimeManager()
    tm.set_fixed_time(seconds)
    return tm


def random_positions(n: int, seed: int = 2026, max_plies: int = 140) -> list[chess.Board]:
    rng = random.Random(seed)
    out = []
    while len(out) < n:
        b = chess.Board()
        for _ in range(rng.randint(0, max_plies)):
            moves = list(b.legal_moves)
            if not moves:
                break
            b.push(rng.choice(moves))
        if not b.is_game_over():
            out.append(b)
    return out


def run_all_tests() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass
    t_start = time.time()
    print("======================================================================")
    print("     EXECUTANDO BATERIA DE VALIDAÇÃO - CHESS CHALLENGE III (ICEV)     ")
    print("======================================================================\n")

    engine = SearchEngine()

    # 1. Avaliação
    eval_start = evaluate_board(chess.Board())
    print(f"[TESTE 1] Avaliação na posição inicial: {eval_start:+d} cp (esperado ~ +10, bônus de tempo)")
    assert -30 <= eval_start <= 30, "Avaliação inicial fora do esperado!"
    asym = 0
    for b in random_positions(400, seed=1):
        if evaluate(b) != evaluate(b.mirror()):
            asym += 1
    print(f"  -> Simetria Brancas/Pretas em 400 posições aleatórias: {400 - asym}/400 iguais")
    assert asym == 0, "Avaliação assimétrica!"

    # 2. Livro de aberturas (FAQ Item 3)
    print("\n[TESTE 2] Trava de 10 meios-lances do Opening Book (FAQ Item 3)...")
    b_book = chess.Board()
    tm = TimeManager(initial_minutes=5.0, increment_seconds=2.0)
    for ply in range(10):
        res = engine.search_best_move(b_book, tm, max_depth=4, use_book=True, verbose=False)
        assert res.source == "BOOK", f"Esperava BOOK no meio-lance {ply + 1}, veio {res.source}"
        b_book.push(res.best_move)
    res_11 = engine.search_best_move(b_book, fixed_tm(0.5), max_depth=6, use_book=True, verbose=False)
    print(f"  -> Meio-lances 1 a 10: BOOK | 11º meio-lance: {res_11.source} ({res_11.best_move.uci()})")
    assert res_11.source == "SEARCH", "O livro não desligou após 10 meios-lances!"

    # 3. Geração em estágios
    print("\n[TESTE 3] Geração de lances em estágios == lances legais (python-chess)...")
    rng = random.Random(3)
    checked = 0
    extra = [chess.Board("r3k2r/p1ppqpb1/bn2pnp1/3PN3/1p2P3/2N2Q1p/PPPBBPPP/R3K2R w KQkq - 0 1"),
             chess.Board("rnbq1k1r/pp1Pbppp/2p5/8/2B5/8/PPP1NnPP/RNBQK2R w KQ - 1 8"),
             chess.Board("8/2p5/3p4/KP5r/1R3p1k/8/4P1P1/8 w - - 0 1")]
    for b in random_positions(300, seed=3) + extra:
        legal = list(b.legal_moves)
        tt = rng.choice(legal)
        engine.killers[0] = [rng.randrange(4096), rng.randrange(4096)]
        got = [m for _, m, _ in engine._staged_moves(b, tt, 0, None)]
        assert len(got) == len(set(got)) and set(got) == set(legal), f"Gerador divergente em {b.fen()}"
        checked += 1
    print(f"  -> {checked} posições conferidas: nenhum lance perdido ou duplicado")

    # 4. SEE
    print("\n[TESTE 4] Static Exchange Evaluation (SEE)...")
    b = chess.Board("4k3/8/2p5/3p4/8/8/3Q4/4K3 w - - 0 1")  # Dxd5 perde a Dama para o peão c6
    assert not see_ge(b, chess.Move.from_uci("d2d5"), 0), "SEE deveria marcar Dxd5 como ruim"
    b = chess.Board("4k3/8/8/3q4/4P3/8/8/4K3 w - - 0 1")   # exd5 ganha a Dama
    assert see_ge(b, chess.Move.from_uci("e4d5"), 0), "SEE deveria marcar exd5 como bom"
    b = chess.Board("4k3/8/8/3p4/4P3/8/8/3RK3 w - - 0 1")  # exd5: troca de peões
    assert see_ge(b, chess.Move.from_uci("e4d5"), 0)
    print("  -> [OK] trocas ruins/boas classificadas corretamente")

    # 5. Táticas
    print("\n[TESTE 5] Busca tática (Negamax/PVS + TT + Quiescence)...")
    tactics = [
        ("6k1/5ppp/8/8/8/8/8/R3K3 w Q - 0 1", "a1a8", "Mate em 1 na última fileira"),
        ("r1bqkb1r/pppp1ppp/2n2n2/4p2Q/2B1P3/8/PPPP1PPP/RNB1K1NR w KQkq - 4 4", "h5f7", "Mate do Pastor"),
        ("2rr3k/pp3pp1/1nnqbN1p/3pN3/2pP4/2P3Q1/PPB4P/R4RK1 w - - 0 1", "g3g6", "WAC.001 sacrifício Dg6!!"),
        ("r3k2r/ppp2ppp/2n5/3N4/8/8/PPP2PPP/R3K2R w KQkq - 0 1", "d5c7", "Garfo de Cavalo (rei+torre)"),
        ("6k1/5ppp/8/8/8/8/5PPP/3R2K1 w - - 0 1", "d1d8", "Mate do corredor"),
        ("7k/8/6K1/8/8/8/8/5Q2 w - - 0 1", "f1f8", "Mate (sem afogar o rei)"),
    ]
    for fen, best, name in tactics:
        b = chess.Board(fen)
        res = engine.search_best_move(b, fixed_tm(2.0), use_book=False, verbose=False)
        ok = res.best_move.uci() == best
        print(f"  -> {name:32s}: {res.best_move.uci()} (prof. {res.depth}, {res.score_cp:+d}) {'OK' if ok else 'FALHOU'}")
        assert ok, f"{name}: esperado {best}, obteve {res.best_move.uci()}"

    # 6. Empates
    print("\n[TESTE 6] Detecção de empates...")
    b = chess.Board("8/8/8/4k3/8/8/8/4KB2 w - - 0 1")
    assert evaluate_board(b) == 0, "K+B x K deveria ser empate"
    # Repetição: depois de Cf3 Cf6 Cg1 Cg8 Cf3 Cf6 Cg1, o lance ...Cg8 repete a posição inicial
    from search import position_key

    b = chess.Board()
    for uci in ["g1f3", "g8f6", "f3g1", "f6g8", "g1f3", "g8f6", "f3g1"]:
        b.push_uci(uci)
    engine._prepare_root(b)
    engine.hash_stack.append(position_key(engine.board))
    engine.board.push_uci("f6g8")
    assert engine._is_repetition(position_key(engine.board), engine.board.halfmove_clock), "Repetição não detectada!"
    print("  -> K+B x K = empate | repetição de posição detectada pela pilha de chaves")
    # Afogamento: com Dama a mais, NÃO pode afogar o rei adversário
    b = chess.Board("7k/8/6K1/8/8/8/8/5Q2 w - - 0 1")
    res = engine.search_best_move(b, fixed_tm(1.5), use_book=False, verbose=False)
    b.push(res.best_move)
    assert not b.is_stalemate(), "O motor afogou o rei adversário com vantagem decisiva!"
    print(f"  -> Evitou afogamento com Dama a mais: {res.best_move.uci()}")

    # 7. Gestão de tempo
    print("\n[TESTE 7] Gestão de tempo (nunca estoura o relógio)...")
    cases = [
        ("15+10 início", 15, 10, 900.0, 4.0),
        ("15+10 apuro", 15, 10, 25.0, 4.0),
        ("25+0 meio", 25, 0, 600.0, 4.0),
        ("Armageddon 4min", 4, 0, 240.0, 4.0),
        ("Armageddon final", 4, 0, 12.0, 4.0),
        ("Relógio 3s", 15, 10, 3.0, 4.0),
    ]
    b = chess.Board("r1bq1rk1/pp2bppp/2n1pn2/3p4/2PP4/2N1PN2/PP3PPP/R2QKB1R w KQ - 0 9")
    for name, mins, inc, remaining, op in cases:
        tmx = TimeManager(initial_minutes=mins, increment_seconds=inc, move_overhead=0.5, operator_overhead=op)
        tmx.remaining_time = remaining
        soft, hard = tmx.allocate_time(b, 30)
        print(f"  -> {name:18s}: relógio {remaining:6.1f}s -> alvo {soft:6.2f}s | máximo {hard:6.2f}s")
        assert 0 < soft <= hard < max(0.2, remaining - 0.4), f"Orçamento inválido em {name}"

    # 8. Sempre lance legal
    print("\n[TESTE 8] A busca sempre devolve lance legal (60 posições aleatórias, 0,15 s)...")
    for b in random_positions(60, seed=8):
        res = engine.search_best_move(b, fixed_tm(0.15), use_book=False, verbose=False)
        assert res.best_move in b.legal_moves, f"Lance ilegal {res.best_move} em {b.fen()}"
    print("  -> [OK] 60/60 lances legais")

    # 9. Pondering
    print("\n[TESTE 9] Pondering (FAQ Item 13): inicia, para rápido e não concorre com a busca...")
    from main import PonderController

    ponder = PonderController(engine)
    b = chess.Board()
    b.push_uci("e2e4")
    ponder.start_ponder(b, predicted_uci="e7e5")
    time.sleep(1.0)
    t0 = time.perf_counter()
    ponder.stop_ponder()
    stop_time = time.perf_counter() - t0
    print(f"  -> Ponder interrompido em {stop_time * 1000:.0f} ms")
    assert stop_time < 1.0, "Ponder demorou demais para parar!"
    res = engine.search_best_move(b, fixed_tm(0.5), use_book=False, verbose=False)
    assert res.best_move in b.legal_moves

    # 10. Log
    print("\n[TESTE 10] Log obrigatório da banca (FAQ Item 9)...")
    logger = MatchLogger(team_color="BRANCAS (TESTE)")
    logger.log_engine_move(
        move_number=1, turn_color=chess.WHITE, uci_move=res_11.best_move.uci(), depth=res_11.depth,
        nodes=res_11.nodes, score_cp=res_11.score_cp, pv_moves=res_11.pv,
        elapsed_sec=res_11.elapsed_sec, source=res_11.source,
    )
    assert os.path.isfile(logger.log_path) and os.path.isfile(logger.txt_mirror_path)
    with open(logger.log_path, encoding="utf-8") as f:
        content = f.read()
    assert res_11.best_move.uci() in content
    print(f"  -> [OK] Log gravado em: {logger.log_path}")

    # 11. Contempt pela nossa cor (Armageddon - FAQ Item 12: empate classifica as Pretas)
    print("\n[TESTE 11] Contempt contado a partir da nossa cor (Armageddon, FAQ Item 12)...")
    rep = chess.Board()  # Pretas a jogar: ...Cg8 repete a posição inicial
    for uci in ["g1f3", "g8f6", "f3g1", "f6g8", "g1f3", "g8f6", "f3g1"]:
        rep.push_uci(uci)
    arm = SearchEngine(tt_bits=16)
    arm.our_color = chess.BLACK
    arm.contempt = -100  # Pretas no Armageddon: empate = classificação
    res = arm.search_best_move(rep, fixed_tm(1.0), use_book=False, use_tb=False, verbose=False)
    assert res.best_move.uci() == "f6g8", f"Pretas deveriam repetir com f6g8, jogaram {res.best_move.uci()}"
    print(f"  -> Pretas, contempt -100: repetem com {res.best_move.uci()} ({res.score_cp:+d} cp)")
    arm = SearchEngine(tt_bits=16)
    arm.our_color = chess.BLACK
    arm.contempt = 100  # sinal trocado: agora o empate é ruim para nós
    res = arm.search_best_move(rep, fixed_tm(1.0), use_book=False, use_tb=False, verbose=False)
    assert res.best_move.uci() != "f6g8", "Com contempt +100 as Pretas não deveriam repetir"
    print(f"  -> Pretas, contempt +100: evitam a repetição com {res.best_move.uci()}")
    # Ponder sem lance previsto: a raiz é a vez do adversário (Brancas), mas o contempt é o NOSSO
    adv = chess.Board()
    for uci in ["g1f3", "g8f6", "f3g1", "f6g8", "g1f3", "g8f6"]:
        adv.push_uci(uci)
    arm = SearchEngine(tt_bits=16)
    arm.our_color = chess.BLACK
    arm.contempt = -100
    arm._prepare_root(adv)
    assert arm._draw_score(0) == -100 and arm._draw_score(1) == 100, "Sinal do empate invertido no ponder"
    res = arm.search_best_move(adv, fixed_tm(1.0), use_book=False, use_tb=False, verbose=False)
    assert res.best_move.uci() != "f3g1", "O adversário simulado não deveria aceitar o empate que nos classifica"
    print(f"  -> Raiz do adversário (ponder): Brancas evitam o empate que classifica as Pretas ({res.best_move.uci()})")

    # 12. Motor do torneio: Stockfish com a nossa gestão de tempo (FAQ itens 1 e 2)
    print("\n[TESTE 12] Stockfish com a nossa gestão de tempo (FAQ itens 1 e 2)...")
    from motor_stockfish import criar_motor, descrever_motor, encontrar_stockfish

    if encontrar_stockfish() is None:
        print("  -> [AVISO] Stockfish não encontrado: só o motor próprio está disponível.")
        print("     Instale antes do torneio com: python tools/baixar_stockfish.py")
    else:
        sf = criar_motor("stockfish")
        print(f"  -> {descrever_motor(sf)}")
        res = sf.search_best_move(chess.Board(), fixed_tm(1.0), verbose=False)
        assert res.source == "BOOK", "Na posição inicial, quem responde é o nosso livro de aberturas"
        # Desempenho Baixo, Médio e Ultra no mesmo processo; termina em Ultra (força total)
        from motor_stockfish import fmt_threads, recursos_do_nivel

        for nivel in ("baixo", "medio", "ultra"):
            sf.definir_desempenho(nivel)
            assert (sf.threads, sf.hash_mb) == recursos_do_nivel(nivel), f"Recursos errados no nível {nivel}"
            res_nivel = sf.search_best_move(random_positions(1, seed=40)[0], fixed_tm(0.2), use_book=False,
                                            verbose=False)
            assert res_nivel.nodes > 0
        print("  -> Desempenho: " + ", ".join(
            f"{nome} {fmt_threads(recursos_do_nivel(nivel)[0])}/{recursos_do_nivel(nivel)[1]} MB"
            for nivel, nome in (("baixo", "Baixo"), ("medio", "Médio"), ("ultra", "Ultra"))))
        print(f"  -> Livro de aberturas primeiro: {res.best_move.uci()}")
        # Quem para a busca é a nossa gestão de tempo: com o relógio baixo, o limite rígido é curto
        b = chess.Board("r1bq1rk1/pp2bppp/2n1pn2/3p4/2PP4/2N1PN2/PP3PPP/R2QKB1R w KQ - 0 9")
        tmx = TimeManager(initial_minutes=15, increment_seconds=10, move_overhead=0.5, operator_overhead=4.0)
        tmx.remaining_time = 60.0
        res = sf.search_best_move(b, tmx, use_book=False, verbose=False)
        assert res.best_move in b.legal_moves and res.source == "SEARCH"
        assert res.elapsed_sec <= tmx.hard_limit + 0.3, f"Passou do limite rígido: {res.elapsed_sec:.2f}s"
        print(f"  -> Relógio com 60 s: {res.best_move.uci()} em {res.elapsed_sec:.2f}s (alvo {tmx.optimum:.2f}s, "
              f"limite {tmx.hard_limit:.2f}s), profundidade {res.depth}, {res.nodes} nós")
        for fen, best, name in tactics:
            res = sf.search_best_move(chess.Board(fen), fixed_tm(1.0), use_book=False, verbose=False)
            assert res.best_move.uci() == best, f"{name}: esperado {best}, obteve {res.best_move.uci()}"
        print(f"  -> [OK] {len(tactics)}/{len(tactics)} posições táticas do teste 5")
        for b in random_positions(20, seed=12):
            res = sf.search_best_move(b, fixed_tm(0.1), use_book=False, verbose=False)
            assert res.best_move in b.legal_moves, f"Lance ilegal {res.best_move} em {b.fen()}"
        print("  -> [OK] 20/20 lances legais em posições aleatórias (0,1 s)")
        ponder = PonderController(sf)
        b = chess.Board()
        b.push_uci("e2e4")
        ponder.start_ponder(b, predicted_uci="e7e5")
        time.sleep(0.5)
        t0 = time.perf_counter()
        ponder.stop_ponder()
        stop_time = time.perf_counter() - t0
        assert stop_time < 0.5, "O ponder com o Stockfish demorou demais para parar"
        print(f"  -> Ponder interrompido em {stop_time * 1000:.0f} ms")
        # Se o processo do Stockfish cair no meio do torneio, a próxima busca abre outro sozinha
        if hasattr(os, "kill") and sys.platform != "win32":
            import signal

            os.kill(sf._sf.protocol.transport.get_pid(), signal.SIGKILL)
            time.sleep(0.3)
            res = sf.search_best_move(b, fixed_tm(0.3), use_book=False, verbose=False)
            assert res.best_move in b.legal_moves
            print("  -> [OK] Processo derrubado de propósito: a busca seguinte reabriu o Stockfish")

    print("\n======================================================================")
    print(f"   TODOS OS TESTES PASSARAM ({time.time() - t_start:.0f}s). MOTOR PRONTO PARA O TORNEIO!")
    print("======================================================================")


if __name__ == "__main__":
    run_all_tests()
