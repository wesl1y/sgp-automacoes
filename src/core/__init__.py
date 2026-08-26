"""Regras de negócio e processamento principal (Polars + DuckDB).

Cada rotina de negócio (deduplicação, motor de etapa de ensino, análise de
discrepâncias/movimentação, cálculo de frequência, exportação padrão MEC,
situação de matrícula e aprovações) vive em seu próprio módulo dentro
deste pacote.

Implementados até agora:
    - deduplicacao:     Módulo A -- deduplicação e matrícula mais recente.
    - etapa_de_ensino:  Módulo B -- motor de etapa de ensino (modalidades).
    - movimentacao:     Módulo C -- reconciliação SGP atual x SIGEDUC (Cadastro + Movimentação).
    - frequencia:       Módulo D -- cálculo de frequência.
    - exportacao:       Módulo E -- exportação padrão MEC.
    - situacao_aluno:   Módulo F -- situação de matrícula e aprovações (com base de destino).
    - situacao_sigeduc: Módulo G -- situação de matrícula direto do SIGEDUC (sem base de destino).
"""

from src.core.deduplicacao import deduplicar_matricula_mais_recente
from src.core.etapa_de_ensino import agrupar_modalidade, calcular_etapa_de_ensino
from src.core.exportacao import exportar_padrao_mec
from src.core.frequencia import calcular_frequencia
from src.core.movimentacao import reconciliar_sgp_sigeduc
from src.core.situacao_aluno import calcular_situacao_aluno
from src.core.situacao_sigeduc import resumir_situacao_sigeduc

__all__ = [
    "deduplicar_matricula_mais_recente",
    "calcular_etapa_de_ensino",
    "agrupar_modalidade",
    "reconciliar_sgp_sigeduc",
    "calcular_frequencia",
    "exportar_padrao_mec",
    "calcular_situacao_aluno",
    "resumir_situacao_sigeduc",
]
