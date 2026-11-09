"""Módulo F: Situação de Matrícula e Aprovações (SIGEDUC -> Pé de Meia).

Cruza uma base **bruta do SIGEDUC** com a base de **destino** pela
combinação de CPF e data de matrícula. A situação principal vem da
matrícula correspondente; a matrícula posterior mais recente aparece
em duas colunas adicionais:

    - `DATA_FIM_OU_APROVACAO`        <- `DATA FIM` da matrícula correspondente.
    - `DATA_CONCLUSAO_ENSINO_MEDIO`  <- `DATA ENCERRAMENTO` dessa matrícula.
    - `PERIODO`                      <- `PERÍODO` dessa matrícula.
    - `SITUACAO_MATRICULA_RECENTE`   <- `SITUAÇÃO MATRÍCULA` do registro
      correspondente (nome legado da coluna).
    - `QTD_MODULOS_APROVADOS`        <- quantas linhas *daquele CPF* (não só
      a matrícula correspondente) têm situação contendo `status_aprovado`.
    - `DATA_MATRICULA_POSTERIOR_SIGEDUC` e
      `SITUACAO_MATRICULA_POSTERIOR_SIGEDUC` <- última matrícula posterior.

Um CPF do destino que não aparece no SIGEDUC enviado mantém os valores que
já tinha (exceto `QTD_MODULOS_APROVADOS`, que fica 0) -- nenhuma linha do
destino é descartada por falta de correspondência.

Duas checagens opcionais (replicam as duas variações mais recentes do
script legado `GERANDO_APROVADOS`, ligadas por padrão como no notebook):

    - **Transferência Externa** (`aplicar_transferencia_externa`): um CPF
      cuja situação da matrícula correspondente é `'TRANSFERIDO'` e que, em todo o
      histórico do SIGEDUC enviado, nunca teve nenhuma outra situação, é
      reclassificado para `'TRANSFERÊNCIA EXTERNA'` -- ele nunca chegou a
      ter uma matrícula própria aqui, só existe por já ter vindo
      transferido de outra rede/escola.
    - **Divergência de Censo** (`aplicar_checagem_censo`): compara o código
      de Censo/INEP da matrícula correspondente com o `CO_ENTIDADE` do destino; quem
      divergir é **removido** de `df_atualizado` e devolvido à parte em
      `df_divergentes`, para conferência manual antes de decidir o que
      fazer com esses alunos.

Implementado com Polars para escolher uma linha por CPF e matrícula e
DuckDB para o cruzamento final com o destino.

As funções que só olham para o SIGEDUC (`preparar_sigeduc`,
`contar_aprovados`, `situacao_mais_recente`,
`reclassificar_transferencia_externa`) são públicas de propósito: o
Módulo G (`src/core/situacao_sigeduc.py`) as reaproveita para gerar um
resumo direto do SIGEDUC, sem nenhuma base de destino.
"""

from __future__ import annotations

import duckdb
import polars as pl

from src.core._colunas import exigir_coluna, quotar_identificador
from src.schemas.situacao_aluno import RelatorioSituacaoAluno
from src.utils.higienizacao import converter_data_robusta, limpar_cpf

COLUNAS_CPF_SIGEDUC_CANDIDATAS = ("NU CPF", "ESTUDANTE_CPF", "ALUNO_CPF", "CPF")
COLUNAS_CPF_DESTINO_CANDIDATAS = ("ESTUDANTE_CPF", "CPF")
COLUNAS_DATA_FIM_CANDIDATAS = ("DATA FIM",)
COLUNAS_DATA_ENCERRAMENTO_CANDIDATAS = ("DATA ENCERRAMENTO",)
COLUNAS_DATA_MATRICULA_SIGEDUC_CANDIDATAS = ("DATA MATRÍCULA", "DATA MATRICULA", "DATA_MATRICULA", "DT_MATRICULA")
COLUNAS_DATA_MATRICULA_DESTINO_CANDIDATAS = ("DATA_MATRICULA", "DATA MATRÍCULA", "DATA MATRICULA", "DT_MATRICULA", "ESTUDANTE_DATA_MATRICULA")
COLUNAS_PERIODO_CANDIDATAS = ("PERÍODO", "PERIODO")
COLUNAS_SITUACAO_CANDIDATAS = ("SITUACAO MATRÍCULA", "SITUAÇÃO MATRÍCULA", "SITUACAO_MATRICULA", "SITUAÇÃO_MATRÍCULA")
COLUNAS_CENSO_CANDIDATAS = ("CENSO",)
COLUNAS_CO_ENTIDADE_CANDIDATAS = ("CO_ENTIDADE",)

# Nomes das colunas escritas no destino -- os mesmos do script legado
# `GERANDO_APROVADOS`, para o resultado continuar compatível com a
# planilha de acompanhamento do Pé de Meia já em uso.
COLUNA_DESTINO_DATA_FIM = "DATA_FIM_OU_APROVACAO"
COLUNA_DESTINO_DATA_CONCLUSAO = "DATA_CONCLUSAO_ENSINO_MEDIO"
COLUNA_DESTINO_PERIODO = "PERIODO"
COLUNA_DESTINO_SITUACAO = "SITUACAO_MATRICULA_RECENTE"
COLUNA_DESTINO_QTD_APROVADOS = "QTD_MODULOS_APROVADOS"
COLUNA_DESTINO_MATRICULA_POSTERIOR = "DATA_MATRICULA_POSTERIOR_SIGEDUC"
COLUNA_DESTINO_SITUACAO_POSTERIOR = "SITUACAO_MATRICULA_POSTERIOR_SIGEDUC"

COLUNAS_DESTINO_GERADAS = (
    COLUNA_DESTINO_DATA_FIM,
    COLUNA_DESTINO_DATA_CONCLUSAO,
    COLUNA_DESTINO_PERIODO,
    COLUNA_DESTINO_SITUACAO,
    COLUNA_DESTINO_QTD_APROVADOS,
    COLUNA_DESTINO_MATRICULA_POSTERIOR,
    COLUNA_DESTINO_SITUACAO_POSTERIOR,
)

# Texto (case-insensitive) usado por padrão para contar "módulo aprovado".
# As duas versões mais recentes do script legado usam este texto; a
# primeira versão usava só 'APROVADO' -- por isso o campo continua
# configurável na UI, não fixo.
STATUS_APROVADO_PADRAO = "APROVADO EM MÓDULO"

_SITUACAO_TRANSFERIDO = "TRANSFERIDO"
SITUACAO_TRANSFERENCIA_EXTERNA = "TRANSFERÊNCIA EXTERNA"


def _data_matricula_normalizada(coluna: str) -> pl.Expr:
    return converter_data_robusta(
        pl.col(coluna).cast(pl.Utf8, strict=False)
        .str.strip_chars().str.replace(r"^['’]+\s*", "")
    ).str.to_date("%d/%m/%Y", strict=False)


def preparar_sigeduc(df: pl.DataFrame, coluna_cpf: str, coluna_data_fim: str, coluna_data_encerramento: str) -> pl.DataFrame:
    """Limpa CPF, normaliza as duas colunas de data e remove linhas sem CPF
    válido -- mesma filosofia do `_preparar_sigeduc` do Módulo D: esta é a
    base "de leitura", perder uma linha aqui só custa não conseguir
    cruzá-la, nunca faz sumir um aluno do destino.

    Pública (sem `_`) porque também é reaproveitada pelo Módulo G
    (`src/core/situacao_sigeduc.py`), que não tem base de destino."""
    return (
        df.with_columns(
            limpar_cpf(pl.col(coluna_cpf)).alias(coluna_cpf),
            converter_data_robusta(pl.col(coluna_data_fim)).alias(coluna_data_fim),
            converter_data_robusta(pl.col(coluna_data_encerramento)).alias(coluna_data_encerramento),
        )
        .filter(pl.col(coluna_cpf).is_not_null())
        .with_columns(
            pl.col(coluna_data_encerramento).str.to_date("%d/%m/%Y", strict=False).alias("_data_ordenacao")
        )
    )


def contar_aprovados(sigeduc: pl.DataFrame, coluna_cpf: str, coluna_situacao: str, status_aprovado: str) -> pl.DataFrame:
    """Uma linha por CPF com `_qtd_aprovados` = quantos registros *daquele
    CPF* (não só o mais recente) têm situação contendo `status_aprovado`
    (comparação sem diferenciar maiúsculas/minúsculas, como no notebook).
    Pública porque também é reaproveitada pelo Módulo G."""
    return (
        sigeduc.filter(
            pl.col(coluna_situacao)
            .cast(pl.Utf8, strict=False)
            .str.to_uppercase()
            .str.contains(status_aprovado.upper(), literal=True)
        )
        .group_by(coluna_cpf)
        .agg(pl.len().alias("_qtd_aprovados"))
    )


def situacao_mais_recente(sigeduc: pl.DataFrame, coluna_cpf: str, coluna_situacao: str) -> pl.DataFrame:
    """Uma linha por CPF: o registro com a `_data_ordenacao` mais recente
    (nulos primeiro, para uma data ausente nunca "vencer" uma data real do
    mesmo CPF); se nenhum registro do CPF tiver data válida, mantém o
    último registro na ordem original da planilha. Mesmo padrão do
    `deduplicar_matricula_mais_recente` do Módulo A. Devolve a linha
    **inteira** de `sigeduc` (todas as colunas originais, mais a coluna
    auxiliar `_data_ordenacao`) -- quem chama decide se seleciona só
    algumas colunas ou usa a linha completa (é o que o Módulo G faz).
    Pública porque também é reaproveitada por ele."""
    ordenado = sigeduc.sort([coluna_cpf, "_data_ordenacao"], nulls_last=False)
    return ordenado.unique(subset=[coluna_cpf], keep="last", maintain_order=True)


def reclassificar_transferencia_externa(
    mais_recente: pl.DataFrame, sigeduc: pl.DataFrame, coluna_cpf: str, coluna_situacao: str
) -> tuple[pl.DataFrame, int]:
    """Reclassifica para `SITUACAO_TRANSFERENCIA_EXTERNA` todo CPF cuja
    situação mais recente é `'TRANSFERIDO'` e que, em nenhum registro do
    SIGEDUC enviado (histórico completo, não só o mais recente), teve
    alguma outra situação -- ou seja, o único "rastro" desse aluno aqui é
    ele ter sido recebido por transferência, nunca chegou a se matricular
    de fato nesta rede. Devolve `(df, total_reclassificados)`: a flag de
    reclassificação e a troca do valor são calculadas na mesma linha do
    mesmo DataFrame, então a contagem não depende da ordem das linhas
    sobreviver a nenhum join anterior.

    Nome sem `_` (verbo, não `aplicar_...`) de propósito: evita colidir
    com o parâmetro booleano `aplicar_transferencia_externa` usado em
    `calcular_situacao_aluno` e em `resumir_situacao_sigeduc` (Módulo G),
    que também reaproveita esta função."""
    teve_outra_situacao = (
        sigeduc.with_columns(
            pl.col(coluna_situacao).cast(pl.Utf8, strict=False).str.strip_chars().str.to_uppercase().alias("_sit_norm")
        )
        .group_by(coluna_cpf)
        .agg(
            (
                pl.col("_sit_norm").is_not_null()
                & (pl.col("_sit_norm") != "")
                & (pl.col("_sit_norm") != _SITUACAO_TRANSFERIDO)
            )
            .any()
            .alias("_teve_outra_situacao")
        )
    )

    unido = mais_recente.join(teve_outra_situacao, on=coluna_cpf, how="left").with_columns(
        (
            (pl.col(coluna_situacao).cast(pl.Utf8, strict=False).str.strip_chars().str.to_uppercase() == _SITUACAO_TRANSFERIDO)
            & (~pl.col("_teve_outra_situacao").fill_null(False))
        ).alias("_reclassificar")
    )
    total_reclassificados = int(unido["_reclassificar"].sum())

    resultado = unido.with_columns(
        pl.when(pl.col("_reclassificar"))
        .then(pl.lit(SITUACAO_TRANSFERENCIA_EXTERNA))
        .otherwise(pl.col(coluna_situacao))
        .alias(coluna_situacao)
    ).drop(["_teve_outra_situacao", "_reclassificar"])

    return resultado, total_reclassificados


def _resumir_por_cpf(
    df_sigeduc: pl.DataFrame,
    coluna_cpf: str,
    coluna_data_fim: str,
    coluna_data_encerramento: str,
    coluna_periodo: str,
    coluna_situacao: str,
    status_aprovado: str,
    aplicar_transferencia_externa: bool,
    coluna_censo: str | None,
) -> tuple[pl.DataFrame, int]:
    """Monta a tabela-resumo (uma linha por CPF) usada para atualizar o
    destino: data fim, data de encerramento, período, situação mais
    recente, contagem de aprovados e (se `coluna_censo` for informada) o
    Censo do registro mais recente. Devolve também quantos CPFs foram
    reclassificados para Transferência Externa."""
    sigeduc = preparar_sigeduc(df_sigeduc, coluna_cpf, coluna_data_fim, coluna_data_encerramento)

    contagem = contar_aprovados(sigeduc, coluna_cpf, coluna_situacao, status_aprovado)
    mais_recente = situacao_mais_recente(sigeduc, coluna_cpf, coluna_situacao)

    total_reclassificados = 0
    if aplicar_transferencia_externa:
        mais_recente, total_reclassificados = reclassificar_transferencia_externa(
            mais_recente, sigeduc, coluna_cpf, coluna_situacao
        )

    colunas_selecionadas = [coluna_cpf, coluna_data_fim, coluna_data_encerramento, coluna_periodo, coluna_situacao]
    if coluna_censo is not None:
        colunas_selecionadas.append(coluna_censo)

    resumo = (
        mais_recente.select(colunas_selecionadas)
        .join(contagem, on=coluna_cpf, how="left")
        .with_columns(pl.col("_qtd_aprovados").fill_null(0))
    )
    return resumo, total_reclassificados


def _resumir_por_matricula(
    df_sigeduc: pl.DataFrame,
    coluna_cpf: str,
    coluna_matricula: str,
    coluna_data_fim: str,
    coluna_data_encerramento: str,
    coluna_periodo: str,
    coluna_situacao: str,
    aplicar_transferencia_externa: bool,
    coluna_censo: str | None,
) -> tuple[pl.DataFrame, int]:
    """Uma situação por CPF e matrícula, com a última matrícula posterior em anexo."""
    sigeduc = (
        preparar_sigeduc(df_sigeduc, coluna_cpf, coluna_data_fim, coluna_data_encerramento)
        .with_row_index("_ordem_original")
        .with_columns(
            _data_matricula_normalizada(coluna_matricula).alias("_matricula_data")
        )
    )
    com_data = sigeduc.filter(pl.col("_matricula_data").is_not_null())
    ordenado = com_data.sort(
        [coluna_cpf, "_matricula_data", "_data_ordenacao", "_ordem_original"],
        nulls_last=False,
    )
    mesma_matricula = ordenado.unique(
        subset=[coluna_cpf, "_matricula_data"], keep="last", maintain_order=True
    )
    ultima_matricula = ordenado.unique(subset=[coluna_cpf], keep="last").select(
        coluna_cpf,
        pl.col("_matricula_data").alias("_matricula_posterior_data"),
        pl.col(coluna_situacao).alias("_situacao_posterior"),
    )

    total_reclassificados = 0
    if aplicar_transferencia_externa:
        mesma_matricula, total_reclassificados = reclassificar_transferencia_externa(
            mesma_matricula, sigeduc, coluna_cpf, coluna_situacao
        )
        total_reclassificados = mesma_matricula.filter(
            pl.col(coluna_situacao) == SITUACAO_TRANSFERENCIA_EXTERNA
        ).select(coluna_cpf).n_unique()

    colunas = [coluna_cpf, "_matricula_data", coluna_data_fim, coluna_data_encerramento,
               coluna_periodo, coluna_situacao]
    if coluna_censo is not None:
        colunas.append(coluna_censo)
    resumo = (
        mesma_matricula.select(colunas)
        .join(ultima_matricula, on=coluna_cpf, how="left")
        .with_columns(
            pl.when(pl.col("_matricula_posterior_data") > pl.col("_matricula_data"))
            .then(pl.col("_matricula_posterior_data").dt.strftime("%d/%m/%Y"))
            .otherwise(None).alias("_matricula_posterior"),
            pl.when(pl.col("_matricula_posterior_data") > pl.col("_matricula_data"))
            .then(pl.col("_situacao_posterior"))
            .otherwise(None).alias("_situacao_posterior_validada"),
        )
        .drop("_matricula_posterior_data")
    )
    return resumo, total_reclassificados


def _normalizar_codigo_entidade(coluna: pl.Expr) -> pl.Expr:
    """Remove espaços e um eventual '.0' de código de escola que virou
    float sem querer -- mesma normalização usada no Módulo C para
    CO_ENTIDADE, reaplicada aqui para o par Censo/CO_ENTIDADE."""
    return coluna.cast(pl.Utf8, strict=False).str.strip_chars().str.replace_all(r"\.0$", "")


def _construir_sql_cruzamento(coluna_cpf_destino: str, incluir_censo: bool) -> str:
    cpf_destino_sql = quotar_identificador(coluna_cpf_destino)
    colunas_geradas_sql = ", ".join(quotar_identificador(c) for c in COLUNAS_DESTINO_GERADAS)

    censo_select = ", resumo._censo AS _censo_sigeduc" if incluir_censo else ""

    return f"""
        SELECT
            destino.* EXCLUDE ({colunas_geradas_sql}),
            COALESCE(resumo._data_fim, destino.{quotar_identificador(COLUNA_DESTINO_DATA_FIM)}) AS {quotar_identificador(COLUNA_DESTINO_DATA_FIM)},
            COALESCE(resumo._data_encerramento, destino.{quotar_identificador(COLUNA_DESTINO_DATA_CONCLUSAO)}) AS {quotar_identificador(COLUNA_DESTINO_DATA_CONCLUSAO)},
            COALESCE(resumo._periodo, destino.{quotar_identificador(COLUNA_DESTINO_PERIODO)}) AS {quotar_identificador(COLUNA_DESTINO_PERIODO)},
            COALESCE(resumo._situacao, destino.{quotar_identificador(COLUNA_DESTINO_SITUACAO)}) AS {quotar_identificador(COLUNA_DESTINO_SITUACAO)},
            COALESCE(contagem._qtd_aprovados, 0) AS {quotar_identificador(COLUNA_DESTINO_QTD_APROVADOS)},
            resumo._matricula_posterior AS {quotar_identificador(COLUNA_DESTINO_MATRICULA_POSTERIOR)},
            resumo._situacao_posterior_validada AS {quotar_identificador(COLUNA_DESTINO_SITUACAO_POSTERIOR)},
            resumo._cpf IS NOT NULL AS _encontrado_sigeduc
            {censo_select}
        FROM destino
        LEFT JOIN resumo ON destino.{cpf_destino_sql} = resumo._cpf
            AND destino._matricula_destino = resumo._matricula_data
        LEFT JOIN contagem ON destino.{cpf_destino_sql} = contagem._cpf
    """


def calcular_situacao_aluno(
    df_sigeduc: pl.DataFrame,
    df_destino: pl.DataFrame,
    coluna_cpf_sigeduc: str | None = None,
    coluna_cpf_destino: str | None = None,
    coluna_data_matricula_sigeduc: str | None = None,
    coluna_data_matricula_destino: str | None = None,
    coluna_data_fim: str | None = None,
    coluna_data_encerramento: str | None = None,
    coluna_periodo: str | None = None,
    coluna_situacao: str | None = None,
    status_aprovado: str = STATUS_APROVADO_PADRAO,
    aplicar_transferencia_externa: bool = True,
    aplicar_checagem_censo: bool = False,
    coluna_censo: str | None = None,
    coluna_co_entidade: str | None = None,
) -> tuple[pl.DataFrame, pl.DataFrame, RelatorioSituacaoAluno]:
    """Cruza SIGEDUC e destino por CPF e data de matrícula.

    Parameters
    ----------
    df_sigeduc:
        Base bruta do SIGEDUC -- um registro por matrícula/período, com a
        situação daquela matrícula (`'SITUAÇÃO MATRÍCULA'`).
    df_destino:
        Base de destino a atualizar (Pé de Meia/SGP) -- precisa ter (pelo
        menos) CPF e data de matrícula; as colunas geradas são criadas vazias se
        ainda não existirem.
    coluna_cpf_sigeduc, coluna_cpf_destino, coluna_data_matricula_sigeduc,
    coluna_data_matricula_destino, coluna_data_fim,
    coluna_data_encerramento, coluna_periodo, coluna_situacao:
        Nomes das colunas a usar em cada base. Se omitidos, tenta detectar
        automaticamente pelas variações mais comuns dos exports do
        SIGEDUC.
    status_aprovado:
        Texto (sem diferenciar maiúsculas/minúsculas) usado para contar
        `QTD_MODULOS_APROVADOS`: conta toda linha do SIGEDUC daquele CPF
        cuja situação contenha esse texto -- não só a matrícula correspondente.
        Default `'APROVADO EM MÓDULO'`.
    aplicar_transferencia_externa:
        Se True (padrão), reclassifica para `'TRANSFERÊNCIA EXTERNA'` todo
        CPF cuja situação da matrícula correspondente é `'TRANSFERIDO'` e que nunca teve
        nenhuma outra situação no histórico do SIGEDUC enviado.
    aplicar_checagem_censo:
        Se True, compara o Censo/INEP do SIGEDUC com o CO_ENTIDADE do
        destino; quem divergir é removido de `df_atualizado` e devolvido em
        `df_divergentes`. Default False (exige `coluna_censo` e
        `coluna_co_entidade` existirem nas respectivas bases).
    coluna_censo, coluna_co_entidade:
        Colunas usadas pela checagem de Censo. Obrigatórias (e detectadas
        automaticamente se omitidas) apenas quando `aplicar_checagem_censo`
        é True; ignoradas caso contrário.

    Returns
    -------
    tuple[pl.DataFrame, pl.DataFrame, RelatorioSituacaoAluno]
        `(df_atualizado, df_divergentes, relatorio)`:

        - `df_atualizado`: o destino inteiro (menos os divergentes de
        Censo, se essa checagem estiver ligada), com as colunas
          atualizadas -- pronto para reenvio.
        - `df_divergentes`: CPFs removidos por divergência de Censo x
          CO_ENTIDADE, para conferência manual. Sempre vazio (0 linhas) se
          `aplicar_checagem_censo` for False ou nenhuma divergência for
          encontrada.
        - `relatorio`: contagens do cruzamento.

    Raises
    ------
    KeyError
        Se alguma coluna obrigatória não existir na base correspondente
        (inclusive Censo/CO_ENTIDADE, quando a checagem está ligada).
    """
    coluna_cpf_sigeduc_r = coluna_cpf_sigeduc or exigir_coluna(df_sigeduc, COLUNAS_CPF_SIGEDUC_CANDIDATAS, "CPF (base SIGEDUC)")
    coluna_cpf_destino_r = coluna_cpf_destino or exigir_coluna(df_destino, COLUNAS_CPF_DESTINO_CANDIDATAS, "CPF (base destino)")
    coluna_data_matricula_sigeduc_r = coluna_data_matricula_sigeduc or exigir_coluna(
        df_sigeduc, COLUNAS_DATA_MATRICULA_SIGEDUC_CANDIDATAS, "Data de Matrícula (SIGEDUC)"
    )
    coluna_data_matricula_destino_r = coluna_data_matricula_destino or exigir_coluna(
        df_destino, COLUNAS_DATA_MATRICULA_DESTINO_CANDIDATAS, "Data de Matrícula (destino)"
    )
    coluna_data_fim_r = coluna_data_fim or exigir_coluna(df_sigeduc, COLUNAS_DATA_FIM_CANDIDATAS, "Data Fim (SIGEDUC)")
    coluna_data_encerramento_r = coluna_data_encerramento or exigir_coluna(
        df_sigeduc, COLUNAS_DATA_ENCERRAMENTO_CANDIDATAS, "Data Encerramento (SIGEDUC)"
    )
    coluna_periodo_r = coluna_periodo or exigir_coluna(df_sigeduc, COLUNAS_PERIODO_CANDIDATAS, "Período (SIGEDUC)")
    coluna_situacao_r = coluna_situacao or exigir_coluna(df_sigeduc, COLUNAS_SITUACAO_CANDIDATAS, "Situação Matrícula (SIGEDUC)")

    coluna_censo_r: str | None = None
    coluna_co_entidade_r: str | None = None
    if aplicar_checagem_censo:
        coluna_censo_r = coluna_censo or exigir_coluna(df_sigeduc, COLUNAS_CENSO_CANDIDATAS, "Censo/INEP (SIGEDUC)")
        coluna_co_entidade_r = coluna_co_entidade or exigir_coluna(
            df_destino, COLUNAS_CO_ENTIDADE_CANDIDATAS, "CO_ENTIDADE (destino)"
        )

    total_sigeduc = df_sigeduc.height
    total_destino = df_destino.height

    resumo, total_reclassificados = _resumir_por_matricula(
        df_sigeduc,
        coluna_cpf_sigeduc_r,
        coluna_data_matricula_sigeduc_r,
        coluna_data_fim_r,
        coluna_data_encerramento_r,
        coluna_periodo_r,
        coluna_situacao_r,
        aplicar_transferencia_externa,
        coluna_censo_r,
    )
    total_sigeduc_apos_limpeza = resumo.height  # pares distintos de CPF e matrícula

    # Renomeia para os nomes internos usados pelo SQL; a contagem de
    # aprovações é cruzada separadamente por CPF.
    renomeio = {
        coluna_cpf_sigeduc_r: "_cpf",
        coluna_data_fim_r: "_data_fim",
        coluna_data_encerramento_r: "_data_encerramento",
        coluna_periodo_r: "_periodo",
        coluna_situacao_r: "_situacao",
    }
    if coluna_censo_r is not None:
        renomeio[coluna_censo_r] = "_censo"
    resumo = resumo.rename(renomeio)
    contagem = contar_aprovados(
        df_sigeduc.with_columns(
            limpar_cpf(pl.col(coluna_cpf_sigeduc_r)).alias(coluna_cpf_sigeduc_r)
        ).filter(pl.col(coluna_cpf_sigeduc_r).is_not_null()),
        coluna_cpf_sigeduc_r, coluna_situacao_r, status_aprovado,
    ).rename({coluna_cpf_sigeduc_r: "_cpf"})

    # Limpa o CPF do destino (mesma normalização usada do lado do SIGEDUC --
    # sem isso o cruzamento por CPF não bate) e garante que o destino já tem
    # as 5 colunas geradas (vazias, se ainda não existirem) -- mesmo padrão
    # do Módulo E para não quebrar o `SELECT * EXCLUDE (...)` no SQL a
    # seguir. Nenhuma linha é descartada mesmo que o CPF do destino seja
    # inválido (mesma filosofia do Módulo D): ele só não encontra
    # correspondência no SIGEDUC.
    destino = df_destino.with_columns(
        limpar_cpf(pl.col(coluna_cpf_destino_r)).alias(coluna_cpf_destino_r)
    ).with_columns(
        _data_matricula_normalizada(coluna_data_matricula_destino_r).alias("_matricula_destino")
    ).with_columns(
        pl.lit(None, dtype=pl.Utf8).alias(coluna)
        for coluna in COLUNAS_DESTINO_GERADAS
        if coluna not in df_destino.columns
    )

    sql = _construir_sql_cruzamento(coluna_cpf_destino_r, incluir_censo=aplicar_checagem_censo)
    con = duckdb.connect()
    con.register("destino", destino)
    con.register("resumo", resumo)
    con.register("contagem", contagem)
    cruzado = con.sql(sql).pl()
    con.close()

    total_encontrado = int(cruzado["_encontrado_sigeduc"].sum())
    total_com_aprovacao = int((cruzado[COLUNA_DESTINO_QTD_APROVADOS] > 0).sum())

    total_divergentes_censo = 0
    if aplicar_checagem_censo:
        censo_norm = _normalizar_codigo_entidade(pl.col("_censo_sigeduc"))
        entidade_norm = _normalizar_codigo_entidade(pl.col(coluna_co_entidade_r))
        cruzado = cruzado.with_columns(
            (
                pl.col("_censo_sigeduc").is_not_null()
                & (censo_norm != "")
                & (censo_norm != entidade_norm)
            ).alias("_divergente_censo")
        )
        df_divergentes = cruzado.filter(pl.col("_divergente_censo")).drop(["_encontrado_sigeduc", "_censo_sigeduc", "_divergente_censo", "_matricula_destino"])
        df_atualizado = cruzado.filter(~pl.col("_divergente_censo")).drop(["_encontrado_sigeduc", "_censo_sigeduc", "_divergente_censo", "_matricula_destino"])
        total_divergentes_censo = df_divergentes.height
    else:
        df_atualizado = cruzado.drop("_encontrado_sigeduc", "_matricula_destino")
        df_divergentes = df_atualizado.head(0)

    relatorio = RelatorioSituacaoAluno(
        coluna_cpf_sigeduc=coluna_cpf_sigeduc_r,
        coluna_cpf_destino=coluna_cpf_destino_r,
        coluna_data_fim=coluna_data_fim_r,
        coluna_data_encerramento=coluna_data_encerramento_r,
        coluna_periodo=coluna_periodo_r,
        coluna_situacao=coluna_situacao_r,
        status_aprovado=status_aprovado,
        aplicar_transferencia_externa=aplicar_transferencia_externa,
        aplicar_checagem_censo=aplicar_checagem_censo,
        coluna_censo=coluna_censo_r,
        coluna_co_entidade=coluna_co_entidade_r,
        total_sigeduc=total_sigeduc,
        total_sigeduc_apos_limpeza=total_sigeduc_apos_limpeza,
        total_destino=total_destino,
        total_com_correspondencia_sigeduc=total_encontrado,
        total_sem_correspondencia_sigeduc=total_destino - total_encontrado,
        total_com_aprovacao=total_com_aprovacao,
        total_reclassificados_transferencia_externa=total_reclassificados,
        total_divergentes_censo=total_divergentes_censo,
    )

    return df_atualizado, df_divergentes, relatorio


if __name__ == "__main__":
    # Auto-teste rápido -- rode com `python -m src.core.situacao_aluno` a
    # partir da raiz do projeto. Cobre: aluno com módulos aprovados,
    # situação mais recente escolhida pela DATA ENCERRAMENTO mais nova,
    # aluno só com datas nulas (mantém o último registro), CPF inválido no
    # SIGEDUC, aluno sem correspondência no SIGEDUC, transferência externa
    # (sem outra situação) x transferido comum (tem outra situação), e
    # divergência de Censo x CO_ENTIDADE.
    sigeduc = pl.DataFrame(
        {
            "NU CPF": [
                "1", "1", "1",
                "2",
                "3", "3",
                "4",
                "5", "5",
                "6", "6",
                "999999999999",
                "7", "7",
            ],
            "DATA FIM": [
                "01/01/2025", "01/06/2025", "01/12/2025",
                "15/03/2026",
                None, None,
                "10/02/2026",
                "01/01/2025", "01/06/2025",
                "01/01/2025", "01/07/2025",
                "01/01/2026",
                "01/01/2025", "01/06/2025",
            ],
            "DATA ENCERRAMENTO": [
                "01/02/2025", "01/07/2025", "20/12/2025",  # CPF 1: mais recente = 20/12/2025
                None,  # CPF 2: sem data válida -> mantém o único registro
                None, None,  # CPF 3: nenhum registro tem data -> mantém o último (2º registro)
                "10/02/2026",  # CPF 4: só aprovação
                None, "01/07/2025",  # CPF 5: TRANSFERIDO é o mais recente, mas teve outra situação -> continua TRANSFERIDO
                None, "01/07/2025",  # CPF 6: só teve TRANSFERIDO -> vira TRANSFERÊNCIA EXTERNA
                "01/01/2026",  # CPF com mais de 11 dígitos -> inválido, descartado
                "01/02/2025", "01/07/2025",  # CPF 7: usado no teste de censo
            ],
            "PERÍODO": ["1", "2", "3", "1", "1", "2", "1", "1", "2", "1", "2", "1", "1", "2"],
            "SITUACAO MATRÍCULA": [
                "APROVADO EM MÓDULO", "REPROVADO", "APROVADO EM MÓDULO",
                "APROVADO EM MÓDULO",
                "CURSANDO", "TRANSFERIDO",
                "APROVADO EM MÓDULO",
                "CURSANDO", "TRANSFERIDO",
                "TRANSFERIDO", "TRANSFERIDO",
                "APROVADO EM MÓDULO",
                "APROVADO EM MÓDULO", "APROVADO EM MÓDULO",
            ],
            "CENSO": [
                # CPFs 1-4 e 6 têm o mesmo código do CO_ENTIDADE do destino ("1") -> sem divergência.
                "1", "1", "1", "1", "1", "1", "1", "1", "1", "1", "1", "1",
                "999", "999",  # CPF 7: Censo diverge do CO_ENTIDADE do destino (800)
            ],
        }
    )

    destino = pl.DataFrame(
        {
            "ESTUDANTE_CPF": ["1", "2", "3", "4", "5", "6", "8", "7", "9"],
            "NOME": [
                "CPF1_2APROV", "CPF2_SEMDATA", "CPF3_TODASNULAS", "CPF4", "CPF5_TRANSF_COM_HIST",
                "CPF6_TRANSF_EXTERNA", "CPF8_SEM_SIGEDUC", "CPF7_CENSO_DIVERGENTE", "CPF9_VALOR_ANTIGO_MANTIDO",
            ],
            "CO_ENTIDADE": ["1", "1", "1", "1", "1", "1", "1", "800", "1"],
            # CPF 9 já tinha valores de uma rodada anterior e não aparece no
            # SIGEDUC enviado desta vez -- precisa manter esses valores
            # (menos QTD_MODULOS_APROVADOS, que sempre reflete só a rodada atual).
            "SITUACAO_MATRICULA_RECENTE": [None, None, None, None, None, None, None, None, "APROVADO EM MÓDULO (RODADA ANTERIOR)"],
            "QTD_MODULOS_APROVADOS": [None, None, None, None, None, None, None, None, 3],
        }
    )

    sigeduc = sigeduc.with_columns(pl.Series("DATA MATRÍCULA", [
        "01/01/2025", "01/06/2025", "01/12/2025",
        "15/03/2026", "01/01/2025", "01/02/2025", "10/02/2026",
        "01/01/2025", "01/06/2025", "01/01/2025", "01/07/2025",
        "01/01/2026", "01/01/2025", "01/06/2025",
    ]))
    destino = destino.with_columns(pl.Series("DATA_MATRICULA", [
        "01/12/2025", "15/03/2026", "01/02/2025", "10/02/2026",
        "01/06/2025", "01/07/2025", "01/01/2026", "01/06/2025", "01/01/2026",
    ]))

    print("=== Sem checagens extras (só o básico) ===")
    atualizado, divergentes, relatorio = calcular_situacao_aluno(sigeduc, destino, aplicar_transferencia_externa=False)
    with pl.Config(tbl_cols=-1, tbl_width_chars=220):
        print(atualizado)
    print(relatorio.model_dump_json(indent=2))
    assert atualizado.height == destino.height, "nenhuma linha do destino pode ser perdida sem a checagem de censo"
    assert divergentes.height == 0

    # Busca por NOME (não pelo CPF, que sai zero-padded/normalizado no resultado).
    linha = lambda nome: atualizado.filter(pl.col("NOME") == nome).row(0, named=True)

    assert linha("CPF1_2APROV")[COLUNA_DESTINO_SITUACAO] == "APROVADO EM MÓDULO"  # 20/12/2025 é o mais recente
    assert linha("CPF1_2APROV")[COLUNA_DESTINO_QTD_APROVADOS] == 2  # 2 registros "APROVADO EM MÓDULO" pro CPF 1
    assert linha("CPF1_2APROV")[COLUNA_DESTINO_DATA_CONCLUSAO] == "20/12/2025"
    assert linha("CPF2_SEMDATA")[COLUNA_DESTINO_SITUACAO] == "APROVADO EM MÓDULO"  # único registro, sem data
    assert linha("CPF3_TODASNULAS")[COLUNA_DESTINO_SITUACAO] == "TRANSFERIDO"  # nenhuma data válida -> último registro (2º)
    assert linha("CPF5_TRANSF_COM_HIST")[COLUNA_DESTINO_SITUACAO] == "TRANSFERIDO"  # sem a checagem ligada, fica como veio
    assert linha("CPF8_SEM_SIGEDUC")[COLUNA_DESTINO_QTD_APROVADOS] == 0  # sem correspondência no SIGEDUC
    assert linha("CPF8_SEM_SIGEDUC")[COLUNA_DESTINO_SITUACAO] is None  # nada pra preencher, e destino não tinha a coluna
    # CPF 9 não aparece no SIGEDUC enviado: mantém a situação de uma rodada
    # anterior, mas a contagem de aprovados sempre reflete só a rodada atual (0).
    assert linha("CPF9_VALOR_ANTIGO_MANTIDO")[COLUNA_DESTINO_SITUACAO] == "APROVADO EM MÓDULO (RODADA ANTERIOR)"
    assert linha("CPF9_VALOR_ANTIGO_MANTIDO")[COLUNA_DESTINO_QTD_APROVADOS] == 0
    print("\nOK: cruzamento básico (sem checagens extras) passou.\n")

    print("=== Com Transferência Externa + Censo ===")
    atualizado2, divergentes2, relatorio2 = calcular_situacao_aluno(
        sigeduc, destino, aplicar_transferencia_externa=True, aplicar_checagem_censo=True
    )
    with pl.Config(tbl_cols=-1, tbl_width_chars=220):
        print("-- Atualizado --")
        print(atualizado2)
        print("-- Divergentes --")
        print(divergentes2)
    print(relatorio2.model_dump_json(indent=2))

    linha2 = lambda df, nome: df.filter(pl.col("NOME") == nome).row(0, named=True)

    assert linha2(atualizado2, "CPF5_TRANSF_COM_HIST")[COLUNA_DESTINO_SITUACAO] == "TRANSFERIDO"  # teve CURSANDO antes -> não é externa
    assert linha2(atualizado2, "CPF6_TRANSF_EXTERNA")[COLUNA_DESTINO_SITUACAO] == "TRANSFERÊNCIA EXTERNA"  # só teve TRANSFERIDO
    assert relatorio2.total_reclassificados_transferencia_externa == 1
    assert atualizado2.filter(pl.col("NOME") == "CPF7_CENSO_DIVERGENTE").height == 0  # saiu por divergência de censo
    assert divergentes2.height == 1
    assert linha2(divergentes2, "CPF7_CENSO_DIVERGENTE")["NOME"] == "CPF7_CENSO_DIVERGENTE"
    assert relatorio2.total_divergentes_censo == 1
    assert atualizado2.height == destino.height - 1
    print("\nOK: transferência externa + divergência de censo passaram.")

    print("\nOK: auto-teste do Módulo F passou.")
