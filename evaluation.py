"""
Módulo de Avaliação Heurística (Função de Avaliação) - Autoria Própria
Chess Challenge III - ICEV 2026

Este módulo transforma qualquer posição do tabuleiro em uma pontuação numérica
em centipawns (100 pontos = 1 peão de vantagem), sempre da perspectiva de quem
tem a vez de jogar (padrão para o algoritmo Negamax).

Destaques técnicos para a Arguição da Banca:
1. Tapered Evaluation (PeSTO): Interpola suavemente a avaliação entre o Meio-Jogo (MG)
   e o Final de Jogo (EG) de acordo com as peças restantes no tabuleiro.
2. Bitboard Masks Pré-Calculadas: Usa operações binárias de 64 bits (via python-chess)
   para detectar peões passados, colunas abertas, peões isolados/dobrados e escudo do rei
   em microssegundos.
3. Heurísticas de Fim de Jogo (Mop-Up): Força o rei adversário para o canto e aproxima
   o nosso rei quando há vantagem decisiva, evitando empates por repetição ou 50 lances.
"""

import chess

# Pontuação de Xeque-Mate e Empate
MATE_SCORE = 30000
MATE_THRESHOLD = 29000
DRAW_SCORE = 0

# Valores materiais no Meio-Jogo (MG) e no Final de Jogo (EG) (inspirados em PeSTO/Texel tuning)
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

# Pesos de fase do jogo para cada tipo de peça (Total inicial = 24)
PHASE_WEIGHTS = {
    chess.PAWN: 0,
    chess.KNIGHT: 1,
    chess.BISHOP: 1,
    chess.ROOK: 2,
    chess.QUEEN: 4,
}
MAX_PHASE = 24

# ==============================================================================
# TABELAS POSICIONAIS (PIECE-SQUARE TABLES - PST)
# Escritas visualmente da Fileira 8 (topo) até a Fileira 1 (base) para as Brancas.
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
    chess.PAWN: MG_PAWN_TABLE,
    chess.KNIGHT: MG_KNIGHT_TABLE,
    chess.BISHOP: MG_BISHOP_TABLE,
    chess.ROOK: MG_ROOK_TABLE,
    chess.QUEEN: MG_QUEEN_TABLE,
    chess.KING: MG_KING_TABLE,
}

RAW_EG_TABLES = {
    chess.PAWN: EG_PAWN_TABLE,
    chess.KNIGHT: EG_KNIGHT_TABLE,
    chess.BISHOP: EG_BISHOP_TABLE,
    chess.ROOK: EG_ROOK_TABLE,
    chess.QUEEN: EG_QUEEN_TABLE,
    chess.KING: EG_KING_TABLE,
}

# ==============================================================================
# PRÉ-COMPUTAÇÃO DE TABELAS E MÁSCARAS DE BITBOARD (PARA ALTA VELOCIDADE)
# ==============================================================================

# Tabelas unificadas (Material + PST) indexadas por [piece_type][square]
MG_TABLE_WHITE = {}
EG_TABLE_WHITE = {}
MG_TABLE_BLACK = {}
EG_TABLE_BLACK = {}

for pt in range(1, 7):
    mg_w, eg_w, mg_b, eg_b = [0] * 64, [0] * 64, [0] * 64, [0] * 64
    for sq in range(64):
        # Em python-chess, sq=0 é A1 e sq=56 é A8.
        # Nas matrizes visuais acima, o índice 0 é A8 e 56 é A1.
        # Portanto, sq ^ 56 espelha verticalmente a fileira para as Brancas.
        w_idx = sq ^ 56
        b_idx = sq  # Para as Pretas, a tabela visual já está espelhada verticalmente
        mg_w[sq] = MG_PIECE_VALUES[pt] + RAW_MG_TABLES[pt][w_idx]
        eg_w[sq] = EG_PIECE_VALUES[pt] + RAW_EG_TABLES[pt][w_idx]
        mg_b[sq] = MG_PIECE_VALUES[pt] + RAW_MG_TABLES[pt][b_idx]
        eg_b[sq] = EG_PIECE_VALUES[pt] + RAW_EG_TABLES[pt][b_idx]
    MG_TABLE_WHITE[pt] = mg_w
    EG_TABLE_WHITE[pt] = eg_w
    MG_TABLE_BLACK[pt] = mg_b
    EG_TABLE_BLACK[pt] = eg_b

# Máscaras de colunas e colunas adjacentes para estrutura de peões
FILE_MASKS = [chess.BB_FILES[f] for f in range(8)]
ADJACENT_FILE_MASKS = [0] * 8
for f in range(8):
    mask = 0
    if f > 0:
        mask |= chess.BB_FILES[f - 1]
    if f < 7:
        mask |= chess.BB_FILES[f + 1]
    ADJACENT_FILE_MASKS[f] = mask

# Máscaras para detecção instantânea de Peões Passados (Passed Pawns)
PASSED_PAWN_MASK_WHITE = [0] * 64
PASSED_PAWN_MASK_BLACK = [0] * 64
for sq in range(64):
    f = chess.square_file(sq)
    r = chess.square_rank(sq)
    front_files = FILE_MASKS[f] | ADJACENT_FILE_MASKS[f]

    # Para Brancas: todas as fileiras acima de r
    w_ranks = 0
    for nr in range(r + 1, 8):
        w_ranks |= chess.BB_RANKS[nr]
    PASSED_PAWN_MASK_WHITE[sq] = front_files & w_ranks

    # Para Pretas: todas as fileiras abaixo de r
    b_ranks = 0
    for nr in range(0, r):
        b_ranks |= chess.BB_RANKS[nr]
    PASSED_PAWN_MASK_BLACK[sq] = front_files & b_ranks

# Bônus de Peão Passado por fileira relativa (0 a 7)
PASSED_PAWN_BONUS_MG = [0, 5, 10, 20, 35, 60, 100, 0]
PASSED_PAWN_BONUS_EG = [0, 10, 20, 40, 70, 120, 200, 0]

# Distância do centro para cada casa (usada no Mop-Up Evaluation de final de jogo)
CENTER_MANHATTAN_DISTANCE = [0] * 64
for sq in range(64):
    f = chess.square_file(sq)
    r = chess.square_rank(sq)
    df = max(3 - f, f - 4, 0)
    dr = max(3 - r, r - 4, 0)
    CENTER_MANHATTAN_DISTANCE[sq] = df + dr

# Máscara de Escudo do Rei (casas imediatamente à frente do rei)
KING_SHIELD_MASK_WHITE = [0] * 64
KING_SHIELD_MASK_BLACK = [0] * 64
for sq in range(64):
    bb_king = chess.BB_SQUARES[sq]
    attacks = chess.BB_KING_ATTACKS[sq]
    r = chess.square_rank(sq)
    if r < 7:
        KING_SHIELD_MASK_WHITE[sq] = attacks & (chess.BB_RANKS[r] | chess.BB_RANKS[r + 1])
    if r > 0:
        KING_SHIELD_MASK_BLACK[sq] = attacks & (chess.BB_RANKS[r] | chess.BB_RANKS[r - 1])


def evaluate_board(board: chess.Board, check_terminal: bool = False) -> int:
    """
    Calcula a avaliação estática da posição em centipawns.
    Retorna um valor positivo se o jogador da vez (board.turn) estiver melhor,
    ou negativo se estiver pior.
    """
    if check_terminal:
        if board.is_checkmate():
            return -MATE_SCORE
        if (
            board.is_stalemate()
            or board.halfmove_clock >= 100
            or board.is_repetition(3)
        ):
            return DRAW_SCORE

    if board.is_insufficient_material():
        return DRAW_SCORE

    white_occ = board.occupied_co[chess.WHITE]
    black_occ = board.occupied_co[chess.BLACK]

    mg_white = 0
    eg_white = 0
    mg_black = 0
    eg_black = 0
    phase = 0

    # 1. Material + Tabelas Posicionais (PeSTO) + Fase do Jogo via Bitboards
    for pt in (chess.PAWN, chess.KNIGHT, chess.BISHOP, chess.ROOK, chess.QUEEN, chess.KING):
        pieces_bb = board.pieces_mask(pt, chess.WHITE)
        mg_tbl_w = MG_TABLE_WHITE[pt]
        eg_tbl_w = EG_TABLE_WHITE[pt]
        pw = PHASE_WEIGHTS.get(pt, 0)

        # Itera apenas sobre os bits 1 do bitboard das Brancas
        bb = pieces_bb
        while bb:
            lsb = bb & -bb
            sq = lsb.bit_length() - 1
            mg_white += mg_tbl_w[sq]
            eg_white += eg_tbl_w[sq]
            phase += pw
            bb ^= lsb

        # Itera apenas sobre os bits 1 do bitboard das Pretas
        pieces_bb_b = board.pieces_mask(pt, chess.BLACK)
        mg_tbl_b = MG_TABLE_BLACK[pt]
        eg_tbl_b = EG_TABLE_BLACK[pt]
        bb = pieces_bb_b
        while bb:
            lsb = bb & -bb
            sq = lsb.bit_length() - 1
            mg_black += mg_tbl_b[sq]
            eg_black += eg_tbl_b[sq]
            phase += pw
            bb ^= lsb

    if phase > MAX_PHASE:
        phase = MAX_PHASE

    # 2. Bônus de Par de Bispos (Bishop Pair)
    if (board.bishops & white_occ).bit_count() >= 2:
        mg_white += 30
        eg_white += 50
    if (board.bishops & black_occ).bit_count() >= 2:
        mg_black += 30
        eg_black += 50

    # 3. Estrutura de Peões: Peões Passados, Dobrados e Isolados
    w_pawns = board.pawns & white_occ
    b_pawns = board.pawns & black_occ

    # Peões Brancos
    bb = w_pawns
    while bb:
        lsb = bb & -bb
        sq = lsb.bit_length() - 1
        f = sq & 7
        r = sq >> 3

        # Peão Passado
        if (PASSED_PAWN_MASK_WHITE[sq] & b_pawns) == 0:
            mg_white += PASSED_PAWN_BONUS_MG[r]
            eg_white += PASSED_PAWN_BONUS_EG[r]

        # Peão Isolado (nenhum peão amigo nas colunas vizinhas)
        if (ADJACENT_FILE_MASKS[f] & w_pawns) == 0:
            mg_white -= 12
            eg_white -= 15

        bb ^= lsb

    # Peões Pretos
    bb = b_pawns
    while bb:
        lsb = bb & -bb
        sq = lsb.bit_length() - 1
        f = sq & 7
        r = 7 - (sq >> 3)  # Fileira relativa para as Pretas (0 a 7)

        # Peão Passado
        if (PASSED_PAWN_MASK_BLACK[sq] & w_pawns) == 0:
            mg_black += PASSED_PAWN_BONUS_MG[r]
            eg_black += PASSED_PAWN_BONUS_EG[r]

        # Peão Isolado
        if (ADJACENT_FILE_MASKS[f] & b_pawns) == 0:
            mg_black -= 12
            eg_black -= 15

        bb ^= lsb

    # Peões Dobrados por coluna
    for f in range(8):
        f_mask = FILE_MASKS[f]
        w_in_file = (w_pawns & f_mask).bit_count()
        if w_in_file > 1:
            mg_white -= 10 * (w_in_file - 1)
            eg_white -= 15 * (w_in_file - 1)

        b_in_file = (b_pawns & f_mask).bit_count()
        if b_in_file > 1:
            mg_black -= 10 * (b_in_file - 1)
            eg_black -= 15 * (b_in_file - 1)

    # 4. Torres em Colunas Abertas e Semi-Abertas
    w_rooks = board.rooks & white_occ
    bb = w_rooks
    while bb:
        lsb = bb & -bb
        sq = lsb.bit_length() - 1
        f_mask = FILE_MASKS[sq & 7]
        if (w_pawns & f_mask) == 0:
            if (b_pawns & f_mask) == 0:
                mg_white += 25  # Coluna totalmente aberta
                eg_white += 20
            else:
                mg_white += 14  # Coluna semi-aberta
                eg_white += 10
        bb ^= lsb

    b_rooks = board.rooks & black_occ
    bb = b_rooks
    while bb:
        lsb = bb & -bb
        sq = lsb.bit_length() - 1
        f_mask = FILE_MASKS[sq & 7]
        if (b_pawns & f_mask) == 0:
            if (w_pawns & f_mask) == 0:
                mg_black += 25
                eg_black += 20
            else:
                mg_black += 14
                eg_black += 10
        bb ^= lsb

    # 5. Segurança do Rei no Meio-Jogo (Escudo de Peões)
    w_king_sq = board.king(chess.WHITE)
    b_king_sq = board.king(chess.BLACK)

    if w_king_sq is not None and phase > 8:
        shield_w = (KING_SHIELD_MASK_WHITE[w_king_sq] & w_pawns).bit_count()
        mg_white += shield_w * 10

    if b_king_sq is not None and phase > 8:
        shield_b = (KING_SHIELD_MASK_BLACK[b_king_sq] & b_pawns).bit_count()
        mg_black += shield_b * 10

    # Interpolação Tapered Eval entre Meio-Jogo (MG) e Final (EG)
    mg_diff = mg_white - mg_black
    eg_diff = eg_white - eg_black
    eg_phase = MAX_PHASE - phase
    score = (mg_diff * phase + eg_diff * eg_phase) // MAX_PHASE

    # 6. Mop-Up Evaluation (Heurística de Mate em Finais Ganhos)
    if phase <= 6 and w_king_sq is not None and b_king_sq is not None:
        king_dist = chess.square_manhattan_distance(w_king_sq, b_king_sq)
        if score > 250:
            # Brancas ganhando no final: empurra o rei preto para a borda e aproxima o rei branco
            score += 12 * CENTER_MANHATTAN_DISTANCE[b_king_sq] + 5 * (14 - king_dist)
        elif score < -250:
            # Pretas ganhando no final: empurra o rei branco para a borda e aproxima o rei preto
            score -= 12 * CENTER_MANHATTAN_DISTANCE[w_king_sq] + 5 * (14 - king_dist)

    # Pequeno bônus de Tempo (Iniciativa de ter a vez de jogar)
    tempo_bonus = 10

    return (score + tempo_bonus) if board.turn == chess.WHITE else (-score + tempo_bonus)
