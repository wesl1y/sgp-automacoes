"""Módulo C: Análise de Discrepâncias e Movimentação.

Compara uma base **Antiga** com uma base **Nova** (ambas já passadas pelos
módulos A e B -- ou seja, já deduplicadas e já com `ESTUDANTE_ETAPA_DE_ENSINO`
calculada) e classifica cada CPF da base Nova em uma das situações:

    - `'novo_cadastro'`    -- CPF não existia na base Antiga.
    - `'trans. escola'`    -- CO_ENTIDADE mudou.
    - `'trans. mod'`       -- mesma escola, mas a modalidade mudou (ver
                              `comparar_por_grupo_amplo` abaixo).
    - `'avanco_terco_eja'` -- aluno de EJA cujo terço/período avançou
                              (1 -> 2, 2 -> 3, ou foi para o 4).
    - `'sem_alteracao'`    -- nada relevante mudou.

A função devolve dois recortes prontos para exportar -- **Movimentação**
(quem já estava na base e teve alguma mudança) e **Cadastro** (quem é novo
e precisa ser cadastrado) -- além de um relatório com as contagens.

Implementado com um `LEFT JOIN` em SQL no DuckDB: comparar duas bases é
exatamente o tipo de cruzamento que o DuckDB foi escolhido para facilitar.
"""

from __future__ import annotations

import duckdb
import polars as pl

from src.core._colunas import encontrar_coluna, exigir_coluna, quotar_identificador
from src.core.etapa_de_ensino import CODIGOS_EJA
from src.schemas.movimentacao import RelatorioMovimentacao
from src.utils.higienizacao import limpar_cpf

COLUNAS_CPF_CANDIDATAS = ("ESTUDANTE_CPF",)
COLUNAS_ENTIDADE_CANDIDATAS = ("CO_ENTIDADE",)
COLUNAS_ETAPA_CANDIDATAS = ("ESTUDANTE_ETAPA_DE_ENSINO",)
COLUNAS_TERCO_CANDIDATAS = ("ESTUDANTE_ANO_PERIODO", "PERIODO", "PERÍODO")

COLUNA_SITUACAO = "MATRICULA_SITUACAO_FINAL"

_SITUACAO_NOVO_CADASTRO = "novo_cadastro"
_SITUACAO_TRANS_ESCOLA = "trans. escola"
_SITUACAO_TRANS_MOD = "trans. mod"
_SITUACAO_AVANCO_TERCO = "avanco_terco_eja"
_SITUACAO_SEM_ALTERACAO = "sem_alteracao"


def _normalizar_entidade(coluna: pl.Expr) -> pl.Expr:
    """Remove espaços e um eventual '.0' de código de escola que virou
    float sem querer (mesmo problema do CPF, mesma solução)."""
    return coluna.cast(pl.Utf8, strict=False).str.strip_chars().str.replace_all(r"\.0$", "")


def _preparar_base(df: pl.DataFrame, coluna_cpf: str, coluna_entidade: str) -> pl.DataFrame:
    return df.with_columns(
        limpar_cpf(pl.col(coluna_cpf)).alias(coluna_cpf),
        _normalizar_entidade(pl.col(coluna_entidade)).alias(coluna_entidade),
    ).filter(pl.col(coluna_cpf).is_not_null())


def _construir_sql(
    coluna_cpf: str,
    coluna_entidade: str,
    coluna_etapa: str,
    coluna_terco: str | None,
    comparar_por_grupo_amplo: bool,
) -> str:
    cpf_sql = quotar_identificador(coluna_cpf)
    entidade_sql = quotar_identificador(coluna_entidade)
    etapa_sql = quotar_identificador(coluna_etapa)

    codigos_eja_sql = ", ".join(f"'{c}'" for c in CODIGOS_EJA)

    def _grupo_sql(referencia_coluna: str) -> str:
        return (
            f"CASE WHEN {referencia_coluna} IN ({codigos_eja_sql}) "
            "THEN 'EJA' ELSE 'OUTRO_OU_REGULAR_TECNICO' END"
        )

    if comparar_por_grupo_amplo:
        condicao_trans_mod = (
            f"({_grupo_sql('antiga._etapa_antiga')}) "
            f"IS DISTINCT FROM ({_grupo_sql('nova.' + etapa_sql)})"
        )
    else:
        condicao_trans_mod = f"antiga._etapa_antiga IS DISTINCT FROM nova.{etapa_sql}"

    condicao_terco = "FALSE"
    if coluna_terco is not None:
        terco_sql = quotar_identificador(coluna_terco)
        condicao_terco = f"""nova.{etapa_sql} IN ({codigos_eja_sql})
             AND try_cast(antiga._terco_antigo AS INTEGER) IS NOT NULL
             AND try_cast(nova.{terco_sql} AS INTEGER) IS NOT NULL
             AND try_cast(nova.{terco_sql} AS INTEGER) > try_cast(antiga._terco_antigo AS INTEGER)"""

    terco_antigo_select = (
        f"{quotar_identificador(coluna_terco)} AS _terco_antigo" if coluna_terco else "NULL AS _terco_antigo"
    )

    return f"""
        WITH antiga AS (
            SELECT
                {cpf_sql} AS _cpf,
                {entidade_sql} AS _entidade_antiga,
                {etapa_sql} AS _etapa_antiga,
                {terco_antigo_select}
            FROM base_antiga
        ),
        nova AS (
            SELECT *, {cpf_sql} AS _cpf
            FROM base_nova
        )
        SELECT
            nova.* EXCLUDE (_cpf),
            antiga._entidade_antiga,
            antiga._etapa_antiga,
            antiga._terco_antigo,
            CASE
                WHEN antiga._cpf IS NULL THEN '{_SITUACAO_NOVO_CADASTRO}'
                WHEN antiga._entidade_antiga IS DISTINCT FROM nova.{entidade_sql} THEN '{_SITUACAO_TRANS_ESCOLA}'
                WHEN {condicao_trans_mod} THEN '{_SITUACAO_TRANS_MOD}'
                WHEN {condicao_terco} THEN '{_SITUACAO_AVANCO_TERCO}'
                ELSE '{_SITUACAO_SEM_ALTERACAO}'
            END AS {COLUNA_SITUACAO}
        FROM nova
        LEFT JOIN antiga ON antiga._cpf = nova._cpf
    """


def analisar_movimentacao(
    df_antiga: pl.DataFrame,
    df_nova: pl.DataFrame,
    coluna_cpf: str | None = None,
    coluna_entidade: str | None = None,
    coluna_etapa: str | None = None,
    coluna_terco: str | None = None,
    comparar_por_grupo_amplo: bool = True,
) -> tuple[pl.DataFrame, pl.DataFrame, RelatorioMovimentacao]:
    """Compara `df_antiga` com `df_nova` e classifica cada CPF.

    Parameters
    ----------
    df_antiga, df_nova:
        As duas bases a comparar (idealmente já passadas pelos módulos A
        e B).
    coluna_cpf, coluna_entidade, coluna_etapa:
        Nomes das colunas a usar. Se omitidos, usam os nomes padrão do
        SGP (`ESTUDANTE_CPF`, `CO_ENTIDADE`, `ESTUDANTE_ETAPA_DE_ENSINO`).
        Precisam existir nas duas bases.
    coluna_terco:
        Coluna de terço/período EJA (normalmente
        `ESTUDANTE_ANO_PERIODO`). Se omitida, tenta detectar
        automaticamente; se não existir em uma das bases, o avanço de
        terço simplesmente não é calculado (sem erro).
    comparar_por_grupo_amplo:
        Se True (padrão), só marca `'trans. mod'` quando o aluno cruza
        entre os grupos EJA e Regular/Técnico -- uma progressão normal de
        série (ex.: Regular 25 -> 26, que acontece todo ano com quem
        continua na mesma modalidade) **não** conta como transferência de
        modalidade. Essa é a mesma regra usada nos scripts legados de
        Movimentação. Se quiser comparar o código bruto de
        `ESTUDANTE_ETAPA_DE_ENSINO` diretamente (inclusive progressão de
        série), passe `False`.

    Returns
    -------
    tuple[pl.DataFrame, pl.DataFrame, RelatorioMovimentacao]
        `(df_movimentacao, df_cadastro, relatorio)`:

        - `df_movimentacao`: CPFs presentes nas duas bases com alguma
          mudança (`'trans. escola'`, `'trans. mod'` ou
          `'avanco_terco_eja'`), com as colunas da base Nova + os valores
          antigos de entidade/etapa/terço para comparação + a coluna
          `MATRICULA_SITUACAO_FINAL`.
        - `df_cadastro`: CPFs que só existem na base Nova (novos
          cadastros), com as colunas da base Nova.
        - `relatorio`: contagens de cada situação.

    Raises
    ------
    KeyError
        Se as colunas de CPF, entidade ou etapa não existirem em alguma
        das duas bases.
    """
    coluna_cpf_resolvida = coluna_cpf or exigir_coluna(df_nova, COLUNAS_CPF_CANDIDATAS, "CPF")
    coluna_entidade_resolvida = coluna_entidade or exigir_coluna(
        df_nova, COLUNAS_ENTIDADE_CANDIDATAS, "CO_ENTIDADE"
    )
    coluna_etapa_resolvida = coluna_etapa or exigir_coluna(
        df_nova, COLUNAS_ETAPA_CANDIDATAS, "ESTUDANTE_ETAPA_DE_ENSINO"
    )
    # As mesmas colunas precisam existir também na base Antiga.
    for nome, candidatos in (
        (coluna_cpf_resolvida, (coluna_cpf_resolvida,)),
        (coluna_entidade_resolvida, (coluna_entidade_resolvida,)),
        (coluna_etapa_resolvida, (coluna_etapa_resolvida,)),
    ):
        exigir_coluna(df_antiga, candidatos, f"'{nome}' (na base Antiga)")

    coluna_terco_resolvida = coluna_terco or (
        encontrar_coluna(df_nova, COLUNAS_TERCO_CANDIDATAS)
        if encontrar_coluna(df_antiga, COLUNAS_TERCO_CANDIDATAS)
        else None
    )

    base_antiga = _preparar_base(df_antiga, coluna_cpf_resolvida, coluna_entidade_resolvida)
    base_nova = _preparar_base(df_nova, coluna_cpf_resolvida, coluna_entidade_resolvida)

    sql = _construir_sql(
        coluna_cpf_resolvida,
        coluna_entidade_resolvida,
        coluna_etapa_resolvida,
        coluna_terco_resolvida,
        comparar_por_grupo_amplo,
    )
    con = duckdb.connect()
    con.register("base_antiga", base_antiga)
    con.register("base_nova", base_nova)
    classificado = con.sql(sql).pl()

    total_somente_na_antiga = base_antiga.height - con.sql(
        f"SELECT count(*) AS n FROM base_antiga WHERE "
        f"{quotar_identificador(coluna_cpf_resolvida)} IN (SELECT {quotar_identificador(coluna_cpf_resolvida)} FROM base_nova)"
    ).pl()["n"][0]

    df_movimentacao = classificado.filter(
        pl.col(COLUNA_SITUACAO).is_in([_SITUACAO_TRANS_ESCOLA, _SITUACAO_TRANS_MOD, _SITUACAO_AVANCO_TERCO])
    )
    df_cadastro = classificado.filter(pl.col(COLUNA_SITUACAO) == _SITUACAO_NOVO_CADASTRO).drop(
        ["_entidade_antiga", "_etapa_antiga", "_terco_antigo", COLUNA_SITUACAO]
    )

    # Uma só chamada a `.value_counts()` -- zipar chaves e contagens vindas de
    # duas chamadas separadas não garante a mesma ordem entre elas.
    contagem_vc = classificado[COLUNA_SITUACAO].value_counts()
    contagem = dict(zip(contagem_vc[COLUNA_SITUACAO].to_list(), contagem_vc["count"].to_list()))

    relatorio = RelatorioMovimentacao(
        coluna_cpf=coluna_cpf_resolvida,
        coluna_entidade=coluna_entidade_resolvida,
        coluna_etapa=coluna_etapa_resolvida,
        coluna_terco=coluna_terco_resolvida,
        comparar_por_grupo_amplo=comparar_por_grupo_amplo,
        total_antiga=base_antiga.height,
        total_nova=base_nova.height,
        total_presentes_nas_duas_bases=base_nova.height - contagem.get(_SITUACAO_NOVO_CADASTRO, 0),
        total_somente_na_antiga=int(total_somente_na_antiga),
        total_novo_cadastro=contagem.get(_SITUACAO_NOVO_CADASTRO, 0),
        total_trans_escola=contagem.get(_SITUACAO_TRANS_ESCOLA, 0),
        total_trans_mod=contagem.get(_SITUACAO_TRANS_MOD, 0),
        total_avanco_terco_eja=contagem.get(_SITUACAO_AVANCO_TERCO, 0),
        total_sem_alteracao=contagem.get(_SITUACAO_SEM_ALTERACAO, 0),
    )

    return df_movimentacao, df_cadastro, relatorio


if __name__ == "__main__":
    # Auto-teste rápido -- rode com `python -m src.core.movimentacao` a
    # partir da raiz do projeto.
    antiga = pl.DataFrame(
        {
            "ESTUDANTE_CPF": ["1", "2", "3", "4", "5", "6", "7"],
            "CO_ENTIDADE": ["100", "100", "100", "100", "100", "100", "100"],
            "ESTUDANTE_ETAPA_DE_ENSINO": ["45", "45", "25", "45", "25", "45", "45"],
            "ESTUDANTE_ANO_PERIODO": ["1", "2", "1", "1", "1", "1", "3"],
        }
    )
    nova = pl.DataFrame(
        {
            "ESTUDANTE_CPF": ["1", "3", "4", "5", "6", "7", "8"],
            "CO_ENTIDADE": ["100", "200", "100", "100", "100", "100", "300"],
            "ESTUDANTE_ETAPA_DE_ENSINO": ["45", "25", "25", "26", "45", "45", "25"],
            "ESTUDANTE_ANO_PERIODO": ["1", "1", "1", "1", "2", "3", "1"],
        }
    )
    df_mov, df_cad, relatorio = analisar_movimentacao(antiga, nova)
    print("=== Movimentação ===")
    with pl.Config(tbl_cols=-1, tbl_width_chars=200):
        print(df_mov)
    print("\n=== Cadastro ===")
    print(df_cad)
    print()
    print(relatorio.model_dump_json(indent=2))
