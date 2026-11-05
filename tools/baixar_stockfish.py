"""
Baixa o Stockfish oficial (programa de terceiros, licença GPLv3) para a pasta stockfish/ ao lado do projeto.
Chess Challenge III - ICEV 2026

RODE ANTES DO TORNEIO (com internet). No dia, o motor_stockfish.py usa o executável local, sem rede
(FAQ item 14). O Stockfish não vai para o repositório: é uma dependência declarada no credenciamento
(FAQ itens 1 e 14), como o python-chess no requirements.txt.

    python tools/baixar_stockfish.py              # Stockfish 19, a versão declarada
    python tools/baixar_stockfish.py --forcar     # baixa de novo mesmo se já estiver instalado
    python tools/baixar_stockfish.py --destino X  # instala em outra pasta (aponte STOCKFISH_PATH para o executável)

O arquivo baixado é conferido pelo SHA-256 que o GitHub publica para cada arquivo da versão.
Funciona em Linux, Windows e macOS (x86-64 ou ARM): o build "universal" escolhe sozinho a versão
mais rápida para o processador.
"""
from __future__ import annotations

import argparse
import glob
import hashlib
import json
import os
import platform
import stat
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
import zipfile

VERSAO = "sf_19"
API = "https://api.github.com/repos/official-stockfish/Stockfish/releases/tags/{}"
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DESTINO_PADRAO = os.path.normpath(os.path.join(BASE_DIR, "..", "stockfish"))
AGENTE = "chess-challenger-baixar-stockfish"


def nome_do_arquivo() -> str:
    """Nome do arquivo da versão para este sistema e processador."""
    sistema = platform.system().lower()
    arm = platform.machine().lower() in ("aarch64", "arm64")
    if sistema == "linux":
        return f"stockfish-linux-{'arm64' if arm else 'x86-64'}-universal.tar.gz"
    if sistema == "windows":
        return f"stockfish-windows-{'arm64' if arm else 'x86-64'}-universal.zip"
    if sistema == "darwin":
        return "stockfish-macos-universal.tar.gz"
    raise SystemExit(f"Sistema não suportado: {platform.system()} {platform.machine()}")


def ler_json(url: str) -> dict:
    pedido = urllib.request.Request(url, headers={"User-Agent": AGENTE, "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(pedido, timeout=60) as resposta:
        return json.loads(resposta.read().decode())


def baixar(url: str, destino: str, tamanho: int) -> str:
    """Baixa mostrando o progresso e devolve o SHA-256 do arquivo."""
    soma = hashlib.sha256()
    recebido = 0
    pedido = urllib.request.Request(url, headers={"User-Agent": AGENTE})
    with urllib.request.urlopen(pedido, timeout=120) as resposta, open(destino, "wb") as saida:
        while bloco := resposta.read(1 << 20):
            saida.write(bloco)
            soma.update(bloco)
            recebido += len(bloco)
            print(f"\r  {recebido / 1e6:6.1f} de {tamanho / 1e6:.1f} MB", end="", flush=True)
    print()
    return soma.hexdigest()


def executavel(pasta: str) -> str | None:
    """O executável dentro da pasta extraída (stockfish/stockfish-<sistema>-...)."""
    for caminho in sorted(glob.glob(os.path.join(pasta, "stockfish", "stockfish*"))):
        if os.path.isfile(caminho) and not caminho.endswith((".txt", ".md")):
            return caminho
    return None


def versao_do(caminho: str) -> str | None:
    """Abre o Stockfish e devolve o 'id name' (prova de que ele roda neste processador)."""
    try:
        saida = subprocess.run([caminho], input="uci\nquit\n", capture_output=True, text=True, timeout=30).stdout
    except (OSError, subprocess.TimeoutExpired):
        return None
    for linha in saida.splitlines():
        if linha.startswith("id name "):
            return linha[len("id name "):].strip()
    return None


def main() -> int:
    analisador = argparse.ArgumentParser(description="Baixa o Stockfish oficial para a pasta stockfish/ ao lado do projeto.")
    analisador.add_argument("--versao", default=VERSAO, help=f"marca da versão no GitHub (padrão {VERSAO})")
    analisador.add_argument("--destino", default=DESTINO_PADRAO, help=f"pasta de instalação (padrão {DESTINO_PADRAO})")
    analisador.add_argument("--forcar", action="store_true", help="baixa de novo mesmo se já estiver instalado")
    args = analisador.parse_args()
    destino = os.path.abspath(args.destino)

    atual = executavel(destino)
    if atual and not args.forcar:
        versao = versao_do(atual)
        if versao:
            print(f"Já instalado: {versao}\n  {atual}\nPara baixar de novo: --forcar")
            return 0

    nome = nome_do_arquivo()
    print(f"Procurando {nome} na versão {args.versao} do Stockfish oficial...")
    dados = ler_json(API.format(args.versao))
    arquivo = next((a for a in dados.get("assets", []) if a["name"] == nome), None)
    if arquivo is None:
        disponiveis = ", ".join(a["name"] for a in dados.get("assets", []))
        raise SystemExit(f"Arquivo {nome} não existe nessa versão. Disponíveis: {disponiveis}")
    esperado = (arquivo.get("digest") or "").removeprefix("sha256:")

    os.makedirs(destino, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=destino) as tmp:
        pacote = os.path.join(tmp, nome)
        print(f"Baixando {dados.get('name', args.versao)} ({arquivo['size'] / 1e6:.0f} MB)...")
        obtido = baixar(arquivo["browser_download_url"], pacote, arquivo["size"])
        if esperado and obtido != esperado:
            raise SystemExit(f"SHA-256 não confere (esperado {esperado}, veio {obtido}). Nada foi instalado.")
        print("SHA-256 confere com o publicado no GitHub." if esperado else "Aviso: o GitHub não publicou o SHA-256.")
        if nome.endswith(".zip"):
            with zipfile.ZipFile(pacote) as zz:
                zz.extractall(destino)  # o zipfile descarta caminhos absolutos e "..": nada sai da pasta
        else:
            with tarfile.open(pacote) as tt:
                tt.extractall(destino, filter="data")  # filtro "data": nada sai da pasta de destino

    caminho = executavel(destino)
    if caminho is None:
        raise SystemExit(f"Não achei o executável depois de extrair em {destino}.")
    if os.name != "nt":
        os.chmod(caminho, os.stat(caminho).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    versao = versao_do(caminho)
    if not versao:
        raise SystemExit(f"O executável não abriu neste computador: {caminho}")
    print(f"Pronto: {versao}\n  {caminho}")
    if destino != DESTINO_PADRAO:
        print(f"Fora da pasta padrão: antes de jogar, defina STOCKFISH_PATH={caminho}")
    print("Confira com: python test_engine.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
