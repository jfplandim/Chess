"""
Baixa as Tablebases Syzygy de 3, 4 e 5 peças (FAQ Item 4: permitidas até 5 peças)
Chess Challenge III - ICEV 2026

RODE ANTES DO TORNEIO (com internet). No dia, o motor usa os arquivos localmente,
100% offline, a partir da pasta  syzygy/  na raiz do projeto.

    python tools/baixar_syzygy.py            # WDL + DTZ (~940 MB) - recomendado
    python tools/baixar_syzygy.py --so-wdl   # só WDL (~380 MB): busca usa, raiz usa a busca

WDL (.rtbw) = vitória/empate/derrota  -> usado DENTRO da busca
DTZ (.rtbz) = distância até zerar a contagem de 50 lances -> escolhe o lance na raiz
"""

import argparse
import os
import re
import sys
import urllib.request

MIRROR = "https://tablebase.lichess.ovh/tables/standard"
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def listar(pasta: str) -> list[tuple[str, int]]:
    """Lê a listagem do espelho e devolve [(nome_arquivo, tamanho_em_bytes)]."""
    with urllib.request.urlopen(f"{MIRROR}/{pasta}/", timeout=60) as resp:
        html = resp.read().decode("utf-8", "replace")
    itens = []
    for m in re.finditer(r'href="([^"]+\.rtb[wz])">[^<]*</a>\s+\S+\s+\S+\s+(\d+)', html):
        itens.append((m.group(1), int(m.group(2))))
    return itens


def baixar(pasta: str, nome: str, tamanho: int, destino: str) -> None:
    alvo = os.path.join(destino, nome)
    if os.path.isfile(alvo) and os.path.getsize(alvo) == tamanho:
        return  # já baixado
    tmp = alvo + ".part"
    with urllib.request.urlopen(f"{MIRROR}/{pasta}/{nome}", timeout=120) as resp, open(tmp, "wb") as out:
        while True:
            bloco = resp.read(1 << 20)
            if not bloco:
                break
            out.write(bloco)
    if os.path.getsize(tmp) != tamanho:
        os.remove(tmp)
        raise IOError(f"tamanho incorreto em {nome}")
    os.replace(tmp, alvo)


def main() -> None:
    ap = argparse.ArgumentParser(description="Baixa Syzygy 3-4-5 peças para a pasta syzygy/")
    ap.add_argument("--so-wdl", action="store_true", help="baixa apenas os arquivos WDL")
    ap.add_argument("--destino", default=os.path.join(BASE_DIR, "syzygy"))
    args = ap.parse_args()

    os.makedirs(args.destino, exist_ok=True)
    pastas = ["3-4-5-wdl"] if args.so_wdl else ["3-4-5-wdl", "3-4-5-dtz"]
    for pasta in pastas:
        itens = listar(pasta)
        total = sum(t for _, t in itens)
        print(f"{pasta}: {len(itens)} arquivos, {total / 1e6:.0f} MB")
        feito = 0
        for i, (nome, tam) in enumerate(itens, 1):
            for tentativa in range(3):
                try:
                    baixar(pasta, nome, tam, args.destino)
                    break
                except Exception as exc:
                    print(f"  erro em {nome} ({exc}), tentativa {tentativa + 1}/3")
            else:
                print(f"  FALHOU: {nome}")
            feito += tam
            sys.stdout.write(f"\r  [{i:3d}/{len(itens)}] {feito / 1e6:7.0f}/{total / 1e6:.0f} MB  {nome:20s}")
            sys.stdout.flush()
        print()
    n = len([f for f in os.listdir(args.destino) if f.endswith((".rtbw", ".rtbz"))])
    print(f"Pronto: {n} arquivos em {args.destino}")
    print("Teste: python test_engine.py  (o motor detecta a pasta syzygy/ sozinho)")


if __name__ == "__main__":
    main()
