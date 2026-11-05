# Motor de Xadrez — Chess Challenge III (ICEV 2026)

Motor de xadrez em Python para o **III Chess Challenge — ICEV 2026**. A biblioteca
`python-chess` é usada **somente** para representar o tabuleiro e gerar lances legais
(permitido pelo **Item 14 do FAQ**). Os três componentes decisórios exigidos pelo
**Item 2 do FAQ** são de autoria própria:

1. **Função de Avaliação** (`evaluation.py`)
2. **Busca e Seleção de Lances** (`search.py`)
3. **Gestão Dinâmica de Tempo** (`time_manager.py`)

**Documentação completa:** [`docs/index.html`](docs/index.html). Abra o arquivo no navegador (funciona sem
internet) ou, com `python web.py` rodando, em `http://127.0.0.1:8765/documentacao`. Tem instalação, uso
durante a partida, arquitetura, o motor por dentro, conformidade com o FAQ e o guia da arguição.

---

## Declaração para o credenciamento (FAQ Item 1)

| Item | Declaração |
| :--- | :--- |
| Projeto-base | [`jfplandim/Chess`](https://github.com/jfplandim/Chess), commit `ccee6c7` |
| Autoria própria (Item 2) | Avaliação (`evaluation.py`), busca (`search.py`) e gestão de tempo (`time_manager.py`) |
| Bibliotecas (Item 14) | `chess` 1.11.2 (python-chess), só para tabuleiro e lances legais; `textual` 8.2.8, só para desenhar a interface. Ambas declaradas em `requirements.txt` |
| Livro (Item 3) | Linhas clássicas de abertura embutidas em `book_and_tb.py`, até 10 meios-lances |
| Tablebases (Item 4) | Syzygy de até 5 peças, baixadas antes do evento com `tools/baixar_syzygy.py` |
| Tabelas da avaliação | Valores públicos do PeSTO como ponto de partida (seção 10 do guia da banca) |
| Hardware (Itens 5 a 8) | Notebook da equipe, só CPU, sem GPU |
| Modelos (Item 15) | Nenhum peso de modelo |
| Rede (Item 14) | Nenhuma requisição externa durante as partidas |

Livro, tablebases e tabelas PeSTO são de terceiros e não contam como autoria da equipe.

---

## Resultado da versão 2 (medido)

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

### 1. Instalar a dependência (antes do torneio, com internet)
Requer **Python 3.10 ou superior** (testado no 3.14; o CPython é mais rápido que o PyPy
para este motor, pois os bitboards de 64 bits viram inteiros grandes no PyPy).
```bash
pip install -r requirements.txt
```

### 2. (Recomendado) Baixar as Tablebases Syzygy de até 5 peças (FAQ Item 4)
```bash
python tools/baixar_syzygy.py          # ~940 MB, cria a pasta syzygy/
```
Sem a pasta `syzygy/` o motor funciona normalmente (só não usa as tablebases).

### 3. Validar o motor (rode antes de cada partida)
```bash
python test_engine.py
```

### 4. Partida oficial (100% offline)
```bash
python web.py          # interface no navegador (gráfica)
python tui.py          # a mesma interface no terminal
python main.py         # modo texto, se nenhuma das duas abrir
```

---

## Interface no navegador (`web.py`)

A interface de torneio em versão gráfica: `python web.py` abre `http://127.0.0.1:8765` no
navegador e o terminal só mostra esse endereço. Tem as mesmas fases, teclas e regras da TUI
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

- **Tela inicial:** cor (tecla B ou P), ritmo (1 a 4: 15+10, Armageddon, 25+0 ou
  personalizado), pondering e segundos do operador. Enter começa a partida. Se a partida
  anterior foi interrompida, aparece **Retomar partida**.
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
| F4 | Ajustes: operador, fator de tempo, contempt, pondering, som e peças |
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

1. Notebook **na tomada**, modo de energia **desempenho**, outros programas fechados.
2. `python test_engine.py` → tem que terminar com "TODOS OS TESTES PASSARAM".
3. Abra `python tui.py` (ou o modo texto, `python main.py`) e informe:
   - cor, controle de tempo (15+10 é o padrão do FAQ Item 7; Armageddon = opção 3, que já
     ajusta o contempt sozinho: com Brancas evita o empate, com Pretas busca, pois o empate
     classifica as Pretas — FAQ Item 12);
   - Pondering **ligado** (FAQ Item 13);
   - **segundos que o operador gasta por lance** (anunciar + mover a peça + apertar o
     relógio + digitar o lance adversário). Meçam no treino! O padrão é 4 s.
     A gestão de tempo desconta esse custo de todos os lances futuros — no Armageddon
     sem incremento isso é o que evita perder por tempo.
4. A cada ~10 lances, sincronize com o relógio físico: F3 na interface, ou
   `tempo 11:42 12:05` no modo texto (nosso tempo e, opcional, o do adversário).
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
   - `contempt 30` — evitar mais os empates contra adversário mais fraco.

---

## Arquitetura

| Arquivo | Responsabilidade |
| :--- | :--- |
| `search.py` | Busca: Iterative Deepening, Aspiração, PVS, TT, Quiescência, SEE, podas e reduções, geração de lances em estágios, repetição, tablebases na busca |
| `evaluation.py` | Avaliação: Tapered Eval (PeSTO), mobilidade, segurança do rei, estrutura de peões com cache, peões passados, conhecimento de finais, Mop-Up |
| `time_manager.py` | Tempo: orçamento por lance com custo do operador, limites soft/hard, extensão por instabilidade e fail-low |
| `book_and_tb.py` | Livro (trava de 10 meios-lances, FAQ Item 3) e Syzygy (até 5 peças, FAQ Item 4) |
| `web.py`, `web/` | Interface no navegador: servidor local (biblioteca padrão) e página com tabuleiro clicável, placa de anúncio, relógios, súmula e os comandos da TUI |
| `tui.py`, `tui.tcss` | Interface de torneio (TUI): placa de anúncio, lance adversário validado ao digitar, relógios, súmula e atalhos F1 a F9 |
| `session.py` | Estado da partida usado pela TUI: a mesma lógica do `main.py` (relógio, log, ponder, contempt) e o salvamento para retomar |
| `main.py` | Interface em modo texto (4 passos do FAQ Item 10), pondering seguro, comandos de emergência |
| `logger.py` | Log obrigatório da banca (FAQ Item 9) |
| `uci.py` | Protocolo UCI (Arena, CuteChess, EnCroissant e `tools/match.py`) |
| `tools/match.py` | Match automático entre duas versões do motor com Elo e intervalo de confiança |
| `tools/baixar_syzygy.py` | Download das tablebases de 3-4-5 peças |
| `tools/bench.py` | Benchmark de velocidade (rode no notebook do torneio) |
| `tools/extrair_pgn.py`, `tools/gerar_dados.py`, `tools/texel.py` | Texel Tuning (experimental): dados rotulados e ajuste automático dos pesos. O resultado foi **rejeitado** no match (−150 Elo), então os pesos finais são os nossos ajustados à mão |
| `test_engine.py` | Bateria de validação |

---

## Guia para a Arguição da Banca

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

### 7. Empates, repetição e "contempt"
Detectamos repetição com uma pilha própria de chaves (só até o último lance irreversível)
e a regra dos 50 lances. Empate vale **−15 cp** para nós (*contempt*): o motor evita
repetir em posições iguais/levemente melhores, porque jogamos para ganhar.

No **Armageddon** (FAQ Item 12) o empate classifica as Pretas, então o `main.py` ajusta o
contempt sozinho: **+100 cp** com Brancas (empate é derrota) e **−100 cp** com Pretas (o motor
passa a buscar repetição e finais empatados). O contempt é sempre contado a partir da nossa
cor, inclusive no ponder e nos empates de tablebase.

### 8. Livro e Tablebases
- Livro embutido de linhas de GMs (e suporte a `.bin` Polyglot em `books/`) com trava
  estrita de **10 meios-lances** (FAQ Item 3).
- **Syzygy** (FAQ Item 4, até 5 peças): na **raiz** escolhe o lance pelo DTZ (vitória mais
  rápida / derrota mais lenta, mate imediato primeiro); **dentro da busca** consulta WDL
  logo após capturas, então o motor já "sabe" quais trocas levam a finais ganhos.

### 9. Pondering (FAQ Item 13)
Durante o turno adversário o motor pensa na posição após o lance que ele **prevê** (2º lance
da PV). Ao digitar o lance real, o ponder para em milissegundos; se o lance foi o previsto,
a Tabela de Transposição já tem a árvore pronta. A busca de ponder e a principal nunca rodam
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
```
