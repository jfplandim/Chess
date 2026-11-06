"""
Ferramenta de Teste: partidas simuladas pelo MESMO caminho das interfaces de torneio
Chess Challenge III - ICEV 2026

O nosso lado joga pela session.GameSession (a mesma da TUI e do navegador): livro, Stockfish com a nossa
gestão de tempo, crédito do ponder, empate morto, regra do empate imediato, log da banca e detecção de fim.
O relógio físico é simulado: cada lance desconta o pensamento mais o custo REAL do operador (que pode ser
maior que o configurado), e quem passa de zero perde (empate se o outro lado não tem material para dar mate).
A cada --f3 lances nossos o "operador" sincroniza o relógio, como o F3 da interface.

O tempo do operador não é esperado de verdade (só descontado do relógio), então uma partida dura só o
tempo de pensamento dos dois motores. Os logs ficam numa pasta temporária, nunca em logs/.

Exemplos:

    # Espelho: nosso Stockfish x Stockfish com a gestão de tempo dele, ritmo reduzido 90 s + 1 s
    python tools/simular_torneio.py --jogos 6 --base 90 --inc 1 --operador 0.4 --operador-real 0.45

    # Contra um adversário mais fraco (Stockfish limitado a Elo 1800), da posição inicial
    python tools/simular_torneio.py --jogos 4 --adversario sf --elo 1800 --posicao-inicial

    # Armageddon no ritmo real (5 e 4 minutos, sem incremento, operador de 4,5 s)
    python tools/simular_torneio.py --jogos 8 --armageddon --operador 4 --operador-real 4.5 --margem 0.5 \
        --overhead-adversario 4000

Ao final mostra o placar, os motivos de fim, a diferença de relógio (o desempate do FAQ Item 12), o tempo
médio por lance de cada lado, o menor relógio e quantas exceções aconteceram (tem que ser zero).
"""
from __future__ import annotations

import argparse
import functools
import os
import random
import statistics
import sys
import tempfile
import threading
import time
import traceback

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

import chess  # noqa: E402
import chess.engine  # noqa: E402

import session as S  # noqa: E402
from logger import MatchLogger  # noqa: E402
from motor_stockfish import encontrar_stockfish  # noqa: E402


def desviar_logs(pasta: str) -> None:
    """A sessão grava log e partida salva aqui, e não em logs/ (que é da banca)."""
    os.makedirs(os.path.join(pasta, "logs"), exist_ok=True)
    S.LOGS_DIR = os.path.join(pasta, "logs")
    S.ARQUIVO_SALVO = os.path.join(pasta, "logs", "partida_em_andamento.json")
    S.ARQUIVO_SALVO_ANTERIOR = os.path.join(pasta, "logs", "partida_salva_anterior.json")
    S.MatchLogger = functools.partial(MatchLogger, base_dir=pasta)


class Adversario:
    """Stockfish com a gestão de tempo dele (opcionalmente limitado em Elo) ou o nosso motor próprio por UCI."""

    def __init__(self, tipo: str, threads: int, hash_mb: int, overhead_ms: int, elo: int | None):
        if tipo == "sf":
            caminho = encontrar_stockfish()
            if not caminho:
                raise SystemExit("Stockfish não encontrado: python tools/baixar_stockfish.py")
            self.eng = chess.engine.SimpleEngine.popen_uci(caminho, timeout=60)
            opcoes = {"Threads": threads, "Hash": hash_mb, "Move Overhead": min(5000, overhead_ms)}
            if elo:
                opcoes.update({"UCI_LimitStrength": True, "UCI_Elo": elo})
            self.eng.configure(opcoes)
        elif tipo == "proprio":
            self.eng = chess.engine.SimpleEngine.popen_uci([sys.executable, os.path.join(BASE_DIR, "uci.py")],
                                                           timeout=60, cwd=BASE_DIR)
            self.eng.configure({"Motor": "Proprio", "Move Overhead": overhead_ms, "Log": False})
        else:
            raise SystemExit(f"Adversário desconhecido: {tipo}")
        self.jogo = object()

    def novo_jogo(self) -> None:
        self.jogo = object()  # o python-chess manda "ucinewgame"

    def jogar(self, board: chess.Board, relogios: dict, inc: float):
        limite = chess.engine.Limit(white_clock=max(0.01, relogios[chess.WHITE]),
                                    black_clock=max(0.01, relogios[chess.BLACK]), white_inc=inc, black_inc=inc)
        t0 = time.perf_counter()
        resultado = self.eng.play(board, limite, game=self.jogo, info=chess.engine.INFO_SCORE)
        return resultado.move, time.perf_counter() - t0

    def fechar(self) -> None:
        try:
            self.eng.quit()
        except Exception:  # noqa: BLE001
            pass


def jogar_partida(numero: int, fen: str, nossa_cor: bool, adv: Adversario, a) -> dict:
    if a.armageddon:
        minutos = {chess.WHITE: 5.0, chess.BLACK: 4.0}
        inc = a.inc_armageddon
        config = S.Config(our_color=nossa_cor, ritmo="armageddon", inc_armageddon=inc, ponder=a.ponder,
                          operador=a.operador, motor="stockfish", desempenho=a.desempenho)
    else:
        minutos = {chess.WHITE: a.base / 60.0, chess.BLACK: a.base / 60.0}
        inc = a.inc
        config = S.Config(our_color=nossa_cor, ritmo="personalizado", minutos=a.base / 60.0, incremento=inc,
                          ponder=a.ponder, operador=a.operador, motor="stockfish", desempenho=a.desempenho)
    sessao = S.GameSession(config)
    sessao.time_manager.move_overhead = a.margem
    if fen != chess.STARTING_FEN:
        sessao.definir_posicao(chess.Board(fen))
    adv.novo_jogo()
    relogio = {cor: m * 60.0 for cor, m in minutos.items()}
    menor = dict(relogio)
    pensa_nosso: list[float] = []
    pensa_adv: list[float] = []
    excecoes: list[str] = []
    resultado = motivo = None
    lances_nossos = 0
    digitar = a.operador_real * 0.3  # parte do operador gasta antes de registrar o lance do adversário
    while not sessao.encerrada:
        board = sessao.board
        if board.ply() >= a.max_meios_lances:
            resultado, motivo = 0.5, "limite de meios-lances"
            break
        if board.turn == nossa_cor:
            t0 = time.perf_counter()
            try:
                busca = sessao.pensar(board.copy(), threading.Event())
            except Exception as erro:  # noqa: BLE001 - conta como falha e segue, como a interface
                excecoes.append(f"{type(erro).__name__}: {erro}")
                busca = sessao.lance_emergencia(erro)
            pensou = time.perf_counter() - t0
            sessao.registrar_nosso(busca)
            lances_nossos += 1
            pensa_nosso.append(pensou)
            relogio[nossa_cor] -= pensou + (a.operador_real - digitar)  # anunciar, mover e apertar o relógio
            menor[nossa_cor] = min(menor[nossa_cor], relogio[nossa_cor])
            if relogio[nossa_cor] < 0:
                mate_possivel = not sessao.board.has_insufficient_material(not nossa_cor)
                resultado, motivo = (0.0 if mate_possivel else 0.5), "nosso tempo acabou"
                break
            relogio[nossa_cor] += inc
            if a.f3 and lances_nossos % a.f3 == 0 and not sessao.encerrada:
                sessao.sincronizar(relogio[nossa_cor], relogio[not nossa_cor])
        else:
            try:
                lance, pensou = adv.jogar(board, relogio, inc)
            except Exception as erro:  # noqa: BLE001
                excecoes.append(f"adversário: {erro}")
                resultado, motivo = 1.0, "adversário falhou"
                break
            cor = board.turn
            pensa_adv.append(pensou)
            relogio[cor] -= pensou + a.operador_real_adversario
            menor[cor] = min(menor[cor], relogio[cor])
            if relogio[cor] < 0:
                mate_possivel = not sessao.board.has_insufficient_material(nossa_cor)
                resultado, motivo = (1.0 if mate_possivel else 0.5), "tempo do adversário acabou"
                break
            relogio[cor] += inc
            # o tempo de digitar o lance (com o nosso relógio já correndo) fica fora do relógio interno
            relogio[nossa_cor] -= digitar
            sessao.registrar_adversario(lance)
    if resultado is None and sessao.resultado:
        placar, motivo = sessao.resultado
        if placar == "1/2-1/2":
            resultado = 0.5
        else:
            resultado = 1.0 if (placar == "1-0") == (nossa_cor == chess.WHITE) else 0.0
    sessao.parar()
    return {
        "numero": numero, "cor": "Brancas" if nossa_cor == chess.WHITE else "Pretas", "resultado": resultado,
        "motivo": motivo, "lances": sessao.board.ply(), "relogio_nosso": relogio[nossa_cor],
        "relogio_adv": relogio[not nossa_cor], "menor_nosso": menor[nossa_cor], "menor_adv": menor[not nossa_cor],
        "pensa_nosso": statistics.mean(pensa_nosso) if pensa_nosso else 0.0,
        "pensa_adv": statistics.mean(pensa_adv) if pensa_adv else 0.0, "excecoes": excecoes,
        "log": sessao.logger.log_path,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="Partidas simuladas pela sessão do torneio")
    ap.add_argument("--jogos", type=int, default=6, help="número de partidas (cada abertura com as duas cores)")
    ap.add_argument("--base", type=float, default=90.0, help="segundos iniciais (15+10 real: 900)")
    ap.add_argument("--inc", type=float, default=1.0, help="incremento em segundos (15+10 real: 10)")
    ap.add_argument("--armageddon", action="store_true", help="5 min Brancas, 4 min Pretas")
    ap.add_argument("--inc-armageddon", type=float, default=0.0)
    ap.add_argument("--operador", type=float, default=0.4, help="custo do operador CONFIGURADO (s por lance)")
    ap.add_argument("--operador-real", type=float, default=0.45, help="custo REAL do nosso operador")
    ap.add_argument("--operador-real-adversario", type=float, default=0.45)
    ap.add_argument("--margem", type=float, default=0.05, help="margem do nosso relógio (0,5 s no torneio)")
    ap.add_argument("--f3", type=int, default=10, help="sincroniza o relógio a cada N lances nossos (0 = nunca)")
    ap.add_argument("--ponder", action="store_true", help="liga o pondering do nosso lado")
    ap.add_argument("--desempenho", default="ultra", choices=("ultra", "medio", "baixo"))
    ap.add_argument("--adversario", default="sf", choices=("sf", "proprio"))
    ap.add_argument("--elo", type=int, default=0, help="limita o Stockfish adversário a este Elo")
    ap.add_argument("--threads-adversario", type=int, default=1)
    ap.add_argument("--hash-adversario", type=int, default=64)
    ap.add_argument("--overhead-adversario", type=int, default=400, help="Move Overhead do adversário (ms)")
    ap.add_argument("--posicao-inicial", action="store_true", help="todas da posição inicial (com o nosso livro)")
    ap.add_argument("--aberturas", default=os.path.join(BASE_DIR, "tools", "aberturas_teste.epd"))
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--max-meios-lances", type=int, default=400)
    a = ap.parse_args()

    desviar_logs(tempfile.mkdtemp(prefix="simular_torneio_"))
    if a.posicao_inicial:
        fens = [chess.STARTING_FEN]
    else:
        with open(a.aberturas) as arquivo:
            fens = [linha.strip() for linha in arquivo if linha.strip()]
        random.Random(a.seed).shuffle(fens)
    adv = Adversario(a.adversario, a.threads_adversario, a.hash_adversario, a.overhead_adversario, a.elo or None)
    partidas = []
    inicio = time.time()
    try:
        for i in range(a.jogos):
            fen = fens[(i // 2) % len(fens)]
            nossa_cor = chess.WHITE if i % 2 == 0 else chess.BLACK
            try:
                p = jogar_partida(i + 1, fen, nossa_cor, adv, a)
            except Exception:  # noqa: BLE001
                print(traceback.format_exc())
                adv.fechar()
                adv = Adversario(a.adversario, a.threads_adversario, a.hash_adversario, a.overhead_adversario,
                                 a.elo or None)
                continue
            partidas.append(p)
            print(f"[{p['numero']:>2}] {p['cor']:<7} {p['resultado']:>3} {p['motivo']:<26} {p['lances']:>3} meios-lances | "
                  f"relógio {p['relogio_nosso']:6.1f}s x {p['relogio_adv']:6.1f}s | por lance {p['pensa_nosso']:.2f}s x "
                  f"{p['pensa_adv']:.2f}s | exceções {len(p['excecoes'])} ({(time.time() - inicio) / 60:.1f} min)",
                  flush=True)
    finally:
        adv.fechar()
    if not partidas:
        return
    v = sum(p["resultado"] == 1.0 for p in partidas)
    e = sum(p["resultado"] == 0.5 for p in partidas)
    d = sum(p["resultado"] == 0.0 for p in partidas)
    motivos: dict[str, int] = {}
    for p in partidas:
        motivos[p["motivo"]] = motivos.get(p["motivo"], 0) + 1
    print("=" * 78)
    print(f"NÓS: +{v} ={e} -{d}   motivos: {motivos}")
    print(f"Relógio no fim (nosso - adversário): média {statistics.mean(p['relogio_nosso'] - p['relogio_adv'] for p in partidas):+.1f}s")
    print(f"Tempo médio por lance: nosso {statistics.mean(p['pensa_nosso'] for p in partidas):.2f}s x "
          f"adversário {statistics.mean(p['pensa_adv'] for p in partidas):.2f}s")
    print(f"Menor relógio: nosso {min(p['menor_nosso'] for p in partidas):.1f}s x adversário "
          f"{min(p['menor_adv'] for p in partidas):.1f}s")
    print(f"Exceções: {sum(len(p['excecoes']) for p in partidas)} (tem que ser zero)")
    sys.stdout.flush()
    os._exit(0)  # sai na hora; o Stockfish compartilhado fecha sozinho quando a entrada dele acaba


if __name__ == "__main__":
    main()
