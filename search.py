"""
Módulo de Busca e Seleção de Lances - Autoria Própria
Chess Challenge III - ICEV 2026

Árvore de decisão do motor (python-chess é usado SOMENTE para representar o
tabuleiro e gerar lances legais – FAQ Item 14; toda a decisão é nossa):

 1. Iterative Deepening + Janela de Aspiração (Aspiration Windows) na raiz.
 2. Negamax Alpha-Beta com Principal Variation Search (PVS) e "fail-soft".
 3. Tabela de Transposição (TT) em vetor de tamanho fixo, chave de 64 bits
    calculada direto dos bitboards (muito mais rápida que o Zobrist do Polyglot),
    guardando profundidade, limite (EXACT/LOWER/UPPER), lance e avaliação estática.
 4. Quiescence Search: capturas/promoções (e todas as fugas quando em xeque),
    com Stand-Pat, Delta Pruning e poda por SEE (Static Exchange Evaluation).
 5. Ordenação: lance da TT -> capturas boas (MVV-LVA + SEE) -> Killer Moves ->
    Counter-Move -> History Heuristic (com "gravidade") -> capturas ruins.
 6. Podas seletivas: Reverse Futility Pruning, Razoring, Null Move Pruning
    adaptativo, Late Move Pruning, Futility Pruning, poda de capturas ruins (SEE)
    e Late Move Reductions (LMR) com tabela logarítmica.
 7. Extensão de Xeque, Internal Iterative Reduction e Mate Distance Pruning.
 8. Detecção de repetição própria (pilha de chaves) e regra dos 50 lances,
    com "contempt" (pequena aversão ao empate, pois jogamos para ganhar).
 9. Tablebases Syzygy (até 5 peças – FAQ Item 4) dentro da busca (WDL) e na raiz (DTZ).
"""

from __future__ import annotations

import math
import threading
import time
from dataclasses import dataclass, field
from operator import itemgetter

import chess
from chess import (
    BB_SQUARES,
    BB_KNIGHT_ATTACKS,
    BB_KING_ATTACKS,
    BB_PAWN_ATTACKS,
    BB_DIAG_ATTACKS,
    BB_DIAG_MASKS,
    BB_RANK_ATTACKS,
    BB_RANK_MASKS,
    BB_FILE_ATTACKS,
    BB_FILE_MASKS,
    BB_RANKS,
    WHITE,
    BLACK,
    PAWN,
    KNIGHT,
    BISHOP,
    ROOK,
    QUEEN,
    KING,
)

from evaluation import (
    evaluate,
    evaluate_board,
    MATE_SCORE,
    MATE_THRESHOLD,
    DRAW_SCORE,
    SEE_VALUES,
)
from time_manager import TimeManager
from book_and_tb import BookAndTablebaseManager

# ==============================================================================
# CONSTANTES
# ==============================================================================
INF = 32000
MAX_PLY = 100
TB_WIN = 20000            # vitória de tablebase (abaixo da faixa de mate)
TB_WIN_THRESHOLD = TB_WIN - MAX_PLY

TT_EXACT = 0
TT_LOWERBOUND = 1         # corte beta: score >= beta
TT_UPPERBOUND = 2         # falhou baixo: score <= alpha

NULL_MOVE = chess.Move.null()
HISTORY_MAX = 16384
BB_ALL = 0xFFFF_FFFF_FFFF_FFFF
_first = itemgetter(0)

# Categorias de ordenação (o valor final do lance é somado a elas)
SCORE_QUEEN_PROMO = 4_000_000
SCORE_GOOD_CAPTURE = 3_000_000
SCORE_KILLER_1 = 2_000_000
SCORE_KILLER_2 = 1_900_000
SCORE_COUNTER = 1_800_000
SCORE_BAD_CAPTURE = -1_000_000
SCORE_UNDER_PROMO = -2_000_000

KIND_QUIET = 0
KIND_GOOD_TACTICAL = 1    # captura boa ou promoção a Dama
KIND_BAD_TACTICAL = 2     # captura que perde material (SEE < 0) ou sub-promoção

# Late Move Reductions: redução = 0.75 + ln(profundidade) * ln(nº do lance) / 2.25
LMR_TABLE = [[0] * 64 for _ in range(64)]
for _d in range(1, 64):
    for _m in range(1, 64):
        LMR_TABLE[_d][_m] = int(0.75 + math.log(_d) * math.log(_m) / 2.25)

# Late Move Pruning: nº máximo de lances quietos examinados por profundidade
LMP_TABLE = [[0] * 9, [0] * 9]
for _d in range(1, 9):
    LMP_TABLE[0][_d] = (3 + _d * _d) // 2   # posição piorando
    LMP_TABLE[1][_d] = 3 + _d * _d          # posição melhorando

RFP_MARGIN = 75           # Reverse Futility Pruning (por ply de profundidade)
FUTILITY_BASE = 80
FUTILITY_PER_DEPTH = 85
RAZOR_BASE = 350          # Razoring: margem = base + fator * profundidade²
RAZOR_PER_DEPTH2 = 250
DELTA_MARGIN = 180        # Delta Pruning na quiescência
ASPIRATION_START = 30


class SearchTimeout(Exception):
    """Disparada internamente quando o tempo máximo (hard limit) acaba ou chega um 'stop'."""


@dataclass
class SearchResult:
    best_move: chess.Move
    score_cp: int
    depth: int
    nodes: int
    elapsed_sec: float
    pv: list[str]
    source: str = "SEARCH"     # "SEARCH", "BOOK" ou "SYZYGY_TB"
    seldepth: int = 0
    pv_moves: list[chess.Move] = field(default_factory=list)


def position_key(board: chess.Board) -> int:
    """
    Chave de 64 bits da posição calculada direto dos bitboards da python-chess.
    Custa ~0,2 µs (o Zobrist do Polyglot custa ~20 µs por chamada!).
    A casa de en passant só entra na chave se a captura en passant for possível.
    """
    ep = board.ep_square
    if ep is not None:
        turn = board.turn
        if not (BB_PAWN_ATTACKS[not turn][ep] & board.pawns & board.occupied_co[turn]):
            ep = None
    return hash((
        board.pawns, board.knights, board.bishops, board.rooks, board.queens, board.kings,
        board.occupied_co[WHITE], board.turn, board.castling_rights, ep,
    ))


def _attackers_to(board: chess.Board, sq: int, occ: int) -> int:
    """Todas as peças (das duas cores) que atacam a casa 'sq' dada a ocupação 'occ'."""
    rq = board.rooks | board.queens
    bq = board.bishops | board.queens
    return (
        (BB_KING_ATTACKS[sq] & board.kings)
        | (BB_KNIGHT_ATTACKS[sq] & board.knights)
        | (BB_RANK_ATTACKS[sq][BB_RANK_MASKS[sq] & occ] & rq)
        | (BB_FILE_ATTACKS[sq][BB_FILE_MASKS[sq] & occ] & rq)
        | (BB_DIAG_ATTACKS[sq][BB_DIAG_MASKS[sq] & occ] & bq)
        | (BB_PAWN_ATTACKS[BLACK][sq] & board.pawns & board.occupied_co[WHITE])
        | (BB_PAWN_ATTACKS[WHITE][sq] & board.pawns & board.occupied_co[BLACK])
    )


def see_ge(board: chess.Board, move: chess.Move, threshold: int = 0) -> bool:
    """
    Static Exchange Evaluation: simula a sequência de capturas na casa de destino
    (sempre com a peça menos valiosa) e responde se o saldo material do lance é
    >= threshold. Ex.: Dama captura peão defendido -> saldo negativo -> lance ruim.
    """
    if move.promotion:
        return True
    from_sq = move.from_square
    to_sq = move.to_square
    victim = board.piece_type_at(to_sq)
    occ = board.occupied ^ BB_SQUARES[from_sq]
    if victim is None:
        if to_sq == board.ep_square and board.pawns & BB_SQUARES[from_sq]:
            victim = PAWN
            occ ^= BB_SQUARES[to_sq + (-8 if board.turn == WHITE else 8)]
        else:
            victim = 0
    swap = SEE_VALUES[victim] - threshold
    if swap < 0:
        return False
    attacker = board.piece_type_at(from_sq)
    swap = SEE_VALUES[attacker] - swap
    if swap <= 0:
        return True

    occ |= BB_SQUARES[to_sq]
    stm = board.turn
    attackers = _attackers_to(board, to_sq, occ)
    bq = board.bishops | board.queens
    rq = board.rooks | board.queens
    res = 1
    while True:
        stm = not stm
        attackers &= occ
        stm_attackers = attackers & board.occupied_co[stm]
        if not stm_attackers:
            break
        res ^= 1
        b = stm_attackers & board.pawns
        if b:
            swap = SEE_VALUES[PAWN] - swap
            if swap < res:
                break
            occ ^= b & -b
            attackers |= BB_DIAG_ATTACKS[to_sq][BB_DIAG_MASKS[to_sq] & occ] & bq
            continue
        b = stm_attackers & board.knights
        if b:
            swap = SEE_VALUES[KNIGHT] - swap
            if swap < res:
                break
            occ ^= b & -b
            continue
        b = stm_attackers & board.bishops
        if b:
            swap = SEE_VALUES[BISHOP] - swap
            if swap < res:
                break
            occ ^= b & -b
            attackers |= BB_DIAG_ATTACKS[to_sq][BB_DIAG_MASKS[to_sq] & occ] & bq
            continue
        b = stm_attackers & board.rooks
        if b:
            swap = SEE_VALUES[ROOK] - swap
            if swap < res:
                break
            occ ^= b & -b
            attackers |= (
                BB_RANK_ATTACKS[to_sq][BB_RANK_MASKS[to_sq] & occ]
                | BB_FILE_ATTACKS[to_sq][BB_FILE_MASKS[to_sq] & occ]
            ) & rq
            continue
        b = stm_attackers & board.queens
        if b:
            swap = SEE_VALUES[QUEEN] - swap
            if swap < res:
                break
            occ ^= b & -b
            attackers |= (BB_DIAG_ATTACKS[to_sq][BB_DIAG_MASKS[to_sq] & occ] & bq) | (
                (BB_RANK_ATTACKS[to_sq][BB_RANK_MASKS[to_sq] & occ]
                 | BB_FILE_ATTACKS[to_sq][BB_FILE_MASKS[to_sq] & occ]) & rq
            )
            continue
        # Só sobrou o Rei para capturar: só é possível se o adversário não tiver mais atacantes
        if attackers & board.occupied_co[not stm] & occ:
            return bool(res ^ 1)
        return bool(res)
    return bool(res)


def check_squares(board: chess.Board) -> list[int]:
    """
    Para cada tipo de peça, as casas de onde ela daria xeque DIRETO no rei adversário.
    Permite saber se um lance dá xeque sem executá-lo (usado para não podar xeques).
    """
    them = not board.turn
    ksq = (board.kings & board.occupied_co[them]).bit_length() - 1
    occ = board.occupied
    diag = BB_DIAG_ATTACKS[ksq][BB_DIAG_MASKS[ksq] & occ]
    ortho = BB_RANK_ATTACKS[ksq][BB_RANK_MASKS[ksq] & occ] | BB_FILE_ATTACKS[ksq][BB_FILE_MASKS[ksq] & occ]
    return [0, BB_PAWN_ATTACKS[them][ksq], BB_KNIGHT_ATTACKS[ksq], diag, ortho, diag | ortho, 0]


class SearchEngine:
    def __init__(self, tt_bits: int = 20, tt_max_entries: int | None = None):
        if tt_max_entries is not None:  # compatibilidade com a versão anterior
            tt_bits = max(16, min(24, int(math.log2(max(1, tt_max_entries))) + 1))
        self.book_tb = BookAndTablebaseManager()
        self.contempt = 15          # centipawns de aversão ao empate (jogamos para ganhar)
        self._lock = threading.Lock()
        self.resize_tt(tt_bits)
        self.history = [[0] * 4096, [0] * 4096]   # [cor][origem*64 + destino]
        self.counter_moves = [0] * 4096           # resposta que refutou o lance anterior
        self.killers = [[-1, -1] for _ in range(MAX_PLY + 2)]
        self.nodes = 0
        self.seldepth = 0
        self.stop_event: threading.Event | None = None
        self.time_manager: TimeManager | None = None
        self.board = chess.Board()
        self.hash_stack: list[int] = []
        self.eval_stack = [0] * (MAX_PLY + 2)
        self.move_stack_ply: list[chess.Move | None] = [None] * (MAX_PLY + 2)
        self.pv_table: list[list[chess.Move]] = [[] for _ in range(MAX_PLY + 2)]
        self.root_turn = WHITE
        self.hard_deadline = 0.0
        self.max_nodes = 0
        self.iter_best_move: chess.Move | None = None
        self.iter_best_score = -INF
        self.tb_pieces = 0

    # ------------------------------------------------------------------
    # Tabela de Transposição
    # ------------------------------------------------------------------
    def resize_tt(self, tt_bits: int) -> None:
        self.tt_size = 1 << tt_bits
        self.tt_mask = self.tt_size - 1
        self.tt: list[tuple | None] = [None] * self.tt_size
        self.tt_age = 0

    def clear_cache(self) -> None:
        """Nova partida: limpa TT, históricos e cache de peões."""
        from evaluation import clear_eval_cache

        with self._lock:
            self.tt = [None] * self.tt_size
            self.tt_age = 0
            self.history = [[0] * 4096, [0] * 4096]
            self.counter_moves = [0] * 4096
            clear_eval_cache()

    def _tt_store(self, key, depth, flag, score, move, static_eval, ply) -> None:
        # Pontuações de mate são guardadas relativas à posição (independentes do ply)
        if score >= MATE_THRESHOLD:
            score += ply
        elif score <= -MATE_THRESHOLD:
            score -= ply
        idx = key & self.tt_mask
        old = self.tt[idx]
        if old is not None:
            if old[0] == key:
                if move is None:
                    move = old[4]
                if static_eval is None:
                    static_eval = old[5]
            # Política de substituição: preserva entradas bem mais profundas da busca atual
            if old[6] == self.tt_age and old[1] > depth + 2 and (old[0] != key or flag != TT_EXACT):
                return
        self.tt[idx] = (key, depth, flag, score, move, static_eval, self.tt_age)

    # ------------------------------------------------------------------
    # Controle de tempo / interrupção
    # ------------------------------------------------------------------
    def _check_time(self) -> None:
        if self.stop_event is not None and self.stop_event.is_set():
            raise SearchTimeout()
        if self.hard_deadline and time.perf_counter() >= self.hard_deadline:
            raise SearchTimeout()
        if self.max_nodes and self.nodes >= self.max_nodes:
            raise SearchTimeout()

    def _draw_score(self, ply: int) -> int:
        # Contempt: para o lado que estava na vez na raiz (nós), empate vale um pouco menos que 0
        return -self.contempt if (ply & 1) == 0 else self.contempt

    def _is_repetition(self, key: int, halfmove_clock: int) -> bool:
        stack = self.hash_stack
        n = len(stack)
        i = n - 4
        stop = n - halfmove_clock
        if stop < 0:
            stop = 0
        while i >= stop:
            if stack[i] == key:
                return True
            i -= 2
        return False

    # ------------------------------------------------------------------
    # Ordenação de lances
    # ------------------------------------------------------------------
    def _score_moves(self, board, moves, ply, skip_move, prev_move):
        them = board.occupied_co[not board.turn]
        piece_type_at = board.piece_type_at
        ep = board.ep_square
        k0, k1 = self.killers[ply]
        counter = self.counter_moves[prev_move.from_square * 64 + prev_move.to_square] if prev_move else -1
        hist = self.history[board.turn]
        skip_to = skip_move.to_square if skip_move is not None else -1
        scored = []
        append = scored.append
        for m in moves:
            to = m.to_square
            if to == skip_to and m == skip_move:
                continue
            promo = m.promotion
            if them & BB_SQUARES[to]:
                victim = piece_type_at(to)
                attacker = piece_type_at(m.from_square)
                if promo is not None:
                    if promo == QUEEN:
                        append((SCORE_QUEEN_PROMO + victim * 100, m, KIND_GOOD_TACTICAL))
                    else:
                        append((SCORE_UNDER_PROMO + victim * 100, m, KIND_BAD_TACTICAL))
                elif SEE_VALUES[attacker] <= SEE_VALUES[victim] or see_ge(board, m, 0):
                    append((SCORE_GOOD_CAPTURE + victim * 100 - attacker, m, KIND_GOOD_TACTICAL))
                else:
                    append((SCORE_BAD_CAPTURE + victim * 100 - attacker, m, KIND_BAD_TACTICAL))
            elif promo is not None:
                if promo == QUEEN:
                    append((SCORE_QUEEN_PROMO, m, KIND_GOOD_TACTICAL))
                else:
                    append((SCORE_UNDER_PROMO, m, KIND_BAD_TACTICAL))
            elif to == ep and piece_type_at(m.from_square) == PAWN:
                append((SCORE_GOOD_CAPTURE + 99, m, KIND_GOOD_TACTICAL))
            else:
                idx = m.from_square * 64 + to
                if idx == k0:
                    append((SCORE_KILLER_1, m, KIND_QUIET))
                elif idx == k1:
                    append((SCORE_KILLER_2, m, KIND_QUIET))
                elif idx == counter:
                    append((SCORE_COUNTER, m, KIND_QUIET))
                else:
                    append((hist[idx], m, KIND_QUIET))
        scored.sort(key=_first, reverse=True)
        return scored

    def _staged_moves(self, board, tt_move, ply, prev_move):
        """
        Geração de lances EM ESTÁGIOS (só gera o que for preciso):
          0) lance da TT  1) capturas boas e promoções a Dama  2) killers e counter-move
          3) lances quietos ordenados pelo History  4) capturas ruins e sub-promoções.
        Em nós de corte (beta cutoff) quase sempre um dos primeiros estágios basta,
        e a geração dos lances quietos (a parte mais cara) nem acontece.
        """
        if tt_move is not None:
            yield SCORE_QUEEN_PROMO * 2, tt_move, self._move_kind(board, tt_move)
            tt_to = tt_move.to_square
            tried = [tt_move.from_square * 64 + tt_to]
        else:
            tt_to = -1
            tried = []

        turn = board.turn
        them = board.occupied_co[not turn]
        occ = board.occupied
        piece_type_at = board.piece_type_at

        # Estágio 1: capturas (inclui en passant) + promoções
        good = []
        bad = []
        for m in board.generate_legal_captures():
            to = m.to_square
            if to == tt_to and m == tt_move:
                continue
            promo = m.promotion
            if them & BB_SQUARES[to]:
                victim = piece_type_at(to)
            else:
                victim = PAWN  # en passant
            if promo is not None:
                if promo == QUEEN:
                    good.append((SCORE_QUEEN_PROMO + victim * 100, m, KIND_GOOD_TACTICAL))
                else:
                    bad.append((SCORE_UNDER_PROMO + victim * 100, m, KIND_BAD_TACTICAL))
                continue
            attacker = piece_type_at(m.from_square)
            if SEE_VALUES[attacker] <= SEE_VALUES[victim] or see_ge(board, m, 0):
                good.append((SCORE_GOOD_CAPTURE + victim * 100 - attacker, m, KIND_GOOD_TACTICAL))
            else:
                bad.append((SCORE_BAD_CAPTURE + victim * 100 - attacker, m, KIND_BAD_TACTICAL))
        promo_from = board.pawns & board.occupied_co[turn] & BB_RANKS[6 if turn == WHITE else 1]
        if promo_from:
            for m in board.generate_legal_moves(promo_from, ~occ & BB_RANKS[7 if turn == WHITE else 0]):
                if m.to_square == tt_to and m == tt_move:
                    continue
                if m.promotion == QUEEN:
                    good.append((SCORE_QUEEN_PROMO, m, KIND_GOOD_TACTICAL))
                else:
                    bad.append((SCORE_UNDER_PROMO, m, KIND_BAD_TACTICAL))
        if good:
            if len(good) > 1:
                good.sort(key=_first, reverse=True)
            yield from good

        # Estágio 2: killers e counter-move (validados: precisam ser legais e quietos)
        k0, k1 = self.killers[ply]
        counter = self.counter_moves[prev_move.from_square * 64 + prev_move.to_square] if prev_move else -1
        for idx, sc in ((k0, SCORE_KILLER_1), (k1, SCORE_KILLER_2), (counter, SCORE_COUNTER)):
            if idx < 0 or idx in tried:
                continue
            to = idx & 63
            if occ & BB_SQUARES[to]:
                continue
            m = chess.Move(idx >> 6, to)
            if board.is_legal(m):
                tried.append(idx)
                yield sc, m, KIND_QUIET

        # Estágio 3: lances quietos ordenados pela History Heuristic
        hist = self.history[turn]
        ep = board.ep_square
        pawns_us = board.pawns & board.occupied_co[turn]
        quiets = []
        # Obs.: a python-chess gera o roque como "rei -> casa da própria torre", então as
        # torres com direito de roque precisam estar na máscara de destino.
        quiet_mask = (~occ & BB_ALL) | (board.castling_rights & board.occupied_co[turn])
        for m in board.generate_legal_moves(BB_ALL, quiet_mask):
            if m.promotion is not None:
                continue  # já gerada no estágio 1
            to = m.to_square
            frm = m.from_square
            if to == ep and pawns_us & BB_SQUARES[frm] and (frm & 7) != (to & 7):
                continue  # en passant já gerado no estágio 1
            idx = frm * 64 + to
            if idx in tried:
                continue
            quiets.append((hist[idx], m, KIND_QUIET))
        if quiets:
            quiets.sort(key=_first, reverse=True)
            yield from quiets

        # Estágio 4: capturas que perdem material e sub-promoções
        if bad:
            bad.sort(key=_first, reverse=True)
            yield from bad

    def _move_kind(self, board, move) -> int:
        to = move.to_square
        if move.promotion is not None:
            return KIND_GOOD_TACTICAL if move.promotion == QUEEN else KIND_BAD_TACTICAL
        if board.occupied_co[not board.turn] & BB_SQUARES[to]:
            return KIND_GOOD_TACTICAL
        if to == board.ep_square and board.pawns & BB_SQUARES[move.from_square]:
            return KIND_GOOD_TACTICAL
        return KIND_QUIET

    def _update_quiet_stats(self, board, move, depth, ply, quiets_tried, prev_move) -> None:
        bonus = min(16 * depth * depth + 32 * depth, 1600)
        hist = self.history[board.turn]
        idx = move.from_square * 64 + move.to_square
        hist[idx] += bonus - hist[idx] * bonus // HISTORY_MAX
        for q in quiets_tried:
            qi = q.from_square * 64 + q.to_square
            hist[qi] += -bonus - hist[qi] * bonus // HISTORY_MAX
        killers = self.killers[ply]
        if killers[0] != idx:
            killers[1] = killers[0]
            killers[0] = idx
        if prev_move is not None:
            self.counter_moves[prev_move.from_square * 64 + prev_move.to_square] = idx

    # ------------------------------------------------------------------
    # Quiescence Search
    # ------------------------------------------------------------------
    def _qsearch(self, alpha: int, beta: int, ply: int) -> int:
        board = self.board
        self.nodes += 1
        if (self.nodes & 1023) == 0:
            self._check_time()
        if ply > self.seldepth:
            self.seldepth = ply
        if ply >= MAX_PLY:
            return evaluate(board)

        key = position_key(board)
        entry = self.tt[key & self.tt_mask]
        tt_eval = None
        if entry is not None and entry[0] == key:
            s = entry[3]
            if s >= MATE_THRESHOLD:
                s -= ply
            elif s <= -MATE_THRESHOLD:
                s += ply
            f = entry[2]
            if f == TT_EXACT or (f == TT_LOWERBOUND and s >= beta) or (f == TT_UPPERBOUND and s <= alpha):
                return s
            tt_eval = entry[5]

        in_check = board.is_check()
        if in_check:
            stand_pat = None
            best = -MATE_SCORE + ply
            moves = list(board.generate_legal_moves())
            if not moves:
                return best  # xeque-mate
            ordered = self._score_moves(board, moves, ply, None, None)
        else:
            stand_pat = tt_eval if tt_eval is not None else evaluate(board)
            if stand_pat >= beta:
                if tt_eval is None:
                    self._tt_store(key, -1, TT_LOWERBOUND, stand_pat, None, stand_pat, ply)
                return stand_pat
            best = stand_pat
            if stand_pat > alpha:
                alpha = stand_pat
            moves = list(board.generate_legal_captures())
            # Promoções sem captura (só a Dama interessa aqui)
            promo_from = board.pawns & board.occupied_co[board.turn] & BB_RANKS[6 if board.turn == WHITE else 1]
            if promo_from:
                for m in board.generate_legal_moves(promo_from, ~board.occupied & BB_RANKS[7 if board.turn == WHITE else 0]):
                    if m.promotion == QUEEN:
                        moves.append(m)
            if not moves:
                if tt_eval is None:
                    self._tt_store(key, -1, TT_UPPERBOUND, best, None, stand_pat, ply)
                return best
            ordered = self._score_moves(board, moves, ply, None, None)

        orig_alpha = alpha
        best_move = None
        piece_type_at = board.piece_type_at
        push = board.push
        pop = board.pop
        for mscore, move, kind in ordered:
            if stand_pat is not None:
                if kind == KIND_BAD_TACTICAL:
                    break  # capturas que perdem material (SEE < 0) e sub-promoções: poda
                if move.promotion is None:
                    victim = piece_type_at(move.to_square)
                    gain = SEE_VALUES[victim] if victim else 100
                    if stand_pat + gain + DELTA_MARGIN <= alpha:
                        continue  # Delta Pruning: nem ganhando a peça alcançamos alpha
            push(move)
            score = -self._qsearch(-beta, -alpha, ply + 1)
            pop()
            if score > best:
                best = score
                if score > alpha:
                    best_move = move
                    if score >= beta:
                        break
                    alpha = score

        if best >= beta:
            flag = TT_LOWERBOUND
        elif best > orig_alpha:
            flag = TT_EXACT
        else:
            flag = TT_UPPERBOUND
        self._tt_store(key, -1, flag, best, best_move, stand_pat, ply)
        return best

    # ------------------------------------------------------------------
    # Negamax Alpha-Beta (PVS)
    # ------------------------------------------------------------------
    def _search(self, depth: int, alpha: int, beta: int, ply: int, do_null: bool = True) -> int:
        board = self.board
        pv_node = beta - alpha > 1
        self.pv_table[ply] = []

        key = position_key(board)

        if ply:
            hmc = board.halfmove_clock
            if hmc >= 100:
                return self._draw_score(ply)
            if hmc >= 4 and self._is_repetition(key, hmc):
                return self._draw_score(ply)
            if not (board.pawns | board.rooks | board.queens) and (board.knights | board.bishops).bit_count() <= 1:
                return self._draw_score(ply)  # material insuficiente
            if ply >= MAX_PLY:
                return evaluate(board)
            # Mate Distance Pruning: não adianta procurar mate mais longo que um já encontrado
            if alpha < -MATE_SCORE + ply:
                alpha = -MATE_SCORE + ply
            if beta > MATE_SCORE - ply - 1:
                beta = MATE_SCORE - ply - 1
            if alpha >= beta:
                return alpha

        in_check = board.is_check()
        if in_check:
            depth += 1  # Extensão de Xeque
        if depth <= 0:
            return self._qsearch(alpha, beta, ply)

        self.nodes += 1
        if (self.nodes & 1023) == 0:
            self._check_time()
        if ply > self.seldepth:
            self.seldepth = ply

        # 1. Tabela de Transposição
        entry = self.tt[key & self.tt_mask]
        tt_move = None
        tt_eval = None
        if entry is not None and entry[0] == key:
            tt_move = entry[4]
            tt_eval = entry[5]
            if not pv_node and entry[1] >= depth:
                s = entry[3]
                if s >= MATE_THRESHOLD:
                    s -= ply
                elif s <= -MATE_THRESHOLD:
                    s += ply
                f = entry[2]
                if f == TT_EXACT or (f == TT_LOWERBOUND and s >= beta) or (f == TT_UPPERBOUND and s <= alpha):
                    return s

        # 2. Tablebases Syzygy dentro da busca (após capturas/lances de peão com <= 5 peças)
        if ply and self.tb_pieces and board.halfmove_clock == 0 and board.occupied.bit_count() <= self.tb_pieces:
            wdl = self.book_tb.probe_wdl(board)
            if wdl is not None:
                if wdl > 1:
                    tb_score = TB_WIN - ply
                elif wdl < -1:
                    tb_score = -TB_WIN + ply
                else:
                    tb_score = DRAW_SCORE
                self._tt_store(key, MAX_PLY, TT_EXACT, tb_score, None, None, ply)
                return tb_score

        # 3. Avaliação estática (reaproveitada da TT quando disponível)
        if in_check:
            static_eval = -INF
            improving = False
        else:
            static_eval = tt_eval if tt_eval is not None else evaluate(board)
            improving = ply >= 2 and static_eval > self.eval_stack[ply - 2]
        self.eval_stack[ply] = static_eval

        prev_move = self.move_stack_ply[ply - 1] if ply else None

        if not pv_node and not in_check:
            # 3a. Reverse Futility Pruning: posição tão boa que mesmo com margem passa de beta
            if depth <= 7 and abs(beta) < TB_WIN_THRESHOLD:
                margin = RFP_MARGIN * depth - (35 if improving else 0)
                if static_eval - margin >= beta:
                    return static_eval - margin

            # 3b. Razoring: posição muito ruim perto das folhas -> confirma só com quiescência
            if depth <= 2 and static_eval + RAZOR_BASE + RAZOR_PER_DEPTH2 * depth * depth < alpha:
                v = self._qsearch(alpha, alpha + 1, ply)
                if v <= alpha:
                    return v

            # 3c. Null Move Pruning: "passamos a vez"; se ainda assim o rival não alcança beta, cortamos
            if (
                do_null
                and depth >= 3
                and static_eval >= beta
                and abs(beta) < TB_WIN_THRESHOLD
                and (board.knights | board.bishops | board.rooks | board.queens) & board.occupied_co[board.turn]
            ):
                r = 3 + depth // 4 + min(3, (static_eval - beta) // 200)
                if r > depth - 2:
                    r = depth - 2  # garante 1 ply completo: a quiescência sozinha não vê ameaças de mate
                self.hash_stack.append(key)
                self.move_stack_ply[ply] = None
                board.push(NULL_MOVE)
                v = -self._search(depth - 1 - r, -beta, -beta + 1, ply + 1, False)
                board.pop()
                self.hash_stack.pop()
                if v >= beta:
                    return beta if v >= TB_WIN_THRESHOLD else v

        # 4. Internal Iterative Reduction: sem lance da TT, a ordenação é pior -> reduz 1 ply
        if depth >= 4 and tt_move is None:
            depth -= 1

        # 5. Laço de lances (estágio 0: lance da TT antes de gerar os demais)
        best_score = -INF
        best_move = None
        move_count = 0
        quiets_tried: list[chess.Move] = []
        orig_alpha = alpha
        push = board.push
        pop = board.pop
        hash_stack = self.hash_stack
        move_stack_ply = self.move_stack_ply

        if tt_move is not None and not board.is_legal(tt_move):
            tt_move = None

        chk_sq = None
        piece_type_at = board.piece_type_at
        futility_ok = (not in_check) and depth <= 6 and static_eval + FUTILITY_BASE + FUTILITY_PER_DEPTH * depth <= alpha
        lmp_limit = LMP_TABLE[1 if improving else 0][depth] if depth <= 8 else 999

        for mscore, move, kind in self._staged_moves(board, tt_move, ply, prev_move):
            quiet = kind == KIND_QUIET

            # Podas por lance (nunca na raiz, nunca em xeque e só depois de já termos um
            # lance que não perde). Lances que dão XEQUE nunca são podados (táticas/mates).
            if ply and best_score > -TB_WIN_THRESHOLD and not in_check:
                if quiet:
                    # Late Move Pruning (muitos lances quietos já vistos) ou
                    # Futility Pruning (nota estática muito abaixo de alpha)
                    if move_count >= lmp_limit or futility_ok:
                        if chk_sq is None:
                            chk_sq = check_squares(board)
                        if not (chk_sq[piece_type_at(move.from_square)] & BB_SQUARES[move.to_square]):
                            continue
                elif kind == KIND_BAD_TACTICAL and depth <= 3:
                    # captura perdedora (SEE < 0) perto das folhas
                    if chk_sq is None:
                        chk_sq = check_squares(board)
                    if not (chk_sq[piece_type_at(move.from_square)] & BB_SQUARES[move.to_square]):
                        continue

            hash_stack.append(key)
            move_stack_ply[ply] = move
            push(move)
            gives_check = board.is_check()

            move_count += 1
            new_depth = depth - 1

            if move_count == 1:
                score = -self._search(new_depth, -beta, -alpha, ply + 1)
            else:
                # Late Move Reductions
                r = 0
                # Sacrifício com xeque (captura "ruim" que dá xeque) não é reduzido: é o
                # padrão típico das combinações de mate (ex.: Bxh7+, Dxg7+).
                if depth >= 3 and kind != KIND_GOOD_TACTICAL and move_count > 2 and not (gives_check and kind == KIND_BAD_TACTICAL):
                    r = LMR_TABLE[depth if depth < 64 else 63][move_count if move_count < 64 else 63]
                    if pv_node:
                        r -= 1
                    if not improving:
                        r += 1
                    if gives_check:
                        r -= 1
                    if mscore >= SCORE_COUNTER:
                        r -= 1  # killer / counter-move
                    elif quiet:
                        r -= mscore // 8000  # history alto reduz menos; baixo reduz mais
                    if r > new_depth - 1:
                        r = new_depth - 1
                    if r < 0:
                        r = 0
                score = -self._search(new_depth - r, -alpha - 1, -alpha, ply + 1)
                if score > alpha and r > 0:
                    score = -self._search(new_depth, -alpha - 1, -alpha, ply + 1)
                if score > alpha and score < beta:
                    score = -self._search(new_depth, -beta, -alpha, ply + 1)

            pop()
            hash_stack.pop()

            if score > best_score:
                best_score = score
                if score > alpha:
                    best_move = move
                    if pv_node:
                        self.pv_table[ply] = [move] + self.pv_table[ply + 1]
                    if ply == 0:
                        self.iter_best_move = move
                        self.iter_best_score = score
                    if score >= beta:
                        if quiet:
                            self._update_quiet_stats(board, move, depth, ply, quiets_tried, prev_move)
                        break
                    alpha = score
            if quiet:
                quiets_tried.append(move)

        if move_count == 0:
            # Nenhum lance legal: xeque-mate ou afogamento
            if best_score == -INF:
                return -MATE_SCORE + ply if in_check else self._draw_score(ply)

        if best_score >= beta:
            flag = TT_LOWERBOUND
        elif best_score > orig_alpha:
            flag = TT_EXACT
        else:
            flag = TT_UPPERBOUND
        self._tt_store(key, depth, flag, best_score, best_move, None if in_check else static_eval, ply)
        return best_score

    # ------------------------------------------------------------------
    # Raiz: Aspiration Windows
    # ------------------------------------------------------------------
    def _aspiration(self, depth: int, prev_score: int | None) -> int:
        if depth < 4 or prev_score is None or abs(prev_score) >= TB_WIN_THRESHOLD:
            return self._search(depth, -INF, INF, 0)
        delta = ASPIRATION_START
        alpha = max(prev_score - delta, -INF)
        beta = min(prev_score + delta, INF)
        while True:
            score = self._search(depth, alpha, beta, 0)
            if score <= alpha:
                beta = (alpha + beta) // 2
                alpha = max(score - delta, -INF)
                if self.time_manager is not None:
                    self.time_manager.notify_fail_low()
            elif score >= beta:
                beta = min(score + delta, INF)
            else:
                return score
            delta += delta // 2 + 10
            if delta > 1000:
                alpha, beta = -INF, INF

    # ------------------------------------------------------------------
    # Interface principal
    # ------------------------------------------------------------------
    def _prepare_root(self, board: chess.Board) -> None:
        self.board = board.copy(stack=False)
        # Chaves das posições anteriores da partida (para detectar repetição)
        keys = []
        b = board.copy()
        back = min(len(b.move_stack), b.halfmove_clock)
        for _ in range(back):
            b.pop()
            keys.append(position_key(b))
        keys.reverse()
        self.hash_stack = keys
        self.root_turn = board.turn
        self.nodes = 0
        self.seldepth = 0
        self.killers = [[-1, -1] for _ in range(MAX_PLY + 2)]
        self.move_stack_ply = [None] * (MAX_PLY + 2)
        self.eval_stack = [0] * (MAX_PLY + 2)
        self.pv_table = [[] for _ in range(MAX_PLY + 2)]
        for c in (0, 1):
            h = self.history[c]
            for i in range(4096):
                h[i] //= 2  # envelhecimento do histórico entre lances
        self.tt_age = (self.tt_age + 1) & 0xFFFF

    def search_best_move(
        self,
        board: chess.Board,
        time_manager: TimeManager,
        max_depth: int = 64,
        use_book: bool = True,
        use_tb: bool = True,
        stop_event: threading.Event | None = None,
        verbose: bool = True,
        infinite: bool = False,
        max_nodes: int = 0,
        info_callback=None,
    ) -> SearchResult:
        """
        Seleção completa do lance:
        1. Livro de aberturas (até 10 meios-lances - FAQ Item 3)
        2. Tablebases Syzygy na raiz (até 5 peças - FAQ Item 4)
        3. Iterative Deepening + Aspiration + Negamax/PVS com gestão dinâmica de tempo
        """
        with self._lock:
            return self._search_root(
                board, time_manager, max_depth, use_book, use_tb, stop_event,
                verbose, infinite, max_nodes, info_callback,
            )

    def _search_root(self, board, time_manager, max_depth, use_book, use_tb, stop_event,
                     verbose, infinite, max_nodes, info_callback) -> SearchResult:
        self.time_manager = time_manager
        self.stop_event = stop_event
        legal_moves = list(board.legal_moves)
        if not legal_moves:
            raise ValueError("Não existem lances legais na posição atual.")

        time_manager.allocate_time(board, len(legal_moves))
        if infinite:
            self.hard_deadline = 0.0
        else:
            self.hard_deadline = time_manager.start_time + time_manager.hard_limit
        self.max_nodes = max_nodes

        # 1. Livro de aberturas
        if use_book:
            book_move = self.book_tb.get_book_move(board)
            if book_move is not None:
                board.push(book_move)
                score_cp = -evaluate(board)
                board.pop()
                elapsed = time_manager.elapsed()
                if verbose:
                    print(f"  [LIVRO DE ABERTURAS] Lance teórico encontrado: {book_move.uci()} ({elapsed:.3f}s)")
                return SearchResult(book_move, score_cp, 1, 1, elapsed, [book_move.uci()], "BOOK", 0, [book_move])

        # 2. Tablebases na raiz
        self.tb_pieces = self.book_tb.max_pieces() if use_tb else 0
        if use_tb:
            tb_move, tb_score = self.book_tb.get_syzygy_move(board)
            if tb_move is not None and tb_score is not None:
                elapsed = time_manager.elapsed()
                if verbose:
                    print(f"  [TABLEBASE 5 PEÇAS] Lance perfeito encontrado: {tb_move.uci()} ({elapsed:.3f}s)")
                return SearchResult(tb_move, tb_score, 1, 1, elapsed, [tb_move.uci()], "SYZYGY_TB", 0, [tb_move])

        # Lance único: responde na hora (sem gastar relógio)
        if len(legal_moves) == 1 and not infinite:
            only = legal_moves[0]
            board.push(only)
            score_cp = -evaluate_board(board, check_terminal=True)
            board.pop()
            elapsed = time_manager.elapsed()
            return SearchResult(only, score_cp, 1, 1, elapsed, [only.uci()], "SEARCH", 0, [only])

        # 3. Iterative Deepening
        self._prepare_root(board)
        best_move = legal_moves[0]
        best_score = 0
        best_pv: list[chess.Move] = [best_move]
        completed_depth = 0
        prev_score: int | None = None

        for depth in range(1, max_depth + 1):
            self.iter_best_move = None
            self.iter_best_score = -INF
            self.seldepth = 0
            try:
                score = self._aspiration(depth, prev_score)
            except SearchTimeout:
                # Tempo acabou no meio da iteração: aproveita o melhor lance JÁ PROVADO nesta
                # profundidade (se houver); senão fica com o da última profundidade completa.
                if self.iter_best_move is not None and completed_depth > 0:
                    if self.iter_best_move != best_move:
                        best_move = self.iter_best_move
                        best_score = self.iter_best_score
                        pv = self.pv_table[0]
                        best_pv = list(pv) if pv and pv[0] == best_move else [best_move]
                break

            completed_depth = depth
            pv = self.pv_table[0]
            new_best = pv[0] if pv else (self.iter_best_move or best_move)
            move_changed = new_best != best_move
            score_drop = (prev_score - score) if prev_score is not None else 0
            best_move = new_best
            best_score = score
            best_pv = list(pv) if pv and pv[0] == best_move else [best_move]
            prev_score = score

            elapsed = time_manager.elapsed()
            if info_callback is not None:
                info_callback(depth, self.seldepth, score, self.nodes, elapsed, best_pv)
            if verbose:
                nps = int(self.nodes / max(0.001, elapsed))
                pv_preview = " ".join(m.uci() for m in best_pv[:8])
                print(
                    f"  info depth {depth:>2}/{self.seldepth:<2} | score {score:>+6d} cp | "
                    f"nodes {self.nodes:>8d} ({nps:>6d} n/s) | time {elapsed:>6.2f}s | pv {pv_preview}"
                )

            if infinite:
                continue
            # Mate forçado já encontrado e confirmado: não precisa pensar mais
            if abs(score) >= MATE_THRESHOLD and depth >= (MATE_SCORE - abs(score)) + 2:
                break
            time_manager.adjust_for_instability(move_changed, score_drop, depth)
            if time_manager.should_stop_iteration():
                break

        elapsed = time_manager.elapsed()
        # Segurança extra: o lance devolvido é SEMPRE legal na posição real
        if best_move not in legal_moves:
            best_move = legal_moves[0]
            best_pv = [best_move]
        return SearchResult(
            best_move=best_move,
            score_cp=best_score,
            depth=max(1, completed_depth),
            nodes=max(1, self.nodes),
            elapsed_sec=elapsed,
            pv=[m.uci() for m in best_pv],
            source="SEARCH",
            seldepth=self.seldepth,
            pv_moves=list(best_pv),
        )
