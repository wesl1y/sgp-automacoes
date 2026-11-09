"""Cadastro SGP da matrícula imediatamente posterior à gerada no módulo F."""

from __future__ import annotations

import polars as pl

from src.core.etapa_de_ensino import calcular_etapa_de_ensino
from src.core.movimentacao import _montar_cadastro
from src.utils.higienizacao import converter_data_robusta, limpar_cpf


def _data(coluna: str) -> pl.Expr:
    return converter_data_robusta(
        pl.col(coluna).cast(pl.Utf8, strict=False)
        .str.strip_chars().str.replace(r"^['’]+\s*", "")
    ).str.to_date("%d/%m/%Y", strict=False)


def gerar_cadastro_proxima_matricula(
    resultado_f: pl.DataFrame,
    sigeduc: pl.DataFrame,
    *,
    cpf_resultado: str,
    matricula_resultado: str,
    cpf_sigeduc: str,
    matricula_sigeduc: str,
    censo_sigeduc: str,
    situacao_sigeduc: str,
    oferta_sigeduc: str,
    serie_sigeduc: str,
    encerramento_sigeduc: str | None = None,
    terco_sigeduc: str | None = None,
) -> tuple[pl.DataFrame, dict[str, int]]:
    """Localiza a primeira matrícula posterior e usa o cadastro do módulo C.

    Cada par distinto de CPF e data do resultado F pode gerar um cadastro.
    Considera apenas matrículas posteriores com situação "MATRICULADO".
    Matrículas com a mesma data são desempatadas pelo encerramento mais
    recente e, por fim, pela última linha do arquivo.
    """
    for df, colunas, origem in (
        (resultado_f, (cpf_resultado, matricula_resultado), "resultado do módulo F"),
        (sigeduc, (cpf_sigeduc, matricula_sigeduc, censo_sigeduc,
                    situacao_sigeduc, oferta_sigeduc, serie_sigeduc,
                    encerramento_sigeduc, terco_sigeduc), "SIGEDUC"),
    ):
        faltantes = [nome for nome in colunas if nome is not None and nome not in df.columns]
        if faltantes:
            raise KeyError(f"Coluna(s) ausente(s) em {origem}: {', '.join(faltantes)}")

    origem = (
        resultado_f.with_row_index("_ordem_base")
        .select(
            limpar_cpf(pl.col(cpf_resultado)).alias("_cpf_chave"),
            _data(matricula_resultado).alias("_data_base"),
            "_ordem_base",
        )
        .filter(pl.col("_cpf_chave").is_not_null() & pl.col("_data_base").is_not_null())
        .unique(subset=["_cpf_chave", "_data_base"], keep="last", maintain_order=True)
    )
    historico = (
        sigeduc.with_row_index("_ordem_sigeduc")
        .with_columns(
            limpar_cpf(pl.col(cpf_sigeduc)).alias("_cpf_chave"),
            _data(matricula_sigeduc).alias("_data_proxima"),
        )
        .filter(pl.col("_cpf_chave").is_not_null() & pl.col("_data_proxima").is_not_null())
    )
    if encerramento_sigeduc is not None:
        historico = historico.with_columns(
            _data(encerramento_sigeduc).alias("_encerramento_desempate")
        )
    else:
        historico = historico.with_columns(
            pl.lit(None, dtype=pl.Date).alias("_encerramento_desempate")
        )
    historico = historico.filter(
        pl.col(situacao_sigeduc).cast(pl.Utf8, strict=False)
        .str.strip_chars().str.to_uppercase() == "MATRICULADO"
    )

    candidatos = origem.join(historico, on="_cpf_chave", how="inner").filter(
        pl.col("_data_proxima") > pl.col("_data_base")
    )
    proximas = (
        candidatos.sort(
            ["_cpf_chave", "_data_base", "_data_proxima",
             "_encerramento_desempate", "_ordem_sigeduc"],
            descending=[False, False, False, True, True],
            nulls_last=True,
        )
        .unique(subset=["_cpf_chave", "_data_base"], keep="first", maintain_order=True)
        .with_columns(
            pl.col("_cpf_chave").alias(cpf_sigeduc),
            pl.col("_data_proxima").dt.strftime("%d/%m/%Y").alias("_data_matricula_str"),
        )
    )
    if terco_sigeduc is not None:
        proximas = proximas.with_columns(
            pl.col(terco_sigeduc).cast(pl.Utf8, strict=False)
            .str.strip_chars().alias("_ano_periodo_terco")
        )
    proximas, _ = calcular_etapa_de_ensino(
        proximas, oferta_sigeduc, serie_sigeduc
    )
    cadastro = _montar_cadastro(
        proximas, cpf_sigeduc, censo_sigeduc, situacao_sigeduc
    )
    return cadastro, {
        "linhas_base": resultado_f.height,
        "matriculas_base_validas": origem.height,
        "cadastros_gerados": cadastro.height,
        "sem_matricula_posterior": origem.height - cadastro.height,
    }
