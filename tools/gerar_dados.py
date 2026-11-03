"""
Geração de dados para o Texel Tuning (autojogo do próprio motor)
Chess Challenge III - ICEV 2026

O motor joga contra si mesmo com poucos nós por lance, a partir de aberturas variadas.
Cada posição "quieta" (sem xeque, e cujo melhor lance não é captura/promoção) é gravada
com o RESULTADO FINAL da partida (1 = Brancas venceram, 0,5 = empate, 0 = Pretas).

    python tools/gerar_dados.py --partidas 600 --nos 2500 --processos 3 --saida dados_texel.txt
"""

import argparse
import os
import random
import sys
from multiprocessing import Pool

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import chess  # noqa: E402

BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def jogar(args):
    seed, fen_inicial, nos = args
    from search import SearchEngine
    from time_manager import TimeManager

    rng = random.Random(seed)
    board = chess.Board(fen_inicial)
    # alguns lances aleatórios para diversificar
    for _ in range(rng.randint(0, 3)):
        moves = list(board.legal_moves)
        if not moves:
            break
        board.push(rng.choice(moves))
    engine = SearchEngine(tt_bits=17)
    candidatos = []
    resultado = None
    seq_ganho = 0
    while resultado is None:
        if board.is_game_over(claim_draw=True):
            resultado = board.result(claim_draw=True)
            break
        if board.ply() > 320:
            resultado = "1/2-1/2"
            break
        tm = TimeManager()
        tm.set_fixed_time(30.0)
        r = engine.search_best_move(board, tm, use_book=False, use_tb=False, verbose=False, max_nodes=nos)
        lance = r.best_move
        quieta = not board.is_check() and not board.is_capture(lance) and lance.promotion is None
        if quieta and abs(r.score_cp) < 2000 and rng.random() < 0.5:
            candidatos.append(board.fen())
        branco = r.score_cp if board.turn == chess.WHITE else -r.score_cp
        # Adjudicação: vantagem decisiva (>= 15 peões) para o MESMO lado por 6 meios-lances seguidos
        sinal = 1 if branco >= 1500 else (-1 if branco <= -1500 else 0)
        seq_ganho = seq_ganho + sinal if sinal and (seq_ganho * sinal) >= 0 else sinal
        if abs(seq_ganho) >= 6:
            resultado = "1-0" if seq_ganho > 0 else "0-1"
            break
        board.push(lance)
    valor = {"1-0": "1.0", "0-1": "0.0"}.get(resultado, "0.5")
    return [f"{fen};{valor}" for fen in candidatos]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--partidas", type=int, default=400)
    ap.add_argument("--nos", type=int, default=2500)
    ap.add_argument("--processos", type=int, default=3)
    ap.add_argument("--saida", default="dados_texel.txt")
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()

    with open(os.path.join(BASE_DIR, "aberturas_teste.epd")) as f:
        aberturas = [line.strip() for line in f if line.strip()]
    rng = random.Random(args.seed)
    jobs = [(args.seed * 100000 + i, rng.choice(aberturas), args.nos) for i in range(args.partidas)]
    total = 0
    with open(args.saida, "a") as out, Pool(args.processos) as pool:
        for i, linhas in enumerate(pool.imap_unordered(jogar, jobs), 1):
            for linha in linhas:
                out.write(linha + "\n")
            out.flush()
            total += len(linhas)
            print(f"\rpartidas {i}/{args.partidas}  posições {total}", end="", flush=True)
    print()


if __name__ == "__main__":
    main()
