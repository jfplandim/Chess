"""
Interface de torneio no navegador - Chess Challenge III - ICEV 2026

    python web.py                   # abre http://127.0.0.1:8765 no navegador
    python web.py --porta 9000      # usa outra porta
    python web.py --sem-navegador   # só liga o servidor (abra o endereço à mão)

É a interface da TUI (tui.py) no navegador: placa de anúncio com o lance em letras grandes,
tabuleiro clicável com as peças desenhadas, relógios, súmula, motor ao vivo e botões (ou as
teclas F1 a F9) para desfazer, sincronizar o relógio, ajustar, corrigir a posição,
economizar tempo e registrar lance manual. O terminal só mostra o endereço.

Tudo roda nesta máquina, em 127.0.0.1: nenhuma requisição externa (FAQ Item 14). A lógica
da partida é a de session.py, a mesma da TUI e do main.py, e o log da banca também.
Uma partida salva por uma interface pode ser retomada pela outra.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import secrets
import threading
import time
import traceback
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

import chess
import chess.svg

from book_and_tb import BookAndTablebaseManager
from evaluation import MATE_SCORE, MATE_THRESHOLD
from main import parse_clock_input
from motor_stockfish import (
    NIVEIS_DESEMPENHO,
    MotorIndisponivel,
    encontrar_stockfish,
    motor_padrao,
    recursos_do_nivel,
    verificar_stockfish,
)
from session import COR_PT, Config, GameSession, arquivar_partida_salva, ler_lance, ler_partida_salva

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PASTA_WEB = os.path.join(BASE_DIR, "web")
PASTA_DOCS = os.path.join(BASE_DIR, "docs")
PORTA_PADRAO = 8765
ARQUIVOS = {
    "/": (os.path.join(PASTA_WEB, "index.html"), "text/html; charset=utf-8"),
    "/app.css": (os.path.join(PASTA_WEB, "app.css"), "text/css; charset=utf-8"),
    "/app.js": (os.path.join(PASTA_WEB, "app.js"), "text/javascript; charset=utf-8"),
    "/documentacao": (os.path.join(PASTA_DOCS, "index.html"), "text/html; charset=utf-8"),
}
IMAGEM_DOCS = re.compile(r"/img/([a-z0-9-]+\.png)")  # imagens da documentação (só .png de docs/img)
PECAS_PROMOCAO = {"q": chess.QUEEN, "r": chess.ROOK, "b": chess.BISHOP, "n": chess.KNIGHT}
MARCAS = {"BOOK": "livro", "SYZYGY_TB": "tb", "MANUAL": "manual", "EMERGENCY": "emerg."}
RITMOS = ("oficial", "armageddon", "edital", "personalizado")

# Peças desenhadas pelo próprio python-chess (as mesmas do tabuleiro SVG do main.py)
PECAS_SVG = {s: chess.svg.piece(chess.Piece.from_symbol(s)) for s in "PNBRQKpnbrqk"}


class ErroOperador(Exception):
    """Pedido que não faz sentido agora; a mensagem aparece na tela para o operador."""


# ==============================================================================
# Textos (as mesmas frases da TUI)
# ==============================================================================
def ler_num(texto) -> float:
    return float(str(texto).strip().replace(",", "."))


def fmt_numero(n: int) -> str:
    if n < 1000:
        return str(n)
    if n < 1_000_000:
        return f"{n / 1000:.0f} mil"
    return f"{n / 1_000_000:.1f} mi".replace(".", ",")


def fmt_tempo(segundos: float) -> str:
    if segundos < 10:
        return f"{segundos:.1f} s".replace(".", ",")
    if segundos < 60:
        return f"{segundos:.0f} s"
    minutos, seg = divmod(int(segundos), 60)
    return f"{minutos} min {seg:02d} s"


def fmt_avaliacao(cp: int) -> str:
    """Avaliação do nosso ponto de vista, em peões."""
    if abs(cp) >= MATE_THRESHOLD:
        lances = max(1, (MATE_SCORE - abs(cp) + 1) // 2)
        return f"mate em {lances} a nosso favor" if cp > 0 else f"mate em {lances} contra nós"
    if abs(cp) >= 19000:
        return "vitória de tablebase" if cp > 0 else "derrota de tablebase"
    peoes = f"{abs(cp) / 100:.2f}".replace(".", ",")
    if cp > 15:
        return f"+{peoes} a nosso favor"
    if cp < -15:
        return f"−{peoes} contra nós"
    return f"equilibrada ({'+' if cp >= 0 else '−'}{peoes})"


def fracao_avaliacao(cp: int) -> float:
    """De -1 (perdido) a +1 (ganho), para a barra de avaliação."""
    if abs(cp) >= 19000:
        return 1.0 if cp > 0 else -1.0
    return math.tanh(cp / 350)


def frase_resultado(placar: str, motivo: str, nossa_cor: bool) -> str:
    if placar == "1/2-1/2":
        return f"Empate: {motivo} ({placar})."
    vencemos = (placar == "1-0") == (nossa_cor == chess.WHITE)
    return f"{'Vencemos' if vencemos else 'Perdemos'}: {motivo} ({placar})."


# ==============================================================================
# Controlador: fases da partida e motor (como a tela da TUI, mas sem desenhar nada)
# ==============================================================================
class Controlador:
    def __init__(self) -> None:
        self.trava = threading.RLock()
        self.sessao: GameSession | None = None
        self.fase = "inicio"          # inicio, pensando, adversario, pausado, manual ou fim
        self.versao = 0               # muda a cada alteração; a página só redesenha quando muda
        self.parada: threading.Event | None = None
        self.thread: threading.Thread | None = None
        self.descartar = False        # a busca em andamento não deve virar lance
        self.jogar_quando_puder = False
        self.info: dict | None = None
        self.pensando_desde = 0.0
        self.ultimo_resultado = None
        self.ultimo_nosso = None
        self.mensagens: list[dict] = []
        self.proxima_mensagem = 1
        self.contador_som = 0
        self.som = True
        self.pedir_relogio = 0        # muda quando a página deve abrir o diálogo do relógio
        self.ao_sair = None           # o servidor preenche: desliga a interface
        try:
            self.tablebases = BookAndTablebaseManager().max_pieces()
        except Exception:  # noqa: BLE001 - só informativo
            self.tablebases = 0
        self.stockfish = encontrar_stockfish()

    # ------------------------------------------------------------------
    # Utilidades
    # ------------------------------------------------------------------
    def _mudou(self) -> None:
        self.versao += 1

    def _avisar(self, texto: str, tipo: str = "info", titulo: str = "") -> None:
        self.mensagens.append({"id": self.proxima_mensagem, "texto": texto, "tipo": tipo, "titulo": titulo})
        self.proxima_mensagem += 1
        self.mensagens = self.mensagens[-20:]
        self._mudou()

    def _exigir_partida(self) -> GameSession:
        if self.sessao is None:
            raise ErroOperador("Nenhuma partida em andamento.")
        return self.sessao

    def _entrar_turno(self, auto: bool) -> None:
        """Decide a fase depois de começar, desfazer, corrigir a posição ou retomar."""
        s = self.sessao
        if s.encerrada:
            self.fase = "fim"
        elif s.nossa_vez:
            if auto:
                self._iniciar_motor()
                return
            self.fase = "pausado"
        else:
            s.retomar_turno()
            self.fase = "adversario"
        self._mudou()

    # ------------------------------------------------------------------
    # Motor
    # ------------------------------------------------------------------
    def _iniciar_motor(self) -> None:
        """Chamar com a trava: a busca roda numa thread e aplica o lance ao terminar."""
        s = self.sessao
        parada = threading.Event()
        self.parada = parada
        self.descartar = False
        self.jogar_quando_puder = False
        self.info = None
        self.pensando_desde = time.perf_counter()
        self.fase = "pensando"
        board = s.board.copy()

        def info(profundidade, seletiva, nota, nos, tempo, pv):
            with self.trava:
                if parada is not self.parada:
                    return
                self.info = {"profundidade": profundidade, "seletiva": seletiva, "nota": nota,
                             "nos": nos, "tempo": tempo, "pv": [m.uci() for m in pv[:12]]}
                if self.jogar_quando_puder:
                    parada.set()
                self._mudou()

        def trabalho():
            resultado, erro = None, None
            try:
                resultado = s.pensar(board, parada, info)
            except Exception as exc:  # noqa: BLE001 - vira lance de emergência
                erro = exc
            with self.trava:
                if parada is self.parada:
                    self._concluir(resultado, erro)

        self.thread = threading.Thread(target=trabalho, name="busca", daemon=True)
        self.thread.start()
        self._mudou()

    def _concluir(self, resultado, erro) -> None:
        """Chamar com a trava."""
        self.parada = None
        self.thread = None
        if self.descartar:
            self.descartar = False
            self.fase = "pausado"
            self._mudou()
            return
        s = self.sessao
        if erro is not None:
            resultado = s.lance_emergencia(erro)
            self._avisar(f"A busca falhou ({erro}). Foi jogado um lance de emergência, escolhido em 1 lance "
                         "de profundidade. Desfazer (F2) se a equipe preferir outro.", "erro", "Lance de emergência")
        try:
            lance = s.registrar_nosso(resultado)
        except ValueError as exc:
            self._avisar(str(exc), "erro")
            self.fase = "pausado"
            self._mudou()
            return
        self.ultimo_nosso = lance
        self.ultimo_resultado = resultado
        self.contador_som += 1
        self.fase = "fim" if s.encerrada else "adversario"
        self._mudou()

    def _parar_motor(self) -> None:
        """Para a busca descartando o resultado. Chamar SEM a trava (espera a thread terminar)."""
        with self.trava:
            if self.fase != "pensando" or self.parada is None:
                return
            self.descartar = True
            self.parada.set()
            thread = self.thread
        if thread is not None:
            thread.join(timeout=10)

    # ------------------------------------------------------------------
    # Pedidos da página
    # ------------------------------------------------------------------
    def nova_partida(self, dados: dict) -> dict:
        config = self._config(dados)
        self._parar_motor()
        with self.trava:
            if self.sessao is not None:
                self.sessao.parar()
            if ler_partida_salva():
                arquivar_partida_salva()
        try:
            sessao = GameSession(config)
        except MotorIndisponivel as erro:
            raise ErroOperador(f"{erro} Escolha o motor próprio para jogar com ele.") from None
        with self.trava:
            self.sessao = sessao
            self.ultimo_nosso = None
            self.ultimo_resultado = None
            self.info = None
            self._entrar_turno(auto=True)
        return {"ok": True}

    def _config(self, dados: dict) -> Config:
        ritmo = dados.get("ritmo", "oficial")
        if ritmo not in RITMOS:
            raise ErroOperador("Ritmo desconhecido.")
        try:
            operador = ler_num(dados.get("operador", "4") or "4")
            minutos = ler_num(dados.get("minutos", "15") or "15")
            incremento = ler_num(dados.get("incremento", "0") or "0")
        except ValueError:
            raise ErroOperador("Confira os números: use algarismos, como 4 ou 2,5.") from None
        if not 0 <= operador <= 30:
            raise ErroOperador("O tempo do operador deve ficar entre 0 e 30 segundos.")
        if ritmo == "personalizado" and not (0.5 <= minutos <= 180 and 0 <= incremento <= 60):
            raise ErroOperador("Ritmo personalizado: de 0,5 a 180 minutos e até 60 segundos de incremento.")
        motor = dados.get("motor") or motor_padrao()
        if motor not in ("stockfish", "proprio"):
            raise ErroOperador("Motor desconhecido.")
        desempenho = dados.get("desempenho") or "ultra"
        if desempenho not in NIVEIS_DESEMPENHO:
            raise ErroOperador("Desempenho desconhecido: use Baixo, Médio ou Ultra.")
        return Config(
            our_color=chess.BLACK if dados.get("cor") == "b" else chess.WHITE,
            ritmo=ritmo, minutos=minutos, incremento=incremento,
            ponder=bool(dados.get("ponder", True)), operador=operador, motor=motor, desempenho=desempenho,
        )

    def retomar(self, dados: dict) -> dict:
        salvo = ler_partida_salva()
        if not salvo:
            raise ErroOperador("Não há partida salva para retomar.")
        try:
            sessao = GameSession.retomar(salvo)
        except Exception as exc:  # noqa: BLE001 - arquivo salvo corrompido
            raise ErroOperador(f"Não deu para retomar a partida salva: {exc}") from None
        with self.trava:
            self.sessao = sessao
            self.ultimo_nosso = sessao.lances[-1] if sessao.lances and sessao.lances[-1].nosso else None
            self.ultimo_resultado = None
            self.info = None
            self._entrar_turno(auto=False)
            self.pedir_relogio = self.versao
            self._avisar("Partida retomada. Confira os relógios físicos.", "info", "Retomar")
        return {"ok": True}

    def ler(self, dados: dict) -> dict:
        """Prévia do que está sendo digitado (não altera nada)."""
        with self.trava:
            if self.sessao is None or self.fase not in ("adversario", "manual"):
                return {"estado": "vazio", "mensagem": "", "origem": None, "destinos": []}
            leitura = ler_lance(self.sessao.board, str(dados.get("texto", "")))
            return {
                "estado": leitura.estado,
                "mensagem": leitura.mensagem,
                "origem": chess.square_name(leitura.origem) if leitura.origem is not None else None,
                "destinos": [chess.square_name(c) for c in leitura.destinos],
            }

    def registrar(self, dados: dict) -> dict:
        with self.trava:
            s = self._exigir_partida()
            if self.fase not in ("adversario", "manual"):
                raise ErroOperador("Agora não é hora de registrar lance.")
            leitura = ler_lance(s.board, str(dados.get("texto", "")))
            if leitura.estado == "promocao":
                letra = dados.get("promocao")
                if letra not in PECAS_PROMOCAO:
                    return {"promocao": True}
                move = chess.Move(leitura.lance.from_square, leitura.lance.to_square, PECAS_PROMOCAO[letra])
            elif leitura.estado == "ok":
                move = leitura.lance
            elif leitura.estado == "vazio":
                raise ErroOperador("Digite o lance antes de registrar.")
            else:
                raise ErroOperador(leitura.mensagem)
            if self.fase == "adversario":
                lance = s.registrar_adversario(move)
                self._avisar(f"{lance.uci}: {lance.descricao}.", "ok", "Lance do adversário registrado")
                if s.encerrada:
                    self.fase = "fim"
                else:
                    self._iniciar_motor()
            else:
                lance = s.registrar_manual(move)
                self.ultimo_nosso = lance
                self._avisar(f"{lance.uci}: {lance.descricao}.", "ok", "Lance manual registrado")
                self.fase = "fim" if s.encerrada else "adversario"
            self._mudou()
        return {"ok": True}

    def pensar(self, dados: dict) -> dict:
        with self.trava:
            self._exigir_partida()
            if self.fase != "pausado":
                raise ErroOperador("O motor só pensa na nossa vez.")
            self._iniciar_motor()
        return {"ok": True}

    def jogar_agora(self, dados: dict) -> dict:
        with self.trava:
            if self.fase != "pensando" or self.parada is None:
                raise ErroOperador("O motor não está pensando.")
            if self.info is not None:
                self.parada.set()
            else:
                self.jogar_quando_puder = True
        return {"ok": True}

    def desfazer(self, dados: dict) -> dict:
        n = 2 if int(dados.get("n", 1)) == 2 else 1
        esperado = dados.get("ultimo")
        self._parar_motor()
        with self.trava:
            s = self._exigir_partida()
            pilha = s.board.move_stack
            if not pilha:
                raise ErroOperador("Não há lance para desfazer.")
            if esperado and pilha[-1].uci() != esperado:
                raise ErroOperador("A posição mudou enquanto o diálogo estava aberto (o motor jogou). "
                                   "Confira e desfaça de novo se precisar.")
            desfeitos = s.desfazer(n)
            self.ultimo_nosso = s.lances[-1] if s.lances and s.lances[-1].nosso else None
            self._avisar(f"Desfeito: {', '.join(desfeitos)}. Se o tempo mudou, confira o relógio (F3).",
                         "info", "Desfazer")
            self._entrar_turno(auto=False)
        return {"ok": True}

    def relogio(self, dados: dict) -> dict:
        try:
            nosso = parse_clock_input(str(dados.get("nosso", "")))
            texto_adv = str(dados.get("adv", "")).strip()
            adv = parse_clock_input(texto_adv) if texto_adv else None
        except (ValueError, IndexError):
            raise ErroOperador("Não entendi o tempo. Use MM:SS, como 12:30, ou segundos, como 750.") from None
        with self.trava:
            self._exigir_partida().sincronizar(nosso, adv)
            self._avisar("Relógios sincronizados.", "ok")
        return {"ok": True}

    def ajustes(self, dados: dict) -> dict:
        try:
            operador = ler_num(dados.get("operador"))
            fator = ler_num(dados.get("fator"))
            contempt = int(str(dados.get("contempt")).strip())
        except (TypeError, ValueError):
            raise ErroOperador("Confira os números: use algarismos, como 4 ou 0,7.") from None
        if not 0 <= operador <= 30:
            raise ErroOperador("O tempo do operador deve ficar entre 0 e 30 segundos.")
        if not 0.1 <= fator <= 3:
            raise ErroOperador("O fator de tempo deve ficar entre 0,1 e 3.")
        if not -100 <= contempt <= 100:
            raise ErroOperador("O contempt deve ficar entre -100 e 100.")
        with self.trava:
            s = self._exigir_partida()
            s.ajustar(operador=operador, fator=fator, contempt=contempt, ponder=bool(dados.get("ponder")))
            self.som = bool(dados.get("som", True))
            self._avisar("Ajustes salvos.", "ok")
        return {"ok": True}

    def validar_fen(self, dados: dict) -> dict:
        board, mensagem = GameSession.validar_fen(str(dados.get("fen", "")))
        return {"valido": board is not None, "mensagem": mensagem}

    def posicao(self, dados: dict) -> dict:
        board, mensagem = GameSession.validar_fen(str(dados.get("fen", "")))
        if board is None:
            raise ErroOperador(mensagem)
        self._parar_motor()
        with self.trava:
            self._exigir_partida().definir_posicao(board)
            self.ultimo_nosso = None
            self._avisar("Posição atualizada. O histórico de repetição recomeça daqui.", "info", "Posição")
            self._entrar_turno(auto=False)
        return {"ok": True}

    def economia(self, dados: dict) -> dict:
        with self.trava:
            if self._exigir_partida().alternar_economia():
                self._avisar("Modo economia ligado: o motor usa metade do tempo por lance.", "info", "Economia")
            else:
                self._avisar("Modo economia desligado: tempo normal.", "info", "Economia")
        return {"ok": True}

    def avaliar(self, dados: dict) -> dict:
        with self.trava:
            nota = self._exigir_partida().avaliacao_estatica()
            self._avisar(f"Avaliação estática, sem busca: {fmt_avaliacao(nota)}.", "info", "Avaliar")
        return {"ok": True}

    def alternar_ponder(self, dados: dict) -> dict:
        with self.trava:
            s = self._exigir_partida()
            s.definir_ponder(not s.use_ponder)
            self._avisar(f"Pondering {'ligado' if s.use_ponder else 'desligado'}.", "info")
        return {"ok": True}

    def manual(self, dados: dict) -> dict:
        self._parar_motor()
        with self.trava:
            self._exigir_partida()
            if self.fase != "pausado":
                raise ErroOperador("Agora é a vez do adversário. Para trocar o nosso último lance, use Desfazer (F2).")
            self.fase = "manual"
            self._mudou()
        return {"ok": True}

    def cancelar_manual(self, dados: dict) -> dict:
        with self.trava:
            if self.fase == "manual":
                self.fase = "pausado"
                self._mudou()
        return {"ok": True}

    def encerrar(self, dados: dict) -> dict:
        escolha = dados.get("escolha")
        if escolha not in ("vitoria", "derrota", "empate", "sair"):
            raise ErroOperador("Escolha desconhecida.")
        self._parar_motor()
        with self.trava:
            s = self._exigir_partida()
            if escolha == "sair":
                s.parar()
                s.salvar()
                if self.ao_sair is not None:
                    self.ao_sair()
                return {"saindo": True}
            s.declarar_resultado(escolha)
            self.fase = "fim"
            self._mudou()
        return {"ok": True}

    def voltar_inicio(self, dados: dict) -> dict:
        self._parar_motor()
        with self.trava:
            if self.sessao is not None:
                self.sessao.parar()
            self.sessao = None
            self.fase = "inicio"
            self._mudou()
        return {"ok": True}

    def sair_sem_partida(self, dados: dict) -> dict:
        with self.trava:
            if self.sessao is not None:
                raise ErroOperador("Há uma partida em andamento: use Encerrar.")
        if self.ao_sair is not None:
            self.ao_sair()
        return {"saindo": True}

    def finalizar(self) -> None:
        """Servidor desligando: para o motor e o ponder e salva a partida."""
        self._parar_motor()
        with self.trava:
            if self.sessao is not None:
                self.sessao.parar()
                self.sessao.salvar()

    ROTAS = {
        "/api/nova": "nova_partida", "/api/retomar": "retomar", "/api/ler": "ler",
        "/api/lance": "registrar", "/api/pensar": "pensar", "/api/jogar-agora": "jogar_agora",
        "/api/desfazer": "desfazer", "/api/relogio": "relogio", "/api/ajustes": "ajustes",
        "/api/validar-fen": "validar_fen", "/api/posicao": "posicao", "/api/economia": "economia",
        "/api/avaliar": "avaliar", "/api/ponder": "alternar_ponder", "/api/manual": "manual",
        "/api/cancelar-manual": "cancelar_manual", "/api/encerrar": "encerrar",
        "/api/inicio": "voltar_inicio", "/api/sair": "sair_sem_partida",
    }

    def executar(self, rota: str, dados: dict) -> dict:
        nome = self.ROTAS.get(rota)
        if nome is None:
            raise ErroOperador("Comando desconhecido.")
        return getattr(self, nome)(dados)

    # ------------------------------------------------------------------
    # Estado para a página
    # ------------------------------------------------------------------
    def estado(self) -> dict:
        with self.trava:
            e = {
                "versao": self.versao, "fase": self.fase, "som": self.contador_som,
                "mensagens": self.mensagens[-8:], "pedir_relogio": self.pedir_relogio,
            }
            if self.sessao is None:
                e["inicio"] = {"salvo": self._resumo_salvo(), "tablebases": self.tablebases,
                               "stockfish": bool(self.stockfish),
                               "recursos": {nivel: list(recursos_do_nivel(nivel)) for nivel in NIVEIS_DESEMPENHO}}
                return e
            s = self.sessao
            board = s.board
            tm, motor = s.time_manager, s.engine
            nosso, adv = s.relogios()
            ultimo = board.peek() if board.move_stack else None
            correndo = None if s.encerrada else ("nosso" if board.turn == s.our_color else "adv")
            e.update({
                "nossa_cor": "w" if s.our_color == chess.WHITE else "b",
                "nome_cor": s.nome_cor,
                "cor_adv": COR_PT[not s.our_color],
                "ritmo": s.config.descricao_ritmo(),
                "nome_motor": s.nome_motor,
                "stockfish": s.usa_stockfish,
                "lance_num": board.fullmove_number,
                "vez": "w" if board.turn == chess.WHITE else "b",
                "fen": board.fen(),
                "pecas": {chess.square_name(c): p.symbol() for c, p in board.piece_map().items()},
                "ultimo": [chess.square_name(ultimo.from_square), chess.square_name(ultimo.to_square)] if ultimo else None,
                "xeque": chess.square_name(board.king(board.turn)) if board.is_check() else None,
                "legais": [m.uci() for m in board.legal_moves] if self.fase in ("adversario", "manual") else [],
                "relogio": {"nosso": nosso, "adv": adv, "correndo": correndo},
                "ultimo_nosso": ({"uci": self.ultimo_nosso.uci, "descricao": self.ultimo_nosso.descricao}
                                 if self.ultimo_nosso is not None else None),
                "motor": self._motor(),
                "pensando": self._pensando() if self.fase == "pensando" else None,
                "ponder": {"ligado": s.use_ponder, "previsto": s.lance_previsto},
                "ajustes": {"operador": tm.operator_overhead, "fator": tm.time_factor, "contempt": motor.contempt,
                            "economia": s.economia, "ponder": s.use_ponder, "som": self.som},
                "sumula": self._sumula(),
                "alertas": s.alertas(),
                "resultado": ({"placar": s.resultado[0], "motivo": s.resultado[1],
                               "frase": frase_resultado(s.resultado[0], s.resultado[1], s.our_color)}
                              if s.resultado else None),
                "log": {"arquivo": s.logger.log_path, "espelho": s.logger.txt_mirror_path},
                "desfazer": self._ultimos(),
            })
            return e

    def _resumo_salvo(self) -> str | None:
        dados = ler_partida_salva()
        if not dados:
            return None
        cor = COR_PT[dados["config"]["our_color"] == "w"]
        return (f"Há uma partida salva: jogando de {cor}, {len(dados['lances'])} meios-lances, "
                f"salva às {dados['salvo_em']}.")

    def _pensando(self) -> dict:
        tm = self.sessao.time_manager
        return {"decorrido": time.perf_counter() - self.pensando_desde,
                "alvo": getattr(tm, "soft_limit", 0.0) or 0.0, "maximo": getattr(tm, "hard_limit", 0.0) or 0.0}

    def _motor(self) -> dict | None:
        if self.fase == "pensando":
            if self.info is None:
                return {"procurando": True}
            i = self.info
            return self._linhas_motor(i["profundidade"], i["seletiva"], i["nota"], i["nos"], i["tempo"], i["pv"], None)
        r = self.ultimo_resultado
        if r is None:
            return None
        return self._linhas_motor(r.depth, r.seldepth, r.score_cp, r.nodes, r.elapsed_sec, r.pv, r.source)

    @staticmethod
    def _linhas_motor(prof, seletiva, nota, nos, tempo, pv, origem) -> dict:
        return {
            "origem": origem, "profundidade": prof, "seletiva": seletiva,
            "nos": fmt_numero(nos), "nps": fmt_numero(int(nos / max(0.001, tempo))), "tempo": fmt_tempo(tempo),
            "avaliacao": fmt_avaliacao(nota), "barra": fracao_avaliacao(nota), "pv": " ".join(pv[:10]),
        }

    def _sumula(self) -> list[dict]:
        s = self.sessao
        inicio = chess.Board(s.fen_inicial)
        numero, vez = inicio.fullmove_number, inicio.turn
        linhas: list[dict] = []
        atual = None
        for lance in s.lances:
            item = {"uci": lance.uci, "nosso": lance.nosso, "marca": MARCAS.get(lance.origem, "")}
            if vez == chess.WHITE:
                atual = {"num": numero, "brancas": item, "pretas": None}
                linhas.append(atual)
            else:
                if atual is None:
                    linhas.append({"num": numero, "brancas": None, "pretas": item})
                else:
                    atual["pretas"] = item
                atual = None
                numero += 1
            vez = not vez
        return linhas

    def _ultimos(self) -> list[dict]:
        s = self.sessao
        if s.lances:
            return [{"uci": l.uci, "nosso": l.nosso} for l in s.lances[-2:]]
        return [{"uci": m.uci(), "nosso": None} for m in s.board.move_stack[-2:]]


# ==============================================================================
# Servidor HTTP (só 127.0.0.1)
# ==============================================================================
class Manipulador(BaseHTTPRequestHandler):
    controlador: Controlador
    token: str = ""
    server_version = "ChessChallenge"

    def log_message(self, formato, *args) -> None:  # terminal limpo: sem uma linha por pedido
        pass

    def _enviar(self, corpo: bytes, tipo: str, status: int = 200) -> None:
        self.send_response(status)
        self.send_header("Content-Type", tipo)
        self.send_header("Content-Length", str(len(corpo)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(corpo)

    def _json(self, dados: dict, status: int = 200) -> None:
        self._enviar(json.dumps(dados, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8", status)

    def do_GET(self) -> None:
        caminho = urlparse(self.path).path
        if caminho == "/api/estado":
            self._json(self.controlador.estado())
        elif caminho == "/api/pecas":
            self._json(PECAS_SVG)
        elif caminho in ARQUIVOS:
            arquivo, tipo = ARQUIVOS[caminho]
            with open(arquivo, "rb") as f:
                corpo = f.read()
            if caminho == "/":
                corpo = corpo.replace(b"__TOKEN__", self.token.encode())
            self._enviar(corpo, tipo)
        elif (imagem := IMAGEM_DOCS.fullmatch(caminho)) and os.path.isfile(os.path.join(PASTA_DOCS, "img", imagem.group(1))):
            with open(os.path.join(PASTA_DOCS, "img", imagem.group(1)), "rb") as f:
                self._enviar(f.read(), "image/png")
        else:
            self._json({"erro": "Não encontrado."}, 404)

    def do_POST(self) -> None:
        # O token impede que outra página do navegador mande comandos para a partida
        if self.headers.get("X-Token") != self.token:
            self._json({"erro": "Pedido recusado (token inválido). Recarregue a página."}, 403)
            return
        try:
            tamanho = int(self.headers.get("Content-Length") or 0)
            dados = json.loads(self.rfile.read(tamanho) or b"{}")
        except ValueError:
            self._json({"erro": "Pedido mal formado."}, 400)
            return
        try:
            resposta = self.controlador.executar(urlparse(self.path).path, dados)
        except ErroOperador as exc:
            self._json({"erro": str(exc)}, 400)
            return
        except Exception as exc:  # noqa: BLE001 - nunca derruba o servidor no meio da partida
            traceback.print_exc()
            self._json({"erro": f"Falha interna: {exc}"}, 500)
            return
        self._json(resposta or {"ok": True})


def main() -> None:
    parser = argparse.ArgumentParser(description="Interface de torneio no navegador.")
    parser.add_argument("--porta", type=int, default=PORTA_PADRAO)
    parser.add_argument("--sem-navegador", action="store_true", help="não abre o navegador sozinho")
    args = parser.parse_args()

    verificar_stockfish()  # sem Stockfish, oferece instalar antes de abrir a página
    controlador = Controlador()
    Manipulador.controlador = controlador
    Manipulador.token = secrets.token_hex(16)
    servidor = None
    for porta in range(args.porta, args.porta + 10):
        try:
            servidor = ThreadingHTTPServer(("127.0.0.1", porta), Manipulador)
            break
        except OSError:
            continue
    if servidor is None:
        raise SystemExit(f"Nenhuma porta livre entre {args.porta} e {args.porta + 9}.")
    servidor.daemon_threads = True
    url = f"http://127.0.0.1:{porta}/"
    controlador.ao_sair = lambda: threading.Thread(target=servidor.shutdown, daemon=True).start()

    print("=" * 70)
    print("  Chess Challenge III - interface no navegador")
    print(f"  Endereço: {url}")
    print("  Se o navegador não abrir sozinho, abra o endereço acima.")
    print("  Para desligar: Ctrl+C aqui ou Encerrar > Sair na página.")
    print("=" * 70, flush=True)
    if not args.sem_navegador:
        webbrowser.open(url)
    try:
        servidor.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        controlador.finalizar()
        servidor.server_close()
        print("\n  Interface desligada. A partida fica salva: para voltar, rode python web.py.")


if __name__ == "__main__":
    main()
