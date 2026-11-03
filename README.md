# Motor de Xadrez — Chess Challenge III (ICEV 2026)

Motor de xadrez desenvolvido integralmente em Python para o **III Chess Challenge — ICEV 2026**, utilizando a biblioteca `python-chess` exclusivamente para a camada de representação de tabuleiro e geração de lances legais (conforme permitido pelo **Item 14 do FAQ Oficial**) e implementando com **100% de autoria própria** todos os três componentes decisórios exigidos pelo **Item 2 do FAQ**:

1. **Função de Avaliação Heurística (`evaluation.py`)**
2. **Método de Busca e Seleção de Lances (`search.py`)**
3. **Gestão Dinâmica de Tempo (`time_manager.py`)**

---

## Como Instalar e Executar

### 1. Instalar a única dependência (antes do torneio, com internet ligada)
```bash
pip install -r requirements.txt
```

### 2. Testar e Validar o Motor
```bash
python test_engine.py
```

### 3. Iniciar a Interface Oficial de Partida (Dia do Torneio - 100% Offline)
```bash
python main.py
```

---

## Arquitetura do Projeto e Autoria Técnica (Guia para a Arguição da Banca)

| Arquivo | Responsabilidade | Destaques Técnicos |
| :--- | :--- | :--- |
| `evaluation.py` | **Função de Avaliação (Heurística)** | *Tapered Evaluation* (PeSTO) interpolando Meio-Jogo (MG) e Final de Jogo (EG); Máscaras de Bitboard de 64 bits pré-calculadas para Peões Passados, Isolados e Dobrados; Bônus de Par de Bispos; Torres em Colunas Abertas/Semi-Abertas; Escudo de Peões do Rei e *Mop-Up Evaluation* para forçar mate rápido em finais ganhos. |
| `search.py` | **Busca e Seleção de Lances** | *Iterative Deepening* (Aprofundamento Iterativo) + *Negamax com Poda Alpha-Beta* + *Principal Variation Search (PVS)* + *Tabela de Transposição* (Zobrist Hashing com flags `EXACT`, `LOWERBOUND`, `UPPERBOUND`) + *Quiescence Search* com *Delta Pruning* (evita Efeito Horizonte) + *Move Ordering* (`TT Move` -> Promoções -> Capturas `MVV-LVA` -> `Killer Moves` -> `History Heuristic`) + *Null Move Pruning* e *Late Move Reductions (LMR)*. |
| `time_manager.py` | **Gestão Dinâmica de Tempo** | Orçamento adaptativo para `15m+10s` (FAQ Item 7) ou `25m+0s` (Edital) e *Armageddon* (FAQ Item 12); calcula `soft_limit` e `hard_limit` conforme a fase da partida e complexidade; estende o tempo se a avaliação cair (*score drop*) ou se o melhor lance mudar na última profundidade, preservando saldo de relógio para o desempate da Melhor de 3. |
| `book_and_tb.py` | **Opening Book e Tablebases** | Repertório de Grandes Mestres embutido + suporte a livros Polyglot `.bin` na pasta `books/` com **trava estrita de 10 meios-lances (5 lances de cada cor)** (FAQ Item 3); integração com *Syzygy Tablebases* na pasta `syzygy/` restrita a **até 5 peças** (FAQ Item 4). |
| `logger.py` | **Log Obrigatório da Banca** | Grava automaticamente a cada lance em `logs/partida_YYYYMMDD_HHMMSS.log` e `logs/ultima_partida_banca.txt` todos os 8 itens exigidos pelo **Item 9 do FAQ**: número do lance, cor, lance em UCI, profundidade atingida, nós avaliados, avaliação da posição, variante principal (PV) e tempo gasto. |
| `main.py` | **Operação Presencial + Pondering** | Painel interativo de linha de comando seguindo os 4 passos do **Item 10 do FAQ** (anúncio em voz alta em UCI), com *Pondering* em background no turno adversário (**Item 13 do FAQ**) e comandos anti-falha (`undo`, `tempo`, `fen`, `forcar`). |
| `uci.py` | **Protocolo UCI Padrão** | Permite conectar o motor em interfaces gráficas como Arena, CuteChess ou EnCroissant. |

---

## Resumo Rápido para Responder à Banca na Arguição Técnica

1. **"Como funciona a busca do motor de vocês?"**
   - Usamos **Iterative Deepening** combinado com **Negamax e Poda Alpha-Beta (PVS)**. Em vez de buscar em uma profundidade fixa e correr risco do tempo acabar no meio, calculamos profundidade 1, 2, 3, 4... A cada nova profundidade, usamos o melhor lance da profundidade anterior e a **Tabela de Transposição (Zobrist Hash)** para ordenar os melhores lances primeiro, gerando muito mais cortes na árvore.
2. **"Como vocês evitam que o motor pare de calcular bem no meio de uma troca de peças (Efeito Horizonte)?"**
   - Quando a profundidade chega a `0`, não chamamos a avaliação estática direto. Chamamos a **Busca de Quiescência (`_quiescence`)**, que continua avaliando apenas **capturas, promoções e saídas de xeque** (usando *Stand-Pat* e *Delta Pruning*) até que a posição fique "calma".
3. **"Como vocês ordenam os lances antes de buscar?"**
   - Na função `_order_moves`: 1º o lance da Tabela de Transposição (`tt_move`), 2º Promoções, 3º Capturas ordenadas por **MVV-LVA** (*Most Valuable Victim - Least Valuable Attacker*, ex: Peão captura Dama é testado antes de Dama captura Peão), 4º **Killer Moves** (dois lances quietos por nível que causaram corte Beta recentemente) e 5º **History Heuristic**.
4. **"Como funciona a Função de Avaliação de vocês?"**
   - Implementamos **Tapered Evaluation (PeSTO)** com operações de **Bitboards**. Cada peça tem duas tabelas posicionais: uma para o **Meio-Jogo (MG)** e outra para o **Final (EG)**. Conforme peças são trocadas, a variável `phase` (de 24 até 0) interpola suavemente a nota. Isso faz o Rei se esconder atrás dos peões no meio-jogo e ir para o centro atacar no final! Além disso, avaliamos Par de Bispos, Peões Passados/Isolados/Dobrados, Torres em Colunas Abertas e *Mop-Up* para dar xeque-mate rápido no final.
5. **"Como funciona a Gestão de Tempo?"**
   - No `TimeManager`, calculamos dois limites por lance: `soft_limit` (meta ideal para não iniciar uma nova profundidade) e `hard_limit` (teto máximo que interrompe a busca imediatamente via exceção `SearchTimeout`). Também monitoramos a estabilidade: se a nota cair mais de 35 centipawns ou se o melhor lance mudar na última profundidade, alocamos mais tempo para evitar erros táticos.
