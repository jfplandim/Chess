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
5. Nunca perder por tempo: margem de segurança fixa + modo de emergência.
"""

import time
import chess


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

        # Modo de emergência (relógio muito baixo)
        if remaining < 20.0:
            optimum = min(optimum, remaining / 25.0 + 0.4 * self.increment)

        hard = min(optimum * 3.5, remaining * 0.18 + 0.5 * self.increment, afford)
        hard = max(hard, 0.05)
        optimum = min(optimum, hard)

        self.optimum = max(0.02, optimum)
        self.soft_limit = self.optimum
        self.hard_limit = max(self.optimum, hard)
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
        return self.elapsed() >= self.soft_limit * 0.6

    def should_abort_search(self) -> bool:
        return self.elapsed() >= self.hard_limit

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
