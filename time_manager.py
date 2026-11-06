"""
Módulo de Gestão de Tempo Dinâmica - Autoria Própria
Chess Challenge III - ICEV 2026

Ideias principais (para a Arguição da Banca):
1. Tempo-alvo por lance ("optimum") = tempo útil / lances que faltam (estimativa),
   onde o tempo útil já considera os incrementos futuros E o custo do OPERADOR
   HUMANO em cada lance (anunciar, mover a peça física, apertar o relógio e
   digitar o lance do adversário). No Armageddon sem incremento isso é decisivo.
2. Limite máximo ("hard limit"): teto absoluto; a busca é interrompida na hora.
3. Gestão dinâmica durante o Iterative Deepening:
   - melhor lance mudando entre profundidades  -> pensa mais (instabilidade);
   - nota caindo / "fail-low" na raiz          -> pensa mais (perigo tático);
   - melhor lance estável por várias iterações -> economiza relógio.
4. Uma nova profundidade só começa se houver tempo provável para terminá-la.
5. Nunca perder por tempo: margem de segurança fixa + zona de sobrevivência com o relógio baixo
   (o alvo cai para metade do incremento líquido mais 1/25 do relógio; o resto do incremento
   reconstrói o relógio).
6. Crédito do ponder: quando o adversário joga o lance previsto, o tempo que o ponder já pensou
   NESTA posição conta como tempo já gasto no lance. A tabela de transposição guarda essa busca, então
   o motor volta à mesma profundidade em milissegundos e o lance sai bem mais rápido, sem perder força.
   No desempate por tempo (FAQ 12) esse relógio poupado conta a nosso favor.
7. Empate morto: se as duas últimas buscas deram nota exatamente 0 com profundidade alta (o Stockfish
   vê empate forçado: repetição, fortaleza, material insuficiente), o lance usa 30% do tempo normal.
"""

import math
import re
import time

import chess

# Crédito do ponder (ideia 6)
CREDITO_PISO = 0.15          # mesmo com o crédito, pensa pelo menos 15% do alvo (confirma com a tabela quente)
CREDITO_PISO_MINIMO = 0.2    # e nunca menos de 0,2 s
CREDITO_PARADA = 0.5         # com o crédito, para quando ponder + agora chega a metade do alvo do lance

# Empate morto (ideia 7)
EMPATE_MORTO_PROFUNDIDADE = 18
EMPATE_MORTO_FATOR = 0.3


class TimeManager:
    def __init__(
        self,
        initial_minutes: float = 15.0,
        increment_seconds: float = 10.0,
        move_overhead: float = 0.3,
        operator_overhead: float = 0.0,
    ):
        """
        :param initial_minutes: tempo inicial em minutos (15 pelo FAQ, 25 pelo Edital).
        :param increment_seconds: incremento por lance (10 s pelo FAQ).
        :param move_overhead: margem de segurança por lance (latência do computador).
        :param operator_overhead: segundos gastos FORA do motor em cada lance nosso
                                  (anúncio + tabuleiro físico + relógio + digitação).
        """
        self.initial_time = initial_minutes * 60.0
        self.increment = increment_seconds
        self.move_overhead = move_overhead
        self.operator_overhead = operator_overhead

        self.remaining_time = self.initial_time
        self.opponent_time = self.initial_time
        self.moves_to_go: int | None = None   # UCI "movestogo" (controles clássicos)
        self.time_factor = 1.0                # <1 economiza relógio (ex.: desempate por tempo), >1 pensa mais
        self.fixed_time: float | None = None  # UCI "movetime"

        self.start_time = time.perf_counter()
        self.optimum = 5.0
        self.soft_limit = 5.0
        self.hard_limit = 15.0
        self._scale = 1.0
        self._stable_iterations = 0
        self._changes = 0.0
        self._fail_low = False

        # Crédito do ponder: definido antes da busca do lance (dar_credito_ponder) e usado uma vez
        self.credito_ponder = 0.0     # segundos que o ponder pensou na posição que apareceu no tabuleiro
        self._credito = 0.0           # crédito em uso na busca atual
        self._piso = 0.0              # com crédito, a busca não para antes disto
        # Empate morto: (nota, profundidade, origem) das nossas últimas buscas
        self.ultimas_buscas: list[tuple[int, int, str]] = []
        self.economia_empate_morto = False  # o lance atual está no modo empate morto (para a interface)

    # ------------------------------------------------------------------
    def dar_credito_ponder(self, segundos: float) -> None:
        """O adversário jogou o lance previsto: o ponder já pensou 'segundos' nesta posição."""
        self.credito_ponder = max(0.0, float(segundos))

    def registrar_busca(self, score_cp: int, depth: int, source: str) -> None:
        """Guarda a nota e a profundidade do nosso lance (empate morto, ideia 7)."""
        self.ultimas_buscas = (self.ultimas_buscas + [(int(score_cp), int(depth), source)])[-2:]

    def limpar_historico(self) -> None:
        """Depois de desfazer, mudar a posição ou retomar: as últimas buscas e o crédito não valem mais."""
        self.ultimas_buscas = []
        self.credito_ponder = 0.0

    def empate_morto(self) -> bool:
        """As duas últimas buscas nossas deram exatamente 0 com profundidade alta."""
        return len(self.ultimas_buscas) == 2 and all(
            nota == 0 and prof >= EMPATE_MORTO_PROFUNDIDADE and origem == "SEARCH"
            for nota, prof, origem in self.ultimas_buscas
        )

    # ------------------------------------------------------------------
    def sync_clocks(self, our_seconds: float, opp_seconds: float | None = None) -> None:
        """Sincroniza manualmente o relógio interno com o relógio físico da mesa."""
        self.remaining_time = max(0.5, float(our_seconds))
        if opp_seconds is not None:
            self.opponent_time = max(0.5, float(opp_seconds))

    def set_fixed_time(self, seconds: float | None) -> None:
        self.fixed_time = seconds

    def allocate_time(self, board: chess.Board, legal_moves_count: int) -> tuple[float, float]:
        """Calcula optimum/soft_limit e hard_limit (em segundos) para o lance atual."""
        self.start_time = time.perf_counter()
        self._scale = 1.0
        self._stable_iterations = 0
        self._changes = 0.0
        self._fail_low = False
        # O crédito do ponder vale só para este lance
        credito, self.credito_ponder = self.credito_ponder, 0.0
        self._credito = self._piso = 0.0
        self.economia_empate_morto = False

        if self.fixed_time is not None:
            t = max(0.02, self.fixed_time - self.move_overhead)
            self.optimum = self.soft_limit = self.hard_limit = t
            return self.soft_limit, self.hard_limit

        remaining = max(0.05, self.remaining_time - self.move_overhead)
        # Quanto podemos pensar AGORA sem estourar: o operador ainda vai anunciar,
        # mover a peça e apertar o relógio depois que o motor responder.
        afford = max(0.05, remaining - self.operator_overhead - 0.3)

        # Lance forçado: responde quase instantaneamente
        if legal_moves_count <= 1:
            self.optimum = self.soft_limit = min(0.1, afford * 0.5)
            self.hard_limit = min(0.3, afford)
            return self.soft_limit, self.hard_limit

        # Estimativa de quantos lances ainda vamos jogar
        move_num = board.fullmove_number
        mtg = max(18.0, 48.0 - 0.5 * move_num)
        if self.moves_to_go:
            mtg = max(1.0, min(mtg, float(self.moves_to_go)))

        # Tempo útil: relógio + incrementos futuros - custo do operador - reserva
        reserve = min(remaining * 0.04, 8.0)
        usable = remaining - reserve + (mtg - 1.0) * (self.increment - self.operator_overhead)
        usable = max(usable, remaining * 0.05)
        optimum = usable / mtg * self.time_factor

        # Empate morto (ideia 7): pensar muito não muda um empate forçado, e cada segundo poupado
        # conta no desempate por tempo. O limite rígido continua proporcional ao alvo, então uma
        # surpresa (nota caindo, lance mudando) ainda ganha mais tempo.
        if self.empate_morto():
            optimum *= EMPATE_MORTO_FATOR
            self.economia_empate_morto = True

        # Zona de sobrevivência (relógio baixo): com poucos lances de folga no relógio, o alvo cai
        # para METADE do incremento líquido (incremento - operador) mais relógio/25. O resto do
        # incremento reconstrói o relógio a cada lance, mesmo que o operador demore um pouco mais
        # do que o configurado. 15+10 com operador de 4 s: abaixo de 42 s, alvo de no máximo
        # 3 s + relógio/25 e limite rígido de no máximo 6 s.
        liquido = max(0.0, self.increment - self.operator_overhead)
        zona = max(20.0, 6.0 * (self.operator_overhead + 0.5 * liquido))
        if remaining < zona:
            optimum = min(optimum, remaining / 25.0 + 0.5 * liquido)

        hard = min(optimum * 3.5, remaining * 0.18 + 0.5 * self.increment, afford)
        if remaining < zona:
            # Nem no pior caso (posição instável, busca até o limite) o relógio desce na zona:
            # pensar no máximo o incremento líquido mantém o relógio onde está.
            hard = min(hard, max(optimum, liquido))
        hard = max(hard, 0.05)
        optimum = min(optimum, hard)

        # Crédito do ponder (ideia 6): o tempo do ponder nesta posição conta como já gasto. Com a
        # tabela quente o motor volta à profundidade do ponder em milissegundos; o piso garante uma
        # busca curta de confirmação, e o limite rígido também desconta o crédito (ele precisa parar
        # no meio de uma profundidade: com a tabela quente, a profundidade seguinte demora segundos).
        if credito > 0:
            self._piso = min(hard, max(CREDITO_PISO_MINIMO, CREDITO_PISO * optimum))
            self._credito = credito
            hard = max(self._piso, hard - credito)

        self.optimum = max(0.02, optimum)
        self.soft_limit = self.optimum
        self.hard_limit = max(self.optimum if not self._credito else self._piso, hard)
        return self.soft_limit, self.hard_limit

    # ------------------------------------------------------------------
    def adjust_for_instability(self, best_move_changed: bool, score_drop: int, depth: int) -> None:
        """Atualiza o fator de escala do tempo depois de cada profundidade concluída."""
        if self.fixed_time is not None:
            return
        self._changes *= 0.5  # mudanças antigas pesam menos
        if best_move_changed and depth >= 4:
            self._changes += 1.0
            self._stable_iterations = 0
        else:
            self._stable_iterations += 1

        scale = 1.0 + 0.6 * self._changes
        if depth >= 5:
            if score_drop >= 25:
                scale *= 1.25
            if score_drop >= 60:
                scale *= 1.25
            if self._fail_low:
                scale *= 1.2
        if self._stable_iterations >= 4 and depth >= 7:
            scale *= 0.7
        self._scale = max(0.4, min(scale, 2.5))
        self._fail_low = False
        self.soft_limit = min(self.hard_limit, self.optimum * self._scale)

    def notify_fail_low(self) -> None:
        """A busca na raiz falhou baixo (o melhor lance piorou): sinaliza perigo."""
        self._fail_low = True

    def elapsed(self) -> float:
        return time.perf_counter() - self.start_time

    def should_stop_iteration(self) -> bool:
        """
        Não inicia nova profundidade se o tempo já gasto torna improvável terminá-la
        antes do limite (cada profundidade custa ~2x a anterior).
        """
        if self.fixed_time is not None:
            return self.elapsed() >= self.hard_limit * 0.6
        if self._credito:
            return self._parar_com_credito()
        return self.elapsed() >= self.soft_limit * 0.6

    def should_abort_search(self) -> bool:
        if self._credito and self._parar_com_credito():
            return True
        return self.elapsed() >= self.hard_limit

    def _parar_com_credito(self) -> bool:
        """Ponder acertou: para quando ponder + agora chega a metade do alvo (que cresce se o lance ficar instável)."""
        agora = self.elapsed()
        return agora >= self._piso and agora + self._credito >= CREDITO_PARADA * self.soft_limit

    # ------------------------------------------------------------------
    def register_move_time(self, elapsed_seconds: float) -> None:
        """Atualiza nosso relógio: desconta o tempo pensado + custo do operador e soma o incremento."""
        self.remaining_time = max(
            0.0, self.remaining_time - elapsed_seconds - self.operator_overhead + self.increment
        )

    def register_opponent_time(self, elapsed_seconds: float) -> None:
        self.opponent_time = max(0.0, self.opponent_time - elapsed_seconds + self.increment)

    @staticmethod
    def format_clock(seconds: float) -> str:
        seconds = max(0.0, seconds)
        mins = int(seconds // 60)
        secs = int(seconds % 60)
        return f"{mins:02d}:{secs:02d}"


_MM_SS = re.compile(r"(\d{1,3}):(\d{1,2})|(\d{1,3})[.,](\d{2})")  # 12:30, ou 12.30 digitado com ponto
_SEGUNDOS = re.compile(r"\d{1,5}([.,]\d)?")                         # 750, ou 19.5 (décimos, relógio digital)


def ler_relogio(texto: str) -> float:
    """
    O que o relógio físico mostra: MM:SS (12:30, também com ponto: 12.30) ou só segundos (750, ou
    19.5 com décimos). Levanta ValueError com a explicação para o operador (segundos de 00 a 59,
    nada de negativo, infinito ou letras).
    """
    t = texto.strip()
    m = _MM_SS.fullmatch(t)
    if m:
        minutos, segundos = (m.group(1), m.group(2)) if m.group(1) else (m.group(3), m.group(4))
        if int(segundos) >= 60:
            raise ValueError("Os segundos vão de 00 a 59, como 12:30.")
        return int(minutos) * 60.0 + int(segundos)
    if _SEGUNDOS.fullmatch(t):
        valor = float(t.replace(",", "."))
        if math.isfinite(valor):
            return valor
    raise ValueError("Não entendi o tempo. Use MM:SS, como 12:30, ou só segundos, como 750.")
