"""
Texel Tuning: ajuste automático dos pesos da função de avaliação
Chess Challenge III - ICEV 2026

Ideia (Peter Österlund, motor Texel): para cada posição de treino conhecemos o
resultado final da partida R (1, 0,5 ou 0). A avaliação estática q (em centipawns,
ponto de vista das Brancas) vira uma "probabilidade de vitória" pela sigmoide

        P(q) = 1 / (1 + 10^(-K·q/400))

e minimizamos o Erro Quadrático Médio  E = média (R - P(q))²  mexendo nos pesos.
Usamos busca local (coordenada a coordenada): para cada peso testamos +passo e
-passo e ficamos com a mudança se E diminuir. Repetimos até não melhorar mais.

    python tools/gerar_dados.py --partidas 600 --saida dados_texel.txt
    python tools/texel.py --dados dados_texel.txt --iteracoes 6 --processos 3
"""

import argparse
import os
import random
import sys
import time
from multiprocessing import Pool

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import chess  # noqa: E402
import evaluation as E  # noqa: E402

PIECES = {"P": chess.PAWN, "N": chess.KNIGHT, "B": chess.BISHOP, "R": chess.ROOK, "Q": chess.QUEEN}


# ------------------------------------------------------------------------------
# Definição dos parâmetros ajustáveis (todos inteiros)
# ------------------------------------------------------------------------------
def _packed(attr):
    return [(f"{attr}.mg", ("packed", attr, None, 0)), (f"{attr}.eg", ("packed", attr, None, 1))]


def _packed_list(attr, indices):
    out = []
    for i in indices:
        out.append((f"{attr}[{i}].mg", ("plist", attr, i, 0)))
        out.append((f"{attr}[{i}].eg", ("plist", attr, i, 1)))
    return out


def build_params():
    params = []
    for name in ("P", "N", "B", "R", "Q"):
        params.append((f"MATERIAL[{name}].mg", ("mat", name, None, 0)))
        params.append((f"MATERIAL[{name}].eg", ("mat", name, None, 1)))
    for attr in ("BISHOP_PAIR", "ROOK_OPEN_FILE", "ROOK_SEMI_OPEN_FILE", "ROOK_ON_SEVENTH",
                 "PAWN_ISOLATED", "PAWN_DOUBLED"):
        params += _packed(attr)
    for attr in ("THREAT_BY_PAWN", "HANGING_PIECE"):
        if hasattr(E, attr):
            params += _packed(attr)
    params += _packed_list("PASSED_BONUS", range(1, 7))
    params += _packed_list("CONNECTED_BONUS", range(1, 7))
    for name in ("N", "B", "R", "Q"):
        params.append((f"MOB_WEIGHT[{name}].mg", ("mob", name, None, 0)))
        params.append((f"MOB_WEIGHT[{name}].eg", ("mob", name, None, 1)))
    params.append(("KING_SHIELD_NEAR.mg", ("packed", "KING_SHIELD_NEAR", None, 0)))
    params.append(("KING_SHIELD_FAR.mg", ("packed", "KING_SHIELD_FAR", None, 0)))
    params.append(("KING_OPEN_FILE.mg", ("packed", "KING_OPEN_FILE", None, 0)))
    for attr in ("KING_SAFETY_PERCENT", "PASSED_KING_DIST_OWN", "PASSED_KING_DIST_ENEMY", "TEMPO_BONUS"):
        params.append((attr, ("int", attr, None, None)))
    return params


def get_value(spec):
    kind, attr, idx, comp = spec
    if kind == "packed":
        return E.unpack(getattr(E, attr))[comp]
    if kind == "plist":
        return E.unpack(getattr(E, attr)[idx])[comp]
    if kind == "mob":
        return E.unpack(E.MOB_WEIGHT[PIECES[attr]])[comp]
    if kind == "mat":
        table = E.MG_PIECE_VALUES if comp == 0 else E.EG_PIECE_VALUES
        return table[PIECES[attr]]
    return getattr(E, attr)


def set_value(spec, v):
    kind, attr, idx, comp = spec
    if kind == "packed":
        mg, eg = E.unpack(getattr(E, attr))
        setattr(E, attr, E.S(v, eg) if comp == 0 else E.S(mg, v))
    elif kind == "plist":
        lst = getattr(E, attr)
        mg, eg = E.unpack(lst[idx])
        lst[idx] = E.S(v, eg) if comp == 0 else E.S(mg, v)
    elif kind == "mob":
        pt = PIECES[attr]
        mg, eg = E.unpack(E.MOB_WEIGHT[pt])
        E.MOB_WEIGHT[pt] = E.S(v, eg) if comp == 0 else E.S(mg, v)
        wmg, weg = E.unpack(E.MOB_WEIGHT[pt])
        off = E.MOB_OFFSET[pt]
        E.MOB_TABLE[pt] = [E.S((n - off) * wmg, (n - off) * weg) for n in range(29)]
    elif kind == "mat":
        table = E.MG_PIECE_VALUES if comp == 0 else E.EG_PIECE_VALUES
        if table[PIECES[attr]] != v:
            table[PIECES[attr]] = v
            E.build_pst_tables()
    else:
        setattr(E, attr, v)


# ------------------------------------------------------------------------------
# Avaliação do erro (paralela: cada processo guarda uma fatia das posições)
# ------------------------------------------------------------------------------
_BOARDS = []
_RESULTS = []
_PARAMS = []


def _init_worker(lines):
    global _BOARDS, _RESULTS, _PARAMS
    _PARAMS = build_params()
    _BOARDS = []
    _RESULTS = []
    for line in lines:
        fen, res = line.rsplit(";", 1)
        _BOARDS.append(chess.Board(fen))
        _RESULTS.append(float(res))


def _partial_error(args):
    values, k = args
    for (_, spec), v in zip(_PARAMS, values):
        set_value(spec, v)
    E.clear_eval_cache()
    total = 0.0
    evaluate = E.evaluate
    for b, r in zip(_BOARDS, _RESULTS):
        q = evaluate(b)
        if not b.turn:
            q = -q
        p = 1.0 / (1.0 + 10.0 ** (-k * q / 400.0))
        total += (r - p) ** 2
    return total, len(_BOARDS)


class Tuner:
    def __init__(self, lines, processes):
        self.params = build_params()
        chunks = [lines[i::processes] for i in range(processes)]
        self.pools = [Pool(1, initializer=_init_worker, initargs=(c,)) for c in chunks]

    def error(self, values, k):
        results = [p.apply_async(_partial_error, ((values, k),)) for p in self.pools]
        tot = n = 0
        for r in results:
            a, b = r.get()
            tot += a
            n += b
        return tot / n

    def close(self):
        for p in self.pools:
            p.close()


def write_back(eval_path: str) -> None:
    """Grava os valores ATUAIS do módulo evaluation (já ajustados) de volta no evaluation.py."""
    import re

    with open(eval_path, encoding="utf-8") as f:
        src = f.read()

    def sub_line(pattern, new_text):
        nonlocal src
        src, n = re.subn(pattern, new_text, src, count=1, flags=re.M)
        if n != 1:
            raise RuntimeError(f"não achei: {pattern}")

    def packed_str(v):
        mg, eg = E.unpack(v)
        return f"S({mg}, {eg})"

    for attr in ("BISHOP_PAIR", "ROOK_OPEN_FILE", "ROOK_SEMI_OPEN_FILE", "ROOK_ON_SEVENTH",
                 "PAWN_ISOLATED", "PAWN_DOUBLED", "THREAT_BY_PAWN", "HANGING_PIECE",
                 "KING_SHIELD_NEAR", "KING_SHIELD_FAR", "KING_OPEN_FILE"):
        if hasattr(E, attr):
            sub_line(rf"^{attr} = S\(-?\d+, -?\d+\)", f"{attr} = {packed_str(getattr(E, attr))}")
    for attr in ("PASSED_BONUS", "CONNECTED_BONUS"):
        items = ", ".join(packed_str(v) for v in getattr(E, attr))
        sub_line(rf"^{attr} = \[.*\]$", f"{attr} = [{items}]")
    mob = ", ".join(
        f"chess.{n}: {packed_str(E.MOB_WEIGHT[pt])}"
        for n, pt in (("KNIGHT", chess.KNIGHT), ("BISHOP", chess.BISHOP), ("ROOK", chess.ROOK), ("QUEEN", chess.QUEEN))
    )
    sub_line(r"^MOB_WEIGHT = \{.*\}$", f"MOB_WEIGHT = {{{mob}}}")
    for attr in ("KING_SAFETY_PERCENT", "PASSED_KING_DIST_OWN", "PASSED_KING_DIST_ENEMY", "TEMPO_BONUS"):
        sub_line(rf"^{attr} = -?\d+", f"{attr} = {getattr(E, attr)}")
    for dict_name, table in (("MG_PIECE_VALUES", E.MG_PIECE_VALUES), ("EG_PIECE_VALUES", E.EG_PIECE_VALUES)):
        for pname, pt in (("PAWN", chess.PAWN), ("KNIGHT", chess.KNIGHT), ("BISHOP", chess.BISHOP),
                          ("ROOK", chess.ROOK), ("QUEEN", chess.QUEEN)):
            pattern = rf"(^{dict_name} = \{{(?:\n    chess\.\w+: -?\d+,)*?\n    chess\.{pname}: )-?\d+"
            src, n = re.subn(pattern, rf"\g<1>{table[pt]}", src, count=1, flags=re.M)
            if n != 1:
                raise RuntimeError(f"não achei {dict_name}[{pname}]")
    with open(eval_path, "w", encoding="utf-8") as f:
        f.write(src)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dados", required=True)
    ap.add_argument("--max-posicoes", type=int, default=200_000)
    ap.add_argument("--iteracoes", type=int, default=6)
    ap.add_argument("--processos", type=int, default=3)
    ap.add_argument("--aplicar", action="store_true", help="grava os pesos ajustados em evaluation.py")
    args = ap.parse_args()

    with open(args.dados) as f:
        lines = [line.strip() for line in f if ";" in line]
    random.Random(1).shuffle(lines)
    lines = lines[: args.max_posicoes]
    print(f"{len(lines)} posições")

    tuner = Tuner(lines, args.processos)
    params = tuner.params
    values = [get_value(spec) for _, spec in params]
    start_values = list(values)

    # 1. Ajusta a constante K da sigmoide
    best_k, best_e = 1.0, None
    for k in [x / 20 for x in range(10, 41)]:
        e = tuner.error(values, k)
        if best_e is None or e < best_e:
            best_k, best_e = k, e
    print(f"K = {best_k:.2f}  erro inicial = {best_e:.6f}")

    # 2. Busca local
    t0 = time.time()
    for it in range(args.iteracoes):
        improved = 0
        step = 4 if it < 2 else (2 if it < 4 else 1)
        for i, (name, _) in enumerate(params):
            for delta in (step, -step):
                trial = list(values)
                trial[i] += delta
                e = tuner.error(trial, best_k)
                if e < best_e:
                    best_e = e
                    values = trial
                    improved += 1
                    break
        print(f"iteração {it + 1}: passo {step}, {improved} melhorias, erro {best_e:.6f} ({(time.time() - t0) / 60:.1f} min)", flush=True)
        if not improved and step == 1:
            break
    tuner.close()

    print("\nPARÂMETROS AJUSTADOS (antes -> depois):")
    for (name, _), a, b in zip(params, start_values, values):
        flag = "" if a == b else "   *"
        print(f"  {name:28s} {a:6d} -> {b:6d}{flag}")

    if args.aplicar:
        for (_, spec), v in zip(params, values):
            set_value(spec, v)
        write_back(os.path.join(ROOT, "evaluation.py"))
        print("\nPesos gravados em evaluation.py")


if __name__ == "__main__":
    main()
