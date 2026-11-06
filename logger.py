"""
Módulo de Registro de Logs Obrigatório para a Banca (FAQ Item 9)
Chess Challenge III - ICEV 2026

Requisito oficial (FAQ Item 9):
"É obrigatório, porém, registrar log em .txt ou .log contendo, para cada lance:
número do lance, cor, lance escolhido em notação UCI, profundidade atingida,
nós avaliados, avaliação da posição, variante principal (PV) e tempo gasto.
O log será entregue à banca ao final de cada partida."
"""

import os
from datetime import datetime
import chess
from evaluation import MATE_THRESHOLD, MATE_SCORE


class MatchLogger:
    def __init__(self, base_dir: str | None = None, team_color: str = "BRANCAS"):
        if base_dir is None:
            base_dir = os.path.dirname(os.path.abspath(__file__))

        self.logs_dir = os.path.join(base_dir, "logs")
        os.makedirs(self.logs_dir, exist_ok=True)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.log_path = os.path.join(self.logs_dir, f"partida_{timestamp}.log")
        extra = 2
        while os.path.exists(self.log_path):  # duas partidas no mesmo segundo: não sobrescreve o log da banca
            self.log_path = os.path.join(self.logs_dir, f"partida_{timestamp}_{extra}.log")
            extra += 1
        self.txt_mirror_path = os.path.join(self.logs_dir, "ultima_partida_banca.txt")

        self._write_header(team_color)

    def _write_both(self, text: str, mode: str = "a") -> None:
        """Grava simultaneamente no arquivo .log com timestamp e no espelho .txt para entrega rápida."""
        for path in (self.log_path, self.txt_mirror_path):
            with open(path, mode, encoding="utf-8") as f:
                f.write(text)
                f.flush()

    def _write_header(self, team_color: str) -> None:
        now_str = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
        header = (
            "========================================================================================================\n"
            "                          CHESS CHALLENGE III - ICEV 2026 - LOG OFICIAL DA PARTIDA                      \n"
            "========================================================================================================\n"
            f"Data/Hora de Início : {now_str}\n"
            f"Cor da Equipe       : {team_color}\n"
            f"Arquivo de Log      : {os.path.basename(self.log_path)}\n"
            "--------------------------------------------------------------------------------------------------------\n"
            f"{'Nº':<4} | {'COR':<7} | {'LANCE (UCI)':<11} | {'PROF.':<6} | {'NÓS AVALIADOS':<14} | "
            f"{'AVALIAÇÃO':<16} | {'TEMPO':<8} | {'VARIANTE PRINCIPAL (PV)'}\n"
            "--------------------------------------------------------------------------------------------------------\n"
        )
        self._write_both(header, mode="w")

    @staticmethod
    def format_score(score_cp: int) -> str:
        """Formata a avaliação em centipawns/peões ou distância para Mate."""
        if abs(score_cp) >= MATE_THRESHOLD:
            plies_to_mate = MATE_SCORE - abs(score_cp)
            moves_to_mate = max(1, (plies_to_mate + 1) // 2)
            sign = "+" if score_cp > 0 else "-"
            return f"{sign}MATE em {moves_to_mate} ({score_cp:+d})"
        if abs(score_cp) >= 19000:
            # Faixa reservada às Tablebases Syzygy (vitória/derrota teórica sem mate à vista)
            return f"{'+VITÓRIA' if score_cp > 0 else '-DERROTA'} TB ({score_cp:+d})"
        pawns = score_cp / 100.0
        return f"{score_cp:+d} cp ({pawns:+.2f})"

    def log_engine_move(
        self,
        move_number: int,
        turn_color: bool,
        uci_move: str,
        depth: int,
        nodes: int,
        score_cp: int,
        pv_moves: list[str],
        elapsed_sec: float,
        source: str = "SEARCH",
    ) -> None:
        """
        Registra todos os campos obrigatórios do Item 9 do FAQ imediatamente após cada lance do motor.
        """
        color_str = "BRANCAS" if turn_color == chess.WHITE else "PRETAS"
        eval_str = self.format_score(score_cp)
        pv_str = " ".join(pv_moves) if pv_moves else uci_move
        if source != "SEARCH":
            pv_str = f"[{source}] {pv_str}"

        line = (
            f"{move_number:<4} | {color_str:<7} | {uci_move:<11} | {depth:<6} | "
            f"{nodes:<14} | {eval_str:<16} | {elapsed_sec:>6.3f}s | {pv_str}\n"
        )
        self._write_both(line, mode="a")

    def log_opponent_move(
        self,
        move_number: int,
        turn_color: bool,
        uci_move: str,
        elapsed_sec: float = 0.0,
    ) -> None:
        """Registra o lance anunciado pelo adversário para manter o histórico completo da partida."""
        color_str = "BRANCAS" if turn_color == chess.WHITE else "PRETAS"
        line = (
            f"{move_number:<4} | {color_str:<7} | {uci_move:<11} | {'-':<6} | "
            f"{'-':<14} | {'(Adversário)':<16} | {elapsed_sec:>6.3f}s | Lance adversário anunciado: {uci_move}\n"
        )
        self._write_both(line, mode="a")

    def log_event(self, message: str) -> None:
        """Registra eventos administrativos (ex: undo, ajuste de tempo, resultado final)."""
        ts = datetime.now().strftime("%H:%M:%S")
        self._write_both(f"# [{ts}] EVENTO: {message}\n", mode="a")
