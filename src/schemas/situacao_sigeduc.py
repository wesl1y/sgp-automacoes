"""Schemas Pydantic do módulo de Situação de Matrícula a partir do SIGEDUC
(`src/core/situacao_sigeduc.py`)."""

from __future__ import annotations

from pydantic import BaseModel, Field


class RelatorioSituacaoSigeduc(BaseModel):
    """Resumo da geração da situação de matrícula direto da base do
    SIGEDUC, sem nenhuma base de destino envolvida.

    `total_sigeduc_cpf_valido` + linhas descartadas por CPF inválido/nulo
    == `total_sigeduc`. `total_cpfs_unicos` é o número de linhas do
    resultado (1 por CPF válido encontrado)."""

    coluna_cpf: str
    coluna_data_fim: str
    coluna_data_encerramento: str = Field(
        description="Coluna usada para decidir qual registro é a 'situação "
        "mais recente' de cada CPF (prioriza data real; sem nenhuma data "
        "válida, mantém o último registro na ordem original da planilha)."
    )
    coluna_periodo: str
    coluna_situacao: str
    status_aprovado: str = Field(
        description="Texto (case-insensitive) usado para contar "
        "'QTD_MODULOS_APROVADOS' -- conta toda linha do SIGEDUC daquele "
        "CPF cuja situação contenha esse texto, não só a situação mais recente."
    )
    aplicar_transferencia_externa: bool = Field(
        description="Se True, um CPF cuja situação mais recente é "
        "'TRANSFERIDO' e que nunca teve nenhuma outra situação no "
        "histórico do SIGEDUC enviado é reclassificado para "
        "'TRANSFERÊNCIA EXTERNA'."
    )

    total_sigeduc: int = Field(ge=0, description="Linhas na planilha do SIGEDUC, como enviada.")
    total_sigeduc_cpf_valido: int = Field(
        ge=0,
        description="Linhas do SIGEDUC com CPF válido (antes de deduplicar por CPF) "
        "-- é essa base que é usada para contar aprovados e decidir a situação mais recente.",
    )
    total_cpfs_unicos: int = Field(
        ge=0, description="Número de linhas do resultado -- 1 por CPF válido encontrado no SIGEDUC."
    )
    total_com_aprovacao: int = Field(ge=0, description="CPFs com QTD_MODULOS_APROVADOS maior que zero.")
    total_reclassificados_transferencia_externa: int = Field(
        ge=0,
        description="CPFs cuja situação mais recente foi trocada para "
        "'TRANSFERÊNCIA EXTERNA'. Sempre 0 se aplicar_transferencia_externa for False.",
    )
