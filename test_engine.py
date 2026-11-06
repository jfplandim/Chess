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
 7. Gestão de tempo: nunca orça mais que o relógio (15+10, 25+0, Armageddon, emergência) e, numa
    partida longa de 15+10, o relógio nunca fica abaixo da zona de sobrevivência
 8. A busca SEMPRE devolve lance legal (posições aleatórias, pouco tempo)
 9. Pondering: inicia/para rápido e nunca roda junto com a busca principal
10. Log obrigatório da banca (FAQ Item 9)
11. Contempt contado a partir da nossa cor (Armageddon, FAQ Item 12)
12. Motor do torneio: Stockfish com a nossa gestão de tempo (livro, desempenho Baixo/Médio/Ultra,
    limite rígido, táticas, lances legais, ponder, reabertura se o processo cair, melhor lance até
    a queda e processo travado). Sem Stockfish instalado, só avisa.
13. Leitura do lance digitado pelo operador: todo lance legal em UCI vira exatamente aquele lance;
    lance nulo, casa repetida e SAN ambíguo viram erro; nenhum texto derruba a interface
14. Custo do operador recalibrado pelo relógio físico a cada sincronização (só para cima) e
    leitura do relógio digitado no F3 (formato estrito, confirmação para valor improvável)
15. Crédito do ponder: o tempo pensado no turno do adversário, na posição que apareceu, conta como
    já gasto (limite menor, piso de confirmação, vale uma vez só) e o PonderController só dá crédito
    quando o adversário joga o lance previsto
16. Empate morto: duas buscas seguidas com nota 0 e profundidade alta -> 30% do tempo no lance seguinte
17. Regra do empate imediato do Stockfish (Armageddon e contempt): Pretas jogam o lance que empata na
    hora, Brancas fogem dele e do lance que deixa o adversário empatar; contempt decide nas outras partidas
18. Armageddon com incremento, regra ligada pela sessão (e desligada na partida seguinte), aviso de
    bandeira sem incremento e aviso de energia durante a partida
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
    # Partida longa de 15+10 (150 lances), o motor gastando sempre o alvo: o relógio desce até a
    # zona de sobrevivência (~42 s com operador de 4 s) e fica ali; gastando sempre o máximo, não estoura.
    for gasta_maximo in (False, True):
        tmx = TimeManager(initial_minutes=15, increment_seconds=10, move_overhead=0.5, operator_overhead=4.0)
        menor = tmx.remaining_time
        for n in range(1, 151):
            b.fullmove_number = n
            soft, hard = tmx.allocate_time(b, 30)
            tmx.register_move_time(hard if gasta_maximo else tmx.optimum)
            menor = min(menor, tmx.remaining_time - 10.0)  # antes do incremento entrar
            assert tmx.remaining_time - 10.0 > 0, f"Estourou o relógio no lance {n}"
        if not gasta_maximo:
            assert menor >= 30.0, f"O relógio desceu a {menor:.1f}s numa partida longa"
        print(f"  -> 150 lances de 15+10 gastando sempre o {'máximo' if gasta_maximo else 'alvo'}: "
              f"menor relógio {menor:.1f}s")

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
    import tempfile

    # Numa pasta temporária: o teste não pode deixar log falso em logs/ nem trocar o
    # logs/ultima_partida_banca.txt, que é o espelho entregue à banca.
    logger = MatchLogger(base_dir=tempfile.mkdtemp(prefix="teste_log_"), team_color="BRANCAS (TESTE)")
    logger.log_engine_move(
        move_number=1, turn_color=chess.WHITE, uci_move=res_11.best_move.uci(), depth=res_11.depth,
        nodes=res_11.nodes, score_cp=res_11.score_cp, pv_moves=res_11.pv,
        elapsed_sec=res_11.elapsed_sec, source=res_11.source,
    )
    assert os.path.isfile(logger.log_path) and os.path.isfile(logger.txt_mirror_path)
    with open(logger.log_path, encoding="utf-8") as f:
        content = f.read()
    assert res_11.best_move.uci() in content
    print("  -> [OK] Log gravado com os campos do Item 9 (numa pasta temporária, apagada em seguida)")
    import shutil

    shutil.rmtree(os.path.dirname(logger.logs_dir), ignore_errors=True)

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
            # Caiu NO MEIO da busca: vale o melhor lance que ele já tinha mostrado (não um lance de emergência)
            b = chess.Board("r1bq1rk1/pp2bppp/2n1pn2/3p4/2PP4/2N1PN2/PP3PPP/R2QKB1R w KQ - 0 9")
            pid = sf._garantir().protocol.transport.get_pid()
            threading.Timer(1.0, lambda: os.kill(pid, signal.SIGKILL)).start()
            res = sf.search_best_move(b, fixed_tm(4.0), use_book=False, verbose=False)
            assert res.best_move in b.legal_moves and res.depth >= 8 and res.elapsed_sec < 2.0
            print(f"  -> [OK] Caiu no meio da busca: jogou o melhor lance até ali ({res.best_move.uci()}, "
                  f"profundidade {res.depth}, {res.elapsed_sec:.1f}s)")
            # Travou (congelado): para no limite rígido + 1,5 s, derruba o processo e joga o melhor lance
            from motor_stockfish import ESPERA_BESTMOVE

            pid = sf._garantir().protocol.transport.get_pid()
            threading.Timer(0.5, lambda: os.kill(pid, signal.SIGSTOP)).start()
            tmx = fixed_tm(1.5)
            t0 = time.perf_counter()
            res = sf.search_best_move(b, tmx, use_book=False, verbose=False)
            gasto = time.perf_counter() - t0
            assert res.best_move in b.legal_moves and gasto <= tmx.hard_limit + ESPERA_BESTMOVE + 0.5
            os.kill(pid, signal.SIGKILL)
            res = sf.search_best_move(b, fixed_tm(0.3), use_book=False, verbose=False)
            assert res.best_move in b.legal_moves
            print(f"  -> [OK] Travou no meio da busca: lance em {gasto:.1f}s (limite {tmx.hard_limit:.1f}s) "
                  "e a busca seguinte abriu outro processo")

    # 13. Leitura do lance digitado pelo operador (interfaces do torneio)
    print("\n[TESTE 13] Leitura do lance digitado: nunca um lance errado, nunca uma exceção...")
    from session import ler_lance

    b = chess.Board()
    for texto in ("0000", "--", "Z0", "@@@@", "e2e2", "a1a1", "e2-e2"):
        leitura = ler_lance(b, texto)
        assert leitura.estado == "erro", f"'{texto}' deveria ser erro, foi {leitura.estado} ({leitura.lance})"
    b = chess.Board("4k3/8/8/2p5/1P1B4/8/8/4K3 w - - 0 1")  # bxc5 (peão) e Bxc5 (bispo) são legais
    assert ler_lance(b, "bxc5").estado == "erro", "SAN minúsculo ambíguo virou lance"
    assert ler_lance(b, "Bxc5").lance == chess.Move.from_uci("d4c5")
    rng = random.Random(13)
    lidos = 0
    for b in random_positions(40, seed=13):
        for m in b.legal_moves:
            for texto in (m.uci(), m.uci().upper(), f" {m.uci()[:2]}-{m.uci()[2:]} "):
                leitura = ler_lance(b, texto)
                assert leitura.estado == "ok" and leitura.lance == m, f"'{texto}' leu {leitura.lance} em {b.fen()}"
                lidos += 1
        for _ in range(50):
            texto = "".join(rng.choice("abcdefgh12345678qrbnkxBNRQK0O-=+# ") for _ in range(rng.randint(1, 7)))
            leitura = ler_lance(b, texto)  # não pode levantar exceção
            assert leitura.estado != "ok" or leitura.lance in b.legal_moves, f"'{texto}' virou lance ilegal"
    print(f"  -> [OK] {lidos // 3} lances legais lidos certo, em 3 grafias cada; lance nulo, casa repetida e SAN "
          "ambíguo viram erro")

    # 14. Custo do operador recalibrado pelo relógio físico
    print("\n[TESTE 14] Custo do operador recalibrado a cada sincronização do relógio...")
    import functools

    import session as sessao_mod
    from search import SearchResult

    pasta = tempfile.mkdtemp(prefix="teste_sessao_")
    salvos = (sessao_mod.LOGS_DIR, sessao_mod.ARQUIVO_SALVO, sessao_mod.ARQUIVO_SALVO_ANTERIOR, sessao_mod.MatchLogger)
    sessao_mod.LOGS_DIR = os.path.join(pasta, "logs")  # não mexe na partida salva de verdade (logs/)
    sessao_mod.ARQUIVO_SALVO = os.path.join(pasta, "logs", "partida_em_andamento.json")
    sessao_mod.ARQUIVO_SALVO_ANTERIOR = os.path.join(pasta, "logs", "partida_salva_anterior.json")
    sessao_mod.MatchLogger = functools.partial(MatchLogger, base_dir=pasta)
    try:
        s = sessao_mod.GameSession(sessao_mod.Config(our_color=chess.WHITE, motor="proprio", operador=4.0, ponder=False))
        rng = random.Random(14)

        def jogar(n):
            for _ in range(n):
                quietos = [m for m in s.board.legal_moves if not s.board.is_capture(m)] or list(s.board.legal_moves)
                m = rng.choice(quietos)
                s.registrar_nosso(SearchResult(m, 0, 10, 1000, 0.0, [m.uci()], "SEARCH", 10, [m]))
                quietos = [m for m in s.board.legal_moves if not s.board.is_capture(m)] or list(s.board.legal_moves)
                s.registrar_adversario(rng.choice(quietos))

        jogar(10)
        aviso = s.sincronizar(s.relogios()[0] - 20.0, None)  # operador real 2 s mais lento por lance
        assert aviso and abs(s.time_manager.operator_overhead - 6.0) < 0.05, aviso
        jogar(6)
        assert s.sincronizar(s.relogios()[0] + 60.0, None) is None  # árbitro parou o relógio: não baixa
        assert abs(s.time_manager.operator_overhead - 6.0) < 0.05
        jogar(6)
        s.desfazer(2)
        assert s.sincronizar(s.relogios()[0] - 30.0, None) is None  # depois de desfazer: não mede
        print(f"  -> [OK] 4,0 s -> {s.time_manager.operator_overhead:.1f} s com o operador 2 s mais lento; "
              "não baixa com o relógio parado e não mede depois de desfazer")
        # Relógio digitado no F3: formato estrito e confirmação para valor improvável
        from session import ler_relogio

        assert ler_relogio("12:30") == ler_relogio("12.30") == ler_relogio("750") == 750.0
        assert ler_relogio("19.5") == 19.5
        for texto in ("12:99", "inf", "nan", "-5", "1e9", "abc", ""):
            try:
                ler_relogio(texto)
                raise AssertionError(f"'{texto}' foi aceito como relógio")
            except ValueError:
                pass
        jogar(4)
        assert s.suspeita_relogio(1230.0, None), "1230 s (20:30) depois de poucos lances deveria pedir confirmação"
        assert s.suspeita_relogio(s.relogios()[0] - 2.0, None) is None
        aviso = s.sincronizar(s.relogios()[0] - 200.0, None)  # 50 s por lance: não é o operador
        assert aviso and "não mudou" in aviso and abs(s.time_manager.operator_overhead - 6.0) < 0.05
        print("  -> [OK] F3: 12:99, inf e -5 recusados; 1230 pede confirmação; diferença absurda não recalibra")
    finally:
        (sessao_mod.LOGS_DIR, sessao_mod.ARQUIVO_SALVO, sessao_mod.ARQUIVO_SALVO_ANTERIOR, sessao_mod.MatchLogger) = salvos
        shutil.rmtree(pasta, ignore_errors=True)

    # 15. Crédito do ponder
    print("\n[TESTE 15] Crédito do ponder: o tempo pensado no turno do adversário conta como já gasto...")
    pos = chess.Board("r1bq1rk1/pp2bppp/2n1pn2/3p4/2PP4/2N1PN2/PP3PPP/R2QKB1R w KQ - 0 9")

    def tm_partida() -> TimeManager:
        t = TimeManager(initial_minutes=15, increment_seconds=10, move_overhead=0.5, operator_overhead=4.0)
        t.remaining_time = 600.0
        return t

    tm_normal = tm_partida()
    alvo, duro = tm_normal.allocate_time(pos, 35)
    tm_c = tm_partida()
    tm_c.dar_credito_ponder(alvo)  # o ponder pensou o alvo inteiro nesta posição
    _, duro_c = tm_c.allocate_time(pos, 35)
    assert duro_c < duro, "Com crédito o limite rígido tem de cair"
    assert duro_c >= 0.15 * alvo - 1e-9, "O piso de confirmação (15% do alvo) não pode sumir"
    tm_c.start_time -= 0.10 * alvo
    assert not tm_c.should_abort_search(), "Parou antes do piso de confirmação"
    tm_c.start_time -= 0.06 * alvo
    assert tm_c.should_abort_search() and tm_c.should_stop_iteration(), "Com crédito grande, para logo depois do piso"
    _, duro_depois = tm_c.allocate_time(pos, 35)
    assert abs(duro_depois - duro) < 1e-6, "O crédito vale um lance só"
    tm_p = tm_partida()
    tm_p.dar_credito_ponder(1.0)  # ponder curto: quase não muda o lance
    tm_p.allocate_time(pos, 35)
    tm_p.start_time -= 0.5 * alvo - 1.5
    assert not tm_p.should_abort_search(), "Crédito de 1 s não pode cortar o lance pela metade"
    tm_f = tm_partida()
    tm_f.set_fixed_time(2.0)
    tm_f.dar_credito_ponder(100.0)
    assert tm_f.allocate_time(pos, 35)[1] > 1.4, "Tempo fixo (UCI movetime) ignora o crédito"
    print(f"  -> [OK] 15+10, 600 s: alvo {alvo:.1f}s, limite {duro:.1f}s -> com crédito {duro_c:.1f}s; "
          "piso de 15%, vale uma vez, crédito pequeno não corta o lance")
    # PonderController: crédito só quando o adversário joga o lance previsto
    from main import PonderController
    from motor_stockfish import encontrar_stockfish as _achar_sf

    if _achar_sf():
        from motor_stockfish import criar_motor as _criar

        motor_p = _criar("stockfish")
        espera = 0.4
    else:
        motor_p = SearchEngine()
        espera = 1.5
    b0 = chess.Board()
    b0.push_uci("e2e4")
    ctrl = PonderController(motor_p)
    ctrl.start_ponder(b0, predicted_uci="e7e5")
    time.sleep(espera)
    b_hit = b0.copy()
    b_hit.push_uci("e7e5")
    credito = ctrl.credito_para(b_hit)
    assert credito >= espera * 0.8, f"Acertou o lance previsto e o crédito foi {credito:.2f}s"
    assert ctrl.credito_para(b_hit) == 0.0, "O crédito vale uma vez só"
    ctrl.start_ponder(b0, predicted_uci="e7e5")
    time.sleep(espera)
    b_erro = b0.copy()
    b_erro.push_uci("c7c5")
    assert ctrl.credito_para(b_erro) == 0.0, "O adversário jogou outro lance: sem crédito"
    print(f"  -> [OK] Ponder: crédito de {credito:.2f}s quando o adversário jogou o previsto; 0 quando jogou outro")

    # 16. Empate morto
    print("\n[TESTE 16] Empate morto: nota 0 estável com profundidade alta -> 30% do tempo...")
    tm_e = tm_partida()
    alvo_normal = tm_e.allocate_time(pos, 35)[0]
    tm_e.registrar_busca(0, 25, "SEARCH")
    assert tm_e.allocate_time(pos, 35)[0] == alvo_normal and not tm_e.economia_empate_morto, "Um 0 só não basta"
    tm_e.registrar_busca(0, 30, "SEARCH")
    alvo_morto = tm_e.allocate_time(pos, 35)[0]
    assert abs(alvo_morto - 0.3 * alvo_normal) < 1e-6 and tm_e.economia_empate_morto, "Empate morto deveria usar 30%"
    tm_e.registrar_busca(12, 30, "SEARCH")
    assert tm_e.allocate_time(pos, 35)[0] == alvo_normal, "Nota diferente de 0: volta ao tempo normal"
    tm_e.registrar_busca(0, 30, "SEARCH")
    tm_e.registrar_busca(0, 9, "SEARCH")
    assert tm_e.allocate_time(pos, 35)[0] == alvo_normal, "Profundidade baixa não é empate morto"
    tm_e.registrar_busca(0, 30, "SEARCH")
    tm_e.registrar_busca(0, 30, "BOOK")
    assert tm_e.allocate_time(pos, 35)[0] == alvo_normal, "Lance de livro não conta"
    tm_e.registrar_busca(0, 30, "SEARCH")
    tm_e.registrar_busca(0, 30, "SEARCH")
    tm_e.limpar_historico()
    assert tm_e.allocate_time(pos, 35)[0] == alvo_normal, "Depois de desfazer, o histórico recomeça"
    print(f"  -> [OK] alvo {alvo_normal:.1f}s -> {alvo_morto:.1f}s em empate morto; nota, profundidade, livro "
          "e desfazer voltam ao normal")

    # 17. Regra do empate imediato (Stockfish)
    print("\n[TESTE 17] Regra do empate imediato do Stockfish (Armageddon e contempt)...")
    from motor_stockfish import lances_que_empatam, permite_empate

    rep = chess.Board()
    for u in "g1f3 g8f6 f3g1 f6g8 g1f3 g8f6 f3g1".split():
        rep.push_uci(u)
    assert [m.uci() for m in lances_que_empatam(rep, list(rep.legal_moves))] == ["f6g8"]
    rep_b = rep.copy()
    rep_b.pop()  # Brancas a jogar: f3g1 deixa as Pretas repetirem
    assert permite_empate(rep_b, chess.Move.from_uci("f3g1"))
    assert not permite_empate(rep_b, chess.Move.from_uci("e2e4"))
    mat = chess.Board("4k3/8/8/8/1n6/8/P7/4K3 b - - 0 40")
    assert chess.Move.from_uci("b4a2") in lances_que_empatam(mat, list(mat.legal_moves)), "Nxa2 deixa material insuficiente"
    if not _achar_sf():
        print("  -> [AVISO] Stockfish não encontrado: só as funções da regra foram testadas.")
    else:
        sf_r = _criar("stockfish")
        try:
            sf_r.our_color = chess.BLACK
            sf_r.regra_empate = "buscar"
            r = sf_r.search_best_move(rep.copy(), fixed_tm(0.5), use_book=False, verbose=False)
            assert r.best_move.uci() == "f6g8" and r.source == "REGRA_EMPATE", f"Pretas deviam empatar: {r.best_move}"
            sf_r.our_color = chess.WHITE
            sf_r.regra_empate = "evitar"
            r = sf_r.search_best_move(rep_b.copy(), fixed_tm(0.5), use_book=False, verbose=False)
            assert r.best_move.uci() != "f3g1" and r.best_move in rep_b.legal_moves, "Brancas deixaram repetir"
            sf_r.regra_empate = None
            sf_r.our_color = chess.BLACK
            sf_r.contempt = 300  # evita o empate se a alternativa não for pior que -3 peões
            r = sf_r.search_best_move(rep.copy(), fixed_tm(0.5), use_book=False, verbose=False)
            assert r.best_move.uci() != "f6g8", "Contempt positivo: devia fugir da repetição"
            sf_r.contempt = -300  # procura o empate: só joga outro lance se ganhar mais de 3 peões
            r = sf_r.search_best_move(rep.copy(), fixed_tm(0.5), use_book=False, verbose=False)
            assert r.best_move.uci() == "f6g8" and r.source == "REGRA_EMPATE", "Contempt negativo: devia repetir"
            print("  -> [OK] Pretas no Armageddon repetem (f6g8); Brancas não deixam repetir; "
                  "contempt +300 foge e -300 aceita a repetição")
        finally:
            sf_r.contempt, sf_r.regra_empate, sf_r.our_color = 0, None, None

    # 18. Armageddon com incremento, aviso de bandeira e de energia
    print("\n[TESTE 18] Armageddon com incremento, aviso de bandeira e aviso de energia...")
    cfg_a = sessao_mod.Config(our_color=chess.BLACK, ritmo="armageddon", inc_armageddon=2.0, motor="proprio")
    assert cfg_a.relogio() == (4.0, 2.0) and cfg_a.descricao_ritmo() == "Armageddon 4 min + 2"
    assert sessao_mod.Config(our_color=chess.WHITE, ritmo="armageddon").relogio() == (5.0, 0.0)
    pasta = tempfile.mkdtemp(prefix="teste_sessao_")
    salvos = (sessao_mod.LOGS_DIR, sessao_mod.ARQUIVO_SALVO, sessao_mod.ARQUIVO_SALVO_ANTERIOR, sessao_mod.MatchLogger,
              sessao_mod.avisos_de_energia)
    sessao_mod.LOGS_DIR = os.path.join(pasta, "logs")
    sessao_mod.ARQUIVO_SALVO = os.path.join(pasta, "logs", "partida_em_andamento.json")
    sessao_mod.ARQUIVO_SALVO_ANTERIOR = os.path.join(pasta, "logs", "partida_salva_anterior.json")
    sessao_mod.MatchLogger = functools.partial(MatchLogger, base_dir=pasta)
    try:
        if _achar_sf():
            s_arm = sessao_mod.GameSession(sessao_mod.Config(our_color=chess.WHITE, ritmo="armageddon",
                                                             motor="stockfish", ponder=False))
            assert s_arm.engine.regra_empate == "evitar", "Brancas no Armageddon: regra 'evitar'"
            s_norm = sessao_mod.GameSession(sessao_mod.Config(our_color=chess.WHITE, motor="stockfish", ponder=False))
            assert s_norm.engine.regra_empate is None, "A regra do Armageddon não pode sobrar na partida seguinte"
        s = sessao_mod.GameSession(sessao_mod.Config(our_color=chess.BLACK, ritmo="armageddon", motor="proprio",
                                                     operador=4.0, ponder=False))
        assert s.engine.contempt == -100, "Motor próprio com Pretas no Armageddon: contempt -100"
        rng = random.Random(18)
        for _ in range(4):
            quietos = [m for m in s.board.legal_moves if not s.board.is_capture(m)] or list(s.board.legal_moves)
            s.registrar_adversario(rng.choice(quietos))
            quietos = [m for m in s.board.legal_moves if not s.board.is_capture(m)] or list(s.board.legal_moves)
            m = rng.choice(quietos)
            s.registrar_nosso(SearchResult(m, 0, 10, 1000, 0.5, [m.uci()], "SEARCH", 10, [m]))
        sessao_mod.avisos_de_energia = lambda: []
        s._energia_em = -1e9
        s.alertas()
        s._energia_thread.join(timeout=5)
        s.time_manager.sync_clocks(200.0, 200.0)  # direto no TM: sem recalibrar o operador
        assert not any("Bandeira" in a for a in s.alertas()), "Com relógio folgado não há aviso de bandeira"
        s.time_manager.sync_clocks(60.0, 200.0)
        bandeira = [a for a in s.alertas() if "Bandeira" in a]
        assert bandeira and "acelere" in bandeira[0], f"Sem incremento e 60 s, devia avisar: {s.alertas()}"
        sessao_mod.avisos_de_energia = lambda: ["O notebook está na bateria: ligue na tomada."]
        s._energia_em = -1e9
        s.alertas()  # a conferência roda numa thread (o perfil do sistema leva até 0,5 s): não trava a tela
        s._energia_thread.join(timeout=5)
        assert any("bateria" in a for a in s.alertas()), "O aviso de energia devia aparecer durante a partida"
        if sys.platform.startswith("linux"):
            import motor_stockfish as ms

            perfil_salvo = ms._perfil_do_sistema
            ms._perfil_do_sistema = lambda: "powersave"  # tuned em economia, mesmo na tomada (caso de 04/11)
            try:
                epp = ""
                try:
                    epp = open("/sys/devices/system/cpu/cpu0/cpufreq/energy_performance_preference").read().strip()
                except OSError:
                    pass
                if epp not in ("low-power", "quiet", "power", "balance_power"):
                    assert any("perfil de energia do sistema" in a for a in ms.avisos_de_energia()), \
                        "O perfil powersave do tuned devia gerar aviso"
            finally:
                ms._perfil_do_sistema = perfil_salvo
        print(f"  -> [OK] Armageddon 4 min + 2 s; regra do Stockfish liga e desliga; \"{bandeira[0][:60]}...\"; "
              "aviso de bateria durante a partida")
    finally:
        (sessao_mod.LOGS_DIR, sessao_mod.ARQUIVO_SALVO, sessao_mod.ARQUIVO_SALVO_ANTERIOR, sessao_mod.MatchLogger,
         sessao_mod.avisos_de_energia) = salvos
        shutil.rmtree(pasta, ignore_errors=True)

    # 19. Recuperação da partida: o notebook travou ou desligou no meio do jogo
    print("\n[TESTE 19] Recuperação da partida pelo arquivo salvo e pelo log da banca (travou, desligou)...")
    import contextlib
    import io
    import json

    import recuperacao as rec

    pasta = tempfile.mkdtemp(prefix="teste_recuperacao_")
    salvos = (sessao_mod.LOGS_DIR, sessao_mod.ARQUIVO_SALVO, sessao_mod.ARQUIVO_SALVO_ANTERIOR, sessao_mod.MatchLogger)
    sessao_mod.LOGS_DIR = logs = os.path.join(pasta, "logs")  # não mexe na partida salva de verdade (logs/)
    sessao_mod.ARQUIVO_SALVO = arq = os.path.join(logs, "partida_em_andamento.json")
    sessao_mod.ARQUIVO_SALVO_ANTERIOR = os.path.join(logs, "partida_salva_anterior.json")
    sessao_mod.MatchLogger = functools.partial(MatchLogger, base_dir=pasta)
    try:
        rng = random.Random(19)

        def vez(s, fonte="SEARCH"):
            """Um meio-lance de quem tiver a vez (sem capturas, para a partida não acabar cedo)."""
            quietos = [m for m in s.board.legal_moves if not s.board.is_capture(m)] or list(s.board.legal_moves)
            m = rng.choice(quietos)
            if s.nossa_vez:
                s.registrar_nosso(SearchResult(m, rng.randint(-50, 50), 12, 5000, 0.0, [m.uci()], fonte, 14, [m]))
            else:
                s.registrar_adversario(m)

        def recuperada():
            d = sessao_mod.ler_partida_salva()
            assert d is not None, "Devia haver partida para retomar"
            return d

        def lances_de(s):
            return [m.uci() for m in s.board.move_stack]

        s = sessao_mod.GameSession(sessao_mod.Config(our_color=chess.BLACK, ritmo="personalizado", minutos=10,
                                                     incremento=5, motor="proprio", operador=3.0, ponder=False))
        for _ in range(12):
            vez(s)
        s.sincronizar(s.relogios()[0] - 6.0, 590.0)  # operador 1 s mais lento em 6 lances: recalibra 3 -> 4 s
        assert abs(s.time_manager.operator_overhead - 4.0) < 1e-9
        for _ in range(4):
            vez(s)
        s.ajustar(operador=3.5, contempt=20)
        s.alternar_economia()
        vez(s)
        s.registrar_manual(rng.choice(list(s.board.legal_moves)))
        vez(s)
        vez(s, "BOOK")
        s.desfazer(2)
        for _ in range(3):
            vez(s)
        nosso, adv = s.time_manager.remaining_time, s.time_manager.opponent_time

        # a) Arquivo salvo inteiro: bate com o log e vale ele
        d = recuperada()
        assert d["fonte"] == "salvo" and d["lances"] == lances_de(s) and not d["avisos"], d["avisos"]
        # b) Só pelo log: a mesma partida, a mesma súmula e os mesmos ajustes
        so_log = rec.remontar(rec.ler_log(s.logger.log_path))
        assert (so_log["fen_inicial"], so_log["lances"]) == (s.fen_inicial, lances_de(s))
        assert so_log["config"] == d["config"], (so_log["config"], d["config"])
        assert abs(so_log["relogio_nosso"] - nosso) < 0.5 and abs(so_log["relogio_adv"] - adv) < 0.5
        assert (so_log["operador"], so_log["operador_inicial"], so_log["contempt"]) == (3.5, 3.5, 20)
        assert (so_log["fator"], so_log["fator_antes_economia"], so_log["ponder"]) == (0.5, 1.0, False)
        campos = ("uci", "san", "descricao", "cor", "nosso", "origem", "profundidade", "avaliacao")
        assert [[h[c] for c in campos] for h in so_log["historico"]] == [[h[c] for c in campos] for h in d["historico"]]
        assert any(h["origem"] == "MANUAL" for h in so_log["historico"])
        # c) Arquivo salvo corrompido (a energia caiu no meio da gravação): remonta pelo log
        with open(arq, "w", encoding="utf-8") as f:
            f.write('{"versao": 1, "config": {"our_')
        d = recuperada()
        assert d["fonte"] == "log" and d["lances"] == lances_de(s) and "corrompido" in d["avisos"][0], d["avisos"]
        # d) Arquivo salvo apagado
        os.remove(arq)
        d = recuperada()
        assert d["fonte"] == "log" and d["lances"] == lances_de(s) and not d["avisos"], d["avisos"]
        # e) Arquivo salvo atrasado (a energia caiu entre a gravação do log e a dele): vale o log, gravado antes
        s.salvar()
        with open(arq, encoding="utf-8") as f:
            atrasado = json.load(f)
        vez(s)
        vez(s)
        rec.gravar_json(arq, atrasado)
        d = recuperada()
        assert d["fonte"] == "log" and d["lances"] == lances_de(s) and "atrasado" in d["avisos"][0], d["avisos"]
        # f) Última linha do log cortada no meio: não vale (o lance talvez nem tenha aparecido na tela)
        with open(s.logger.log_path, "a", encoding="utf-8") as f:
            f.write("99   | BRANCAS | e2e4        | 30     | 1000           | +10 cp (+0.10)   |  1.000s | e2")
        d = recuperada()
        assert d["lances"] == lances_de(s) and any("pela metade" in a for a in d["avisos"]), d["avisos"]
        # g) Retomar pelo log e seguir: o log continua no mesmo arquivo e uma segunda queda também se recupera
        s.parar()
        s2 = sessao_mod.GameSession.retomar(d)
        assert lances_de(s2) == lances_de(s) and s2.logger.log_path == s.logger.log_path and len(s2.lances) == len(d["lances"])
        assert (s2.time_manager.operator_overhead, s2.engine.contempt, s2.fator_antes_economia) == (3.5, 20, 1.0)
        assert s2.aviso and "pela metade" in s2.aviso
        vez(s2)
        vez(s2)
        os.remove(arq)
        d = recuperada()
        assert d["lances"] == lances_de(s2) and not d["avisos"], d["avisos"]
        n_lances = len(d["lances"])
        # h) Partida terminada não é oferecida; desfazer depois do fim volta a oferecer
        s3 = sessao_mod.GameSession(sessao_mod.Config(our_color=chess.WHITE, motor="proprio", ponder=False))
        for uci in ("f2f3", "e7e5", "g2g4", "d8h4"):  # mate do louco
            m = chess.Move.from_uci(uci)
            s3.registrar_manual(m) if s3.nossa_vez else s3.registrar_adversario(m)
        assert s3.encerrada and sessao_mod.ler_partida_salva() is None
        os.remove(arq)
        assert sessao_mod.ler_partida_salva() is None, "Pelo log também: foi xeque-mate"
        s3.desfazer(1)
        assert recuperada()["lances"] == ["f2f3", "e7e5", "g2g4"]
        s3.declarar_resultado("derrota")
        os.remove(arq)
        assert sessao_mod.ler_partida_salva() is None, "Resultado declarado pelo árbitro: não há o que retomar"
        # i) Log do modo texto (main.py): lance forçado, desfazer depois do fim, relógio do adversário
        cabecalho = "Data/Hora de Início : 06/11/2026 10:00:00\nCor da Equipe       : {}\n"
        log_cli = os.path.join(logs, "partida_20991231_235958.log")
        with open(log_cli, "w", encoding="utf-8") as f:
            f.write(cabecalho.format("PRETAS") + "\n".join([
                "# [10:00:00] EVENTO: Motor: Motor próprio da equipe (avaliação, busca e gestão de tempo)",
                "# [10:00:00] EVENTO: " + rec.evento_configuracao(sessao_mod.Config(motor="proprio")),
                "1    | BRANCAS | e2e4        | -      | -              | (Adversário)     |  5.000s | Lance adversário anunciado: e2e4",
                "1    | PRETAS  | e7e5        | 12     | 1000           | +10 cp (+0.10)   |  2.000s | e7e5 g1f3",
                "# [10:00:10] EVENTO: Lance forçado manualmente: g1f3",
                "# [10:00:11] EVENTO: UNDO executado. Lances revertidos: g1f3",
                "2    | BRANCAS | f1c4        | -      | -              | (Adversário)     |  3.000s | Lance adversário anunciado: f1c4",
                "2    | PRETAS  | b8c6        | 1      | 1              | +MATE em 3 (+29995) |  1.500s | [BOOK] b8c6",
                "# [10:00:20] EVENTO: Relógio sincronizado para 880.0s (adversário 870.5s)",
                "# [10:00:20] EVENTO: Pondering desativado",
                "3    | BRANCAS | d1h5        | -      | -              | (Adversário)     |  4.000s | Lance adversário anunciado: d1h5",
                "3    | PRETAS  | g8f6        | 9      | 800            | -250 cp (-2.50)  |  1.000s | g8f6 h5f7",
                "4    | BRANCAS | h5f7        | -      | -              | (Adversário)     |  2.000s | Lance adversário anunciado: h5f7",
                "# [10:00:30] EVENTO: Fim da partida: 1-0 (CHECKMATE)",
                "# [10:00:31] EVENTO: Comando UNDO executado após fim de jogo.",
            ]) + "\n")
        d = recuperada()
        assert d["fonte"] == "log" and d["config"]["our_color"] == "b" and not d["encerrada"]
        assert d["lances"] == ["e2e4", "e7e5", "f1c4", "b8c6", "d1h5", "g8f6"], d["lances"]
        assert d["ponder"] is False and (d["historico"][3]["origem"], d["historico"][3]["avaliacao"]) == ("BOOK", 29995)
        assert (round(d["relogio_nosso"], 3), round(d["relogio_adv"], 3)) == (885.0, 884.5), (d["relogio_nosso"], d["relogio_adv"])
        # j) Log do modo UCI não é partida de torneio (o lance do adversário não vai para ele)
        with open(os.path.join(logs, "partida_20991231_235959.log"), "w", encoding="utf-8") as f:
            f.write(cabecalho.format("UCI_MODE") + "1    | BRANCAS | d2d4        | 20     | 5000           | +20 cp (+0.20)   |  1.000s | d2d4\n")
        assert recuperada()["log_path"] == log_cli
        # k) Linha de comando: remonta um log copiado de outro notebook e deixa pronto para as interfaces
        fora = os.path.join(pasta, "pendrive")
        os.makedirs(fora)
        shutil.copyfile(s2.logger.log_path, os.path.join(fora, "partida_copiada.log"))
        saida = io.StringIO()
        with contextlib.redirect_stdout(saida):
            assert rec.main(["--log", os.path.join(fora, "partida_copiada.log"), "--pgn"]) == 0
        assert "Partida interrompida encontrada" in saida.getvalue() and '[Result "*"]' in saida.getvalue()
        estado = rec.remontar(rec.ler_log(os.path.join(fora, "partida_copiada.log")))
        rec.restaurar(estado, arq, logs)
        d = recuperada()
        assert d["fonte"] == "salvo" and len(d["lances"]) == n_lances and d["log_path"] == os.path.join(logs, "partida_copiada.log")
        s4 = sessao_mod.GameSession.retomar(d)
        assert len(s4.board.move_stack) == n_lances and s4.engine.contempt == 20
        print(f"  -> [OK] Arquivo salvo corrompido, apagado ou atrasado: partida remontada pelo log ({n_lances} "
              "meios-lances, ajustes e relógio); linha cortada ignorada; terminada não é oferecida; log do modo "
              "texto e cópia de outro notebook retomados")
    finally:
        (sessao_mod.LOGS_DIR, sessao_mod.ARQUIVO_SALVO, sessao_mod.ARQUIVO_SALVO_ANTERIOR, sessao_mod.MatchLogger) = salvos
        shutil.rmtree(pasta, ignore_errors=True)

    print("\n======================================================================")
    print(f"   TODOS OS TESTES PASSARAM ({time.time() - t_start:.0f}s). MOTOR PRONTO PARA O TORNEIO!")
    print("======================================================================")


if __name__ == "__main__":
    run_all_tests()
