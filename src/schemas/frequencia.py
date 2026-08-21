"""Schemas Pydantic do módulo de Cálculo de Frequência
(`src/core/frequencia.py`)."""

from __future__ import annotations

from pydantic import BaseModel, Field


class RelatorioFrequencia(BaseModel):
    """Resumo do cruzamento entre a base do SGP e a base do SIGEDUC.

    `total_com_correspondencia_sigeduc` + `total_sem_correspondencia_sigeduc`
    == `total_sgp` (nenhuma linha do SGP é descartada -- só os valores de
    frequência de quem tem correspondência no SIGEDUC podem mudar).

    `total_alterados` == `total_subiu` + `total_desceu_abaixo_80` +
    `total_desceu_acima_80` + `total_manteve_percentual`.
    """

    coluna_cpf_sgp: str
    coluna_cpf_sigeduc: str
    coluna_ofertada: str
    coluna_presente: str

    total_sgp: int = Field(ge=0, description="Linhas na planilha do SGP, como enviada.")
    total_sigeduc: int = Field(ge=0, description="Linhas na planilha do SIGEDUC, como enviada.")
    total_sigeduc_apos_limpeza: int = Field(
        ge=0,
        description="Linhas do SIGEDUC após remover CPF inválido e duplicatas "
        "(mantendo a última ocorrência por CPF) -- é essa base que é usada no cruzamento.",
    )

    total_com_correspondencia_sigeduc: int = Field(ge=0)
    total_sem_correspondencia_sigeduc: int = Field(
        ge=0,
        description="Alunos do SGP não encontrados no SIGEDUC (por CPF ausente, "
        "inválido, ou que simplesmente não está na base SIGEDUC enviada) -- "
        "o valor de frequência desses alunos é mantido sem alteração.",
    )

    total_alterados: int = Field(ge=0, description="Alunos cujo HL_OFERTADA e/ou HL_PRESENTE mudou.")
    total_subiu: int = Field(ge=0, description="Percentual de presença subiu.")
    total_desceu_abaixo_80: int = Field(
        ge=0, description="Percentual de presença desceu e ficou abaixo de 80%."
    )
    total_desceu_acima_80: int = Field(
        ge=0, description="Percentual de presença desceu, mas continua em 80% ou mais."
    )
    total_manteve_percentual: int = Field(
        ge=0, description="Os valores em horas mudaram, mas o percentual de presença ficou igual."
    )
    total_sem_alteracao: int = Field(ge=0, description="Nada mudou (inclui quem não tem correspondência no SIGEDUC).")
