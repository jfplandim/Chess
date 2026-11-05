"""
Sessão de partida usada pela interface de torneio (tui.py)
Chess Challenge III - ICEV 2026

Guarda o estado de uma partida e aplica o protocolo do FAQ Item 10 com a mesma lógica do
main.py: relógio interno com o custo do operador, log obrigatório da banca (FAQ Item 9),
pondering e contempt do Armageddon. Aqui não há entrada nem saída de texto: a interface
chama os métodos. A partida é salva a cada lance em logs/partida_em_andamento.json para
poder ser retomada se o notebook travar ou a janela fechar.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import threading
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime

import chess

from evaluation import evaluate_board
from logger import MatchLogger
from main import (
    ARMAGEDDON_CONTEMPT,
    PIECE_NAMES_PT,
    PonderController,
    lance_de_emergencia,
    partida_acabou,
    resultado_partida,
)
from motor_stockfish import (
    NOME_DESEMPENHO,
    NOME_MOTOR,
    MotorStockfish,
    criar_motor,
    descrever_motor,
    motor_padrao,
)
from search import SearchResult
from time_manager import TimeManager

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOGS_DIR = os.path.join(BASE_DIR, "logs")
ARQUIVO_SALVO = os.path.join(LOGS_DIR, "partida_em_andamento.json")
ARQUIVO_SALVO_ANTERIOR = os.path.join(LOGS_DIR, "partida_salva_anterior.json")
VERSAO_SALVO = 1

COR_PT = {chess.WHITE: "Brancas", chess.BLACK: "Pretas"}

# Ritmos fixos: (minutos, incremento em segundos). No Armageddon os minutos dependem da cor.
RITMOS_FIXOS = {
    "oficial": (15.0, 10.0),   # FAQ Item 7
    "edital": (25.0, 0.0),
}

# Motivos de fim de partida, pelo nome que resultado_partida() devolve (o mesmo do log)
MOTIVOS_FIM = {
    "CHECKMATE": "xeque-mate",
    "STALEMATE": "afogamento",
    "INSUFFICIENT_MATERIAL": "material insuficiente",
    "SEVENTYFIVE_MOVES": "regra dos 75 lances",
    "FIVEFOLD_REPETITION": "repetição de posição",
    "FIFTY_MOVES": "regra dos 50 lances",
    "THREEFOLD_REPETITION": "tripla repetição",
}

ARTIGO_PECA = {
    chess.PAWN: "o peão",
    chess.KNIGHT: "o cavalo",
    chess.BISHOP: "o bispo",
    chess.ROOK: "a torre",
    chess.QUEEN: "a dama",
    chess.KING: "o rei",
}


# ==============================================================================
# Dados da partida
# ==============================================================================
@dataclass
class Config:
    """Escolhas da tela inicial."""

    our_color: bool = chess.WHITE
    ritmo: str = "oficial"          # oficial, armageddon, edital ou personalizado
    minutos: float = 15.0           # usados só no ritmo personalizado
    incremento: float = 10.0
    ponder: bool = True
    operador: float = 4.0           # segundos que a equipe gasta por lance (anunciar, mover, relógio)
    motor: str = field(default_factory=motor_padrao)  # "stockfish" (torneio) ou "proprio" (reserva)
    desempenho: str = "ultra"       # Stockfish: "ultra" (força total), "medio" ou "baixo"

    def relogio(self) -> tuple[float, float]:
        """Minutos e incremento efetivos (Armageddon: 5 min Brancas, 4 min Pretas, sem incremento)."""
        if self.ritmo == "armageddon":
            return (5.0 if self.our_color == chess.WHITE else 4.0), 0.0
        if self.ritmo in RITMOS_FIXOS:
            return RITMOS_FIXOS[self.ritmo]
        return self.minutos, self.incremento

    def descricao_ritmo(self) -> str:
        minutos, inc = self.relogio()
        if self.ritmo == "armageddon":
            return f"Armageddon {minutos:g} min"
        return f"{minutos:g} + {inc:g}"


@dataclass
class Lance:
    """Um meio-lance da súmula."""

    uci: str
    san: str
    descricao: str
    cor: bool                       # cor de quem jogou
    nosso: bool                     # lance da nossa equipe?
    origem: str                     # BOOK, SYZYGY_TB, SEARCH, MANUAL ou ADVERSARIO
    tempo: float                    # segundos gastos
    avaliacao: int | None = None    # centipawns do nosso ponto de vista (lances nossos)
    profundidade: int = 0


@dataclass
class Leitura:
    """Interpretação ao vivo do que o operador digitou, antes do Enter."""

    estado: str                     # vazio, parcial, ok, promocao ou erro
    mensagem: str
    lance: chess.Move | None = None
    origem: int | None = None       # casa de origem reconhecida (destacada no tabuleiro)
    destinos: list[int] = field(default_factory=list)


# ==============================================================================
# Textos para o operador
# ==============================================================================
def descrever(board: chess.Board, move: chess.Move) -> str:
    """Descrição curta em português para mover a peça no tabuleiro físico."""
    de, para = chess.square_name(move.from_square), chess.square_name(move.to_square)
    if board.is_castling(move):
        pequeno = chess.square_file(move.to_square) == 6
        fileira = "1" if board.turn == chess.WHITE else "8"
        torre_de, torre_para = ("h", "f") if pequeno else ("a", "d")
        texto = (
            f"Roque {'pequeno' if pequeno else 'grande'}: rei de {de} para {para} "
            f"e torre de {torre_de}{fileira} para {torre_para}{fileira}"
        )
    else:
        peca = board.piece_type_at(move.from_square)
        texto = f"{PIECE_NAMES_PT.get(peca, 'Peça')} de {de} para {para}"
        if board.is_en_passant(move):
            capturado = chess.square(chess.square_file(move.to_square), chess.square_rank(move.from_square))
            texto += f", capturando en passant o peão de {chess.square_name(capturado)}"
        elif board.is_capture(move):
            texto += f", capturando {ARTIGO_PECA.get(board.piece_type_at(move.to_square), 'a peça')}"
        if move.promotion:
            texto += f", promovendo a {PIECE_NAMES_PT[move.promotion].lower()}"
    if board.gives_check(move):
        board.push(move)
        mate = board.is_checkmate()
        board.pop()
        texto += ", com xeque-mate" if mate else ", com xeque"
    return texto


def _lista_casas(casas: list[int]) -> str:
    nomes = [chess.square_name(c) for c in casas]
    if len(nomes) <= 1:
        return "".join(nomes)
    return ", ".join(nomes[:-1]) + " ou " + nomes[-1]


_CASA = re.compile(r"[a-h][1-8]")
_UCI = re.compile(r"[a-h][1-8][a-h][1-8][qrbn]?")


def ler_lance(board: chess.Board, texto: str) -> Leitura:
    """Interpreta UCI (g8f6, e7e8q), só a casa de origem (g8) ou SAN como reserva (Nf6, O-O)."""
    bruto = texto.strip()
    if not bruto:
        return Leitura("vazio", "Digite o lance anunciado, como e7e5. Roque: e1g1. Promoção: e7e8q.")
    t = re.sub(r"[\s\-=+#x:]", "", bruto.lower())
    lado = COR_PT[board.turn]
    legais = list(board.legal_moves)

    def destinos_de(casa: int) -> list[int]:
        return sorted({m.to_square for m in legais if m.from_square == casa})

    def nome_peca(casa: int) -> str:
        return PIECE_NAMES_PT.get(board.piece_type_at(casa), "Peça")

    def peca_do_lado(casa: int) -> bool:
        peca = board.piece_at(casa)
        return peca is not None and peca.color == board.turn

    # Só a origem (g8) ou origem + coluna (g8f): mostra para onde a peça pode ir
    if _CASA.fullmatch(t) or (len(t) == 3 and _CASA.fullmatch(t[:2]) and t[2] in "abcdefgh"):
        casa = chess.parse_square(t[:2])
        if not peca_do_lado(casa):
            return Leitura("erro", f"Não há peça das {lado} em {t[:2]}.", origem=casa)
        destinos = destinos_de(casa)
        if not destinos:
            return Leitura("erro", f"{nome_peca(casa)} de {t[:2]} não tem lance legal agora.", origem=casa)
        return Leitura(
            "parcial", f"{nome_peca(casa)} de {t[:2]} pode ir para {_lista_casas(destinos)}.",
            origem=casa, destinos=destinos,
        )

    if _UCI.fullmatch(t):
        origem, destino = chess.parse_square(t[:2]), chess.parse_square(t[2:4])
        if not peca_do_lado(origem):
            return Leitura("erro", f"Não há peça das {lado} em {t[:2]}.", origem=origem)
        destinos = destinos_de(origem)
        lance = chess.Move.from_uci(t)
        promove = board.piece_type_at(origem) == chess.PAWN and chess.square_rank(destino) in (0, 7)
        if promove and lance.promotion is None and chess.Move(origem, destino, chess.QUEEN) in legais:
            return Leitura(
                "promocao", "Promoção: Enter abre a escolha da peça (ou termine com q, r, b ou n).",
                lance=chess.Move(origem, destino, chess.QUEEN), origem=origem, destinos=destinos,
            )
        if lance in legais:
            return Leitura("ok", descrever(board, lance), lance=lance, origem=origem, destinos=destinos)
        if board.piece_type_at(origem) == chess.KING and abs(chess.square_file(destino) - chess.square_file(origem)) >= 2:
            dica = "Roque em UCI é o rei andando duas casas: e1g1, e1c1, e8g8 ou e8c8."
        elif destinos:
            dica = f"{nome_peca(origem)} de {t[:2]} pode ir para {_lista_casas(destinos)}."
        else:
            dica = f"{nome_peca(origem)} de {t[:2]} não tem lance legal agora."
        xeque = " O rei está em xeque." if board.is_check() else ""
        return Leitura(
            "erro", f"Lance ilegal. {dica}{xeque} Se foi anunciado assim, chame o árbitro.",
            origem=origem, destinos=destinos,
        )

    # Reserva: SAN (Nf6, exd5, O-O), convertido para UCI
    candidatos = [bruto, bruto.replace("0", "O")]
    if bruto[0] in "nbrqk":
        candidatos.append(bruto[0].upper() + bruto[1:])
    for candidato in candidatos:
        try:
            lance = board.parse_san(candidato)
        except chess.AmbiguousMoveError:
            return Leitura("erro", "Lance ambíguo. Digite em UCI, com a casa de origem (ex.: b8d7).")
        except ValueError:
            continue
        return Leitura(
            "ok", f"{descrever(board, lance)}. Em UCI: {lance.uci()}",
            lance=lance, origem=lance.from_square, destinos=[lance.to_square],
        )
    if len(t) == 1 and t in "abcdefgh":
        return Leitura("parcial", "Continue: casa de origem e de destino, como g8f6.")
    return Leitura("erro", "Não entendi. Use a casa de origem e a de destino, como g8f6.")


# ==============================================================================
# Log retomado
# ==============================================================================
class LogRetomado(MatchLogger):
    """Reabre o log de uma partida interrompida sem reescrever o cabeçalho."""

    def __init__(self, log_path: str):  # não chama o __init__ da base, que criaria um log novo
        self.logs_dir = os.path.dirname(log_path)
        self.log_path = log_path
        self.txt_mirror_path = os.path.join(self.logs_dir, "ultima_partida_banca.txt")
        try:
            shutil.copyfile(log_path, self.txt_mirror_path)  # o espelho volta a ser desta partida
        except OSError:
            pass


def ler_partida_salva() -> dict | None:
    """Partida salva e ainda não encerrada, ou None."""
    try:
        with open(ARQUIVO_SALVO, encoding="utf-8") as f:
            dados = json.load(f)
    except (OSError, ValueError):
        return None
    if dados.get("versao") != VERSAO_SALVO or dados.get("encerrada"):
        return None
    return dados


def arquivar_partida_salva() -> None:
    """Ao começar uma partida nova, guarda a salva anterior em vez de apagá-la."""
    if os.path.exists(ARQUIVO_SALVO):
        try:
            os.replace(ARQUIVO_SALVO, ARQUIVO_SALVO_ANTERIOR)
        except OSError:
            pass


# ==============================================================================
# Sessão
# ==============================================================================
class GameSession:
    def __init__(self, config: Config, logger: MatchLogger | None = None):
        self.config = config
        self.our_color = config.our_color
        minutos, incremento = config.relogio()
        self.time_manager = TimeManager(
            initial_minutes=minutos,
            increment_seconds=incremento,
            move_overhead=0.5,
            operator_overhead=config.operador,
        )
        self.engine = criar_motor(config.motor, config.desempenho)  # MotorIndisponivel se o Stockfish não abrir
        self.engine.our_color = config.our_color  # contempt contado a partir da nossa cor
        if config.ritmo == "armageddon" and not self.usa_stockfish:
            # FAQ Item 12: empate classifica as Pretas (mesma regra do main.py). O Stockfish não tem contempt.
            self.engine.contempt = ARMAGEDDON_CONTEMPT if config.our_color == chess.WHITE else -ARMAGEDDON_CONTEMPT
        self.board = chess.Board()
        self.fen_inicial = self.board.fen()
        self.lances: list[Lance] = []
        self.ponder_ctrl = PonderController(self.engine)
        self.use_ponder = config.ponder
        self.lance_previsto: str | None = None
        self.inicio_turno = time.perf_counter()
        self.resultado: tuple[str, str] | None = None   # (placar, motivo)
        self.encerrada = False
        self.fator_antes_economia: float | None = None
        if logger is None:
            logger = MatchLogger(team_color=COR_PT[self.our_color].upper())
            logger.log_event(f"Motor: {descrever_motor(self.engine)}")
            if config.ritmo == "armageddon" and self.usa_stockfish:
                logger.log_event("Armageddon: empate classifica as Pretas (FAQ Item 12). "
                                 "O Stockfish não tem contempt: avalia o empate como 0.")
            elif config.ritmo == "armageddon":
                logger.log_event(
                    f"Armageddon: contempt {self.engine.contempt:+d} cp (empate classifica as Pretas - FAQ Item 12)"
                )
        self.logger = logger

    # ------------------------------------------------------------------
    # Consultas
    # ------------------------------------------------------------------
    @property
    def usa_stockfish(self) -> bool:
        return isinstance(self.engine, MotorStockfish)

    @property
    def nome_motor(self) -> str:
        if self.usa_stockfish:
            return f"{self.engine.nome} ({NOME_DESEMPENHO.get(self.engine.desempenho, self.engine.desempenho)})"
        return NOME_MOTOR["proprio"]

    @property
    def nome_cor(self) -> str:
        return COR_PT[self.our_color]

    @property
    def nossa_vez(self) -> bool:
        return self.board.turn == self.our_color and not self.encerrada

    @property
    def economia(self) -> bool:
        return self.fator_antes_economia is not None

    def relogios(self) -> tuple[float, float]:
        """Relógios estimados agora: o do lado que joga está correndo."""
        nosso, adv = self.time_manager.remaining_time, self.time_manager.opponent_time
        if not self.encerrada:
            corrido = time.perf_counter() - self.inicio_turno
            if self.board.turn == self.our_color:
                nosso -= corrido
            else:
                adv -= corrido
        return max(0.0, nosso), max(0.0, adv)

    def avaliacao_estatica(self) -> int:
        """Avaliação sem busca, do nosso ponto de vista."""
        valor = evaluate_board(self.board, check_terminal=True)  # ponto de vista de quem joga
        return valor if self.board.turn == self.our_color else -valor

    def alertas(self) -> list[str]:
        if self.encerrada:
            return []
        avisos = []
        if self.board.is_repetition(2):
            avisos.append("Esta posição já apareceu duas vezes: mais uma repetição empata a partida.")
        if self.board.halfmove_clock >= 80:
            faltam = (100 - self.board.halfmove_clock + 1) // 2
            avisos.append(f"Regra dos 50 lances: faltam {faltam} lances sem captura nem lance de peão.")
        return avisos

    # ------------------------------------------------------------------
    # Lances
    # ------------------------------------------------------------------
    def pensar(self, board: chess.Board, stop_event: threading.Event, info_callback=None) -> SearchResult:
        """Busca o nosso lance numa cópia do tabuleiro. Roda fora da thread da interface."""
        self.ponder_ctrl.stop_ponder()
        return self.engine.search_best_move(
            board=board,
            time_manager=self.time_manager,
            max_depth=64,
            use_book=True,
            use_tb=True,
            stop_event=stop_event,
            verbose=False,
            info_callback=info_callback,
        )

    def registrar_nosso(self, resultado: SearchResult) -> Lance:
        """Aplica o lance do motor (mesmos registros do main.py)."""
        move = resultado.best_move
        if move not in self.board.legal_moves:
            raise ValueError(f"O lance {move.uci()} não é legal na posição atual.")
        gasto = time.perf_counter() - self.inicio_turno
        self.time_manager.register_move_time(gasto)
        self.logger.log_engine_move(
            move_number=self.board.fullmove_number,
            turn_color=self.our_color,
            uci_move=move.uci(),
            depth=resultado.depth,
            nodes=resultado.nodes,
            score_cp=resultado.score_cp,
            pv_moves=resultado.pv,
            elapsed_sec=resultado.elapsed_sec,
            source=resultado.source,
        )
        lance = Lance(
            uci=move.uci(), san=self.board.san(move), descricao=descrever(self.board, move),
            cor=self.board.turn, nosso=True, origem=resultado.source, tempo=resultado.elapsed_sec,
            avaliacao=resultado.score_cp, profundidade=resultado.depth,
        )
        self.lance_previsto = resultado.pv[1] if len(resultado.pv) > 1 else None
        self._empurrar(move, lance)
        return lance

    def registrar_adversario(self, move: chess.Move) -> Lance:
        self.ponder_ctrl.stop_ponder()
        gasto = time.perf_counter() - self.inicio_turno
        self.time_manager.register_opponent_time(gasto)
        self.logger.log_opponent_move(
            move_number=self.board.fullmove_number,
            turn_color=self.board.turn,
            uci_move=move.uci(),
            elapsed_sec=gasto,
        )
        lance = Lance(
            uci=move.uci(), san=self.board.san(move), descricao=descrever(self.board, move),
            cor=self.board.turn, nosso=False, origem="ADVERSARIO", tempo=gasto,
        )
        self._empurrar(move, lance)
        return lance

    def registrar_manual(self, move: chess.Move) -> Lance:
        """Lance nosso escolhido pela equipe (ex.: regra da peça tocada); vai ao log como MANUAL."""
        gasto = time.perf_counter() - self.inicio_turno
        self.time_manager.register_move_time(gasto)
        self.board.push(move)
        avaliacao = -evaluate_board(self.board, check_terminal=True)
        self.board.pop()
        self.logger.log_event(f"Lance manual (nosso): {move.uci()}")
        self.logger.log_engine_move(
            move_number=self.board.fullmove_number,
            turn_color=self.our_color,
            uci_move=move.uci(),
            depth=0,
            nodes=0,
            score_cp=avaliacao,
            pv_moves=[move.uci()],
            elapsed_sec=gasto,
            source="MANUAL",
        )
        lance = Lance(
            uci=move.uci(), san=self.board.san(move), descricao=descrever(self.board, move),
            cor=self.board.turn, nosso=True, origem="MANUAL", tempo=gasto, avaliacao=avaliacao,
        )
        self.lance_previsto = None
        self._empurrar(move, lance)
        return lance

    def _empurrar(self, move: chess.Move, lance: Lance) -> None:
        self.board.push(move)
        self.lances.append(lance)
        self.inicio_turno = time.perf_counter()
        self._verificar_fim()
        self.retomar_turno()
        self.salvar()

    def retomar_turno(self) -> None:
        """Na vez do adversário, liga o pondering (se ativado)."""
        if self.use_ponder and not self.encerrada and self.board.turn != self.our_color:
            self.ponder_ctrl.start_ponder(self.board, predicted_uci=self.lance_previsto)

    def _verificar_fim(self) -> None:
        # Mesma regra do main.py: repetição e 50 lances só encerram quando já aconteceram,
        # não quando o próximo lance poderia causá-los (o que claim_draw=True acusaria).
        if not partida_acabou(self.board):
            return
        placar, reason = resultado_partida(self.board)
        self.resultado = (placar, MOTIVOS_FIM.get(reason, "fim de jogo"))
        self.encerrada = True
        self.ponder_ctrl.stop_ponder()
        self.logger.log_event(f"Fim da partida: {placar} ({reason})")

    def lance_emergencia(self, erro: Exception) -> SearchResult:
        """A busca falhou: lance pela avaliação em 1 ply, como no main.py (o relógio não pode ficar parado)."""
        self.logger.log_event(f"Exceção na busca: {erro}. Lance de emergência usado.")
        return lance_de_emergencia(self.board.copy(), self.time_manager)

    # ------------------------------------------------------------------
    # Correções e ajustes
    # ------------------------------------------------------------------
    def desfazer(self, n: int = 1) -> list[str]:
        self.ponder_ctrl.stop_ponder()
        desfeitos = []
        for _ in range(min(n, len(self.board.move_stack))):
            desfeitos.append(self.board.pop().uci())
            if self.lances:
                self.lances.pop()
        if desfeitos:
            self.logger.log_event(f"UNDO executado. Lances revertidos: {', '.join(desfeitos)}")
        self.lance_previsto = None
        self.resultado, self.encerrada = None, False
        self.inicio_turno = time.perf_counter()
        self.salvar()
        return desfeitos

    @staticmethod
    def validar_fen(fen: str) -> tuple[chess.Board | None, str]:
        try:
            board = chess.Board(fen.strip())
        except ValueError as exc:
            return None, f"FEN inválido: {exc}"
        if not board.is_valid():
            return None, "Posição impossível. Confira os reis, peões na primeira ou última fileira e quem joga."
        return board, f"{COR_PT[board.turn]} jogam, {chess.popcount(board.occupied)} peças no tabuleiro."

    def definir_posicao(self, board: chess.Board) -> None:
        self.ponder_ctrl.stop_ponder()
        self.board = board
        self.fen_inicial = board.fen()
        self.lances = []
        self.lance_previsto = None
        self.resultado, self.encerrada = None, False
        self.inicio_turno = time.perf_counter()
        self.logger.log_event(f"Posição ajustada via FEN: {board.fen()}")
        self._verificar_fim()
        self.salvar()

    def sincronizar(self, nosso: float, adv: float | None) -> None:
        """O relógio físico é a referência: o turno atual volta a contar a partir de agora."""
        self.time_manager.sync_clocks(nosso, adv)
        self.inicio_turno = time.perf_counter()
        extra = f" (adversário {adv:.1f}s)" if adv is not None else ""
        self.logger.log_event(f"Relógio sincronizado para {nosso:.1f}s{extra}")
        self.salvar()

    def ajustar(self, operador: float | None = None, fator: float | None = None,
                contempt: int | None = None, ponder: bool | None = None) -> None:
        tm = self.time_manager
        if operador is not None and abs(operador - tm.operator_overhead) > 1e-9:
            tm.operator_overhead = max(0.0, operador)
            self.logger.log_event(f"Custo do operador ajustado para {tm.operator_overhead:.1f}s")
        if fator is not None and abs(fator - tm.time_factor) > 1e-9:
            tm.time_factor = min(3.0, max(0.1, fator))
            self.fator_antes_economia = None  # ajuste manual desliga o modo economia
            self.logger.log_event(f"Fator de tempo ajustado para {tm.time_factor:.2f}")
        if contempt is not None and int(contempt) != self.engine.contempt:
            self.engine.contempt = max(-100, min(100, int(contempt)))
            self.logger.log_event(f"Contempt ajustado para {self.engine.contempt} cp")
        if ponder is not None and ponder != self.use_ponder:
            self.definir_ponder(ponder)
        self.salvar()

    def definir_ponder(self, ligado: bool) -> None:
        self.use_ponder = ligado
        self.logger.log_event(f"Pondering {'ativado' if ligado else 'desativado'}")
        if ligado:
            self.retomar_turno()
        else:
            self.ponder_ctrl.stop_ponder()
        self.salvar()

    def alternar_economia(self) -> bool:
        """Modo economia: metade do tempo por lance (desempate por tempo, FAQ Item 12)."""
        tm = self.time_manager
        if self.fator_antes_economia is None:
            self.fator_antes_economia = tm.time_factor
            tm.time_factor = 0.5
        else:
            tm.time_factor = self.fator_antes_economia
            self.fator_antes_economia = None
        sufixo = " (modo economia)" if self.economia else ""
        self.logger.log_event(f"Fator de tempo ajustado para {tm.time_factor:.2f}{sufixo}")
        self.salvar()
        return self.economia

    def declarar_resultado(self, nosso_resultado: str) -> tuple[str, str]:
        """Resultado dado pelo árbitro (abandono, acordo, tempo): vitoria, derrota ou empate."""
        if nosso_resultado == "empate":
            placar = "1/2-1/2"
        else:
            brancas_venceram = (nosso_resultado == "vitoria") == (self.our_color == chess.WHITE)
            placar = "1-0" if brancas_venceram else "0-1"
        self.ponder_ctrl.stop_ponder()
        self.resultado = (placar, "declarado pelo árbitro")
        self.encerrada = True
        self.logger.log_event(f"Resultado declarado pelo operador: {placar}")
        self.salvar()
        return self.resultado

    def parar(self) -> None:
        self.ponder_ctrl.stop_ponder()

    # ------------------------------------------------------------------
    # Salvar e retomar
    # ------------------------------------------------------------------
    def para_dict(self) -> dict:
        nosso, adv = self.relogios()
        config = asdict(self.config)
        config["our_color"] = "w" if self.our_color == chess.WHITE else "b"
        return {
            "versao": VERSAO_SALVO,
            "config": config,
            "fen_inicial": self.fen_inicial,
            "lances": [m.uci() for m in self.board.move_stack],
            "historico": [asdict(lance) for lance in self.lances],
            "relogio_nosso": nosso,
            "relogio_adv": adv,
            "operador": self.time_manager.operator_overhead,
            "fator": self.time_manager.time_factor,
            "fator_antes_economia": self.fator_antes_economia,
            "contempt": self.engine.contempt,
            "ponder": self.use_ponder,
            "log_path": self.logger.log_path,
            "encerrada": self.encerrada,
            "resultado": list(self.resultado) if self.resultado else None,
            "salvo_em": datetime.now().strftime("%H:%M:%S"),
        }

    def salvar(self) -> None:
        """Grava a partida a cada mudança. Falhar aqui nunca pode atrapalhar o jogo."""
        try:
            os.makedirs(LOGS_DIR, exist_ok=True)
            tmp = ARQUIVO_SALVO + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.para_dict(), f, ensure_ascii=False, indent=1)
            os.replace(tmp, ARQUIVO_SALVO)
        except OSError:
            pass

    @classmethod
    def retomar(cls, dados: dict) -> "GameSession":
        cfg = dict(dados["config"])
        cfg["our_color"] = cfg["our_color"] == "w"
        cfg.setdefault("motor", "proprio")  # partidas salvas antes do Stockfish foram jogadas pelo motor próprio
        cfg.setdefault("desempenho", "ultra")
        log_path = dados.get("log_path")
        logger = LogRetomado(log_path) if log_path and os.path.exists(log_path) else None
        sessao = cls(Config(**cfg), logger=logger)
        board = chess.Board(dados["fen_inicial"])
        for uci in dados["lances"]:
            board.push_uci(uci)
        sessao.board = board
        sessao.fen_inicial = dados["fen_inicial"]
        sessao.lances = [Lance(**item) for item in dados.get("historico", [])]
        sessao.time_manager.operator_overhead = dados["operador"]
        sessao.time_manager.time_factor = dados["fator"]
        sessao.fator_antes_economia = dados.get("fator_antes_economia")
        sessao.engine.contempt = dados["contempt"]
        sessao.use_ponder = dados["ponder"]
        sessao.time_manager.sync_clocks(dados["relogio_nosso"], dados["relogio_adv"])
        sessao.inicio_turno = time.perf_counter()
        sessao.logger.log_event(
            f"Partida retomada pela interface: {len(dados['lances'])} meios-lances, "
            f"relógio {dados['relogio_nosso']:.1f}s. Motor: {descrever_motor(sessao.engine)}"
        )
        sessao.salvar()
        return sessao
