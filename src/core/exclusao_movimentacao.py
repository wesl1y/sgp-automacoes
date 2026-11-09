"""Remove da base SGP os alunos presentes na planilha de movimentação."""

from __future__ import annotations

import polars as pl

from src.utils.higienizacao import limpar_cpf


def excluir_cpfs_movimentacao(
    sgp: pl.DataFrame,
    movimentacao: pl.DataFrame,
    *,
    cpf_sgp: str,
    cpf_movimentacao: str,
) -> tuple[pl.DataFrame, dict[str, int]]:
    """Preserva todas as colunas e linhas do SGP sem CPF presente na movimentação."""
    if cpf_sgp not in sgp.columns:
        raise KeyError(f"Coluna de CPF não encontrada no SGP: {cpf_sgp}")
    if cpf_movimentacao not in movimentacao.columns:
        raise KeyError(f"Coluna de CPF não encontrada na movimentação: {cpf_movimentacao}")

    cpfs_movimentacao = set(
        limpar_cpf(movimentacao.get_column(cpf_movimentacao)).drop_nulls().to_list()
    )
    cpfs_sgp = limpar_cpf(sgp.get_column(cpf_sgp))
    encontrados = cpfs_sgp.is_in(cpfs_movimentacao).fill_null(False)
    resultado = sgp.filter(~encontrados)

    return resultado, {
        "linhas_sgp": sgp.height,
        "cpfs_movimentacao": len(cpfs_movimentacao),
        "alunos_encontrados": cpfs_sgp.filter(encontrados).n_unique(),
        "linhas_removidas": int(encontrados.sum()),
        "linhas_restantes": resultado.height,
    }
