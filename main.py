"""
Interface Principal de Torneio (CLI Blindada + Pondering + Logs + Tabuleiro no navegador)
Chess Challenge III - ICEV 2026

Segue rigorosamente o protocolo de 4 passos do FAQ Item 10:
1. Exibe em destaque o lance em notação UCI (ex: e2e4, g1f3, e7e8q) para anúncio em voz alta.
2. Exibe explicação clara da casa de origem e destino para movimentação no tabuleiro físico.
3. Registra automaticamente no arquivo .log e .txt todos os dados exigidos pelo FAQ Item 9.
4. Possui Pondering em background (FAQ Item 13) e comandos de emergência (undo, tempo, fen)
   para garantir que o software jamais trave ou perca sincronia com o tabuleiro físico.

Visualização: a cada lance o tabuleiro é gravado em tabuleiro.svg e a página tabuleiro.html
(aberta no navegador) se atualiza sozinha. Mude SO_NAVEGADOR para False para ver também
o tabuleiro de texto no terminal.
"""

import os
import sys
import time
import threading
import webbrowser

import chess
import chess.svg

from evaluation import evaluate_board
from time_manager import TimeManager, ler_relogio
from search import SearchEngine, SearchResult
from logger import MatchLogger
from recuperacao import detalhes_da_partida, evento_configuracao
from motor_stockfish import (
    MotorIndisponivel,
    avisos_de_energia,
    criar_motor,
    descrever_motor,
    verificar_stockfish,
)

# ==============================================================================
# TABULEIRO NO NAVEGADOR (SVG que se atualiza sozinho)
# ==============================================================================
_DIR = os.path.dirname(os.path.abspath(__file__))
SVG_PATH = os.path.join(_DIR, "tabuleiro.svg")
HTML_PATH = os.path.join(_DIR, "tabuleiro.html")
SO_NAVEGADOR = True        # True = não desenha no terminal, só no navegador
ABRIR_NAVEGADOR = True     # abre a página automaticamente ao iniciar a partida

_HTML = """<!doctype html><meta charset="utf-8"><title>Tabuleiro</title>
<style>body{margin:0;background:#262421;display:flex;justify-content:center;
align-items:center;height:100vh}img{height:96vmin;width:96vmin;
box-shadow:0 8px 30px #000a;border-radius:4px}</style>
<img id="b" src="tabuleiro.svg">
<script>setInterval(()=>{document.getElementById('b').src='tabuleiro.svg?'+Date.now()},400)</script>"""


def salvar_tabuleiro_svg(board: chess.Board, perspective: bool = chess.WHITE,
                         last_move: chess.Move | None = None) -> None:
    """Grava o tabuleiro atual em tabuleiro.svg (troca atômica) e cria tabuleiro.html se faltar."""
    if not os.path.isfile(HTML_PATH):
        with open(HTML_PATH, "w", encoding="utf-8") as f:
            f.write(_HTML)
    svg = chess.svg.board(
        board,
        lastmove=last_move,
        flipped=(perspective == chess.BLACK),
        check=board.king(board.turn) if board.is_check() else None,
        size=800,
        colors={
            "square light": "#eeeed2", "square dark": "#769656",
            "square light lastmove": "#f6f669", "square dark lastmove": "#baca2b",
            "margin": "#262421", "coord": "#ffffff",
        },
    )
    tmp = SVG_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(svg)
    os.replace(tmp, SVG_PATH)  # o navegador nunca lê o arquivo pela metade


# Armageddon (FAQ Item 12): o empate classifica as Pretas, então o contempt muda de sinal pela cor
ARMAGEDDON_CONTEMPT = 100

PIECE_NAMES_PT = {
    chess.PAWN: "Peão",
    chess.KNIGHT: "Cavalo",
    chess.BISHOP: "Bispo",
    chess.ROOK: "Torre",
    chess.QUEEN: "Dama",
    chess.KING: "Rei",
}

PROMO_NAMES_PT = {
    chess.QUEEN: "DAMA (q)",
    chess.ROOK: "TORRE (r)",
    chess.BISHOP: "BISPO (b)",
    chess.KNIGHT: "CAVALO (n)",
}


def describe_move_pt(board: chess.Board, move: chess.Move) -> str:
    """Gera descrição em português claro para auxiliar a movimentação rápida no tabuleiro físico."""
    piece = board.piece_at(move.from_square)
    piece_name = PIECE_NAMES_PT.get(piece.piece_type, "Peça") if piece else "Peça"
    from_name = chess.square_name(move.from_square)
    to_name = chess.square_name(move.to_square)

    if board.is_castling(move):
        if chess.square_file(move.to_square) == 6:
            return f"ROQUE MENOR (Rei de {from_name} para {to_name})"
        return f"ROQUE MAIOR (Rei de {from_name} para {to_name})"

    desc = f"{piece_name} de {from_name} para {to_name}"
    if board.is_capture(move):
        if board.is_en_passant(move):
            desc += " (Captura EN PASSANT)"
        else:
            victim = board.piece_at(move.to_square)
            v_name = PIECE_NAMES_PT.get(victim.piece_type, "peça") if victim else "peça"
            desc += f" (CAPTURA {v_name})"

    if move.promotion is not None:
        promo_str = PROMO_NAMES_PT.get(move.promotion, "DAMA")
        desc += f" -> PROMOVE PARA {promo_str}"

    return desc


def print_board_visual(
    board: chess.Board,
    perspective: bool = chess.WHITE,
    last_move: chess.Move | None = None,
) -> None:
    """Atualiza o tabuleiro no navegador e, se SO_NAVEGADOR=False, também desenha no terminal."""
    try:
        salvar_tabuleiro_svg(board, perspective, last_move)
    except Exception as exc:  # a visualização nunca pode derrubar a partida
        print(f"  [AVISO] Não foi possível atualizar o tabuleiro SVG: {exc}")
    if SO_NAVEGADOR:
        return

    # (topo, base) de cada tipo de casa em cores 256
    LIGHT = (223, 180)
    DARK = (108, 65)
    HL_LIGHT = (229, 186)
    HL_DARK = (143, 101)
    ranks = range(7, -1, -1) if perspective == chess.WHITE else range(8)
    files = range(8) if perspective == chess.WHITE else range(7, -1, -1)

    print()
    for r in ranks:
        top_row = f" {r + 1} "
        bot_row = "   "
        for f in files:
            sq = chess.square(f, r)
            light = (f + r) % 2 == 1
            top_bg, bot_bg = LIGHT if light else DARK
            if last_move and sq in (last_move.from_square, last_move.to_square):
                top_bg, bot_bg = HL_LIGHT if light else HL_DARK
            piece = board.piece_at(sq)
            if piece:
                letter = piece.symbol().upper()
                if piece.color == chess.WHITE:
                    chip = f"\033[48;5;255m\033[38;5;16m\033[1m {letter} \033[0m"
                else:
                    chip = f"\033[48;5;16m\033[38;5;255m\033[1m {letter} \033[0m"
                top_cell = f"\033[48;5;{top_bg}m \033[0m{chip}\033[48;5;{top_bg}m \033[0m"
            else:
                top_cell = f"\033[48;5;{top_bg}m     \033[0m"
            top_row += top_cell
            bot_row += f"\033[48;5;{bot_bg}m     \033[0m"
        print(top_row)
        print(bot_row)
    letters = "abcdefgh" if perspective == chess.WHITE else "hgfedcba"
    print("   " + "".join(f"  {c}  " for c in letters) + "\n")


def parse_clock_input(val_str: str) -> float:
    """
    Converte 'MM:SS' (ou 'MM.SS') ou segundos para segundos. Recusa (ValueError) segundos acima de 59,
    negativo, infinito e letras: um relógio digitado errado faria o motor planejar com tempo que não tem.
    """
    return ler_relogio(val_str)


def partida_acabou(board: chess.Board) -> bool:
    """
    Fim de jogo REAL: xeque-mate, afogamento, material insuficiente, tripla repetição já
    ocorrida ou 100 meios-lances sem captura/peão. (Não usa claim_draw=True, que também
    acusaria empates que só aconteceriam se um lance futuro fosse jogado.)
    """
    return board.is_game_over() or board.is_repetition(3) or board.halfmove_clock >= 100


def resultado_partida(board: chess.Board) -> tuple[str, str]:
    """Retorna (resultado, motivo) de uma partida já encerrada segundo partida_acabou()."""
    if board.is_game_over():
        outcome = board.outcome()
        reason = outcome.termination.name if outcome else "FIM DE JOGO"
        return board.result(), reason
    if board.is_repetition(3):
        return "1/2-1/2", "THREEFOLD_REPETITION"
    return "1/2-1/2", "FIFTY_MOVES"


def lance_de_emergencia(board: chess.Board, time_manager: TimeManager) -> SearchResult:
    """
    Se a busca falhar por qualquer motivo, joga o melhor lance em 1 ply pela avaliação
    estática (ou o primeiro lance legal, se até isso falhar). Nunca deixa o relógio correndo sem lance.
    """
    legal = list(board.legal_moves)
    best_move = legal[0]
    best_score = 0
    try:
        best_val = None
        for m in legal:
            board.push(m)
            try:
                val = -evaluate_board(board, check_terminal=True)
            finally:
                board.pop()
            if best_val is None or val > best_val:
                best_val, best_move = val, m
        best_score = best_val if best_val is not None else 0
    except Exception:
        best_move = legal[0]
        best_score = 0
    elapsed = time_manager.elapsed()
    return SearchResult(best_move, best_score, 1, 1, elapsed, [best_move.uci()], "EMERGENCY", 0, [best_move])


class PonderController:
    """
    Pondering em background (FAQ Item 13) durante o turno do adversário.
    Enquanto o operador espera o anúncio do lance adversário, o motor pensa na posição
    após o lance que ele PREVÊ (2º lance da variante principal), enchendo a Tabela de
    Transposição. Se o adversário jogar o lance previsto, a busca seguinte começa com
    a árvore quase pronta e chega muito mais fundo no mesmo tempo.

    Segurança: a busca de ponder e a busca principal NUNCA rodam ao mesmo tempo
    (stop_ponder() espera a thread terminar; além disso o motor tem um lock interno).

    Crédito do ponder (gestão de tempo, time_manager.py ideia 6): o controlador guarda a posição
    ponderada, quanto tempo o ponder pensou nela e até que profundidade chegou. Se o adversário jogar
    o lance previsto, credito_para() devolve esse tempo, que conta como já gasto no nosso lance.
    """

    PROFUNDIDADE_MINIMA_CREDITO = 6  # ponder raso (adversário jogou na hora) quase não dá crédito

    def __init__(self, engine: SearchEngine):
        self.engine = engine
        self.stop_event = threading.Event()
        self.thread: threading.Thread | None = None
        self.posicao_prevista: str | None = None  # FEN depois do lance previsto (None: sem previsão)
        self.inicio: float | None = None
        self.duracao = 0.0
        self.resultado: SearchResult | None = None

    def start_ponder(self, board: chess.Board, predicted_uci: str | None = None) -> None:
        self.stop_ponder()
        ponder_board = board.copy()
        previsto = False
        if predicted_uci:
            try:
                pmove = chess.Move.from_uci(predicted_uci)
                if pmove in ponder_board.legal_moves:
                    ponder_board.push(pmove)
                    previsto = True
            except ValueError:
                pass
        self.posicao_prevista, self.resultado, self.duracao, self.inicio = None, None, 0.0, None
        if ponder_board.is_game_over():
            return

        stop_event = threading.Event()
        self.stop_event = stop_event
        self.posicao_prevista = ponder_board.fen() if previsto else None
        self.inicio = time.perf_counter()

        def _ponder_worker():
            try:
                self.resultado = self.engine.search_best_move(
                    board=ponder_board,
                    time_manager=TimeManager(initial_minutes=60.0, increment_seconds=0.0),
                    max_depth=64,
                    use_book=False,
                    use_tb=True,
                    stop_event=stop_event,
                    verbose=False,
                    infinite=True,
                )
            except Exception:
                self.resultado = None

        self.thread = threading.Thread(target=_ponder_worker, daemon=True)
        self.thread.start()

    def stop_ponder(self) -> None:
        if self.thread is not None:
            self.stop_event.set()
            self.thread.join(timeout=10.0)  # a busca verifica o 'stop' a cada ~1000 nós (< 0,1 s)
            if self.inicio is not None:
                self.duracao = time.perf_counter() - self.inicio
                self.inicio = None
        self.thread = None

    def credito_para(self, board: chess.Board) -> float:
        """
        Segundos de ponder que valem para esta posição: o adversário jogou o lance previsto e o ponder
        pensou nela até uma profundidade razoável. 0 se errou, se não havia previsão ou se o ponder falhou.
        Usa o crédito uma vez só (a próxima consulta devolve 0).
        """
        self.stop_ponder()
        acertou = (
            self.posicao_prevista is not None
            and self.posicao_prevista == board.fen()
            and self.resultado is not None
            and self.resultado.depth >= self.PROFUNDIDADE_MINIMA_CREDITO
        )
        credito = self.duracao if acertou else 0.0
        self.posicao_prevista, self.resultado, self.duracao = None, None, 0.0
        return credito


def print_help_menu() -> None:
    print(
        "\n==================== COMANDOS ESPECIAIS DURANTE A PARTIDA ====================\n"
        "  <lance_uci>          : Registra lance adversário (ex: e2e4, g1f3, e7e8q)\n"
        "  undo / voltar        : Desfaz o último meio-lance (use 'undo 2' para voltar 2 lances)\n"
        "  tempo <nosso> [adv]  : Sincroniza relógio em MM:SS ou segundos (ex: tempo 12:30 11:45)\n"
        "  operador <segundos>  : Ajusta o tempo gasto pelo operador por lance (ex: operador 2)\n"
        "  fator <x>            : Multiplica o tempo de pensamento (ex: fator 0.7 economiza relógio)\n"
        "  contempt <cp>        : Aversão ao empate em cp (padrão 15; maior = evita empate; negativo = busca)\n"
        "  fen <string_fen>     : Atualiza posição do tabuleiro conforme tabuleiro físico\n"
        "  forcar <lance_uci>   : Força a execução de um lance específico\n"
        "  ponder               : Ativa/Desativa o cálculo no turno do adversário (Pondering)\n"
        "  avaliar              : Mostra a avaliação estática detalhada da posição atual\n"
        "  log                  : Mostra o caminho do arquivo .log/.txt oficial para a banca\n"
        "  sair / quit          : Encerra: resultado do árbitro (v, d, e) ou sair e retomar depois (r)\n"
        "==============================================================================\n"
    )


def _oferecer_retomada():
    """
    Se a última partida foi interrompida (o notebook travou, desligou ou a janela fechou), oferece retomar.
    É a mesma recuperação da TUI e do navegador (recuperacao.py): pelo arquivo salvo ou, se ele se perdeu,
    pelo log da banca. Devolve a GameSession retomada ou None para uma partida nova.
    """
    from session import GameSession, arquivar_partida_salva, ler_partida_salva  # tardio: session importa este arquivo

    dados = ler_partida_salva()
    if not dados:
        return None
    print("\n" + "!" * 78)
    for linha in detalhes_da_partida(dados):
        print(f"  {linha}")
    print("!" * 78)
    try:
        resposta = input("\nRetomar esta partida? [S/n]: ").strip().lower()
    except EOFError:
        return None
    if resposta in ("n", "nao", "não", "no"):
        arquivar_partida_salva()
        print("  Partida nova. A interrompida continua no log dela (python recuperacao.py --listar).")
        return None
    try:
        sessao = GameSession.retomar(dados)
    except Exception as exc:  # noqa: BLE001 - arquivo salvo ou log com problema: o jogo não pode parar
        print(f"\n  [ERRO] Não deu para retomar: {exc}\n  Começando uma partida nova (corrija a posição com 'fen').")
        return None
    # Daqui em diante o modo texto grava só o log da banca: um arquivo salvo parado nesta jogada faria a
    # próxima retomada avisar que ele ficou atrasado. Sem ele, a recuperação usa o log direto.
    arquivar_partida_salva()
    return sessao


def _perguntar_config(stockfish):
    """Perguntas da partida nova (cor, ritmo, pondering, operador e motor); devolve a Config da session.py."""
    from session import Config  # import tardio: session.py importa este arquivo

    # 1. Seleção da Cor da Equipe
    while True:
        choice = input("\nQual a cor da nossa equipe nesta partida? [1] BRANCAS (padrão) | [2] PRETAS: ").strip().lower()
        if choice in ("", "1", "b", "brancas", "w", "white"):
            our_color = chess.WHITE
            break
        elif choice in ("2", "p", "pretas", "black"):
            our_color = chess.BLACK
            break
        print("Opção inválida. Digite 1 para Brancas ou 2 para Pretas.")

    # 2. Seleção do Controle de Tempo
    print("\nSelecione o Controle de Tempo:")
    print("  [1] 15 min + 10s incremento (Padrão Oficial FAQ Item 7 - Recomendado)")
    print("  [2] 25 min + 0s incremento  (Padrão Edital)")
    print("  [3] Armageddon (5 min Brancas / 4 min Pretas - FAQ Item 12)")
    print("  [4] Personalizado")
    tc_choice = input("Escolha [1/2/3/4] (Enter = 1): ").strip()

    ritmo, init_min, inc_sec, inc_armageddon = "oficial", 15.0, 10.0, 0.0
    if tc_choice == "2":
        ritmo = "edital"
    elif tc_choice == "3":
        ritmo = "armageddon"  # 5 min com Brancas, 4 com Pretas (Config.relogio)
        try:
            # O FAQ 12 não fala em incremento no Armageddon: confirme com o árbitro
            inc_armageddon = max(0.0, float(input("  Incremento do Armageddon em segundos (Enter = 0): ").strip() or "0"))
        except ValueError:
            inc_armageddon = 0.0
    elif tc_choice == "4":
        ritmo = "personalizado"
        try:
            init_min = float(input("  Minutos iniciais (ex: 15): ").strip() or "15")
            inc_sec = float(input("  Incremento em segundos (ex: 10): ").strip() or "10")
        except ValueError:
            init_min, inc_sec = 15.0, 10.0

    # 3. Pondering (FAQ Item 13)
    ponder_input = input("\nAtivar Pondering (calcular durante turno adversário - FAQ Item 13)? [S/n]: ").strip().lower()
    use_ponder = ponder_input not in ("n", "nao", "não", "no", "0")

    # 4. Custo do operador humano por lance (anunciar + mover peça + apertar relógio + digitar)
    try:
        op_in = input("\nSegundos gastos pelo OPERADOR em cada lance nosso (anúncio+tabuleiro+relógio) [4]: ").strip()
        operator_overhead = max(0.0, float(op_in)) if op_in else 4.0
    except ValueError:
        operator_overhead = 4.0

    # 5. Motor: Stockfish com a nossa gestão de tempo (torneio) ou o motor próprio (reserva)
    motor, desempenho = "proprio", "ultra"
    if stockfish:
        escolha = input("\nMotor: [1] Stockfish com a nossa gestão de tempo (padrão) | [2] Próprio, reserva: ").strip()
        motor = "proprio" if escolha == "2" else "stockfish"
    if motor == "stockfish":
        escolha = input("\nDesempenho do Stockfish: [1] Ultra, força total (padrão) | [2] Médio | [3] Baixo: ").strip()
        desempenho = {"2": "medio", "3": "baixo"}.get(escolha, "ultra")

    return Config(our_color=our_color, ritmo=ritmo, minutos=init_min, incremento=inc_sec, ponder=use_ponder,
                  operador=operator_overhead, motor=motor, desempenho=desempenho, inc_armageddon=inc_armageddon)


def _conferir_relogio_retomado(time_manager: TimeManager, logger: MatchLogger) -> None:
    """Partida retomada: o relógio físico manda, porque o tempo com o programa fechado não está em arquivo nenhum."""
    fmt = TimeManager.format_clock
    print("\n  Confira os relógios físicos: o tempo em que o programa ficou fechado não está em arquivo nenhum.")
    while True:
        try:
            texto = input(f"  Relógios agora, nosso e do adversário (ex.: 12:30 11:45). Enter mantém "
                          f"{fmt(time_manager.remaining_time)} e {fmt(time_manager.opponent_time)}: ").strip()
        except EOFError:
            return
        if not texto:
            return
        partes = texto.split()
        try:
            nosso = ler_relogio(partes[0])
            adv = ler_relogio(partes[1]) if len(partes) > 1 else None
        except ValueError as exc:
            print(f"  [ERRO] {exc}")
            continue
        time_manager.sync_clocks(nosso, adv)
        logger.log_event(f"Relógio sincronizado para {nosso:.1f}s" + (f" (adversário {adv:.1f}s)" if adv is not None else ""))
        return


def run_tournament_cli() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass
    print("==============================================================================")
    print("              CHESS CHALLENGE III - ICEV 2026 - MOTOR OFICIAL                 ")
    print("      Stockfish + gestão de tempo da equipe | reserva: motor próprio (Python) ")
    print("==============================================================================")
    stockfish = verificar_stockfish()  # sem Stockfish, oferece instalar; senão segue com o motor próprio

    # Partida interrompida (o notebook travou, desligou ou a janela fechou): retomada, pula as perguntas
    sessao = _oferecer_retomada()
    if sessao is None:
        config = _perguntar_config(stockfish)
        our_color = config.our_color
        color_name = "BRANCAS" if our_color == chess.WHITE else "PRETAS"
        armageddon = config.ritmo == "armageddon"
        init_min, inc_sec = config.relogio()
        use_ponder = config.ponder
        operator_overhead = config.operador

        # Inicializa componentes
        board = chess.Board()
        time_manager = TimeManager(
            initial_minutes=init_min,
            increment_seconds=inc_sec,
            move_overhead=0.5,
            operator_overhead=operator_overhead,
        )
        try:
            engine = criar_motor(config.motor, config.desempenho)
        except MotorIndisponivel as erro:
            print(f"\n  {erro}\n  Seguindo com o motor próprio.")
            engine = criar_motor("proprio")
            config.motor = "proprio"
        engine.our_color = our_color  # contempt contado a partir da nossa cor, inclusive no ponder
        logger = MatchLogger(team_color=color_name)
        logger.log_event(f"Motor: {descrever_motor(engine)}")
        logger.log_event(evento_configuracao(config))  # com ela, o log sozinho basta para retomar a partida
        if armageddon and not isinstance(engine, SearchEngine):
            # Armageddon (FAQ Item 12): o Stockfish não tem contempt; a regra do empate imediato faz as
            # Pretas jogarem o lance que empata na hora e as Brancas fugirem dele (motor_stockfish.py)
            engine.regra_empate = "evitar" if our_color == chess.WHITE else "buscar"
            logger.log_event("Armageddon: empate classifica as Pretas (FAQ Item 12). Regra do empate imediato: "
                             + ("Brancas evitam o lance que empata na hora." if our_color == chess.WHITE
                                else "Pretas jogam o lance que empata na hora."))
        elif armageddon:
            # Armageddon (FAQ Item 12): empate classifica as Pretas. Com Brancas o empate é derrota
            # (evitar); com Pretas vale a classificação (buscar repetição e finais empatados).
            engine.contempt = ARMAGEDDON_CONTEMPT if our_color == chess.WHITE else -ARMAGEDDON_CONTEMPT
            logger.log_event(f"Armageddon: contempt {engine.contempt:+d} cp (empate classifica as Pretas - FAQ Item 12)")
    else:
        # Motor, contempt do Armageddon, relógios e ajustes já vêm da partida retomada (session.py)
        config = sessao.config
        our_color = sessao.our_color
        color_name = "BRANCAS" if our_color == chess.WHITE else "PRETAS"
        armageddon = config.ritmo == "armageddon"
        init_min, inc_sec = config.relogio()
        use_ponder = sessao.use_ponder
        board, time_manager, engine, logger = sessao.board, sessao.time_manager, sessao.engine, sessao.logger
        operator_overhead = time_manager.operator_overhead
        aviso = sessao.tomar_aviso()
        if aviso:
            print(f"\n  [ATENÇÃO] {aviso}")
        _conferir_relogio_retomado(time_manager, logger)
    usa_stockfish = not isinstance(engine, SearchEngine)
    ponder_ctrl = PonderController(engine)

    print("\n------------------------------------------------------------------------------")
    print(f"  Partida {'Retomada' if sessao is not None else 'Iniciada'}! Jogando de: {color_name}")
    print(f"  Relógio Configurado         : {init_min:.0f} min + {inc_sec:.0f}s/lance")
    print(f"  Pondering (FAQ Item 13)     : {'ATIVADO' if use_ponder else 'DESATIVADO'}")
    print(f"  Custo do operador por lance : {operator_overhead:.1f}s (descontado do relógio interno)")
    print(f"  {'Motor':<28}: {descrever_motor(engine)}")
    if usa_stockfish:
        if armageddon:
            regra = "evitam o lance que empata na hora" if our_color == chess.WHITE else "jogam o lance que empata na hora"
            print(f"  {'Armageddon':<28}: empate classifica as Pretas; regra do empate imediato: {'Brancas' if our_color == chess.WHITE else 'Pretas'} {regra}")
    else:
        print(f"  {'Contempt (empate)':<28}: {engine.contempt:+d} cp"
              + ("  (Armageddon: empate classifica as Pretas)" if armageddon else ""))
    print("  DICA: sincronize o relógio a cada ~10 lances com 'tempo MM:SS [MM:SS]'.")
    print(f"  Log Oficial (FAQ Item 9)    : {logger.log_path}")
    print(f"  Espelho Rápido p/ Banca     : {logger.txt_mirror_path}")
    print(f"  Tabuleiro no navegador      : {HTML_PATH}")
    print("  Digite 'ajuda' a qualquer momento para ver os comandos de emergência.")
    print("------------------------------------------------------------------------------")

    # Gera o primeiro SVG e abre a página do tabuleiro (uma vez só)
    try:
        salvar_tabuleiro_svg(board, our_color, None)
        if ABRIR_NAVEGADOR:
            webbrowser.open("file://" + HTML_PATH)
    except Exception as exc:
        print(f"  [AVISO] Não foi possível abrir o tabuleiro no navegador: {exc}")

    last_predicted_opp_move: str | None = None
    ultima_energia = -20.0

    while True:
        try:
            last_move = board.peek() if board.move_stack else None
            print_board_visual(board, perspective=our_color, last_move=last_move)

            our_clock_str = TimeManager.format_clock(time_manager.remaining_time)
            opp_clock_str = TimeManager.format_clock(time_manager.opponent_time)
            turn_str = "BRANCAS" if board.turn == chess.WHITE else "PRETAS"
            print(
                f"  Lance #{board.fullmove_number} | Vez de: {turn_str} | "
                f"Nosso Tempo: [{our_clock_str}] | Tempo Adv (est.): [{opp_clock_str}]"
            )

            # Verifica fim de jogo REAL (mate, afogamento, material, tripla repetição ou 50 lances)
            if partida_acabou(board):
                ponder_ctrl.stop_ponder()
                result_str, reason = resultado_partida(board)
                print("\n==============================================================================")
                print(f"  FIM DA PARTIDA! Resultado: {result_str} ({reason})")
                print(f"  Arquivo de Log pronto para entrega à banca em:\n  -> {logger.log_path}")
                print(f"  -> {logger.txt_mirror_path}")
                print("==============================================================================")
                logger.log_event(f"Fim da partida: {result_str} ({reason})")

                post_cmd = input("\nDigite 'undo' se precisar voltar o último lance ou Enter para encerrar: ").strip().lower()
                if post_cmd.startswith("undo") or post_cmd.startswith("voltar"):
                    if board.move_stack:
                        board.pop()
                        logger.log_event("Comando UNDO executado após fim de jogo.")
                    continue
                break

            # Alerta preventivo: o PRÓXIMO lance pode permitir reivindicar empate (FAQ Item 12)
            if board.can_claim_threefold_repetition():
                print("  [ALERTA ÁRBITRO - FAQ ITEM 12] Atenção: tripla repetição possível neste lance.")
            elif board.halfmove_clock >= 96:
                print(f"  [ALERTA ÁRBITRO - FAQ ITEM 12] Regra dos 50 lances perto: {board.halfmove_clock}/100 meios-lances.")

            # ==================================================================
            # TURNO DA NOSSA EQUIPE (MOTOR CALCULA E ESCOLHE O LANCE)
            # ==================================================================
            if board.turn == our_color:
                # Ponder acertou o lance do adversário: o tempo dele nesta posição conta como já gasto
                credito = ponder_ctrl.credito_para(board)
                time_manager.dar_credito_ponder(credito)
                print("\n  >>> Calculando melhor lance..." + (f" (ponder acertou: {credito:.1f}s de crédito)" if credito else ""))
                try:
                    try:
                        result = engine.search_best_move(
                            board=board,
                            time_manager=time_manager,
                            max_depth=64,
                            use_book=True,
                            use_tb=True,
                            verbose=True,
                        )
                    except MotorIndisponivel as exc:
                        # O Stockfish caiu e não reabriu: a partida segue com o motor próprio (o relógio não para)
                        print(f"\n  [STOCKFISH CAIU] {exc}\n  >>> Seguindo com o motor próprio da equipe.")
                        logger.log_event("O Stockfish caiu e não reabriu: a partida segue com o motor próprio "
                                         f"da equipe. Detalhe: {exc}")
                        engine = SearchEngine()
                        engine.our_color = our_color
                        if armageddon:
                            engine.contempt = ARMAGEDDON_CONTEMPT if our_color == chess.WHITE else -ARMAGEDDON_CONTEMPT
                        ponder_ctrl = PonderController(engine)
                        result = engine.search_best_move(
                            board=board,
                            time_manager=time_manager,
                            max_depth=64,
                            use_book=True,
                            use_tb=True,
                            verbose=True,
                        )
                except Exception as exc:
                    print(f"\n  [ERRO NA BUSCA] {exc}\n  >>> Usando LANCE DE EMERGÊNCIA (avaliação em 1 ply).")
                    logger.log_event(f"Exceção na busca: {exc}. Lance de emergência usado.")
                    result = lance_de_emergencia(board, time_manager)

                chosen_move = result.best_move
                uci_str = chosen_move.uci()
                move_desc = describe_move_pt(board, chosen_move)
                move_num = board.fullmove_number

                # Atualiza relógio interno e registra no Log Obrigatório (FAQ Item 9)
                time_manager.register_move_time(result.elapsed_sec)
                time_manager.registrar_busca(result.score_cp, result.depth, result.source)
                logger.log_engine_move(
                    move_number=move_num,
                    turn_color=our_color,
                    uci_move=uci_str,
                    depth=result.depth,
                    nodes=result.nodes,
                    score_cp=result.score_cp,
                    pv_moves=result.pv,
                    elapsed_sec=result.elapsed_sec,
                    source=result.source,
                )

                # Executa o lance no tabuleiro
                board.push(chosen_move)

                # Guarda o lance esperado do adversário (2º elemento da PV) para o Pondering
                last_predicted_opp_move = result.pv[1] if len(result.pv) > 1 else None

                eval_formatted = MatchLogger.format_score(result.score_cp)
                pv_formatted = " ".join(result.pv)

                print("\n" + "=" * 78)
                print(f"  >>> PASSO 1 (ANUNCIAR EM VOZ ALTA - UCI):   {uci_str}   <<<")
                print(f"  >>> PASSO 3 (MOVER NO TABULEIRO FÍSICO) :   {move_desc}")
                print("-" * 78)
                print(
                    f"  Profundidade: {result.depth} (seletiva {result.seldepth}) | Nós: {result.nodes} | "
                    f"Avaliação: {eval_formatted} | Tempo: {result.elapsed_sec:.2f}s [{result.source}]"
                )
                print(f"  Variante Principal (PV): {pv_formatted}")
                print("=" * 78)
                continue

            # ==================================================================
            # TURNO DO ADVERSÁRIO (AGUARDA ANÚNCIO VERBAL + PONDERING OPCIONAL)
            # ==================================================================
            opp_start_wait = time.perf_counter()
            if use_ponder:
                ponder_ctrl.start_ponder(board, predicted_uci=last_predicted_opp_move)
            # Tomada e modo de energia, na vez do adversário (a consulta leva até ~0,5 s; no máximo a cada 20 s)
            if opp_start_wait - ultima_energia >= 20.0:
                ultima_energia = opp_start_wait
                for aviso in avisos_de_energia():
                    print(f"  [ATENÇÃO - ENERGIA] {aviso}")

            raw_cmd = input(
                f"\n[VEZ DO ADVERSÁRIO] Digite o lance anunciado em UCI (ex: e7e5) ou comando ('ajuda'): "
            ).strip()

            # Assim que o operador aperta Enter, paramos o Pondering imediatamente!
            ponder_ctrl.stop_ponder()
            opp_elapsed = time.perf_counter() - opp_start_wait

            if not raw_cmd:
                continue

            cmd_lower = raw_cmd.lower()

            # --- Tratamento de Comandos Administrativos / Emergência ---
            if cmd_lower in ("sair", "quit", "exit"):
                # Como o Encerrar da TUI: resultado do árbitro (abandono, acordo, bandeira) encerra a partida;
                # "r" fecha o programa e a partida fica para retomar (recuperacao.py)
                escolha = input("Encerrar: [v] vencemos | [d] perdemos | [e] empate (resultado do árbitro) | "
                                "[r] sair e retomar depois | Enter volta à partida: ").strip().lower()
                if escolha in ("v", "d", "e"):
                    if escolha == "e":
                        placar = "1/2-1/2"
                    else:
                        placar = "1-0" if (escolha == "v") == (our_color == chess.WHITE) else "0-1"
                    logger.log_event(f"Resultado declarado pelo operador: {placar}")
                    print(f"\n  Resultado {placar} registrado. Log para a banca: {logger.log_path}")
                    break
                if escolha == "r":
                    logger.log_event("Partida interrompida pelo operador: pode ser retomada (recuperacao.py).")
                    print("\n  Partida guardada. Para continuar: python main.py (ou tui.py, web.py) e Retomar.")
                    break
                continue

            if cmd_lower in ("ajuda", "help", "?"):
                print_help_menu()
                continue

            if cmd_lower == "log":
                print(f"\n  Arquivo .log oficial : {logger.log_path}")
                print(f"  Espelho .txt p/ banca: {logger.txt_mirror_path}")
                continue

            if cmd_lower == "ponder":
                use_ponder = not use_ponder
                print(f"\n  [CONFIG] Pondering agora está: {'ATIVADO' if use_ponder else 'DESATIVADO'}")
                logger.log_event(f"Pondering {'ativado' if use_ponder else 'desativado'}")  # para retomar igual
                continue

            if cmd_lower == "avaliar":
                st_eval = evaluate_board(board, check_terminal=True)
                print(f"\n  [AVALIAÇÃO ESTÁTICA ATUAL]: {MatchLogger.format_score(st_eval)} (perspectiva de quem joga)")
                continue

            if cmd_lower.startswith("undo") or cmd_lower.startswith("voltar"):
                parts = cmd_lower.split()
                steps = 2 if len(parts) == 1 and len(board.move_stack) >= 2 else 1
                if len(parts) > 1 and parts[1].isdigit():
                    steps = int(parts[1])
                undone = []
                for _ in range(min(steps, len(board.move_stack))):
                    m = board.pop()
                    undone.append(m.uci())
                last_predicted_opp_move = None
                time_manager.limpar_historico()
                print(f"\n  [UNDO] Lances desfeitos com sucesso: {', '.join(undone)}")
                logger.log_event(f"UNDO executado. Lances revertidos: {', '.join(undone)}")
                continue

            if cmd_lower.startswith("tempo"):
                parts = cmd_lower.split()
                if len(parts) >= 2:
                    try:
                        our_s = parse_clock_input(parts[1])
                        opp_s = parse_clock_input(parts[2]) if len(parts) >= 3 else None
                        time_manager.sync_clocks(our_s, opp_s)
                        print(
                            f"\n  [RELÓGIO SINCRONIZADO] Nosso tempo: {TimeManager.format_clock(time_manager.remaining_time)}"
                        )
                        extra = f" (adversário {opp_s:.1f}s)" if opp_s is not None else ""
                        logger.log_event(f"Relógio sincronizado para {our_s:.1f}s{extra}")
                    except ValueError:
                        print("  [ERRO] Formato inválido. Use: tempo 12:30 ou tempo 750")
                else:
                    print("  [USO] Digite: tempo <nosso_tempo_mm:ss> [tempo_adversario_mm:ss]")
                continue

            if cmd_lower.startswith("contempt"):
                parts = cmd_lower.split()
                try:
                    engine.contempt = max(-100, min(100, int(parts[1])))
                    print(f"\n  [CONFIG] Contempt: {engine.contempt} cp")
                    logger.log_event(f"Contempt ajustado para {engine.contempt} cp")
                except (IndexError, ValueError):
                    print("  [USO] Digite: contempt <centipawns>  (ex: contempt 25)")
                continue

            if cmd_lower.startswith("fator"):
                parts = cmd_lower.split()
                try:
                    time_manager.time_factor = min(3.0, max(0.1, float(parts[1])))
                    print(f"\n  [CONFIG] Fator de tempo: {time_manager.time_factor:.2f}x")
                    logger.log_event(f"Fator de tempo ajustado para {time_manager.time_factor:.2f}")
                except (IndexError, ValueError):
                    print("  [USO] Digite: fator <x>  (ex: fator 0.7)")
                continue

            if cmd_lower.startswith("operador"):
                parts = cmd_lower.split()
                try:
                    time_manager.operator_overhead = max(0.0, float(parts[1]))
                    print(f"\n  [CONFIG] Custo do operador por lance: {time_manager.operator_overhead:.1f}s")
                    logger.log_event(f"Custo do operador ajustado para {time_manager.operator_overhead:.1f}s")
                except (IndexError, ValueError):
                    print("  [USO] Digite: operador <segundos>  (ex: operador 2.5)")
                continue

            if cmd_lower.startswith("fen "):
                fen_str = raw_cmd[4:].strip()
                try:
                    new_board = chess.Board(fen_str)
                    board = new_board
                    last_predicted_opp_move = None
                    time_manager.limpar_historico()
                    print("\n  [TABULEIRO ATUALIZADO VIA FEN COM SUCESSO!]")
                    logger.log_event(f"Posição ajustada via FEN: {fen_str}")
                except ValueError as e:
                    print(f"  [ERRO] FEN inválido ({e}). O tabuleiro não foi alterado.")
                continue

            if cmd_lower.startswith("forcar "):
                forced_str = cmd_lower.split()[1]
                try:
                    fmove = chess.Move.from_uci(forced_str)
                    if fmove in board.legal_moves:
                        board.push(fmove)
                        time_manager.limpar_historico()
                        logger.log_event(f"Lance forçado manualmente: {forced_str}")
                        print(f"\n  [FORÇADO] Lance {forced_str} aplicado.")
                    else:
                        print(f"  [ERRO] O lance {forced_str} não é legal nesta posição!")
                except ValueError:
                    print(f"  [ERRO] Formato UCI inválido: {forced_str}")
                continue

            # --- Interpretação do Lance do Adversário (UCI ou SAN com validação) ---
            clean_move_str = cmd_lower.replace("-", "").replace(" ", "")
            parsed_move: chess.Move | None = None

            # 1º Tenta interpretar como notação UCI padrão (ex: e2e4, g1f3, e7e8q)
            try:
                candidate = chess.Move.from_uci(clean_move_str)
                if candidate in board.legal_moves:
                    parsed_move = candidate
                else:
                    # Verifica se o operador esqueceu a letra da promoção (ex: digitou e7e8 em vez de e7e8q)
                    promo_candidate = chess.Move.from_uci(clean_move_str + "q")
                    if promo_candidate in board.legal_moves:
                        print("  [AVISO] Promoção detectada! Assumindo promoção para Dama ('q').")
                        parsed_move = promo_candidate
            except ValueError:
                pass

            # 2º Se não for UCI válido, tenta interpretar como SAN (ex: Nf3, O-O, exd5) para evitar erro humano
            if parsed_move is None:
                try:
                    san_candidate = board.parse_san(raw_cmd.strip())
                    if san_candidate in board.legal_moves:
                        print(
                            f"  [CONVERSÃO AUTOMÁTICA] Você digitou '{raw_cmd}', convertido para UCI: '{san_candidate.uci()}'"
                        )
                        parsed_move = san_candidate
                except ValueError:
                    pass

            if parsed_move is None:
                print("\n" + "!" * 78)
                print(f"  [ATENÇÃO - LANCE INVÁLIDO OU ILEGAL]: '{raw_cmd}'")
                print("  1. Verifique se você digitou corretamente em notação UCI (ex: e7e5, g8f6, e8g8).")
                print("  2. Se o adversário realmente anunciou esse lance e ele é ilegal no tabuleiro,")
                print("     chame o árbitro! Pelo FAQ Item 10: 'Lance ilegal anunciado é anulado e a")
                print("     equipe adversária deve anunciar lance legal com o relógio correndo.'")
                legal_sample = ", ".join(m.uci() for m in list(board.legal_moves)[:12])
                print(f"  Lances legais possíveis agora: {legal_sample} ...")
                print("!" * 78)
                continue

            # Registra e executa o lance válido do adversário
            opp_desc = describe_move_pt(board, parsed_move)
            time_manager.register_opponent_time(opp_elapsed)
            logger.log_opponent_move(
                move_number=board.fullmove_number,
                turn_color=board.turn,
                uci_move=parsed_move.uci(),
                elapsed_sec=opp_elapsed,
            )
            board.push(parsed_move)
            print(f"\n  [OK] Lance adversário registrado: {parsed_move.uci()} ({opp_desc})")

        except KeyboardInterrupt:
            ponder_ctrl.stop_ponder()
            print("\n\n  [INTERRUPÇÃO DETECTADA] Digite 'sair' se quiser encerrar ou continue digitando o lance.")
        except EOFError:
            # Sem entrada (terminal fechado): sair em vez de repetir o erro sem parar, enchendo o log da banca.
            # A partida está no log e pode ser retomada em qualquer interface (recuperacao.py).
            ponder_ctrl.stop_ponder()
            logger.log_event("Entrada do terminal fechada: a partida pode ser retomada (recuperacao.py).")
            print("\n\n  [ENTRADA FECHADA] A partida ficou no log e pode ser retomada: python main.py")
            break
        except Exception as exc:
            ponder_ctrl.stop_ponder()
            print(f"\n  [RECUPERAÇÃO AUTOMÁTICA DE ERRO]: {exc}")
            logger.log_event(f"Exceção recuperada automaticamente: {exc}")


if __name__ == "__main__":
    run_tournament_cli()