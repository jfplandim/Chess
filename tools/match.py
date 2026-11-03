"""
Ferramenta de Teste: Match automático entre dois motores UCI (medição de Elo)
Chess Challenge III - ICEV 2026

Uso típico (versão nova contra versão antiga do motor):

    python tools/match.py \
        --a "python uci.py" --a-name NOVO \
        --b "python /caminho/versao_antiga/uci.py" --b-name ANTIGO \
        --games 100 --tc 20+0.2 --concurrency 3

Cada abertura de tools/aberturas_teste.epd é jogada DUAS vezes com as cores
invertidas (elimina o viés da abertura). O relógio é controlado aqui pelo
script: quem estourar o tempo perde (exatamente como no torneio presencial).

Ao final mostra Vitórias/Empates/Derrotas, diferença de Elo estimada com
intervalo de confiança de 95% e LOS (probabilidade de A ser mais forte que B).
"""

import argparse
import math
import os
import random
import shlex
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import chess
import chess.engine
import chess.pgn

BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def elo_from_score(score: float) -> float:
    score = min(max(score, 1e-6), 1 - 1e-6)
    return -400.0 * math.log10(1.0 / score - 1.0)


def elo_stats(wins: int, draws: int, losses: int) -> tuple[float, float, float]:
    """Retorna (elo, margem_95%, LOS) usando o modelo trinomial padrão."""
    n = wins + draws + losses
    if n == 0:
        return 0.0, 0.0, 0.5
    score = (wins + 0.5 * draws) / n
    var = (wins * (1 - score) ** 2 + draws * (0.5 - score) ** 2 + losses * (0 - score) ** 2) / n
    stderr = math.sqrt(var / n) if n > 1 else 0.0
    elo = elo_from_score(score)
    lo = elo_from_score(score - 1.96 * stderr)
    hi = elo_from_score(score + 1.96 * stderr)
    los = 0.5 * (1 + math.erf((wins - losses) / math.sqrt(2 * max(1, wins + losses))))
    return elo, (hi - lo) / 2, los


class EnginePool:
    """Cada thread do match mantém seu próprio par de processos de motor (reuso entre partidas)."""

    def __init__(self, cmd: str, options: dict[str, str]):
        self.cmd = cmd
        self.options = options
        self.local = threading.local()
        self.all_engines: list[chess.engine.SimpleEngine] = []
        self.lock = threading.Lock()

    def get(self) -> chess.engine.SimpleEngine:
        eng = getattr(self.local, "engine", None)
        if eng is None:
            eng = chess.engine.SimpleEngine.popen_uci(shlex.split(self.cmd), stderr=None, timeout=60)
            for k, v in self.options.items():
                try:
                    eng.configure({k: v})
                except Exception:
                    pass
            self.local.engine = eng
            with self.lock:
                self.all_engines.append(eng)
        return eng

    def reset(self) -> None:
        eng = getattr(self.local, "engine", None)
        if eng is not None:
            try:
                eng.quit()
            except Exception:
                pass
        self.local.engine = None

    def close_all(self) -> None:
        for eng in self.all_engines:
            try:
                eng.quit()
            except Exception:
                pass


def play_game(game_id, fen, a_is_white, pool_a, pool_b, base, inc, movetime, max_plies, grace, overhead=0.0):
    board = chess.Board(fen)
    clocks = {chess.WHITE: base, chess.BLACK: base}
    min_clock = {chess.WHITE: base, chess.BLACK: base}
    think = {chess.WHITE: [], chess.BLACK: []}
    engines = {
        chess.WHITE: pool_a if a_is_white else pool_b,
        chess.BLACK: pool_b if a_is_white else pool_a,
    }
    result = None
    reason = ""
    win_streak = 0
    draw_streak = 0
    game_token = object()  # força ucinewgame a cada nova partida

    while result is None:
        if board.is_game_over(claim_draw=True):
            result = board.result(claim_draw=True)
            reason = board.outcome(claim_draw=True).termination.name
            break
        if board.ply() >= max_plies:
            result, reason = "1/2-1/2", "MAX_PLIES"
            break

        side = board.turn
        pool = engines[side]
        eng = pool.get()
        if movetime:
            limit = chess.engine.Limit(time=movetime)
        else:
            limit = chess.engine.Limit(
                white_clock=clocks[chess.WHITE], black_clock=clocks[chess.BLACK],
                white_inc=inc, black_inc=inc,
            )
        t0 = time.perf_counter()
        try:
            res = eng.play(board, limit, info=chess.engine.INFO_SCORE, game=game_token)
        except Exception as exc:  # motor travou/morreu: derrota
            pool.reset()
            result = "0-1" if side == chess.WHITE else "1-0"
            reason = f"CRASH ({exc.__class__.__name__})"
            break
        elapsed = time.perf_counter() - t0

        if not movetime:
            think[side].append(elapsed)
            clocks[side] -= elapsed + overhead  # overhead: simula o operador humano
            min_clock[side] = min(min_clock[side], clocks[side])
            if clocks[side] < -grace:
                result = "0-1" if side == chess.WHITE else "1-0"
                reason = "TEMPO"
                break
            clocks[side] += inc

        move = res.move
        if move is None or move not in board.legal_moves:
            result = "0-1" if side == chess.WHITE else "1-0"
            reason = f"LANCE ILEGAL ({move})"
            break

        # Adjudicação para acelerar: vantagem enorme confirmada por vários lances, ou empate morto
        score = res.info.get("score")
        if score is not None:
            cp = score.pov(chess.WHITE).score(mate_score=100000)
            if abs(cp) >= 1000:
                win_streak = win_streak + 1 if (win_streak == 0 or (cp > 0) == (win_streak > 0)) else 1
            else:
                win_streak = 0
            draw_streak = draw_streak + 1 if (abs(cp) <= 8 and board.ply() >= 80) else 0
            if win_streak >= 8:
                result = "1-0" if cp > 0 else "0-1"
                reason = "ADJUDICADO"
            elif draw_streak >= 12:
                result, reason = "1/2-1/2", "ADJUDICADO_EMPATE"
        board.push(move)

    a_score = {"1-0": 1.0, "0-1": 0.0}.get(result, 0.5)
    if not a_is_white:
        a_score = 1.0 - a_score
    stats = {
        "min_clock_a": min_clock[chess.WHITE if a_is_white else chess.BLACK],
        "min_clock_b": min_clock[chess.BLACK if a_is_white else chess.WHITE],
        "avg_think_a": sum(think[chess.WHITE if a_is_white else chess.BLACK]) / max(1, len(think[chess.WHITE if a_is_white else chess.BLACK])),
        "avg_think_b": sum(think[chess.BLACK if a_is_white else chess.WHITE]) / max(1, len(think[chess.BLACK if a_is_white else chess.WHITE])),
    }
    return game_id, fen, a_is_white, result, reason, a_score, board, stats


def main() -> None:
    ap = argparse.ArgumentParser(description="Match UCI entre dois motores (A vs B)")
    ap.add_argument("--a", required=True, help="comando do motor A")
    ap.add_argument("--b", required=True, help="comando do motor B")
    ap.add_argument("--a-name", default="A")
    ap.add_argument("--b-name", default="B")
    ap.add_argument("--a-opt", action="append", default=[], help="opção UCI de A: Nome=Valor")
    ap.add_argument("--b-opt", action="append", default=[], help="opção UCI de B: Nome=Valor")
    ap.add_argument("--games", type=int, default=100, help="número de partidas (par)")
    ap.add_argument("--tc", default="20+0.2", help="controle de tempo base+inc em segundos")
    ap.add_argument("--movetime", type=float, default=0.0, help="tempo fixo por lance (ignora --tc)")
    ap.add_argument("--concurrency", type=int, default=2)
    ap.add_argument("--openings", default=os.path.join(BASE_DIR, "aberturas_teste.epd"))
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--pgn", default="")
    ap.add_argument("--max-plies", type=int, default=400)
    ap.add_argument("--grace", type=float, default=0.25, help="tolerância de latência do processo (s)")
    ap.add_argument("--overhead", type=float, default=0.0, help="segundos extras descontados por lance (simula operador)")
    args = ap.parse_args()

    base_s, inc_s = (float(x) for x in args.tc.split("+"))
    with open(args.openings) as f:
        fens = [line.strip() for line in f if line.strip()]
    rng = random.Random(args.seed)
    rng.shuffle(fens)

    def parse_opts(lst):
        out = {}
        for item in lst:
            k, v = item.split("=", 1)
            out[k] = v
        return out

    pool_a = EnginePool(args.a, parse_opts(args.a_opt))
    pool_b = EnginePool(args.b, parse_opts(args.b_opt))

    jobs = []
    for i in range(args.games):
        fen = fens[(i // 2) % len(fens)]
        jobs.append((i, fen, i % 2 == 0))

    w = d = l = 0
    reasons: dict[str, int] = {}
    pgn_out = open(args.pgn, "w") if args.pgn else None
    lock = threading.Lock()
    t_start = time.time()

    def run(job):
        return play_game(job[0], job[1], job[2], pool_a, pool_b, base_s, inc_s,
                         args.movetime, args.max_plies, args.grace, args.overhead)

    try:
        with ThreadPoolExecutor(max_workers=args.concurrency) as ex:
            for gid, fen, a_white, result, reason, a_score, board, stats in ex.map(run, jobs):
                with lock:
                    if a_score == 1.0:
                        w += 1
                    elif a_score == 0.0:
                        l += 1
                    else:
                        d += 1
                    reasons[reason] = reasons.get(reason, 0) + 1
                    elo, margin, los = elo_stats(w, d, l)
                    n = w + d + l
                    print(
                        f"[{n:4d}/{args.games}] {args.a_name} {'brancas' if a_white else 'pretas '} "
                        f"{result:7s} {reason:18s} | {args.a_name} vs {args.b_name}: "
                        f"+{w} ={d} -{l}  Elo {elo:+6.1f} ± {margin:5.1f}  LOS {los*100:5.1f}%  "
                        f"({(time.time()-t_start)/60:.1f} min)",
                        flush=True,
                    )
                    if not args.movetime:
                        print(
                            f"         relógio mínimo {args.a_name} {stats['min_clock_a']:6.1f}s "
                            f"(média {stats['avg_think_a']:.2f}s/lance) | {args.b_name} "
                            f"{stats['min_clock_b']:6.1f}s (média {stats['avg_think_b']:.2f}s/lance) "
                            f"| {board.ply()} meios-lances",
                            flush=True,
                        )
                    if pgn_out:
                        game = chess.pgn.Game.from_board(board)
                        game.headers["White"] = args.a_name if a_white else args.b_name
                        game.headers["Black"] = args.b_name if a_white else args.a_name
                        game.headers["Result"] = result
                        game.headers["Termination"] = reason
                        game.headers["Round"] = str(gid + 1)
                        print(game, file=pgn_out, end="\n\n", flush=True)
    finally:
        pool_a.close_all()
        pool_b.close_all()
        if pgn_out:
            pgn_out.close()

    elo, margin, los = elo_stats(w, d, l)
    print("=" * 78)
    print(f"RESULTADO FINAL {args.a_name} vs {args.b_name}: +{w} ={d} -{l}")
    print(f"Elo {elo:+.1f} ± {margin:.1f} (95%)  LOS {los*100:.1f}%")
    print(f"Terminações: {reasons}")


if __name__ == "__main__":
    main()
