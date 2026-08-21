"""Schemas Pydantic do módulo de Discrepâncias e Movimentação
(`src/core/movimentacao.py`)."""

from __future__ import annotations

from pydantic import BaseModel, Field


class RelatorioMovimentacao(BaseModel):
    """Resumo da comparação entre a base Antiga e a base Nova.

    `total_presentes_nas_duas_bases` se divide em `total_trans_escola` +
    `total_trans_mod` + `total_avanco_terco_eja` + `total_sem_alteracao`.
    """

    coluna_cpf: str
    coluna_entidade: str
    coluna_etapa: str
    coluna_terco: str | None = Field(
        default=None,
        description="Coluna de terço/período EJA usada (normalmente "
        "'ESTUDANTE_ANO_PERIODO'). None se não encontrada em uma das bases "
        "-- nesse caso o avanço de terço simplesmente não é verificado.",
    )
    comparar_por_grupo_amplo: bool = Field(
        description="Se True (padrão), 'trans. mod' só é marcado quando o "
        "aluno cruza entre os grupos EJA e Regular/Técnico -- uma "
        "progressão normal de série dentro da mesma modalidade (ex.: "
        "Regular 25 -> 26) NÃO conta como transferência de modalidade. Se "
        "False, qualquer mudança no código bruto de "
        "ESTUDANTE_ETAPA_DE_ENSINO conta, inclusive progressão de série."
    )

    total_antiga: int = Field(ge=0)
    total_nova: int = Field(ge=0)
    total_presentes_nas_duas_bases: int = Field(ge=0)
    total_somente_na_antiga: int = Field(
        ge=0, description="CPFs que estavam na base Antiga mas não aparecem na Nova."
    )

    total_novo_cadastro: int = Field(ge=0)
    total_trans_escola: int = Field(ge=0)
    total_trans_mod: int = Field(ge=0)
    total_avanco_terco_eja: int = Field(ge=0)
    total_sem_alteracao: int = Field(ge=0)
