"""
Módulo de Gestão de Tempo Dinâmica - Autoria Própria
Chess Challenge III - ICEV 2026

Gerencia o orçamento de tempo do relógio (Padrão FAQ: 15 min + 10s de incremento;
Padrão Edital: 25 min) com foco em:
1. Nunca perder por tempo (margem de segurança para anúncio verbal e digitação).
2. Alocar mais tempo no meio-jogo tático (lances 10 a 30) e economizar em lances óbvios.
3. Preservar vantagem no relógio para o critério de desempate da Melhor de 3 (FAQ Item 12).
4. Estender o tempo dinamicamente se a avaliação cair ou se o melhor lance mudar na última profundidade.
"""

import time
import chess


class TimeManager:
    def __init__(
        self,
        initial_minutes: float = 15.0,
        increment_seconds: float = 10.0,
        move_overhead: float = 1.5,
    ):
        """
        Inicializa o gerenciador de tempo.
        :param initial_minutes: Tempo inicial em minutos (15.0 pelo FAQ ou 25.0 pelo Edital).
        :param increment_seconds: Incremento por lance em segundos (10.0 pelo FAQ).
        :param move_overhead: Margem de segurança em segundos para anúncio em voz alta/digitação.
        """
        self.initial_time = initial_minutes * 60.0
        self.increment = increment_seconds
        self.move_overhead = move_overhead

        self.remaining_time = self.initial_time
        self.opponent_time = self.initial_time

        self.start_time = 0.0
        self.soft_limit = 5.0
        self.hard_limit = 15.0

    def sync_clocks(self, our_seconds: float, opp_seconds: float | None = None) -> None:
        """Sincroniza manualmente o relógio interno com o relógio físico da mesa."""
        self.remaining_time = max(1.0, float(our_seconds))
        if opp_seconds is not None:
            self.opponent_time = max(1.0, float(opp_seconds))

    def allocate_time(self, board: chess.Board, legal_moves_count: int) -> tuple[float, float]:
        """
        Calcula o limite ideal (soft_limit) e o limite máximo absoluto (hard_limit)
        para o lance atual, em segundos.
        """
        self.start_time = time.perf_counter()

        # Se só existe 1 lance legal (ex: única fuga de xeque), joga quase instantaneamente!
        if legal_moves_count <= 1:
            self.soft_limit = 0.15
            self.hard_limit = 0.40
            return self.soft_limit, self.hard_limit

        # Tempo útil descontando margem de segurança de operação humana
        safe_remaining = max(1.0, self.remaining_time - self.move_overhead)

        # Modo de Emergência (menos de 20 segundos no relógio)
        if safe_remaining < 20.0:
            self.soft_limit = max(0.3, min(1.5, safe_remaining * 0.08 + self.increment * 0.5))
            self.hard_limit = max(0.6, min(3.0, safe_remaining * 0.20))
            return self.soft_limit, self.hard_limit

        # Estimativa dinâmica de quantos lances faltam para acabar a partida (Moves To Go)
        move_num = board.fullmove_number
        total_pieces = board.occupied.bit_count()

        if move_num <= 10:
            moves_to_go = 34.0
            phase_factor = 0.85  # Início pós-book: economiza um pouco de tempo
        elif move_num <= 28 and total_pieces >= 16:
            moves_to_go = 26.0
            phase_factor = 1.20  # Meio-jogo complexo: investe 20% a mais de tempo
        elif total_pieces > 8:
            moves_to_go = 22.0
            phase_factor = 1.00  # Transição para o final
        else:
            moves_to_go = 18.0
            phase_factor = 0.80  # Final com poucas peças: busca é naturalmente mais rápida

        # Fator de complexidade baseado na quantidade de lances legais disponíveis
        complexity_factor = 1.0
        if legal_moves_count >= 35:
            complexity_factor = 1.15
        elif legal_moves_count <= 8:
            complexity_factor = 0.75

        # Fórmula base: fração do tempo restante + 75% do incremento (guarda 25% do inc para o desempate!)
        base_time = (safe_remaining / moves_to_go) + (self.increment * 0.75)
        target_soft = base_time * phase_factor * complexity_factor

        # Limites de segurança para a partida presencial (pensamento ágil e profundo)
        # Soft limit fica normalmente entre 2s e 18s; Hard limit até 3.2x o soft limit (ou 20% do relógio)
        self.soft_limit = max(1.0, min(target_soft, safe_remaining * 0.08, 18.0))
        self.hard_limit = max(
            self.soft_limit * 1.5,
            min(self.soft_limit * 3.0, safe_remaining * 0.22, 35.0),
        )

        return self.soft_limit, self.hard_limit

    def adjust_for_instability(
        self, best_move_changed: bool, score_drop: int, depth: int
    ) -> None:
        """
        Ajusta dinamicamente o soft_limit durante o Iterative Deepening:
        - Se o melhor lance mudou em profundidade >= 4 ou a nota caiu muito (> 35 cp),
          aloca mais tempo (até o hard_limit) para não cometer erro tático.
        """
        if depth < 4:
            return

        factor = 1.0
        if best_move_changed:
            factor *= 1.25
        if score_drop >= 35:
            factor *= 1.30
        if score_drop >= 100:
            factor *= 1.25

        if factor > 1.0:
            self.soft_limit = min(self.hard_limit, self.soft_limit * factor)

    def elapsed(self) -> float:
        """Retorna o tempo gasto até o momento no lance atual (em segundos)."""
        return time.perf_counter() - self.start_time

    def should_stop_iteration(self) -> bool:
        """Verifica se já passou do soft_limit (não deve iniciar nova profundidade)."""
        return self.elapsed() >= self.soft_limit

    def should_abort_search(self) -> bool:
        """Verifica se atingiu o hard_limit (deve abortar a busca imediatamente)."""
        return self.elapsed() >= self.hard_limit

    def register_move_time(self, elapsed_seconds: float) -> None:
        """Atualiza o nosso relógio após efetuar o lance (desconta tempo gasto e soma incremento)."""
        self.remaining_time = max(0.0, self.remaining_time - elapsed_seconds + self.increment)

    def register_opponent_time(self, elapsed_seconds: float) -> None:
        """Atualiza o relógio estimado do adversário."""
        self.opponent_time = max(0.0, self.opponent_time - elapsed_seconds + self.increment)

    @staticmethod
    def format_clock(seconds: float) -> str:
        """Formata segundos como MM:SS para exibição no painel."""
        seconds = max(0.0, seconds)
        mins = int(seconds // 60)
        secs = int(seconds % 60)
        return f"{mins:02d}:{secs:02d}"
