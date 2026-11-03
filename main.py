"""
Interface Principal de Torneio (CLI Blindada + Pondering + Logs)
Chess Challenge III - ICEV 2026

Segue rigorosamente o protocolo de 4 passos do FAQ Item 10:
1. Exibe em destaque o lance em notação UCI (ex: e2e4, g1f3, e7e8q) para anúncio em voz alta.
2. Exibe explicação clara da casa de origem e destino para movimentação no tabuleiro físico.
3. Registra automaticamente no arquivo .log e .txt todos os dados exigidos pelo FAQ Item 9.
4. Possui Pondering em background (FAQ Item 13) e comandos de emergência (undo, tempo, fen)
   para garantir que o software jamais trave ou perca sincronia com o tabuleiro físico.
"""

import sys
import time
import threading
import chess

from evaluation import evaluate_board
from time_manager import TimeManager
from search import SearchEngine
from logger import MatchLogger

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

UNICODE_PIECES = {
    "P": "♙", "N": "♘", "B": "♗", "R": "♖", "Q": "♕", "K": "♔",
    "p": "♟", "n": "♞", "b": "♝", "r": "♜", "q": "♛", "k": "♚",
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
    """Imprime o tabuleiro formatado no terminal com coordenadas e destaque do último lance."""
    print("\n    +------------------------+")
    ranks = range(7, -1, -1) if perspective == chess.WHITE else range(8)
    files = range(8) if perspective == chess.WHITE else range(7, -1, -1)

    for r in ranks:
        row_str = f"  {r + 1} | "
        for f in files:
            sq = chess.square(f, r)
            piece = board.piece_at(sq)
            symbol = piece.symbol() if piece else "."

            # Destaca casas de origem/destino do último lance com colchetes
            if last_move and (sq == last_move.from_square or sq == last_move.to_square):
                cell = f"[{symbol}]"
            else:
                cell = f" {symbol} "
            row_str += cell
        row_str += "|"
        print(row_str)

    print("    +------------------------+")
    if perspective == chess.WHITE:
        print("       a  b  c  d  e  f  g  h\n")
    else:
        print("       h  g  f  e  d  c  b  a\n")


def parse_clock_input(val_str: str) -> float:
    """Converte entrada 'MM:SS' ou segundos/minutos para segundos."""
    val_str = val_str.strip()
    if ":" in val_str:
        parts = val_str.split(":")
        return float(parts[0]) * 60.0 + float(parts[1])
    return float(val_str)


class PonderController:
    """
    Gerencia o Pondering em background (FAQ Item 13) durante o turno do adversário.
    Preenche a Tabela de Transposição enquanto aguarda a digitação do lance oponente,
    interrompendo instantaneamente assim que o operador pressiona Enter.
    """

    def __init__(self, engine: SearchEngine):
        self.engine = engine
        self.stop_event = threading.Event()
        self.thread: threading.Thread | None = None

    def start_ponder(self, board: chess.Board, predicted_uci: str | None = None) -> None:
        self.stop_ponder()
        self.stop_event.clear()

        ponder_board = board.copy()
        if predicted_uci:
            try:
                pmove = chess.Move.from_uci(predicted_uci)
                if pmove in ponder_board.legal_moves:
                    ponder_board.push(pmove)
            except ValueError:
                pass

        if ponder_board.is_game_over():
            return

        def _ponder_worker():
            # Cria um TimeManager virtual longo apenas para o background (limitado a 90s)
            ponder_tm = TimeManager(initial_minutes=30.0, increment_seconds=0.0)
            ponder_tm.soft_limit = 60.0
            ponder_tm.hard_limit = 90.0
            ponder_tm.start_time = time.perf_counter()
            try:
                self.engine.search_best_move(
                    board=ponder_board,
                    time_manager=ponder_tm,
                    max_depth=12,
                    use_book=False,
                    use_tb=False,
                    stop_event=self.stop_event,
                    verbose=False,
                )
            except Exception:
                pass

        self.thread = threading.Thread(target=_ponder_worker, daemon=True)
        self.thread.start()

    def stop_ponder(self) -> None:
        if self.thread is not None and self.thread.is_alive():
            self.stop_event.set()
            self.thread.join(timeout=0.5)
        self.thread = None


def print_help_menu() -> None:
    print(
        "\n==================== COMANDOS ESPECIAIS DURANTE A PARTIDA ====================\n"
        "  <lance_uci>          : Registra lance adversário (ex: e2e4, g1f3, e7e8q)\n"
        "  undo / voltar        : Desfaz o último meio-lance (use 'undo 2' para voltar 2 lances)\n"
        "  tempo <nosso> [adv]  : Sincroniza relógio em MM:SS ou segundos (ex: tempo 12:30 11:45)\n"
        "  fen <string_fen>     : Atualiza posição do tabuleiro conforme tabuleiro físico\n"
        "  forcar <lance_uci>   : Força a execução de um lance específico\n"
        "  ponder               : Ativa/Desativa o cálculo no turno do adversário (Pondering)\n"
        "  avaliar              : Mostra a avaliação estática detalhada da posição atual\n"
        "  log                  : Mostra o caminho do arquivo .log/.txt oficial para a banca\n"
        "  sair / quit          : Encerra a partida e salva o log final\n"
        "==============================================================================\n"
    )


def run_tournament_cli() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass
    print("==============================================================================")
    print("              CHESS CHALLENGE III - ICEV 2026 - MOTOR OFICIAL                 ")
    print("        Negamax Alpha-Beta + PVS + TT + PeSTO Tapered Eval + Book/TB          ")
    print("==============================================================================")

    # 1. Seleção da Cor da Equipe
    our_color = chess.WHITE
    while True:
        choice = input("\nQual a cor da nossa equipe nesta partida? [1] BRANCAS (padrão) | [2] PRETAS: ").strip().lower()
        if choice in ("", "1", "b", "brancas", "w", "white"):
            our_color = chess.WHITE
            color_name = "BRANCAS"
            break
        elif choice in ("2", "p", "pretas", "black"):
            our_color = chess.BLACK
            color_name = "PRETAS"
            break
        print("Opção inválida. Digite 1 para Brancas ou 2 para Pretas.")

    # 2. Seleção do Controle de Tempo
    print("\nSelecione o Controle de Tempo:")
    print("  [1] 15 min + 10s incremento (Padrão Oficial FAQ Item 7 - Recomendado)")
    print("  [2] 25 min + 0s incremento  (Padrão Edital)")
    print("  [3] Armageddon (5 min Brancas / 4 min Pretas - FAQ Item 12)")
    print("  [4] Personalizado")
    tc_choice = input("Escolha [1/2/3/4] (Enter = 1): ").strip()

    if tc_choice == "2":
        init_min, inc_sec = 25.0, 0.0
    elif tc_choice == "3":
        init_min = 5.0 if our_color == chess.WHITE else 4.0
        inc_sec = 0.0
    elif tc_choice == "4":
        try:
            init_min = float(input("  Minutos iniciais (ex: 15): ").strip() or "15")
            inc_sec = float(input("  Incremento em segundos (ex: 10): ").strip() or "10")
        except ValueError:
            init_min, inc_sec = 15.0, 10.0
    else:
        init_min, inc_sec = 15.0, 10.0

    # 3. Pondering (FAQ Item 13)
    ponder_input = input("\nAtivar Pondering (calcular durante turno adversário - FAQ Item 13)? [S/n]: ").strip().lower()
    use_ponder = ponder_input not in ("n", "nao", "não", "no", "0")

    # Inicializa componentes
    board = chess.Board()
    time_manager = TimeManager(initial_minutes=init_min, increment_seconds=inc_sec)
    engine = SearchEngine()
    logger = MatchLogger(team_color=color_name)
    ponder_ctrl = PonderController(engine)

    print("\n------------------------------------------------------------------------------")
    print(f"  Partida Iniciada! Jogando de: {color_name}")
    print(f"  Relógio Configurado         : {init_min:.0f} min + {inc_sec:.0f}s/lance")
    print(f"  Pondering (FAQ Item 13)     : {'ATIVADO' if use_ponder else 'DESATIVADO'}")
    print(f"  Log Oficial (FAQ Item 9)    : {logger.log_path}")
    print(f"  Espelho Rápido p/ Banca     : {logger.txt_mirror_path}")
    print("  Digite 'ajuda' a qualquer momento para ver os comandos de emergência.")
    print("------------------------------------------------------------------------------")

    last_predicted_opp_move: str | None = None

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

            # Verifica fim de jogo (Xeque-mate, Empate por tripla repetição, 50 lances, etc.)
            if board.is_game_over(claim_draw=True):
                ponder_ctrl.stop_ponder()
                outcome = board.outcome(claim_draw=True)
                result_str = board.result(claim_draw=True)
                reason = outcome.termination.name if outcome else "FIM DE JOGO"
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

            # Alerta de Tripla Repetição ou 50 lances (FAQ Item 12)
            if board.is_repetition(3):
                print("  [ALERTA ÁRBITRO - FAQ ITEM 12] Tripla repetição detectada! Empate automático.")
            elif board.halfmove_clock >= 100:
                print("  [ALERTA ÁRBITRO - FAQ ITEM 12] Regra dos 50 lances atingida! Empate automático.")

            # ==================================================================
            # TURNO DA NOSSA EQUIPE (MOTOR CALCULA E ESCOLHE O LANCE)
            # ==================================================================
            if board.turn == our_color:
                ponder_ctrl.stop_ponder()
                print("\n  >>> Calculando melhor lance...")
                result = engine.search_best_move(
                    board=board,
                    time_manager=time_manager,
                    max_depth=20,
                    use_book=True,
                    use_tb=True,
                    verbose=True,
                )

                chosen_move = result.best_move
                uci_str = chosen_move.uci()
                move_desc = describe_move_pt(board, chosen_move)
                move_num = board.fullmove_number

                # Atualiza relógio interno e registra no Log Obrigatório (FAQ Item 9)
                time_manager.register_move_time(result.elapsed_sec)
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
                    f"  Profundidade: {result.depth} | Nós: {result.nodes} | "
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
                confirm = input("Tem certeza que deseja encerrar a partida? [s/N]: ").strip().lower()
                if confirm in ("s", "sim", "y", "yes"):
                    logger.log_event("Partida encerrada manualmente pelo operador.")
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
                        logger.log_event(f"Relógio sincronizado para {our_s:.1f}s")
                    except ValueError:
                        print("  [ERRO] Formato inválido. Use: tempo 12:30 ou tempo 750")
                else:
                    print("  [USO] Digite: tempo <nosso_tempo_mm:ss> [tempo_adversario_mm:ss]")
                continue

            if cmd_lower.startswith("fen "):
                fen_str = raw_cmd[4:].strip()
                try:
                    new_board = chess.Board(fen_str)
                    board = new_board
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
        except Exception as exc:
            ponder_ctrl.stop_ponder()
            print(f"\n  [RECUPERAÇÃO AUTOMÁTICA DE ERRO]: {exc}")
            logger.log_event(f"Exceção recuperada automaticamente: {exc}")


if __name__ == "__main__":
    run_tournament_cli()
