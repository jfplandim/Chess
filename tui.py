"""
Interface de torneio (TUI) - Chess Challenge III - ICEV 2026

    python tui.py

Mostra, a cada momento, o que o operador precisa fazer no protocolo do FAQ Item 10:
anunciar o nosso lance em UCI, mover a peça, apertar o relógio e registrar o lance do
adversário (digitando ou clicando no tabuleiro). Os comandos ficam nas teclas F1 a F9 e
no rodapé, que também aceita clique. A lógica da partida está em session.py; o main.py
continua disponível como modo texto.
"""

from __future__ import annotations

import math
import threading
import time

import chess
from rich.text import Text
from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Center, Horizontal, Vertical, VerticalScroll
from textual.events import Click, Resize
from textual.message import Message
from textual.screen import ModalScreen, Screen
from textual.theme import Theme
from textual.widget import Widget
from textual.widgets import Button, Digits, Footer, Input, Label, Markdown, RadioButton, RadioSet, Static, Switch

from book_and_tb import BookAndTablebaseManager
from evaluation import MATE_SCORE, MATE_THRESHOLD
from motor_stockfish import (
    NOME_DESEMPENHO,
    MotorIndisponivel,
    encontrar_stockfish,
    fmt_threads,
    recursos_do_nivel,
    verificar_stockfish,
)
from recuperacao import resumo_da_partida
from session import (
    COR_PT,
    Config,
    GameSession,
    Lance,
    Leitura,
    arquivar_partida_salva,
    ler_lance,
    ler_relogio,
    ler_partida_salva,
)

# ==============================================================================
# Paleta "mesa de torneio": madeira do tabuleiro, latão do relógio, feltro da mesa
# (as mesmas cores do tui.tcss)
# ==============================================================================
MADEIRA = "#211A14"
PAINEL = "#2B221A"
BORDA = "#4A3B2C"
MARFIM = "#EFE3CC"
GRAFITE = "#A8977F"
LATAO = "#E8B44C"
FELTRO = "#7FB77E"
BANDEIRA = "#E0583F"
TINTA = "#1A120B"
CASA_CLARA = "#E8D3AE"
CASA_ESCURA = "#A87B52"
ULTIMO_CLARA = "#EDCB78"
ULTIMO_ESCURA = "#C49A46"
SELECAO_CLARA = "#AFD39C"
SELECAO_ESCURA = "#80A970"
CAPTURA_CLARA = "#EBA483"
CAPTURA_ESCURA = "#C57B59"
PONTO = "#2F5A2C"

TEMA = Theme(
    name="mesa-de-torneio",
    primary=LATAO,
    secondary=FELTRO,
    accent=LATAO,
    foreground=MARFIM,
    background=MADEIRA,
    surface=PAINEL,
    panel=BORDA,
    success=FELTRO,
    warning=LATAO,
    error=BANDEIRA,
    dark=True,
    variables={
        "footer-background": PAINEL,
        "footer-key-foreground": LATAO,
        "footer-description-foreground": GRAFITE,
        "input-selection-background": f"{LATAO} 35%",
        "border": LATAO,
        "border-blurred": BORDA,
    },
)

# ==============================================================================
# Letras de bloco para a placa de anúncio (5 linhas de altura)
# ==============================================================================
FONTE = {
    "A": (" ███ ", "█   █", "█████", "█   █", "█   █"),
    "B": ("████ ", "█   █", "████ ", "█   █", "████ "),
    "C": (" ████", "█    ", "█    ", "█    ", " ████"),
    "D": ("████ ", "█   █", "█   █", "█   █", "████ "),
    "E": ("█████", "█    ", "████ ", "█    ", "█████"),
    "F": ("█████", "█    ", "████ ", "█    ", "█    "),
    "G": (" ████", "█    ", "█  ██", "█   █", " ████"),
    "H": ("█   █", "█   █", "█████", "█   █", "█   █"),
    "N": ("█   █", "██  █", "█ █ █", "█  ██", "█   █"),
    "Q": (" ███ ", "█   █", "█ █ █", "█  █ ", " ██ █"),
    "R": ("████ ", "█   █", "████ ", "█  █ ", "█   █"),
    "1": ("  █  ", " ██  ", "  █  ", "  █  ", " ███ "),
    "2": (" ███ ", "█   █", "  ██ ", " █   ", "█████"),
    "3": ("████ ", "    █", " ███ ", "    █", "████ "),
    "4": ("█   █", "█   █", "█████", "    █", "    █"),
    "5": ("█████", "█    ", "████ ", "    █", "████ "),
    "6": (" ███ ", "█    ", "████ ", "█   █", " ███ "),
    "7": ("█████", "    █", "   █ ", "  █  ", "  █  "),
    "8": (" ███ ", "█   █", " ███ ", "█   █", " ███ "),
    " ": ("     ",) * 5,
}


def texto_lance(uci: str, estilo: str) -> Text:
    """O lance em letras de bloco, com um vão entre a casa de origem e a de destino."""
    partes = [uci[:2], uci[2:4]] + ([uci[4:]] if len(uci) > 4 else [])
    linhas = [""] * 5
    for p, parte in enumerate(partes):
        for c, letra in enumerate(parte.upper()):
            glifo = FONTE.get(letra, FONTE[" "])
            separador = "      " if (p and c == 0) else ("  " if c else "")
            for r in range(5):
                linhas[r] += separador + glifo[r]
    return Text("\n".join(linhas), style=estilo)


# ==============================================================================
# Formatação
# ==============================================================================
def fmt_relogio(segundos: float) -> str:
    minutos, seg = divmod(int(max(0.0, segundos)), 60)
    return f"{minutos:02d}:{seg:02d}"


def fmt_tempo(segundos: float) -> str:
    if segundos < 10:
        return f"{segundos:.1f} s".replace(".", ",")
    if segundos < 60:
        return f"{segundos:.0f} s"
    minutos, seg = divmod(int(segundos), 60)
    return f"{minutos} min {seg:02d} s"


def fmt_numero(n: int) -> str:
    if n < 1000:
        return str(n)
    if n < 1_000_000:
        return f"{n / 1000:.0f} mil"
    return f"{n / 1_000_000:.1f} mi".replace(".", ",")


def fmt_num(x: float) -> str:
    return f"{x:g}".replace(".", ",")


def ler_num(texto: str) -> float:
    return float(texto.strip().replace(",", "."))


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


def barra_avaliacao(cp: int, largura: int = 31) -> Text:
    meio = largura // 2
    fracao = (1.0 if cp > 0 else -1.0) if abs(cp) >= 19000 else math.tanh(cp / 350)
    cheio = round(abs(fracao) * meio)
    t = Text()
    for i in range(largura):
        if i == meio:
            t.append("┃", style=MARFIM)
        elif fracao > 0 and meio < i <= meio + cheio:
            t.append("━", style=FELTRO)
        elif fracao < 0 and meio - cheio <= i < meio:
            t.append("━", style=BANDEIRA)
        else:
            t.append("─", style=BORDA)
    return t


def barra_progresso(fracao: float, largura: int = 30) -> Text:
    cheio = round(min(1.0, max(0.0, fracao)) * largura)
    return Text.assemble(("━" * cheio, LATAO), ("─" * (largura - cheio), BORDA))


def frase_resultado(placar: str, motivo: str, nossa_cor: bool) -> str:
    if placar == "1/2-1/2":
        return f"Empate: {motivo} ({placar})."
    brancas_venceram = placar == "1-0"
    vencemos = brancas_venceram == (nossa_cor == chess.WHITE)
    return f"{'Vencemos' if vencemos else 'Perdemos'}: {motivo} ({placar})."


# ==============================================================================
# Mensagens da thread do motor (post_message é seguro entre threads)
# ==============================================================================
class InfoMotor(Message):
    def __init__(self, parada, profundidade, seletiva, nota, nos, tempo, pv) -> None:
        super().__init__()
        self.parada = parada
        self.profundidade = profundidade
        self.seletiva = seletiva
        self.nota = nota
        self.nos = nos
        self.tempo = tempo
        self.pv = pv


class MotorTerminou(Message):
    def __init__(self, parada, resultado) -> None:
        super().__init__()
        self.parada = parada
        self.resultado = resultado


class MotorFalhou(Message):
    def __init__(self, parada, erro) -> None:
        super().__init__()
        self.parada = parada
        self.erro = erro


# ==============================================================================
# Tabuleiro
# ==============================================================================
# As peças são fichas vistas de cima: a cor da ficha diz o lado, o símbolo diz a peça.
GLIFOS = {
    chess.KING: "♚", chess.QUEEN: "♛", chess.ROOK: "♜",
    chess.BISHOP: "♝", chess.KNIGHT: "♞", chess.PAWN: "♟",
}
LETRAS_PT = {
    chess.KING: "R", chess.QUEEN: "D", chess.ROOK: "T",
    chess.BISHOP: "B", chess.KNIGHT: "C", chess.PAWN: "P",
}
FICHA_BRANCA = "#F6EEDC"
FICHA_PRETA = "#2A1D14"


class Tabuleiro(Widget):
    """Tabuleiro do ponto de vista da nossa equipe. Clique na peça e depois no destino."""

    class CasaClicada(Message):
        def __init__(self, casa: int) -> None:
            super().__init__()
            self.casa = casa

    def __init__(self, sessao: GameSession, **kwargs) -> None:
        super().__init__(**kwargs)
        self.sessao = sessao
        self.simbolos = False  # letras (R, D, T, B, C, P) são nítidas em qualquer fonte de terminal
        self.origem: int | None = None
        self.destinos: set[int] = set()
        self.celula = (5, 2)

    def marcar(self, origem: int | None, destinos) -> None:
        self.origem = origem
        self.destinos = set(destinos or [])
        self.refresh()

    @staticmethod
    def medida(largura: int, altura: int) -> tuple[int, int]:
        """Maior casa (largura, altura) que cabe; a largura é ~2x a altura para parecer quadrada."""
        for alt in (3, 2, 1):
            larg = 2 * alt + 1
            if 8 * alt + 1 <= altura and 8 * larg + 3 <= largura:
                return larg, alt
        return 3, 1

    def _glifo(self, peca: chess.Piece) -> str:
        return GLIFOS[peca.piece_type] if self.simbolos else LETRAS_PT[peca.piece_type]

    def render(self) -> Text:
        board = self.sessao.board
        larg, alt = self.medida(self.size.width, self.size.height)
        self.celula = (larg, alt)
        linha_peca = (alt - 1) // 2          # linha da casa onde fica a letra da peça
        ficha = 5 if larg >= 7 else 3        # largura da ficha
        # bordas da ficha com cantos arredondados (meios-blocos e quadrantes)
        topo = "▗" + "▄" * (ficha - 2) + "▖"
        base = "▝" + "▀" * (ficha - 2) + "▘"
        brancas_embaixo = self.sessao.our_color == chess.WHITE
        fileiras = range(7, -1, -1) if brancas_embaixo else range(8)
        colunas = list(range(8)) if brancas_embaixo else list(range(7, -1, -1))
        ultimo = board.peek() if board.move_stack else None
        rei_em_xeque = board.king(board.turn) if board.is_check() else None
        t = Text()
        for fileira in fileiras:
            for sub in range(alt):
                t.append(f" {fileira + 1} " if sub == linha_peca else "   ", style=GRAFITE)
                for coluna in colunas:
                    casa = chess.square(coluna, fileira)
                    clara = (coluna + fileira) % 2 == 1
                    peca = board.piece_at(casa)
                    fundo = CASA_CLARA if clara else CASA_ESCURA
                    if ultimo is not None and casa in (ultimo.from_square, ultimo.to_square):
                        fundo = ULTIMO_CLARA if clara else ULTIMO_ESCURA
                    if casa == self.origem:
                        fundo = SELECAO_CLARA if clara else SELECAO_ESCURA
                    elif casa in self.destinos and peca is not None:
                        fundo = CAPTURA_CLARA if clara else CAPTURA_ESCURA
                    if casa == rei_em_xeque:
                        fundo = BANDEIRA
                    if peca is not None and abs(sub - linha_peca) <= 1:
                        branca = peca.color == chess.WHITE
                        cor_ficha = FICHA_BRANCA if branca else FICHA_PRETA
                        if sub == linha_peca:
                            texto = self._glifo(peca).center(ficha)
                            estilo = f"bold {TINTA if branca else FICHA_BRANCA} on {cor_ficha}"
                        else:
                            texto = topo if sub < linha_peca else base
                            estilo = f"{cor_ficha} on {fundo}"
                        margem = (larg - ficha) // 2
                        t.append(" " * margem, style=f"on {fundo}")
                        t.append(texto, style=estilo)
                        t.append(" " * (larg - ficha - margem), style=f"on {fundo}")
                    elif sub == linha_peca and casa in self.destinos:
                        t.append("•".center(larg), style=f"bold {PONTO} on {fundo}")
                    else:
                        t.append(" " * larg, style=f"on {fundo}")
                t.append("\n")
        t.append("   ")
        for coluna in colunas:
            t.append(chess.FILE_NAMES[coluna].center(larg), style=GRAFITE)
        return t

    def on_click(self, event: Click) -> None:
        larg, alt = self.celula
        if event.x < 3:
            return
        coluna_tela, linha_tela = (event.x - 3) // larg, event.y // alt
        if not (0 <= coluna_tela < 8 and 0 <= linha_tela < 8):
            return
        if self.sessao.our_color == chess.WHITE:
            casa = chess.square(coluna_tela, 7 - linha_tela)
        else:
            casa = chess.square(7 - coluna_tela, linha_tela)
        self.post_message(self.CasaClicada(casa))


# ==============================================================================
# Diálogos
# ==============================================================================
class Dialogo(ModalScreen):
    """Base dos diálogos: Esc cancela."""

    BINDINGS = [Binding("escape", "cancelar", "Cancelar")]

    def action_cancelar(self) -> None:
        self.dismiss(None)


class DialogoConfirmar(Dialogo):
    def __init__(self, titulo: str, texto: str, sim: str, nao: str) -> None:
        super().__init__()
        self.titulo, self.texto, self.sim, self.nao = titulo, texto, sim, nao

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialogo"):
            yield Label(self.titulo, classes="titulo")
            yield Static(self.texto, classes="texto")
            with Horizontal(classes="botoes"):
                yield Button(self.nao, id="nao")
                yield Button(self.sim, id="sim", variant="warning")

    def on_mount(self) -> None:
        self.query_one("#nao", Button).focus()

    @on(Button.Pressed)
    def _botao(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "sim")

    def action_cancelar(self) -> None:
        self.dismiss(False)


class DialogoDesfazer(Dialogo):
    BINDINGS = [
        Binding("escape", "cancelar", "Cancelar"),
        Binding("1", "escolher(1)", show=False),
        Binding("2", "escolher(2)", show=False),
    ]

    def __init__(self, sessao: GameSession) -> None:
        super().__init__()
        self.sessao = sessao

    def _ultimos(self) -> list[str]:
        """Descrição dos dois últimos meios-lances, do mais antigo para o mais recente."""
        if len(self.sessao.lances) >= len(self.sessao.board.move_stack[-2:]) and self.sessao.lances:
            return [f"{l.uci} ({'nosso' if l.nosso else 'do adversário'})" for l in self.sessao.lances[-2:]]
        return [m.uci() for m in self.sessao.board.move_stack[-2:]]

    def compose(self) -> ComposeResult:
        ultimos = self._ultimos()
        with Vertical(classes="dialogo"):
            yield Label("Desfazer lance", classes="titulo")
            yield Static(f"Último meio-lance: {ultimos[-1]}.", classes="texto")
            yield Static("O relógio interno não volta sozinho. Depois, confira com F3.", classes="nota")
            with Horizontal(classes="botoes"):
                yield Button("Cancelar", id="cancelar")
                if len(ultimos) >= 2:
                    yield Button("Desfazer os 2 últimos", id="dois")
                yield Button("Desfazer o último", id="um", variant="warning")

    def on_mount(self) -> None:
        self.query_one("#um", Button).focus()

    @on(Button.Pressed)
    def _botao(self, event: Button.Pressed) -> None:
        self.dismiss({"um": 1, "dois": 2}.get(event.button.id))

    def action_escolher(self, n: int) -> None:
        if n <= len(self.sessao.board.move_stack):
            self.dismiss(n)


class DialogoRelogio(Dialogo):
    def __init__(self, sessao: GameSession, aviso: str = "") -> None:
        super().__init__()
        self.sessao = sessao
        self.aviso = aviso
        self.confirmado: tuple | None = None  # valor suspeito digitado de novo = confirmado

    def compose(self) -> ComposeResult:
        nosso, adv = self.sessao.relogios()
        with Vertical(classes="dialogo"):
            yield Label("Sincronizar relógios", classes="titulo")
            yield Static(self.aviso or "Digite o que o relógio físico mostra agora. Ele é a referência.", classes="texto")
            with Horizontal(classes="campo"):
                yield Label(f"Nós ({self.sessao.nome_cor})", classes="rotulo")
                yield Input(fmt_relogio(nosso), id="nosso", max_length=8)
            with Horizontal(classes="campo"):
                yield Label(f"Adversário ({COR_PT[not self.sessao.our_color]})", classes="rotulo")
                yield Input(fmt_relogio(adv), id="adv", max_length=8)
            yield Static("Formato MM:SS, como 12:30, ou só segundos, como 750.", classes="nota")
            yield Static("", id="erro", classes="erro")
            with Horizontal(classes="botoes"):
                yield Button("Cancelar", id="cancelar")
                yield Button("Sincronizar", id="ok", variant="primary")

    def on_mount(self) -> None:
        self.query_one("#nosso", Input).focus()

    @on(Input.Submitted, "#nosso")
    def _proximo(self, event: Input.Submitted) -> None:
        self.query_one("#adv", Input).focus()

    @on(Input.Submitted, "#adv")
    @on(Button.Pressed, "#ok")
    def _aplicar(self, event) -> None:
        try:
            nosso = ler_relogio(self.query_one("#nosso", Input).value)
            texto_adv = self.query_one("#adv", Input).value.strip()
            adv = ler_relogio(texto_adv) if texto_adv else None
        except ValueError as erro:
            self.query_one("#erro", Static).update(str(erro))
            return
        suspeita = self.sessao.suspeita_relogio(nosso, adv)
        if suspeita and self.confirmado != (nosso, adv):
            self.confirmado = (nosso, adv)
            self.query_one("#erro", Static).update(f"{suspeita} Enter de novo confirma.")
            return
        self.dismiss((nosso, adv))

    @on(Button.Pressed, "#cancelar")
    def _cancelar(self, event: Button.Pressed) -> None:
        self.dismiss(None)


class DialogoAjustes(Dialogo):
    def __init__(self, sessao: GameSession, som: bool, simbolos: bool) -> None:
        super().__init__()
        self.sessao, self.som, self.simbolos = sessao, som, simbolos

    def compose(self) -> ComposeResult:
        tm, motor = self.sessao.time_manager, self.sessao.engine
        with Vertical(classes="dialogo largo"):
            yield Label("Ajustes", classes="titulo")
            with Horizontal(classes="campo"):
                yield Label("Operador (s por lance)", classes="rotulo")
                yield Input(fmt_num(tm.operator_overhead), id="operador", max_length=5)
            yield Static("Tempo para anunciar, mover a peça e apertar o relógio. Meçam no treino.", classes="nota")
            with Horizontal(classes="campo"):
                yield Label("Fator de tempo", classes="rotulo")
                yield Input(fmt_num(tm.time_factor), id="fator", max_length=5)
            yield Static("1 é o normal. 0,5 pensa metade do tempo; 1,3 pensa mais.", classes="nota")
            with Horizontal(classes="campo"):
                yield Label("Contempt (cp)", classes="rotulo")
                yield Input(str(motor.contempt), id="contempt", max_length=4)
            yield Static("Positivo evita o empate, negativo procura o empate. No Armageddon já vem ajustado. "
                         "No Stockfish vale para o lance que empata na hora: ele só é jogado se a melhor "
                         "alternativa for pior que -contempt.", classes="nota")
            with Horizontal(classes="campo"):
                yield Label("Pondering", classes="rotulo")
                yield Switch(self.sessao.use_ponder, id="ponder")
            with Horizontal(classes="campo"):
                yield Label("Som quando o motor joga", classes="rotulo")
                yield Switch(self.som, id="som")
            with Horizontal(classes="campo"):
                yield Label("Peças como símbolos (♞)", classes="rotulo")
                yield Switch(self.simbolos, id="simbolos")
            yield Static("Desligado, as peças aparecem como letras: R rei, D dama, T torre, B bispo, C cavalo, P peão.",
                         classes="nota")
            yield Static("", id="erro", classes="erro")
            with Horizontal(classes="botoes"):
                yield Button("Cancelar", id="cancelar")
                yield Button("Salvar ajustes", id="ok", variant="primary")

    def on_mount(self) -> None:
        self.query_one("#operador", Input).focus()

    @on(Input.Submitted)
    @on(Button.Pressed, "#ok")
    def _salvar(self, event) -> None:
        erro = self.query_one("#erro", Static)
        try:
            operador = ler_num(self.query_one("#operador", Input).value)
            fator = ler_num(self.query_one("#fator", Input).value)
            contempt = int(self.query_one("#contempt", Input).value.strip())
        except ValueError:
            erro.update("Confira os números: use algarismos, como 4 ou 0,7.")
            return
        if not 0 <= operador <= 30:
            erro.update("O tempo do operador deve ficar entre 0 e 30 segundos.")
            return
        if not 0.1 <= fator <= 3:
            erro.update("O fator de tempo deve ficar entre 0,1 e 3.")
            return
        if not -100 <= contempt <= 100:
            erro.update("O contempt deve ficar entre -100 e 100.")
            return
        self.dismiss({
            "operador": operador,
            "fator": fator,
            "contempt": contempt,
            "ponder": self.query_one("#ponder", Switch).value,
            "som": self.query_one("#som", Switch).value,
            "simbolos": self.query_one("#simbolos", Switch).value,
        })

    @on(Button.Pressed, "#cancelar")
    def _cancelar(self, event: Button.Pressed) -> None:
        self.dismiss(None)


class DialogoPosicao(Dialogo):
    def __init__(self, sessao: GameSession) -> None:
        super().__init__()
        self.sessao = sessao
        self.board: chess.Board | None = sessao.board.copy()

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialogo largo"):
            yield Label("Corrigir a posição", classes="titulo")
            yield Static("O tabuleiro físico é a referência. Cole ou digite o FEN da posição real.", classes="texto")
            yield Input(self.sessao.board.fen(), id="fen")
            yield Static("", id="validacao", classes="nota")
            yield Static("O histórico de repetição recomeça a partir desta posição.", classes="nota")
            with Horizontal(classes="botoes"):
                yield Button("Cancelar", id="cancelar")
                yield Button("Posição inicial", id="inicial")
                yield Button("Aplicar posição", id="ok", variant="primary")

    def on_mount(self) -> None:
        self.query_one("#fen", Input).focus()
        self._validar(self.query_one("#fen", Input).value)

    def _validar(self, fen: str) -> None:
        self.board, mensagem = GameSession.validar_fen(fen)
        cor = FELTRO if self.board is not None else BANDEIRA
        self.query_one("#validacao", Static).update(Text(mensagem, style=cor))

    @on(Input.Changed, "#fen")
    def _mudou(self, event: Input.Changed) -> None:
        self._validar(event.value)

    @on(Input.Submitted, "#fen")
    @on(Button.Pressed, "#ok")
    def _aplicar(self, event) -> None:
        if self.board is not None:
            self.dismiss(self.board)

    @on(Button.Pressed, "#inicial")
    def _inicial(self, event: Button.Pressed) -> None:
        self.dismiss(chess.Board())

    @on(Button.Pressed, "#cancelar")
    def _cancelar(self, event: Button.Pressed) -> None:
        self.dismiss(None)


class DialogoPromocao(Dialogo):
    BINDINGS = [
        Binding("escape", "cancelar", "Cancelar"),
        Binding("q,d", "escolher('q')", show=False),
        Binding("r,t", "escolher('r')", show=False),
        Binding("b", "escolher('b')", show=False),
        Binding("n,c", "escolher('n')", show=False),
    ]
    PECAS = {"q": chess.QUEEN, "r": chess.ROOK, "b": chess.BISHOP, "n": chess.KNIGHT}

    def __init__(self, pergunta: str) -> None:
        super().__init__()
        self.pergunta = pergunta

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialogo"):
            yield Label("Promoção", classes="titulo")
            yield Static(self.pergunta, classes="texto")
            with Horizontal(classes="botoes"):
                yield Button("Cavalo (n)", id="p-n")
                yield Button("Bispo (b)", id="p-b")
                yield Button("Torre (r)", id="p-r")
                yield Button("Dama (q)", id="p-q", variant="primary")

    def on_mount(self) -> None:
        self.query_one("#p-q", Button).focus()

    @on(Button.Pressed)
    def _botao(self, event: Button.Pressed) -> None:
        self.dismiss(self.PECAS[event.button.id[2:]])

    def action_escolher(self, letra: str) -> None:
        self.dismiss(self.PECAS[letra])


class DialogoEncerrar(Dialogo):
    def compose(self) -> ComposeResult:
        with Vertical(classes="dialogo largo"):
            yield Label("Encerrar a partida", classes="titulo")
            yield Static(
                "Use quando o árbitro declarar o resultado: abandono, acordo de empate ou queda de "
                "bandeira. O resultado vai para o log.",
                classes="texto",
            )
            with Horizontal(classes="botoes"):
                yield Button("Empate", id="empate")
                yield Button("Perdemos", id="derrota", variant="error")
                yield Button("Vencemos", id="vitoria", variant="success")
            yield Static("Para fechar sem resultado e continuar depois, use Sair: a partida fica salva.", classes="nota")
            with Horizontal(classes="botoes"):
                yield Button("Sair e retomar depois", id="sair")
                yield Button("Voltar à partida", id="voltar", variant="primary")

    def on_mount(self) -> None:
        self.query_one("#voltar", Button).focus()

    @on(Button.Pressed)
    def _botao(self, event: Button.Pressed) -> None:
        self.dismiss(None if event.button.id == "voltar" else event.button.id)


class DialogoFim(Dialogo):
    def __init__(self, sessao: GameSession) -> None:
        super().__init__()
        self.sessao = sessao

    def compose(self) -> ComposeResult:
        placar, motivo = self.sessao.resultado or ("*", "")
        log = self.sessao.logger
        with Vertical(classes="dialogo largo"):
            yield Label("Fim da partida", classes="titulo")
            yield Static(frase_resultado(placar, motivo, self.sessao.our_color), classes="destaque")
            yield Static(f"Log para a banca:\n{log.log_path}\n{log.txt_mirror_path}", classes="nota")
            with Horizontal(classes="botoes"):
                yield Button("Copiar caminho do log", id="copiar")
                yield Button("Desfazer o último lance", id="desfazer")
                yield Button("Sair", id="sair")
                yield Button("Nova partida", id="nova", variant="primary")

    def on_mount(self) -> None:
        self.query_one("#nova", Button).focus()

    @on(Button.Pressed)
    def _botao(self, event: Button.Pressed) -> None:
        if event.button.id == "copiar":
            self.app.copy_to_clipboard(self.sessao.logger.log_path)
            self.app.notify("Caminho do log copiado.", timeout=3)
            return
        self.dismiss(event.button.id)


AJUDA = """\
# Como jogar com a interface

## A cada lance nosso
1. A placa **Agora** mostra o lance em letras grandes. Anuncie em voz alta, em UCI, e espere o adversário repetir.
2. Mova a peça no tabuleiro físico. A placa descreve o movimento, inclusive roque e promoção.
3. Aperte o relógio.

## A cada lance do adversário
4. Digite o lance anunciado e aperte **Enter**. Antes do Enter, a linha ao lado diz se o lance é legal e o que ele faz.
   Também dá para clicar na peça e depois na casa de destino; o Enter confirma.

## Teclas
| Tecla | O que faz |
|---|---|
| Enter | Registra o lance digitado. Com o motor pausado, manda o motor pensar |
| Espaço | Enquanto o motor pensa, joga agora com o melhor lance encontrado |
| Esc | Limpa o que foi digitado |
| F2 | Desfaz o último meio-lance (para o motor se ele estiver pensando) |
| F3 | Sincroniza com o relógio físico |
| F4 | Ajustes: operador, fator de tempo, contempt, pondering, som, peças |
| F5 | Corrige a posição por FEN (o tabuleiro físico é a referência) |
| F6 | Modo economia: o motor usa metade do tempo por lance |
| F7 | Avaliação estática da posição, sem busca |
| F8 | Liga ou desliga o pondering |
| F9 | Registra um lance nosso diferente do motor (regra da peça tocada) |
| Ctrl+Q | Encerra: resultado declarado pelo árbitro, ou sair para retomar depois |

O rodapé também aceita clique. Se o terminal capturar alguma tecla F (como F1 no GNOME Terminal), clique nela no rodapé.

## Situações comuns
- **Digitei errado o lance do adversário:** F2 desfaz e o motor para. Digite o lance certo.
- **O tabuleiro físico ficou diferente:** F5 e o FEN da posição real.
- **Relógio (F3):** sincronize a cada uns 10 lances. Isso também mede o operador: se ele estiver mais lento do que o configurado, o custo por lance sobe sozinho.
- **Desempate da melhor de 3 (FAQ item 12):** vale o tempo restante somado nas três partidas. Em posição morta, F6 economiza relógio.
- **Armageddon:** o empate classifica as Pretas. Com o motor próprio, o contempt já vem ajustado. Com o Stockfish, a regra do empate imediato: com Pretas, se algum lance empata na hora (repetição, 50 lances, afogamento, material insuficiente), o motor joga esse lance; com Brancas, nunca joga lance que empata na hora nem deixa o adversário empatar no lance seguinte. Sem incremento o relógio acaba sozinho (operador): a tela avisa quantos lances faltam para a bandeira de cada lado.
- **Motor:** o topo mostra qual está jogando. O Stockfish busca e avalia; quem decide quanto ele pensa é a nossa gestão de tempo. Se ele cair ou travar, vale o melhor lance que ele já tinha e o processo reabre; se não reabrir, a partida segue com o motor próprio e a tela avisa.
- **O notebook travou ou a janela fechou:** rode `python tui.py` de novo e escolha **Retomar partida**. Depois confira os relógios.
- **A busca falhou:** só se nem o motor próprio conseguir buscar. A interface joga um lance de emergência (avaliação em 1 lance), avisa e registra no log. F2 desfaz se a equipe preferir outro lance.
- **A interface não abre:** o modo texto continua em `python main.py`.

No tabuleiro, fichas claras são as Brancas e fichas escuras são as Pretas. As letras são R rei, D dama, T torre, B bispo, C cavalo e P peão (F4 troca por símbolos).

Na súmula, os lances nossos aparecem em amarelo; *livro* indica lance do livro de aberturas, *tb* de tablebase e *manual* lance registrado pela equipe.

O log oficial da banca (FAQ item 9) é gravado a cada lance na pasta `logs/`.

A documentação completa do projeto está em `docs/index.html` (abra no navegador, funciona sem internet).
"""


class TelaAjuda(ModalScreen):
    BINDINGS = [Binding("escape,f1", "fechar", "Fechar")]

    def compose(self) -> ComposeResult:
        with Vertical(id="ajuda"):
            with VerticalScroll():
                yield Markdown(AJUDA)
            yield Static("Esc fecha a ajuda.", classes="nota")

    def action_fechar(self) -> None:
        self.dismiss(None)


# ==============================================================================
# Tela inicial
# ==============================================================================
class TelaInicio(Screen):
    BINDINGS = [
        Binding("b", "cor('w')", show=False),
        Binding("p", "cor('b')", show=False),
        Binding("1", "ritmo('oficial')", show=False),
        Binding("2", "ritmo('armageddon')", show=False),
        Binding("3", "ritmo('edital')", show=False),
        Binding("4", "ritmo('personalizado')", show=False),
        Binding("o", "alternar_ponder", show=False),
        Binding("m", "alternar_motor", show=False),
        Binding("d", "alternar_desempenho", show=False),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.salvo = ler_partida_salva()
        self.stockfish = encontrar_stockfish()

    def compose(self) -> ComposeResult:
        with Center():
            with Vertical(id="inicio"):
                yield Static(id="titulo")
                if self.salvo:
                    with Horizontal(id="salvo"):
                        yield Static(self._texto_salvo(), id="salvo-texto")
                        yield Button("Retomar partida", id="retomar", variant="warning")
                with Horizontal(id="opcoes"):
                    with Vertical(classes="grupo"):
                        yield Label("Nossa cor (B ou P)", classes="rotulo-grupo")
                        with RadioSet(id="cor"):
                            yield RadioButton("Brancas", value=True, id="cor-w")
                            yield RadioButton("Pretas", id="cor-b")
                        yield Label("Motor (M)", classes="rotulo-grupo")
                        with RadioSet(id="escolha-motor"):
                            yield RadioButton("Stockfish", value=bool(self.stockfish), id="m-stockfish",
                                              disabled=not self.stockfish)
                            yield RadioButton("Próprio, reserva", value=not self.stockfish, id="m-proprio")
                    with Vertical(classes="grupo"):
                        yield Label("Ritmo (1 a 4)", classes="rotulo-grupo")
                        with RadioSet(id="ritmo"):
                            yield RadioButton("15 + 10, oficial", value=True, id="r-oficial")
                            yield RadioButton("Armageddon", id="r-armageddon")
                            yield RadioButton("25 + 0", id="r-edital")
                            yield RadioButton("Personalizado", id="r-personalizado")
                        with Horizontal(id="personalizado"):
                            yield Input("15", id="minutos", max_length=4)
                            yield Label("min +", classes="unidade")
                            yield Input("10", id="incremento", max_length=4)
                            yield Label("s", classes="unidade")
                        with Horizontal(id="armageddon-inc"):
                            yield Label("incremento", classes="unidade")
                            yield Input("0", id="inc-armageddon", max_length=4)
                            yield Label("s (confirme com o árbitro)", classes="unidade")
                    with Vertical(classes="grupo"):
                        yield Label("Pondering (O)", classes="rotulo-grupo")
                        yield Switch(True, id="ponder")
                        yield Label("Operador, segundos por lance", classes="rotulo-grupo")
                        yield Input("4", id="operador", max_length=4)
                        yield Label("Desempenho do Stockfish (D)", classes="rotulo-grupo")
                        with RadioSet(id="escolha-desempenho"):
                            yield RadioButton("Ultra, força total", value=True, id="d-ultra")
                            yield RadioButton("Médio", id="d-medio")
                            yield RadioButton("Baixo", id="d-baixo")
                yield Static(id="resumo")
                yield Static(id="ambiente")
                yield Button("Começar partida", id="comecar", variant="primary")
                yield Static("Enter começa a partida. Ctrl+Q sai.", id="dica-inicio")

    def on_mount(self) -> None:
        titulo = Text()
        titulo.append("Chess Challenge III\n", style=f"bold {LATAO}")
        titulo.append("ICEV 2026, interface de torneio", style=GRAFITE)
        self.query_one("#titulo", Static).update(titulo)
        for grupo in self.query(RadioSet):
            grupo.can_focus = False  # Enter fica reservado para começar a partida
        self.query_one("#comecar", Button).focus()
        try:
            pecas = BookAndTablebaseManager().max_pieces()
        except Exception:  # noqa: BLE001 - só informativo
            pecas = 0
        livro = "Livro de aberturas ativo até o 10º meio-lance."
        if pecas:
            ambiente = f"Tablebases Syzygy prontas, até {pecas} peças. {livro}"
        else:
            ambiente = f"Tablebases Syzygy não encontradas: o motor joga normalmente sem elas. {livro}"
        if not self.stockfish:
            ambiente += "\nStockfish não encontrado: só o motor próprio. Instale com python tools/baixar_stockfish.py"
        self.query_one("#ambiente", Static).update(ambiente)
        self._atualizar()

    def _texto_salvo(self) -> str:
        return resumo_da_partida(self.salvo)  # o mesmo texto no navegador (recuperacao.py)

    def _ritmo(self) -> str:
        botao = self.query_one("#ritmo", RadioSet).pressed_button
        return botao.id[2:] if botao is not None and botao.id else "oficial"

    def _config(self) -> Config | str:
        """Configuração escolhida, ou uma mensagem dizendo o que corrigir."""
        try:
            operador = ler_num(self.query_one("#operador", Input).value or "4")
            minutos = ler_num(self.query_one("#minutos", Input).value or "15")
            incremento = ler_num(self.query_one("#incremento", Input).value or "0")
            inc_armageddon = ler_num(self.query_one("#inc-armageddon", Input).value or "0")
        except ValueError:
            return "Confira os números: use algarismos, como 4 ou 2,5."
        ritmo = self._ritmo()
        if not 0 <= operador <= 30:
            return "O tempo do operador deve ficar entre 0 e 30 segundos."
        if ritmo == "personalizado" and not (0.5 <= minutos <= 180 and 0 <= incremento <= 60):
            return "Ritmo personalizado: de 0,5 a 180 minutos e até 60 segundos de incremento."
        if ritmo == "armageddon" and not 0 <= inc_armageddon <= 60:
            return "Incremento do Armageddon: de 0 a 60 segundos."
        return Config(
            our_color=not self.query_one("#cor-b", RadioButton).value,
            ritmo=ritmo,
            minutos=minutos,
            incremento=incremento,
            ponder=self.query_one("#ponder", Switch).value,
            operador=operador,
            motor="proprio" if self.query_one("#m-proprio", RadioButton).value else "stockfish",
            desempenho=self._desempenho(),
            inc_armageddon=inc_armageddon if ritmo == "armageddon" else 0.0,
        )

    def _desempenho(self) -> str:
        botao = self.query_one("#escolha-desempenho", RadioSet).pressed_button
        return botao.id[2:] if botao is not None and botao.id else "ultra"

    def _atualizar(self) -> None:
        self.query_one("#personalizado").display = self._ritmo() == "personalizado"
        self.query_one("#armageddon-inc").display = self._ritmo() == "armageddon"
        config = self._config()
        resumo = self.query_one("#resumo", Static)
        if isinstance(config, str):
            resumo.update(Text(config, style=BANDEIRA))
            return
        minutos, inc = config.relogio()
        cor = COR_PT[config.our_color]
        t = Text()
        if config.ritmo == "armageddon":
            sem_inc = "sem incremento" if not inc else f"{inc:g} segundos de incremento"
            t.append(f"Jogamos de {cor}, Armageddon com {minutos:g} minutos e {sem_inc}.\n", style=MARFIM)
            if config.motor == "stockfish" and config.our_color == chess.BLACK:
                t.append("O empate classifica as Pretas: se algum lance empatar na hora, o motor joga esse lance.",
                         style=LATAO)
            elif config.motor == "stockfish":
                t.append("O empate classifica as Pretas: o motor não joga lance que empata na hora nem deixa o "
                         "adversário empatar no lance seguinte.", style=LATAO)
            elif config.our_color == chess.BLACK:
                t.append("O empate classifica as Pretas: o motor vai procurar o empate (contempt −100).", style=LATAO)
            else:
                t.append("O empate classifica as Pretas: o motor vai evitar o empate (contempt +100).", style=LATAO)
        else:
            t.append(f"Jogamos de {cor}, {minutos:g} minutos e {inc:g} segundos de incremento por lance.", style=MARFIM)
        self.query_one("#escolha-desempenho", RadioSet).disabled = config.motor != "stockfish"
        if config.motor == "stockfish":
            threads, tabela = recursos_do_nivel(config.desempenho)
            total = " (força total)" if config.desempenho == "ultra" else ""
            t.append("\nMotor: Stockfish na busca e na avaliação, com a nossa gestão de tempo. "
                     f"Desempenho {NOME_DESEMPENHO[config.desempenho]}{total}: {fmt_threads(threads)} e {tabela} MB.",
                     style=MARFIM)
        else:
            t.append("\nMotor: o próprio da equipe (reserva), que usa um processador só.", style=MARFIM)
        t.append(
            f"\nPondering {'ligado' if config.ponder else 'desligado'}. "
            f"Operador: {fmt_num(config.operador)} s por lance.",
            style=GRAFITE,
        )
        resumo.update(t)

    @on(RadioSet.Changed)
    @on(Switch.Changed)
    @on(Input.Changed)
    def _mudou(self, event) -> None:
        self._atualizar()

    def action_cor(self, cor: str) -> None:
        self.query_one(f"#cor-{cor}", RadioButton).value = True

    def action_ritmo(self, ritmo: str) -> None:
        self.query_one(f"#r-{ritmo}", RadioButton).value = True

    def action_alternar_ponder(self) -> None:
        chave = self.query_one("#ponder", Switch)
        chave.value = not chave.value

    def action_alternar_motor(self) -> None:
        if not self.stockfish:
            self.app.notify("Stockfish não encontrado: instale com python tools/baixar_stockfish.py", severity="warning")
            return
        proprio = self.query_one("#m-proprio", RadioButton)
        alvo = "#m-stockfish" if proprio.value else "#m-proprio"
        self.query_one(alvo, RadioButton).value = True

    def action_alternar_desempenho(self) -> None:
        if self.query_one("#m-proprio", RadioButton).value:
            self.app.notify("O desempenho vale para o Stockfish; o motor próprio usa um processador só.")
            return
        seguinte = {"ultra": "medio", "medio": "baixo", "baixo": "ultra"}[self._desempenho()]
        self.query_one(f"#d-{seguinte}", RadioButton).value = True

    @on(Button.Pressed, "#comecar")
    @on(Input.Submitted)
    def _comecar(self, event) -> None:
        config = self._config()
        if isinstance(config, str):
            self.app.notify(config, severity="error")
            return
        try:
            sessao = GameSession(config)
        except MotorIndisponivel as erro:
            self.app.notify(f"{erro} Escolha o motor próprio (M) para jogar com ele.", severity="error", timeout=10)
            return
        if self.salvo:
            arquivar_partida_salva()
        self.app.switch_screen(TelaPartida(sessao))

    @on(Button.Pressed, "#retomar")
    def _retomar(self, event: Button.Pressed) -> None:
        try:
            sessao = GameSession.retomar(self.salvo)
        except Exception as exc:  # noqa: BLE001 - arquivo salvo corrompido
            self.app.notify(f"Não deu para retomar a partida salva: {exc}", severity="error")
            return
        aviso = sessao.tomar_aviso()
        if aviso:
            self.app.notify(aviso, severity="error", timeout=15)
        self.app.switch_screen(TelaPartida(sessao, retomada=True))


# ==============================================================================
# Tela da partida
# ==============================================================================
MENSAGEM_FASE = {
    "pensando": "O motor está pensando. O lance aparece na placa Agora.",
    "pausado": "Motor pausado. Enter faz o motor pensar.",
    "fim": "Partida encerrada.",
}


class TelaPartida(Screen):
    BINDINGS = [
        Binding("f1", "ajuda", "Ajuda", key_display="F1"),
        Binding("f2", "desfazer", "Desfazer", key_display="F2"),
        Binding("f3", "relogio", "Relógio", key_display="F3"),
        Binding("f4", "ajustes", "Ajustes", key_display="F4"),
        Binding("f5", "posicao", "Posição", key_display="F5"),
        Binding("f6", "economia", "Economia", key_display="F6"),
        Binding("f7", "avaliar", "Avaliar", key_display="F7"),
        Binding("f8", "ponder", "Ponder", key_display="F8"),
        Binding("f9", "manual", "Lance manual", key_display="F9"),
        Binding("ctrl+q", "encerrar", "Encerrar", key_display="Ctrl+Q"),
        Binding("space", "jogar_agora", "Jogar agora", show=False),
        Binding("enter", "pensar", "Motor pensa", show=False),
        Binding("escape", "limpar", "Limpar", show=False),
    ]

    def __init__(self, sessao: GameSession, retomada: bool = False) -> None:
        super().__init__()
        self.sessao = sessao
        self.retomada = retomada
        self.fase = "adversario"      # pensando, adversario, pausado, manual ou fim
        self.parada: threading.Event | None = None
        self.descartar = False         # a busca em andamento não deve virar lance
        self.segurar = False           # um diálogo está aberto: guarda o resultado do motor
        self.retido = None
        self.apos_parar = None         # ação a executar quando o motor terminar de parar
        self.jogar_quando_puder = False
        self.tem_info = False
        self.pensando_desde = 0.0
        self.info: InfoMotor | None = None
        self.ultimo_resultado = None
        self.ultimo_nosso: Lance | None = None
        self.leitura: Leitura | None = None
        self.som = True
        self.avisou_janela = False

    # ------------------------------------------------------------------
    # Montagem
    # ------------------------------------------------------------------
    def compose(self) -> ComposeResult:
        with Horizontal(id="topo"):
            yield Static(id="topo-esq")
            yield Static(id="topo-dir")
        with Horizontal(id="corpo"):
            with Vertical(id="esquerda"):
                yield Tabuleiro(self.sessao, id="tabuleiro")
                with VerticalScroll(id="sumula-rolagem"):
                    yield Static(id="sumula")
            with Vertical(id="direita"):
                yield Static(id="agora")
                with Horizontal(id="relogios"):
                    with Vertical(id="relogio-nosso", classes="relogio"):
                        yield Digits("00:00", id="digitos-nosso")
                    with Vertical(id="relogio-adv", classes="relogio"):
                        yield Digits("00:00", id="digitos-adv")
                yield Static(id="motor")
        with Horizontal(id="barra-entrada"):
            yield Label("Lance do adversário", id="rotulo-entrada")
            yield Input(placeholder="ex.: e7e5", id="entrada", max_length=12)
            yield Static(id="previa")
        yield Footer()

    def on_mount(self) -> None:
        s = self.sessao
        self.query_one("#agora").border_title = "Agora"
        self.query_one("#relogio-nosso").border_title = f"Nós, {s.nome_cor}"
        self.query_one("#relogio-adv").border_title = f"Adversário, {COR_PT[not s.our_color]}"
        self.query_one("#motor").border_title = "Motor"
        self.query_one("#sumula-rolagem").border_title = "Súmula"
        self.query_one("#sumula-rolagem").can_focus = False
        self._ajustar_tabuleiro()
        self.set_interval(0.5, self._tique)
        self.ultimo_nosso = s.lances[-1] if s.lances and s.lances[-1].nosso else None
        if s.encerrada:
            self.definir_fase("fim")
        elif self.retomada:
            self._entrar_turno_atual()
            self.app.push_screen(
                DialogoRelogio(s, aviso="Partida retomada. Confira os relógios físicos e corrija se for preciso."),
                self._relogio_definido,
            )
        elif s.nossa_vez:
            self.iniciar_motor()
        else:
            self._entrar_turno_atual()

    def on_unmount(self) -> None:
        if self.parada is not None:
            self.descartar = True
            self.parada.set()
        self.sessao.parar()

    def on_resize(self, event: Resize) -> None:
        self._ajustar_tabuleiro()

    def _ajustar_tabuleiro(self) -> None:
        largura, altura = self.size.width, self.size.height
        corpo = max(9, altura - 6)  # topo, barra de entrada e rodapé
        tabuleiro = self.query_one(Tabuleiro)
        for alt in (3, 2, 1):
            larg = 2 * alt + 1
            # sobra espaço para a súmula embaixo (6 linhas) e para a coluna da direita (64)
            if 8 * alt + 2 + 6 <= corpo and 8 * larg + 3 + 64 <= largura:
                break
        tabuleiro.styles.width = 8 * larg + 3
        tabuleiro.styles.height = 8 * alt + 1
        self.query_one("#esquerda").styles.width = 8 * larg + 3 + 3
        if (largura < 110 or altura < 32) and not self.avisou_janela:
            self.avisou_janela = True
            self.app.notify("Janela pequena: maximize o terminal (o ideal é pelo menos 120 x 36).",
                            severity="warning", timeout=8)

    # ------------------------------------------------------------------
    # Fases
    # ------------------------------------------------------------------
    def check_action(self, action: str, parameters) -> bool | None:
        if action == "jogar_agora":
            return True if self.fase == "pensando" else None
        if action == "pensar":
            return True if self.fase == "pausado" else None
        if action == "manual":
            return True if self.fase in ("pensando", "pausado") else None
        return True

    def definir_fase(self, fase: str) -> None:
        self.fase = fase
        entrada = self.query_one("#entrada", Input)
        rotulo = self.query_one("#rotulo-entrada", Label)
        digitavel = fase in ("adversario", "manual")
        entrada.disabled = not digitavel
        entrada.set_class(fase == "manual", "manual")
        if fase == "manual":
            rotulo.update("Nosso lance (manual)")
            entrada.placeholder = "ex.: g1f3"
        else:
            rotulo.update("Lance do adversário")
            entrada.placeholder = "ex.: e7e5" if digitavel else ""
        if not digitavel:
            self.leitura = None
            self.query_one(Tabuleiro).marcar(None, [])
        elif self.app.screen is self:
            entrada.focus()
        self.refresh_bindings()
        self.atualizar_tudo()

    def _entrar_turno_atual(self) -> None:
        """Depois de desfazer, corrigir a posição ou retomar: decide a fase sem o motor pensar sozinho."""
        if self.sessao.encerrada:
            self.definir_fase("fim")
        elif self.sessao.nossa_vez:
            self.definir_fase("pausado")
        else:
            self.sessao.retomar_turno()
            self.limpar_entrada()
            self.definir_fase("adversario")

    def limpar_entrada(self) -> None:
        self.query_one("#entrada", Input).value = ""
        self.leitura = None
        self.query_one(Tabuleiro).marcar(None, [])

    # ------------------------------------------------------------------
    # Motor
    # ------------------------------------------------------------------
    def iniciar_motor(self) -> None:
        if not self.sessao.nossa_vez:
            return
        self.parada = threading.Event()
        self.descartar = self.segurar = False
        self.retido = None
        self.apos_parar = None
        self.jogar_quando_puder = False
        self.tem_info = False
        self.info = None
        self.pensando_desde = time.perf_counter()
        self.limpar_entrada()
        self.definir_fase("pensando")
        self._buscar(self.sessao.board.copy(), self.parada)

    @work(thread=True, group="motor")
    def _buscar(self, board: chess.Board, parada: threading.Event) -> None:
        def info(profundidade, seletiva, nota, nos, tempo, pv):
            self.post_message(InfoMotor(parada, profundidade, seletiva, nota, nos, tempo, [m.uci() for m in pv[:12]]))

        try:
            resultado = self.sessao.pensar(board, parada, info)
        except Exception as exc:  # noqa: BLE001 - a falha vira aviso na tela
            self.post_message(MotorFalhou(parada, exc))
            return
        self.post_message(MotorTerminou(parada, resultado))

    def on_info_motor(self, msg: InfoMotor) -> None:
        if msg.parada is not self.parada:
            return
        self.tem_info = True
        self.info = msg
        if self.jogar_quando_puder:
            msg.parada.set()
        self.atualizar_motor()

    def on_motor_terminou(self, msg: MotorTerminou) -> None:
        if msg.parada is not self.parada:
            return
        if self.segurar:
            self.retido = msg.resultado
            return
        self._concluir(msg.resultado)

    def on_motor_falhou(self, msg: MotorFalhou) -> None:
        if msg.parada is not self.parada:
            return
        if self.descartar:          # a busca já ia ser descartada: só segue com a ação pedida
            self._concluir(None)
            return
        resultado = self.sessao.lance_emergencia(msg.erro)
        self.app.notify(
            f"A busca falhou ({msg.erro}). Foi jogado um lance de emergência, escolhido em 1 lance de "
            "profundidade. F2 desfaz se a equipe preferir outro.",
            severity="error", timeout=12,
        )
        if self.segurar:
            self.retido = resultado
            return
        self._concluir(resultado)

    def _concluir(self, resultado) -> None:
        self.parada = None
        if self.descartar:
            self.descartar = False
            self.definir_fase("pausado")
            acao, self.apos_parar = self.apos_parar, None
            if acao is not None:
                acao()
            return
        try:
            lance = self.sessao.registrar_nosso(resultado)
        except ValueError as exc:
            self.app.notify(str(exc), severity="error")
            self.definir_fase("pausado")
            return
        self.ultimo_nosso = lance
        self.ultimo_resultado = resultado
        aviso = self.sessao.tomar_aviso()
        if aviso:
            self.app.notify(aviso, severity="error", timeout=15)
        if self.som:
            self.app.bell()
        if self.sessao.encerrada:
            self.definir_fase("fim")
            self.mostrar_fim()
        else:
            self.limpar_entrada()
            self.definir_fase("adversario")

    def _segurar_motor(self) -> None:
        """Um diálogo vai abrir: se o motor terminar enquanto isso, o lance espera a decisão."""
        if self.fase == "pensando":
            self.segurar = True

    def _soltar_motor(self) -> None:
        """O diálogo foi cancelado: o motor segue e, se já terminou, o lance é aplicado."""
        self.segurar = False
        if self.retido is not None:
            retido, self.retido = self.retido, None
            self._concluir(retido)

    def _parar_e_depois(self, acao) -> None:
        """Para o motor, descartando a busca, e executa a ação quando ele terminar de parar."""
        if self.fase != "pensando":
            self.segurar = False
            acao()
            return
        self.segurar = False
        self.descartar = True
        self.apos_parar = acao
        if self.retido is not None:
            retido, self.retido = self.retido, None
            self._concluir(retido)
        elif self.parada is not None:
            self.parada.set()

    # ------------------------------------------------------------------
    # Lances do operador
    # ------------------------------------------------------------------
    @on(Input.Changed, "#entrada")
    def _digitando(self, event: Input.Changed) -> None:
        if self.fase not in ("adversario", "manual"):
            return
        self.leitura = ler_lance(self.sessao.board, event.value)
        self.query_one(Tabuleiro).marcar(self.leitura.origem, self.leitura.destinos)
        self.atualizar_previa()

    @on(Input.Submitted, "#entrada")
    def _enviar(self, event: Input.Submitted) -> None:
        if self.fase not in ("adversario", "manual"):
            return
        leitura = ler_lance(self.sessao.board, event.value)
        if leitura.estado == "ok":
            self.registrar(leitura.lance)
        elif leitura.estado == "promocao":
            pergunta = "Qual peça o adversário escolheu?" if self.fase == "adversario" else "Para qual peça nós promovemos?"
            base = leitura.lance

            def escolhida(peca) -> None:
                if peca:
                    self.registrar(chess.Move(base.from_square, base.to_square, peca))

            self.app.push_screen(DialogoPromocao(pergunta), escolhida)
        elif leitura.estado != "vazio":
            if self.som:
                self.app.bell()
            self.app.notify(leitura.mensagem, title="Lance não registrado", severity="error", timeout=6)

    def registrar(self, move: chess.Move) -> None:
        if self.fase == "adversario":
            lance = self.sessao.registrar_adversario(move)
            self.app.notify(f"{lance.uci}: {lance.descricao}.", title="Lance do adversário registrado", timeout=3)
            if self.sessao.encerrada:
                self.limpar_entrada()
                self.definir_fase("fim")
                self.mostrar_fim()
            else:
                self.iniciar_motor()
        elif self.fase == "manual":
            lance = self.sessao.registrar_manual(move)
            self.ultimo_nosso = lance
            self.limpar_entrada()
            if self.sessao.encerrada:
                self.definir_fase("fim")
                self.mostrar_fim()
            else:
                self.definir_fase("adversario")

    def on_tabuleiro_casa_clicada(self, msg: Tabuleiro.CasaClicada) -> None:
        if self.fase not in ("adversario", "manual"):
            return
        board = self.sessao.board
        entrada = self.query_one("#entrada", Input)
        peca = board.piece_at(msg.casa)
        nome = chess.square_name(msg.casa)
        leitura = self.leitura
        if peca is not None and peca.color == board.turn:
            entrada.value = nome
        elif leitura is not None and leitura.origem is not None and msg.casa in leitura.destinos:
            entrada.value = chess.square_name(leitura.origem) + nome
        else:
            entrada.value = ""
        entrada.focus()
        entrada.cursor_position = len(entrada.value)

    # ------------------------------------------------------------------
    # Ações (teclas)
    # ------------------------------------------------------------------
    def action_jogar_agora(self) -> None:
        if self.fase != "pensando" or self.parada is None:
            return
        if self.tem_info:
            self.parada.set()
        else:
            self.jogar_quando_puder = True
        self.app.notify("Jogando com o melhor lance encontrado até agora.", timeout=2)

    def action_pensar(self) -> None:
        if self.fase == "pausado":
            self.iniciar_motor()

    def action_limpar(self) -> None:
        if self.fase == "manual":
            self.definir_fase("pausado")
        else:
            self.limpar_entrada()
            self.atualizar_previa()

    def action_ajuda(self) -> None:
        self.app.push_screen(TelaAjuda())

    def action_desfazer(self) -> None:
        if not self.sessao.board.move_stack:
            self.app.notify("Não há lance para desfazer.", severity="warning")
            return
        self._segurar_motor()
        self.app.push_screen(DialogoDesfazer(self.sessao), self._desfazer_escolhido)

    def _desfazer_escolhido(self, n: int | None) -> None:
        if not n:
            self._soltar_motor()
            return
        self._parar_e_depois(lambda: self._aplicar_desfazer(n))

    def _aplicar_desfazer(self, n: int) -> None:
        desfeitos = self.sessao.desfazer(n)
        s = self.sessao
        self.ultimo_nosso = s.lances[-1] if s.lances and s.lances[-1].nosso else None
        self.app.notify(f"Desfeito: {', '.join(desfeitos)}. Se o tempo mudou, confira com F3.", title="Desfazer")
        self._entrar_turno_atual()

    def action_relogio(self) -> None:
        self.app.push_screen(DialogoRelogio(self.sessao), self._relogio_definido)

    def _relogio_definido(self, valores) -> None:
        if valores:
            nosso, adv = valores
            recalibrado = self.sessao.sincronizar(nosso, adv)
            if recalibrado:
                self.app.notify(recalibrado, title="Relógios sincronizados", timeout=10)
            else:
                self.app.notify("Relógios sincronizados.", timeout=2)
        self.atualizar_tudo()

    def action_ajustes(self) -> None:
        tabuleiro = self.query_one(Tabuleiro)
        self.app.push_screen(DialogoAjustes(self.sessao, self.som, tabuleiro.simbolos), self._ajustes_salvos)

    def _ajustes_salvos(self, valores: dict | None) -> None:
        if not valores:
            return
        self.som = valores.pop("som")
        self.query_one(Tabuleiro).simbolos = valores.pop("simbolos")
        self.sessao.ajustar(**valores)
        self.app.notify("Ajustes salvos.", timeout=2)
        self.atualizar_tudo()

    def action_posicao(self) -> None:
        self._segurar_motor()
        self.app.push_screen(DialogoPosicao(self.sessao), self._posicao_escolhida)

    def _posicao_escolhida(self, board: chess.Board | None) -> None:
        if board is None:
            self._soltar_motor()
            return
        self._parar_e_depois(lambda: self._aplicar_posicao(board))

    def _aplicar_posicao(self, board: chess.Board) -> None:
        self.sessao.definir_posicao(board)
        self.ultimo_nosso = None
        self.app.notify("Posição atualizada. O histórico de repetição recomeça daqui.", title="Posição")
        self._entrar_turno_atual()

    def action_economia(self) -> None:
        if self.sessao.alternar_economia():
            self.app.notify("Modo economia ligado: o motor usa metade do tempo por lance.", title="Economia")
        else:
            self.app.notify("Modo economia desligado: tempo normal.", title="Economia")
        self.atualizar_tudo()

    def action_avaliar(self) -> None:
        nota = self.sessao.avaliacao_estatica()
        self.app.notify(f"Avaliação estática, sem busca: {fmt_avaliacao(nota)}.", title="Avaliar", timeout=6)

    def action_ponder(self) -> None:
        ligado = not self.sessao.use_ponder
        self.sessao.definir_ponder(ligado)
        self.app.notify(f"Pondering {'ligado' if ligado else 'desligado'}.", timeout=2)
        self.atualizar_tudo()

    def action_manual(self) -> None:
        if self.fase == "pausado":
            self.limpar_entrada()
            self.definir_fase("manual")
        elif self.fase == "pensando":
            self._segurar_motor()
            self.app.push_screen(
                DialogoConfirmar(
                    "Lance manual",
                    "Parar o motor e registrar um lance escolhido pela equipe? Use quando o lance jogado "
                    "no tabuleiro foi outro, por exemplo pela regra da peça tocada.",
                    "Parar e digitar o lance",
                    "Continuar pensando",
                ),
                self._manual_confirmado,
            )

    def _manual_confirmado(self, confirmado: bool | None) -> None:
        if not confirmado:
            self._soltar_motor()
            return
        self._parar_e_depois(lambda: (self.limpar_entrada(), self.definir_fase("manual")))

    def action_encerrar(self) -> None:
        self._segurar_motor()
        self.app.push_screen(DialogoEncerrar(), self._encerrar_escolhido)

    def _encerrar_escolhido(self, escolha: str | None) -> None:
        if escolha is None:
            self._soltar_motor()
        elif escolha == "sair":
            self._parar_e_depois(self._sair)
        else:
            self._parar_e_depois(lambda: self._declarar(escolha))

    def _sair(self) -> None:
        self.sessao.parar()
        self.sessao.salvar()
        self.app.exit()

    def _declarar(self, escolha: str) -> None:
        self.sessao.declarar_resultado(escolha)
        self.definir_fase("fim")
        self.mostrar_fim()

    def mostrar_fim(self) -> None:
        self.app.push_screen(DialogoFim(self.sessao), self._fim_escolhido)

    def _fim_escolhido(self, escolha: str | None) -> None:
        if escolha == "desfazer":
            self._aplicar_desfazer(1)
        elif escolha == "nova":
            self.app.switch_screen(TelaInicio())
        elif escolha == "sair":
            self.app.exit()

    # ------------------------------------------------------------------
    # Desenho
    # ------------------------------------------------------------------
    def _tique(self) -> None:
        self.atualizar_relogios()
        if self.fase == "pensando":
            self.atualizar_agora()

    def atualizar_tudo(self) -> None:
        self.atualizar_topo()
        self.atualizar_agora()
        self.atualizar_relogios()
        self.atualizar_motor()
        self.atualizar_sumula()
        self.atualizar_previa()
        self.query_one(Tabuleiro).refresh()

    def atualizar_topo(self) -> None:
        s = self.sessao
        esquerda = Text.assemble((" Chess Challenge III ", f"bold {LATAO}"), ("ICEV 2026", GRAFITE))
        self.query_one("#topo-esq", Static).update(esquerda)
        direita = Text()
        if s.economia:
            direita.append("Economia ligada    ", style=f"bold {LATAO}")
        if not s.use_ponder:
            direita.append("Ponder desligado    ", style=GRAFITE)
        if s.engine.contempt != (0 if s.usa_stockfish else 15):
            direita.append(f"Contempt {s.engine.contempt:+d}    ", style=GRAFITE)
        direita.append(f"{s.nome_motor}    ", style=LATAO if s.usa_stockfish else GRAFITE)
        direita.append(f"Jogamos de {s.nome_cor}    ", style=MARFIM)
        direita.append(f"Ritmo {s.config.descricao_ritmo()}    ", style=MARFIM)
        direita.append(f"Lance {s.board.fullmove_number}", style=f"bold {MARFIM}")
        self.query_one("#topo-dir", Static).update(direita)

    def atualizar_agora(self) -> None:
        painel = self.query_one("#agora", Static)
        s = self.sessao
        t = Text()
        if self.fase == "pensando":
            painel.border_title = "Agora: o motor está pensando"
            decorrido = time.perf_counter() - self.pensando_desde
            alvo = getattr(s.time_manager, "soft_limit", 0.0) or 0.0
            maximo = getattr(s.time_manager, "hard_limit", 0.0) or 0.0
            t.append("Aguarde o lance do motor.\n\n", style=f"bold {MARFIM}")
            if alvo > 0:
                t.append_text(barra_progresso(decorrido / alvo))
                t.append(f"\n{fmt_tempo(decorrido)} de cerca de {fmt_tempo(alvo)}, no máximo {fmt_tempo(maximo)}.\n\n",
                         style=GRAFITE)
            t.append("Espaço:", style=f"bold {LATAO}")
            t.append(" joga agora com o melhor lance encontrado até aqui.", style=GRAFITE)
        elif self.fase == "adversario":
            if self.ultimo_nosso is not None:
                lance = self.ultimo_nosso
                painel.border_title = "Agora: anuncie o nosso lance"
                t.append_text(texto_lance(lance.uci, f"bold {LATAO}"))
                t.append("\n\n")
                passos = [
                    f"Anuncie {lance.uci} em voz alta e espere o adversário repetir.",
                    f"No tabuleiro: {lance.descricao}.",
                    "Aperte o relógio.",
                    "Digite abaixo o lance que o adversário anunciar.",
                ]
                for i, passo in enumerate(passos, 1):
                    t.append(f" {i} ", style=f"bold {MADEIRA} on {LATAO}")
                    t.append(f" {passo}\n", style=MARFIM)
            else:
                painel.border_title = "Agora: vez do adversário"
                t.append("Aguardando o lance do adversário.\n\n", style=f"bold {MARFIM}")
                t.append("Digite abaixo o lance anunciado e aperte Enter. "
                         "Também dá para clicar na peça e depois no destino.", style=GRAFITE)
        elif self.fase == "pausado":
            painel.border_title = "Agora: nossa vez, motor pausado"
            t.append("O motor está esperando.\n\n", style=f"bold {MARFIM}")
            t.append("Enter:", style=f"bold {LATAO}")
            t.append(" o motor pensa agora.\n", style=MARFIM)
            t.append("F9:", style=f"bold {LATAO}")
            t.append(" registra um lance que a equipe já jogou no tabuleiro.", style=MARFIM)
        elif self.fase == "manual":
            painel.border_title = "Agora: lance manual"
            t.append("Digite o lance que a nossa equipe jogou no tabuleiro.\n\n", style=f"bold {MARFIM}")
            t.append("Ele vai para o log como MANUAL. Esc volta sem registrar.", style=GRAFITE)
        else:
            painel.border_title = "Agora: partida encerrada"
            placar, motivo = s.resultado or ("*", "")
            t.append(frase_resultado(placar, motivo, s.our_color) + "\n\n", style=f"bold {LATAO}")
            t.append("Entregue o log à banca. Ctrl+Q sai; F2 desfaz o último lance se houve engano.", style=GRAFITE)
        for aviso in s.alertas():
            t.append(f"\n{aviso}", style=f"bold {BANDEIRA}")
        painel.update(t)

    def atualizar_relogios(self) -> None:
        s = self.sessao
        nosso, adv = s.relogios()
        for seletor, valor in (("#digitos-nosso", nosso), ("#digitos-adv", adv)):
            digitos = self.query_one(seletor, Digits)
            texto = fmt_relogio(valor)
            if digitos.value != texto:
                digitos.update(texto)
        vez_nossa = s.board.turn == s.our_color
        self.query_one("#relogio-nosso").set_class(vez_nossa and not s.encerrada, "ativo")
        self.query_one("#relogio-adv").set_class(not vez_nossa and not s.encerrada, "ativo")
        self.query_one("#relogio-nosso").set_class(nosso < 60, "pouco")

    def atualizar_motor(self) -> None:
        painel = self.query_one("#motor", Static)
        s = self.sessao
        t = Text()
        if self.fase == "pensando" and self.info is not None:
            i = self.info
            self._linhas_motor(t, i.profundidade, i.seletiva, i.nota, i.nos, i.tempo, i.pv, None)
        elif self.fase == "pensando":
            t.append("Procurando o lance...", style=GRAFITE)
        elif self.ultimo_resultado is not None:
            r = self.ultimo_resultado
            self._linhas_motor(t, r.depth, r.seldepth, r.score_cp, r.nodes, r.elapsed_sec, r.pv, r.source)
            # Gestão de tempo da equipe: por que este lance saiu mais rápido
            if s.ultimo_credito > 0:
                t.append(f"\nPonder acertou: {fmt_tempo(s.ultimo_credito)} pensados no tempo do adversário "
                         "contaram como já gastos.", style=GRAFITE)
            if s.time_manager.economia_empate_morto:
                t.append("\nEmpate morto (nota 0,00 estável): este lance usou 30% do tempo.", style=GRAFITE)
        else:
            t.append("O motor ainda não jogou nesta partida.", style=GRAFITE)
        if s.use_ponder and self.fase == "adversario" and not s.encerrada:
            previsto = f", esperando {s.lance_previsto}" if s.lance_previsto else ""
            t.append(f"\nPonder: pensando no tempo do adversário{previsto}.", style=GRAFITE)
        painel.update(t)

    @staticmethod
    def _linhas_motor(t: Text, prof, seletiva, nota, nos, tempo, pv, origem) -> None:
        if origem == "BOOK":
            t.append("Lance do livro de aberturas, sem busca.\n", style=f"bold {MARFIM}")
        elif origem == "SYZYGY_TB":
            t.append("Lance de tablebase: resultado teórico exato.\n", style=f"bold {MARFIM}")
        elif origem == "EMERGENCY":
            t.append("Lance de emergência: a busca falhou e o lance saiu da avaliação em 1 lance.\n",
                     style=f"bold {BANDEIRA}")
        elif origem == "REGRA_EMPATE":
            t.append("Regra do empate imediato (Armageddon ou contempt): lance escolhido pela regra da equipe.\n",
                     style=f"bold {LATAO}")
        else:
            t.append(f"Profundidade {prof}", style=f"bold {MARFIM}")
            t.append(f" (seletiva {seletiva})    ", style=GRAFITE)
            nps = int(nos / max(0.001, tempo))
            t.append(f"{fmt_numero(nos)} nós, {fmt_numero(nps)} por segundo, {fmt_tempo(tempo)}\n", style=GRAFITE)
        t.append("Avaliação ", style=GRAFITE)
        t.append(fmt_avaliacao(nota), style=f"bold {MARFIM}")
        t.append("\nadversário ", style=GRAFITE)
        t.append_text(barra_avaliacao(nota))
        t.append(" nós\n", style=GRAFITE)
        t.append("Linha principal  ", style=GRAFITE)
        t.append(" ".join(pv[:10]), style=MARFIM)

    def atualizar_sumula(self) -> None:
        s = self.sessao
        t = Text()
        if not s.lances:
            t.append("Nenhum lance ainda.", style=GRAFITE)
        else:
            inicio = chess.Board(s.fen_inicial)
            numero, vez = inicio.fullmove_number, inicio.turn
            linha_aberta = False
            for lance in s.lances:
                if vez == chess.WHITE:
                    if linha_aberta:
                        t.append("\n")
                    t.append(f"{numero:>3}. ", style=GRAFITE)
                    linha_aberta = True
                elif not linha_aberta:
                    t.append(f"{numero:>3}. ", style=GRAFITE)
                    t.append(f"{'...':<13}", style=GRAFITE)
                    linha_aberta = True
                t.append(f"{lance.uci:<6}", style=f"bold {LATAO}" if lance.nosso else MARFIM)
                marca = {"BOOK": "livro", "SYZYGY_TB": "tb", "MANUAL": "manual",
                         "EMERGENCY": "emerg.", "REGRA_EMPATE": "empate"}.get(lance.origem, "")
                t.append(f"{marca:<7}", style=GRAFITE)
                if vez == chess.BLACK:
                    numero += 1
                vez = not vez
        self.query_one("#sumula", Static).update(t)
        self.query_one("#sumula-rolagem", VerticalScroll).scroll_end(animate=False)

    def atualizar_previa(self) -> None:
        previa = self.query_one("#previa", Static)
        if self.fase not in ("adversario", "manual"):
            previa.update(Text(MENSAGEM_FASE.get(self.fase, ""), style=GRAFITE))
            return
        leitura = self.leitura or ler_lance(self.sessao.board, "")
        if leitura.estado == "ok":
            previa.update(Text.assemble(("✓ ", f"bold {FELTRO}"), (leitura.mensagem, FELTRO),
                                        (". Enter registra.", GRAFITE)))
        elif leitura.estado == "promocao":
            previa.update(Text.assemble(("▲ ", f"bold {LATAO}"), (leitura.mensagem, LATAO)))
        elif leitura.estado == "erro":
            previa.update(Text.assemble(("✗ ", f"bold {BANDEIRA}"), (leitura.mensagem, BANDEIRA)))
        else:
            previa.update(Text(leitura.mensagem, style=GRAFITE))


# ==============================================================================
# Aplicativo
# ==============================================================================
class TorneioApp(App):
    CSS_PATH = "tui.tcss"
    TITLE = "Chess Challenge III"
    ENABLE_COMMAND_PALETTE = False
    # O Textual fecha o app no Ctrl+Q sem perguntar; aqui ele abre o diálogo de encerrar
    BINDINGS = [Binding("ctrl+q", "encerrar", "Encerrar", show=False, priority=True)]

    def action_encerrar(self) -> None:
        tela = self.screen
        if isinstance(tela, TelaPartida):
            tela.action_encerrar()
        elif isinstance(tela, TelaInicio):
            self.exit()

    def on_mount(self) -> None:
        self.register_theme(TEMA)
        self.theme = TEMA.name
        self.push_screen(TelaInicio())


if __name__ == "__main__":
    verificar_stockfish()  # sem Stockfish, oferece instalar antes de desenhar a tela
    TorneioApp().run()
