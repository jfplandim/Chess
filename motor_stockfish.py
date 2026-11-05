"""
Motor de torneio: Stockfish (busca e avaliação, de terceiros) com a GESTÃO DE TEMPO da equipe.
Chess Challenge III - ICEV 2026

Regras (FAQ oficial):
- Item 1: usar o Stockfish é permitido se tudo roda localmente. O projeto-base e a versão são
  declarados no credenciamento. O Stockfish (GPLv3) roda como programa separado, conversando
  por UCI, sem nenhuma alteração no código dele.
- Item 2: o componente decisório de autoria própria é a gestão de tempo (time_manager.py).
  O Stockfish pensa sem limite ("go infinite") e quem decide quanto pensar e quando parar é o
  nosso TimeManager, com as mesmas regras usadas no motor próprio:
    * orçamento por lance que desconta o tempo do operador (anunciar, mover, apertar o relógio);
    * a cada profundidade concluída: pensa mais se o melhor lance mudou, se a nota caiu ou se a
      raiz falhou baixo, e economiza quando o lance fica estável;
    * nova profundidade só se houver tempo provável para terminá-la; o limite rígido para na hora.
- Itens 3 e 4: livro de aberturas (até 10 meios-lances) e tablebases na raiz (até 5 peças) são
  os mesmos do motor próprio (book_and_tb.py). O Stockfish só recebe tablebases de até 5 peças.

O motor próprio (search.py) continua disponível como reserva: os dois têm a mesma interface
(search_best_move, contempt, our_color, book_tb, resize_tt, clear_cache).
"""
from __future__ import annotations

import atexit
import glob
import logging
import os
import shutil
import subprocess
import sys
import threading
import time

import chess
import chess.engine

from book_and_tb import MAX_SYZYGY_PIECES, BookAndTablebaseManager
from evaluation import MATE_SCORE, MATE_THRESHOLD, evaluate, evaluate_board
from search import SearchResult
from time_manager import TimeManager

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PASTA_SYZYGY = os.path.join(BASE_DIR, "syzygy")
ESPERA_ENTRE_CONSULTAS = 0.01  # segundos entre uma olhada e outra nas informações do Stockfish
# Tabela de transposição pela memória do computador: (RAM mínima em MB, tabela em MB)
HASH_POR_RAM = ((30_000, 4096), (14_000, 2048), (7_000, 1024), (0, 512))

NOME_MOTOR = {"stockfish": "Stockfish", "proprio": "Motor próprio"}

# O python-chess avisa no stderr sobre linhas como "info string Using 4 threads"; na TUI isso
# bagunçaria a tela e no UCI sujaria o terminal da GUI. Erros de verdade continuam virando exceção.
logging.getLogger("chess.engine").addHandler(logging.NullHandler())


class MotorIndisponivel(RuntimeError):
    """O Stockfish não foi encontrado ou não abriu."""


def _registrar_fechamento(funcao) -> None:
    """
    Fecha o Stockfish antes de o Python esperar as threads do programa: o python-chess conversa com
    o Stockfish numa thread que não é daemon, então um atexit comum nunca rodaria e o programa
    travaria na saída. threading._register_atexit roda antes dessa espera (o concurrent.futures usa
    o mesmo gancho); o atexit fica de reserva.
    """
    registrar = getattr(threading, "_register_atexit", None)
    if registrar is not None:
        try:
            registrar(funcao)
        except RuntimeError:  # o programa já está encerrando
            pass
    atexit.register(funcao)


def encontrar_stockfish() -> str | None:
    """
    Caminho do executável do Stockfish, nesta ordem:
    1. variável de ambiente STOCKFISH_PATH;
    2. pasta stockfish/ ao lado do projeto (onde o tools/baixar_stockfish.py instala);
    3. pasta stockfish/ dentro do projeto;
    4. "stockfish" no PATH e os caminhos comuns dos pacotes Linux.
    """
    candidatos: list[str] = []
    variavel = os.environ.get("STOCKFISH_PATH", "").strip()
    if variavel:
        candidatos.append(variavel)
    for pasta in (os.path.join(BASE_DIR, "..", "stockfish"), os.path.join(BASE_DIR, "stockfish")):
        candidatos += sorted(glob.glob(os.path.join(pasta, "stockfish", "stockfish*")))
        candidatos += sorted(glob.glob(os.path.join(pasta, "stockfish*")))
    no_path = shutil.which("stockfish")
    if no_path:
        candidatos.append(no_path)
    candidatos += ["/usr/games/stockfish", "/usr/bin/stockfish", "/usr/local/bin/stockfish"]
    for caminho in candidatos:
        if os.path.isfile(caminho) and os.access(caminho, os.X_OK):
            return os.path.abspath(caminho)
    return None


# ----------------------------------------------------------------------
# Força total em qualquer computador: o Stockfish universal já escolhe a versão para o processador
# (AVX2, AVX-512...); aqui escolhemos threads e tabela pelo hardware. O Stockfish não usa GPU.
# ----------------------------------------------------------------------
def threads_padrao() -> int:
    """Todos os processadores que este programa pode usar (ou STOCKFISH_THREADS)."""
    variavel = os.environ.get("STOCKFISH_THREADS", "").strip()
    if variavel.isdigit():
        return max(1, int(variavel))
    contar = getattr(os, "process_cpu_count", None) or os.cpu_count  # process_cpu_count: Python 3.13+
    return max(1, contar() or 1)


def memoria_total_mb() -> int | None:
    """RAM total do computador, em MB (Linux, macOS e Windows)."""
    try:
        return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") // (1024 * 1024)
    except (ValueError, OSError, AttributeError):
        pass
    if sys.platform == "win32":
        import ctypes

        class Memoria(ctypes.Structure):
            _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                        ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                        ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                        ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                        ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]

        estado = Memoria()
        estado.dwLength = ctypes.sizeof(estado)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(estado)):
            return estado.ullTotalPhys // (1024 * 1024)
    return None


def hash_padrao_mb() -> int:
    """Tabela de transposição proporcional à RAM (ou STOCKFISH_HASH), deixando folga para o resto."""
    variavel = os.environ.get("STOCKFISH_HASH", "").strip()
    if variavel.isdigit():
        return max(16, int(variavel))
    total = memoria_total_mb()
    if total is None:
        return 1024
    return next(tamanho for minimo, tamanho in HASH_POR_RAM if total >= minimo)


NIVEIS_DESEMPENHO = ("ultra", "medio", "baixo")
NOME_DESEMPENHO = {"ultra": "Ultra", "medio": "Médio", "baixo": "Baixo"}


def fmt_threads(n: int) -> str:
    return "1 thread" if n == 1 else f"{n} threads"


def recursos_do_nivel(nivel: str) -> tuple[int, int]:
    """
    (threads, tabela em MB) de cada nível de desempenho do Stockfish:
    Ultra = força total (todos os threads e tabela pela RAM); Médio = metade dos dois;
    Baixo = 1 thread e 256 MB (notebook fraco, na bateria ou com outros programas abertos).
    """
    threads, tabela = threads_padrao(), hash_padrao_mb()
    if nivel == "baixo":
        return 1, min(256, tabela)
    if nivel == "medio":
        return max(1, threads // 2), max(256, tabela // 2)
    if nivel != "ultra":
        raise ValueError(f"Nível de desempenho desconhecido: {nivel}")
    return threads, tabela


def arquitetura_stockfish(caminho: str) -> str | None:
    """Versão escolhida pelo Stockfish universal para este processador (ex.: x86-64-avx512icl)."""
    try:
        saida = subprocess.run([caminho], input="compiler\nquit\n", capture_output=True, text=True, timeout=15).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    for linha in saida.splitlines():
        if linha.startswith("Compilation architecture"):
            return linha.split(":", 1)[1].strip()
    return None


def avisos_de_energia() -> list[str]:
    """Na bateria ou em economia de energia o processador fica bem mais lento, e o motor mais fraco."""
    avisos: list[str] = []
    na_bateria = None
    if sys.platform.startswith("linux"):
        for tipo in glob.glob("/sys/class/power_supply/*/type"):
            try:
                if open(tipo).read().strip() != "Mains":
                    continue
                online = open(os.path.join(os.path.dirname(tipo), "online")).read().strip()
            except OSError:
                continue
            na_bateria = online != "1" if na_bateria is None else (na_bateria and online != "1")
        perfil = ""
        for arquivo in ("/sys/firmware/acpi/platform_profile",
                        "/sys/devices/system/cpu/cpu0/cpufreq/energy_performance_preference"):
            try:
                perfil = open(arquivo).read().strip()
                break
            except OSError:
                continue
        if perfil in ("low-power", "quiet", "power", "balance_power"):
            avisos.append(f"O modo de energia está em economia ({perfil}): mude para desempenho antes de jogar.")
    elif sys.platform == "win32":
        import ctypes

        class Energia(ctypes.Structure):
            _fields_ = [("ACLineStatus", ctypes.c_ubyte), ("BatteryFlag", ctypes.c_ubyte),
                        ("BatteryLifePercent", ctypes.c_ubyte), ("SystemStatusFlag", ctypes.c_ubyte),
                        ("BatteryLifeTime", ctypes.c_ulong), ("BatteryFullLifeTime", ctypes.c_ulong)]

        estado = Energia()
        if ctypes.windll.kernel32.GetSystemPowerStatus(ctypes.byref(estado)):
            na_bateria = {0: True, 1: False}.get(estado.ACLineStatus)
            if estado.SystemStatusFlag == 1:
                avisos.append("A economia de bateria do Windows está ligada: desligue antes de jogar.")
    if na_bateria:
        avisos.insert(0, "O notebook está na bateria: ligue na tomada. Na bateria o processador fica mais lento.")
    return avisos


COMANDO_INSTALAR = "python tools/baixar_stockfish.py"
RESPOSTAS_SIM = ("", "s", "sim", "y", "yes")


def versao_stockfish(caminho: str) -> str | None:
    """Abre o Stockfish e devolve o 'id name' (ex.: 'Stockfish 19'), ou None se ele não roda aqui."""
    try:
        saida = subprocess.run([caminho], input="uci\nquit\n", capture_output=True, text=True, timeout=15).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    for linha in saida.splitlines():
        if linha.startswith("id name "):
            return linha[len("id name "):].strip()
    return None


def _anunciar_pronto(versao: str, caminho: str) -> None:
    """O que foi reconhecido neste computador, e os avisos de energia."""
    arquitetura = arquitetura_stockfish(caminho)
    feito_para = f", versão para {arquitetura}" if arquitetura else ""
    print(f"Motor do torneio pronto: {versao}{feito_para}; no desempenho Ultra, {fmt_threads(threads_padrao())} e "
          f"{hash_padrao_mb()} MB de tabela ({caminho})")
    for aviso in avisos_de_energia():
        print(f"Atenção: {aviso}")


def verificar_stockfish(perguntar: bool | None = None) -> str | None:
    """
    Conferência feita ao abrir web.py, tui.py e main.py: o Stockfish está instalado e roda neste
    computador? Se não, explica e oferece instalar (com internet), usando tools/baixar_stockfish.py.
    Nada acessa a internet sem o operador responder que sim.

    perguntar=None pergunta só num terminal interativo; fora dele (testes, GUIs) só mostra o comando.
    Devolve o caminho do executável pronto, ou None (a partida segue com o motor próprio).
    """
    caminho = encontrar_stockfish()
    versao = versao_stockfish(caminho) if caminho else None
    if versao:
        _anunciar_pronto(versao, caminho)
        return caminho
    if caminho:
        print(f"O Stockfish encontrado em {caminho} não abriu neste computador.")
    else:
        print("Stockfish não encontrado neste computador.")
    print("Sem ele, a partida usa o motor próprio da equipe (reserva).")
    if perguntar is None:
        perguntar = sys.stdin.isatty() and sys.stdout.isatty()
    if not perguntar:
        print(f"Para instalar (precisa de internet, cerca de 80 MB): {COMANDO_INSTALAR}")
        return None
    try:
        resposta = input("Instalar o Stockfish 19 agora? Precisa de internet, cerca de 80 MB. [S/n] ")
    except (EOFError, KeyboardInterrupt):
        print()
        resposta = "n"
    if resposta.strip().lower() not in RESPOSTAS_SIM:
        print(f"Seguindo com o motor próprio. Para instalar depois: {COMANDO_INSTALAR}")
        return None
    instalador = os.path.join(BASE_DIR, "tools", "baixar_stockfish.py")
    try:
        deu_certo = subprocess.run([sys.executable, instalador]).returncode == 0
    except KeyboardInterrupt:
        deu_certo = False
    caminho = encontrar_stockfish()
    versao = versao_stockfish(caminho) if caminho else None
    if deu_certo and versao:
        _anunciar_pronto(versao, caminho)
        return caminho
    print(f"Não deu para instalar agora (sem internet?). Seguindo com o motor próprio. Depois: {COMANDO_INSTALAR}")
    return None


def nota_da_info(score: chess.engine.PovScore, cor: chess.Color) -> int:
    """Nota do ponto de vista de quem joga, na mesma escala do motor próprio (mate = MATE_SCORE - meios-lances)."""
    relativa = score.pov(cor)
    mate = relativa.mate()
    if mate is None:
        return int(relativa.score())
    if mate > 0:
        return MATE_SCORE - (2 * mate - 1)
    return -(MATE_SCORE - 2 * (-mate))


class MotorStockfish:
    """Adaptador com a mesma interface do SearchEngine (search.py)."""

    def __init__(self, caminho: str | None = None, threads: int | None = None, hash_mb: int | None = None):
        self.caminho = caminho or encontrar_stockfish()
        if not self.caminho:
            raise MotorIndisponivel(
                "Stockfish não encontrado. Instale com: python tools/baixar_stockfish.py "
                "(ou defina STOCKFISH_PATH com o caminho do executável)."
            )
        self.desempenho = "ultra"
        threads_ultra, tabela_ultra = recursos_do_nivel("ultra")
        self.threads = threads or threads_ultra
        self.hash_mb = hash_mb or tabela_ultra
        self.arquitetura = arquitetura_stockfish(self.caminho)
        # Compatibilidade com o motor próprio. O Stockfish não tem contempt (avalia o empate como 0).
        self.contempt = 0
        self.our_color: bool | None = None
        self.book_tb = BookAndTablebaseManager()
        self.nome = "Stockfish"
        self._trava = threading.Lock()
        self._sf: chess.engine.SimpleEngine | None = None
        self._jogo = object()  # trocar este objeto faz o python-chess mandar "ucinewgame"
        self._abrir()
        _registrar_fechamento(self.fechar)

    # ------------------------------------------------------------------
    # Processo do Stockfish
    # ------------------------------------------------------------------
    def _abrir(self) -> None:
        try:
            sf = chess.engine.SimpleEngine.popen_uci(self.caminho, timeout=30)
        except (OSError, chess.engine.EngineError, chess.engine.EngineTerminatedError, TimeoutError) as erro:
            raise MotorIndisponivel(f"O Stockfish não abriu ({self.caminho}): {erro}") from erro
        opcoes: dict[str, int | str] = {"Threads": self.threads, "Hash": self.hash_mb}
        if self.book_tb.max_pieces():
            # FAQ item 4: só tablebases de até 5 peças
            opcoes["SyzygyPath"] = PASTA_SYZYGY
            opcoes["SyzygyProbeLimit"] = MAX_SYZYGY_PIECES
        sf.configure(opcoes)
        self._sf = sf
        self.nome = sf.id.get("name", "Stockfish")

    def _garantir(self) -> chess.engine.SimpleEngine:
        """Devolve o Stockfish pronto; se o processo tiver caído, abre de novo."""
        if self._sf is not None:
            try:
                self._sf.ping()
                return self._sf
            except (chess.engine.EngineError, chess.engine.EngineTerminatedError, OSError, TimeoutError):
                self._fechar_processo()
        self._abrir()
        return self._sf

    def _fechar_processo(self) -> None:
        sf, self._sf = self._sf, None
        if sf is not None:
            try:
                sf.quit()
            except Exception:  # noqa: BLE001 - processo já caído
                try:
                    sf.close()
                except Exception:  # noqa: BLE001
                    pass

    def fechar(self) -> None:
        """Encerra o processo. Se uma busca não liberar a trava em 2 s, fecha assim mesmo (saída do programa)."""
        pegou = self._trava.acquire(timeout=2.0)
        try:
            self._fechar_processo()
        finally:
            if pegou:
                self._trava.release()

    # ------------------------------------------------------------------
    # Interface comum com o motor próprio
    # ------------------------------------------------------------------
    def clear_cache(self) -> None:
        """
        Nova partida: manda "ucinewgame" na hora (com uma busca de profundidade 1), para o Stockfish
        limpar a tabela de transposição agora, e não no primeiro lance, com o relógio correndo.
        """
        with self._trava:
            self._jogo = object()
            try:
                self._garantir().analyse(chess.Board(), chess.engine.Limit(depth=1), game=self._jogo)
            except (chess.engine.EngineError, chess.engine.EngineTerminatedError, OSError, TimeoutError):
                pass  # se falhar aqui, a próxima busca reabre o processo

    def resize_tt(self, mb: int) -> None:
        self.ajustar_recursos(self.threads, mb)

    def definir_desempenho(self, nivel: str) -> None:
        """Baixo, Médio ou Ultra (força total): muda threads e tabela do mesmo processo do Stockfish."""
        threads, tabela = recursos_do_nivel(nivel)
        self.desempenho = nivel
        self.ajustar_recursos(threads, tabela)

    def ajustar_recursos(self, threads: int, hash_mb: int) -> None:
        """Reconfigura o Stockfish entre buscas (mudar a tabela a apaga, então é feito no começo da partida)."""
        threads, hash_mb = max(1, int(threads)), max(1, int(hash_mb))
        with self._trava:
            if (threads, hash_mb) == (self.threads, self.hash_mb):
                return
            self.threads, self.hash_mb = threads, hash_mb
            if self._sf is None:
                return  # abre com os valores novos na próxima busca
            try:
                self._sf.configure({"Threads": threads, "Hash": hash_mb})
            except (chess.engine.EngineError, chess.engine.EngineTerminatedError, OSError, TimeoutError):
                self._fechar_processo()  # reabre com os valores novos na próxima busca

    def search_best_move(
        self,
        board: chess.Board,
        time_manager: TimeManager,
        max_depth: int = 64,
        use_book: bool = True,
        use_tb: bool = True,
        stop_event: threading.Event | None = None,
        verbose: bool = True,
        infinite: bool = False,
        max_nodes: int = 0,
        info_callback=None,
    ) -> SearchResult:
        """
        Seleção do lance:
        1. Livro de aberturas (até 10 meios-lances - FAQ item 3), o mesmo do motor próprio
        2. Tablebases na raiz (até 5 peças - FAQ item 4)
        3. Stockfish em "go infinite", parado pela nossa gestão de tempo
        """
        with self._trava:
            return self._buscar(board, time_manager, max_depth, use_book, use_tb, stop_event,
                                verbose, infinite, max_nodes, info_callback)

    # ------------------------------------------------------------------
    def _buscar(self, board, tm: TimeManager, max_depth, use_book, use_tb, stop_event,
                verbose, infinite, max_nodes, info_callback) -> SearchResult:
        legais = list(board.legal_moves)
        if not legais:
            raise ValueError("Não existem lances legais na posição atual.")
        tm.allocate_time(board, len(legais))

        if use_book:
            lance = self.book_tb.get_book_move(board)
            if lance is not None:
                board.push(lance)
                nota = -evaluate(board)
                board.pop()
                if verbose:
                    print(f"  [LIVRO DE ABERTURAS] Lance teórico encontrado: {lance.uci()} ({tm.elapsed():.3f}s)")
                return SearchResult(lance, nota, 1, 1, tm.elapsed(), [lance.uci()], "BOOK", 0, [lance])

        if use_tb:
            lance, nota = self.book_tb.get_syzygy_move(board)
            if lance is not None and nota is not None:
                if verbose:
                    print(f"  [TABLEBASE 5 PEÇAS] Lance perfeito encontrado: {lance.uci()} ({tm.elapsed():.3f}s)")
                return SearchResult(lance, nota, 1, 1, tm.elapsed(), [lance.uci()], "SYZYGY_TB", 0, [lance])

        if len(legais) == 1 and not infinite:
            unico = legais[0]
            board.push(unico)
            nota = -evaluate_board(board, check_terminal=True)
            board.pop()
            return SearchResult(unico, nota, 1, 1, tm.elapsed(), [unico.uci()], "SEARCH", 0, [unico])

        # Limites que o próprio Stockfish respeita (usados só em testes e no UCI): profundidade e nós.
        # Sem eles, "go infinite": quem para é a nossa gestão de tempo.
        limite = None
        if max_nodes:
            limite = chess.engine.Limit(nodes=max_nodes)
        elif max_depth < 64:
            limite = chess.engine.Limit(depth=max_depth)

        cor = board.turn
        melhor: chess.Move | None = None
        nota = 0
        pv: list[chess.Move] = []
        profundidade = seldepth = nos = 0
        nota_anterior: int | None = None
        parar = False

        sf = self._garantir()
        with sf.analysis(board, limite, game=self._jogo, info=chess.engine.INFO_ALL) as analise:
            terminou = False
            while True:
                while not analise.would_block():
                    info = analise.next()
                    if info is None:  # o Stockfish parou sozinho (limite de profundidade ou de nós)
                        terminou = True
                        break
                    nos = info.get("nodes", nos)
                    seldepth = info.get("seldepth", seldepth)
                    falhou_baixo = bool(info.get("upperbound"))
                    if falhou_baixo and not infinite:
                        tm.notify_fail_low()  # a nota da raiz caiu: a gestão de tempo pensa mais
                    if falhou_baixo or info.get("lowerbound") or not info.get("pv") or "score" not in info:
                        continue
                    d = info.get("depth", 0)
                    if d <= profundidade:
                        continue
                    # Profundidade concluída: é aqui que a nossa gestão de tempo decide
                    novo_pv = list(info["pv"])
                    nova_nota = nota_da_info(info["score"], cor)
                    mudou = melhor is not None and novo_pv[0] != melhor
                    queda = (nota_anterior - nova_nota) if nota_anterior is not None else 0
                    melhor, nota, pv, profundidade = novo_pv[0], nova_nota, novo_pv, d
                    nota_anterior = nova_nota
                    decorrido = tm.elapsed()
                    if info_callback is not None:
                        info_callback(d, seldepth, nota, nos, decorrido, list(pv))
                    if verbose:
                        nps = int(nos / max(0.001, decorrido))
                        print(f"  info depth {d:>2}/{seldepth:<2} | score {nota:>+6d} cp | nodes {nos:>10d} "
                              f"({nps:>8d} n/s) | time {decorrido:>6.2f}s | pv {' '.join(m.uci() for m in pv[:8])}")
                    if infinite:
                        continue
                    if abs(nota) >= MATE_THRESHOLD and d >= (MATE_SCORE - abs(nota)) + 2:
                        parar = True  # mate forçado confirmado: não precisa pensar mais
                    tm.adjust_for_instability(mudou, queda, d)
                    if tm.should_stop_iteration():
                        parar = True
                if terminou or parar:
                    break
                if stop_event is not None and stop_event.is_set():
                    break
                if not infinite and tm.should_abort_search():
                    break  # limite rígido: para na hora
                if stop_event is not None:
                    stop_event.wait(ESPERA_ENTRE_CONSULTAS)
                else:
                    time.sleep(ESPERA_ENTRE_CONSULTAS)
            analise.stop()
            resposta = analise.wait()
            final = analise.info

        lance = resposta.move if resposta is not None and resposta.move in legais else (melhor or legais[0])
        nos = final.get("nodes", nos)
        seldepth = final.get("seldepth", seldepth)
        pv_final = list(final.get("pv") or [])
        if pv_final and pv_final[0] == lance:
            pv = pv_final
            if "score" in final:
                nota = nota_da_info(final["score"], cor)
            profundidade = max(profundidade, final.get("depth", 0))
        elif not pv or pv[0] != lance:
            pv = [lance]
        return SearchResult(lance, nota, max(1, profundidade), max(1, nos), tm.elapsed(),
                            [m.uci() for m in pv], "SEARCH", seldepth, list(pv))


# ----------------------------------------------------------------------
# Escolha do motor
# ----------------------------------------------------------------------
_compartilhado: MotorStockfish | None = None
_trava_compartilhado = threading.Lock()


def stockfish_compartilhado() -> MotorStockfish:
    """Um só processo do Stockfish por programa: partidas novas reaproveitam (com "ucinewgame")."""
    global _compartilhado
    with _trava_compartilhado:
        if _compartilhado is None:
            _compartilhado = MotorStockfish()
        return _compartilhado


def criar_motor(nome: str, desempenho: str = "ultra"):
    """
    'stockfish' (padrão do torneio) ou 'proprio' (reserva: o motor da equipe em Python).
    O desempenho (Baixo, Médio ou Ultra) vale para o Stockfish; o motor próprio usa um processador só.
    """
    if nome == "stockfish":
        motor = stockfish_compartilhado()
        motor.definir_desempenho(desempenho)
        motor.clear_cache()
        motor.contempt = 0
        motor.our_color = None
        return motor
    if nome == "proprio":
        from search import SearchEngine
        return SearchEngine()
    raise ValueError(f"Motor desconhecido: {nome}")


def motor_padrao() -> str:
    return "stockfish" if encontrar_stockfish() else "proprio"


def descrever_motor(motor) -> str:
    """Uma linha para o log da banca e para a tela."""
    if isinstance(motor, MotorStockfish):
        feito_para = f"versão {motor.arquitetura}, " if motor.arquitetura else ""
        return (f"{motor.nome} (busca e avaliação, de terceiros, GPLv3) com a gestão de tempo da equipe; "
                f"desempenho {NOME_DESEMPENHO.get(motor.desempenho, motor.desempenho)}: "
                f"{feito_para}{fmt_threads(motor.threads)}, hash de {motor.hash_mb} MB")
    return "Motor próprio da equipe (avaliação, busca e gestão de tempo)"
