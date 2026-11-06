"""Schemas Pydantic do módulo de Deduplicação (`src/core/deduplicacao.py`)."""

from __future__ import annotations

from pydantic import BaseModel, Field


class RelatorioDeduplicacao(BaseModel):
    """Resumo do que aconteceu ao deduplicar uma base do SIGEDUC.

    Devolvido junto com o DataFrame limpo por
    `deduplicar_matricula_mais_recente`, para a UI do Streamlit poder
    mostrar números objetivos do que foi alterado -- nada de "confia em
    mim", o usuário vê exatamente quantos registros saíram e por quê.
    """

    coluna_cpf: str
    coluna_data: str = Field(
        description="Coluna da data de matrícula usada na ordenação."
    )
    coluna_data_encerramento: str | None
    coluna_data_atualizacao: str | None
    coluna_data_alocacao: str
    total_registros_recebidos: int = Field(ge=0)
    total_cpf_invalido_removido: int = Field(
        ge=0,
        description="Linhas descartadas por não terem um CPF válido (nulo "
        "ou só zeros) -- sem CPF não há como identificar o aluno.",
    )
    total_duplicatas_removidas: int = Field(
        ge=0,
        description="Quantos registros 'antigos' foram descartados por "
        "existir uma matrícula mais recente do mesmo CPF.",
    )
    total_data_invalida_mantida: int = Field(
        ge=0,
        description="Registros selecionados sem matrícula, encerramento, atualização ou alocação reconhecidos.",
    )
    total_registros_final: int = Field(ge=0)
