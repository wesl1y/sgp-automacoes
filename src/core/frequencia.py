"""Módulo D: Cálculo de Frequência.

Cruza a base atual do **SGP** (o que já está lançado) com a base bruta do
**SIGEDUC** (a fonte "de verdade" das aulas dadas e faltas) e devolve a base
do SGP com a frequência corrigida, pronta para reenviar.

Regras de negócio (conforme o briefing e os scripts legados
`FREQUENCIA_2026` / `RETIFICANDO_FREQUENCIA`):

    1. A partir do SIGEDUC (colunas `HORA_AULA_DADA` e `FALTAS_ALUNO_HORA`),
       calcula:

           HL_PRESENTE_PERIODO = HORA_AULA_DADA - FALTAS_ALUNO_HORA

       (e `HL_OFERTADA_PERIODO = HORA_AULA_DADA`).

    2. Cruza com o SGP pelo CPF e, para cada aluno encontrado nas duas
       bases, fica sempre com o **maior** valor entre o que já estava no
       SGP e o calculado a partir do SIGEDUC -- a frequência nunca deve
       *diminuir* por causa deste cruzamento. Aluno que não aparece no
       SIGEDUC mantém o valor do SGP intocado.
    3. Para quem teve o valor alterado, classifica se o percentual de
       presença (`HL_PRESENTE / HL_OFERTADA`) subiu ou desceu -- e, se
       desceu, se ficou abaixo ou continua igual/acima de 80%.

Nenhuma linha do SGP é descartada: a planilha devolvida em
`df_atualizado` tem sempre o mesmo número de linhas da planilha do SGP
enviada, pronta para reenvio. O cruzamento em si (comparar duas bases por
CPF e ficar com o maior valor) é feito em SQL via DuckDB.
"""

from __future__ import annotations

import duckdb
import polars as pl

from src.core._colunas import exigir_coluna, quotar_identificador
from src.schemas.frequencia import RelatorioFrequencia
from src.utils.higienizacao import limpar_cpf

COLUNAS_CPF_SGP_CANDIDATAS = ("ESTUDANTE_CPF", "CPF")
COLUNAS_CPF_SIGEDUC_CANDIDATAS = ("ESTUDANTE_CPF", "ALUNO_CPF", "CPF")
COLUNAS_OFERTADA_CANDIDATAS = ("HL_OFERTADA_PERIODO", "HL_OFERTADA_ESTUDANTE_PERIODO")
COLUNAS_PRESENTE_CANDIDATAS = ("HL_PRESENTE_PERIODO", "HL_PRESENTE_ESTUDANTE_PERIODO")
COLUNA_HORA_AULA_DADA = ("HORA_AULA_DADA",)
COLUNA_FALTAS_ALUNO_HORA = ("FALTAS_ALUNO_HORA",)

LIMIAR_80_PORCENTO = 0.80

COLUNA_STATUS = "STATUS_FREQUENCIA"
COLUNA_ENCONTRADO = "ENCONTRADO_NO_SIGEDUC"

_STATUS_SUBIU = "Subiu (frequência melhorou)"
_STATUS_DESCEU_ABAIXO_80 = "Desceu (frequência piorou) - abaixo de 80%"
_STATUS_DESCEU_ACIMA_80 = "Desceu (frequência piorou) - acima de 80%"
_STATUS_MANTEVE_PERCENTUAL = "Manteve o percentual (mudou só a carga horária)"
_STATUS_SEM_ALTERACAO = "Sem alteração"


def _preparar_sgp(df: pl.DataFrame, coluna_cpf: str, coluna_ofertada: str, coluna_presente: str) -> pl.DataFrame:
    """Limpa o CPF e garante que as colunas de frequência sejam numéricas.

    Diferente dos módulos A/C, aqui **nenhuma linha é descartada** mesmo
    que o CPF seja inválido -- esta é a planilha que volta para reenvio no
    SGP, então perder um aluno aqui seria pior do que só não conseguir
    cruzar aquele CPF com o SIGEDUC (ele simplesmente fica sem
    correspondência e mantém o valor original).
    """
    return df.with_columns(
        limpar_cpf(pl.col(coluna_cpf)).alias(coluna_cpf),
        pl.col(coluna_ofertada).cast(pl.Float64, strict=False).fill_null(0.0).alias(coluna_ofertada),
        pl.col(coluna_presente).cast(pl.Float64, strict=False).fill_null(0.0).alias(coluna_presente),
    )


def _preparar_sigeduc(df: pl.DataFrame, coluna_cpf: str) -> pl.DataFrame:
    """Limpa o CPF, calcula HL_OFERTADA/HL_PRESENTE a partir de
    HORA_AULA_DADA/FALTAS_ALUNO_HORA, remove CPF inválido e deduplica por
    CPF mantendo a última ocorrência (mesma regra dos scripts legados)."""
    coluna_hora_dada = exigir_coluna(df, COLUNA_HORA_AULA_DADA, "Horas de aula dada (SIGEDUC)")
    coluna_faltas = exigir_coluna(df, COLUNA_FALTAS_ALUNO_HORA, "Faltas do aluno em horas (SIGEDUC)")

    preparado = (
        df.with_columns(
            limpar_cpf(pl.col(coluna_cpf)).alias("_cpf_sigeduc"),
            pl.col(coluna_hora_dada).cast(pl.Float64, strict=False).fill_null(0.0).alias("_hl_ofertada_sigeduc"),
            pl.col(coluna_faltas).cast(pl.Float64, strict=False).fill_null(0.0).alias("_faltas_sigeduc"),
        )
        .filter(pl.col("_cpf_sigeduc").is_not_null())
        .with_columns(
            (pl.col("_hl_ofertada_sigeduc") - pl.col("_faltas_sigeduc")).alias("_hl_presente_sigeduc")
        )
        .select("_cpf_sigeduc", "_hl_ofertada_sigeduc", "_hl_presente_sigeduc")
    )
    return preparado.unique(subset=["_cpf_sigeduc"], keep="last", maintain_order=True)


def _construir_sql(coluna_cpf_sgp: str, coluna_ofertada: str, coluna_presente: str) -> str:
    cpf_sql = quotar_identificador(coluna_cpf_sgp)
    ofertada_sql = quotar_identificador(coluna_ofertada)
    presente_sql = quotar_identificador(coluna_presente)

    return f"""
        WITH cruzado AS (
            SELECT
                sgp.* EXCLUDE ({ofertada_sql}, {presente_sql}),
                sgp.{ofertada_sql} AS _ofertada_sgp,
                sgp.{presente_sql} AS _presente_sgp,
                sigeduc._cpf_sigeduc IS NOT NULL AS _encontrado,
                sigeduc._hl_ofertada_sigeduc,
                sigeduc._hl_presente_sigeduc
            FROM sgp
            LEFT JOIN sigeduc ON sgp.{cpf_sql} = sigeduc._cpf_sigeduc
        ),
        calculado AS (
            SELECT
                cruzado.* EXCLUDE (_hl_ofertada_sigeduc, _hl_presente_sigeduc),
                CASE WHEN _encontrado THEN GREATEST(_ofertada_sgp, _hl_ofertada_sigeduc)
                     ELSE _ofertada_sgp END AS _maior_ofertada,
                CASE WHEN _encontrado THEN GREATEST(_presente_sgp, _hl_presente_sigeduc)
                     ELSE _presente_sgp END AS _maior_presente
            FROM cruzado
        ),
        classificado AS (
            SELECT
                calculado.*,
                (_maior_ofertada IS DISTINCT FROM _ofertada_sgp)
                    OR (_maior_presente IS DISTINCT FROM _presente_sgp) AS _mudou,
                CASE WHEN _ofertada_sgp > 0 THEN _presente_sgp / _ofertada_sgp ELSE 0 END AS _perc_antes,
                CASE WHEN _maior_ofertada > 0 THEN _maior_presente / _maior_ofertada ELSE 0 END AS _perc_depois
            FROM calculado
        )
        SELECT
            * EXCLUDE (
                _ofertada_sgp, _presente_sgp, _maior_ofertada, _maior_presente,
                _encontrado, _mudou, _perc_antes, _perc_depois
            ),
            round(_ofertada_sgp, 2) AS "{coluna_ofertada}_ANTES",
            round(_presente_sgp, 2) AS "{coluna_presente}_ANTES",
            round(_maior_ofertada, 2) AS {ofertada_sql},
            round(_maior_presente, 2) AS {presente_sql},
            _encontrado AS {COLUNA_ENCONTRADO},
            CASE
                WHEN NOT _mudou THEN '{_STATUS_SEM_ALTERACAO}'
                WHEN _perc_depois > _perc_antes THEN '{_STATUS_SUBIU}'
                WHEN _perc_depois < _perc_antes AND _perc_depois < {LIMIAR_80_PORCENTO} THEN '{_STATUS_DESCEU_ABAIXO_80}'
                WHEN _perc_depois < _perc_antes THEN '{_STATUS_DESCEU_ACIMA_80}'
                ELSE '{_STATUS_MANTEVE_PERCENTUAL}'
            END AS {COLUNA_STATUS}
        FROM classificado
    """


def calcular_frequencia(
    df_sgp: pl.DataFrame,
    df_sigeduc: pl.DataFrame,
    coluna_cpf_sgp: str | None = None,
    coluna_cpf_sigeduc: str | None = None,
    coluna_ofertada: str | None = None,
    coluna_presente: str | None = None,
) -> tuple[pl.DataFrame, pl.DataFrame, RelatorioFrequencia]:
    """Cruza `df_sgp` com `df_sigeduc` pelo CPF e atualiza a frequência.

    Parameters
    ----------
    df_sgp:
        Base atual do SGP -- precisa ter as colunas de CPF, HL_OFERTADA e
        HL_PRESENTE do período (nomes configuráveis abaixo).
    df_sigeduc:
        Base bruta do SIGEDUC -- precisa ter CPF, `HORA_AULA_DADA` e
        `FALTAS_ALUNO_HORA`.
    coluna_cpf_sgp, coluna_cpf_sigeduc, coluna_ofertada, coluna_presente:
        Nomes das colunas a usar. Se omitidos, tenta detectar automaticamente
        pelos nomes mais comuns do SGP/SIGEDUC.

    Returns
    -------
    tuple[pl.DataFrame, pl.DataFrame, RelatorioFrequencia]
        `(df_atualizado, df_alterados, relatorio)`:

        - `df_atualizado`: a base do SGP inteira (mesmo número de linhas),
          com `HL_OFERTADA`/`HL_PRESENTE` já corrigidos (maior valor entre
          SGP e SIGEDUC) -- pronta para reenviar.
        - `df_alterados`: apenas quem teve o valor alterado, com colunas
          extras `..._ANTES` (valor anterior), `STATUS_FREQUENCIA`
          (subiu/desceu/manteve percentual) e `ENCONTRADO_NO_SIGEDUC` --
          para conferência manual.
        - `relatorio`: contagens do cruzamento.

    Raises
    ------
    KeyError
        Se alguma coluna obrigatória não existir na base correspondente.
    """
    coluna_cpf_sgp_resolvida = coluna_cpf_sgp or exigir_coluna(df_sgp, COLUNAS_CPF_SGP_CANDIDATAS, "CPF (base SGP)")
    coluna_cpf_sigeduc_resolvida = coluna_cpf_sigeduc or exigir_coluna(
        df_sigeduc, COLUNAS_CPF_SIGEDUC_CANDIDATAS, "CPF (base SIGEDUC)"
    )
    coluna_ofertada_resolvida = coluna_ofertada or exigir_coluna(
        df_sgp, COLUNAS_OFERTADA_CANDIDATAS, "Horas ofertadas no período (SGP)"
    )
    coluna_presente_resolvida = coluna_presente or exigir_coluna(
        df_sgp, COLUNAS_PRESENTE_CANDIDATAS, "Horas de presença no período (SGP)"
    )

    sgp = _preparar_sgp(df_sgp, coluna_cpf_sgp_resolvida, coluna_ofertada_resolvida, coluna_presente_resolvida)
    sigeduc = _preparar_sigeduc(df_sigeduc, coluna_cpf_sigeduc_resolvida)

    sql = _construir_sql(coluna_cpf_sgp_resolvida, coluna_ofertada_resolvida, coluna_presente_resolvida)
    con = duckdb.connect()
    con.register("sgp", sgp)
    con.register("sigeduc", sigeduc)
    resultado = con.sql(sql).pl()
    con.close()

    coluna_ofertada_antes = f"{coluna_ofertada_resolvida}_ANTES"
    coluna_presente_antes = f"{coluna_presente_resolvida}_ANTES"

    df_atualizado = resultado.drop(
        [coluna_ofertada_antes, coluna_presente_antes, COLUNA_ENCONTRADO, COLUNA_STATUS]
    )
    df_alterados = resultado.filter(pl.col(COLUNA_STATUS) != _STATUS_SEM_ALTERACAO)

    # Uma só chamada a `.value_counts()` -- zipar chaves e contagens vindas de
    # duas chamadas separadas não garante a mesma ordem entre elas.
    contagem_vc = resultado[COLUNA_STATUS].value_counts()
    contagem = dict(zip(contagem_vc[COLUNA_STATUS].to_list(), contagem_vc["count"].to_list()))
    total_encontrado = int(resultado[COLUNA_ENCONTRADO].sum())

    relatorio = RelatorioFrequencia(
        coluna_cpf_sgp=coluna_cpf_sgp_resolvida,
        coluna_cpf_sigeduc=coluna_cpf_sigeduc_resolvida,
        coluna_ofertada=coluna_ofertada_resolvida,
        coluna_presente=coluna_presente_resolvida,
        total_sgp=df_sgp.height,
        total_sigeduc=df_sigeduc.height,
        total_sigeduc_apos_limpeza=sigeduc.height,
        total_com_correspondencia_sigeduc=total_encontrado,
        total_sem_correspondencia_sigeduc=df_sgp.height - total_encontrado,
        total_alterados=df_alterados.height,
        total_subiu=contagem.get(_STATUS_SUBIU, 0),
        total_desceu_abaixo_80=contagem.get(_STATUS_DESCEU_ABAIXO_80, 0),
        total_desceu_acima_80=contagem.get(_STATUS_DESCEU_ACIMA_80, 0),
        total_manteve_percentual=contagem.get(_STATUS_MANTEVE_PERCENTUAL, 0),
        total_sem_alteracao=contagem.get(_STATUS_SEM_ALTERACAO, 0),
    )

    return df_atualizado, df_alterados, relatorio


if __name__ == "__main__":
    # Auto-teste rápido -- rode com `python -m src.core.frequencia` a
    # partir da raiz do projeto. Cobre os 5 status possíveis + aluno sem
    # correspondência + CPF inválido no SGP + duplicata no SIGEDUC.
    sgp = pl.DataFrame(
        {
            "ESTUDANTE_CPF": ["1", "2", "3", "4", "5", "6", "0", "7"],
            "NOME": ["A_SOBE", "B_JA_MAIOR", "C_DESCE_ACIMA_80", "D_DESCE_ABAIXO_80", "E_MANTEVE_PERC", "F_SO_SGP", "G_CPF_INVALIDO", "H_DUPLICADO_SIGEDUC"],
            "HL_OFERTADA_PERIODO": [20, 20, 20, 20, 10, 15, 20, 20],
            "HL_PRESENTE_PERIODO": [10, 18, 19, 19, 5, 15, 10, 10],
        }
    )
    sigeduc = pl.DataFrame(
        {
            "ALUNO_CPF": ["1", "2", "3", "5", "7", "7"],
            "HORA_AULA_DADA": [20, 20, 25, 20, 20, 20],
            "FALTAS_ALUNO_HORA": [2, 15, 2, 10, 15, 5],  # última ocorrência do 7 (faltas=5) deve prevalecer
        }
    )
    # aluno 4 (D_DESCE_ABAIXO_80) precisa de um salto grande de ofertada
    sigeduc = sigeduc.vstack(pl.DataFrame({"ALUNO_CPF": ["4"], "HORA_AULA_DADA": [30], "FALTAS_ALUNO_HORA": [25]}))

    df_atualizado, df_alterados, relatorio = calcular_frequencia(sgp, sigeduc)

    print("=== Atualizado (mesmo total de linhas do SGP) ===")
    with pl.Config(tbl_cols=-1, tbl_width_chars=200):
        print(df_atualizado)
    print("\n=== Alterados ===")
    with pl.Config(tbl_cols=-1, tbl_width_chars=200):
        print(df_alterados.select("NOME", COLUNA_STATUS, "HL_OFERTADA_PERIODO_ANTES", "HL_OFERTADA_PERIODO", "HL_PRESENTE_PERIODO_ANTES", "HL_PRESENTE_PERIODO"))
    print()
    print(relatorio.model_dump_json(indent=2))

    assert df_atualizado.height == sgp.height, "nenhuma linha do SGP pode ser perdida"
    assert relatorio.total_com_correspondencia_sigeduc + relatorio.total_sem_correspondencia_sigeduc == relatorio.total_sgp
    print("\nOK: auto-teste do Módulo D passou.")
