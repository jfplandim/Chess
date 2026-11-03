"""
Módulo de Livro de Aberturas (Opening Book) e Tablebases Syzygy (5 peças)
Chess Challenge III - ICEV 2026

Regras estritas do FAQ respeitadas neste módulo:
- Item 3 (Opening Book): Limite estrito de 10 meios-lances (5 lances de cada cor).
  A partir do 11º meio-lance (len(board.move_stack) >= 10), o livro é desativado
  e o motor decide 100% por conta própria.
- Item 4 (Tablebases): Permitidas apenas para até 5 peças no tabuleiro
  (board.occupied.bit_count() <= 5).
"""

import os
import random
import chess
import chess.polyglot
import chess.syzygy

# Limite estrito do FAQ Item 3: 10 meios-lances (5 lances de cada cor)
MAX_BOOK_PLIES = 10

# Limite estrito do FAQ Item 4: máximo de 5 peças nas Tablebases
MAX_SYZYGY_PIECES = 5

# ==============================================================================
# LIVRO DE ABERTURAS EMBUTIDO (LINHAS CLÁSSICAS DE GRANDES MESTRES)
# Garante abertura instantânea e sólida nos primeiros 5 lances mesmo sem .bin externo.
# Cada linha é uma sequência de lances em notação UCI (até 10 meios-lances).
# ==============================================================================
BUILTIN_GM_LINES = [
    # --- Abertura Italiana / Giuoco Piano ---
    ["e2e4", "e7e5", "g1f3", "b8c6", "f1c4", "f8c5", "c2c3", "g8f6", "d2d3", "d7d6"],
    ["e2e4", "e7e5", "g1f3", "b8c6", "f1c4", "g8f6", "d2d3", "f8c5", "c2c3", "d7d6"],
    # --- Ruy Lopez (Espanhola) ---
    ["e2e4", "e7e5", "g1f3", "b8c6", "f1b5", "a7a6", "b5a4", "g8f6", "e1g1", "f8e7"],
    ["e2e4", "e7e5", "g1f3", "b8c6", "f1b5", "g8f6", "e1g1", "f6e4", "d2d4", "f8e7"],
    # --- Jogo dos Quatro Cavalos / Escocesa ---
    ["e2e4", "e7e5", "g1f3", "b8c6", "b1c3", "g8f6", "f1b5", "f8b4", "e1g1", "e8g8"],
    ["e2e4", "e7e5", "g1f3", "b8c6", "d2d4", "e5d4", "f3d4", "g8f6", "b1c3", "f8b4"],
    # --- Defesa Petroff ---
    ["e2e4", "e7e5", "g1f3", "g8f6", "f3e5", "d7d6", "e5f3", "f6e4", "d2d4", "d6d5"],
    # --- Defesa Siciliana (Najdorf / Dragão / Clássica / Alapin) ---
    ["e2e4", "c7c5", "g1f3", "d7d6", "d2d4", "c5d4", "f3d4", "g8f6", "b1c3", "a7a6"],
    ["e2e4", "c7c5", "g1f3", "b8c6", "d2d4", "c5d4", "f3d4", "g8f6", "b1c3", "d7d6"],
    ["e2e4", "c7c5", "g1f3", "e7e6", "d2d4", "c5d4", "f3d4", "a7a6", "b1c3", "d8c7"],
    ["e2e4", "c7c5", "c2c3", "g8f6", "e4e5", "f6d5", "d2d4", "c5d4", "g1f3", "b8c6"],
    ["e2e4", "c7c5", "b1c3", "b8c6", "g1f3", "d7d6", "d2d4", "c5d4", "f3d4", "g8f6"],
    # --- Defesa Francesa ---
    ["e2e4", "e7e6", "d2d4", "d7d5", "b1c3", "g8f6", "e4e5", "f6d7", "f2f4", "c7c5"],
    ["e2e4", "e7e6", "d2d4", "d7d5", "b1d2", "c7c5", "e4d5", "e6d5", "g1f3", "b8c6"],
    ["e2e4", "e7e6", "d2d4", "d7d5", "e4e5", "c7c5", "c2c3", "b8c6", "g1f3", "d8b6"],
    ["e2e4", "e7e6", "d2d4", "d7d5", "e4d5", "e6d5", "g1f3", "g8f6", "f1d3", "f8d6"],
    # --- Defesa Caro-Kann ---
    ["e2e4", "c7c6", "d2d4", "d7d5", "b1c3", "d5e4", "c3e4", "c8f5", "e4g3", "f5g6"],
    ["e2e4", "c7c6", "d2d4", "d7d5", "e4e5", "c8f5", "g1f3", "e7e6", "f1e2", "c6c5"],
    ["e2e4", "c7c6", "d2d4", "d7d5", "e4d5", "c6d5", "f1d3", "b8c6", "c2c3", "g8f6"],
    # --- Defesa Escandinava e Pirc ---
    ["e2e4", "d7d5", "e4d5", "d8d5", "b1c3", "d5a5", "d2d4", "g8f6", "g1f3", "c7c6"],
    ["e2e4", "d7d6", "d2d4", "g8f6", "b1c3", "g7g6", "g1f3", "f8g7", "f1e2", "e8g8"],
    # --- Gambito da Dama Recusado e Eslava ---
    ["d2d4", "d7d5", "c2c4", "e7e6", "b1c3", "g8f6", "g1f3", "f8e7", "c1f4", "e8g8"],
    ["d2d4", "d7d5", "c2c4", "c7c6", "g1f3", "g8f6", "b1c3", "d5c4", "a2a4", "c8f5"],
    ["d2d4", "d7d5", "c2c4", "d5c4", "g1f3", "g8f6", "e2e3", "e7e6", "f1c4", "c7c5"],
    # --- Sistema Londres ---
    ["d2d4", "d7d5", "c1f4", "g8f6", "e2e3", "c7c5", "c2c3", "b8c6", "b1d2", "e7e6"],
    ["d2d4", "g8f6", "c1f4", "d7d5", "e2e3", "c7c5", "c2c3", "b8c6", "g1f3", "e7e6"],
    # --- Defesa Índia do Rei e Nimzo-Índia ---
    ["d2d4", "g8f6", "c2c4", "g7g6", "b1c3", "f8g7", "e2e4", "d7d6", "g1f3", "e8g8"],
    ["d2d4", "g8f6", "c2c4", "e7e6", "b1c3", "f8b4", "e2e3", "e8g8", "f1d3", "d7d5"],
    ["d2d4", "g8f6", "c2c4", "e7e6", "g1f3", "d7d5", "b1c3", "f8e7", "c1f4", "e8g8"],
    # --- Abertura Inglesa e Reti ---
    ["c2c4", "e7e5", "b1c3", "g8f6", "g1f3", "b8c6", "g2g3", "f8b4", "f1g2", "e8g8"],
    ["c2c4", "c7c5", "b1c3", "b8c6", "g2g3", "g7g6", "f1g2", "f8g7", "g1f3", "g8f6"],
    ["g1f3", "d7d5", "g2g3", "g8f6", "f1g2", "c7c6", "e1g1", "c8f5", "d2d3", "e7e6"],
    ["g1f3", "g8f6", "c2c4", "g7g6", "b1c3", "f8g7", "d2d4", "e8g8", "e2e4", "d7d6"],
]


class BookAndTablebaseManager:
    def __init__(self, base_dir: str | None = None):
        if base_dir is None:
            base_dir = os.path.dirname(os.path.abspath(__file__))
        self.base_dir = base_dir

        # 1. Constrói dicionário em memória do Livro de Aberturas Embutido
        self.builtin_book: dict[str, list[str]] = {}
        self._build_internal_book()

        # 2. Procura arquivos Polyglot (.bin) locais na pasta books/ ou raiz
        self.polyglot_paths: list[str] = []
        for folder in [os.path.join(base_dir, "books"), base_dir]:
            if os.path.isdir(folder):
                for fname in os.listdir(folder):
                    if fname.lower().endswith(".bin"):
                        self.polyglot_paths.append(os.path.join(folder, fname))

        # 3. Inicializa Tablebases Syzygy se a pasta syzygy/ existir localmente
        self.tablebase = None
        syzygy_dir = os.path.join(base_dir, "syzygy")
        if os.path.isdir(syzygy_dir):
            try:
                self.tablebase = chess.syzygy.open_tablebase(syzygy_dir)
            except Exception:
                self.tablebase = None

    @staticmethod
    def _pos_key(board: chess.Board) -> str:
        """Chave única da posição (peças + turno + roques)."""
        return f"{board.board_fen()} {'w' if board.turn == chess.WHITE else 'b'} {board.castling_xfen()}"

    def _build_internal_book(self) -> None:
        """Indexa todas as linhas clássicas para consulta instantânea O(1)."""
        for line in BUILTIN_GM_LINES:
            b = chess.Board()
            for ply_idx, uci_str in enumerate(line):
                if ply_idx >= MAX_BOOK_PLIES:
                    break
                try:
                    move = chess.Move.from_uci(uci_str)
                    if move not in b.legal_moves:
                        break
                    key = self._pos_key(b)
                    if key not in self.builtin_book:
                        self.builtin_book[key] = []
                    if uci_str not in self.builtin_book[key]:
                        self.builtin_book[key].append(uci_str)
                    b.push(move)
                except ValueError:
                    break

    def get_book_move(self, board: chess.Board) -> chess.Move | None:
        """
        Retorna um lance do Opening Book se e somente se a partida estiver dentro
        do limite de 10 meios-lances (5 lances de cada cor) do FAQ Item 3.
        """
        # Verifica trava estrita do FAQ Item 3 (10 meios-lances)
        plies_played = len(board.move_stack)
        if plies_played >= MAX_BOOK_PLIES or board.fullmove_number > 5:
            return None

        # 1º tenta arquivo Polyglot .bin externo se disponível
        for bin_path in self.polyglot_paths:
            try:
                with chess.polyglot.open_reader(bin_path) as reader:
                    entries = list(reader.find_all(board))
                    if entries:
                        # Escolhe entre os lances de maior peso no livro
                        best_weight = entries[0].weight
                        top_moves = [
                            e.move for e in entries if e.weight >= best_weight * 0.8
                        ]
                        chosen = random.choice(top_moves)
                        if chosen in board.legal_moves:
                            return chosen
            except Exception:
                pass

        # 2º consulta o Livro de Grandes Mestres embutido
        key = self._pos_key(board)
        candidates = self.builtin_book.get(key)
        if candidates:
            # Prioriza o primeiro lance principal da lista (ou sorteia entre os 2 primeiros para variedade segura)
            top_candidates = candidates[:2] if len(candidates) > 1 else candidates
            chosen_uci = random.choice(top_candidates)
            move = chess.Move.from_uci(chosen_uci)
            if move in board.legal_moves:
                return move

        return None

    def get_syzygy_move(self, board: chess.Board) -> tuple[chess.Move | None, int | None]:
        """
        Consulta as Tablebases Syzygy locais se houver no máximo 5 peças no tabuleiro
        (regra estrita do FAQ Item 4).
        Retorna (melhor_lance, avaliacao_em_centipawns).
        """
        if self.tablebase is None:
            return None, None

        # Regra estrita do FAQ Item 4: apenas até 5 peças!
        if board.occupied.bit_count() > MAX_SYZYGY_PIECES:
            return None, None

        try:
            best_move = None
            best_wdl = -3
            best_dtz = 0

            for move in board.legal_moves:
                board.push(move)
                try:
                    # WDL do oponente após o nosso lance (invertemos o sinal)
                    wdl = -self.tablebase.probe_wdl(board)
                    dtz = -self.tablebase.probe_dtz(board)
                except Exception:
                    board.pop()
                    return None, None
                board.pop()

                if best_move is None or wdl > best_wdl:
                    best_wdl = wdl
                    best_dtz = dtz
                    best_move = move
                elif wdl == best_wdl:
                    # Se estamos ganhando (wdl > 0), queremos o menor DTZ positivo para ganhar rápido
                    if wdl > 0 and 0 < dtz < best_dtz:
                        best_dtz = dtz
                        best_move = move
                    # Se estamos perdendo (wdl < 0), queremos atrasar o máximo possível
                    elif wdl < 0 and dtz < best_dtz:
                        best_dtz = dtz
                        best_move = move

            if best_move is not None:
                score_cp = 25000 if best_wdl > 0 else (-25000 if best_wdl < 0 else 0)
                return best_move, score_cp
        except Exception:
            pass

        return None, None
