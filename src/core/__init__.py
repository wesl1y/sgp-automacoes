"""Regras de negócio e processamento principal (Polars + DuckDB).

Cada rotina de negócio (deduplicação, motor de etapa de ensino, análise de
discrepâncias/movimentação, cálculo de frequência, exportação padrão MEC)
vive em seu próprio módulo dentro deste pacote.

Implementados até agora:
    - deduplicacao:     Módulo A -- deduplicação e matrícula mais recente.
    - etapa_de_ensino:  Módulo B -- motor de etapa de ensino (modalidades).
    - movimentacao:     Módulo C -- discrepâncias e movimentação.
    - frequencia:       Módulo D -- cálculo de frequência.
    - exportacao:       Módulo E -- exportação padrão MEC.
"""

from src.core.deduplicacao import deduplicar_matricula_mais_recente
from src.core.etapa_de_ensino import agrupar_modalidade, calcular_etapa_de_ensino
from src.core.exportacao import exportar_padrao_mec
from src.core.frequencia import calcular_frequencia
from src.core.movimentacao import analisar_movimentacao

__all__ = [
    "deduplicar_matricula_mais_recente",
    "calcular_etapa_de_ensino",
    "agrupar_modalidade",
    "analisar_movimentacao",
    "calcular_frequencia",
    "exportar_padrao_mec",
]
