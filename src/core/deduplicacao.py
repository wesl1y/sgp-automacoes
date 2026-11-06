"""Módulo A: Deduplicação e Matrícula Mais Recente.

Recebe uma base do SIGEDUC com CPF e datas de matrícula e alocação.
Compara a data de encerramento de cada registro ou, se estiver vazia,
a data de atualização do cadastro.

É, em espírito, o mesmo `sort_values(...).drop_duplicates(subset='CPF',
keep='last')` que aparece manualmente em praticamente todo script legado
do Escola 10 / Pé de Meia (CONCLUENTES_EJA, RESULTADOS, ALUNOS_NOVOS,
GERANDO_APROVADOS...) -- só que centralizado, testado uma vez só, e pronto
para ser reaproveitado pelos próximos módulos (Motor de Etapa, Movimentação,
Frequência) que também precisam partir de "uma linha por aluno".
"""

from __future__ import annotations

import polars as pl

from src.schemas.deduplicacao import RelatorioDeduplicacao
from src.utils.higienizacao import converter_data_robusta, limpar_cpf

COLUNAS_DATA_CANDIDATAS = ("DATA_INICIO_MATRICULA", "DATA MATRICULA", "DATA DE MATRÍCULA")
COLUNAS_ENCERRAMENTO_CANDIDATAS = ("DATA ENCERRAMENTO", "DATA_ENCERRAMENTO", "DATA DE ENCERRAMENTO")
COLUNAS_ATUALIZACAO_CANDIDATAS = (
    "DATA ATUALIZAÇÃO DO CADASTRO",
    "DATA ATUALIZACAO DO CADASTRO",
    "DATA_ATUALIZACAO_DO_CADASTRO",
)
COLUNAS_ALOCACAO_CANDIDATAS = ("DATA ALOCAÇÃO", "DATA_ALOCACAO", "DATA DE ALOCAÇÃO")


def deduplicar_matricula_mais_recente(
    df: pl.DataFrame,
    coluna_cpf: str = "ESTUDANTE_CPF",
    coluna_data: str | None = None,
    coluna_data_encerramento: str | None = None,
    coluna_data_alocacao: str | None = None,
    coluna_data_atualizacao: str | None = None,
) -> tuple[pl.DataFrame, RelatorioDeduplicacao]:
    """Seleciona por CPF a referência cronológica mais recente.

    Em cada linha, a referência é a data de encerramento ou, se não houver,
    a data de atualização do cadastro. Compara essa referência entre os
    registros do CPF, inclusive quando um tem encerramento e outro tem
    atualização. Em empate, prefere o encerramento. Sem ambas as datas,
    prefere registros com alocação; depois a matrícula mais recente e a
    alocação mais recente. Persistindo empate, fica a última linha.
    """
    if coluna_cpf not in df.columns:
        raise KeyError(
            f"Coluna de CPF '{coluna_cpf}' não encontrada. "
            f"Colunas disponíveis: {df.columns}"
        )

    def resolver_coluna(informada: str | None, candidatas: tuple[str, ...], descricao: str) -> str:
        nome = informada or next((c for c in candidatas if c in df.columns), None)
        if nome not in df.columns:
            raise KeyError(
                f"Não encontrei a coluna de {descricao}. "
                f"Selecione a coluna correta entre: {df.columns}"
            )
        return nome

    coluna_data_resolvida = resolver_coluna(coluna_data, COLUNAS_DATA_CANDIDATAS, "data de matrícula")
    def resolver_opcional(informada: str | None, candidatas: tuple[str, ...], descricao: str) -> str | None:
        if informada == "":
            return None
        nome = informada if informada is not None else next((c for c in candidatas if c in df.columns), None)
        if nome is not None and nome not in df.columns:
            raise KeyError(f"Coluna de {descricao} '{nome}' não encontrada.")
        return nome

    coluna_encerramento_resolvida = resolver_opcional(
        coluna_data_encerramento, COLUNAS_ENCERRAMENTO_CANDIDATAS, "data de encerramento"
    )
    coluna_atualizacao_resolvida = resolver_opcional(
        coluna_data_atualizacao, COLUNAS_ATUALIZACAO_CANDIDATAS, "data de atualização do cadastro"
    )
    coluna_alocacao_resolvida = resolver_coluna(
        coluna_data_alocacao, COLUNAS_ALOCACAO_CANDIDATAS, "data de alocação"
    )
    if coluna_encerramento_resolvida is None and coluna_atualizacao_resolvida is None:
        raise ValueError("Selecione a data de encerramento ou a data de atualização do cadastro.")
    selecionadas = [
        coluna_data_resolvida, coluna_alocacao_resolvida,
        coluna_encerramento_resolvida, coluna_atualizacao_resolvida,
    ]
    presentes = [coluna for coluna in selecionadas if coluna is not None]
    if len(set(presentes)) != len(presentes):
        raise ValueError("Selecione colunas de data diferentes para cada finalidade.")

    total_recebidos = df.height

    # 1. CPF limpo; fora quem não tem CPF válido.
    df_com_cpf_limpo = df.with_row_index("_ordem_original").with_columns(
        limpar_cpf(pl.col(coluna_cpf)).alias(coluna_cpf)
    )
    df_com_cpf = df_com_cpf_limpo.filter(pl.col(coluna_cpf).is_not_null())
    total_cpf_invalido = total_recebidos - df_com_cpf.height

    # Uma data precedida de apóstrofo é comum em CSVs exportados do Excel.
    # Removê-lo antes da conversão preserva a data para a ordenação.
    df_com_data = df_com_cpf.with_columns(
        *[
            converter_data_robusta(
                pl.col(coluna).cast(pl.Utf8, strict=False)
                .str.strip_chars()
                .str.replace(r"^['’]+\s*", "")
            ).alias(coluna)
            for coluna in presentes
        ]
    ).with_columns(
        pl.col(coluna_data_resolvida).str.to_date("%d/%m/%Y", strict=False).alias("_matricula_ordem"),
        (
            pl.col(coluna_encerramento_resolvida).str.to_date("%d/%m/%Y", strict=False)
            if coluna_encerramento_resolvida else pl.lit(None, dtype=pl.Date)
        ).alias("_encerramento_ordem"),
        (
            pl.col(coluna_atualizacao_resolvida).str.to_date("%d/%m/%Y", strict=False)
            if coluna_atualizacao_resolvida else pl.lit(None, dtype=pl.Date)
        ).alias("_atualizacao_ordem"),
        pl.col(coluna_alocacao_resolvida).str.to_date("%d/%m/%Y", strict=False).alias("_alocacao_ordem"),
    ).with_columns(
        pl.coalesce("_encerramento_ordem", "_atualizacao_ordem").alias("_data_referencia"),
        pl.col("_encerramento_ordem").is_not_null().alias("_tem_encerramento"),
        pl.col("_alocacao_ordem").is_not_null().alias("_tem_alocacao"),
    )
    df_ordenado = df_com_data.sort(
        [
            coluna_cpf, "_data_referencia", "_tem_encerramento", "_tem_alocacao",
            "_matricula_ordem", "_alocacao_ordem", "_ordem_original",
        ],
        nulls_last=False,
    )
    df_dedup = df_ordenado.unique(subset=[coluna_cpf], keep="last", maintain_order=True)

    total_data_invalida_mantida = df_dedup.filter(
        pl.all_horizontal(
            pl.col("_matricula_ordem").is_null(),
            pl.col("_encerramento_ordem").is_null(),
            pl.col("_atualizacao_ordem").is_null(),
            pl.col("_alocacao_ordem").is_null(),
        )
    ).height

    df_final = df_dedup.drop(
        "_ordem_original", "_matricula_ordem", "_encerramento_ordem",
        "_atualizacao_ordem", "_data_referencia", "_tem_encerramento",
        "_alocacao_ordem", "_tem_alocacao"
    ).sort(coluna_cpf)

    relatorio = RelatorioDeduplicacao(
        coluna_cpf=coluna_cpf,
        coluna_data=coluna_data_resolvida,
        coluna_data_encerramento=coluna_encerramento_resolvida,
        coluna_data_atualizacao=coluna_atualizacao_resolvida,
        coluna_data_alocacao=coluna_alocacao_resolvida,
        total_registros_recebidos=total_recebidos,
        total_cpf_invalido_removido=total_cpf_invalido,
        total_duplicatas_removidas=df_com_cpf.height - df_final.height,
        total_data_invalida_mantida=total_data_invalida_mantida,
        total_registros_final=df_final.height,
    )

    return df_final, relatorio


if __name__ == "__main__":
    # Auto-teste rápido -- rode com `python src/core/deduplicacao.py` a
    # partir da raiz do projeto.
    amostra = pl.DataFrame(
        {
            "ESTUDANTE_CPF": ["111.111.111-11", "111.111.111-11", "111.111.111-11", "222", "000.000.000-00", "333", "333"],
            "DATA_INICIO_MATRICULA": ["01/01/2026", "2026-06-15", "10/03/2026", "20/02/2026", "01/01/2026", None, "05/05/2026"],
            "DATA ENCERRAMENTO": ["10/01/2026", None, None, None, None, None, None],
            "DATA ALOCAÇÃO": ["01/01/2026", "15/06/2026", "10/03/2026", "20/02/2026", "01/01/2026", None, "05/05/2026"],
            "NOME": ["antigo", "MAIS_RECENTE", "meio", "unico", "cpf_invalido", "sem_data_mantido", "MAIS_RECENTE"],
        }
    )
    resultado, relatorio = deduplicar_matricula_mais_recente(amostra)
    print(resultado)
    print()
    print(relatorio.model_dump_json(indent=2))
