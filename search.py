"""
Módulo de Busca e Seleção de Lances - Autoria Própria
Chess Challenge III - ICEV 2026

Implementa a árvore de decisão completa do motor:
1. Iterative Deepening (Aprofundamento Iterativo)
2. Negamax com Poda Alpha-Beta e Principal Variation Search (PVS)
3. Tabela de Transposição (Zobrist Hashing) com flags EXACT, LOWERBOUND e UPPERBOUND
4. Quiescence Search (Busca de Quiescência) com Stand-Pat e Delta Pruning contra Efeito Horizonte
5. Ordenação de Lances (Move Ordering): TT Move -> Promoções -> Capturas (MVV-LVA) -> Killer Moves -> History Heuristic
6. Otimizações de Poda: Null Move Pruning (Poda de Lance Nulo), Late Move Reductions (LMR) e Check Extension
"""

from dataclasses import dataclass
import threading
import chess
import chess.polyglot

from evaluation import (
    evaluate_board,
    MG_PIECE_VALUES,
    MATE_SCORE,
    MATE_THRESHOLD,
    DRAW_SCORE,
)
from time_manager import TimeManager
from book_and_tb import BookAndTablebaseManager

# Constantes da Tabela de Transposição
TT_EXACT = 0
TT_LOWERBOUND = 1  # Beta Cutoff (score >= beta)
TT_UPPERBOUND = 2  # Falhou em superar Alpha (score <= alpha)

MAX_PLY = 64
MAX_Q_PLY = 8


class SearchTimeout(Exception):
    """Exceção interna disparada quando o limite máximo de tempo (hard_limit) é atingido."""
    pass


@dataclass
class TTEntry:
    depth: int
    score: int
    flag: int
    best_move: chess.Move | None


@dataclass
class SearchResult:
    best_move: chess.Move
    score_cp: int
    depth: int
    nodes: int
    elapsed_sec: float
    pv: list[str]
    source: str = "SEARCH"  # "SEARCH", "BOOK" ou "SYZYGY_TB"


class SearchEngine:
    def __init__(self, tt_max_entries: int = 250_000):
        self.tt: dict[int, TTEntry] = {}
        self.tt_max_entries = tt_max_entries

        # Estruturas de ordenação de lances quietos
        self.killer_moves: list[list[chess.Move | None]] = [
            [None, None] for _ in range(MAX_PLY)
        ]
        # history[cor][from_sq][to_sq]
        self.history: list[list[list[int]]] = [
            [[0] * 64 for _ in range(64)],
            [[0] * 64 for _ in range(64)],
        ]

        self.nodes = 0
        self.time_manager: TimeManager | None = None
        self.stop_event: threading.Event | None = None
        self.book_tb = BookAndTablebaseManager()

    def clear_cache(self) -> None:
        """Limpa a Tabela de Transposição e heurísticas históricas para uma nova partida."""
        self.tt.clear()
        self._reset_heuristics()

    def _reset_heuristics(self) -> None:
        for ply in range(MAX_PLY):
            self.killer_moves[ply][0] = None
            self.killer_moves[ply][1] = None
        for c in (0, 1):
            for f_sq in range(64):
                row = self.history[c][f_sq]
                for t_sq in range(64):
                    row[t_sq] //= 4  # Decaimento suave entre lances para aproveitar memória recente

    def _check_stop(self) -> None:
        """Verifica periodicamente se o tempo acabou ou se o Pondering foi interrompido."""
        if (self.nodes & 127) == 0:
            if self.stop_event is not None and self.stop_event.is_set():
                raise SearchTimeout()
            if self.time_manager is not None and self.time_manager.should_abort_search():
                raise SearchTimeout()

    @staticmethod
    def _score_to_tt(score: int, ply: int) -> int:
        """Ajusta pontuação de Mate para armazenamento independente do ply na Tabela de Transposição."""
        if score >= MATE_THRESHOLD:
            return score + ply
        if score <= -MATE_THRESHOLD:
            return score - ply
        return score

    @staticmethod
    def _score_from_tt(score: int, ply: int) -> int:
        """Recupera pontuação de Mate da Tabela de Transposição ajustada para o ply atual."""
        if score >= MATE_THRESHOLD:
            return score - ply
        if score <= -MATE_THRESHOLD:
            return score + ply
        return score

    def _store_tt(
        self,
        zhash: int,
        depth: int,
        score: int,
        flag: int,
        best_move: chess.Move | None,
        ply: int,
    ) -> None:
        """Armazena posição na Tabela de Transposição com política de substituição por profundidade."""
        if len(self.tt) >= self.tt_max_entries and zhash not in self.tt:
            # Limpeza parcial rápida caso exceda o limite de entradas
            self.tt.clear()

        existing = self.tt.get(zhash)
        if existing is None or depth >= existing.depth or flag == TT_EXACT:
            move_to_save = best_move if best_move is not None else (existing.best_move if existing else None)
            self.tt[zhash] = TTEntry(
                depth=depth,
                score=self._score_to_tt(score, ply),
                flag=flag,
                best_move=move_to_save,
            )

    def _order_moves(
        self,
        board: chess.Board,
        moves: list[chess.Move],
        tt_move: chess.Move | None,
        ply: int,
    ) -> list[chess.Move]:
        """
        Ordena os lances para maximizar os cortes da Poda Alpha-Beta:
        1. Lance da Tabela de Transposição (TT Move)
        2. Promoções para Dama/Cavalo
        3. Capturas ordenadas por MVV-LVA (Vítima Mais Valiosa - Atacante Menos Valioso)
        4. Killer Moves (lances quietos que geraram corte Beta no mesmo ply)
        5. History Heuristic (lances quietos historicamente fortes na busca)
        """
        k1, k2 = (
            (self.killer_moves[ply][0], self.killer_moves[ply][1])
            if ply < MAX_PLY
            else (None, None)
        )
        hist_side = self.history[int(board.turn)]

        def move_priority(move: chess.Move) -> int:
            if tt_move is not None and move == tt_move:
                return 2_000_000

            score = 0
            if move.promotion is not None:
                score += 900_000 + MG_PIECE_VALUES.get(move.promotion, 0)

            if board.is_capture(move):
                victim_pt = board.piece_type_at(move.to_square)
                if victim_pt is None:
                    # Captura En Passant
                    victim_val = 100
                else:
                    victim_val = MG_PIECE_VALUES.get(victim_pt, 100)
                attacker_pt = board.piece_type_at(move.from_square) or chess.PAWN
                attacker_val = MG_PIECE_VALUES.get(attacker_pt, 100)
                # MVV-LVA: prioriza capturar peça grande usando peça pequena
                score += 500_000 + (victim_val * 10) - attacker_val
            else:
                if move == k1:
                    score += 400_000
                elif move == k2:
                    score += 390_000
                else:
                    score += min(300_000, hist_side[move.from_square][move.to_square])

            return score

        return sorted(moves, key=move_priority, reverse=True)

    def _quiescence(
        self, board: chess.Board, alpha: int, beta: int, ply: int, q_ply: int = 0
    ) -> int:
        """
        Busca de Quiescência (Estabilidade):
        Continua analisando capturas e promoções após depth == 0 até que a posição
        esteja calma, eliminando o Efeito Horizonte.
        """
        self._check_stop()
        self.nodes += 1

        if board.is_repetition(2) or board.halfmove_clock >= 100:
            return DRAW_SCORE

        in_check = board.is_check()

        # Se atingiu o limite máximo da quiescência, retorna avaliação estática
        if q_ply >= MAX_Q_PLY:
            return evaluate_board(board, check_terminal=in_check)

        if not in_check:
            stand_pat = evaluate_board(board)
            if stand_pat >= beta:
                return beta
            if stand_pat > alpha:
                alpha = stand_pat

            # Gera apenas capturas e promoções
            moves = [
                m
                for m in board.legal_moves
                if board.is_capture(m) or m.promotion is not None
            ]
        else:
            # Em xeque, precisamos testar todas as saídas de xeque legais
            stand_pat = -MATE_SCORE + ply
            moves = list(board.legal_moves)
            if not moves:
                return -MATE_SCORE + ply

        moves = self._order_moves(board, moves, None, ply)

        for move in moves:
            # Delta Pruning (Poda por Delta): se não estamos em xeque e não é promoção,
            # verifica se a peça capturada tem chance matemática de melhorar Alpha
            if not in_check and move.promotion is None:
                victim_pt = board.piece_type_at(move.to_square)
                victim_val = MG_PIECE_VALUES.get(victim_pt, 100) if victim_pt else 100
                if stand_pat + victim_val + 200 < alpha:
                    continue

            board.push(move)
            score = -self._quiescence(board, -beta, -alpha, ply + 1, q_ply + 1)
            board.pop()

            if score >= beta:
                return beta
            if score > alpha:
                alpha = score

        return alpha

    def _negamax(
        self,
        board: chess.Board,
        depth: int,
        alpha: int,
        beta: int,
        ply: int,
        allow_null: bool = True,
    ) -> int:
        """
        Algoritmo Negamax com Poda Alpha-Beta, Tabela de Transposição,
        Null Move Pruning e Late Move Reductions (LMR).
        """
        self._check_stop()

        # Detecção rápida de empate na árvore (Repetição ou 50 lances ou Material Insuficiente)
        if ply > 0:
            if (
                board.is_repetition(2)
                or board.halfmove_clock >= 100
                or board.is_insufficient_material()
            ):
                return DRAW_SCORE

            # Mate Distance Pruning: se já encontramos um mate mais curto, poda!
            alpha = max(alpha, -MATE_SCORE + ply)
            beta = min(beta, MATE_SCORE - ply - 1)
            if alpha >= beta:
                return alpha

        in_check = board.is_check()

        # Extensão de Xeque (Check Extension): não para de calcular no meio de um xeque
        if in_check and ply < MAX_PLY - 2:
            depth += 1

        if depth <= 0:
            return self._quiescence(board, alpha, beta, ply)

        self.nodes += 1
        orig_alpha = alpha

        # 1. Consulta à Tabela de Transposição (Zobrist Hash)
        zhash = chess.polyglot.zobrist_hash(board)
        tt_entry = self.tt.get(zhash)
        tt_move = None

        if tt_entry is not None:
            tt_move = tt_entry.best_move
            if ply > 0 and tt_entry.depth >= depth:
                tt_score = self._score_from_tt(tt_entry.score, ply)
                if tt_entry.flag == TT_EXACT:
                    return tt_score
                elif tt_entry.flag == TT_LOWERBOUND and tt_score >= beta:
                    return tt_score
                elif tt_entry.flag == TT_UPPERBOUND and tt_score <= alpha:
                    return tt_score

        # 2. Null Move Pruning (Poda de Lance Nulo)
        # Se não estamos em xeque e temos peças além de peões, passamos a vez:
        # se mesmo assim o oponente não conseguir superar Beta, a posição é tão boa que podemos podar!
        non_pawn_material = (
            board.knights | board.bishops | board.rooks | board.queens
        ) & board.occupied_co[board.turn]
        if (
            allow_null
            and ply > 0
            and depth >= 3
            and not in_check
            and non_pawn_material != 0
        ):
            board.push(chess.Move.null())
            reduction = 2 if depth <= 6 else 3
            null_score = -self._negamax(
                board,
                depth - 1 - reduction,
                -beta,
                -beta + 1,
                ply + 1,
                allow_null=False,
            )
            board.pop()
            if null_score >= beta:
                return beta

        # 3. Geração e Ordenação de Lances Legais
        legal_moves = list(board.legal_moves)
        if not legal_moves:
            if in_check:
                return -MATE_SCORE + ply  # Xeque-Mate! (prefere mates mais rápidos via +ply)
            return DRAW_SCORE  # Afogamento (Stalemate)!

        ordered_moves = self._order_moves(board, legal_moves, tt_move, ply)

        best_score = -MATE_SCORE - 100
        best_move = ordered_moves[0]

        for move_idx, move in enumerate(ordered_moves):
            is_capture = board.is_capture(move)
            is_promotion = move.promotion is not None

            board.push(move)
            gives_check = board.is_check()

            # Principal Variation Search (PVS) + Late Move Reductions (LMR)
            if move_idx == 0:
                score = -self._negamax(board, depth - 1, -beta, -alpha, ply + 1)
            else:
                # LMR: reduz profundidade para lances quietos mais ao fim da lista ordenada
                do_lmr = (
                    depth >= 3
                    and move_idx >= 3
                    and not in_check
                    and not gives_check
                    and not is_capture
                    and not is_promotion
                )
                if do_lmr:
                    r = 1 if move_idx < 8 else 2
                    score = -self._negamax(
                        board, max(1, depth - 1 - r), -alpha - 1, -alpha, ply + 1
                    )
                else:
                    score = alpha + 1  # Força busca na janela nula PVS

                # Se o lance reduzido superou Alpha, re-busca na profundidade normal
                if score > alpha:
                    score = -self._negamax(
                        board, depth - 1, -alpha - 1, -alpha, ply + 1
                    )
                    if score > alpha and score < beta:
                        score = -self._negamax(board, depth - 1, -beta, -alpha, ply + 1)

            board.pop()

            if score > best_score:
                best_score = score
                best_move = move

            if score > alpha:
                alpha = score
                if alpha >= beta:
                    # Corte Beta! Atualiza Killer Moves e History Heuristic se for lance quieto
                    if not is_capture and not is_promotion and ply < MAX_PLY:
                        if self.killer_moves[ply][0] != move:
                            self.killer_moves[ply][1] = self.killer_moves[ply][0]
                            self.killer_moves[ply][0] = move
                        bonus = depth * depth
                        self.history[int(board.turn)][move.from_square][move.to_square] += bonus
                    break

        # 4. Salva resultado na Tabela de Transposição
        if best_score <= orig_alpha:
            flag = TT_UPPERBOUND
        elif best_score >= beta:
            flag = TT_LOWERBOUND
        else:
            flag = TT_EXACT

        self._store_tt(zhash, depth, best_score, flag, best_move, ply)
        return best_score

    def extract_pv(self, board: chess.Board, max_len: int = 8) -> list[str]:
        """Extrai a Variante Principal (PV) navegando pela Tabela de Transposição."""
        pv_uci: list[str] = []
        visited_hashes: set[int] = set()
        sim_board = board.copy()

        for _ in range(max_len):
            zhash = chess.polyglot.zobrist_hash(sim_board)
            if zhash in visited_hashes:
                break
            visited_hashes.add(zhash)

            entry = self.tt.get(zhash)
            if entry is None or entry.best_move is None:
                break
            move = entry.best_move
            if move not in sim_board.legal_moves:
                break

            pv_uci.append(move.uci())
            sim_board.push(move)

        return pv_uci

    def search_best_move(
        self,
        board: chess.Board,
        time_manager: TimeManager,
        max_depth: int = 20,
        use_book: bool = True,
        use_tb: bool = True,
        stop_event: threading.Event | None = None,
        verbose: bool = True,
    ) -> SearchResult:
        """
        Executa a seleção completa do melhor lance usando:
        1. Opening Book (até 10 meios-lances / 5 lances de cada cor - FAQ Item 3)
        2. Syzygy Tablebases (até 5 peças - FAQ Item 4)
        3. Iterative Deepening + Negamax Alpha-Beta com Gestão Dinâmica de Tempo
        """
        self.time_manager = time_manager
        self.stop_event = stop_event
        self.nodes = 0
        self._reset_heuristics()

        legal_moves = list(board.legal_moves)
        if not legal_moves:
            raise ValueError("Não existem lances legais na posição atual.")

        # Inicia cronômetro do lance
        soft_limit, hard_limit = time_manager.allocate_time(board, len(legal_moves))

        # 1. Verifica Opening Book (estritamente até o lance 5 / 10 meios-lances - FAQ Item 3)
        if use_book:
            book_move = self.book_tb.get_book_move(board)
            if book_move is not None:
                board.push(book_move)
                score_cp = -evaluate_board(board)
                board.pop()
                elapsed = time_manager.elapsed()
                if verbose:
                    print(
                        f"  [LIVRO DE ABERTURAS] Lance teórico encontrado: {book_move.uci()} ({elapsed:.3f}s)"
                    )
                return SearchResult(
                    best_move=book_move,
                    score_cp=score_cp,
                    depth=1,
                    nodes=1,
                    elapsed_sec=elapsed,
                    pv=[book_move.uci()],
                    source="BOOK",
                )

        # 2. Verifica Tablebases Syzygy de até 5 peças (FAQ Item 4)
        if use_tb:
            tb_move, tb_score = self.book_tb.get_syzygy_move(board)
            if tb_move is not None and tb_score is not None:
                elapsed = time_manager.elapsed()
                if verbose:
                    print(
                        f"  [TABLEBASE 5 PEÇAS] Lance perfeito encontrado: {tb_move.uci()} ({elapsed:.3f}s)"
                    )
                return SearchResult(
                    best_move=tb_move,
                    score_cp=tb_score,
                    depth=1,
                    nodes=1,
                    elapsed_sec=elapsed,
                    pv=[tb_move.uci()],
                    source="SYZYGY_TB",
                )

        # Se houver apenas 1 lance legal obrigado, executa rápido com avaliação de profundidade 1
        if len(legal_moves) == 1:
            only_move = legal_moves[0]
            board.push(only_move)
            score_cp = -evaluate_board(board, check_terminal=True)
            board.pop()
            elapsed = time_manager.elapsed()
            return SearchResult(
                best_move=only_move,
                score_cp=score_cp,
                depth=1,
                nodes=1,
                elapsed_sec=elapsed,
                pv=[only_move.uci()],
                source="SEARCH",
            )

        # 3. Iterative Deepening (Aprofundamento Iterativo: Profundidade 1, 2, 3, ..., max_depth)
        best_move = legal_moves[0]
        best_score = evaluate_board(board)
        completed_depth = 0
        best_pv = [best_move.uci()]

        for current_depth in range(1, max_depth + 1):
            try:
                # Ordena os lances na raiz colocando o melhor lance da profundidade anterior em 1º lugar
                root_moves = self._order_moves(board, legal_moves, best_move, ply=0)

                iter_best_move = root_moves[0]
                iter_best_score = -MATE_SCORE - 100
                alpha = -MATE_SCORE - 100
                beta = MATE_SCORE + 100

                for move_idx, move in enumerate(root_moves):
                    self._check_stop()
                    board.push(move)

                    if move_idx == 0:
                        score = -self._negamax(
                            board, current_depth - 1, -beta, -alpha, ply=1
                        )
                    else:
                        # PVS na raiz
                        score = -self._negamax(
                            board, current_depth - 1, -alpha - 1, -alpha, ply=1
                        )
                        if score > alpha:
                            score = -self._negamax(
                                board, current_depth - 1, -beta, -alpha, ply=1
                            )

                    board.pop()

                    if score > iter_best_score:
                        iter_best_score = score
                        iter_best_move = move

                    if score > alpha:
                        alpha = score

                # Atualiza a Tabela de Transposição para a raiz
                zhash = chess.polyglot.zobrist_hash(board)
                self._store_tt(
                    zhash, current_depth, iter_best_score, TT_EXACT, iter_best_move, ply=0
                )

                # Verifica estabilidade para gestão dinâmica de tempo
                move_changed = iter_best_move != best_move
                score_drop = max(0, best_score - iter_best_score) if completed_depth > 0 else 0
                time_manager.adjust_for_instability(
                    move_changed, score_drop, current_depth
                )

                best_move = iter_best_move
                best_score = iter_best_score
                completed_depth = current_depth
                best_pv = self.extract_pv(board, max_len=current_depth)
                if not best_pv or best_pv[0] != best_move.uci():
                    best_pv = [best_move.uci()] + [m for m in best_pv if m != best_move.uci()]

                elapsed = time_manager.elapsed()
                nps = int(self.nodes / max(0.001, elapsed))

                if verbose:
                    pv_preview = " ".join(best_pv[:6])
                    print(
                        f"  info depth {completed_depth:>2} | score {best_score:>+6d} cp | "
                        f"nodes {self.nodes:>8d} ({nps:>6d} n/s) | time {elapsed:>5.2f}s | pv {pv_preview}"
                    )

                # Se já encontrou Xeque-Mate forçado, não precisa gastar mais tempo!
                if abs(best_score) >= MATE_THRESHOLD:
                    plies_to_mate = MATE_SCORE - abs(best_score)
                    if current_depth >= plies_to_mate:
                        break

                # Verifica se já atingiu o soft_limit para não iniciar nova profundidade
                if time_manager.should_stop_iteration():
                    break

            except SearchTimeout:
                # Tempo limite estourado no meio da profundidade atual:
                # Retornamos com segurança total o melhor lance da última profundidade concluída!
                break

        elapsed = time_manager.elapsed()
        return SearchResult(
            best_move=best_move,
            score_cp=best_score,
            depth=max(1, completed_depth),
            nodes=max(1, self.nodes),
            elapsed_sec=elapsed,
            pv=best_pv,
            source="SEARCH",
        )
