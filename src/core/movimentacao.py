"""Módulo C: Reconciliação entre a base SGP atual e o SIGEDUC.

Compara a base **SGP atual** (quem já está cadastrado, no layout oficial --
`ESTUDANTE_CPF`, `CO_ENTIDADE`, `ESTUDANTE_ETAPA_DE_ENSINO`, ...) com uma ou
mais planilhas brutas do **SIGEDUC** (a fonte de verdade -- `NU CPF`,
`CENSO`, `SÉRIE`, `OFERTA DE ENSINO`, `SITUAÇÃO MATRÍCULA`, ...) e devolve
dois relatórios:

    - **Cadastro** (`df_cadastro`): CPFs que aparecem no SIGEDUC mas ainda
      não existem na base SGP -- formatados prontos no layout oficial de
      cadastro do SGP (24 colunas), para importar como aluno novo, mais
      uma 25ª coluna extra (`SITUACAO_ALUNO`, fora do layout oficial) com
      a situação de matrícula mais recente no SIGEDUC, só para conferência.
      `ESTUDANTE_ANO_PERIODO` (terço do EJA) sai preenchida quando uma
      planilha opcional de Terços é enviada (cruzada por CPF).
    - **Movimentação** (`df_movimentacao`): só os CPFs da base SGP que
      tiveram `'trans. escola'` ou `'trans. mod'`, já classificados em
      `MATRICULA_SITUACAO_FINAL` com `DATA_FIM_OU_APROVACAO` preenchida.
      Quem não mudou nada, ou foi classificado como `'cancelado'`, NÃO
      aparece nesta planilha (mas ambos continuam contados em
      `relatorio.total_sem_alteracao`/`relatorio.total_cancelado`) --
      diferente do Módulo D/F, que devolvem a base inteira para reenvio,
      este é um relatório de "quem se mexeu (e continua ativo)".

Para decidir se um aluno já cadastrado "se mexeu", cada CPF do SGP passa
por duas buscas independentes no SIGEDUC:

    1. **Mesma matrícula** (histórico da matrícula de origem): registros do
       SIGEDUC do mesmo CPF *na mesma escola* (CO_ENTIDADE/CENSO) em que
       ele está hoje no SGP. Fica com o mais recente (por Data
       Encerramento, caindo para Data Atualização do Cadastro quando
       aquela estiver vazia). Se esse registro tiver uma data de
       encerramento, entende-se que aquela matrícula específica foi
       formalmente fechada no SIGEDUC.
    2. **Última matrícula ativa**: entre TODOS os registros do SIGEDUC
       daquele CPF (qualquer escola), ignorando quem está
       `TRANSFERIDO`/`CANCELADO`, fica com o mais recente -- isto é, onde
       o aluno está *hoje*, segundo o SIGEDUC.

A partir das duas buscas:

    - Matrícula de origem fechada (tem data) + escola atual diferente da
      do SGP -> `'trans. escola'`.
    - Matrícula de origem fechada (tem data) + mesma escola, mas
      modalidade (EJA x Regular/Técnico) diferente -> `'trans. mod'`.
    - Matrícula de origem **sem** data de encerramento + mesma escola +
      modalidade diferente -> `'trans. mod'`, a não ser que o CPF tenha
      histórico de alguma situação `CANCELADO` no SIGEDUC, caso em que
      vira `'cancelado'`.
    - Nenhuma das condições acima -> nada é preenchido (sem alteração).

Implementado com Polars para a parte de "uma linha por CPF, priorizando
data real" (mesmo padrão do Módulo A e do Módulo F) e para o restante da
lógica -- não precisa de SQL porque os cruzamentos aqui são todos por uma
chave só (CPF), sem o tipo de `CASE WHEN` complexo que justifica DuckDB em
outros módulos.
"""

from __future__ import annotations

import polars as pl

from src.core._colunas import encontrar_coluna, exigir_coluna
from src.core.etapa_de_ensino import (
    CODIGOS_EJA,
    CODIGOS_REGULAR_TECNICO,
    COLUNAS_OFERTA_CANDIDATAS,
    COLUNAS_SERIE_CANDIDATAS,
    calcular_etapa_de_ensino,
)
from src.schemas.movimentacao import RelatorioMovimentacao
from src.utils.higienizacao import converter_data_robusta, limpar_cpf

# --- Colunas "estruturais" (afetam o cruzamento/classificação) ---
COLUNAS_CPF_SGP_CANDIDATAS = ("ESTUDANTE_CPF", "CPF")
COLUNAS_ENTIDADE_SGP_CANDIDATAS = ("CO_ENTIDADE",)
COLUNAS_ETAPA_SGP_CANDIDATAS = ("ESTUDANTE_ETAPA_DE_ENSINO",)

COLUNAS_CPF_SIGEDUC_CANDIDATAS = ("NU CPF", "ESTUDANTE_CPF", "ALUNO_CPF", "CPF")
COLUNAS_CENSO_SIGEDUC_CANDIDATAS = ("CENSO",)
COLUNAS_SITUACAO_SIGEDUC_CANDIDATAS = (
    "SITUACAO MATRÍCULA",
    "SITUAÇÃO MATRÍCULA",
    "SITUACAO_MATRICULA",
    "SITUAÇÃO_MATRÍCULA",
    "SITUAÇÃO",
    "SITUACAO",
)
COLUNAS_DATA_ENCERRAMENTO_SIGEDUC_CANDIDATAS = ("DATA ENCERRAMENTO",)
COLUNAS_DATA_ATUALIZACAO_SIGEDUC_CANDIDATAS = (
    "DATA ATUALIZAÇÃO DO CADASTRO",
    "DATA ATUALIZACAO DO CADASTRO",
    "DATA ATUALIZAÇÃO",
    "DATA ATUALIZACAO",
)
COLUNAS_DATA_MATRICULA_SIGEDUC_CANDIDATAS = ("DATA MATRÍCULA", "DATA MATRICULA")

# --- Planilha opcional de Terços (SIGEDUC) -- só CPF + ano/período do EJA,
# cruzada exclusivamente para preencher ESTUDANTE_ANO_PERIODO no Cadastro.
COLUNAS_CPF_TERCO_CANDIDATAS = ("ESTUDANTE_CPF", "CPF", "NU CPF")
COLUNAS_ANO_PERIODO_TERCO_CANDIDATAS = ("ESTUDANTE_ANO_PERIODO", "ANO_PERIODO", "ANO PERÍODO", "ANO PERIODO", "TERÇO", "TERCO")

# --- Colunas só usadas para formatar o relatório de Cadastro -- sempre
# detectadas automaticamente (sem parâmetro dedicado: são muitas, e nenhuma
# delas afeta o cruzamento em si). Ausente na planilha enviada = fica "".
COLUNAS_NOME_CANDIDATAS = ("ALUNO", "NOME", "ESTUDANTE_NOME")
COLUNAS_DATA_NASCIMENTO_CANDIDATAS = ("DATA NASCIMENTO", "DATA DE NASCIMENTO")
COLUNAS_SEXO_CANDIDATAS = ("SEXO",)
COLUNAS_RACA_COR_CANDIDATAS = ("RAÇA/COR", "RACA/COR", "RAÇA COR", "RACA COR")
COLUNAS_RESPONSAVEL_CANDIDATAS = ("RESPONSÁVEL", "RESPONSAVEL")
COLUNAS_PAI_CANDIDATAS = ("PAI",)
COLUNAS_MAE_CANDIDATAS = ("MÃE", "NOME DA MÃE", "MAE", "NOME DA MAE")
COLUNAS_MUNICIPIO_UF_CANDIDATAS = ("MUNICÍPIO - UF", "MUNICIPIO - UF", "MUNICÍPIO-UF", "MUNICIPIO-UF")
COLUNAS_POSSUI_DEFICIENCIA_CANDIDATAS = ("POSSUI DEFICIÊNCIA", "POSSUI DEFICIENCIA")
COLUNAS_DEFICIENCIAS_CANDIDATAS = ("DEFICIÊNCIAS", "DEFICIENCIAS")
COLUNAS_ESCOLA_CANDIDATAS = ("ESCOLA",)
COLUNAS_ANO_REFERENCIA_CANDIDATAS = ("ANO REFERÊNCIA", "ANO REFERENCIA")
COLUNAS_TURNO_CANDIDATAS = ("TURNO",)
COLUNAS_DATA_INICIO_PERIODO_CANDIDATAS = (
    "DATA INÍCIO DO PERÍODO LETIVO",
    "DATA INICIO DO PERIODO LETIVO",
    "DATA INÍCIO",
    "DATA INICIO",
)

_SITUACAO_TRANSFERIDO = "TRANSFERIDO"
_SITUACAO_CANCELADO = "CANCELADO"

_SITUACAO_TRANS_ESCOLA = "trans. escola"
_SITUACAO_TRANS_MOD = "trans. mod"
_SITUACAO_CANCELADO_SAIDA = "cancelado"

COLUNA_SITUACAO_FINAL = "MATRICULA_SITUACAO_FINAL"
COLUNA_DATA_FIM_OU_APROVACAO = "DATA_FIM_OU_APROVACAO"

# Layout oficial de cadastro do SGP -- mesma ordem/nomes do script legado.
COLUNAS_CADASTRO_FINAIS = (
    "ESTUDANTE_CPF",
    "ESTUDANTE_NOME",
    "ESTUDANTE_DT_NASCIMENTO",
    "ESTUDANTE_SEXO",
    "ESTUDANTE_RACA_COR",
    "ESTUDANTE_QUILOMBOLA",
    "ESTUDANTE_NACIONALIDADE",
    "ESTUDANTE_RESPONSAVEL_1_NOME",
    "ESTUDANTE_VINCULO_RESPONSAVEL_1",
    "ESTUDANTE_CO_UF_RES",
    "ESTUDANTE_CO_MUNICIPIO_RES",
    "ESTUDANTE_DEFICIENCIA",
    "ESTUDANTE_APOIO_PEDAGOGICO",
    "CO_ENTIDADE",
    "NO_ENTIDADE",
    "TIPO_ATENDIMENTO_ESPECIALIZADO",
    "ESTUDANTE_ETAPA_DE_ENSINO",
    "NU_ANO_MATRICULA",
    "DATA_INICIO_MATRICULA",
    "ESTUDANTE_MATRICULA_INTEGRAL",
    "ESTUDANTE_ANO_PERIODO",
    "MODALIDADE_ENSINO_ESTUDANTE",
    "DATA_INICIO_PERIODO_LETIVO",
    "TURMA_LOCALIZACAO",
)

# Coluna extra do relatório de Cadastro, fora do layout oficial das 24
# acima -- situação de matrícula do SIGEDUC, só para conferência antes de
# importar (pedida à parte pelo usuário).
COLUNA_CADASTRO_SITUACAO_EXTRA = "SITUACAO_ALUNO"


def _normalizar_entidade(coluna: pl.Expr) -> pl.Expr:
    """Remove espaços e um eventual '.0' de código de escola que virou
    float sem querer -- mesma normalização usada para CENSO/CO_ENTIDADE em
    todo o projeto (ver `situacao_aluno._normalizar_codigo_entidade`)."""
    return coluna.cast(pl.Utf8, strict=False).str.strip_chars().str.replace_all(r"\.0$", "")


def _texto_upper(coluna: str) -> pl.Expr:
    return pl.col(coluna).cast(pl.Utf8, strict=False).str.strip_chars().str.to_uppercase()


def _grupo_modalidade(coluna_etapa: str) -> pl.Expr:
    """`'EJA'` / `'REGULAR_TECNICO'` / `'OUTRO'` a partir de um código de
    `ESTUDANTE_ETAPA_DE_ENSINO`, reaproveitando os grupos já definidos no
    Módulo B (mesmo padrão que o Módulo C sempre usou)."""
    col = pl.col(coluna_etapa)
    return (
        pl.when(col.is_in(CODIGOS_EJA))
        .then(pl.lit("EJA"))
        .when(col.is_in(CODIGOS_REGULAR_TECNICO))
        .then(pl.lit("REGULAR_TECNICO"))
        .otherwise(pl.lit("OUTRO"))
    )


def _valor_ou_vazio(df: pl.DataFrame, candidatos: tuple[str, ...]) -> pl.Expr:
    """Valor da coluna encontrada (tolerante a acento/espaço/caixa) entre
    `candidatos`, ou `""` literal se nenhuma existir -- equivalente ao
    `df.get(coluna, '')` do script legado, mas sem depender do nome exato."""
    nome = encontrar_coluna(df, candidatos)
    if nome is None:
        return pl.lit("")
    return pl.col(nome).cast(pl.Utf8, strict=False).fill_null("")


def _expr_sexo(df: pl.DataFrame) -> pl.Expr:
    sexo = _valor_ou_vazio(df, COLUNAS_SEXO_CANDIDATAS).str.to_uppercase().str.strip_chars()
    return pl.when(sexo == "M").then(pl.lit("1")).when(sexo == "F").then(pl.lit("2")).otherwise(pl.lit(""))


def _expr_raca_cor(raca_upper: pl.Expr) -> pl.Expr:
    return (
        pl.when(raca_upper.str.contains("BRANC", literal=True))
        .then(pl.lit("1"))
        .when(raca_upper.str.contains("PRET", literal=True))
        .then(pl.lit("2"))
        .when(raca_upper.str.contains("PARD", literal=True))
        .then(pl.lit("3"))
        .when(raca_upper.str.contains("INDÍGENA", literal=True) | raca_upper.str.contains("INDIGENA", literal=True))
        .then(pl.lit("4"))
        .when(raca_upper.str.contains("AMAREL", literal=True))
        .then(pl.lit("5"))
        .otherwise(pl.lit("0"))
    )


def _expr_deficiencia(df: pl.DataFrame) -> pl.Expr:
    possui = _valor_ou_vazio(df, COLUNAS_POSSUI_DEFICIENCIA_CANDIDATAS).str.to_uppercase().str.strip_chars()
    defs = _valor_ou_vazio(df, COLUNAS_DEFICIENCIAS_CANDIDATAS).str.to_uppercase()

    pares = (
        ("BAIXA VISAO", "1"),
        ("BAIXA VISÃO", "1"),
        ("MONOCULAR", "2"),
        ("CEGUEIRA", "3"),
        ("AUDITIVA", "4"),
        ("SURDEZ", "5"),
        ("SURDOCEGUEIRA", "6"),
        ("FISICA", "7"),
        ("FÍSICA", "7"),
        ("INTELECTUAL", "8"),
        ("AUTISTA", "9"),
        ("ALTAS HABILIDADES", "10"),
        ("SUPERDOTACAO", "10"),
        ("SUPERDOTAÇÃO", "10"),
    )
    partes = [pl.when(defs.str.contains(padrao, literal=True)).then(pl.lit(codigo)) for padrao, codigo in pares]
    codigos = pl.concat_list(partes).list.drop_nulls().list.unique(maintain_order=True).list.join(";")

    return pl.when(possui != "SIM").then(pl.lit("11")).when(codigos == "").then(pl.lit("0")).otherwise(codigos)


def _expr_modalidade_ensino(oferta_upper: pl.Expr) -> pl.Expr:
    return (
        pl.when(oferta_upper.str.contains("EJA", literal=True))
        .then(pl.lit("3"))
        .when(oferta_upper.str.contains("ESPECIAL", literal=True))
        .then(pl.lit("2"))
        .when(
            oferta_upper.str.contains("PROFISSIONAL", literal=True)
            | oferta_upper.str.contains("TÉCNIC", literal=True)
            | oferta_upper.str.contains("TECNIC", literal=True)
        )
        .then(pl.lit("4"))
        .when(oferta_upper.str.contains("MÉDIO", literal=True) | oferta_upper.str.contains("MEDIO", literal=True))
        .then(pl.lit("1"))
        .otherwise(pl.lit("0"))
    )


def _montar_cadastro(
    sigeduc_novos: pl.DataFrame, coluna_cpf: str, coluna_censo: str, coluna_situacao: str
) -> pl.DataFrame:
    """Formata `sigeduc_novos` (já deduplicado, 1 linha por CPF) no layout
    oficial de cadastro do SGP -- réplica das 24 regras do script legado de
    Cadastro, com a diferença de que cada coluna de origem é localizada de
    forma tolerante a acento/espaço/caixa em vez de nome exato. Acrescenta
    uma 25ª coluna (`COLUNA_CADASTRO_SITUACAO_EXTRA`), fora do layout
    oficial, com a situação de matrícula do SIGEDUC -- só para conferência,
    a pedido do usuário. `ESTUDANTE_ANO_PERIODO` (terço do EJA) sai
    preenchido quando `sigeduc_novos` já chega com a coluna auxiliar
    `_ano_periodo_terco` (join feito em `reconciliar_sgp_sigeduc` com a
    planilha opcional de Terços); senão fica "" como no script legado."""
    resp = _valor_ou_vazio(sigeduc_novos, COLUNAS_RESPONSAVEL_CANDIDATAS)
    pai = _valor_ou_vazio(sigeduc_novos, COLUNAS_PAI_CANDIDATAS).str.to_uppercase().str.strip_chars()
    mae = _valor_ou_vazio(sigeduc_novos, COLUNAS_MAE_CANDIDATAS).str.to_uppercase().str.strip_chars()
    resp_upper = resp.str.to_uppercase().str.strip_chars()
    eh_pai_ou_mae = (resp_upper != "") & ((resp_upper == pai) | (resp_upper == mae))

    raca_upper = _valor_ou_vazio(sigeduc_novos, COLUNAS_RACA_COR_CANDIDATAS).str.to_uppercase()
    mun_uf = _valor_ou_vazio(sigeduc_novos, COLUNAS_MUNICIPIO_UF_CANDIDATAS)
    turno_upper = _valor_ou_vazio(sigeduc_novos, COLUNAS_TURNO_CANDIDATAS).str.to_uppercase()
    oferta_upper = _valor_ou_vazio(sigeduc_novos, COLUNAS_OFERTA_CANDIDATAS).str.to_uppercase()
    # `_ano_periodo_terco` só existe quando uma planilha de Terços foi
    # cruzada em `reconciliar_sgp_sigeduc` (join deixado pronto antes de
    # chamar esta função) -- sem ela, mantém o "" do script legado.
    ano_periodo = (
        pl.col("_ano_periodo_terco").cast(pl.Utf8, strict=False).fill_null("")
        if "_ano_periodo_terco" in sigeduc_novos.columns
        else pl.lit("")
    )

    resultado = sigeduc_novos.select(
        pl.col(coluna_cpf).alias("ESTUDANTE_CPF"),
        _valor_ou_vazio(sigeduc_novos, COLUNAS_NOME_CANDIDATAS).alias("ESTUDANTE_NOME"),
        converter_data_robusta(_valor_ou_vazio(sigeduc_novos, COLUNAS_DATA_NASCIMENTO_CANDIDATAS)).alias(
            "ESTUDANTE_DT_NASCIMENTO"
        ),
        _expr_sexo(sigeduc_novos).alias("ESTUDANTE_SEXO"),
        _expr_raca_cor(raca_upper).alias("ESTUDANTE_RACA_COR"),
        pl.when(raca_upper.str.contains("QUILOMBO", literal=True))
        .then(pl.lit("1"))
        .otherwise(pl.lit("2"))
        .alias("ESTUDANTE_QUILOMBOLA"),
        pl.lit("0").alias("ESTUDANTE_NACIONALIDADE"),
        resp.alias("ESTUDANTE_RESPONSAVEL_1_NOME"),
        pl.when(eh_pai_ou_mae).then(pl.lit("1")).otherwise(pl.lit("0")).alias("ESTUDANTE_VINCULO_RESPONSAVEL_1"),
        pl.when(mun_uf.str.to_uppercase().str.ends_with("AL")).then(pl.lit("27")).otherwise(mun_uf).alias(
            "ESTUDANTE_CO_UF_RES"
        ),
        mun_uf.alias("ESTUDANTE_CO_MUNICIPIO_RES"),
        _expr_deficiencia(sigeduc_novos).alias("ESTUDANTE_DEFICIENCIA"),
        pl.lit("0").alias("ESTUDANTE_APOIO_PEDAGOGICO"),
        pl.col(coluna_censo).alias("CO_ENTIDADE"),
        _valor_ou_vazio(sigeduc_novos, COLUNAS_ESCOLA_CANDIDATAS).alias("NO_ENTIDADE"),
        pl.lit("0").alias("TIPO_ATENDIMENTO_ESPECIALIZADO"),
        pl.col("ESTUDANTE_ETAPA_DE_ENSINO"),
        _valor_ou_vazio(sigeduc_novos, COLUNAS_ANO_REFERENCIA_CANDIDATAS).alias("NU_ANO_MATRICULA"),
        converter_data_robusta(pl.col("_data_matricula_str")).alias("DATA_INICIO_MATRICULA"),
        pl.when(turno_upper.str.contains("INTEGRAL", literal=True))
        .then(pl.lit("1"))
        .otherwise(pl.lit("0"))
        .alias("ESTUDANTE_MATRICULA_INTEGRAL"),
        ano_periodo.alias("ESTUDANTE_ANO_PERIODO"),
        _expr_modalidade_ensino(oferta_upper).alias("MODALIDADE_ENSINO_ESTUDANTE"),
        converter_data_robusta(_valor_ou_vazio(sigeduc_novos, COLUNAS_DATA_INICIO_PERIODO_CANDIDATAS)).alias(
            "DATA_INICIO_PERIODO_LETIVO"
        ),
        pl.lit("1").alias("TURMA_LOCALIZACAO"),
        pl.col(coluna_situacao).cast(pl.Utf8, strict=False).str.strip_chars().fill_null("").alias(
            COLUNA_CADASTRO_SITUACAO_EXTRA
        ),
    )
    return resultado.select(list(COLUNAS_CADASTRO_FINAIS) + [COLUNA_CADASTRO_SITUACAO_EXTRA]).sort("ESTUDANTE_CPF")


def reconciliar_sgp_sigeduc(
    df_sgp: pl.DataFrame,
    df_sigeduc: pl.DataFrame,
    coluna_cpf_sgp: str | None = None,
    coluna_entidade_sgp: str | None = None,
    coluna_etapa_sgp: str | None = None,
    coluna_cpf_sigeduc: str | None = None,
    coluna_censo_sigeduc: str | None = None,
    coluna_situacao_sigeduc: str | None = None,
    coluna_data_encerramento_sigeduc: str | None = None,
    coluna_data_matricula_sigeduc: str | None = None,
    coluna_oferta_sigeduc: str | None = None,
    coluna_serie_sigeduc: str | None = None,
    df_tercos: pl.DataFrame | None = None,
    coluna_cpf_terco: str | None = None,
    coluna_ano_periodo_terco: str | None = None,
) -> tuple[pl.DataFrame, pl.DataFrame, RelatorioMovimentacao]:
    """Reconcilia a base SGP atual com o SIGEDUC: quem falta cadastrar e
    quem já cadastrado se movimentou.

    Parameters
    ----------
    df_sgp:
        Base atual do SGP (Pé de Meia), já no layout oficial -- precisa ter
        (pelo menos) CPF, CO_ENTIDADE e Etapa de Ensino.
    df_sigeduc:
        Planilha(s) bruta(s) do SIGEDUC **já concatenadas** (EJA + Técnico
        + Médio, por exemplo) -- um registro por matrícula/período.
    coluna_cpf_sgp, coluna_entidade_sgp, coluna_etapa_sgp,
    coluna_cpf_sigeduc, coluna_censo_sigeduc, coluna_situacao_sigeduc,
    coluna_data_encerramento_sigeduc, coluna_data_matricula_sigeduc,
    coluna_oferta_sigeduc, coluna_serie_sigeduc:
        Nomes das colunas "estruturais" (as que participam do cruzamento ou
        da classificação). Se omitidas, tenta detectar automaticamente
        pelas variações mais comuns dos exports do SGP/SIGEDUC. As demais
        ~15 colunas usadas só para formatar o relatório de Cadastro (nome,
        data de nascimento, sexo, raça/cor, responsável, etc.) são sempre
        detectadas automaticamente, sem parâmetro dedicado -- ausentes na
        planilha enviada, ficam em branco no cadastro gerado.
    df_tercos:
        Planilha **opcional** e à parte do SIGEDUC principal, só com CPF e
        o terço/ano-período do EJA (`ESTUDANTE_CPF`/`ESTUDANTE_ANO_PERIODO`
        no export típico). Cruzada por CPF exclusivamente para preencher
        `ESTUDANTE_ANO_PERIODO` no Cadastro -- não participa do
        cruzamento SGP x SIGEDUC nem da classificação de Movimentação. Se
        omitida, `ESTUDANTE_ANO_PERIODO` sai "" (mesmo comportamento de
        antes desse parâmetro existir).
    coluna_cpf_terco, coluna_ano_periodo_terco:
        Nomes das colunas de `df_tercos`. Se omitidas (e `df_tercos` for
        passada), tenta detectar automaticamente. Ignoradas se `df_tercos`
        for `None`.

    Returns
    -------
    tuple[pl.DataFrame, pl.DataFrame, RelatorioMovimentacao]
        `(df_cadastro, df_movimentacao, relatorio)`:

        - `df_cadastro`: CPFs do SIGEDUC ausentes do SGP, já deduplicados
          (prioriza situação `MATRICULADO`, depois a Data Matrícula mais
          recente) e formatados nas 24 colunas oficiais de cadastro
          (`ESTUDANTE_ANO_PERIODO` vem de `df_tercos`, cruzado por CPF,
          quando essa planilha é enviada), mais a coluna extra
          `SITUACAO_ALUNO` (situação de matrícula no SIGEDUC, fora do
          layout oficial, só informativa).
        - `df_movimentacao`: só os CPFs da base SGP com `'trans. escola'`
          ou `'trans. mod'`, com `MATRICULA_SITUACAO_FINAL` e
          `DATA_FIM_OU_APROVACAO` preenchidas. Quem não teve nenhuma
          mudança, ou foi classificado como `'cancelado'`, não entra
          nesta planilha (mas ambos continuam contados em
          `relatorio.total_sem_alteracao`/`relatorio.total_cancelado`).
        - `relatorio`: contagens da reconciliação.

    Raises
    ------
    KeyError
        Se alguma coluna estrutural não existir na base correspondente.
    """
    coluna_cpf_sgp_r = coluna_cpf_sgp or exigir_coluna(df_sgp, COLUNAS_CPF_SGP_CANDIDATAS, "CPF (base SGP)")
    coluna_entidade_sgp_r = coluna_entidade_sgp or exigir_coluna(
        df_sgp, COLUNAS_ENTIDADE_SGP_CANDIDATAS, "CO_ENTIDADE (base SGP)"
    )
    coluna_etapa_sgp_r = coluna_etapa_sgp or exigir_coluna(
        df_sgp, COLUNAS_ETAPA_SGP_CANDIDATAS, "Etapa de Ensino (base SGP)"
    )
    coluna_cpf_sigeduc_r = coluna_cpf_sigeduc or exigir_coluna(df_sigeduc, COLUNAS_CPF_SIGEDUC_CANDIDATAS, "CPF (SIGEDUC)")
    coluna_censo_sigeduc_r = coluna_censo_sigeduc or exigir_coluna(
        df_sigeduc, COLUNAS_CENSO_SIGEDUC_CANDIDATAS, "CENSO (SIGEDUC)"
    )
    coluna_situacao_sigeduc_r = coluna_situacao_sigeduc or exigir_coluna(
        df_sigeduc, COLUNAS_SITUACAO_SIGEDUC_CANDIDATAS, "Situação Matrícula (SIGEDUC)"
    )
    coluna_data_encerramento_sigeduc_r = coluna_data_encerramento_sigeduc or exigir_coluna(
        df_sigeduc, COLUNAS_DATA_ENCERRAMENTO_SIGEDUC_CANDIDATAS, "Data Encerramento (SIGEDUC)"
    )
    coluna_data_matricula_sigeduc_r = coluna_data_matricula_sigeduc or exigir_coluna(
        df_sigeduc, COLUNAS_DATA_MATRICULA_SIGEDUC_CANDIDATAS, "Data Matrícula (SIGEDUC)"
    )
    coluna_oferta_sigeduc_r = coluna_oferta_sigeduc or exigir_coluna(
        df_sigeduc, COLUNAS_OFERTA_CANDIDATAS, "Oferta de Ensino (SIGEDUC)"
    )
    coluna_serie_sigeduc_r = coluna_serie_sigeduc or exigir_coluna(df_sigeduc, COLUNAS_SERIE_CANDIDATAS, "Série (SIGEDUC)")
    # Opcional: se não existir, a Data Encerramento sozinha é usada como
    # data de referência (sem cair no erro que o script original teria se
    # essa coluna não existisse com o nome padrão).
    coluna_data_atualizacao_sigeduc_r = encontrar_coluna(df_sigeduc, COLUNAS_DATA_ATUALIZACAO_SIGEDUC_CANDIDATAS)

    total_sgp = df_sgp.height
    total_sigeduc = df_sigeduc.height

    # --- Prepara a base SGP ---
    sgp = df_sgp.with_columns(
        limpar_cpf(pl.col(coluna_cpf_sgp_r)).alias(coluna_cpf_sgp_r),
        _normalizar_entidade(pl.col(coluna_entidade_sgp_r)).alias(coluna_entidade_sgp_r),
    ).filter(pl.col(coluna_cpf_sgp_r).is_not_null())
    sgp = sgp.with_columns(_grupo_modalidade(coluna_etapa_sgp_r).alias("_modalidade_grupo_sgp"))
    total_sgp_cpf_valido = sgp.height

    # --- Prepara o SIGEDUC: limpa CPF/CENSO, normaliza as 3 datas
    # envolvidas e calcula a etapa/modalidade a partir de Oferta + Série
    # (reaproveita o Módulo B em vez de reimplementar a regra). ---
    sigeduc = df_sigeduc.with_columns(
        limpar_cpf(pl.col(coluna_cpf_sigeduc_r)).alias(coluna_cpf_sigeduc_r),
        _normalizar_entidade(pl.col(coluna_censo_sigeduc_r)).alias(coluna_censo_sigeduc_r),
        converter_data_robusta(pl.col(coluna_data_encerramento_sigeduc_r)).alias(coluna_data_encerramento_sigeduc_r),
        converter_data_robusta(pl.col(coluna_data_matricula_sigeduc_r)).alias("_data_matricula_str"),
    ).filter(pl.col(coluna_cpf_sigeduc_r).is_not_null())
    total_sigeduc_cpf_valido = sigeduc.height

    if coluna_data_atualizacao_sigeduc_r is not None:
        sigeduc = sigeduc.with_columns(
            converter_data_robusta(pl.col(coluna_data_atualizacao_sigeduc_r)).alias(coluna_data_atualizacao_sigeduc_r)
        )
        data_referencia_str = pl.coalesce(
            [pl.col(coluna_data_encerramento_sigeduc_r), pl.col(coluna_data_atualizacao_sigeduc_r)]
        )
    else:
        data_referencia_str = pl.col(coluna_data_encerramento_sigeduc_r)

    sigeduc, _ = calcular_etapa_de_ensino(sigeduc, coluna_oferta_sigeduc_r, coluna_serie_sigeduc_r)
    sigeduc = sigeduc.with_columns(
        data_referencia_str.alias("_data_referencia_str"),
        pl.col("_data_matricula_str").str.to_date("%d/%m/%Y", strict=False).alias("_data_matricula_ordenacao"),
        _grupo_modalidade("ESTUDANTE_ETAPA_DE_ENSINO").alias("_modalidade_grupo_sigeduc"),
    ).with_columns(pl.col("_data_referencia_str").str.to_date("%d/%m/%Y", strict=False).alias("_data_ordenacao"))

    # =========================================================
    # CADASTRO: CPFs do SIGEDUC que ainda não estão no SGP.
    # =========================================================
    # `.to_list()` -- passar a Series direto para `.is_in()` é ambíguo e
    # está deprecado no Polars quando os dois lados têm o mesmo dtype.
    cpfs_no_sgp = sgp[coluna_cpf_sgp_r].to_list()
    sigeduc_novos = sigeduc.filter(~pl.col(coluna_cpf_sigeduc_r).is_in(cpfs_no_sgp))

    prioridade_matriculado = (
        _texto_upper(coluna_situacao_sigeduc_r).str.contains("MATRICULADO", literal=True).fill_null(False)
    )
    sigeduc_novos_ordenado = sigeduc_novos.with_columns(
        prioridade_matriculado.alias("_prioridade_matriculado")
    ).sort(
        [coluna_cpf_sigeduc_r, "_prioridade_matriculado", "_data_matricula_ordenacao"],
        descending=[False, True, True],
        nulls_last=True,
    )
    sigeduc_novos_dedup = sigeduc_novos_ordenado.unique(
        subset=[coluna_cpf_sigeduc_r], keep="first", maintain_order=True
    )

    # --- Planilha opcional de Terços: cruzada só por CPF, só para
    # preencher ESTUDANTE_ANO_PERIODO no Cadastro -- não afeta o
    # cruzamento SGP x SIGEDUC nem a Movimentação. ---
    if df_tercos is not None:
        coluna_cpf_terco_r = coluna_cpf_terco or exigir_coluna(df_tercos, COLUNAS_CPF_TERCO_CANDIDATAS, "CPF (Terços)")
        coluna_ano_periodo_terco_r = coluna_ano_periodo_terco or exigir_coluna(
            df_tercos, COLUNAS_ANO_PERIODO_TERCO_CANDIDATAS, "Ano/Período (Terços)"
        )
        tercos = (
            df_tercos.select(
                limpar_cpf(pl.col(coluna_cpf_terco_r)).alias(coluna_cpf_sigeduc_r),
                pl.col(coluna_ano_periodo_terco_r)
                .cast(pl.Utf8, strict=False)
                .str.strip_chars()
                .alias("_ano_periodo_terco"),
            )
            .filter(pl.col(coluna_cpf_sigeduc_r).is_not_null())
            # Se o CPF se repetir na planilha de Terços, fica só com a
            # última ocorrência -- mesma tolerância usada nas outras
            # planilhas auxiliares do projeto.
            .unique(subset=[coluna_cpf_sigeduc_r], keep="last", maintain_order=True)
        )
        sigeduc_novos_dedup = sigeduc_novos_dedup.join(tercos, on=coluna_cpf_sigeduc_r, how="left")
        total_cadastro_sem_terco = int(sigeduc_novos_dedup["_ano_periodo_terco"].is_null().sum())
    else:
        coluna_cpf_terco_r = None
        coluna_ano_periodo_terco_r = None
        total_cadastro_sem_terco = 0

    df_cadastro = _montar_cadastro(
        sigeduc_novos_dedup, coluna_cpf_sigeduc_r, coluna_censo_sigeduc_r, coluna_situacao_sigeduc_r
    )
    total_cadastro = df_cadastro.height

    # =========================================================
    # MOVIMENTAÇÃO: CPFs do SGP, cruzados com o histórico deles no
    # SIGEDUC (só o SIGEDUC de quem já está no SGP interessa aqui).
    # =========================================================
    sigeduc_existentes = sigeduc.filter(pl.col(coluna_cpf_sigeduc_r).is_in(cpfs_no_sgp))

    cpfs_com_cancelamento = (
        sigeduc_existentes.filter(_texto_upper(coluna_situacao_sigeduc_r) == _SITUACAO_CANCELADO)[coluna_cpf_sigeduc_r]
        .unique()
        .to_list()
    )

    # 1) Mesma matrícula: registros do SIGEDUC do mesmo CPF NA MESMA escola
    # em que ele está hoje no SGP.
    mesma_escola = sgp.select([coluna_cpf_sgp_r, coluna_entidade_sgp_r]).join(
        sigeduc_existentes,
        left_on=[coluna_cpf_sgp_r, coluna_entidade_sgp_r],
        right_on=[coluna_cpf_sigeduc_r, coluna_censo_sigeduc_r],
        how="inner",
    )
    mesma_escola_recente = mesma_escola.sort([coluna_cpf_sgp_r, "_data_ordenacao"], nulls_last=False).unique(
        subset=[coluna_cpf_sgp_r], keep="last", maintain_order=True
    )
    historico_mesma_matricula = mesma_escola_recente.select(
        pl.col(coluna_cpf_sgp_r),
        pl.col("_data_referencia_str").alias("_data_fim_mesma_matricula_str"),
        pl.col("_data_ordenacao").alias("_data_fim_mesma_matricula"),
    )

    # 2) Última matrícula ativa: entre TODOS os registros do SIGEDUC do
    # CPF (qualquer escola), ignorando quem saiu (TRANSFERIDO/CANCELADO).
    nao_saidas = sigeduc_existentes.filter(
        ~_texto_upper(coluna_situacao_sigeduc_r).is_in([_SITUACAO_TRANSFERIDO, _SITUACAO_CANCELADO])
    )
    nao_saidas_recente = nao_saidas.sort([coluna_cpf_sigeduc_r, "_data_ordenacao"], nulls_last=False).unique(
        subset=[coluna_cpf_sigeduc_r], keep="last", maintain_order=True
    )
    ultimas_matriculas = nao_saidas_recente.select(
        pl.col(coluna_cpf_sigeduc_r).alias(coluna_cpf_sgp_r),
        pl.col(coluna_censo_sigeduc_r).alias("_censo_atual_sigeduc"),
        pl.col("_modalidade_grupo_sigeduc").alias("_modalidade_atual_sigeduc"),
    )

    final = sgp.join(historico_mesma_matricula, on=coluna_cpf_sgp_r, how="left").join(
        ultimas_matriculas, on=coluna_cpf_sgp_r, how="left"
    )

    aluno_encontrado = pl.col("_censo_atual_sigeduc").is_not_null()
    escola_diferente = aluno_encontrado & (pl.col("_censo_atual_sigeduc") != pl.col(coluna_entidade_sgp_r))
    escola_igual = aluno_encontrado & (pl.col("_censo_atual_sigeduc") == pl.col(coluna_entidade_sgp_r))
    modalidade_diferente = aluno_encontrado & (pl.col("_modalidade_atual_sigeduc") != pl.col("_modalidade_grupo_sgp"))
    tem_data_fim_mesma_matricula = pl.col("_data_fim_mesma_matricula").is_not_null()
    tem_cancelamento_historico = pl.col(coluna_cpf_sgp_r).is_in(cpfs_com_cancelamento)

    mascara_trans_escola = tem_data_fim_mesma_matricula & escola_diferente
    mascara_trans_mod_com_data = tem_data_fim_mesma_matricula & escola_igual & modalidade_diferente
    mascara_trans_mod_sem_data = (~tem_data_fim_mesma_matricula) & escola_igual & modalidade_diferente
    mascara_trans_mod_sem_data_com_cancel = mascara_trans_mod_sem_data & tem_cancelamento_historico
    mascara_trans_mod_sem_data_sem_cancel = mascara_trans_mod_sem_data & (~tem_cancelamento_historico)

    situacao_final = (
        pl.when(mascara_trans_escola)
        .then(pl.lit(_SITUACAO_TRANS_ESCOLA))
        .when(mascara_trans_mod_com_data | mascara_trans_mod_sem_data_sem_cancel)
        .then(pl.lit(_SITUACAO_TRANS_MOD))
        .when(mascara_trans_mod_sem_data_com_cancel)
        .then(pl.lit(_SITUACAO_CANCELADO_SAIDA))
        .otherwise(None)
    )
    data_fim_ou_aprovacao = (
        pl.when(mascara_trans_escola | mascara_trans_mod_com_data)
        .then(pl.col("_data_fim_mesma_matricula_str"))
        .otherwise(None)
    )

    final = final.with_columns(
        situacao_final.alias(COLUNA_SITUACAO_FINAL), data_fim_ou_aprovacao.alias(COLUNA_DATA_FIM_OU_APROVACAO)
    )

    # Contagens tiradas do resultado final já materializado (coluna
    # `COLUNA_SITUACAO_FINAL`/`_censo_atual_sigeduc`), não das máscaras
    # booleanas isoladas -- mesmo cuidado do Módulo F para não depender de
    # nenhuma ordem de linhas sobreviver a um join anterior.
    total_trans_escola = int((final[COLUNA_SITUACAO_FINAL] == _SITUACAO_TRANS_ESCOLA).sum())
    total_trans_mod = int((final[COLUNA_SITUACAO_FINAL] == _SITUACAO_TRANS_MOD).sum())
    total_cancelado = int((final[COLUNA_SITUACAO_FINAL] == _SITUACAO_CANCELADO_SAIDA).sum())
    total_sgp_sem_correspondencia = int(final["_censo_atual_sigeduc"].is_null().sum())
    total_sem_alteracao = total_sgp_cpf_valido - total_trans_escola - total_trans_mod - total_cancelado

    # A planilha de Movimentação só lista quem de fato "se mexeu e
    # continua ativo": trans. escola ou trans. mod -- diferente do Módulo
    # D/F (que devolvem a base inteira, pronta para reenvio), este é um
    # relatório de acompanhamento, não um arquivo para reimportar no SGP.
    # Quem não mudou nada, ou foi classificado como cancelado, continua
    # contado em total_sem_alteracao/total_cancelado, só não entra aqui.
    df_movimentacao = final.filter(
        pl.col(COLUNA_SITUACAO_FINAL).is_in([_SITUACAO_TRANS_ESCOLA, _SITUACAO_TRANS_MOD])
    ).drop(
        [
            "_modalidade_grupo_sgp",
            "_data_fim_mesma_matricula_str",
            "_data_fim_mesma_matricula",
            "_censo_atual_sigeduc",
            "_modalidade_atual_sigeduc",
        ]
    )

    relatorio = RelatorioMovimentacao(
        coluna_cpf_sgp=coluna_cpf_sgp_r,
        coluna_entidade_sgp=coluna_entidade_sgp_r,
        coluna_etapa_sgp=coluna_etapa_sgp_r,
        coluna_cpf_sigeduc=coluna_cpf_sigeduc_r,
        coluna_censo_sigeduc=coluna_censo_sigeduc_r,
        coluna_situacao_sigeduc=coluna_situacao_sigeduc_r,
        coluna_data_encerramento_sigeduc=coluna_data_encerramento_sigeduc_r,
        coluna_data_atualizacao_sigeduc=coluna_data_atualizacao_sigeduc_r,
        coluna_data_matricula_sigeduc=coluna_data_matricula_sigeduc_r,
        coluna_oferta_sigeduc=coluna_oferta_sigeduc_r,
        coluna_serie_sigeduc=coluna_serie_sigeduc_r,
        coluna_cpf_terco=coluna_cpf_terco_r,
        coluna_ano_periodo_terco=coluna_ano_periodo_terco_r,
        total_sgp=total_sgp,
        total_sgp_cpf_valido=total_sgp_cpf_valido,
        total_sigeduc=total_sigeduc,
        total_sigeduc_cpf_valido=total_sigeduc_cpf_valido,
        total_cadastro=total_cadastro,
        total_cadastro_sem_terco=total_cadastro_sem_terco,
        total_sgp_sem_correspondencia_sigeduc=total_sgp_sem_correspondencia,
        total_trans_escola=total_trans_escola,
        total_trans_mod=total_trans_mod,
        total_cancelado=total_cancelado,
        total_sem_alteracao=total_sem_alteracao,
    )

    return df_cadastro, df_movimentacao, relatorio


if __name__ == "__main__":
    # Auto-teste -- rode com `python -m src.core.movimentacao` a partir da
    # raiz do projeto.
    sgp = pl.DataFrame(
        {
            "ESTUDANTE_CPF": ["1", "2", "3", "4", "5"],
            "CO_ENTIDADE": ["100", "100", "100", "100", "100"],
            "ESTUDANTE_ETAPA_DE_ENSINO": ["45", "45", "45", "45", "45"],
            "NOME_SGP": [
                "CPF1_TRANS_ESCOLA",
                "CPF2_TRANS_MOD_COM_DATA",
                "CPF3_TRANS_MOD_SEM_DATA_CANCEL",
                "CPF4_SEM_ALTERACAO",
                "CPF5_SEM_SIGEDUC",
            ],
        }
    )

    sigeduc = pl.DataFrame(
        {
            "NU CPF": [
                # CPF 1: matrícula de origem (escola 100) fechada, e a
                # última matrícula ativa está em outra escola (200).
                "1", "1",
                # CPF 2: matrícula de origem (100) fechada, última
                # matrícula ativa na MESMA escola mas modalidade mudou
                # (25 = Regular, fora do grupo EJA).
                "2", "2",
                # CPF 3: matrícula de origem (100) SEM data de
                # encerramento, modalidade mudou, e o CPF tem uma
                # ocorrência de CANCELADO no histórico -> 'cancelado'.
                "3", "3",
                # CPF 4: nada muda (mesma escola, mesma modalidade).
                "4",
                # CPF 6: não está no SGP -> candidato a Cadastro.
                "6",
            ],
            "CENSO": ["100", "200", "100", "100", "100", "100", "100", "300"],
            "SITUAÇÃO MATRÍCULA": [
                "TRANSFERIDO", "MATRICULADO",
                "TRANSFERIDO", "MATRICULADO",
                "CANCELADO", "MATRICULADO",
                "MATRICULADO",
                "MATRICULADO",
            ],
            "DATA ENCERRAMENTO": [
                "10/03/2026", None,
                "10/03/2026", None,
                None, None,
                None,
                None,
            ],
            "DATA MATRÍCULA": [
                "01/02/2025", "11/03/2026",
                "01/02/2025", "11/03/2026",
                "01/02/2025", "15/03/2026",
                "01/02/2025",
                "20/03/2026",
            ],
            "OFERTA DE ENSINO": [
                "EDUCACAO DE JOVENS E ADULTOS - EJA", "EDUCACAO DE JOVENS E ADULTOS - EJA",
                "EDUCACAO DE JOVENS E ADULTOS - EJA", "ENSINO MEDIO REGULAR",
                "EDUCACAO DE JOVENS E ADULTOS - EJA", "ENSINO MEDIO REGULAR",
                "EDUCACAO DE JOVENS E ADULTOS - EJA",
                "ENSINO MEDIO REGULAR",
            ],
            "SÉRIE": ["1ª SÉRIE"] * 4 + ["1ª SÉRIE", "2ª SÉRIE"] + ["1ª SÉRIE", "1ª SÉRIE"],
            "ALUNO": [None, None, None, None, None, None, None, "FULANO NOVO"],
            "SEXO": [None, None, None, None, None, None, None, "F"],
            "RAÇA/COR": [None, None, None, None, None, None, None, "PARDA"],
        }
    )

    df_cadastro, df_movimentacao, relatorio = reconciliar_sgp_sigeduc(sgp, sigeduc)

    with pl.Config(tbl_cols=-1, tbl_width_chars=220):
        print("=== Cadastro ===")
        print(df_cadastro)
        print("\n=== Movimentação ===")
        print(df_movimentacao)
    print()
    print(relatorio.model_dump_json(indent=2))

    # A planilha de Movimentação só traz trans. escola/trans. mod -- CPF3
    # (cancelado), CPF4 (sem alteração) e CPF5 (sem SIGEDUC) não devem
    # aparecer nela, embora continuem contados no relatório.
    assert df_movimentacao.height == 2
    assert set(df_movimentacao["NOME_SGP"]) == {
        "CPF1_TRANS_ESCOLA",
        "CPF2_TRANS_MOD_COM_DATA",
    }

    linha = lambda nome: df_movimentacao.filter(pl.col("NOME_SGP") == nome).row(0, named=True)

    assert linha("CPF1_TRANS_ESCOLA")[COLUNA_SITUACAO_FINAL] == "trans. escola"
    assert linha("CPF1_TRANS_ESCOLA")[COLUNA_DATA_FIM_OU_APROVACAO] == "10/03/2026"
    assert linha("CPF2_TRANS_MOD_COM_DATA")[COLUNA_SITUACAO_FINAL] == "trans. mod"
    assert linha("CPF2_TRANS_MOD_COM_DATA")[COLUNA_DATA_FIM_OU_APROVACAO] == "10/03/2026"

    assert relatorio.total_trans_escola == 1
    assert relatorio.total_trans_mod == 1
    assert relatorio.total_cancelado == 1  # CPF3 -- contado, mas fora da planilha
    assert relatorio.total_sem_alteracao == 2  # CPF4 e CPF5 -- contados, mas fora da planilha
    assert relatorio.total_sgp_sem_correspondencia_sigeduc == 1  # CPF5
    assert relatorio.total_cadastro == 1  # CPF 6

    assert df_cadastro.height == 1
    linha_cad = df_cadastro.row(0, named=True)
    assert linha_cad["ESTUDANTE_CPF"] == limpar_cpf(pl.Series(["6"]))[0]
    assert linha_cad["ESTUDANTE_NOME"] == "FULANO NOVO"
    assert linha_cad["ESTUDANTE_SEXO"] == "2"  # F -> 2
    assert linha_cad["ESTUDANTE_RACA_COR"] == "3"  # PARDA -> 3
    assert linha_cad["CO_ENTIDADE"] == "300"
    assert linha_cad["ESTUDANTE_ETAPA_DE_ENSINO"] == "25"  # Regular, 1ª série
    assert linha_cad["SITUACAO_ALUNO"] == "MATRICULADO"  # coluna extra, fora do layout oficial

    # Sem planilha de Terços (comportamento padrão, sem o parâmetro):
    # ESTUDANTE_ANO_PERIODO fica "" e o relatório mostra que a planilha
    # não foi usada (coluna_cpf_terco is None).
    assert linha_cad["ESTUDANTE_ANO_PERIODO"] == ""
    assert relatorio.coluna_cpf_terco is None
    assert relatorio.total_cadastro_sem_terco == 0

    # Com planilha de Terços, CPF 6 encontrado -> ESTUDANTE_ANO_PERIODO
    # preenchido.
    tercos = pl.DataFrame(
        {
            "ESTUDANTE_CPF": ["6", "999"],  # 999 não existe em nenhuma base -- só ruído
            "ESTUDANTE_ANO_PERIODO": ["2º TERÇO", "1º TERÇO"],
        }
    )
    df_cadastro_com_terco, _, relatorio_com_terco = reconciliar_sgp_sigeduc(sgp, sigeduc, df_tercos=tercos)
    linha_cad_terco = df_cadastro_com_terco.row(0, named=True)
    assert linha_cad_terco["ESTUDANTE_ANO_PERIODO"] == "2º TERÇO"
    assert relatorio_com_terco.coluna_cpf_terco is not None
    assert relatorio_com_terco.total_cadastro_sem_terco == 0

    # Com planilha de Terços, mas SEM o CPF 6 nela -> fica "" mesmo assim
    # (não quebra), e o relatório aponta 1 CPF do Cadastro sem terço.
    tercos_sem_match = pl.DataFrame({"ESTUDANTE_CPF": ["999"], "ESTUDANTE_ANO_PERIODO": ["1º TERÇO"]})
    df_cadastro_sem_match, _, relatorio_sem_match = reconciliar_sgp_sigeduc(sgp, sigeduc, df_tercos=tercos_sem_match)
    linha_cad_sem_match = df_cadastro_sem_match.row(0, named=True)
    assert linha_cad_sem_match["ESTUDANTE_ANO_PERIODO"] == ""
    assert relatorio_sem_match.coluna_cpf_terco is not None
    assert relatorio_sem_match.total_cadastro_sem_terco == 1

    print("\nOK: auto-teste do Módulo C (reconciliação SGP x SIGEDUC) passou.")
