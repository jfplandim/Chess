"""
Recuperação da partida interrompida - Chess Challenge III - ICEV 2026

Se o notebook travar, desligar (bateria, tomada) ou a janela fechar no meio da partida, a mesma função,
recuperar_partida(), remonta o jogo para as três interfaces: a TUI (tui.py), o navegador (web.py) e o
modo texto (main.py). Uma partida começada numa interface pode ser retomada em outra.

Há duas fontes, gravadas a cada lance e forçadas para o disco (fsync): o lance que aparece na tela já
está gravado, mesmo que a energia caia logo depois.
1. logs/partida_em_andamento.json, o arquivo salvo da sessão: configuração, relógios e súmula.
2. O log da banca (logs/partida_*.log, FAQ Item 9), que as três interfaces escrevem no mesmo formato.

O log é gravado antes do arquivo salvo, então nunca fica atrás dele. Se o arquivo salvo faltar, estiver
corrompido (a energia caiu no meio da gravação) ou atrasado, a partida é remontada lance a lance pelo log:
lances nossos e do adversário, desfazer, posição por FEN, lances forçados, relógio sincronizado, custo do
operador, fator de tempo, contempt e pondering. O relógio é refeito com a mesma conta do time_manager.py.
Ao retomar, as interfaces pedem os relógios físicos: o tempo com o programa fechado não está em arquivo
nenhum, e o tabuleiro físico é a fonte da verdade (FAQ Item 10).

Sem abrir interface nenhuma:
    python recuperacao.py                    # a partida que as interfaces vão oferecer para retomar
    python recuperacao.py --listar           # os logs de logs/ e como terminou cada partida
    python recuperacao.py --log ARQUIVO      # remonta a partida de um log (até de uma cópia de outro notebook)
    python recuperacao.py --log ARQUIVO --restaurar   # e deixa essa partida pronta para as interfaces retomarem
    python recuperacao.py --pgn              # mostra também o PGN, para conferir com o árbitro
"""

from __future__ import annotations

import argparse
import copy
import filecmp
import json
import os
import re
import shutil
import sys
from dataclasses import asdict, dataclass, field, is_dataclass
from datetime import datetime

import chess
import chess.pgn

from logger import forcar_disco, forcar_pasta
from time_manager import TimeManager

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOGS_DIR = os.path.join(BASE_DIR, "logs")
ARQUIVO_SALVO = os.path.join(LOGS_DIR, "partida_em_andamento.json")
ARQUIVO_SALVO_ANTERIOR = os.path.join(LOGS_DIR, "partida_salva_anterior.json")
VERSAO_SALVO = 1

COR_TEXTO = {chess.WHITE: "Brancas", chess.BLACK: "Pretas"}
PREFIXO_CONFIG = "Configuração da partida:"
CAMPOS_CONFIG = ("ritmo", "minutos", "incremento", "inc_armageddon", "operador", "ponder", "motor", "desempenho")
CAMPOS_LANCE = ("uci", "san", "descricao", "cor", "nosso", "origem", "tempo", "avaliacao", "profundidade")
# Só para log sem a linha de configuração (versão anterior do programa) e sem arquivo salvo
CONFIG_PADRAO = {"ritmo": "oficial", "minutos": 15.0, "incremento": 10.0, "inc_armageddon": 0.0,
                 "operador": 4.0, "ponder": True, "motor": "proprio", "desempenho": "ultra"}
# Chaves que a recuperação acrescenta para as interfaces e que não vão para o arquivo salvo
CHAVES_DA_RECUPERACAO = ("fonte", "avisos", "problema", "gravado_em")
AVISO_LINHA_CORTADA = ("A última linha do log ficou pela metade (a energia caiu durante a gravação) e foi "
                       "ignorada: se era o lance do adversário, digite de novo; se era o nosso, o motor pensa de novo.")
# Escrita no log ao retomar, logo depois da linha cortada (session.LogRetomado): a leitura descarta aquela linha
EVENTO_LINHA_CORTADA = "A linha anterior ficou pela metade (o programa parou durante a gravação) e não vale."
EVENTO_RELOGIO_MANTIDO = "Relógio interno mantido após o ajuste do tabuleiro:"  # session.ajustar_tabuleiro


# ==============================================================================
# Gravação que sobrevive a um desligamento
# ==============================================================================
def gravar_json(caminho: str, dados: dict) -> None:
    """
    Troca atômica: grava ao lado (.tmp), força o disco e só então troca pelo arquivo antigo. Se a energia
    cair no meio, fica o arquivo antigo inteiro ou o novo inteiro, nunca um pela metade.
    """
    pasta = os.path.dirname(os.path.abspath(caminho))
    os.makedirs(pasta, exist_ok=True)
    tmp = caminho + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(dados, f, ensure_ascii=False, indent=1)
        forcar_disco(f)
    os.replace(tmp, caminho)
    forcar_pasta(pasta)


def evento_configuracao(config) -> str:
    """
    Linha do log da banca com a configuração da partida (a cor já está no cabeçalho, e os ajustes feitos
    durante o jogo viram linhas próprias). Com ela, o log sozinho basta para retomar a partida.
    """
    valores = asdict(config) if is_dataclass(config) else dict(config)

    def texto(valor) -> str:
        if isinstance(valor, bool):
            return "sim" if valor else "nao"
        if isinstance(valor, (int, float)):
            return f"{float(valor):g}"
        return str(valor)

    return PREFIXO_CONFIG + " " + " ".join(f"{campo}={texto(valores[campo])}" for campo in CAMPOS_CONFIG)


def _ler_configuracao(texto: str) -> dict:
    pares = dict(re.findall(r"(\w+)=(\S+)", texto))
    config = {}
    for campo in ("minutos", "incremento", "inc_armageddon", "operador"):
        try:
            config[campo] = float(pares[campo].replace(",", "."))
        except (KeyError, ValueError):
            pass
    for campo, validos in (("ritmo", ("oficial", "armageddon", "edital", "personalizado")),
                           ("motor", ("stockfish", "proprio")), ("desempenho", ("ultra", "medio", "baixo"))):
        if pares.get(campo) in validos:
            config[campo] = pares[campo]
    if pares.get("ponder") in ("sim", "nao"):
        config["ponder"] = pares["ponder"] == "sim"
    return config


# ==============================================================================
# Leitura do log da banca (formato do logger.py)
# ==============================================================================
_EVENTO = re.compile(r"# \[(\d{2}:\d{2}:\d{2})\] EVENTO: (.*)")
_LINHA_DE_LANCE = re.compile(r"\s*\d+\s*\|")
_UCI = re.compile(r"[a-h][1-8][a-h][1-8][qrbn]?")
_CABECALHO_COR = re.compile(r"Cor da Equipe\s*:\s*(\S+)")
_CABECALHO_INICIO = re.compile(r"Data/Hora de Início\s*:\s*(.+?)\s*$")
_CORES_DO_LOG = {"BRANCAS": chess.WHITE, "PRETAS": chess.BLACK}


@dataclass
class LinhaDoLog:
    """Uma linha útil do log: um lance (nosso ou do adversário) ou um evento."""

    numero: int                     # linha no arquivo, para as mensagens
    evento: str | None = None       # texto do evento; None quando a linha é um lance
    uci: str = ""
    cor: bool = chess.WHITE
    nosso: bool = False
    origem: str = ""                # BOOK, SYZYGY_TB, SEARCH, MANUAL... ou ADVERSARIO
    tempo: float = 0.0
    avaliacao: int | None = None
    profundidade: int = 0


@dataclass
class LogLido:
    caminho: str
    cor_equipe: bool | None = None  # None: log do modo UCI ou sem cabeçalho (não é partida de torneio)
    inicio: str = ""                # "06/11/2026 14:00:00", do cabeçalho
    linhas: list[LinhaDoLog] = field(default_factory=list)
    cortado: bool = False           # a última linha ficou pela metade (a energia caiu durante a gravação)
    erro: str | None = None


_LOGS_LIDOS: dict[str, tuple[tuple, LogLido]] = {}


def _marca(caminho: str | None) -> tuple | None:
    """Muda sempre que o arquivo muda (para não refazer a leitura a cada consulta)."""
    if not caminho:
        return None
    try:
        info = os.stat(caminho)
    except OSError:
        return None
    return info.st_mtime_ns, info.st_size, info.st_ino


def ler_log(caminho: str) -> LogLido:
    """Lê um log da banca, guardando a leitura enquanto o arquivo não muda (o navegador consulta a cada 0,4 s)."""
    marca = _marca(caminho)
    if marca is None:
        return LogLido(caminho, erro=f"Não deu para abrir o log {caminho}.")
    guardado = _LOGS_LIDOS.get(caminho)
    if guardado is not None and guardado[0] == marca:
        return guardado[1]
    lido = _ler_log(caminho)
    _LOGS_LIDOS[caminho] = (marca, lido)
    return lido


def _ler_log(caminho: str) -> LogLido:
    lido = LogLido(caminho)
    try:
        with open(caminho, encoding="utf-8", errors="replace", newline="") as f:
            conteudo = f.read()
    except OSError as exc:
        lido.erro = f"Não deu para ler o log {caminho}: {exc.strerror or exc}"
        return lido
    # A energia caindo no meio da gravação pode deixar zeros no fim do arquivo
    linhas = conteudo.replace("\x00", "").split("\n")
    # Sem a quebra de linha no fim, a gravação parou no meio da última linha, e ela não vale: se era o nosso
    # lance, talvez nem tenha aparecido na tela; se era o do adversário, o operador digita de novo
    lido.cortado = bool(linhas[-1].strip())
    for numero, linha in enumerate(linhas[:-1], 1):
        linha = linha.rstrip("\r")
        if numero <= 12:
            m = _CABECALHO_COR.match(linha)
            if m:
                lido.cor_equipe = _CORES_DO_LOG.get(m.group(1))  # UCI_MODE fica None
            m = _CABECALHO_INICIO.match(linha)
            if m:
                lido.inicio = m.group(1)
        m = _EVENTO.fullmatch(linha)
        if m and m.group(2).strip() == EVENTO_LINHA_CORTADA:
            if lido.linhas and lido.linhas[-1].numero == numero - 1:
                lido.linhas.pop()  # a linha cortada, que a retomada fechou com uma quebra de linha
        elif m:
            lido.linhas.append(LinhaDoLog(numero, evento=m.group(2).strip()))
        elif _LINHA_DE_LANCE.match(linha):
            lance = _ler_linha_de_lance(numero, linha)
            if lance is not None:
                lido.linhas.append(lance)
    return lido


def _ler_linha_de_lance(numero: int, linha: str) -> LinhaDoLog | None:
    partes = [parte.strip() for parte in linha.split("|", 7)]
    if len(partes) < 8 or partes[1] not in _CORES_DO_LOG or not _UCI.fullmatch(partes[2]):
        return None
    _, cor, uci, profundidade, _nos, avaliacao, tempo, pv = partes
    try:
        segundos = float(tempo.rstrip("s"))
    except ValueError:
        segundos = 0.0
    if avaliacao == "(Adversário)":
        return LinhaDoLog(numero, uci=uci, cor=_CORES_DO_LOG[cor], origem="ADVERSARIO", tempo=segundos)
    origem = re.match(r"\[(\w+)\]", pv)  # [BOOK], [SYZYGY_TB], [MANUAL]... sem marca, foi a busca
    return LinhaDoLog(
        numero, uci=uci, cor=_CORES_DO_LOG[cor], nosso=True, origem=origem.group(1) if origem else "SEARCH",
        tempo=segundos, avaliacao=_nota(avaliacao), profundidade=int(profundidade) if profundidade.isdigit() else 0,
    )


def _nota(texto: str) -> int | None:
    """'+36 cp (+0.36)' dá 36; '+MATE em 3 (+29995)' e '+VITÓRIA TB (+19500)' dão o número entre parênteses."""
    m = re.match(r"([+-]?\d+) cp", texto) or re.search(r"\(([+-]?\d+)\)$", texto)
    return int(m.group(1)) if m else None


def _configuracao(lido: LogLido, base: dict | None) -> tuple[dict, str]:
    """
    A configuração da partida e de onde ela veio: a linha de configuração do log; sem ela (versão anterior
    do programa), a do arquivo salvo da mesma partida; sem nenhuma das duas, o que der para deduzir.
    """
    config = dict(CONFIG_PADRAO)
    eventos = [linha.evento for linha in lido.linhas if linha.evento is not None]
    linha_config = next((evento for evento in eventos if evento.startswith(PREFIXO_CONFIG)), None)
    if linha_config is not None:
        config.update(_ler_configuracao(linha_config))
        return config, "log"
    if base:
        for campo in CAMPOS_CONFIG:
            if campo not in base:
                continue
            try:
                padrao = CONFIG_PADRAO[campo]
                config[campo] = (bool(base[campo]) if isinstance(padrao, bool)
                                 else float(base[campo]) if isinstance(padrao, float) else str(base[campo]))
            except (TypeError, ValueError):
                pass
        return config, "salvo"
    for evento in eventos:
        if evento.startswith("Motor:"):
            config["motor"] = "stockfish" if "Stockfish" in evento else "proprio"
            nivel = re.search(r"desempenho (Ultra|Médio|Baixo)", evento)
            if nivel:
                config["desempenho"] = {"Ultra": "ultra", "Médio": "medio", "Baixo": "baixo"}[nivel.group(1)]
        elif evento.startswith("Armageddon:"):
            config["ritmo"] = "armageddon"
    return config, "deduzida"


# ==============================================================================
# Remontar a partida pelo log
# ==============================================================================
def remontar(lido: LogLido, config_base: dict | None = None) -> dict | None:
    """
    Refaz a partida do log, linha por linha, no formato do arquivo salvo (o que GameSession.retomar recebe).
    config_base: configuração do arquivo salvo da mesma partida, para logs sem a linha de configuração.
    None se o log não abriu ou não é de partida de torneio (o modo UCI não registra o lance do adversário).
    """
    # Import tardio: session.py importa este módulo
    from session import MOTIVOS_FIM, Config, descrever, partida_acabou, resultado_partida

    if lido.erro or lido.cor_equipe is None:
        return None
    nossa_cor = lido.cor_equipe
    config, origem_config = _configuracao(lido, config_base)
    minutos, incremento = Config(our_color=nossa_cor, **config).relogio()
    tm = TimeManager(initial_minutes=minutos, increment_seconds=incremento, move_overhead=0.5,
                     operator_overhead=config["operador"])
    operador_inicial, ponder, contempt, fator_antes = config["operador"], config["ponder"], None, None
    board = chess.Board()
    historico: list[dict] = []
    fim: tuple[str, str] | None = None   # resultado anunciado no log: vale até o próximo lance ou desfazer
    problema: str | None = None

    def jogar(lance: chess.Move, item: dict) -> None:
        item.update(uci=lance.uci(), san=board.san(lance), descricao=descrever(board, lance), cor=board.turn)
        board.push(lance)
        historico.append(item)

    def desfazer() -> chess.Move | None:
        if not board.move_stack:
            return None
        if historico:
            historico.pop()
        return board.pop()

    for linha in lido.linhas:
        evento = linha.evento
        if evento is None:
            lance = chess.Move.from_uci(linha.uci)
            if linha.cor != board.turn or lance not in board.legal_moves:
                problema = (f"na linha {linha.numero}, o lance {linha.uci} das {COR_TEXTO[linha.cor]} não é legal "
                            "na posição remontada")
                break
            if linha.nosso:
                tm.register_move_time(linha.tempo)
            else:
                tm.register_opponent_time(linha.tempo)
            jogar(lance, {"nosso": linha.nosso, "origem": linha.origem, "tempo": linha.tempo,
                          "avaliacao": linha.avaliacao, "profundidade": linha.profundidade})
            fim = None
        elif evento.startswith("UNDO executado. Lances revertidos:"):
            for nome in [n.strip() for n in evento.split(":", 1)[1].split(",") if n.strip()]:
                desfeito = desfazer()
                if desfeito is None or desfeito.uci() != nome:
                    problema = f"na linha {linha.numero}, o log desfaz {nome}, que não era o último lance remontado"
                    break
            if problema:
                break
            fim = None
        elif evento == "Comando UNDO executado após fim de jogo.":  # main.py: volta um meio-lance
            desfazer()
            fim = None
        elif evento.startswith("Posição ajustada via FEN:"):
            try:
                board = chess.Board(evento.split(":", 1)[1].strip())
            except ValueError:
                problema = f"na linha {linha.numero}, a FEN não pôde ser lida"
                break
            historico, fim = [], None
        elif evento.startswith("Lance forçado manualmente:"):  # main.py, comando forcar (não tem linha de lance)
            try:
                lance = chess.Move.from_uci(evento.split(":", 1)[1].strip())
            except ValueError:
                lance = None
            if lance is None or lance not in board.legal_moves:
                problema = f"na linha {linha.numero}, o lance forçado não é legal na posição remontada"
                break
            jogar(lance, {"nosso": board.turn == nossa_cor, "origem": "MANUAL", "tempo": 0.0,
                          "avaliacao": None, "profundidade": 0})
            fim = None
        elif evento.startswith(("Relógio sincronizado para", "Partida retomada", EVENTO_RELOGIO_MANTIDO)):
            m = re.search(r"(?:sincronizado para|relógio|tabuleiro:) (\d+(?:\.\d+)?)s(?: \(adversário (\d+(?:\.\d+)?)s\))?",
                          evento)
            if m:
                tm.sync_clocks(float(m.group(1)), float(m.group(2)) if m.group(2) else None)
        elif evento.startswith("Custo do operador ajustado para"):
            m = re.search(r"para (\d+(?:\.\d+)?)s", evento)
            if m:
                tm.operator_overhead = operador_inicial = float(m.group(1))
        elif evento.startswith("Custo do operador recalibrado"):
            m = re.search(r"para (\d+(?:,\d+)?) s", evento)
            if m:
                tm.operator_overhead = float(m.group(1).replace(",", "."))
        elif evento.startswith("Fator de tempo ajustado para"):
            m = re.search(r"para (\d+(?:\.\d+)?)", evento)
            if m:
                fator_antes = tm.time_factor if "(modo economia)" in evento else None
                tm.time_factor = float(m.group(1))
        elif evento.startswith(("Contempt ajustado para", "Armageddon: contempt")):
            m = re.search(r"(?:para|contempt) ([+-]?\d+) cp", evento)
            if m:
                contempt = int(m.group(1))
        elif evento in ("Pondering ativado", "Pondering desativado"):
            ponder = evento == "Pondering ativado"
        elif evento.startswith("Fim da partida:"):
            m = re.match(r"Fim da partida: (\S+) \((\w+)\)", evento)
            fim = (m.group(1), MOTIVOS_FIM.get(m.group(2), "fim de jogo")) if m else ("*", "fim de jogo")
        elif evento.startswith("Resultado declarado pelo operador:"):
            fim = (evento.split(":", 1)[1].strip(), "declarado pelo árbitro")
        elif evento == "Partida encerrada manualmente pelo operador.":  # main.py antigo: 'sair' encerrava
            fim = ("*", "encerrada pelo operador")

    if partida_acabou(board):
        placar, motivo = resultado_partida(board)
        fim = (placar, MOTIVOS_FIM.get(motivo, "fim de jogo"))
    avisos = []
    if origem_config == "deduzida":
        avisos.append("O log não tem a linha de configuração (versão anterior do programa): ritmo, operador e "
                      "pondering foram deduzidos. Confira nos Ajustes.")
    if problema:
        avisos.append(f"O log tem um trecho que não bate: {problema}. A partida foi remontada até ali; confira o "
                      "tabuleiro físico e corrija a posição (F5 na interface, fen no modo texto).")
    if lido.cortado:
        avisos.append(AVISO_LINHA_CORTADA)
    return {
        "versao": VERSAO_SALVO,
        "config": {**config, "our_color": "w" if nossa_cor == chess.WHITE else "b"},
        "fen_inicial": board.root().fen(),
        "lances": [lance.uci() for lance in board.move_stack],
        "historico": historico,
        "relogio_nosso": tm.remaining_time,
        "relogio_adv": tm.opponent_time,
        "operador": tm.operator_overhead,
        "operador_inicial": operador_inicial,
        "fator": tm.time_factor,
        "fator_antes_economia": fator_antes,
        "contempt": contempt,                # None: o padrão do motor para esta configuração
        "ponder": ponder,
        "log_path": lido.caminho,
        "encerrada": fim is not None,
        "resultado": list(fim) if fim else None,
        "salvo_em": _hora(lido.caminho),
        "fonte": "log",
        "avisos": avisos,
        "problema": problema,
    }


# ==============================================================================
# Arquivo salvo, conferido com o log
# ==============================================================================
def _ler_salvo(caminho: str) -> tuple[dict | None, str | None]:
    """O arquivo salvo, conferido (versão e lances legais). Faltando: (None, None). Estragado: (None, motivo)."""
    try:
        with open(caminho, encoding="utf-8") as f:
            dados = json.load(f)
    except FileNotFoundError:
        return None, None
    except (OSError, ValueError):
        return None, "O arquivo salvo da partida estava corrompido (a energia pode ter caído durante a gravação)"
    try:
        if dados["versao"] != VERSAO_SALVO:
            return None, "O arquivo salvo é de outra versão do programa"
        board = chess.Board(dados["fen_inicial"])
        for uci in dados["lances"]:
            board.push_uci(uci)  # erro se o lance não for legal
        if dados["config"]["our_color"] not in ("w", "b"):
            raise ValueError(dados["config"]["our_color"])
        for chave in ("relogio_nosso", "relogio_adv", "operador", "fator"):
            float(dados[chave])
    except (KeyError, TypeError, ValueError, AttributeError):
        return None, "O arquivo salvo da partida está incompleto ou não bate com as regras"
    return dados, None


def _conferir_com_log(salvo: dict, caminho_log: str | None) -> dict:
    """
    O arquivo salvo, conferido com o log da mesma partida. O log é gravado antes: se os lances não batem, o
    arquivo salvo ficou para trás (a energia caiu entre as duas gravações, ou o modo texto continuou a
    partida) e vale o log.
    """
    estado = copy.deepcopy(salvo)
    estado.update(fonte="salvo", avisos=[], problema=None)
    lido = ler_log(caminho_log) if caminho_log else None
    do_log = remontar(lido, config_base=salvo["config"]) if lido else None
    if do_log is None:
        if salvo.get("log_path"):
            estado["avisos"].append("O log da banca desta partida não foi encontrado: ao retomar, um log novo é aberto.")
        estado["historico"] = _sumula_conferida(estado)
        return estado
    estado["log_path"] = caminho_log
    if (do_log["fen_inicial"], do_log["lances"]) == (salvo["fen_inicial"], list(salvo["lances"])):
        estado["historico"] = _sumula_conferida(estado, do_log["historico"])
        estado["encerrada"], estado["resultado"] = do_log["encerrada"], do_log["resultado"]
        if lido.cortado:
            estado["avisos"].append(AVISO_LINHA_CORTADA)
        return estado
    if do_log["problema"]:
        estado["avisos"].append("O log da banca não bate com o arquivo salvo e tem um trecho que não pôde ser "
                                "remontado: a partida segue pelo arquivo salvo. Confira o tabuleiro físico.")
        estado["historico"] = _sumula_conferida(estado)
        return estado
    do_log["avisos"].insert(0, f"O arquivo salvo estava atrasado ({len(salvo['lances'])} meios-lances, contra "
                               f"{len(do_log['lances'])} no log da banca, que é gravado antes): a partida foi "
                               "remontada pelo log.")
    return do_log


def _sumula_conferida(estado: dict, do_log: list | None = None) -> list[dict]:
    """A súmula que bate com os lances: a do arquivo salvo, a do log ou, sem nenhuma, refeita dos lances."""
    lances = list(estado["lances"])
    for sumula in (estado.get("historico"), do_log):
        if (isinstance(sumula, list) and len(sumula) == len(lances)
                and all(isinstance(item, dict) and all(campo in item for campo in CAMPOS_LANCE) for item in sumula)
                and [item["uci"] for item in sumula] == lances):
            return sumula
    from session import descrever  # import tardio: session.py importa este módulo

    nossa_cor = estado["config"]["our_color"] == "w"
    board = chess.Board(estado["fen_inicial"])
    sumula = []
    for uci in lances:
        lance = chess.Move.from_uci(uci)
        nosso = board.turn == nossa_cor
        sumula.append({"uci": uci, "san": board.san(lance), "descricao": descrever(board, lance), "cor": board.turn,
                       "nosso": nosso, "origem": "SEARCH" if nosso else "ADVERSARIO", "tempo": 0.0,
                       "avaliacao": None, "profundidade": 0})
        board.push(lance)
    return sumula


# ==============================================================================
# A partida para retomar (a função das três interfaces)
# ==============================================================================
_RECUPERADAS: dict[tuple, tuple] = {}


def recuperar_partida(arquivo_salvo: str | None = None, pasta_logs: str | None = None) -> dict | None:
    """
    A partida para retomar, no formato do arquivo salvo (o que GameSession.retomar recebe), ou None se a
    última partida terminou ou não há nada para retomar.

    Vale a partida gravada por último: o arquivo salvo conferido com o seu log ou, se ele faltar, estiver
    corrompido ou for de uma partida mais antiga, a remontada pelo log mais recente. Chaves a mais para as
    interfaces: fonte ("salvo" ou "log"), avisos (o que conferir) e gravado_em.
    """
    global erro_da_recuperacao
    arquivo_salvo = arquivo_salvo or ARQUIVO_SALVO
    pasta_logs = pasta_logs or LOGS_DIR
    chave = (os.path.abspath(arquivo_salvo), os.path.abspath(pasta_logs))
    marca = (_marca(arquivo_salvo), tuple((log, _marca(log)) for log in listar_logs(pasta_logs)))
    guardado = _RECUPERADAS.get(chave)
    if guardado is None or guardado[0] != marca:
        try:
            estado, erro_da_recuperacao = _recuperar(arquivo_salvo, pasta_logs), None
        except Exception as exc:  # noqa: BLE001 - arquivo estragado de um jeito imprevisto: a interface abre assim mesmo
            estado, erro_da_recuperacao = None, f"{type(exc).__name__}: {exc}"
        guardado = (marca, estado)
        _RECUPERADAS[chave] = guardado
    return copy.deepcopy(guardado[1])


erro_da_recuperacao: str | None = None  # a linha de comando mostra, se a recuperação falhou por um erro imprevisto


def _recuperar(arquivo_salvo: str, pasta_logs: str) -> dict | None:
    candidatos: list[tuple[float, dict]] = []  # (última gravação, partida)
    salvo, defeito = _ler_salvo(arquivo_salvo)
    log_do_salvo = None
    if salvo is not None:
        log_do_salvo = achar_log(salvo.get("log_path"), pasta_logs)
        candidatos.append((_ultima_gravacao(arquivo_salvo, log_do_salvo), _conferir_com_log(salvo, log_do_salvo)))
    recente = log_mais_recente(pasta_logs)
    if recente is not None and not _mesmo_arquivo(recente, log_do_salvo):
        estado = remontar(ler_log(recente))
        if estado is not None and estado["lances"]:  # log sem lance nenhum: nada a retomar
            if defeito:
                estado["avisos"].insert(0, f"{defeito}: a partida foi remontada pelo log da banca.")
            candidatos.append((_ultima_gravacao(recente), estado))
    if not candidatos:
        return None
    instante, estado = max(candidatos, key=lambda candidato: candidato[0])
    if estado["encerrada"]:
        return None
    estado["gravado_em"] = datetime.fromtimestamp(instante).isoformat(timespec="seconds")
    return estado


def achar_log(caminho: str | None, pasta_logs: str | None = None) -> str | None:
    """
    O log da partida: o de mesmo nome na pasta de logs, onde mora também o arquivo salvo (assim vale mesmo
    se a pasta do projeto foi copiada ou mudou de lugar) ou, se lá não houver, o caminho gravado.
    """
    if not caminho:
        return None
    na_pasta = os.path.join(pasta_logs or LOGS_DIR, re.split(r"[\\/]", caminho)[-1])
    if os.path.isfile(na_pasta):
        return na_pasta
    return caminho if os.path.isfile(caminho) else None


def listar_logs(pasta_logs: str | None = None) -> list[str]:
    """Logs de partida da pasta, do gravado por último para o mais antigo."""
    logs = []
    try:
        with os.scandir(pasta_logs or LOGS_DIR) as entradas:
            for entrada in entradas:
                if entrada.name.startswith("partida_") and entrada.name.endswith(".log"):
                    try:
                        if entrada.is_file():
                            logs.append((entrada.stat().st_mtime_ns, entrada.path))
                    except OSError:
                        pass
    except OSError:
        return []
    return [caminho for _, caminho in sorted(logs, reverse=True)]


def log_mais_recente(pasta_logs: str | None = None) -> str | None:
    """O log de partida de torneio gravado por último (os do modo UCI ficam de fora)."""
    for caminho in listar_logs(pasta_logs):
        if _cor_do_cabecalho(caminho) is not None:
            return caminho
    return None


def _cor_do_cabecalho(caminho: str) -> bool | None:
    try:
        with open(caminho, encoding="utf-8", errors="replace") as f:
            for _, linha in zip(range(12), f):
                m = _CABECALHO_COR.match(linha)
                if m:
                    return _CORES_DO_LOG.get(m.group(1))
    except OSError:
        pass
    return None


def _ultima_gravacao(*caminhos: str | None) -> float:
    instantes = []
    for caminho in caminhos:
        if caminho:
            try:
                instantes.append(os.path.getmtime(caminho))
            except OSError:
                pass
    return max(instantes, default=0.0)


def _mesmo_arquivo(a: str | None, b: str | None) -> bool:
    if not a or not b:
        return False
    try:
        return os.path.samefile(a, b)
    except OSError:
        return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


def _hora(caminho: str) -> str:
    try:
        return datetime.fromtimestamp(os.path.getmtime(caminho)).strftime("%H:%M:%S")
    except OSError:
        return "?"


# ==============================================================================
# Textos para as interfaces
# ==============================================================================
def _quando(iso: str | None) -> str:
    try:
        momento = datetime.fromisoformat(iso) if iso else None
    except ValueError:
        momento = None
    if momento is None:
        return ""
    if momento.date() == datetime.now().date():
        return f"às {momento:%H:%M:%S}"
    return f"em {momento:%d/%m} às {momento:%H:%M}"


def _numero(valor: float) -> str:
    return f"{valor:g}".replace(".", ",")


def resumo_da_partida(dados: dict) -> str:
    """Uma frase para a tela inicial da TUI e do navegador."""
    cor = COR_TEXTO[dados["config"]["our_color"] == "w"]
    texto = f"Há uma partida interrompida: jogando de {cor}, {len(dados['lances'])} meios-lances"
    sumula = dados.get("historico") or []
    if sumula:
        texto += f", último lance {sumula[-1]['uci']} ({'nosso' if sumula[-1]['nosso'] else 'do adversário'})"
    texto += f", gravada {_quando(dados.get('gravado_em')) or 'às ' + str(dados.get('salvo_em', '?'))}."
    if dados.get("fonte") == "log":
        texto += " Remontada pelo log da banca."
    return texto


def detalhes_da_partida(dados: dict) -> list[str]:
    """As linhas que o modo texto e a linha de comando mostram antes de retomar."""
    from session import NOME_DESEMPENHO, Config  # import tardio: session.py importa este módulo

    config = dados["config"]
    nossa_cor = config["our_color"] == "w"
    cfg = Config(our_color=nossa_cor, **{campo: config[campo] for campo in CAMPOS_CONFIG if campo in config})
    motor = (f"Stockfish ({NOME_DESEMPENHO.get(cfg.desempenho, cfg.desempenho)})" if cfg.motor == "stockfish"
             else "motor próprio")
    fonte = "pelo log da banca" if dados.get("fonte") == "log" else "no arquivo salvo, conferido com o log da banca"
    linhas = [
        f"Partida {'terminada' if dados.get('encerrada') else 'interrompida'} encontrada {fonte}.",
        f"Jogamos de {COR_TEXTO[nossa_cor]}, {cfg.descricao_ritmo()}, {motor}, operador de "
        f"{_numero(dados['operador'])} s por lance, pondering {'ligado' if dados.get('ponder') else 'desligado'}.",
    ]
    board = chess.Board(dados["fen_inicial"])
    lances, sumula = dados["lances"], dados.get("historico") or []
    for uci in lances[:-1]:
        board.push_uci(uci)
    if lances:
        ultimo = sumula[-1] if sumula else {"uci": lances[-1], "nosso": board.turn == nossa_cor}
        numero = f"{board.fullmove_number}{'.' if board.turn == chess.WHITE else '...'}"
        lance = chess.Move.from_uci(lances[-1])
        quem = "nosso" if ultimo["nosso"] else "do adversário"
        linhas.append(f"{len(lances)} meios-lances. Último: {numero} {board.san(lance)} ({lances[-1]}), {quem}"
                      + (f": {ultimo['descricao']}." if ultimo.get("descricao") else "."))
        board.push(lance)
        if ultimo["nosso"] and not dados.get("encerrada"):
            linhas.append("Se esse lance ainda não foi anunciado e jogado no tabuleiro físico, faça isso agora.")
    else:
        linhas.append("Nenhum lance jogado ainda.")
    if dados.get("encerrada") and dados.get("resultado"):
        placar, motivo = dados["resultado"]
        linhas.append(f"A partida terminou: {placar} ({motivo}).")
    else:
        vez = "a nossa vez" if board.turn == nossa_cor else "a vez do adversário"
        linhas.append(f"Agora jogam as {COR_TEXTO[board.turn]} ({vez}).")
    quando = _quando(dados.get("gravado_em")) or f"às {dados.get('salvo_em', '?')}"
    linhas.append(f"Relógios na última gravação ({quando}): nosso {TimeManager.format_clock(dados['relogio_nosso'])}"
                  f", adversário {TimeManager.format_clock(dados['relogio_adv'])}."
                  + ("" if dados.get("encerrada") else " Confira os relógios físicos."))
    if dados.get("log_path"):
        linhas.append(f"Log da banca: {dados['log_path']}")
    linhas += [f"Atenção: {aviso}" for aviso in dados.get("avisos") or []]
    return linhas


def tabuleiro_texto(board: chess.Board, nossa_cor: bool) -> str:
    """O tabuleiro em texto, do ponto de vista da nossa cor (maiúsculas são as Brancas)."""
    fileiras = range(7, -1, -1) if nossa_cor == chess.WHITE else range(8)
    colunas = list(range(8)) if nossa_cor == chess.WHITE else list(range(7, -1, -1))
    linhas = []
    for fileira in fileiras:
        casas = []
        for coluna in colunas:
            peca = board.piece_at(chess.square(coluna, fileira))
            casas.append(peca.symbol() if peca else ".")
        linhas.append(f"  {fileira + 1}  " + " ".join(casas))
    linhas.append("     " + " ".join(chess.FILE_NAMES[coluna] for coluna in colunas))
    return "\n".join(linhas)


def pgn_da_partida(dados: dict, inicio: str = "") -> str:
    """A partida em PGN, para conferir com o árbitro ou abrir em outro programa."""
    board = chess.Board(dados["fen_inicial"])
    for uci in dados["lances"]:
        board.push_uci(uci)
    jogo = chess.pgn.Game.from_board(board)
    jogo.headers["Event"] = "III Chess Challenge ICEV 2026"
    nossa, adversario = "Nossa equipe", "Adversário"
    brancas_nossas = dados["config"]["our_color"] == "w"
    jogo.headers["White"], jogo.headers["Black"] = (nossa, adversario) if brancas_nossas else (adversario, nossa)
    data = re.match(r"(\d{2})/(\d{2})/(\d{4})", inicio)
    jogo.headers["Date"] = f"{data.group(3)}.{data.group(2)}.{data.group(1)}" if data else "????.??.??"
    jogo.headers["Result"] = dados["resultado"][0] if dados.get("encerrada") and dados.get("resultado") else "*"
    return str(jogo)


# ==============================================================================
# Linha de comando
# ==============================================================================
def restaurar(estado: dict, arquivo_salvo: str | None = None, pasta_logs: str | None = None) -> str:
    """
    Deixa a partida como partida em andamento, para as interfaces oferecerem Retomar (o arquivo salvo que
    havia vai para partida_salva_anterior.json). Um log de fora de logs/ (pen drive, outro notebook) ganha
    uma cópia em logs/, que é a que continua; o original fica intacto. Devolve o log que a partida vai usar.
    """
    arquivo_salvo = arquivo_salvo or ARQUIVO_SALVO
    pasta_logs = os.path.abspath(pasta_logs or LOGS_DIR)
    os.makedirs(pasta_logs, exist_ok=True)
    log = os.path.abspath(estado["log_path"])
    if os.path.dirname(log) != pasta_logs:
        base, extensao = os.path.splitext(os.path.join(pasta_logs, os.path.basename(log)))
        destino, n = base + extensao, 1
        while os.path.exists(destino) and not filecmp.cmp(destino, log, shallow=False):
            destino = f"{base}_recuperado{'' if n == 1 else f'_{n}'}{extensao}"
            n += 1
        if not os.path.exists(destino):
            shutil.copyfile(log, destino)
        log = destino
    dados = {chave: valor for chave, valor in estado.items() if chave not in CHAVES_DA_RECUPERACAO}
    dados.update(log_path=log, salvo_em=datetime.now().strftime("%H:%M:%S"))
    if os.path.exists(arquivo_salvo):
        os.replace(arquivo_salvo, os.path.join(os.path.dirname(arquivo_salvo),
                                               os.path.basename(ARQUIVO_SALVO_ANTERIOR)))
    gravar_json(arquivo_salvo, dados)
    return log


def descrever_logs(arquivo_salvo: str | None = None, pasta_logs: str | None = None) -> list[str]:
    """Uma linha por log de logs/, do mais recente ao mais antigo, com o estado da partida."""
    oferecida = recuperar_partida(arquivo_salvo, pasta_logs)
    linhas = []
    for caminho in listar_logs(pasta_logs):
        nome = os.path.basename(caminho)
        lido = ler_log(caminho)
        estado = remontar(lido)
        if estado is None:
            linhas.append(f"{nome}  {lido.erro or 'modo UCI ou sem cabeçalho: não é partida de torneio'}")
            continue
        if estado["encerrada"]:
            situacao = f"terminou: {estado['resultado'][0]} ({estado['resultado'][1]})"
        else:
            situacao = "interrompida" if estado["lances"] else "sem lances"
        if oferecida and _mesmo_arquivo(caminho, oferecida.get("log_path")):
            situacao += "  <- a que as interfaces oferecem para retomar"
        linhas.append(f"{nome}  {lido.inicio or '?':19}  {COR_TEXTO[lido.cor_equipe]:7}  "
                      f"{len(estado['lances']):>3} meios-lances  {situacao}")
    return linhas


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except (OSError, ValueError):
            pass
    parser = argparse.ArgumentParser(
        description="Recupera a partida interrompida (o notebook travou, desligou ou a janela fechou).",
        epilog="Para retomar, abra python tui.py, python web.py ou python main.py: elas usam esta mesma recuperação.",
    )
    parser.add_argument("--log", metavar="ARQUIVO",
                        help="remonta a partida deste log da banca (pode ser uma cópia de outro notebook)")
    parser.add_argument("--listar", action="store_true", help="lista os logs de logs/ e o estado de cada partida")
    parser.add_argument("--restaurar", action="store_true",
                        help="grava a partida como partida em andamento, para as interfaces retomarem")
    parser.add_argument("--pgn", action="store_true", help="mostra também a partida em PGN")
    args = parser.parse_args(argv)

    if args.listar:
        linhas = descrever_logs()
        print("\n".join(linhas) if linhas else f"Nenhum log de partida em {LOGS_DIR}.")
        return 0
    if args.log:
        lido = ler_log(args.log)
        estado = remontar(lido)
        if estado is None:
            print(lido.erro or "Este log não é de partida de torneio (modo UCI ou sem cabeçalho): não dá para remontar.")
            return 1
    else:
        lido = None
        estado = recuperar_partida()
        if estado is None and erro_da_recuperacao:
            print(f"A recuperação parou num erro imprevisto ({erro_da_recuperacao}).\n"
                  "Veja os logs com --listar e remonte um deles com --log ARQUIVO.")
            return 1
        if estado is None:
            print("Nenhuma partida interrompida: a última partida terminou ou não há partida gravada.\n"
                  "Para ver os logs: python recuperacao.py --listar")
            return 0
    print("\n".join(detalhes_da_partida(estado)))
    board = chess.Board(estado["fen_inicial"])
    lances = [chess.Move.from_uci(uci) for uci in estado["lances"]]
    sumula = board.variation_san(lances) if lances else "(nenhum)"
    for lance in lances:
        board.push(lance)
    print(f"\n{tabuleiro_texto(board, estado['config']['our_color'] == 'w')}")
    print(f"\nFEN: {board.fen()}")
    print(f"Lances: {sumula}")
    if args.pgn:
        if lido is None and estado.get("log_path"):
            lido = ler_log(estado["log_path"])
        print(f"\n{pgn_da_partida(estado, lido.inicio if lido else '')}")
    if estado["encerrada"]:
        print("\nEsta partida terminou: não há o que retomar.")
        return 1 if args.restaurar else 0
    if args.restaurar:
        log = restaurar(estado)
        print(f"\nPronto: ficou como a partida em andamento, continuando o log {log}.\n"
              "Abra python tui.py, python web.py ou python main.py e escolha Retomar.")
    elif args.log:
        print("\nPara as interfaces retomarem esta partida, rode de novo com --restaurar.")
    else:
        print("\nPara retomar: abra python tui.py, python web.py ou python main.py e escolha Retomar.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
