"""
Módulo de Avaliação Heurística (Função de Avaliação) - Autoria Própria
Chess Challenge III - ICEV 2026

Transforma qualquer posição em uma nota em centipawns (100 = 1 peão), sempre da
perspectiva de quem tem a vez de jogar (convenção do Negamax).

Destaques técnicos para a Arguição da Banca:
1. Tapered Evaluation: cada termo tem um valor de Meio-Jogo (MG) e outro de Final (EG).
   A fase do jogo (24 = todas as peças, 0 = só reis e peões) interpola os dois.
2. Escores "empacotados" S(mg, eg): MG e EG ficam no MESMO inteiro (mg + eg * 2^16),
   então cada termo custa UMA soma em vez de duas (técnica usada por motores como o
   Stockfish). Só no final o inteiro é "desempacotado".
3. Material + Tabelas Posicionais (PST) no estilo PeSTO.
4. Mobilidade das peças (casas seguras atacadas, fora do alcance de peões inimigos).
5. Segurança do Rei: "unidades de ataque" das peças inimigas na zona do rei passam por
   uma tabela NÃO-LINEAR (2 atacantes são muito mais perigosos que o dobro de 1),
   além de escudo de peões e colunas abertas perto do rei.
6. Estrutura de Peões com CACHE (Pawn Hash Table): passados, isolados, dobrados e
   conectados. Como os peões mudam pouco na árvore, ~95% das consultas vêm do cache.
7. Peões passados no final: distância dos reis até a casa de promoção e caminho livre.
8. Conhecimento de finais: material insuficiente (K, KN, KB, KNN), finais sem peões
   com pouca vantagem (ex: Torre x Bispo) e bispos de cores opostas são "escalados"
   para perto do empate; Mop-Up empurra o rei sozinho para a borda/canto correto
   (inclusive o canto da cor do bispo no mate de Bispo + Cavalo).
"""

import chess
from chess import (
    BB_SQUARES,
    BB_FILES,
    BB_RANKS,
    BB_KNIGHT_ATTACKS,
    BB_KING_ATTACKS,
    BB_DIAG_ATTACKS,
    BB_DIAG_MASKS,
    BB_RANK_ATTACKS,
    BB_RANK_MASKS,
    BB_FILE_ATTACKS,
    BB_FILE_MASKS,
    BB_LIGHT_SQUARES,
    BB_DARK_SQUARES,
    WHITE,
    BLACK,
)

# ==============================================================================
# CONSTANTES GERAIS
# ==============================================================================
MATE_SCORE = 30000
MATE_THRESHOLD = 29000
DRAW_SCORE = 0

BB_ALL = 0xFFFF_FFFF_FFFF_FFFF
BB_FILE_A = BB_FILES[0]
BB_FILE_H = BB_FILES[7]
MAX_PHASE = 24


def S(mg: int, eg: int) -> int:
    """Empacota (MG, EG) em um único inteiro: mg + eg * 65536."""
    return mg + (eg << 16)


def unpack(packed: int) -> tuple[int, int]:
    """Desempacota um escore S(mg, eg) de volta em (mg, eg)."""
    mg = ((packed + 0x8000) & 0xFFFF) - 0x8000
    eg = (packed - mg) >> 16
    return mg, eg


# Valores de material (MG, EG) – escala PeSTO
MG_PIECE_VALUES = {
    chess.PAWN: 82,
    chess.KNIGHT: 337,
    chess.BISHOP: 365,
    chess.ROOK: 477,
    chess.QUEEN: 1025,
    chess.KING: 0,
}
EG_PIECE_VALUES = {
    chess.PAWN: 94,
    chess.KNIGHT: 281,
    chess.BISHOP: 297,
    chess.ROOK: 512,
    chess.QUEEN: 936,
    chess.KING: 0,
}

# Valores simples usados pela busca (MVV-LVA e SEE – Static Exchange Evaluation)
SEE_VALUES = [0, 100, 320, 330, 500, 950, 20000]  # índice = tipo de peça (1..6)

# ==============================================================================
# TABELAS POSICIONAIS (PST) – visualmente da fileira 8 (topo) até a fileira 1,
# do ponto de vista das Brancas (estilo PeSTO).
# ==============================================================================
MG_PAWN_TABLE = [
      0,   0,   0,   0,   0,   0,   0,   0,
     98, 134,  61,  95,  68, 126,  34, -11,
     -6,   7,  26,  31,  65,  56,  25, -20,
    -14,  13,   6,  21,  23,  12,  17, -23,
    -27,  -2,  -5,  12,  17,   6,  10, -25,
    -26,  -4,  -4, -10,   3,   3,  33, -12,
    -35,  -1, -20, -23, -15,  24,  38, -22,
      0,   0,   0,   0,   0,   0,   0,   0,
]
EG_PAWN_TABLE = [
      0,   0,   0,   0,   0,   0,   0,   0,
    178, 173, 158, 134, 147, 132, 165, 187,
     94, 100,  85,  67,  56,  53,  82,  84,
     32,  24,  13,   5,  -2,   4,  17,  17,
     13,   9,  -3,  -7,  -7,  -8,   3,  -1,
      4,   7,  -6,   1,   0,  -5,  -1,  -8,
     13,   8,   8,  10,  13,   0,   2,  -7,
      0,   0,   0,   0,   0,   0,   0,   0,
]
MG_KNIGHT_TABLE = [
   -167, -89, -34, -49,  61, -97, -15, -107,
    -73, -41,  72,  36,  23,  62,   7,  -17,
    -47,  60,  37,  65,  84, 129,  73,   44,
     -9,  17,  19,  53,  37,  69,  18,   22,
    -13,   4,  16,  13,  28,  19,  21,   -8,
    -23,  -9,  12,  10,  19,  17,  25,  -16,
    -29, -53, -12,  -3,  -1,  18, -14,  -19,
   -105, -21, -58, -33, -17, -28, -19,  -23,
]
EG_KNIGHT_TABLE = [
    -58, -38, -13, -28, -31, -27, -63, -99,
    -25,  -8, -25,  -2,  -9, -25, -24, -52,
    -24, -20,  10,   9,  -1,  -9, -19, -41,
    -17,   3,  22,  22,  22,  11,   8, -18,
    -18,  -6,  16,  25,  16,  17,   4, -18,
    -23,  -3,  -1,  15,  10,  -3, -20, -22,
    -42, -20, -10,  -5,  -2, -20, -23, -44,
    -29, -51, -23, -15, -22, -18, -50, -64,
]
MG_BISHOP_TABLE = [
    -29,   4, -82, -37, -25, -42,   7,  -8,
    -26,  16, -18, -13,  30,  59,  18, -47,
    -16,  37,  43,  40,  35,  50,  37,  -2,
     -4,   5,  19,  50,  37,  37,   7,  -2,
     -6,  13,  13,  26,  34,  12,  10,   4,
      0,  15,  15,  15,  14,  27,  18,  10,
      4,  15,  16,   0,   7,  21,  33,   1,
    -33,  -3, -14, -21, -13, -12, -39, -21,
]
EG_BISHOP_TABLE = [
    -14, -21, -11,  -8, -7,  -9, -17, -24,
     -8,  -4,   7, -12, -3, -13,  -4, -14,
      2,  -8,   0,  -1, -2,   6,   0,   4,
     -3,   9,  12,   9, 14,  10,   3,   2,
     -6,   3,  13,  19,  7,  10,  -3,  -9,
    -12,  -3,   8,  10, 13,   3,  -7, -15,
    -14, -18,  -7,  -1,  4,  -9, -15, -27,
    -23,  -9, -23,  -5, -9, -16,  -5, -17,
]
MG_ROOK_TABLE = [
     32,  42,  32,  51, 63,  9,  31,  43,
     27,  32,  58,  62, 80, 67,  26,  44,
     -5,  19,  26,  36, 17, 45,  61,  16,
    -24, -11,   7,  26, 24, 35,  -8, -20,
    -36, -26, -12,  -1,  9, -7,   6, -23,
    -45, -25, -16, -17,  3,  0,  -5, -33,
    -44, -16, -20,  -9, -1, 11,  -6, -71,
    -19, -13,   1,  17, 16,  7, -37, -26,
]
EG_ROOK_TABLE = [
     13, 10, 18, 15, 12,  12,   8,   5,
     11, 13, 13, 11, -3,   3,   8,   3,
      7,  7,  7,  5,  4,  -3,  -5,  -3,
      4,  3, 13,  1,  2,   1,  -1,   2,
      3,  5,  8,  4, -5,  -6,  -8, -11,
     -4,  0, -5, -1, -7, -12,  -8, -16,
     -6, -6,  0,  2, -9,  -9, -11,  -3,
     -9,  2,  3, -1, -5, -13,   4, -20,
]
MG_QUEEN_TABLE = [
    -28,   0,  29,  12,  59,  44,  43,  45,
    -24, -39,  -5,   1, -16,  57,  28,  54,
    -13, -17,   7,   8,  29,  56,  47,  57,
    -27, -27, -16, -16,  -1,  17,  -2,   1,
     -9, -26,  -9, -10,  -2,  -4,   3,  -3,
    -14,   2, -11,  -2,  -5,   2,  14,   5,
    -35,  -8,  11,   2,   8,  15,  -3,   1,
     -1, -18,  -9,  10, -15, -25, -31, -50,
]
EG_QUEEN_TABLE = [
     -9,  22,  22,  27,  27,  19,  10,  20,
    -17,  20,  32,  41,  58,  25,  30,   0,
    -20,   6,   9,  49,  47,  35,  19,   9,
      3,  22,  24,  45,  57,  40,  57,  36,
    -18,  28,  19,  47,  31,  34,  39,  23,
    -16, -27,  15,   6,   9,  17,  10,   5,
    -22, -23, -30, -16, -16, -23, -36, -32,
    -33, -28, -22, -43,  -5, -32, -20, -41,
]
MG_KING_TABLE = [
    -65,  23,  16, -15, -56, -34,   2,  13,
     29,  -1, -20,  -7,  -8,  -4, -38, -29,
     -9,  24,   2, -16, -20,   6,  22, -22,
    -17, -20, -12, -27, -30, -25, -14, -36,
    -49,  -1, -27, -39, -46, -44, -33, -51,
    -14, -14, -22, -46, -44, -30, -15, -27,
      1,   7,  -8, -64, -43, -16,   9,   8,
    -15,  36,  12, -54,   8, -28,  24,  14,
]
EG_KING_TABLE = [
    -74, -35, -18, -18, -11,  15,   4, -17,
    -12,  17,  14,  17,  17,  38,  23,  11,
     10,  17,  23,  15,  20,  45,  44,  13,
     -8,  22,  24,  27,  26,  33,  26,   3,
    -18,  -4,  21,  24,  27,  23,   9, -11,
    -19,  -3,  11,  21,  23,  16,   7,  -9,
    -27, -11,   4,  13,  14,   4,  -5, -17,
    -53, -34, -21, -11, -28, -14, -24, -43,
]

RAW_MG_TABLES = {
    chess.PAWN: MG_PAWN_TABLE, chess.KNIGHT: MG_KNIGHT_TABLE, chess.BISHOP: MG_BISHOP_TABLE,
    chess.ROOK: MG_ROOK_TABLE, chess.QUEEN: MG_QUEEN_TABLE, chess.KING: MG_KING_TABLE,
}
RAW_EG_TABLES = {
    chess.PAWN: EG_PAWN_TABLE, chess.KNIGHT: EG_KNIGHT_TABLE, chess.BISHOP: EG_BISHOP_TABLE,
    chess.ROOK: EG_ROOK_TABLE, chess.QUEEN: EG_QUEEN_TABLE, chess.KING: EG_KING_TABLE,
}

# Tabelas unificadas (material + PST) já empacotadas, indexadas por [tipo][casa].
# Em python-chess a casa 0 é A1; nas matrizes visuais o índice 0 é A8, então
# "sq ^ 56" espelha verticalmente para as Brancas. Para as Pretas o índice visual
# já coincide com a casa espelhada.
PST_WHITE = [None] * 7
PST_BLACK = [None] * 7


def build_pst_tables() -> None:
    """(Re)constrói as tabelas material+PST empacotadas (usado também pelo Texel Tuning)."""
    for pt in range(1, 7):
        PST_WHITE[pt] = [
            S(MG_PIECE_VALUES[pt] + RAW_MG_TABLES[pt][sq ^ 56], EG_PIECE_VALUES[pt] + RAW_EG_TABLES[pt][sq ^ 56])
            for sq in range(64)
        ]
        PST_BLACK[pt] = [
            S(MG_PIECE_VALUES[pt] + RAW_MG_TABLES[pt][sq], EG_PIECE_VALUES[pt] + RAW_EG_TABLES[pt][sq])
            for sq in range(64)
        ]


build_pst_tables()

# ==============================================================================
# PESOS DOS TERMOS HEURÍSTICOS (todos empacotados em S(mg, eg))
# ==============================================================================
BISHOP_PAIR = S(30, 50)
ROOK_OPEN_FILE = S(25, 12)
ROOK_SEMI_OPEN_FILE = S(12, 8)
ROOK_ON_SEVENTH = S(10, 25)
TEMPO_BONUS = 10
THREAT_BY_PAWN = S(45, 30)     # peça (não-peão) atacada por peão inimigo
HANGING_PIECE = S(25, 15)      # peça atacada e sem nenhum defensor

PAWN_ISOLATED = S(-10, -13)
PAWN_DOUBLED = S(-10, -22)
# Bônus por fileira relativa (0..7) – peões passados (complementa a PST) e conectados
PASSED_BONUS = [S(0, 0), S(0, 5), S(3, 10), S(8, 20), S(18, 36), S(32, 62), S(55, 100), S(0, 0)]
CONNECTED_BONUS = [S(0, 0), S(2, 0), S(4, 3), S(6, 5), S(11, 10), S(20, 22), S(32, 36), S(0, 0)]
# Final: distância dos reis à casa à frente do peão passado, ponderada pela fileira
PASSED_KING_DIST_OWN = -2
PASSED_KING_DIST_ENEMY = 5
PASSED_RANK_FACTOR = [0, 0, 0, 1, 2, 3, 4, 0]
PASSED_FREE_PATH = [0, 0, 0, 4, 8, 15, 25, 0]  # em EG, casa da frente vazia

# Mobilidade (número de casas seguras) – estilo Fruit/Toga, valor linear por casa
MOB_OFFSET = {chess.KNIGHT: 4, chess.BISHOP: 6, chess.ROOK: 7, chess.QUEEN: 13}
MOB_WEIGHT = {chess.KNIGHT: S(4, 4), chess.BISHOP: S(5, 5), chess.ROOK: S(2, 4), chess.QUEEN: S(1, 2)}
MOB_TABLE = [None] * 7
for _pt in (chess.KNIGHT, chess.BISHOP, chess.ROOK, chess.QUEEN):
    _mg, _eg = unpack(MOB_WEIGHT[_pt])
    MOB_TABLE[_pt] = [S((_n - MOB_OFFSET[_pt]) * _mg, (_n - MOB_OFFSET[_pt]) * _eg) for _n in range(29)]

# Segurança do Rei: peso de cada tipo de atacante por casa atacada na zona do rei
KING_ATTACK_WEIGHT = [0, 0, 2, 2, 3, 5, 0]
# Tabela não-linear de perigo (cresce quase quadraticamente e satura em ~5 peões)
KING_SAFETY_TABLE = [
      0,   0,   1,   2,   3,   5,   7,   9,  12,  15,
     18,  22,  26,  30,  35,  39,  44,  50,  56,  62,
     68,  75,  82,  85,  89,  97, 105, 113, 122, 131,
    140, 150, 169, 180, 191, 202, 213, 225, 237, 248,
    260, 272, 283, 295, 307, 319, 330, 342, 354, 366,
    377, 389, 401, 412, 424, 436, 448, 459, 471, 483,
    494, 500, 500, 500, 500, 500, 500, 500, 500, 500,
] + [500] * 30
KING_SAFETY_PERCENT = 75    # % da tabela aplicada (o PST do rei já cobre parte do risco)
KING_SHIELD_NEAR = S(12, 0)  # peão na fileira imediatamente à frente do rei
KING_SHIELD_FAR = S(6, 0)    # peão duas fileiras à frente
KING_OPEN_FILE = S(-14, 0)   # coluna (do rei ou vizinha) sem peão próprio

# ==============================================================================
# MÁSCARAS DE BITBOARD PRÉ-CALCULADAS
# ==============================================================================
FILE_MASKS = [BB_FILES[f] for f in range(8)]
ADJACENT_FILE_MASKS = [0] * 8
for _f in range(8):
    _m = 0
    if _f > 0:
        _m |= BB_FILES[_f - 1]
    if _f < 7:
        _m |= BB_FILES[_f + 1]
    ADJACENT_FILE_MASKS[_f] = _m

PASSED_MASK_W = [0] * 64   # casas à frente (mesma coluna e vizinhas) – Brancas
PASSED_MASK_B = [0] * 64
FRONT_SPAN_W = [0] * 64    # casas à frente na mesma coluna
FRONT_SPAN_B = [0] * 64
for _sq in range(64):
    _f, _r = _sq & 7, _sq >> 3
    _files = FILE_MASKS[_f] | ADJACENT_FILE_MASKS[_f]
    _up = 0
    for _nr in range(_r + 1, 8):
        _up |= BB_RANKS[_nr]
    _down = 0
    for _nr in range(0, _r):
        _down |= BB_RANKS[_nr]
    PASSED_MASK_W[_sq] = _files & _up
    PASSED_MASK_B[_sq] = _files & _down
    FRONT_SPAN_W[_sq] = FILE_MASKS[_f] & _up
    FRONT_SPAN_B[_sq] = FILE_MASKS[_f] & _down

# Zona do rei: a casa do rei + casas vizinhas
KING_ZONE = [BB_KING_ATTACKS[_sq] | BB_SQUARES[_sq] for _sq in range(64)]

# Escudo de peões (1 e 2 fileiras à frente do rei, colunas do rei e vizinhas)
SHIELD_NEAR_W = [0] * 64
SHIELD_FAR_W = [0] * 64
SHIELD_NEAR_B = [0] * 64
SHIELD_FAR_B = [0] * 64
for _sq in range(64):
    _f, _r = _sq & 7, _sq >> 3
    _files = FILE_MASKS[_f] | ADJACENT_FILE_MASKS[_f]
    if _r + 1 <= 7:
        SHIELD_NEAR_W[_sq] = _files & BB_RANKS[_r + 1]
    if _r + 2 <= 7:
        SHIELD_FAR_W[_sq] = _files & BB_RANKS[_r + 2]
    if _r - 1 >= 0:
        SHIELD_NEAR_B[_sq] = _files & BB_RANKS[_r - 1]
    if _r - 2 >= 0:
        SHIELD_FAR_B[_sq] = _files & BB_RANKS[_r - 2]

# Distância de Chebyshev (número de lances de rei) entre duas casas
DISTANCE = [[max(abs((a & 7) - (b & 7)), abs((a >> 3) - (b >> 3))) for b in range(64)] for a in range(64)]

# Distância até o centro (Manhattan) – usada no Mop-Up
CENTER_DISTANCE = [0] * 64
for _sq in range(64):
    _f, _r = _sq & 7, _sq >> 3
    CENTER_DISTANCE[_sq] = max(3 - _f, _f - 4, 0) + max(3 - _r, _r - 4, 0)

# Distância até o canto de cor clara (a8/h1) e escura (a1/h8) – mate de Bispo + Cavalo
CORNER_DIST_DARK = [min(DISTANCE[_sq][chess.A1], DISTANCE[_sq][chess.H8]) for _sq in range(64)]
CORNER_DIST_LIGHT = [min(DISTANCE[_sq][chess.A8], DISTANCE[_sq][chess.H1]) for _sq in range(64)]


def _pawn_attacks_white(wp: int) -> int:
    return ((wp << 9) & ~BB_FILE_A | (wp << 7) & ~BB_FILE_H) & BB_ALL


def _pawn_attacks_black(bp: int) -> int:
    return ((bp >> 7) & ~BB_FILE_A) | ((bp >> 9) & ~BB_FILE_H)


# ==============================================================================
# CACHE DA ESTRUTURA DE PEÕES (PAWN HASH TABLE)
# ==============================================================================
_PAWN_CACHE: dict[int, tuple[int, int, int]] = {}
_PAWN_CACHE_MAX = 200_000


def _evaluate_pawns(wp: int, bp: int) -> tuple[int, int, int]:
    """
    Avalia somente a estrutura de peões (depende apenas dos bitboards de peões,
    por isso pode ser guardada em cache). Inclui material + PST dos peões.
    Retorna (escore_empacotado_brancas_menos_pretas, passados_brancos, passados_pretos).
    """
    score = 0
    w_passed = 0
    b_passed = 0
    pst_w = PST_WHITE[chess.PAWN]
    pst_b = PST_BLACK[chess.PAWN]

    bb = wp
    while bb:
        lsb = bb & -bb
        sq = lsb.bit_length() - 1
        f = sq & 7
        r = sq >> 3
        score += pst_w[sq]
        if not (PASSED_MASK_W[sq] & bp):
            w_passed |= lsb
            score += PASSED_BONUS[r]
        if not (ADJACENT_FILE_MASKS[f] & wp):
            score += PAWN_ISOLATED
        if FRONT_SPAN_W[sq] & wp:
            score += PAWN_DOUBLED
        # Conectado: defendido por peão (casas atrás na diagonal) ou lado a lado (falange)
        side = ((lsb << 1) & ~BB_FILE_A) | ((lsb >> 1) & ~BB_FILE_H)
        if (side & wp) or (((side >> 8) & wp)):
            score += CONNECTED_BONUS[r]
        bb ^= lsb

    bb = bp
    while bb:
        lsb = bb & -bb
        sq = lsb.bit_length() - 1
        f = sq & 7
        r = 7 - (sq >> 3)
        score -= pst_b[sq]
        if not (PASSED_MASK_B[sq] & wp):
            b_passed |= lsb
            score -= PASSED_BONUS[r]
        if not (ADJACENT_FILE_MASKS[f] & bp):
            score -= PAWN_ISOLATED
        if FRONT_SPAN_B[sq] & bp:
            score -= PAWN_DOUBLED
        side = ((lsb << 1) & ~BB_FILE_A) | ((lsb >> 1) & ~BB_FILE_H)
        if (side & bp) or ((side << 8) & bp):
            score -= CONNECTED_BONUS[r]
        bb ^= lsb

    return score, w_passed, b_passed


def clear_eval_cache() -> None:
    _PAWN_CACHE.clear()


# ==============================================================================
# FUNÇÃO DE AVALIAÇÃO PRINCIPAL
# ==============================================================================
def evaluate(board: chess.Board) -> int:
    """
    Avaliação estática rápida (usada milhões de vezes pela busca).
    Retorna a nota em centipawns do ponto de vista de quem joga.
    """
    pawns = board.pawns
    knights = board.knights
    bishops = board.bishops
    rooks = board.rooks
    queens = board.queens
    kings = board.kings
    white = board.occupied_co[WHITE]
    black = board.occupied_co[BLACK]
    occ = white | black

    # --- Material insuficiente / finais sem peões e sem peças maiores ---
    minors = knights | bishops
    if not (pawns | rooks | queens):
        w_minor = (minors & white).bit_count()
        b_minor = (minors & black).bit_count()
        if w_minor <= 1 and b_minor <= 1:
            return DRAW_SCORE  # K x K, KN x K, KB x K, KB x KN... (não há mate forçado)

    wp = pawns & white
    bp = pawns & black

    # --- 1. Estrutura de peões (cache) ---
    pkey = (wp << 64) | bp
    pentry = _PAWN_CACHE.get(pkey)
    if pentry is None:
        if len(_PAWN_CACHE) >= _PAWN_CACHE_MAX:
            _PAWN_CACHE.clear()
        pentry = _evaluate_pawns(wp, bp)
        _PAWN_CACHE[pkey] = pentry
    score, w_passed, b_passed = pentry

    w_pawn_att = _pawn_attacks_white(wp)
    b_pawn_att = _pawn_attacks_black(bp)

    wk = (kings & white).bit_length() - 1
    bk = (kings & black).bit_length() - 1
    w_zone = KING_ZONE[wk]
    b_zone = KING_ZONE[bk]

    # Área de mobilidade: casas que não têm peão/rei próprio e não são atacadas por peão inimigo
    w_area = ~(wp | (kings & white) | b_pawn_att) & BB_ALL
    b_area = ~(bp | (kings & black) | w_pawn_att) & BB_ALL

    # Mapas de ataque acumulados (para ameaças / peças penduradas)
    w_att = w_pawn_att | BB_KING_ATTACKS[wk]
    b_att = b_pawn_att | BB_KING_ATTACKS[bk]

    # Ataque ao rei: (unidades, número de atacantes)
    w_units = 0
    w_attackers = 0
    b_units = 0
    b_attackers = 0

    # --- 2. Peças: PST + Mobilidade + Ataques na zona do rei ---
    pst_wn = PST_WHITE[chess.KNIGHT]
    pst_bn = PST_BLACK[chess.KNIGHT]
    mob_n = MOB_TABLE[chess.KNIGHT]
    bb = knights & white
    while bb:
        lsb = bb & -bb
        sq = lsb.bit_length() - 1
        att = BB_KNIGHT_ATTACKS[sq]
        score += pst_wn[sq] + mob_n[(att & w_area).bit_count()]
        w_att |= att
        z = att & b_zone
        if z:
            w_attackers += 1
            w_units += 2 * z.bit_count()
        bb ^= lsb
    bb = knights & black
    while bb:
        lsb = bb & -bb
        sq = lsb.bit_length() - 1
        att = BB_KNIGHT_ATTACKS[sq]
        score -= pst_bn[sq] + mob_n[(att & b_area).bit_count()]
        b_att |= att
        z = att & w_zone
        if z:
            b_attackers += 1
            b_units += 2 * z.bit_count()
        bb ^= lsb

    pst_wb = PST_WHITE[chess.BISHOP]
    pst_bb = PST_BLACK[chess.BISHOP]
    mob_b = MOB_TABLE[chess.BISHOP]
    bb = bishops & white
    while bb:
        lsb = bb & -bb
        sq = lsb.bit_length() - 1
        att = BB_DIAG_ATTACKS[sq][BB_DIAG_MASKS[sq] & occ]
        score += pst_wb[sq] + mob_b[(att & w_area).bit_count()]
        w_att |= att
        z = att & b_zone
        if z:
            w_attackers += 1
            w_units += 2 * z.bit_count()
        bb ^= lsb
    bb = bishops & black
    while bb:
        lsb = bb & -bb
        sq = lsb.bit_length() - 1
        att = BB_DIAG_ATTACKS[sq][BB_DIAG_MASKS[sq] & occ]
        score -= pst_bb[sq] + mob_b[(att & b_area).bit_count()]
        b_att |= att
        z = att & w_zone
        if z:
            b_attackers += 1
            b_units += 2 * z.bit_count()
        bb ^= lsb

    pst_wr = PST_WHITE[chess.ROOK]
    pst_br = PST_BLACK[chess.ROOK]
    mob_r = MOB_TABLE[chess.ROOK]
    bb = rooks & white
    while bb:
        lsb = bb & -bb
        sq = lsb.bit_length() - 1
        att = BB_RANK_ATTACKS[sq][BB_RANK_MASKS[sq] & occ] | BB_FILE_ATTACKS[sq][BB_FILE_MASKS[sq] & occ]
        score += pst_wr[sq] + mob_r[(att & w_area).bit_count()]
        w_att |= att
        fmask = FILE_MASKS[sq & 7]
        if not (wp & fmask):
            score += ROOK_SEMI_OPEN_FILE if (bp & fmask) else ROOK_OPEN_FILE
        if (sq >> 3) == 6 and ((bp & BB_RANKS[6]) or (bk >> 3) == 7):
            score += ROOK_ON_SEVENTH
        z = att & b_zone
        if z:
            w_attackers += 1
            w_units += 3 * z.bit_count()
        bb ^= lsb
    bb = rooks & black
    while bb:
        lsb = bb & -bb
        sq = lsb.bit_length() - 1
        att = BB_RANK_ATTACKS[sq][BB_RANK_MASKS[sq] & occ] | BB_FILE_ATTACKS[sq][BB_FILE_MASKS[sq] & occ]
        score -= pst_br[sq] + mob_r[(att & b_area).bit_count()]
        b_att |= att
        fmask = FILE_MASKS[sq & 7]
        if not (bp & fmask):
            score -= ROOK_SEMI_OPEN_FILE if (wp & fmask) else ROOK_OPEN_FILE
        if (sq >> 3) == 1 and ((wp & BB_RANKS[1]) or (wk >> 3) == 0):
            score -= ROOK_ON_SEVENTH
        z = att & w_zone
        if z:
            b_attackers += 1
            b_units += 3 * z.bit_count()
        bb ^= lsb

    pst_wq = PST_WHITE[chess.QUEEN]
    pst_bq = PST_BLACK[chess.QUEEN]
    mob_q = MOB_TABLE[chess.QUEEN]
    bb = queens & white
    while bb:
        lsb = bb & -bb
        sq = lsb.bit_length() - 1
        att = (
            BB_DIAG_ATTACKS[sq][BB_DIAG_MASKS[sq] & occ]
            | BB_RANK_ATTACKS[sq][BB_RANK_MASKS[sq] & occ]
            | BB_FILE_ATTACKS[sq][BB_FILE_MASKS[sq] & occ]
        )
        score += pst_wq[sq] + mob_q[(att & w_area).bit_count()]
        w_att |= att
        z = att & b_zone
        if z:
            w_attackers += 1
            w_units += 5 * z.bit_count()
        bb ^= lsb
    bb = queens & black
    while bb:
        lsb = bb & -bb
        sq = lsb.bit_length() - 1
        att = (
            BB_DIAG_ATTACKS[sq][BB_DIAG_MASKS[sq] & occ]
            | BB_RANK_ATTACKS[sq][BB_RANK_MASKS[sq] & occ]
            | BB_FILE_ATTACKS[sq][BB_FILE_MASKS[sq] & occ]
        )
        score -= pst_bq[sq] + mob_q[(att & b_area).bit_count()]
        b_att |= att
        z = att & w_zone
        if z:
            b_attackers += 1
            b_units += 5 * z.bit_count()
        bb ^= lsb

    # --- 3. Reis: PST + segurança ---
    score += PST_WHITE[chess.KING][wk] - PST_BLACK[chess.KING][bk]

    w_queens = queens & white
    b_queens = queens & black
    # Ataque das Brancas ao rei preto (só é perigoso com Dama e pelo menos 2 atacantes)
    if w_attackers >= 2 and w_queens:
        danger = KING_SAFETY_TABLE[w_units if w_units < 99 else 99]
        score += S(danger * KING_SAFETY_PERCENT // 100, 0)
    if b_attackers >= 2 and b_queens:
        danger = KING_SAFETY_TABLE[b_units if b_units < 99 else 99]
        score -= S(danger * KING_SAFETY_PERCENT // 100, 0)

    # Escudo de peões e colunas abertas perto do rei (só importa com Damas/peças em jogo)
    if b_queens:
        score += KING_SHIELD_NEAR * (SHIELD_NEAR_W[wk] & wp).bit_count()
        score += KING_SHIELD_FAR * (SHIELD_FAR_W[wk] & wp).bit_count()
        kf = wk & 7
        for f in (kf - 1, kf, kf + 1):
            if 0 <= f <= 7 and not (wp & FILE_MASKS[f]):
                score += KING_OPEN_FILE
    if w_queens:
        score -= KING_SHIELD_NEAR * (SHIELD_NEAR_B[bk] & bp).bit_count()
        score -= KING_SHIELD_FAR * (SHIELD_FAR_B[bk] & bp).bit_count()
        kf = bk & 7
        for f in (kf - 1, kf, kf + 1):
            if 0 <= f <= 7 and not (bp & FILE_MASKS[f]):
                score -= KING_OPEN_FILE

    # --- 3b. Ameaças: peças atacadas por peões e peças penduradas (sem defensor) ---
    w_pieces = white & ~pawns & ~kings
    b_pieces = black & ~pawns & ~kings
    score += THREAT_BY_PAWN * (w_pawn_att & b_pieces).bit_count()
    score -= THREAT_BY_PAWN * (b_pawn_att & w_pieces).bit_count()
    score += HANGING_PIECE * (w_att & b_pieces & ~b_att).bit_count()
    score -= HANGING_PIECE * (b_att & w_pieces & ~w_att).bit_count()

    # --- 4. Par de Bispos ---
    w_bishops = bishops & white
    b_bishops = bishops & black
    if w_bishops & (w_bishops - 1):
        score += BISHOP_PAIR
    if b_bishops & (b_bishops - 1):
        score -= BISHOP_PAIR

    # --- 5. Peões passados no final: reis próximos e caminho livre ---
    eg_extra = 0
    bb = w_passed
    while bb:
        lsb = bb & -bb
        sq = lsb.bit_length() - 1
        r = sq >> 3
        if r >= 3:
            front = sq + 8
            eg_extra += PASSED_RANK_FACTOR[r] * (
                PASSED_KING_DIST_ENEMY * DISTANCE[bk][front] + PASSED_KING_DIST_OWN * DISTANCE[wk][front]
            )
            if not (occ & BB_SQUARES[front]):
                eg_extra += PASSED_FREE_PATH[r]
        bb ^= lsb
    bb = b_passed
    while bb:
        lsb = bb & -bb
        sq = lsb.bit_length() - 1
        r = 7 - (sq >> 3)
        if r >= 3:
            front = sq - 8
            eg_extra -= PASSED_RANK_FACTOR[r] * (
                PASSED_KING_DIST_ENEMY * DISTANCE[wk][front] + PASSED_KING_DIST_OWN * DISTANCE[bk][front]
            )
            if not (occ & BB_SQUARES[front]):
                eg_extra -= PASSED_FREE_PATH[r]
        bb ^= lsb
    score += eg_extra << 16

    # --- 6. Interpolação MG/EG (Tapered Eval) ---
    phase = (minors.bit_count() + 2 * rooks.bit_count() + 4 * queens.bit_count())
    if phase > MAX_PHASE:
        phase = MAX_PHASE
    mg = ((score + 0x8000) & 0xFFFF) - 0x8000
    eg = (score - mg) >> 16
    tapered = mg * phase + eg * (MAX_PHASE - phase)
    # divisão truncada em direção ao zero (mantém a avaliação simétrica entre as cores)
    value = tapered // MAX_PHASE if tapered >= 0 else -((-tapered) // MAX_PHASE)

    # --- 7. Conhecimento de finais (escala para empate e Mop-Up) ---
    if phase <= 8:
        value = _endgame_adjust(board, value, wp, bp, white, black, wk, bk)

    if board.turn == WHITE:
        return value + TEMPO_BONUS
    return -value + TEMPO_BONUS


def _non_pawn_material(board: chess.Board, color_mask: int) -> int:
    return (
        320 * ((board.knights | board.bishops) & color_mask).bit_count()
        + 500 * (board.rooks & color_mask).bit_count()
        + 950 * (board.queens & color_mask).bit_count()
    )


def _scale(value: int, num: int, den: int) -> int:
    """Multiplica por num/den truncando em direção ao zero (simétrico para as duas cores)."""
    if value >= 0:
        return value * num // den
    return -((-value) * num // den)


def _endgame_adjust(board, value, wp, bp, white, black, wk, bk) -> int:
    """Ajustes de finais: escala lances empatados e aplica Mop-Up em finais ganhos sem peões."""
    if value == 0:
        return 0
    strong_white = value > 0
    strong_mask, weak_mask = (white, black) if strong_white else (black, white)
    strong_pawns = wp if strong_white else bp
    weak_pawns = bp if strong_white else wp
    strong_np = _non_pawn_material(board, strong_mask)
    weak_np = _non_pawn_material(board, weak_mask)

    # (a) Lado mais forte SEM peões: precisa de vantagem real de peças para ganhar
    if not strong_pawns:
        if strong_np <= 330:
            # Só um Bispo/Cavalo (ou nada): impossível forçar mate
            return _scale(value, 1, 16)
        only_knights = not ((board.bishops | board.rooks | board.queens) & strong_mask)
        if only_knights and (board.knights & strong_mask).bit_count() == 2 and not weak_np:
            return _scale(value, 1, 16)  # K+N+N x K: empate teórico
        if strong_np - weak_np <= 330:
            # Ex: Torre x Bispo, Torre+Bispo x Torre, Dama x Torre+Bispo... tendem ao empate
            value = _scale(value, 1, 4)

    # (b) Bispos de cores opostas (sem outras peças): tendência forte de empate
    if not (board.knights | board.rooks | board.queens):
        wb = board.bishops & white
        bb_ = board.bishops & black
        if wb.bit_count() == 1 and bb_.bit_count() == 1:
            if bool(wb & BB_LIGHT_SQUARES) != bool(bb_ & BB_LIGHT_SQUARES):
                value = _scale(value, 1, 2)

    # (c) Mop-Up: o lado fraco tem só o Rei e o forte tem material de mate
    #     -> empurra o rei sozinho para a borda e aproxima o nosso rei
    if not weak_pawns and not weak_np:
        s_rq = (board.rooks | board.queens) & strong_mask
        s_b = board.bishops & strong_mask
        s_n = board.knights & strong_mask
        bishop_pair = (s_b & BB_LIGHT_SQUARES) and (s_b & BB_DARK_SQUARES)
        if s_rq or (s_b and s_n) or bishop_pair:
            loser_k = bk if strong_white else wk
            winner_k = wk if strong_white else bk
            bonus = 400 + 10 * CENTER_DISTANCE[loser_k] + 6 * (7 - DISTANCE[loser_k][winner_k])
            if s_b and s_n and not s_rq and not bishop_pair:
                # K+B+N x K: o mate só sai no canto da COR do bispo
                if s_b & BB_DARK_SQUARES:
                    bonus += 25 * (7 - CORNER_DIST_DARK[loser_k])
                else:
                    bonus += 25 * (7 - CORNER_DIST_LIGHT[loser_k])
            value = value + bonus if strong_white else value - bonus

    return value


def evaluate_board(board: chess.Board, check_terminal: bool = False) -> int:
    """
    Interface compatível com a versão anterior (usada pelo painel 'avaliar').
    Se check_terminal=True, detecta mate/afogamento/empates antes de avaliar.
    """
    if check_terminal:
        if board.is_checkmate():
            return -MATE_SCORE
        if board.is_stalemate() or board.halfmove_clock >= 100 or board.is_repetition(3):
            return DRAW_SCORE
    if board.is_insufficient_material():
        return DRAW_SCORE
    return evaluate(board)
