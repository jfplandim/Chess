"""
Mede a velocidade do Stockfish neste computador, com a configuração do torneio (threads e tabela).
Chess Challenge III - ICEV 2026

Rode nos notebooks da equipe e leve o mais rápido ao torneio: o Stockfish não usa GPU, então o que
conta é o processador. Com o mesmo tempo de reflexão, mais nós por segundo = o Stockfish enxerga
mais fundo = mais força.

    python tools/bench_stockfish.py              # desempenho Ultra (força total); cerca de 45 s num notebook comum
    python tools/bench_stockfish.py --desempenho medio  # ou baixo: os outros níveis da tela inicial
    python tools/bench_stockfish.py --profundidade 18   # mais longo e mais estável

Ligue o notebook na tomada e feche os outros programas antes de medir.
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from motor_stockfish import (  # noqa: E402
    COMANDO_INSTALAR,
    NIVEIS_DESEMPENHO,
    NOME_DESEMPENHO,
    arquitetura_stockfish,
    avisos_de_energia,
    encontrar_stockfish,
    fmt_threads,
    memoria_total_mb,
    recursos_do_nivel,
    versao_stockfish,
)


def main() -> int:
    analisador = argparse.ArgumentParser(description="Velocidade do Stockfish com a configuração do torneio.")
    analisador.add_argument("--profundidade", type=int, default=16, help="profundidade do bench (padrão 16)")
    analisador.add_argument("--desempenho", choices=NIVEIS_DESEMPENHO, default="ultra",
                            help="nível da tela inicial (padrão ultra, a força total)")
    args = analisador.parse_args()

    caminho = encontrar_stockfish()
    versao = versao_stockfish(caminho) if caminho else None
    if not versao:
        raise SystemExit(f"Stockfish não encontrado ou não abre neste computador. Instale com: {COMANDO_INSTALAR}")
    threads, tabela = recursos_do_nivel(args.desempenho)
    ram = memoria_total_mb()
    print(f"{versao}, versão {arquitetura_stockfish(caminho) or '?'}")
    print(f"Desempenho {NOME_DESEMPENHO[args.desempenho]} neste computador: {fmt_threads(threads)}, {tabela} MB de tabela"
          + (f" (RAM de {ram / 1024:.1f} GB)" if ram else ""))
    for aviso in avisos_de_energia():
        print(f"Atenção: {aviso}")
    print(f"Medindo (bench do Stockfish, profundidade {args.profundidade})...", flush=True)

    comando = f"bench {tabela} {threads} {args.profundidade} default depth\nquit\n"
    saida = subprocess.run([caminho], input=comando, capture_output=True, text=True, timeout=900)
    texto = saida.stdout + saida.stderr
    nps = re.search(r"Nodes/second\s*:\s*(\d+)", texto)
    total = re.search(r"Total time \(ms\)\s*:\s*(\d+)", texto)
    if not nps:
        raise SystemExit("O bench não terminou como esperado:\n" + texto[-800:])
    velocidade = int(nps.group(1))
    print(f"\nVelocidade: {velocidade / 1e6:.2f} milhões de nós por segundo"
          + (f" (bench em {int(total.group(1)) / 1000:.0f} s)" if total else ""))
    print("Compare esse número entre os notebooks da equipe: no torneio, use o que tiver o maior.")
    print("Referência: o notebook de testes da equipe (Core i3-1005G1, 4 threads, 2048 MB) fez 1,9 milhão.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
