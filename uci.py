"""
Interface Universal de Xadrez (Protocolo UCI Padrão)
Chess Challenge III - ICEV 2026

Permite conectar o motor a interfaces gráficas (Arena, CuteChess, EnCroissant) e ao
script de testes tools/match.py. A busca roda em uma thread separada, então os
comandos 'stop', 'isready', 'ponderhit' e 'quit' são atendidos a qualquer momento.

Opções UCI:
  Motor (Proprio ou Stockfish), Desempenho (Baixo, Medio ou Ultra; só Stockfish), Threads (só Stockfish,
  vale por cima do desempenho), Hash (MB), Move Overhead (ms), OwnBook,
  Ponder, Contempt (só motor próprio), SyzygyPath, Log
Com Motor=Stockfish, o Stockfish faz a busca e a avaliação e a nossa gestão de tempo decide quanto
pensar e quando parar (motor_stockfish.py).
"""

import os
import sys
import threading

import chess

from time_manager import TimeManager
from search import SearchEngine, MATE_SCORE, MATE_THRESHOLD
from logger import MatchLogger
from motor_stockfish import NIVEIS_DESEMPENHO, MotorIndisponivel, MotorStockfish, criar_motor, threads_padrao

ENGINE_NAME = "ICEV_Chess_Engine_2026"
ENGINE_AUTHOR = "Equipe ICEV 2026"


def _send(line: str) -> None:
    sys.stdout.write(line + "\n")
    sys.stdout.flush()


def _uci_score(score: int) -> str:
    if abs(score) >= MATE_THRESHOLD:
        plies = MATE_SCORE - abs(score)
        moves = (plies + 1) // 2
        return f"mate {moves if score > 0 else -moves}"
    return f"cp {score}"


class UCIEngine:
    def __init__(self) -> None:
        self.board = chess.Board()
        self.engine = SearchEngine()
        self.own_book = True
        self.move_overhead_ms = 100
        self.log_enabled = True
        self.logger: MatchLogger | None = None
        self.search_thread: threading.Thread | None = None
        self.stop_event = threading.Event()
        self.ponder_event = threading.Event()  # setado enquanto estamos em "go ponder"
        self.pending_tm: TimeManager | None = None
        self.hash_mb: int | None = None  # None = padrão de cada motor (o do Stockfish depende da RAM)
        self.desempenho = "ultra"  # Stockfish: ultra (força total), medio ou baixo
        self.threads: int | None = None  # só para o Stockfish; None = todos os núcleos
        self.ponder_go: list[str] | None = None  # o "go ponder" em andamento, sem a palavra "ponder"
        self.silenciar = threading.Event()  # cala a busca de ponder quando ela vira busca normal

    # ------------------------------------------------------------------
    def _get_logger(self) -> MatchLogger | None:
        if not self.log_enabled:
            return None
        if self.logger is None:
            self.logger = MatchLogger(team_color="UCI_MODE")
        return self.logger

    def wait_search(self) -> None:
        if self.search_thread is not None:
            self.search_thread.join()
            self.search_thread = None

    def stop_search(self) -> None:
        self.ponder_event.clear()
        self.stop_event.set()
        self.wait_search()

    # ------------------------------------------------------------------
    def _novo_motor(self, nome: str):
        """Troca entre o motor próprio e o Stockfish (com a nossa gestão de tempo)."""
        if nome == "stockfish":
            try:
                motor = criar_motor("stockfish", self.desempenho)
            except MotorIndisponivel as erro:
                _send(f"info string {erro} Continuo com o motor próprio.")
                return self.engine
        else:
            motor = SearchEngine()
        self.engine = motor
        self._aplicar_recursos()
        return motor

    def _aplicar_recursos(self) -> None:
        """Stockfish: nível de desempenho, com Threads e Hash pedidos pela GUI valendo por cima."""
        if isinstance(self.engine, MotorStockfish):
            self.engine.definir_desempenho(self.desempenho)
            self.engine.ajustar_recursos(self.threads or self.engine.threads, self.hash_mb or self.engine.hash_mb)
        else:
            self._aplicar_hash()

    def _aplicar_hash(self) -> None:
        if self.hash_mb is None:
            return  # a GUI não pediu tamanho: fica o padrão do motor
        if isinstance(self.engine, MotorStockfish):
            self.engine.resize_tt(self.hash_mb)
        else:
            # ~190 bytes por entrada na TT em Python
            bits = max(14, min(24, (self.hash_mb * 1024 * 1024 // 190).bit_length() - 1))
            self.engine.resize_tt(bits)

    def cmd_setoption(self, tokens: list[str]) -> None:
        # setoption name <id> [value <x>]
        if "name" not in tokens:
            return
        ni = tokens.index("name")
        vi = tokens.index("value") if "value" in tokens else len(tokens)
        name = " ".join(tokens[ni + 1:vi]).strip().lower()
        value = " ".join(tokens[vi + 1:]).strip()
        try:
            if name == "motor":
                self.stop_search()
                self.engine = self._novo_motor(value.strip().lower())
            elif name == "desempenho":
                nivel = value.strip().lower().replace("é", "e")
                if nivel in NIVEIS_DESEMPENHO:
                    self.desempenho = nivel
                    self._aplicar_recursos()
            elif name == "threads":
                self.threads = max(1, int(value))
                self._aplicar_recursos()
            elif name == "hash":
                self.hash_mb = max(1, int(value))
                self._aplicar_recursos()
            elif name == "move overhead":
                self.move_overhead_ms = max(0, int(value))
            elif name == "ownbook":
                self.own_book = value.lower() == "true"
            elif name == "contempt":
                self.engine.contempt = int(value)
            elif name == "log":
                self.log_enabled = value.lower() == "true"
            elif name == "syzygypath":
                if value and value != "<empty>" and os.path.isdir(value):
                    self.engine.book_tb.load_syzygy(value)
        except ValueError:
            pass

    def cmd_position(self, tokens: list[str]) -> None:
        moves_idx = tokens.index("moves") if "moves" in tokens else len(tokens)
        if len(tokens) > 1 and tokens[1] == "startpos":
            board = chess.Board()
        elif len(tokens) > 1 and tokens[1] == "fen":
            board = chess.Board(" ".join(tokens[2:moves_idx]))
        else:
            return
        for m_str in tokens[moves_idx + 1:]:
            try:
                board.push_uci(m_str)
            except ValueError:
                break
        self.board = board

    def cmd_go(self, tokens: list[str]) -> None:
        self.stop_search()
        params: dict[str, int] = {}
        infinite = "infinite" in tokens
        ponder = "ponder" in tokens
        keys = ("wtime", "btime", "winc", "binc", "movestogo", "movetime", "depth", "nodes")
        for i, t in enumerate(tokens):
            if t in keys and i + 1 < len(tokens):
                try:
                    params[t] = int(tokens[i + 1])
                except ValueError:
                    pass

        board = self.board.copy()
        overhead = self.move_overhead_ms / 1000.0
        # "Move Overhead": tempo perdido por lance fora do motor (latência da GUI ou operador humano)
        tm = TimeManager(initial_minutes=15.0, increment_seconds=0.0,
                         move_overhead=min(overhead, 0.2), operator_overhead=overhead)
        us_white = board.turn == chess.WHITE
        t_key, i_key = ("wtime", "winc") if us_white else ("btime", "binc")
        if "movetime" in params:
            tm.set_fixed_time(params["movetime"] / 1000.0)
        elif t_key in params:
            tm.remaining_time = params[t_key] / 1000.0
            tm.increment = params.get(i_key, 0) / 1000.0
            tm.moves_to_go = params.get("movestogo")
        else:
            infinite = True  # sem relógio: pensa até receber 'stop' (ou até a profundidade)
        max_depth = params.get("depth", 64)
        if "depth" in params and not ("movetime" in params or t_key in params):
            infinite = False
            tm.set_fixed_time(10_000.0)
        max_nodes = params.get("nodes", 0)
        if max_nodes and not (t_key in params or "movetime" in params):
            infinite = False
            tm.set_fixed_time(10_000.0)

        self.stop_event = threading.Event()
        stop_event = self.stop_event
        self.silenciar = threading.Event()
        silenciar = self.silenciar
        self.ponder_go = [t for t in tokens if t != "ponder"] if ponder else None
        if ponder:
            self.ponder_event.set()
        else:
            self.ponder_event.clear()

        def info_cb(depth, seldepth, score, nodes, elapsed, pv):
            nps = int(nodes / max(elapsed, 1e-3))
            _send(
                f"info depth {depth} seldepth {seldepth} score {_uci_score(score)} nodes {nodes} "
                f"nps {nps} time {int(elapsed * 1000)} pv {' '.join(m.uci() for m in pv)}"
            )

        def worker():
            try:
                # Em 'go ponder' / 'go infinite' pensa sem limite até 'stop' ou 'ponderhit'
                res = self.engine.search_best_move(
                    board=board,
                    time_manager=tm,
                    max_depth=max_depth,
                    use_book=self.own_book and not ponder,
                    use_tb=True,
                    stop_event=stop_event,
                    verbose=False,
                    infinite=infinite or ponder,
                    max_nodes=max_nodes,
                    info_callback=info_cb,
                )
            except Exception as exc:  # nunca deixa a GUI sem resposta
                _send(f"info string erro na busca: {exc}")
                legal = list(board.legal_moves)
                if not legal:
                    _send("bestmove 0000")
                    return
                from search import SearchResult
                res = SearchResult(legal[0], 0, 1, 1, 0.0, [legal[0].uci()])
            # Em modo ponder/infinite, o protocolo exige esperar o 'stop' antes do bestmove
            while (infinite or ponder) and not stop_event.is_set():
                stop_event.wait(0.01)
            if silenciar.is_set():
                return  # ponderhit: quem responde é a busca normal que substituiu esta
            if res.source != "SEARCH":
                _send(f"info depth {res.depth} score {_uci_score(res.score_cp)} nodes {res.nodes} "
                      f"time {int(res.elapsed_sec * 1000)} pv {' '.join(res.pv)} string {res.source}")
            # Lance de ponder nunca é jogado: no 'stop' a GUI descarta e no 'ponderhit' vem a busca normal
            logger = None if ponder else self._get_logger()
            if logger is not None:
                logger.log_engine_move(
                    move_number=board.fullmove_number, turn_color=board.turn,
                    uci_move=res.best_move.uci(), depth=res.depth, nodes=res.nodes,
                    score_cp=res.score_cp, pv_moves=res.pv, elapsed_sec=res.elapsed_sec,
                    source=res.source,
                )
            line = f"bestmove {res.best_move.uci()}"
            if len(res.pv) > 1:
                line += f" ponder {res.pv[1]}"
            _send(line)

        self.search_thread = threading.Thread(target=worker, daemon=True)
        self.search_thread.start()

    # ------------------------------------------------------------------
    def loop(self) -> None:
        while True:
            line = sys.stdin.readline()
            if not line:
                self.stop_search()
                break
            tokens = line.strip().split()
            if not tokens:
                continue
            cmd = tokens[0]
            if cmd == "uci":
                _send(f"id name {ENGINE_NAME}")
                _send(f"id author {ENGINE_AUTHOR}")
                _send("option name Motor type combo default Proprio var Proprio var Stockfish")
                _send("option name Desempenho type combo default Ultra var Baixo var Medio var Ultra")
                _send(f"option name Threads type spin default {threads_padrao()} min 1 max 512")
                _send("option name Hash type spin default 190 min 4 max 4096")
                _send("option name Move Overhead type spin default 100 min 0 max 10000")
                _send("option name OwnBook type check default true")
                _send("option name Ponder type check default false")
                _send("option name Contempt type spin default 15 min -100 max 100")
                _send("option name SyzygyPath type string default <empty>")
                _send("option name Log type check default true")
                _send("uciok")
            elif cmd == "isready":
                _send("readyok")
            elif cmd == "ucinewgame":
                self.stop_search()
                self.engine.clear_cache()
                logger = self._get_logger()
                if logger is not None:
                    logger.log_event("Novo jogo UCI iniciado (ucinewgame).")
            elif cmd == "setoption":
                self.cmd_setoption(tokens)
            elif cmd == "position":
                self.cmd_position(tokens)
            elif cmd == "go":
                self.cmd_go(tokens)
            elif cmd == "stop":
                self.stop_search()
            elif cmd == "ponderhit":
                # O adversário jogou o lance previsto. Como no torneio (session.py): o ponder para,
                # deixando a tabela de transposição pronta, e o motor faz a busca normal do lance
                # com os tempos do "go ponder". Parar e responder na hora deixava o lance com quase
                # nenhuma busca quando o adversário respondia rápido.
                if self.ponder_go is not None:
                    self.silenciar.set()
                    self.cmd_go(self.ponder_go)  # o cmd_go para a busca de ponder antes de começar
                else:
                    self.stop_search()
            elif cmd == "quit":
                self.stop_search()
                break
        if isinstance(self.engine, MotorStockfish):
            self.engine.fechar()  # sai na hora, sem esperar o encerramento do Python


def run_uci_loop() -> None:
    UCIEngine().loop()


if __name__ == "__main__":
    run_uci_loop()
