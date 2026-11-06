"""Módulo D: Cálculo de Frequência.

Dois modos independentes, conforme o que já está disponível:

1. `calcular_frequencia` -- **atualizar** uma frequência que já existe no
   SGP. Cruza a base atual do **SGP** (o que já está lançado) com a base
   bruta do **SIGEDUC** (a fonte "de verdade" das aulas dadas e faltas) e
   devolve a base do SGP com a frequência corrigida, pronta para reenviar.

   Regras de negócio (conforme o briefing e os scripts legados
   `FREQUENCIA_2026` / `RETIFICANDO_FREQUENCIA`):

       a. `HL_OFERTADA_PERIODO`/`HL_PRESENTE_PERIODO` são lidos **direto**
          das colunas de mesmo nome na base do SIGEDUC -- desde 03/09/2026
          o SIGEDUC já manda esses dois valores prontos, então não se
          calcula mais `HL_PRESENTE_PERIODO = HORA_AULA_DADA -
          FALTAS_ALUNO_HORA` como antes (isso ainda existe, mas só no
          outro modo, `gerar_frequencia_do_sigeduc`, que parte de uma base
          SIGEDUC diferente/mais antiga sem essas colunas prontas).

       b. Cruza com o SGP pelo CPF **e** pela matrícula exata do aluno
          (`ID_SGP_MATRICULA` na base do SGP / `Matrícula SGP` na base do
          SIGEDUC) -- não basta o CPF bater, porque o mesmo aluno pode ter
          mais de uma matrícula (ex.: períodos/turmas diferentes), e
          cruzar só por CPF podia pegar a frequência de uma matrícula
          errada. Para cada matrícula encontrada nas duas bases, fica
          sempre com o **maior** valor entre o que já estava no SGP e o
          calculado a partir do SIGEDUC -- a frequência nunca deve
          *diminuir* por causa deste cruzamento. Aluno/matrícula que não
          aparece no SIGEDUC (mesmo CPF e matrícula) mantém o valor do SGP
          intocado.
       c. Para quem teve o valor alterado, classifica se o percentual de
          presença (`HL_PRESENTE / HL_OFERTADA`) subiu ou desceu -- e, se
          desceu, se ficou abaixo ou continua igual/acima de 80%.
       d. Se a base do SIGEDUC trouxer mais de uma linha para o mesmo CPF
          + matrícula, fica com a linha de **maior** `HL_PRESENTE_PERIODO`
          entre as duplicatas (não a última do arquivo) -- mesmo princípio
          do item "b" de nunca perder frequência por causa de como as
          linhas vieram organizadas na planilha.

   Nenhuma linha do SGP é descartada: a planilha devolvida em
   `df_atualizado` tem sempre o mesmo número de linhas da planilha do SGP
   enviada, pronta para reenvio. O cruzamento em si (comparar duas bases
   por CPF e ficar com o maior valor) é feito em SQL via DuckDB.

2. `gerar_frequencia_do_sigeduc` -- **gerar** uma frequência nova, do
   zero, só a partir da base bruta do SIGEDUC (sem precisar de nenhuma
   frequência já lançada no SGP) -- útil para a primeira carga do
   período. Reproduz o script legado `FREQUENCIA_2026`: além das mesmas
   `HL_OFERTADA_PERIODO`/`HL_PRESENTE_PERIODO` de cima, calcula
   `ESTUDANTE_ETAPA_DE_ENSINO` reaproveitando o Módulo B
   (`calcular_etapa_de_ensino`) e, opcionalmente, cruza por CPF com
   planilhas à parte de Terços e de ID_SGP para preencher
   `ESTUDANTE_ANO_PERIODO`/`ID_SGP_MATRICULA`.
"""

from __future__ import annotations

import duckdb
import polars as pl

from src.core._colunas import exigir_coluna, quotar_identificador
from src.core.etapa_de_ensino import COLUNA_SAIDA as _COLUNA_ETAPA_SAIDA
from src.core.etapa_de_ensino import calcular_etapa_de_ensino
from src.schemas.frequencia import RelatorioFrequencia, RelatorioFrequenciaSigeduc
from src.utils.higienizacao import limpar_cpf

COLUNAS_CPF_SGP_CANDIDATAS = ("ESTUDANTE_CPF", "CPF")
COLUNAS_CPF_SIGEDUC_CANDIDATAS = ("ESTUDANTE_CPF", "ALUNO_CPF", "CPF")
COLUNAS_OFERTADA_CANDIDATAS = ("HL_OFERTADA_PERIODO", "HL_OFERTADA_ESTUDANTE_PERIODO")
COLUNAS_PRESENTE_CANDIDATAS = ("HL_PRESENTE_PERIODO", "HL_PRESENTE_ESTUDANTE_PERIODO")
COLUNA_HORA_AULA_DADA = ("HORA_AULA_DADA",)
COLUNA_FALTAS_ALUNO_HORA = ("FALTAS_ALUNO_HORA",)

# Só para `calcular_frequencia` (atualizar): o cruzamento passa a exigir
# CPF **e** a matrícula exata do aluno -- o mesmo CPF pode ter mais de uma
# matrícula, então só o CPF não garante que a frequência do SIGEDUC é da
# matrícula certa do SGP (ver módulo docstring, item "b").
COLUNAS_MATRICULA_SGP_CANDIDATAS = ("ID_SGP_MATRICULA", "ID_SGP", "MATRICULA", "MATRÍCULA")
COLUNAS_MATRICULA_SIGEDUC_CANDIDATAS = ("MATRÍCULA SGP", "MATRICULA SGP", "MATRICULA_SGP", "ID_SGP_MATRICULA")

# --- Só para `gerar_frequencia_do_sigeduc` (planilha nova, do zero) ---
COLUNAS_ENTIDADE_SIGEDUC_CANDIDATAS = ("ESCOLA_COD_INEP", "CO_ENTIDADE")
COLUNAS_MES_REFERENCIA_CANDIDATAS = ("MES_REFERENCIA", "MÊS_REFERENCIA", "MES REFERENCIA")

# Planilha opcional de Terços (mesmo conceito/candidatos do Módulo C) --
# cruzada só por CPF, só para preencher ESTUDANTE_ANO_PERIODO.
COLUNAS_CPF_TERCO_CANDIDATAS = ("ESTUDANTE_CPF", "CPF", "NU CPF")
COLUNAS_ANO_PERIODO_TERCO_CANDIDATAS = ("ESTUDANTE_ANO_PERIODO", "ANO_PERIODO", "ANO PERÍODO", "ANO PERIODO", "TERÇO", "TERCO")

# Planilha opcional de ID_SGP -- cruzada só por CPF, só para preencher
# ID_SGP_MATRICULA.
COLUNAS_CPF_ID_SGP_CANDIDATAS = ("ESTUDANTE_CPF", "CPF", "NU CPF")
COLUNAS_ID_SGP_MATRICULA_CANDIDATAS = ("ID_SGP_MATRICULA", "ID_SGP", "MATRICULA", "MATRÍCULA")

# Layout final de `gerar_frequencia_do_sigeduc`, na ordem do script legado.
COLUNAS_FREQUENCIA_NOVA_FINAIS = (
    "ID_SGP_MATRICULA",
    "ESTUDANTE_CPF",
    "CO_ENTIDADE",
    "ESTUDANTE_ETAPA_DE_ENSINO",
    "ESTUDANTE_ANO_PERIODO",
    "NU_ANO_MATRICULA",
    "MES_REFERENCIA",
    "ANO_REFERENCIA",
    "HL_OFERTADA_PERIODO",
    "HL_PRESENTE_PERIODO",
)

LIMIAR_80_PORCENTO = 0.80

COLUNA_STATUS = "STATUS_FREQUENCIA"
COLUNA_ENCONTRADO = "ENCONTRADO_NO_SIGEDUC"

_STATUS_SUBIU = "Subiu (frequência melhorou)"
_STATUS_DESCEU_ABAIXO_80 = "Desceu (frequência piorou) - abaixo de 80%"
_STATUS_DESCEU_ACIMA_80 = "Desceu (frequência piorou) - acima de 80%"
_STATUS_MANTEVE_PERCENTUAL = "Manteve o percentual (mudou só a carga horária)"
_STATUS_SEM_ALTERACAO = "Sem alteração"


def _normalizar_matricula(coluna: pl.Expr) -> pl.Expr:
    """Remove espaços e um eventual '.0' de matrícula que virou float sem
    querer -- mesma normalização usada para código de escola em outros
    módulos do projeto (ver `movimentacao._normalizar_entidade`)."""
    return coluna.cast(pl.Utf8, strict=False).str.strip_chars().str.replace_all(r"\.0$", "")


def _preparar_sgp(
    df: pl.DataFrame, coluna_cpf: str, coluna_ofertada: str, coluna_presente: str, coluna_matricula: str
) -> pl.DataFrame:
    """Limpa o CPF (tolerante a pontuação e a zero à esquerda ausente --
    ver `limpar_cpf`), normaliza a matrícula e garante que as colunas de
    frequência sejam numéricas.

    Diferente dos módulos A/C, aqui **nenhuma linha é descartada** mesmo
    que o CPF seja inválido -- esta é a planilha que volta para reenvio no
    SGP, então perder um aluno aqui seria pior do que só não conseguir
    cruzar aquele CPF/matrícula com o SIGEDUC (ele simplesmente fica sem
    correspondência e mantém o valor original).
    """
    return df.with_columns(
        limpar_cpf(pl.col(coluna_cpf)).alias(coluna_cpf),
        pl.col(coluna_ofertada).cast(pl.Float64, strict=False).fill_null(0.0).alias(coluna_ofertada),
        pl.col(coluna_presente).cast(pl.Float64, strict=False).fill_null(0.0).alias(coluna_presente),
        _normalizar_matricula(pl.col(coluna_matricula)).alias(coluna_matricula),
    )


def _preparar_sigeduc(
    df: pl.DataFrame, coluna_cpf: str, coluna_ofertada: str, coluna_presente: str, coluna_matricula: str
) -> pl.DataFrame:
    """Limpa o CPF (tolerante a pontuação e a zero à esquerda ausente --
    ver `limpar_cpf`; é o que garante o cruzamento mesmo quando o SGP
    exporta CPF pontuado/com zero e o SIGEDUC exporta como número puro
    sem zero, ou vice-versa), normaliza a matrícula e lê HL_OFERTADA/HL_PRESENTE
    **direto** das colunas da base do SIGEDUC (já vêm prontos na fonte --
    não é mais calculado a partir de HORA_AULA_DADA/FALTAS_ALUNO_HORA, ver
    módulo docstring), remove CPF inválido e deduplica por CPF **+**
    matrícula -- quando há mais de uma linha para o mesmo CPF+matrícula,
    fica com a linha de **maior** `HL_PRESENTE` entre as duplicatas (não a
    última do arquivo) -- ver módulo docstring, item "d"."""
    preparado = (
        df.with_columns(
            limpar_cpf(pl.col(coluna_cpf)).alias("_cpf_sigeduc"),
            _normalizar_matricula(pl.col(coluna_matricula)).alias("_matricula_sigeduc"),
            pl.col(coluna_ofertada).cast(pl.Float64, strict=False).fill_null(0.0).alias("_hl_ofertada_sigeduc"),
            pl.col(coluna_presente).cast(pl.Float64, strict=False).fill_null(0.0).alias("_hl_presente_sigeduc"),
        )
        .filter(pl.col("_cpf_sigeduc").is_not_null())
        .select("_cpf_sigeduc", "_matricula_sigeduc", "_hl_ofertada_sigeduc", "_hl_presente_sigeduc")
    )
    # Ordena por HL_PRESENTE decrescente antes de deduplicar (keep="first")
    # -- assim, entre duplicatas do mesmo CPF+matrícula, sobrevive sempre a
    # de maior presença, não a que por acaso aparece por último no arquivo.
    return (
        preparado.sort("_hl_presente_sigeduc", descending=True)
        .unique(subset=["_cpf_sigeduc", "_matricula_sigeduc"], keep="first", maintain_order=True)
    )


def _construir_sql(coluna_cpf_sgp: str, coluna_ofertada: str, coluna_presente: str, coluna_matricula_sgp: str) -> str:
    cpf_sql = quotar_identificador(coluna_cpf_sgp)
    ofertada_sql = quotar_identificador(coluna_ofertada)
    presente_sql = quotar_identificador(coluna_presente)
    matricula_sql = quotar_identificador(coluna_matricula_sgp)

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
            LEFT JOIN sigeduc
                ON sgp.{cpf_sql} = sigeduc._cpf_sigeduc
                AND sgp.{matricula_sql} = sigeduc._matricula_sigeduc
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
    coluna_ofertada_sigeduc: str | None = None,
    coluna_presente_sigeduc: str | None = None,
    coluna_matricula_sgp: str | None = None,
    coluna_matricula_sigeduc: str | None = None,
) -> tuple[pl.DataFrame, pl.DataFrame, RelatorioFrequencia]:
    """Cruza `df_sgp` com `df_sigeduc` pelo CPF **e** pela matrícula exata
    do aluno, e atualiza a frequência.

    Parameters
    ----------
    df_sgp:
        Base atual do SGP -- precisa ter as colunas de CPF, matrícula
        (`ID_SGP_MATRICULA`), HL_OFERTADA e HL_PRESENTE do período (nomes
        configuráveis abaixo).
    df_sigeduc:
        Base bruta do SIGEDUC -- precisa ter CPF, matrícula (`Matrícula
        SGP`) e as colunas de HL_OFERTADA/HL_PRESENTE do período (já
        prontas na fonte desde 03/09/2026 -- não é mais calculado a partir
        de HORA_AULA_DADA/FALTAS_ALUNO_HORA, ver docstring do módulo).
    coluna_cpf_sgp, coluna_cpf_sigeduc, coluna_ofertada, coluna_presente,
    coluna_ofertada_sigeduc, coluna_presente_sigeduc, coluna_matricula_sgp,
    coluna_matricula_sigeduc:
        Nomes das colunas a usar (as do lado do SIGEDUC são
        `coluna_cpf_sigeduc`, `coluna_ofertada_sigeduc`,
        `coluna_presente_sigeduc` e `coluna_matricula_sigeduc`;
        `coluna_ofertada`/`coluna_presente`/`coluna_matricula_sgp`, apesar
        do nome, são sempre do SGP). Se omitidos, tenta detectar
        automaticamente pelos nomes mais comuns do SGP/SIGEDUC. O
        cruzamento exige CPF **e** matrícula batendo -- ver módulo
        docstring, item "b".

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
    coluna_ofertada_sigeduc_resolvida = coluna_ofertada_sigeduc or exigir_coluna(
        df_sigeduc, COLUNAS_OFERTADA_CANDIDATAS, "Horas ofertadas no período (SIGEDUC)"
    )
    coluna_presente_sigeduc_resolvida = coluna_presente_sigeduc or exigir_coluna(
        df_sigeduc, COLUNAS_PRESENTE_CANDIDATAS, "Horas de presença no período (SIGEDUC)"
    )
    coluna_matricula_sgp_resolvida = coluna_matricula_sgp or exigir_coluna(
        df_sgp, COLUNAS_MATRICULA_SGP_CANDIDATAS, "Matrícula do aluno (ID_SGP_MATRICULA, base SGP)"
    )
    coluna_matricula_sigeduc_resolvida = coluna_matricula_sigeduc or exigir_coluna(
        df_sigeduc, COLUNAS_MATRICULA_SIGEDUC_CANDIDATAS, "Matrícula do aluno (Matrícula SGP, base SIGEDUC)"
    )

    sgp = _preparar_sgp(
        df_sgp, coluna_cpf_sgp_resolvida, coluna_ofertada_resolvida, coluna_presente_resolvida, coluna_matricula_sgp_resolvida
    )
    sigeduc = _preparar_sigeduc(
        df_sigeduc,
        coluna_cpf_sigeduc_resolvida,
        coluna_ofertada_sigeduc_resolvida,
        coluna_presente_sigeduc_resolvida,
        coluna_matricula_sigeduc_resolvida,
    )

    sql = _construir_sql(coluna_cpf_sgp_resolvida, coluna_ofertada_resolvida, coluna_presente_resolvida, coluna_matricula_sgp_resolvida)
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
        coluna_ofertada_sigeduc=coluna_ofertada_sigeduc_resolvida,
        coluna_presente_sigeduc=coluna_presente_sigeduc_resolvida,
        coluna_matricula_sgp=coluna_matricula_sgp_resolvida,
        coluna_matricula_sigeduc=coluna_matricula_sigeduc_resolvida,
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


def _preparar_id_sgp_ou_terco(
    df_aux: pl.DataFrame,
    coluna_cpf_aux: str | None,
    coluna_valor_aux: str | None,
    candidatos_cpf: tuple[str, ...],
    candidatos_valor: tuple[str, ...],
    descricao_cpf: str,
    descricao_valor: str,
    coluna_cpf_saida: str,
    coluna_valor_saida: str,
) -> tuple[pl.DataFrame, str, str]:
    """Prepara uma planilha auxiliar opcional (Terços ou ID_SGP) para o
    `LEFT JOIN` por CPF em `gerar_frequencia_do_sigeduc`: resolve as
    colunas de CPF/valor, limpa o CPF e deduplica mantendo a última
    ocorrência -- mesma tolerância usada nas outras planilhas auxiliares
    do projeto (ex.: Terços no Módulo C)."""
    coluna_cpf_r = coluna_cpf_aux or exigir_coluna(df_aux, candidatos_cpf, descricao_cpf)
    coluna_valor_r = coluna_valor_aux or exigir_coluna(df_aux, candidatos_valor, descricao_valor)
    preparado = (
        df_aux.select(
            limpar_cpf(pl.col(coluna_cpf_r)).alias(coluna_cpf_saida),
            pl.col(coluna_valor_r).cast(pl.Utf8, strict=False).str.strip_chars().alias(coluna_valor_saida),
        )
        .filter(pl.col(coluna_cpf_saida).is_not_null())
        .unique(subset=[coluna_cpf_saida], keep="last", maintain_order=True)
    )
    return preparado, coluna_cpf_r, coluna_valor_r


def gerar_frequencia_do_sigeduc(
    df_sigeduc: pl.DataFrame,
    ano: int,
    coluna_cpf_sigeduc: str | None = None,
    coluna_entidade: str | None = None,
    coluna_oferta: str | None = None,
    coluna_serie: str | None = None,
    coluna_mes_referencia: str | None = None,
    coluna_hora_aula_dada: str | None = None,
    coluna_faltas_aluno_hora: str | None = None,
    df_tercos: pl.DataFrame | None = None,
    coluna_cpf_terco: str | None = None,
    coluna_ano_periodo_terco: str | None = None,
    df_id_sgp: pl.DataFrame | None = None,
    coluna_cpf_id_sgp: str | None = None,
    coluna_id_sgp_matricula: str | None = None,
) -> tuple[pl.DataFrame, RelatorioFrequenciaSigeduc]:
    """Monta uma planilha de frequência **do zero**, só a partir da base
    bruta do SIGEDUC -- reproduz o script legado `FREQUENCIA_2026`, sem
    precisar de nenhuma frequência já lançada no SGP (diferente de
    `calcular_frequencia`, que *atualiza* uma frequência existente).

    Parameters
    ----------
    df_sigeduc:
        Base bruta do SIGEDUC -- precisa ter CPF, código da entidade/escola,
        Oferta de Ensino, Série, mês de referência, `HORA_AULA_DADA` e
        `FALTAS_ALUNO_HORA`.
    ano:
        Usado em `NU_ANO_MATRICULA` e `ANO_REFERENCIA` na saída (o script
        legado tinha isso fixo em `2026`; aqui fica configurável para o
        formulário não precisar de manutenção todo ano).
    coluna_cpf_sigeduc, coluna_entidade, coluna_oferta, coluna_serie,
    coluna_mes_referencia, coluna_hora_aula_dada, coluna_faltas_aluno_hora:
        Nomes das colunas a usar. Se omitidos, tenta detectar
        automaticamente (oferta/série pela mesma lógica do Módulo B).
    df_tercos:
        Planilha **opcional** e à parte do SIGEDUC principal, só com CPF e
        o terço/ano-período do EJA (`ESTUDANTE_CPF`/`ESTUDANTE_ANO_PERIODO`
        no export típico). Cruzada por CPF exclusivamente para preencher
        `ESTUDANTE_ANO_PERIODO`. Se omitida, essa coluna sai "".
    coluna_cpf_terco, coluna_ano_periodo_terco:
        Nomes das colunas de `df_tercos`. Ignoradas se `df_tercos` for
        `None`.
    df_id_sgp:
        Planilha **opcional** e à parte do SIGEDUC principal, só com CPF e
        `ID_SGP_MATRICULA`. Cruzada por CPF exclusivamente para preencher
        `ID_SGP_MATRICULA`. Se omitida, essa coluna sai "".
    coluna_cpf_id_sgp, coluna_id_sgp_matricula:
        Nomes das colunas de `df_id_sgp`. Ignoradas se `df_id_sgp` for
        `None`.

    Returns
    -------
    tuple[pl.DataFrame, RelatorioFrequenciaSigeduc]
        `(df_frequencia, relatorio)`: uma linha por CPF válido e único
        (mantém a última ocorrência em caso de duplicata), já nas 10
        colunas oficiais (`COLUNAS_FREQUENCIA_NOVA_FINAIS`).

    Raises
    ------
    KeyError
        Se alguma coluna obrigatória não existir na base correspondente.
    """
    coluna_cpf_r = coluna_cpf_sigeduc or exigir_coluna(df_sigeduc, COLUNAS_CPF_SIGEDUC_CANDIDATAS, "CPF (SIGEDUC)")
    coluna_entidade_r = coluna_entidade or exigir_coluna(
        df_sigeduc, COLUNAS_ENTIDADE_SIGEDUC_CANDIDATAS, "Código da entidade/escola (SIGEDUC)"
    )
    coluna_mes_referencia_r = coluna_mes_referencia or exigir_coluna(
        df_sigeduc, COLUNAS_MES_REFERENCIA_CANDIDATAS, "Mês de referência (SIGEDUC)"
    )
    coluna_hora_dada_r = coluna_hora_aula_dada or exigir_coluna(
        df_sigeduc, COLUNA_HORA_AULA_DADA, "Horas de aula dada (SIGEDUC)"
    )
    coluna_faltas_r = coluna_faltas_aluno_hora or exigir_coluna(
        df_sigeduc, COLUNA_FALTAS_ALUNO_HORA, "Faltas do aluno em horas (SIGEDUC)"
    )

    # Etapa de ensino: reaproveita o Módulo B -- mesma regra do script
    # legado (EJA > Técnico/Integrado > Regular, por série 1ª-4ª), só que
    # com tolerância extra a variações de marcador ordinal (1ª/1º/1°).
    com_etapa, relatorio_etapa = calcular_etapa_de_ensino(df_sigeduc, coluna_oferta, coluna_serie)

    # Limpa CPF, converte as horas para número (inválido/ausente -> 0, como
    # no script legado) e deduplica por CPF mantendo a última ocorrência --
    # 1 linha por aluno na planilha final.
    limpo = (
        com_etapa.with_columns(
            limpar_cpf(pl.col(coluna_cpf_r)).alias("_cpf_novo"),
            pl.col(coluna_hora_dada_r).cast(pl.Float64, strict=False).fill_null(0.0).alias("_hora_dada"),
            pl.col(coluna_faltas_r).cast(pl.Float64, strict=False).fill_null(0.0).alias("_faltas"),
        )
        .filter(pl.col("_cpf_novo").is_not_null())
        .unique(subset=["_cpf_novo"], keep="last", maintain_order=True)
    )
    total_sigeduc_apos_limpeza = limpo.height
    # Conta em cima do que efetivamente sobra na planilha final -- não do
    # total pré-deduplicação, que poderia contar um NAO_MAPEADO que nem
    # chega a aparecer na saída (mesmo princípio de "métrica = o que está
    # na planilha" usado no Módulo C).
    total_etapa_nao_mapeada = int((limpo[_COLUNA_ETAPA_SAIDA] == "NAO_MAPEADO").sum())

    # --- Planilha opcional de Terços: cruzada só por CPF, só para
    # preencher ESTUDANTE_ANO_PERIODO. ---
    if df_tercos is not None:
        tercos, coluna_cpf_terco_r, coluna_ano_periodo_terco_r = _preparar_id_sgp_ou_terco(
            df_tercos,
            coluna_cpf_terco,
            coluna_ano_periodo_terco,
            COLUNAS_CPF_TERCO_CANDIDATAS,
            COLUNAS_ANO_PERIODO_TERCO_CANDIDATAS,
            "CPF (Terços)",
            "Ano/Período (Terços)",
            "_cpf_novo",
            "_ano_periodo_terco",
        )
        limpo = limpo.join(tercos, on="_cpf_novo", how="left")
        total_sem_terco = int(limpo["_ano_periodo_terco"].is_null().sum())
    else:
        coluna_cpf_terco_r = None
        coluna_ano_periodo_terco_r = None
        total_sem_terco = 0

    # --- Planilha opcional de ID_SGP: cruzada só por CPF, só para
    # preencher ID_SGP_MATRICULA. ---
    if df_id_sgp is not None:
        id_sgp, coluna_cpf_id_sgp_r, coluna_id_sgp_matricula_r = _preparar_id_sgp_ou_terco(
            df_id_sgp,
            coluna_cpf_id_sgp,
            coluna_id_sgp_matricula,
            COLUNAS_CPF_ID_SGP_CANDIDATAS,
            COLUNAS_ID_SGP_MATRICULA_CANDIDATAS,
            "CPF (ID_SGP)",
            "ID_SGP_MATRICULA (ID_SGP)",
            "_cpf_novo",
            "_id_sgp_matricula",
        )
        limpo = limpo.join(id_sgp, on="_cpf_novo", how="left")
        total_sem_id_sgp = int(limpo["_id_sgp_matricula"].is_null().sum())
    else:
        coluna_cpf_id_sgp_r = None
        coluna_id_sgp_matricula_r = None
        total_sem_id_sgp = 0

    ano_periodo_expr = (
        pl.col("_ano_periodo_terco").fill_null("") if "_ano_periodo_terco" in limpo.columns else pl.lit("")
    )
    id_sgp_matricula_expr = (
        pl.col("_id_sgp_matricula").fill_null("") if "_id_sgp_matricula" in limpo.columns else pl.lit("")
    )
    ano_texto = str(ano)

    final = limpo.with_columns(
        id_sgp_matricula_expr.alias("ID_SGP_MATRICULA"),
        pl.col("_cpf_novo").alias("ESTUDANTE_CPF"),
        pl.col(coluna_entidade_r).cast(pl.Utf8, strict=False).str.strip_chars().alias("CO_ENTIDADE"),
        ano_periodo_expr.alias("ESTUDANTE_ANO_PERIODO"),
        pl.lit(ano_texto).alias("NU_ANO_MATRICULA"),
        pl.col(coluna_mes_referencia_r).cast(pl.Utf8, strict=False).str.strip_chars().alias("MES_REFERENCIA"),
        pl.lit(ano_texto).alias("ANO_REFERENCIA"),
        pl.col("_hora_dada").round(2).alias("HL_OFERTADA_PERIODO"),
        (pl.col("_hora_dada") - pl.col("_faltas")).round(2).alias("HL_PRESENTE_PERIODO"),
    ).select(list(COLUNAS_FREQUENCIA_NOVA_FINAIS))

    relatorio = RelatorioFrequenciaSigeduc(
        coluna_cpf_sigeduc=coluna_cpf_r,
        coluna_entidade=coluna_entidade_r,
        coluna_oferta=relatorio_etapa.coluna_oferta,
        coluna_serie=relatorio_etapa.coluna_serie,
        coluna_mes_referencia=coluna_mes_referencia_r,
        coluna_hora_aula_dada=coluna_hora_dada_r,
        coluna_faltas_aluno_hora=coluna_faltas_r,
        ano=ano,
        coluna_cpf_terco=coluna_cpf_terco_r,
        coluna_ano_periodo_terco=coluna_ano_periodo_terco_r,
        coluna_cpf_id_sgp=coluna_cpf_id_sgp_r,
        coluna_id_sgp_matricula=coluna_id_sgp_matricula_r,
        total_sigeduc=df_sigeduc.height,
        total_sigeduc_apos_limpeza=total_sigeduc_apos_limpeza,
        total_etapa_nao_mapeada=total_etapa_nao_mapeada,
        total_sem_terco=total_sem_terco,
        total_sem_id_sgp=total_sem_id_sgp,
    )

    return final, relatorio


if __name__ == "__main__":
    # Auto-teste rápido -- rode com `python -m src.core.frequencia` a
    # partir da raiz do projeto. Cobre os 5 status possíveis + aluno sem
    # correspondência + CPF inválido no SGP + duplicata no SIGEDUC + CPF
    # que bate mas com matrícula diferente (não deve cruzar -- prova do
    # cruzamento CPF + matrícula) + CPF com 3 duplicatas de matrícula onde
    # a de MAIOR HL_PRESENTE não é nem a primeira nem a última do arquivo
    # (prova do item "d" do módulo docstring).
    sgp = pl.DataFrame(
        {
            "ESTUDANTE_CPF": ["1", "2", "3", "4", "5", "6", "0", "7", "8", "9"],
            "NOME": ["A_SOBE", "B_JA_MAIOR", "C_DESCE_ACIMA_80", "D_DESCE_ABAIXO_80", "E_MANTEVE_PERC", "F_SO_SGP", "G_CPF_INVALIDO", "H_DUPLICADO_SIGEDUC", "I_MATRICULA_DIFERENTE", "J_DUPLICADO_MAX_PRESENTE"],
            "ID_SGP_MATRICULA": ["101", "102", "103", "104", "105", "106", "107", "108", "109", "110"],
            "HL_OFERTADA_PERIODO": [20, 20, 20, 20, 10, 15, 20, 20, 20, 20],
            "HL_PRESENTE_PERIODO": [10, 18, 19, 19, 5, 15, 10, 10, 10, 5],
        }
    )
    # HL_OFERTADA_PERIODO/HL_PRESENTE_PERIODO já vêm prontos do SIGEDUC
    # (não são mais calculados a partir de HORA_AULA_DADA/FALTAS_ALUNO_HORA
    # neste modo -- ver módulo docstring); valores abaixo equivalem ao que
    # HORA_AULA_DADA - FALTAS_ALUNO_HORA dava antes, só pra manter as
    # mesmas asserções de status (subiu/desceu/manteve) do teste original.
    # "Matrícula SGP" (com acento e espaço, de propósito, pra também testar
    # a tolerância a acento/espaço/caixa) repete a matrícula do SGP para
    # cada CPF -- exceto o CPF 8, que aparece com matrícula diferente
    # (999 != 109) e por isso não deve cruzar, mesmo o CPF batendo.
    sigeduc = pl.DataFrame(
        {
            "ALUNO_CPF": ["1", "2", "3", "5", "7", "7", "8"],
            "Matrícula SGP": ["101", "102", "103", "105", "108", "108", "999"],
            "HL_OFERTADA_PERIODO": [20, 20, 25, 20, 20, 20, 20],
            "HL_PRESENTE_PERIODO": [18, 5, 23, 10, 5, 15, 20],  # última ocorrência do 7 (presente=15) deve prevalecer
        }
    )
    # aluno 4 (D_DESCE_ABAIXO_80) precisa de um salto grande de ofertada
    sigeduc = sigeduc.vstack(
        pl.DataFrame(
            {"ALUNO_CPF": ["4"], "Matrícula SGP": ["104"], "HL_OFERTADA_PERIODO": [30], "HL_PRESENTE_PERIODO": [5]}
        )
    )
    # CPF 9: 3 linhas duplicadas pro mesmo CPF+matrícula (110), de propósito
    # fora de ordem (baixa, ALTA, baixa) -- só passa se o dedup pegar a de
    # MAIOR HL_PRESENTE (19, no meio do arquivo), não a primeira nem a
    # última (prova de verdade do item "d", não só uma troca de keep="last"
    # por keep="first" que acertaria por coincidência de posição).
    sigeduc = sigeduc.vstack(
        pl.DataFrame(
            {
                "ALUNO_CPF": ["9", "9", "9"],
                "Matrícula SGP": ["110", "110", "110"],
                "HL_OFERTADA_PERIODO": [20, 20, 20],
                "HL_PRESENTE_PERIODO": [8, 19, 3],
            }
        )
    )

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

    # CPF 8 existe no SIGEDUC, mas com matrícula diferente (999 != 109) --
    # não deve cruzar (com a lógica antiga, só por CPF, esse aluno teria
    # "subido" para HL_PRESENTE_PERIODO=20; com CPF+matrícula, fica igual).
    assert df_alterados.filter(pl.col("NOME") == "I_MATRICULA_DIFERENTE").height == 0
    linha_8 = df_atualizado.filter(pl.col("NOME") == "I_MATRICULA_DIFERENTE").row(0, named=True)
    assert linha_8["HL_OFERTADA_PERIODO"] == 20.0
    assert linha_8["HL_PRESENTE_PERIODO"] == 10.0

    # CPF 9: 3 duplicatas no SIGEDUC pro mesmo CPF+matrícula -- tem que
    # ficar com a de MAIOR HL_PRESENTE (19, a do meio do arquivo), não a
    # primeira (8) nem a última (3). Frequência final = max(5 do SGP, 19) = 19.
    linha_9 = df_atualizado.filter(pl.col("NOME") == "J_DUPLICADO_MAX_PRESENTE").row(0, named=True)
    assert linha_9["HL_OFERTADA_PERIODO"] == 20.0
    assert linha_9["HL_PRESENTE_PERIODO"] == 19.0

    print("\nOK: auto-teste do Módulo D (atualizar) passou.")

    # --- Auto-teste extra: tolerância de FORMATO de CPF entre as duas
    # bases do modo "Atualizar" -- é comum o SGP exportar CPF pontuado e já
    # com zero à esquerda ("012.345.678-99") e o SIGEDUC exportar o mesmo
    # CPF sem pontuação e sem zero à esquerda (1234567899, porque a coluna
    # foi lida/gerada como número) -- `limpar_cpf` (usado nos dois lados,
    # ver `_preparar_sgp`/`_preparar_sigeduc`) resolve as duas formas pro
    # mesmo valor de 11 dígitos, então o cruzamento tem que funcionar
    # normalmente mesmo com os dois lados formatando o CPF de jeitos
    # diferentes. Usa tipos nativos (str pontuado de um lado, int puro do
    # outro) para simular fielmente os dois formatos, não só strings já
    # parecidas.
    sgp_formato_pontuado = pl.DataFrame(
        {
            "ESTUDANTE_CPF": ["012.345.678-99", "001.234.567-89", "123.456.789-01"],
            "ID_SGP_MATRICULA": ["201", "202", "203"],
            "HL_OFERTADA_PERIODO": [20, 20, 20],
            "HL_PRESENTE_PERIODO": [5, 5, 5],
        }
    )
    sigeduc_formato_numero = pl.DataFrame(
        {
            # mesmos 3 CPFs de cima (respectivamente 01234567899, 00123456789,
            # 12345678901), só que como número puro -- sem pontuação e sem
            # os zeros à esquerda que a base do SGP preservou.
            "ALUNO_CPF": [1234567899, 123456789, 12345678901],
            "Matrícula SGP": ["201", "202", "203"],
            "HL_OFERTADA_PERIODO": [20, 20, 20],
            "HL_PRESENTE_PERIODO": [17, 17, 17],
        }
    )
    df_formato, _, relatorio_formato = calcular_frequencia(sgp_formato_pontuado, sigeduc_formato_numero)
    assert relatorio_formato.total_com_correspondencia_sigeduc == 3, (
        "CPF pontuado+zero à esquerda de um lado e número puro+sem zero do outro devem cruzar"
    )
    assert relatorio_formato.total_sem_correspondencia_sigeduc == 0
    assert set(df_formato["HL_PRESENTE_PERIODO"].to_list()) == {17.0}, "todos deveriam ter cruzado e subido pra 17"
    print(
        "OK: CPF pontuado/com zero à esquerda (SGP) e número puro/sem zero à esquerda "
        "(SIGEDUC) cruzam normalmente."
    )

    # --- Auto-teste de gerar_frequencia_do_sigeduc (planilha nova, do
    # zero, a partir só do SIGEDUC -- script legado FREQUENCIA_2026).
    # Cobre: EJA, técnico, regular, NAO_MAPEADO, CPF inválido, CPF
    # duplicado (mantém a última ocorrência) e as duas planilhas
    # auxiliares opcionais (Terços e ID_SGP). ---
    sigeduc_novo = pl.DataFrame(
        {
            "ALUNO_CPF": ["1", "2", "3", "4", "5", "5", "0"],
            "ESCOLA_COD_INEP": ["11111111", "11111111", "22222222", "22222222", "33333333", "33333333", "44444444"],
            "OFERTA_ENSINO": [
                "EDUCACAO DE JOVENS E ADULTOS - EJA",
                "CURSO TECNICO INTEGRADO AO ENSINO MEDIO",
                "ENSINO MEDIO REGULAR",
                "CURSO SUPERIOR",
                "ENSINO MEDIO REGULAR",
                "ENSINO MEDIO REGULAR",
                "ENSINO MEDIO REGULAR",
            ],
            "SERIE": ["1ª SÉRIE", "1ª SÉRIE", "3ª SÉRIE", None, "2ª SÉRIE", "2ª SÉRIE", "1ª SÉRIE"],
            "MES_REFERENCIA": ["MAIO", "MAIO", "MAIO", "MAIO", "MAIO", "MAIO", "MAIO"],
            "HORA_AULA_DADA": [20, 20, 25, 10, 15, 30, 20],
            "FALTAS_ALUNO_HORA": [2, 5, 3, 1, 5, 8, 0],  # última ocorrência do CPF 5 (faltas=8) deve prevalecer
        }
    )

    df_freq_nova, relatorio_novo = gerar_frequencia_do_sigeduc(sigeduc_novo, ano=2026)

    print("\n=== Frequência nova (do zero, a partir do SIGEDUC) ===")
    with pl.Config(tbl_cols=-1, tbl_width_chars=200):
        print(df_freq_nova)
    print()
    print(relatorio_novo.model_dump_json(indent=2))

    # CPF "0" (só zeros) é inválido e cai fora; CPF 5 duplicado vira 1 linha
    # só -- sobram 5 linhas (CPFs 1-5).
    assert df_freq_nova.height == 5
    assert relatorio_novo.total_sigeduc == 7
    assert relatorio_novo.total_sigeduc_apos_limpeza == 5
    assert set(df_freq_nova["ESTUDANTE_CPF"].to_list()) == {
        "00000000001", "00000000002", "00000000003", "00000000004", "00000000005"
    }
    assert list(df_freq_nova.columns) == list(COLUNAS_FREQUENCIA_NOVA_FINAIS)

    linha_1 = df_freq_nova.filter(pl.col("ESTUDANTE_CPF") == "00000000001").row(0, named=True)
    assert linha_1["ESTUDANTE_ETAPA_DE_ENSINO"] == "45"  # EJA
    linha_2 = df_freq_nova.filter(pl.col("ESTUDANTE_CPF") == "00000000002").row(0, named=True)
    assert linha_2["ESTUDANTE_ETAPA_DE_ENSINO"] == "30"  # Técnico, 1ª série
    linha_3 = df_freq_nova.filter(pl.col("ESTUDANTE_CPF") == "00000000003").row(0, named=True)
    assert linha_3["ESTUDANTE_ETAPA_DE_ENSINO"] == "27"  # Regular, 3ª série
    linha_4 = df_freq_nova.filter(pl.col("ESTUDANTE_CPF") == "00000000004").row(0, named=True)
    assert linha_4["ESTUDANTE_ETAPA_DE_ENSINO"] == "NAO_MAPEADO"
    assert relatorio_novo.total_etapa_nao_mapeada == 1

    # CPF 5 duplicado -- fica só a última ocorrência (HORA_AULA_DADA=30, FALTAS=8).
    linha_5 = df_freq_nova.filter(pl.col("ESTUDANTE_CPF") == "00000000005").row(0, named=True)
    assert linha_5["HL_OFERTADA_PERIODO"] == 30.0
    assert linha_5["HL_PRESENTE_PERIODO"] == 22.0

    # Sem planilhas auxiliares: ESTUDANTE_ANO_PERIODO e ID_SGP_MATRICULA em branco.
    assert linha_1["ESTUDANTE_ANO_PERIODO"] == ""
    assert linha_1["ID_SGP_MATRICULA"] == ""
    assert linha_1["NU_ANO_MATRICULA"] == "2026"
    assert linha_1["ANO_REFERENCIA"] == "2026"
    assert relatorio_novo.coluna_cpf_terco is None
    assert relatorio_novo.coluna_cpf_id_sgp is None
    assert relatorio_novo.total_sem_terco == 0
    assert relatorio_novo.total_sem_id_sgp == 0

    # Com as duas planilhas auxiliares: CPF 1 encontrado nas duas ->
    # preenchido; CPF 999 é só ruído (não existe em nenhuma base); CPFs
    # 2-5 continuam "" (não encontrados nas auxiliares) e entram na
    # contagem de "sem correspondência".
    tercos = pl.DataFrame({"ESTUDANTE_CPF": ["1", "999"], "ESTUDANTE_ANO_PERIODO": ["2º TERÇO", "1º TERÇO"]})
    id_sgp = pl.DataFrame({"ESTUDANTE_CPF": ["1", "999"], "ID_SGP_MATRICULA": ["555", "111"]})
    df_com_aux, relatorio_com_aux = gerar_frequencia_do_sigeduc(
        sigeduc_novo, ano=2026, df_tercos=tercos, df_id_sgp=id_sgp
    )
    linha_1_aux = df_com_aux.filter(pl.col("ESTUDANTE_CPF") == "00000000001").row(0, named=True)
    assert linha_1_aux["ESTUDANTE_ANO_PERIODO"] == "2º TERÇO"
    assert linha_1_aux["ID_SGP_MATRICULA"] == "555"
    assert relatorio_com_aux.coluna_cpf_terco is not None
    assert relatorio_com_aux.coluna_cpf_id_sgp is not None
    assert relatorio_com_aux.total_sem_terco == 4
    assert relatorio_com_aux.total_sem_id_sgp == 4

    linha_2_aux = df_com_aux.filter(pl.col("ESTUDANTE_CPF") == "00000000002").row(0, named=True)
    assert linha_2_aux["ESTUDANTE_ANO_PERIODO"] == ""
    assert linha_2_aux["ID_SGP_MATRICULA"] == ""

    print("\nOK: auto-teste do Módulo D (gerar do zero) passou.")
