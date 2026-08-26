"""Modelos Pydantic usados para validar colunas e relatórios de cada módulo.

Cada módulo de `/src/core` que precisa de um contrato de dados formal (uma
planilha de entrada com colunas obrigatórias, ou um relatório de saída)
ganha seu próprio arquivo aqui, nomeado igual ao módulo correspondente.
"""

from src.schemas.deduplicacao import RelatorioDeduplicacao
from src.schemas.etapa_de_ensino import RelatorioEtapaDeEnsino
from src.schemas.exportacao import RelatorioExportacaoMEC
from src.schemas.frequencia import RelatorioFrequencia
from src.schemas.movimentacao import RelatorioMovimentacao
from src.schemas.situacao_aluno import RelatorioSituacaoAluno
from src.schemas.situacao_sigeduc import RelatorioSituacaoSigeduc

__all__ = [
    "RelatorioDeduplicacao",
    "RelatorioEtapaDeEnsino",
    "RelatorioMovimentacao",
    "RelatorioFrequencia",
    "RelatorioExportacaoMEC",
    "RelatorioSituacaoAluno",
    "RelatorioSituacaoSigeduc",
]
