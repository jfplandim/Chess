# Motor de Xadrez — Chess Challenge III (ICEV 2026)

Motor de xadrez para o **III Chess Challenge — ICEV 2026**.

- **Motor do torneio:** o **Stockfish 19** faz a busca e a avaliação (projeto-base declarado,
  permitido pelo **Item 1 do FAQ**). A **gestão de tempo é da equipe** (`time_manager.py`,
  componente de autoria própria do **Item 2**): é ela que decide quanto o Stockfish pensa em cada
  lance e quando ele para.
- **Motor próprio (reserva):** o motor da equipe em Python, com avaliação (`evaluation.py`),
  busca (`search.py`) e gestão de tempo (`time_manager.py`) de autoria própria. Escolhido na tela
  inicial (tecla M), caso o Stockfish não esteja disponível.
- A biblioteca `python-chess` só representa o tabuleiro e gera lances legais (**Item 14**).

**Documentação completa: <https://jfplandim.github.io/Chess/>**. A mesma página está em
[`docs/index.html`](docs/index.html), que abre no navegador sem internet, e em
`http://127.0.0.1:8765/documentacao` com o `python web.py` rodando. Tem instalação, uso durante a
partida, arquitetura, o Stockfish com a nossa gestão de tempo, o motor próprio, conformidade com o
FAQ e o guia da arguição.

---

## Declaração para o credenciamento (FAQ Item 1)

| Item | Declaração |
| :--- | :--- |
| Projeto-base | **Stockfish 19** (versão oficial `sf_19`), na busca e na avaliação do motor do torneio; e [`jfplandim/Chess`](https://github.com/jfplandim/Chess), commit `ccee6c7`, para o motor próprio e as interfaces |
| Autoria própria (Item 2) | **Gestão de tempo** (`time_manager.py`), que controla o Stockfish por meio de `motor_stockfish.py`. No motor próprio (reserva), também a avaliação (`evaluation.py`) e a busca (`search.py`) |
| Licença do Stockfish (Item 1) | GPLv3. Roda como programa separado, conversando por UCI, sem nenhuma alteração no código dele. Não está no repositório: é baixado por `tools/baixar_stockfish.py`. Código-fonte: [official-stockfish/Stockfish, tag `sf_19`](https://github.com/official-stockfish/Stockfish/tree/sf_19) |
| Bibliotecas (Item 14) | `chess` 1.11.2 (python-chess), só para tabuleiro e lances legais; `textual` 8.2.8, só para desenhar a interface. Ambas em `requirements.txt`. Programa externo: Stockfish 19 |
| Livro (Item 3) | Linhas clássicas de abertura embutidas em `book_and_tb.py`, até 10 meios-lances, usadas pelos dois motores |
| Tablebases (Item 4) | Syzygy de até 5 peças, baixadas antes do evento com `tools/baixar_syzygy.py`. O Stockfish recebe `SyzygyProbeLimit 5` |
| Tabelas da avaliação | Valores públicos do PeSTO como ponto de partida do motor próprio (seção 10 do guia da banca) |
| Hardware (Itens 5 a 8) | Notebook da equipe, só CPU. O Stockfish usa todos os threads do processador e uma tabela de transposição proporcional à RAM (no notebook de testes: 4 threads e 2048 MB). Sem GPU: o Stockfish não usa |
| Modelos (Item 15) | A rede neural (NNUE) que vem dentro do Stockfish 19: peso de terceiros, não conta como autoria. Nenhum peso treinado pela equipe |
| Rede (Item 14) | Nenhuma requisição externa durante as partidas |

Stockfish, a rede neural dele, livro, tablebases e tabelas PeSTO são de terceiros e não contam como
autoria da equipe.

---

## Motor do torneio: Stockfish com a nossa gestão de tempo (medido)

| Métrica | Resultado |
| :--- | :---: |
| Velocidade do Stockfish 19 no notebook da equipe (4 threads, 2048 MB, `tools/bench_stockfish.py`) | **1,9 milhão de nós/s** (o motor próprio faz 13 a 22 mil) |
| Nossa gestão de tempo x a do próprio Stockfish (o mesmo Stockfish dos dois lados, 12 partidas, 40 s + 0,4 s, 0,16 s de operador descontado por lance dos dois lados) | **+0 =12 −0** |
| Tempo médio por lance | **0,74 s** (nosso) x 1,14 s (Stockfish): 34% menos |
| Menor relógio em todo o match | **19,1 s** (nosso) x 1,5 s (Stockfish) |
| Ensaio no ritmo real, 15+10, pela sessão do torneio, contra o Stockfish puro com a gestão de tempo dele. Operador real de 5 s por lance (o motor configurado com 4 s) e F3 a cada 10 lances | empate por tripla repetição em 29 lances |
| Relógio físico no fim desse ensaio | **10:23** (nosso) x 3:46 (Stockfish); a calibração subiu o operador para 5,0 s no 10º lance |
| `python test_engine.py`, teste 12 (livro, limite rígido, táticas, lances legais, ponder, reabertura, queda e travamento no meio da busca) | passa |
| Simulação de torneio da véspera (`tools/simular_torneio.py`, 136 partidas pela sessão das interfaces, 6.222 lances nossos) | **0 exceções** e 0 lances de emergência; 8/8 vitórias por mate contra motores mais fracos |
| Crédito do ponder + empate morto, espelho contra o Stockfish com a gestão de tempo dele (6 partidas, 90 s + 1 s, ponder ligado) | **+0 =6 −0**; relógio no fim **+70,5 s** a nosso favor (antes: +24,4 s); 0,71 s por lance (antes: 1,48 s) |
| Lance com crédito do ponder x busca normal, 24 posições, referência Stockfish 8 s | perda média de 1,2 cp (normal: 2,7 cp) com 0,43 s em vez de 2,31 s |

Entre dois Stockfish quase tudo empata, e o confronto empatado se decide pela soma do tempo restante
(FAQ Item 12). Doze partidas não medem pequenas diferenças de força, mas mostram que economizar
relógio não custou nenhum resultado. Comando para repetir na seção *Ferramentas de teste*.

---

## Motor próprio: resultado da versão 2 (medido)

| Métrica | Versão anterior | Versão atual |
| :--- | :---: | :---: |
| Match direto (40 partidas, 20s+0,2s, mesma gestão de tempo nos dois) | — | **+38 =2 −0** |
| Profundidade em 5 s (posições de meio-jogo) | 5 – 8 | **12 – 17** |
| Velocidade (nós/s, mesmo notebook) | ~15 mil | ~19 mil |
| Avaliação estática | ~16–20 µs | ~10–12 µs (com muito mais conhecimento) |
| Perde por tempo em ritmo rápido | sim (mínimo de 1 s/lance) | não |
| Ritmo do torneio simulado (3+2 com 0,8 s de operador ≈ 15+10 com 4 s) | — | nunca estourou o relógio |

Cada mudança só entrou depois de vencer um match contra a versão anterior. Exemplos:

| Experimento | Resultado | Decisão |
| :--- | :--- | :--- |
| Termos de ameaça (peça atacada por peão / pendurada) | +42 =31 −27 (**+52 Elo**, LOS 96,5%) | aceito |
| Pesos ajustados por Texel Tuning com partidas da CCRL | +6 =4 −17 (**−150 Elo**) | **rejeitado** |
| Sacrifício com xeque sem redução (LMR) | WAC 197 → 200 de 300; match +26 =17 −17 | aceito |

Como reproduzir: `python tools/match.py --help` (veja a seção *Ferramentas*).

---

## Como instalar e executar

### 1. Instalar as dependências (antes do torneio, com internet)
Requer **Python 3.10 ou superior** (testado no 3.14; o CPython é mais rápido que o PyPy
para o motor próprio, pois os bitboards de 64 bits viram inteiros grandes no PyPy).
```bash
pip install -r requirements.txt
```

### 2. Baixar o Stockfish 19 (antes do torneio, com internet)
```bash
python tools/baixar_stockfish.py       # ~80 MB, instala em ../stockfish/ (fora do repositório)
```
Baixa a versão oficial para o sistema (Linux, Windows ou macOS), confere o SHA-256 publicado no
GitHub e testa o executável. O build "universal" escolhe sozinho a versão mais rápida para o
processador. Em outra pasta, aponte a variável `STOCKFISH_PATH` para o executável.

**Força total em qualquer computador.** O Stockfish se ajusta sozinho ao hardware:

- **Processador:** o build "universal" escolhe a versão mais rápida que o processador aceita
  (AVX2, AVX-512, VNNI...).
- **Threads:** usa todos os threads que o programa pode usar.
- **Tabela de transposição:** cresce com a RAM: 512 MB abaixo de 7 GB, 1024 MB até 14 GB, 2048 MB
  até 30 GB e 4096 MB acima disso. Dá para forçar outros valores com `STOCKFISH_THREADS` e
  `STOCKFISH_HASH`.
- **Força:** nenhuma opção limita a força.
- **GPU:** não faz diferença. O Stockfish roda no processador, e a rede neural dele (NNUE) foi
  feita para a CPU. O que conta é o processador e a energia: na bateria ou em modo de economia ele
  fica bem mais lento, e a abertura das interfaces avisa.

**Desempenho: Ultra, Médio ou Baixo.** Todas as interfaces deixam escolher quanto do computador o
Stockfish usa (tecla D na tela inicial do navegador e da TUI, uma pergunta no `main.py`, opção
`Desempenho` no `uci.py`). O nível fica salvo com a partida e aparece no topo da tela e no log da banca.

| Nível | Threads | Tabela de transposição | No notebook de testes |
| :--- | :--- | :--- | :--- |
| **Ultra** (padrão, força total) | todos | pela RAM | 4 threads, 2048 MB |
| **Médio** | metade | metade | 2 threads, 1024 MB |
| **Baixo** | 1 | 256 MB | 1 thread, 256 MB |

No torneio, use o **Ultra**. Médio e Baixo servem para um notebook fraco, na bateria ou com outros
programas abertos, e para testes. O motor próprio usa um processador só, então o desempenho não
vale para ele.

Para escolher o notebook do torneio, rode nos computadores da equipe e leve o que tiver o maior
número de nós por segundo:

```bash
python tools/bench_stockfish.py        # cerca de 30 a 45 s; 1,9 milhão de nós/s no notebook de testes
python tools/bench_stockfish.py --desempenho medio   # mede os outros níveis
```

Não precisa decorar: ao abrir, `python web.py`, `python tui.py` e `python main.py` conferem se o
Stockfish está instalado e roda neste computador. Se não estiver, avisam e perguntam
"Instalar o Stockfish 19 agora? [S/n]": Enter instala (com internet) e a partida já começa com ele;
`n` segue com o motor próprio. Nada acessa a internet sem essa resposta.

### 3. (Recomendado) Baixar as Tablebases Syzygy de até 5 peças (FAQ Item 4)
```bash
python tools/baixar_syzygy.py          # ~940 MB, cria a pasta syzygy/
```
Sem a pasta `syzygy/` os dois motores funcionam normalmente (só não usam as tablebases).

### 4. Validar o motor (rode antes de cada partida)
```bash
python test_engine.py
```
São 14 testes:
- **12º:** o Stockfish com a nossa gestão de tempo: livro, limite rígido, táticas, lances legais,
  pondering e o que acontece se o processo cair ou travar no meio da busca.
- **13º:** a leitura do lance digitado pelo operador.
- **14º:** a recalibração do custo do operador pelo relógio físico.

### 5. Partida oficial (100% offline)
```bash
python web.py          # interface no navegador (gráfica)
python tui.py          # a mesma interface no terminal
python main.py         # modo texto, se nenhuma das duas abrir
```

---

## Interface no navegador (`web.py`)

A interface de torneio em versão gráfica: `python web.py` abre `http://127.0.0.1:8765` no
navegador; o terminal só mostra a conferência do Stockfish e esse endereço. Tem as mesmas fases, teclas e regras da TUI
(descrita abaixo), com peças desenhadas, tabuleiro clicável e botões para todos os comandos.

- **Só nesta máquina:** o servidor escuta apenas em `127.0.0.1` e a página não carrega nada da
  internet (FAQ Item 14). Um código gerado a cada execução impede que outra página do
  navegador mande comandos para a partida.
- **A partida mora no `web.py`, não na aba:** fechar ou recarregar a página não perde nada;
  é só abrir o endereço de novo. O F5 desta página corrige a posição em vez de recarregar.
- **Para desligar:** Encerrar > Sair e retomar depois, ou Ctrl+C no terminal. A partida fica
  salva no mesmo arquivo da TUI: uma interface retoma a partida da outra.
- **Mesmo motor e mesmo log:** a lógica está em `session.py`, compartilhada com a TUI. Com a
  página aberta, o motor rende cerca de 94% da velocidade sem ela (medido).
- `python web.py --porta 9000` usa outra porta; `--sem-navegador` não abre o navegador sozinho.

---

## Interface de torneio (`tui.py`)

Feita para a mesa do torneio: o operador não digita comandos, só o lance do adversário.

- **Tela inicial:** cor (tecla B ou P), motor (tecla M: Stockfish ou o próprio, reserva),
  ritmo (1 a 4: 15+10, Armageddon, 25+0 ou personalizado), pondering, segundos do operador e
  desempenho do Stockfish (tecla D: Ultra, Médio ou Baixo).
  Enter começa a partida. Se a partida anterior foi interrompida, aparece **Retomar partida**,
  com o mesmo motor. O topo da partida mostra o motor em uso (por exemplo, "Stockfish 19").
- **Placa "Agora":** diz o que fazer em cada momento. Quando o motor joga, o lance aparece em
  letras grandes com os passos do FAQ Item 10: anunciar, mover a peça (com a descrição do
  movimento, inclusive roque e promoção), apertar o relógio e digitar o lance do adversário.
  Um bipe avisa que o lance saiu.
- **Lance do adversário:** digite em UCI (`e7e5`) e aperte Enter. Antes do Enter, a linha ao
  lado diz se o lance é legal e o que ele faz; lance ilegal não é aceito. Também dá para
  clicar na peça e depois na casa de destino. Promoção sem a peça (`e7e8`) abre a escolha.
- **Relógios:** os dois correm na tela (o do adversário é estimado) e ficam vermelhos com
  menos de 1 minuto.
- **Motor:** profundidade, nós, avaliação com barra, linha principal e o lance esperado no ponder.
- **Súmula:** todos os lances; os nossos em amarelo, com a origem (livro, tablebase, manual).
- **Tabuleiro:** do ponto de vista da nossa cor; fichas claras são as Brancas e escuras as
  Pretas, com letras (R rei, D dama, T torre, B bispo, C cavalo, P peão) ou símbolos.

| Tecla | O que faz |
| :--- | :--- |
| Enter | Registra o lance digitado; com o motor pausado, manda o motor pensar |
| Espaço | Enquanto o motor pensa, joga agora com o melhor lance encontrado |
| F1 | Ajuda |
| F2 | Desfaz o último meio-lance (e para o motor, se ele estiver pensando) |
| F3 | Sincroniza com o relógio físico (MM:SS) |
| F4 | Ajustes: operador, fator de tempo, contempt (no Stockfish, regra do empate imediato), pondering, som e peças |
| F5 | Corrige a posição por FEN (o tabuleiro físico é a referência) |
| F6 | Modo economia: metade do tempo por lance (desempate por tempo, FAQ Item 12) |
| F7 | Avaliação estática da posição |
| F8 | Liga ou desliga o pondering |
| F9 | Registra um lance nosso diferente do motor (peça tocada); vai ao log como MANUAL |
| Ctrl+Q | Encerra: resultado declarado pelo árbitro, ou sair e retomar depois |

O rodapé aceita clique, útil se o terminal capturar alguma tecla F (o GNOME Terminal usa o
F1). A partida é salva a cada lance em `logs/partida_em_andamento.json`: se o notebook
travar, rode `python tui.py` de novo e escolha **Retomar partida**; o log continua no mesmo
arquivo. Maximize o terminal (o ideal é pelo menos 120 × 36). O log da banca tem o mesmo
formato nos dois modos.

---

## Checklist do dia do torneio

1. O notebook mais rápido da equipe (`python tools/bench_stockfish.py`), **na tomada**, modo de
   energia **desempenho**, outros programas fechados (navegadores pesados roubam processador e
   memória), **Wi-Fi e dados móveis desligados** (FAQ Item 14) e ventilação livre: com carga
   contínua o processador esquenta e reduz o clock (medido: 91 °C e −18% de nós por segundo).
   Durante a partida a tela avisa se o notebook sair da tomada ou entrar em economia de energia.
2. `python test_engine.py` → tem que terminar com "TODOS OS TESTES PASSARAM", e o teste 12
   tem que mostrar "Stockfish 19". Se aparecer o aviso de Stockfish não encontrado, rode
   `python tools/baixar_stockfish.py` enquanto ainda há internet.
3. Abra `python web.py` ou `python tui.py` (ou o modo texto, `python main.py`). A primeira linha
   tem que ser "Motor do torneio pronto: Stockfish 19". Depois informe:
   - motor **Stockfish** com desempenho **Ultra** (os padrões quando ele está instalado; confira
     "Stockfish 19 (Ultra)" no topo da partida). O motor próprio fica de reserva;
   - cor, controle de tempo (15+10 é o padrão do FAQ Item 7; no Armageddon o empate classifica
     as Pretas — FAQ Item 12. O motor próprio ajusta o contempt sozinho; com o Stockfish vale a
     regra do empate imediato: com Pretas ele joga o lance que empata na hora, com Brancas foge
     dele. O FAQ não diz se o Armageddon tem incremento: confirme com o árbitro e preencha o campo
     do incremento, que aparece quando o Armageddon é escolhido);
   - Pondering **ligado** (FAQ Item 13);
   - **segundos que o operador gasta por lance** (anunciar + mover a peça + apertar o
     relógio + digitar o lance adversário). Meçam no treino! O padrão é 4 s.
     A gestão de tempo desconta esse custo de todos os lances futuros — no Armageddon
     sem incremento isso é o que evita perder por tempo.
   - **O operador decide o desempate.** Digitem o lance do adversário assim que ele for
     confirmado, antes de ele apertar o relógio (esse tempo é dele). Cada segundo a menos por
     lance vale cerca de um minuto por partida na soma do tempo restante (FAQ Item 12). No
     Armageddon sem incremento, com Pretas, 4 min e 4,5 s de operador dão só uns 50 lances: a tela
     avisa quantos lances faltam para a bandeira de cada lado.
4. A cada ~10 lances, sincronize com o relógio físico: F3 na interface, ou
   `tempo 11:42 12:05` no modo texto (nosso tempo e, opcional, o do adversário).
   - **Na interface, a sincronização também mede o operador.** Se ele estiver mais lento do que
     o configurado, o custo por lance sobe sozinho, e a tela avisa o valor novo.
   - **Valor improvável pede confirmação** (Enter de novo), como `1230` no lugar de `12:30`. O
     ponto vale como dois-pontos: `12.30` é 12:30.
   - **Sem sincronizar,** o motor não tem como saber que o operador demora mais. A simulação de
     150 lances de 15+10 mostra o risco: com o operador 2 s mais lento que o configurado, ele perde
     por tempo perto do lance 60.
5. O log exigido pelo FAQ Item 9 é gravado a cada lance em `logs/partida_*.log`
   e espelhado em `logs/ultima_partida_banca.txt`.
6. Na interface, desfazer, posição, ajustes e os demais comandos ficam nas teclas F1 a F9
   (tabela acima). No modo texto, os comandos de emergência são `undo`, `fen <FEN>`,
   `forcar <lance>`, `tempo`, `ponder`, `avaliar`, `log` e `ajuda`.
7. Ajustes durante a partida (F4 e F6 na interface; no modo texto, digitados na vez do adversário):
   - `operador 2` — o operador ficou mais rápido/lento (segundos por lance);
   - `fator 0.7` — pensar 30% menos. O desempate da Melhor de 3 é a soma do tempo restante
     nas três partidas (FAQ Item 12): em posição morta, `fator 0.5` guarda relógio e
     `fator 1` volta ao normal;
   - `contempt 30` — evitar mais os empates contra adversário mais fraco. No Stockfish vale para
     o lance que empata na hora (repetição, 50 lances, afogamento ou material insuficiente): ele só
     é jogado se a melhor alternativa for pior que −30 cp.

---

## Arquitetura

| Arquivo | Responsabilidade |
| :--- | :--- |
| `motor_stockfish.py` | Motor do torneio: abre o Stockfish por UCI, consulta o nosso livro e as tablebases primeiro e deixa o Stockfish pensar sem limite (`go infinite`); a cada profundidade concluída, a nossa gestão de tempo decide se continua ou para. Mesma interface do motor próprio, que fica de reserva |
| `search.py` | Busca do motor próprio: Iterative Deepening, Aspiração, PVS, TT, Quiescência, SEE, podas e reduções, geração de lances em estágios, repetição, tablebases na busca |
| `evaluation.py` | Avaliação: Tapered Eval (PeSTO), mobilidade, segurança do rei, estrutura de peões com cache, peões passados, conhecimento de finais, Mop-Up |
| `time_manager.py` | Tempo: orçamento por lance com custo do operador, limites soft/hard, extensão por instabilidade e fail-low |
| `book_and_tb.py` | Livro (trava de 10 meios-lances, FAQ Item 3) e Syzygy (até 5 peças, FAQ Item 4) |
| `web.py`, `web/` | Interface no navegador: servidor local (biblioteca padrão) e página com tabuleiro clicável, placa de anúncio, relógios, súmula e os comandos da TUI |
| `tui.py`, `tui.tcss` | Interface de torneio (TUI): placa de anúncio, lance adversário validado ao digitar, relógios, súmula e atalhos F1 a F9 |
| `session.py` | Estado da partida usado pela TUI e pelo navegador: escolha do motor, a mesma lógica do `main.py` (relógio, log, ponder, contempt) e o salvamento para retomar |
| `main.py` | Interface em modo texto (4 passos do FAQ Item 10), pondering seguro, comandos de emergência |
| `logger.py` | Log obrigatório da banca (FAQ Item 9) |
| `uci.py` | Protocolo UCI (Arena, CuteChess, EnCroissant, Lichess e `tools/match.py`); opções `Motor` (`Proprio` ou `Stockfish`) e `Desempenho` (`Baixo`, `Medio` ou `Ultra`) |
| `tools/match.py` | Match automático entre dois motores com Elo e intervalo de confiança |
| `tools/baixar_stockfish.py` | Download do Stockfish 19 oficial, com conferência do SHA-256 |
| `tools/bench_stockfish.py` | Velocidade do Stockfish com a configuração do torneio, para escolher o notebook mais rápido |
| `tools/baixar_syzygy.py` | Download das tablebases de 3-4-5 peças |
| `tools/bench.py` | Benchmark de velocidade (rode no notebook do torneio) |
| `tools/extrair_pgn.py`, `tools/gerar_dados.py`, `tools/texel.py` | Texel Tuning (experimental): dados rotulados e ajuste automático dos pesos. O resultado foi **rejeitado** no match (−150 Elo), então os pesos finais são os nossos ajustados à mão |
| `test_engine.py` | Bateria de validação |

---

## Guia para a Arguição da Banca

O motor do torneio é o Stockfish com a **nossa gestão de tempo**: as perguntas A a E são as
principais. As seções 1 a 11 explicam o motor próprio (reserva), todo de autoria da equipe.

### A. "O que é de vocês, se o Stockfish faz a busca e a avaliação?"
O FAQ permite usar o Stockfish, desde que declarado (Item 1), e exige ao menos um componente
decisório próprio (Item 2). O nosso é a **gestão de tempo** (`time_manager.py`). O Stockfish roda
em `go infinite`, ou seja, ele não decide quando parar: quanto pensar em cada lance e quando parar
é decisão do nosso código, com as mesmas regras do motor próprio. Também é nossa uma parte da
**seleção de lances**: a regra do empate imediato (seção F), que decide entre empatar agora ou não
quando o Stockfish, que avalia todo empate como 0, não sabe o que o empate vale no torneio. São
nossos ainda o livro de aberturas consultado antes do Stockfish, as interfaces de torneio, o log
da banca e o motor reserva inteiro (avaliação, busca e tempo).

### B. "Como a gestão de tempo controla o Stockfish?"
O `motor_stockfish.py` abre o Stockfish por UCI e lê as linhas de informação que ele envia:

1. Antes de começar, `allocate_time` calcula o tempo-alvo e o limite rígido do lance (fórmula
   da seção 6, com o custo do operador).
2. A cada profundidade concluída: se o melhor lance mudou ou a nota caiu, o motor pensa mais
   (`adjust_for_instability`); se o lance está estável há várias profundidades, economiza.
3. Se o Stockfish informa que a raiz falhou baixo (linha `upperbound`), `notify_fail_low` dá mais
   tempo ao lance.
4. Uma nova profundidade só é esperada se houver tempo provável para terminá-la
   (`should_stop_iteration`). O limite rígido manda parar na hora (`should_abort_search`), e o
   Stockfish devolve o melhor lance que tem.
5. Mate forçado confirmado: para sem gastar o resto do tempo.
6. **Crédito do ponder:** se o adversário jogou o lance que o ponder previu, o tempo que o ponder
   já pensou nessa posição conta como gasto. A tabela de transposição guarda essa busca, então o
   Stockfish volta à mesma profundidade em milissegundos e o lance sai bem antes, sem perder força.
   A parada acontece no meio da profundidade (com a tabela quente, a profundidade seguinte demora
   segundos), com um piso de 15% do alvo para confirmar o lance.
7. **Empate morto:** se as duas últimas buscas deram nota exatamente 0,00 com profundidade 18 ou
   mais (o Stockfish vê empate forçado), o lance usa 30% do tempo normal.

### C. "Por que a gestão de tempo importa contra outro Stockfish?"
Entre dois Stockfish a maioria das partidas empata. Com o confronto empatado, o FAQ (Item 12)
desempata pela **soma do tempo restante** nas três partidas. A nossa gestão de tempo foi feita
para a mesa: desconta o tempo do operador em cada lance, economiza quando o lance está estável e
tem o modo economia (F6) para guardar relógio em posição morta. No teste com o mesmo Stockfish
dos dois lados, foram 12 empates em 12 partidas, e a nossa gestão de tempo gastou 34% menos tempo
por lance: o nosso relógio nunca ficou abaixo de 19 s, e o da gestão de tempo do Stockfish chegou
a 1,5 s.

Na simulação de torneio da véspera (136 partidas pela mesma sessão da interface), o ponder
acertou o lance do adversário em 67% a 77% das vezes contra o Stockfish, mas o lance seguinte
ainda usava o tempo cheio; e 53% dos nossos lances foram em posição com nota 0,00. O crédito do
ponder e o empate morto (itens 6 e 7 da seção B) atacam exatamente esses dois desperdícios.

### D. "E se o Stockfish cair ou travar no meio da partida?"
Ele roda como processo separado. Por isso uma falha dele não derruba a interface, e o relógio
não fica parado:
- **Caiu no meio da busca:** vale o melhor lance que ele já tinha mostrado. Se caiu antes de
  mostrar algum, o processo é reaberto e a busca continua no tempo que sobra do lance.
- **Travou:** a busca para no limite rígido. Se o `bestmove` não chegar em 1,5 s depois do
  `stop`, o processo é derrubado e vale o melhor lance até ali.
- **Não reabre:** por exemplo, o executável sumiu. A partida segue com o motor próprio, a tela
  avisa e o log registra a troca.

O teste 12 derruba e congela o processo de propósito no meio da busca.

### E. "E a licença do Stockfish?"
GPLv3. Usamos o programa oficial sem alterar o código: ele roda como processo separado e conversa
por UCI. O executável não está no repositório; é baixado por `tools/baixar_stockfish.py` da
versão oficial `sf_19`, com conferência do SHA-256. A rede neural embutida nele é peso de
terceiros (Item 15) e está declarada.

### F. "O que é a regra do empate imediato?"
Repetição tripla e 50 lances encerram a partida na hora (FAQ Item 12), e o Stockfish avalia todo
empate como 0. Mas no torneio o empate nem sempre vale 0:
- **Armageddon, Pretas** (o empate classifica): se algum lance empata na hora (repetição, 50
  lances, afogamento ou material insuficiente), o motor joga esse lance sem buscar.
- **Armageddon, Brancas** (o empate elimina): a busca do Stockfish fica restrita (`searchmoves`)
  aos lances que não empatam na hora e que não deixam o adversário empatar no lance seguinte, se
  sobrar algum.
- **Partida normal com contempt** (F4): o empate vale −contempt para nós. Se existe lance que empata
  na hora, o Stockfish procura o melhor entre os outros, e o empate só é escolhido se essa
  alternativa for pior que −contempt. Com contempt 0 (o padrão), nada muda.

O teste 17 cobre os três casos. Na simulação de Armageddon a regra disparou poucas vezes, mas
decidiu: numa delas as Pretas afogaram o rei adversário com 1 s no relógio.

### 1. "Como funciona a busca?"
**Iterative Deepening + Negamax Alpha-Beta com PVS.** Buscamos profundidade 1, 2, 3...
Cada iteração reaproveita a anterior (Tabela de Transposição e melhor lance primeiro), e
se o tempo acaba no meio de uma iteração usamos o melhor lance **já provado** nela.

- **Janela de Aspiração:** a partir da profundidade 4 a raiz busca numa janela estreita
  (±30 cp) em volta da nota anterior; se falhar, alarga. Janelas estreitas cortam mais.
- **PVS (Principal Variation Search):** o 1º lance é buscado com janela cheia; os demais
  com janela nula (`alpha, alpha+1`), só para provar que são piores. Se algum surpreender,
  é rebuscado com janela cheia.
- **Tabela de Transposição:** vetor de tamanho fixo (2^20 entradas). A chave de 64 bits é
  calculada direto dos bitboards (`position_key`, ~0,2 µs, contra ~20 µs do Zobrist da
  biblioteca). Guarda profundidade, tipo de limite (EXACT/LOWER/UPPER), melhor lance e
  avaliação estática. Política de substituição preserva entradas profundas da busca atual.

### 2. "Como vocês fazem a árvore ficar tão profunda em Python?"
Python é lento (~20 mil nós/s), então o ganho vem de **não olhar o que não importa**:

| Técnica | Ideia |
| :--- | :--- |
| Reverse Futility Pruning | Perto das folhas, se nota estática − margem ainda ≥ beta, corta |
| Null Move Pruning | "Passamos a vez": se mesmo assim o rival não alcança beta, a posição é boa demais → corta (R adaptativo) |
| Razoring | Nota muito abaixo de alpha a 1–2 plies do fim: confirma só com quiescência |
| Late Move Pruning | Em profundidade baixa, depois de N lances quietos, os seguintes (mal ordenados) são podados |
| Futility Pruning | Lance quieto que não dá xeque não consegue levantar alpha → podado |
| Late Move Reductions | Lances tardios são buscados mais rasos (tabela `0,75 + ln(prof)·ln(nº)/2,25`); se surpreenderem, rebusca completa |
| Internal Iterative Reduction | Sem lance da TT a ordenação é pior: reduz 1 ply |
| Extensão de Xeque | Em xeque, busca 1 ply a mais (não para no meio de um ataque) |
| Mate Distance Pruning | Não procura mates mais longos que um já encontrado |

**Cuidados contra cegueira tática** (bugs que encontramos com a bateria WAC e corrigimos):
lances que **dão xeque nunca são podados** (verificado sem executar o lance, por máscaras
de ataque ao rei); a busca do lance nulo sempre tem pelo menos 1 ply completo (a quiescência
sozinha não vê mates quietos); a margem do Razoring é grande; **sacrifícios com xeque**
(captura "perdedora" que dá xeque, ex.: Bxh7+) não sofrem redução LMR.

### 3. "E o Efeito Horizonte?"
**Quiescence Search:** na profundidade 0 continuamos só com capturas e promoções a Dama
(e todas as fugas, se estiver em xeque) até a posição ficar calma. Usa *stand-pat*,
**Delta Pruning** (nem ganhando a peça alcança alpha → ignora) e **SEE**: capturas que
perdem material (ex.: Dama toma peão defendido) são descartadas.

**SEE (Static Exchange Evaluation)** simula a sequência de capturas numa casa, sempre com a
peça menos valiosa, incluindo peças "em raio-x" atrás das que saem (`see_ge`).

### 4. "Como ordenam os lances?"
Geração **em estágios** (`_staged_moves`): 0) lance da TT → 1) capturas boas (MVV-LVA +
SEE) e promoções → 2) killer moves e counter-move → 3) lances quietos pela History
Heuristic (com "gravidade": bônus para o lance que cortou, penalidade para os quietos
que falharam) → 4) capturas ruins. Como no nó de corte quase sempre os primeiros estágios
bastam, os lances quietos (a parte cara) muitas vezes nem são gerados.

### 5. "Como funciona a avaliação?"
- **Tapered Eval:** cada termo tem valor de meio-jogo (MG) e de final (EG); a fase
  (24 → 0, pelas peças em jogo) interpola os dois.
- **Escore empacotado** `S(mg, eg) = mg + eg·2^16`: MG e EG no mesmo inteiro, uma soma por
  termo (técnica dos motores modernos). Só desempacota no final.
- **Material + PST** (valores no estilo PeSTO), **mobilidade** (casas seguras, fora do
  ataque de peões inimigos), **segurança do rei** (unidades de ataque na zona do rei numa
  tabela não-linear + escudo de peões + colunas abertas), **par de bispos**, **torres** em
  colunas abertas/semiabertas e na 7ª.
- **Ameaças:** peça atacada por peão inimigo e peça **pendurada** (atacada e sem defensor),
  usando os mapas de ataque já calculados para a mobilidade (custo quase zero).
- **Estrutura de peões com cache** (*pawn hash*): passados, isolados, dobrados e
  conectados dependem só dos peões, então são calculados uma vez e reaproveitados.
- **Peões passados no final:** distância dos reis à casa de promoção e caminho livre.
- **Conhecimento de finais:** K+B/K+N/K+N+N não ganham; finais sem peões com pouca
  vantagem e bispos de cores opostas são "escalados" para o empate; **Mop-Up** empurra o
  rei sozinho para a borda (e para o canto da cor do bispo no mate de Bispo + Cavalo).
- Avaliação **simétrica** (testada: espelhar a posição troca só o sinal).

### 6. "Como funciona a gestão de tempo?"
`tempo_alvo = (relógio − reserva + lances_restantes × (incremento − custo_do_operador)) / lances_restantes`

- O **custo do operador** (anúncio + tabuleiro + relógio) entra na conta: no Armageddon
  sem incremento o motor sabe que cada lance custa alguns segundos fora dele.
- **Hard limit** (teto absoluto) interrompe a busca na hora; uma nova profundidade só
  começa se houver tempo provável para terminá-la.
- **Instabilidade:** se o melhor lance muda entre profundidades, se a nota cai ou se a
  raiz falha baixo (*fail-low*), o motor pensa mais; se o lance está estável há várias
  iterações, economiza.
- Lance único (ex.: única fuga de xeque) é jogado na hora.
- **Zona de sobrevivência:** com poucos lances de folga no relógio (abaixo de ~42 s em 15+10
  com 4 s de operador), o alvo de cada lance cai para metade do incremento líquido (incremento
  menos operador) mais 1/25 do relógio, e o limite rígido não passa do incremento líquido
  inteiro. O resto do incremento reconstrói o relógio a cada lance. Numa partida simulada de
  150 lances, o relógio para em ~40 s e não cai mais, mesmo se a busca for até o limite em todo
  lance.
- **O operador é medido pelo relógio físico:** a cada sincronização (F3), a diferença entre o
  relógio interno e o físico, dividida pelos lances desde a última, mostra quanto o operador
  demora a mais por lance. O custo configurado sobe para esse valor.
  - Só sobe, porque pensar menos é o lado seguro. Um relógio parado pelo árbitro não faz o motor
    pensar mais.
  - Desfazer, FEN e retomada invalidam a medida até a próxima sincronização.
- **Crédito do ponder:** ponder acertou o lance do adversário → o tempo dele nessa posição conta
  como gasto; o lance para quando ponder + agora chega à metade do alvo (o alvo cresce se o lance
  ficar instável), nunca antes de 15% do alvo.
- **Empate morto:** duas buscas seguidas com nota 0,00 e profundidade alta → 30% do tempo.

### 7. Empates, repetição e "contempt"
Detectamos repetição com uma pilha própria de chaves (só até o último lance irreversível)
e a regra dos 50 lances. Empate vale **−15 cp** para nós (*contempt*): o motor evita
repetir em posições iguais/levemente melhores, porque jogamos para ganhar.

No **Armageddon** (FAQ Item 12) o empate classifica as Pretas, então o `main.py` ajusta o
contempt sozinho: **+100 cp** com Brancas (empate é derrota) e **−100 cp** com Pretas (o motor
passa a buscar repetição e finais empatados). O contempt é sempre contado a partir da nossa
cor, inclusive no ponder e nos empates de tablebase. Com o Stockfish, que não tem contempt, vale
a regra do empate imediato (seção F).

### 8. Livro e Tablebases
- Livro embutido de linhas de GMs (e suporte a `.bin` Polyglot em `books/`) com trava
  estrita de **10 meios-lances** (FAQ Item 3).
- **Syzygy** (FAQ Item 4, até 5 peças): na **raiz** escolhe o lance pelo DTZ (vitória mais
  rápida / derrota mais lenta, mate imediato primeiro); **dentro da busca** consulta WDL
  logo após capturas, então o motor já "sabe" quais trocas levam a finais ganhos.

### 9. Pondering (FAQ Item 13)
Durante o turno adversário o motor pensa na posição após o lance que ele **prevê** (2º lance
da PV). Ao digitar o lance real, o ponder para em milissegundos; se o lance foi o previsto,
a Tabela de Transposição já tem a árvore pronta, e o tempo pensado no ponder vira crédito na
gestão de tempo (o lance sai mais rápido). A busca de ponder e a principal nunca rodam
ao mesmo tempo (o motor tem um *lock* e o `stop_ponder` espera a thread terminar).

### 10. "Os números da avaliação são de vocês?"
O **código** da avaliação é todo nosso. As tabelas de material/PST partem dos valores
públicos do PeSTO (Ronald Friederich, publicados na Chess Programming Wiki), um ponto de
partida conhecido; por cima delas criamos e calibramos os demais termos (mobilidade,
segurança do rei, peões, ameaças, finais). Também implementamos **Texel Tuning**
(`tools/texel.py`): minimizar o erro entre a probabilidade prevista pela avaliação
(sigmoide) e o resultado real de ~98 mil posições. O erro caiu 2,5%, mas no match os
pesos ajustados **perderam** (−150 Elo) — então ficamos com os nossos. Lição: o que
manda é o resultado em partidas, não o erro estatístico.

### 11. "Como vocês sabem que cada mudança melhorou o motor?"
Com `tools/match.py`: a versão nova joga contra a anterior dezenas de partidas a partir de
aberturas equilibradas, cada abertura duas vezes com as cores trocadas. O script calcula a
diferença de Elo com intervalo de confiança de 95% e a LOS (probabilidade de a nova ser
mais forte). Para táticas usamos a bateria **WAC** (300 posições "Win At Chess").

---

## Ferramentas de teste

```bash
# Match versão nova x versão antiga (Elo com intervalo de 95% e LOS)
python tools/match.py --a "python uci.py" --a-name NOVO --a-opt Log=false \
                      --b "python ../versao_antiga/uci.py" --b-name ANTIGO \
                      --games 100 --tc 20+0.2 --concurrency 3

# Simular o operador humano (desconta 0,8 s extra por lance) em ritmo reduzido 3+2
python tools/match.py ... --tc 180+2 --overhead 0.8 --a-opt "Move Overhead=800"

# Nossa gestão de tempo x a do próprio Stockfish (o mesmo Stockfish dos dois lados)
python tools/match.py --a "python uci.py" --a-name NOSSO_TEMPO --a-opt Motor=Stockfish \
                      --a-opt "Move Overhead=160" --a-opt Log=false --a-opt OwnBook=false \
                      --b ../stockfish/stockfish/stockfish-linux-x86-64-universal --b-name SF_PURO \
                      --b-opt Threads=4 --b-opt "Move Overhead=160" \
                      --games 12 --tc 40+0.4 --overhead 0.16 --concurrency 1
```

`tools/simular_torneio.py` joga pelo **mesmo caminho das interfaces** (`session.GameSession`: livro,
Stockfish com a nossa gestão de tempo, crédito do ponder, empate morto, regra do empate, log e fim de
partida), com o relógio físico e o operador simulados (o operador real pode ser mais lento que o
configurado) e o F3 a cada 10 lances. Os logs vão para uma pasta temporária, nunca para `logs/`.

```bash
# Espelho contra o Stockfish com a gestão de tempo dele, em 90 s + 1 s (1/10 do 15+10), com ponder
python tools/simular_torneio.py --jogos 6 --base 90 --inc 1 --ponder

# Contra um adversário mais fraco, da posição inicial (com o nosso livro)
python tools/simular_torneio.py --jogos 4 --adversario sf --elo 1800 --posicao-inicial

# Armageddon no ritmo real (5 e 4 minutos, operador configurado 4 s e real 4,5 s)
python tools/simular_torneio.py --jogos 8 --armageddon --operador 4 --operador-real 4.5 \
    --operador-real-adversario 4.5 --overhead-adversario 4000 --margem 0.5
```
