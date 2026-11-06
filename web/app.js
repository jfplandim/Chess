/* Interface de torneio no navegador - Chess Challenge III - ICEV 2026
   A página só desenha e envia pedidos: motor, relógio, log e salvamento rodam no web.py. */
"use strict";

const TOKEN = window.TOKEN;
const $ = (seletor) => document.querySelector(seletor);
const COLUNAS = "abcdefgh";
const TECLAS_F = {
  F1: "ajuda", F2: "desfazer", F3: "relogio", F4: "ajustes", F5: "posicao",
  F6: "economia", F7: "avaliar", F8: "ponder", F9: "manual",
};
const RITMOS = ["oficial", "armageddon", "edital", "personalizado"];
const POSICAO_INICIAL = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1";
const MENSAGEM_FASE = {
  pensando: "O motor está pensando. O lance aparece na placa Agora.",
  pausado: "Motor pausado. Enter faz o motor pensar.",
  fim: "Partida encerrada.",
};
const DICA_ENTRADA = "Digite o lance anunciado, como e7e5. Roque: e1g1. Promoção: e7e8q.";

let E = null;                  // último estado vindo do web.py
let recebidoEm = 0;            // performance.now() quando o estado chegou
let faseAnterior = null;
let pecas = {};                // símbolo da peça -> imagem (data URI)
let ultimaMensagem = null;
let somVisto = null;
let relogioPedidoVisto = null;
let relogioConfirmado = null;
let selecao = null;            // casa de origem destacada no tabuleiro
let destinos = new Set();      // casas de destino destacadas
let textoPromocao = "";
let desfazerEsperado = null;
let audio = null;
let conectado = true;
let desligada = false;
let buscando = false;
let repetirBusca = false;
let temporizadorLeitura = null;
let temporizadorFen = null;

// ============================================================================ utilidades
function esc(texto) {
  return String(texto).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
}

function fmtRelogio(segundos) {
  const s = Math.max(0, Math.floor(segundos));
  return `${String(Math.floor(s / 60)).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}`;
}

function fmtTempo(segundos) {
  if (segundos < 10) return `${segundos.toFixed(1).replace(".", ",")} s`;
  if (segundos < 60) return `${Math.round(segundos)} s`;
  return `${Math.floor(segundos / 60)} min ${String(Math.floor(segundos % 60)).padStart(2, "0")} s`;
}

function fmtNumero(x) {
  return String(x).replace(".", ",");
}

function digitavel() {
  return !!E && (E.fase === "adversario" || E.fase === "manual");
}

function dialogoAberto() {
  return !!document.querySelector("dialog[open]");
}

function abrir(seletor) {
  const dialogo = $(seletor);
  dialogo.returnValue = "";
  if (!dialogo.open) dialogo.showModal();
}

function focarEntrada() {
  const entrada = $("#entrada");
  if (!entrada.disabled && !dialogoAberto()) entrada.focus();
}

function relogiosAgora() {
  const dt = (performance.now() - recebidoEm) / 1000;
  const r = E.relogio;
  return {
    nosso: r.correndo === "nosso" ? r.nosso - dt : r.nosso,
    adv: r.correndo === "adv" ? r.adv - dt : r.adv,
  };
}

// ============================================================================ avisos e som
function avisar(texto, tipo = "info", titulo = "") {
  const caixa = $("#avisos");
  const el = document.createElement("div");
  el.className = `aviso ${tipo}`;
  el.innerHTML = (titulo ? `<strong>${esc(titulo)}</strong>` : "") + esc(texto);
  caixa.append(el);
  while (caixa.children.length > 3) caixa.firstElementChild.remove();
  setTimeout(() => el.remove(), tipo === "erro" ? 9000 : 3500);
}

function desbloquearAudio() {
  try {
    audio = audio || new (window.AudioContext || window.webkitAudioContext)();
    if (audio.state === "suspended") audio.resume();
  } catch (erro) { /* sem som não atrapalha a partida */ }
}

function bipe(tipo = "lance") {
  try {
    desbloquearAudio();
    const t = audio.currentTime;
    const notas = tipo === "erro" ? [[0, 330, 0.22]] : [[0, 880, 0.12], [0.16, 1100, 0.12]];
    for (const [inicio, freq, duracao] of notas) {
      const osc = audio.createOscillator();
      const ganho = audio.createGain();
      osc.frequency.value = freq;
      ganho.gain.setValueAtTime(0.0001, t + inicio);
      ganho.gain.exponentialRampToValueAtTime(0.15, t + inicio + 0.01);
      ganho.gain.exponentialRampToValueAtTime(0.0001, t + inicio + duracao);
      osc.connect(ganho).connect(audio.destination);
      osc.start(t + inicio);
      osc.stop(t + inicio + duracao + 0.02);
    }
  } catch (erro) { /* idem */ }
}

// ============================================================================ comunicação
async function api(rota, dados = {}, { silencioso = false, atualizar = true } = {}) {
  let resposta;
  let json;
  try {
    resposta = await fetch(rota, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Token": TOKEN },
      body: JSON.stringify(dados),
    });
    json = await resposta.json();
  } catch (erro) {
    if (!desligada) avisar("Sem conexão com o web.py. Confira se ele ainda está rodando no terminal.", "erro");
    return null;
  }
  if (atualizar) buscarEstado();
  if (!resposta.ok) {
    const mensagem = json.erro || "O pedido falhou.";
    if (silencioso) return { erro: mensagem };
    avisar(mensagem, "erro");
    return null;
  }
  return json;
}

async function apiDialogo(rota, dados, seletorErro) {
  const r = await api(rota, dados, { silencioso: true });
  if (r && r.erro) {
    $(seletorErro).textContent = r.erro;
    $(seletorErro).style.color = "var(--bandeira)";
    return null;
  }
  return r;
}

async function buscarEstado() {
  if (desligada) return;
  if (buscando) {
    repetirBusca = true;
    return;
  }
  buscando = true;
  try {
    const resposta = await fetch("/api/estado", { cache: "no-store" });
    const novo = await resposta.json();
    marcarConexao(true);
    const mudou = !E || novo.versao !== E.versao;
    E = novo;
    recebidoEm = performance.now();
    if (mudou) desenhar();
    eventos();
  } catch (erro) {
    marcarConexao(false);
  } finally {
    buscando = false;
    if (repetirBusca) {
      repetirBusca = false;
      buscarEstado();
    }
  }
}

function ciclo() {
  if (desligada) return;
  // Enquanto o motor pensa, consulta menos (relógio e progresso andam aqui mesmo): sobra CPU para a busca
  buscarEstado().finally(() => setTimeout(ciclo, E && E.fase === "pensando" ? 600 : 400));
}

function marcarConexao(ok) {
  if (ok === conectado) return;
  conectado = ok;
  $("#conexao").hidden = ok;
}

function eventos() {
  const mensagens = E.mensagens || [];
  if (ultimaMensagem === null) ultimaMensagem = mensagens.length ? mensagens[mensagens.length - 1].id : 0;
  for (const m of mensagens) {
    if (m.id > ultimaMensagem) {
      avisar(m.texto, m.tipo, m.titulo);
      ultimaMensagem = m.id;
    }
  }
  if (somVisto === null) somVisto = E.som;
  if (E.som !== somVisto) {
    somVisto = E.som;
    if (!E.ajustes || E.ajustes.som) bipe("lance");
  }
  if (relogioPedidoVisto === null) relogioPedidoVisto = E.pedir_relogio;
  if (E.pedir_relogio !== relogioPedidoVisto) {
    relogioPedidoVisto = E.pedir_relogio;
    if (E.fase !== "inicio") abrirRelogio("Partida retomada. Confira os relógios físicos e corrija se for preciso.");
  }
}

function mostrarDesligada() {
  desligada = true;
  document.querySelectorAll("dialog[open]").forEach((d) => d.close());
  document.body.innerHTML = `<main class="desligada"><div class="cartao"><h1>Interface desligada</h1>
    <p>A partida ficou salva. Para voltar, rode <code>python web.py</code> no terminal e escolha
    <b>Retomar partida</b>.</p></div></main>`;
}

// ============================================================================ desenho
function desenhar() {
  const inicio = E.fase === "inicio";
  $("#inicio").hidden = !inicio;
  $("#partida").hidden = inicio;
  $("#barra").hidden = inicio;
  desenharTopo();
  if (inicio) {
    desenharInicio();
    faseAnterior = E.fase;
    return;
  }
  if (E.fase !== faseAnterior) aoMudarFase();
  desenharTabuleiro();
  desenharAgora();
  desenharRelogios();
  desenharMotor();
  desenharSumula();
  desenharEntrada();
  desenharAcoes();
  faseAnterior = E.fase;
}

function aoMudarFase() {
  selecao = null;
  destinos = new Set();
  $("#entrada").value = "";
  if (digitavel()) setTimeout(focarEntrada, 0);
  else if (document.activeElement && document.activeElement !== document.body) document.activeElement.blur();
  if (E.fase === "fim" && E.resultado && faseAnterior !== null) abrirFim();
}

function desenharTopo() {
  const info = $("#topo-info");
  if (E.fase === "inicio") {
    info.innerHTML = "";
    return;
  }
  const partes = [];
  if (E.ajustes.economia) partes.push('<span class="marcado">Economia ligada</span>');
  if (!E.ajustes.ponder) partes.push('<span class="fraco">Ponder desligado</span>');
  if (E.ajustes.contempt !== (E.stockfish ? 0 : 15)) {
    partes.push(`<span class="fraco">Contempt ${E.ajustes.contempt > 0 ? "+" : ""}${E.ajustes.contempt}</span>`);
  }
  partes.push(`<span class="${E.stockfish ? "marcado" : "fraco"}">${esc(E.nome_motor)}</span>`);
  partes.push(`<span>Jogamos de ${esc(E.nome_cor)}</span>`);
  partes.push(`<span>Ritmo ${esc(E.ritmo)}</span>`);
  partes.push(`<strong>Lance ${E.lance_num}</strong>`);
  info.innerHTML = partes.join("");
}

// ---------------------------------------------------------------- tela inicial
function configInicio() {
  const form = $("#form-inicio");
  return {
    cor: form.elements.cor.value,
    ritmo: form.elements.ritmo.value,
    minutos: $("#minutos").value,
    incremento: $("#incremento").value,
    inc_armageddon: $("#inc-armageddon").value,
    ponder: $("#ponder").checked,
    operador: $("#operador").value,
    motor: form.elements.motor.value || "proprio",
    desempenho: form.elements.desempenho.value || "ultra",
  };
}

const NOME_DESEMPENHO = { ultra: "Ultra", medio: "Médio", baixo: "Baixo" };

function desenharInicio() {
  const ini = E.inicio || {};
  $("#salvo").hidden = !ini.salvo;
  if (ini.salvo) $("#salvo-texto").textContent = ini.salvo;
  const tablebases = ini.tablebases
    ? `Tablebases Syzygy prontas, até ${ini.tablebases} peças.`
    : "Tablebases Syzygy não encontradas: o motor joga normalmente sem elas.";
  const radioStockfish = document.querySelector('#form-inicio input[name="motor"][value="stockfish"]');
  radioStockfish.disabled = !ini.stockfish;
  radioStockfish.closest("label").classList.toggle("indisponivel", !ini.stockfish);
  if (!ini.stockfish) document.querySelector('#form-inicio input[name="motor"][value="proprio"]').checked = true;
  const semStockfish = ini.stockfish ? "" : " Stockfish não encontrado: só o motor próprio. Instale com python tools/baixar_stockfish.py";
  $("#ambiente").textContent = `${tablebases} Livro de aberturas ativo até o 10º meio-lance.${semStockfish}`;
  atualizarResumo();
  if (faseAnterior !== "inicio" && !dialogoAberto()) $("#comecar").focus();
}

function atualizarResumo() {
  const c = configInicio();
  $("#personalizado").hidden = c.ritmo !== "personalizado";
  $("#armageddon-inc").hidden = c.ritmo !== "armageddon";
  const cor = c.cor === "b" ? "Pretas" : "Brancas";
  let html;
  if (c.ritmo === "armageddon") {
    const minutos = c.cor === "b" ? 4 : 5;
    const plano = c.motor === "stockfish"
      ? (c.cor === "b"
        ? "Se algum lance empatar na hora, o motor joga esse lance."
        : "O motor não joga lance que empata na hora nem deixa o adversário empatar no lance seguinte.")
      : `O motor vai ${c.cor === "b" ? "procurar o empate (contempt −100)" : "evitar o empate (contempt +100)"}.`;
    const incArm = parseFloat(String(c.inc_armageddon).replace(",", ".")) || 0;
    const semInc = incArm ? `${esc(c.inc_armageddon)} segundos de incremento` : "sem incremento";
    html = `Jogamos de ${cor}, Armageddon com ${minutos} minutos e ${semInc}.<br>
      <span class="armageddon">O empate classifica as Pretas. ${plano}</span>`;
  } else {
    const [minutos, inc] = c.ritmo === "oficial" ? [15, 10] : c.ritmo === "edital" ? [25, 0] : [c.minutos, c.incremento];
    html = `Jogamos de ${cor}, ${esc(minutos)} minutos e ${esc(inc)} segundos de incremento por lance.`;
  }
  document.querySelectorAll('#form-inicio input[name="desempenho"]').forEach((el) => {
    el.disabled = c.motor !== "stockfish";
    el.closest("label").classList.toggle("indisponivel", el.disabled);
  });
  if (c.motor === "stockfish") {
    const [threads, tabela] = (E.inicio && E.inicio.recursos && E.inicio.recursos[c.desempenho]) || ["?", "?"];
    const total = c.desempenho === "ultra" ? " (força total)" : "";
    html += `<br>Motor: Stockfish na busca e na avaliação, com a nossa gestão de tempo.
      Desempenho ${NOME_DESEMPENHO[c.desempenho]}${total}: ${threads} ${threads === 1 ? "thread" : "threads"} e ${tabela} MB.`;
  } else {
    html += "<br>Motor: o próprio da equipe (reserva), que usa um processador só.";
  }
  html += `<br><span class="fraco">Pondering ${c.ponder ? "ligado" : "desligado"}. Operador: ${esc(c.operador)} s por lance.</span>`;
  $("#resumo").innerHTML = html;
}

function teclaInicio(ev) {
  if (dialogoAberto() || ev.ctrlKey || ev.altKey || ev.metaKey) return;
  const alvo = document.activeElement;
  if (alvo && alvo.tagName === "INPUT" && alvo.type === "text") return;  // digitando um número
  const k = ev.key.toLowerCase();
  const marcar = (nome, valor) => {
    const el = document.querySelector(`#form-inicio input[name="${nome}"][value="${valor}"]`);
    if (el) {
      el.checked = true;
      atualizarResumo();
    }
  };
  if (k === "b") marcar("cor", "w");
  else if (k === "p") marcar("cor", "b");
  else if (["1", "2", "3", "4"].includes(k)) marcar("ritmo", RITMOS[Number(k) - 1]);
  else if (k === "o") {
    $("#ponder").checked = !$("#ponder").checked;
    atualizarResumo();
  } else if (k === "m") {
    const sf = document.querySelector('#form-inicio input[name="motor"][value="stockfish"]');
    if (!sf.disabled) marcar("motor", sf.checked ? "proprio" : "stockfish");
  } else if (k === "d") {
    const atual = configInicio();
    if (atual.motor === "stockfish") marcar("desempenho", { ultra: "medio", medio: "baixo", baixo: "ultra" }[atual.desempenho]);
  } else if (k === "enter" && (!alvo || alvo === document.body)) {
    ev.preventDefault();
    $("#form-inicio").requestSubmit();
  }
}

// ---------------------------------------------------------------- tabuleiro
function desenharTabuleiro() {
  const tabuleiro = $("#tabuleiro");
  const brancasEmbaixo = E.nossa_cor === "w";
  const interativo = digitavel();
  tabuleiro.classList.toggle("interativo", interativo);
  const casas = [];
  for (let linha = 0; linha < 8; linha++) {
    for (let coluna = 0; coluna < 8; coluna++) {
      const f = brancasEmbaixo ? coluna : 7 - coluna;
      const r = brancasEmbaixo ? 7 - linha : linha;
      const casa = COLUNAS[f] + (r + 1);
      const peca = E.pecas[casa];
      const classes = ["casa", (f + r) % 2 === 1 ? "clara" : "escura"];
      if (E.ultimo && E.ultimo.includes(casa)) classes.push("ultimo");
      if (interativo && casa === selecao) classes.push("selecionada");
      if (interativo && destinos.has(casa)) classes.push(peca ? "captura" : "destino");
      if (casa === E.xeque) classes.push("xeque");
      let conteudo = peca ? `<img class="peca" alt="" src="${pecas[peca] || ""}">` : "";
      if (coluna === 0) conteudo += `<span class="coord fileira">${r + 1}</span>`;
      if (linha === 7) conteudo += `<span class="coord coluna">${COLUNAS[f]}</span>`;
      casas.push(`<div class="${classes.join(" ")}" data-casa="${casa}">${conteudo}</div>`);
    }
  }
  tabuleiro.innerHTML = casas.join("");
}

function cliqueNoTabuleiro(ev) {
  const el = ev.target.closest(".casa");
  if (!el || !digitavel()) return;
  const casa = el.dataset.casa;
  const peca = E.pecas[casa];
  const doLado = peca && ((peca === peca.toUpperCase()) === (E.vez === "w"));
  const entrada = $("#entrada");
  if (doLado) entrada.value = casa;
  else if (selecao && destinos.has(casa)) entrada.value = selecao + casa;
  else entrada.value = "";
  focarEntrada();
  lerEntrada(0);
}

// ---------------------------------------------------------------- placa Agora
function desenharAgora() {
  const titulo = $("#agora-titulo");
  let html = "";
  if (E.fase === "pensando") {
    titulo.textContent = "Agora: o motor está pensando";
    html = `<p class="forte">Aguarde o lance do motor.</p>
      <div class="progresso"><div id="progresso"></div></div>
      <p class="fraco" id="progresso-texto"></p>
      <p><span class="tecla">Espaço:</span> joga agora com o melhor lance encontrado até aqui.</p>`;
  } else if (E.fase === "adversario" && E.ultimo_nosso) {
    const u = E.ultimo_nosso.uci;
    const partes = [u.slice(0, 2), u.slice(2, 4)];
    if (u.length > 4) partes.push(u.slice(4));
    titulo.textContent = "Agora: anuncie o nosso lance";
    html = `<div class="lance-grande">${partes.map((p) => `<span>${esc(p.toUpperCase())}</span>`).join("")}</div>
      <ol class="passos">
        <li>Anuncie ${esc(u)} em voz alta e espere o adversário repetir.</li>
        <li>No tabuleiro: ${esc(E.ultimo_nosso.descricao)}.</li>
        <li>Aperte o relógio.</li>
        <li>Digite abaixo o lance que o adversário anunciar.</li>
      </ol>`;
  } else if (E.fase === "adversario") {
    titulo.textContent = "Agora: vez do adversário";
    html = `<p class="forte">Aguardando o lance do adversário.</p>
      <p class="fraco">Digite abaixo o lance anunciado e aperte Enter. Também dá para clicar na peça e depois no destino.</p>`;
  } else if (E.fase === "pausado") {
    titulo.textContent = "Agora: nossa vez, motor pausado";
    html = `<p class="forte">O motor está esperando.</p>
      <p><span class="tecla">Enter:</span> o motor pensa agora.</p>
      <p><span class="tecla">F9:</span> registra um lance que a equipe já jogou no tabuleiro.</p>
      <p><button type="button" class="primario" id="botao-pensar">Pensar agora</button></p>`;
  } else if (E.fase === "manual") {
    titulo.textContent = "Agora: lance manual";
    html = `<p class="forte">Digite o lance que a nossa equipe jogou no tabuleiro.</p>
      <p class="fraco">Ele vai para o log como MANUAL. Esc volta sem registrar.</p>`;
  } else if (E.fase === "fim") {
    titulo.textContent = "Agora: partida encerrada";
    html = `<p class="destaque">${esc(E.resultado ? E.resultado.frase : "")}</p>
      <p class="fraco">Entregue o log à banca. Desfazer (F2) volta o último lance se houve engano.</p>
      <p><button type="button" class="primario" id="botao-nova">Nova partida</button></p>`;
  }
  for (const alerta of E.alertas || []) html += `<p class="alerta">${esc(alerta)}</p>`;
  $("#agora-corpo").innerHTML = html;
  const pensar = $("#botao-pensar");
  if (pensar) pensar.onclick = () => api("/api/pensar");
  const nova = $("#botao-nova");
  if (nova) nova.onclick = () => api("/api/inicio");
  tique();
}

// ---------------------------------------------------------------- relógios
function desenharRelogios() {
  $("#relogio-nosso h2").textContent = `Nós, ${E.nome_cor}`;
  $("#relogio-adv h2").textContent = `Adversário, ${E.cor_adv}`;
  $("#relogio-nosso").classList.toggle("ativo", E.relogio.correndo === "nosso");
  $("#relogio-adv").classList.toggle("ativo", E.relogio.correndo === "adv");
}

function tique() {
  if (!E || E.fase === "inicio" || desligada) return;
  const { nosso, adv } = relogiosAgora();
  $("#relogio-nosso .tempo").textContent = fmtRelogio(nosso);
  $("#relogio-adv .tempo").textContent = fmtRelogio(adv);
  $("#relogio-nosso").classList.toggle("pouco", nosso < 60);
  if (E.fase === "pensando" && E.pensando) {
    const decorrido = E.pensando.decorrido + (performance.now() - recebidoEm) / 1000;
    const barra = $("#progresso");
    const texto = $("#progresso-texto");
    if (barra && E.pensando.alvo > 0) {
      barra.style.width = `${Math.min(100, (100 * decorrido) / E.pensando.alvo)}%`;
      texto.textContent = `${fmtTempo(decorrido)} de cerca de ${fmtTempo(E.pensando.alvo)}, no máximo ${fmtTempo(E.pensando.maximo)}.`;
    }
  }
}

// ---------------------------------------------------------------- motor e súmula
function desenharMotor() {
  const m = E.motor;
  let html = "";
  if (!m) {
    html = '<p class="vazio">O motor ainda não jogou nesta partida.</p>';
  } else if (m.procurando) {
    html = '<p class="vazio">Procurando o lance...</p>';
  } else {
    if (m.origem === "BOOK") html += '<p class="forte">Lance do livro de aberturas, sem busca.</p>';
    else if (m.origem === "SYZYGY_TB") html += '<p class="forte">Lance de tablebase: resultado teórico exato.</p>';
    else if (m.origem === "EMERGENCY") {
      html += '<p class="emergencia">Lance de emergência: a busca falhou e o lance saiu da avaliação em 1 lance.</p>';
    } else if (m.origem === "REGRA_EMPATE") {
      html += '<p class="forte">Regra do empate imediato (Armageddon ou contempt): lance escolhido pela regra da equipe.</p>';
    } else {
      html += `<p><strong>Profundidade ${m.profundidade}</strong>
        <span class="fraco">(seletiva ${m.seletiva}), ${esc(m.nos)} nós, ${esc(m.nps)} por segundo, ${esc(m.tempo)}</span></p>`;
    }
    const largura = Math.round(Math.abs(m.barra) * 50);
    html += `<p>Avaliação <strong>${esc(m.avaliacao)}</strong></p>
      <div class="barra-aval"><span>adversário</span>
        <div class="trilho"><div class="cheio ${m.barra >= 0 ? "bom" : "ruim"}" style="width:${largura}%"></div><div class="meio"></div></div>
        <span>nós</span></div>
      <p><span class="fraco">Linha principal</span> <span class="pv">${esc(m.pv)}</span></p>`;
    if (m.credito) html += `<p class="fraco">Ponder acertou: ${esc(m.credito)} pensados no tempo do adversário contaram como já gastos.</p>`;
    if (m.empate_morto) html += '<p class="fraco">Empate morto (nota 0,00 estável): este lance usou 30% do tempo.</p>';
  }
  if (E.fase === "adversario" && E.ponder.ligado) {
    const previsto = E.ponder.previsto ? `, esperando ${esc(E.ponder.previsto)}` : "";
    html += `<p class="fraco">Ponder: pensando no tempo do adversário${previsto}.</p>`;
  }
  $("#motor-corpo").innerHTML = html;
}

function desenharSumula() {
  const lista = $("#sumula-lista");
  if (!E.sumula.length) {
    lista.innerHTML = '<p class="vazio">Nenhum lance ainda.</p>';
    return;
  }
  const celula = (l) => {
    if (!l) return "";
    const marca = l.marca ? `<span class="marca">${esc(l.marca)}</span>` : "";
    return `<span class="${l.nosso ? "nosso" : ""}">${esc(l.uci)}</span>${marca}`;
  };
  const linhas = E.sumula.map((x) => `<tr><td class="num">${x.num}.</td>
    <td>${x.brancas ? celula(x.brancas) : '<span class="fraco">...</span>'}</td><td>${celula(x.pretas)}</td></tr>`);
  lista.innerHTML = `<table class="sumula">${linhas.join("")}</table>`;
  lista.scrollTop = lista.scrollHeight;
}

// ---------------------------------------------------------------- entrada de lance
function desenharEntrada() {
  const pode = digitavel();
  const entrada = $("#entrada");
  entrada.disabled = !pode;
  $("#registrar").disabled = !pode;
  entrada.classList.toggle("manual", E.fase === "manual");
  $("#rotulo-entrada").textContent = E.fase === "manual" ? "Nosso lance (manual)" : "Lance do adversário";
  entrada.placeholder = pode ? (E.fase === "manual" ? "ex.: g1f3" : "ex.: e7e5") : "";
  if (!pode || !entrada.value) previa(null);
}

function previa(leitura) {
  const p = $("#previa");
  p.className = "";
  if (!digitavel()) {
    p.textContent = (E && MENSAGEM_FASE[E.fase]) || "";
  } else if (!leitura || leitura.estado === "vazio") {
    p.textContent = DICA_ENTRADA;
  } else if (leitura.estado === "ok") {
    p.className = "ok";
    p.textContent = `✓ ${leitura.mensagem}. Enter registra.`;
  } else if (leitura.estado === "promocao") {
    p.className = "promocao";
    p.textContent = `▲ ${leitura.mensagem}`;
  } else if (leitura.estado === "erro") {
    p.className = "erro";
    p.textContent = `✗ ${leitura.mensagem}`;
  } else {
    p.textContent = leitura.mensagem;
  }
}

function lerEntrada(espera = 60) {
  clearTimeout(temporizadorLeitura);
  temporizadorLeitura = setTimeout(async () => {
    const texto = $("#entrada").value;
    const r = await api("/api/ler", { texto }, { atualizar: false, silencioso: true });
    if (!r || r.erro || $("#entrada").value !== texto) return;
    selecao = r.origem;
    destinos = new Set(r.destinos || []);
    previa(r);
    desenharTabuleiro();
  }, espera);
}

async function enviarLance(ev) {
  ev.preventDefault();
  const texto = $("#entrada").value.trim();
  if (!texto || !digitavel()) return;
  const r = await api("/api/lance", { texto });
  if (!r) {
    bipe("erro");
    return;
  }
  if (r.promocao) {
    textoPromocao = texto;
    $("#promocao-pergunta").textContent =
      E.fase === "manual" ? "Para qual peça nós promovemos?" : "Qual peça o adversário escolheu?";
    abrir("#dlg-promocao");
    return;
  }
  limparEntrada();
}

function limparEntrada() {
  $("#entrada").value = "";
  selecao = null;
  destinos = new Set();
  previa(null);
  if (E && E.fase !== "inicio") desenharTabuleiro();
}

// ---------------------------------------------------------------- ações e diálogos
function desenharAcoes() {
  const botao = (nome) => document.querySelector(`#acoes [data-acao="${nome}"]`);
  botao("economia").classList.toggle("ativo-toggle", E.ajustes.economia);
  botao("ponder").classList.toggle("ativo-toggle", E.ajustes.ponder);
  botao("manual").disabled = !(E.fase === "pensando" || E.fase === "pausado");
  botao("desfazer").disabled = !E.desfazer.length;
}

function acao(nome) {
  if (!E || E.fase === "inicio") return;
  if (nome === "ajuda") abrir("#dlg-ajuda");
  else if (nome === "desfazer") abrirDesfazer();
  else if (nome === "relogio") abrirRelogio();
  else if (nome === "ajustes") abrirAjustes();
  else if (nome === "posicao") abrirPosicao();
  else if (nome === "economia") api("/api/economia");
  else if (nome === "avaliar") api("/api/avaliar");
  else if (nome === "ponder") api("/api/ponder");
  else if (nome === "encerrar") abrir("#dlg-encerrar");
  else if (nome === "manual") {
    if (E.fase === "pensando") abrir("#dlg-manual");
    else if (E.fase === "pausado") api("/api/manual");
    else avisar("Agora é a vez do adversário. Para trocar o nosso último lance, use Desfazer (F2).", "info");
  }
}

function abrirDesfazer() {
  if (!E.desfazer.length) {
    avisar("Não há lance para desfazer.", "info");
    return;
  }
  const ultimo = E.desfazer[E.desfazer.length - 1];
  const quem = ultimo.nosso === null ? "" : ultimo.nosso ? " (nosso)" : " (do adversário)";
  desfazerEsperado = ultimo.uci;
  $("#desfazer-texto").textContent = `Último meio-lance: ${ultimo.uci}${quem}.`;
  $("#desfazer-dois").hidden = E.desfazer.length < 2;
  abrir("#dlg-desfazer");
}

function abrirRelogio(aviso) {
  const { nosso, adv } = relogiosAgora();
  $("#relogio-nosso-valor").value = fmtRelogio(nosso);
  $("#relogio-adv-valor").value = fmtRelogio(adv);
  $("#relogio-rotulo-nosso").textContent = `Nós (${E.nome_cor})`;
  $("#relogio-rotulo-adv").textContent = `Adversário (${E.cor_adv})`;
  $("#relogio-aviso").textContent = aviso || "Digite o que o relógio físico mostra agora. Ele é a referência.";
  $("#relogio-erro").textContent = "";
  relogioConfirmado = null;
  abrir("#dlg-relogio");
  $("#relogio-nosso-valor").select();
}

function abrirAjustes() {
  const a = E.ajustes;
  $("#aj-operador").value = fmtNumero(a.operador);
  $("#aj-fator").value = fmtNumero(a.fator);
  $("#aj-contempt").value = a.contempt;
  $("#aj-ponder").checked = a.ponder;
  $("#aj-som").checked = a.som;
  $("#ajustes-erro").textContent = "";
  abrir("#dlg-ajustes");
}

function abrirPosicao() {
  $("#fen").value = E.fen;
  validarFen();
  abrir("#dlg-posicao");
  $("#fen").select();
}

async function validarFen() {
  const r = await api("/api/validar-fen", { fen: $("#fen").value }, { atualizar: false, silencioso: true });
  if (!r || r.erro) return;
  const p = $("#fen-validacao");
  p.textContent = r.mensagem;
  p.style.color = r.valido ? "var(--feltro)" : "var(--bandeira)";
}

async function aplicarPosicao(fen) {
  const r = await apiDialogo("/api/posicao", { fen }, "#fen-validacao");
  if (r) $("#dlg-posicao").close("ok");
}

function abrirFim() {
  $("#fim-frase").textContent = E.resultado ? E.resultado.frase : "";
  $("#fim-log").innerHTML = `${esc(E.log.arquivo)}<br>${esc(E.log.espelho)}`;
  abrir("#dlg-fim");
}

function teclaDialogo(ev, mapa) {
  const valor = mapa[ev.key.toLowerCase()];
  if (valor) {
    ev.preventDefault();
    ev.currentTarget.close(valor);
  }
}

// ============================================================================ teclado
function teclado(ev) {
  if (!E || desligada) return;
  if (E.fase === "inicio") {
    teclaInicio(ev);
    return;
  }
  const k = ev.key;
  if (TECLAS_F[k]) {
    ev.preventDefault();  // F1, F3, F5 e F7 do navegador ficam desligadas nesta página
    if (!dialogoAberto()) acao(TECLAS_F[k]);
    return;
  }
  if (dialogoAberto() || ev.ctrlKey || ev.altKey || ev.metaKey) return;
  const noCampo = document.activeElement === $("#entrada");
  if (k === " " && E.fase === "pensando" && !noCampo) {
    ev.preventDefault();
    api("/api/jogar-agora").then((r) => r && avisar("Jogando com o melhor lance encontrado até agora.", "info"));
  } else if (k === "Enter" && E.fase === "pausado" && !noCampo) {
    ev.preventDefault();
    api("/api/pensar");
  } else if (k === "Escape") {
    if (E.fase === "manual") api("/api/cancelar-manual");
    else limparEntrada();
  } else if (!noCampo && digitavel() && k.length === 1 && /[a-h1-8qrbnkoO0x-]/i.test(k)) {
    focarEntrada();  // começa a digitar o lance sem precisar clicar no campo
  }
}

// ============================================================================ ligações
function ligarEventos() {
  document.addEventListener("keydown", teclado);
  document.addEventListener("pointerdown", desbloquearAudio, { once: true });
  setInterval(tique, 200);

  $("#form-inicio").addEventListener("input", atualizarResumo);
  $("#form-inicio").addEventListener("submit", (ev) => {
    ev.preventDefault();
    desbloquearAudio();
    api("/api/nova", configInicio());
  });
  $("#retomar").addEventListener("click", () => {
    desbloquearAudio();
    api("/api/retomar");
  });

  $("#tabuleiro").addEventListener("click", cliqueNoTabuleiro);
  $("#form-lance").addEventListener("submit", enviarLance);
  $("#entrada").addEventListener("input", () => lerEntrada());
  document.querySelectorAll("#acoes [data-acao]").forEach((b) => b.addEventListener("click", () => {
    b.blur();
    acao(b.dataset.acao);
  }));
  document.querySelectorAll("[data-fechar]").forEach((b) => b.addEventListener("click", () => {
    b.closest("dialog").close("cancelar");
  }));

  $("#dlg-desfazer").addEventListener("keydown", (ev) => teclaDialogo(ev, { 1: "1", 2: E && E.desfazer.length > 1 ? "2" : null }));
  $("#dlg-desfazer").addEventListener("close", () => {
    const v = $("#dlg-desfazer").returnValue;
    if (v === "1" || v === "2") api("/api/desfazer", { n: Number(v), ultimo: desfazerEsperado });
  });

  $("#form-relogio").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const valores = { nosso: $("#relogio-nosso-valor").value, adv: $("#relogio-adv-valor").value };
    const chave = `${valores.nosso}|${valores.adv}`;
    // Valor suspeito (ex.: 1230 no lugar de 12:30): o servidor pede confirmação; o mesmo valor de novo confirma
    const r = await apiDialogo("/api/relogio", { ...valores, confirmar: relogioConfirmado === chave }, "#relogio-erro");
    if (r && r.confirmar) {
      relogioConfirmado = chave;
      $("#relogio-erro").textContent = r.confirmar;
      $("#relogio-erro").style.color = "var(--bandeira)";
      return;
    }
    relogioConfirmado = null;
    if (r) $("#dlg-relogio").close("ok");
  });

  $("#form-ajustes").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const r = await apiDialogo("/api/ajustes", {
      operador: $("#aj-operador").value, fator: $("#aj-fator").value, contempt: $("#aj-contempt").value,
      ponder: $("#aj-ponder").checked, som: $("#aj-som").checked,
    }, "#ajustes-erro");
    if (r) $("#dlg-ajustes").close("ok");
  });

  $("#fen").addEventListener("input", () => {
    clearTimeout(temporizadorFen);
    temporizadorFen = setTimeout(validarFen, 120);
  });
  $("#form-posicao").addEventListener("submit", (ev) => {
    ev.preventDefault();
    aplicarPosicao($("#fen").value);
  });
  $("#posicao-inicial").addEventListener("click", () => aplicarPosicao(POSICAO_INICIAL));

  $("#dlg-promocao").addEventListener("keydown", (ev) => teclaDialogo(ev,
    { q: "q", d: "q", r: "r", t: "r", b: "b", n: "n", c: "n" }));
  $("#dlg-promocao").addEventListener("close", async () => {
    const v = $("#dlg-promocao").returnValue;
    if (["q", "r", "b", "n"].includes(v)) {
      const r = await api("/api/lance", { texto: textoPromocao, promocao: v });
      if (r && r.ok) limparEntrada();
    }
    focarEntrada();
  });

  $("#dlg-manual").addEventListener("close", () => {
    if ($("#dlg-manual").returnValue === "ok") api("/api/manual");
  });

  $("#dlg-encerrar").addEventListener("close", async () => {
    const v = $("#dlg-encerrar").returnValue;
    if (["vitoria", "derrota", "empate"].includes(v)) {
      api("/api/encerrar", { escolha: v });
    } else if (v === "sair") {
      const r = await api("/api/encerrar", { escolha: "sair" }, { atualizar: false });
      if (r && r.saindo) mostrarDesligada();
    }
  });

  $("#copiar-log").addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(E.log.arquivo);
      avisar("Caminho do log copiado.", "ok");
    } catch (erro) {
      avisar("Não deu para copiar. O caminho está escrito no diálogo.", "info");
    }
  });
  $("#dlg-fim").addEventListener("close", () => {
    const v = $("#dlg-fim").returnValue;
    if (v === "desfazer") {
      const ultimo = E.desfazer.length ? E.desfazer[E.desfazer.length - 1].uci : null;
      api("/api/desfazer", { n: 1, ultimo });
    } else if (v === "nova") {
      api("/api/inicio");
    }
  });
}

async function iniciar() {
  try {
    const resposta = await fetch("/api/pecas");
    const desenhos = await resposta.json();
    for (const [simbolo, svg] of Object.entries(desenhos)) {
      pecas[simbolo] = `data:image/svg+xml;charset=utf-8,${encodeURIComponent(svg)}`;
    }
  } catch (erro) { /* sem desenho, a peça fica vazia até a próxima carga */ }
  ligarEventos();
  ciclo();
}

iniciar();
