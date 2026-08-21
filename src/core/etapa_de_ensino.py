"""Módulo B: Motor de Etapa de Ensino (Modalidades).

Lê as colunas 'OFERTA DE ENSINO' e 'SÉRIE' de uma base do SIGEDUC e gera a
coluna 'ESTUDANTE_ETAPA_DE_ENSINO', codificando modalidade + série do aluno
num único código usado pelo SGP:

    - Contém 'EJA'                                   -> 45
    - Contém 'TÉCNIC'/'INTEGRADO' + 1ª a 4ª série     -> 30, 31, 32, 33
    - Regular (nem técnico nem EJA) + 1ª a 4ª série   -> 25, 26, 27, 28
    - Qualquer outra combinação                       -> 'NAO_MAPEADO'

Implementado como uma consulta SQL no DuckDB -- a regra é, no fundo, um
CASE WHEN, e SQL é a forma mais direta de ler e auditar essa lógica --
rodando diretamente sobre o DataFrame do Polars, sem nenhuma cópia
intermediária em arquivo.
"""

from __future__ import annotations

import duckdb
import polars as pl

from src.core._colunas import exigir_coluna, quotar_identificador
from src.schemas.etapa_de_ensino import RelatorioEtapaDeEnsino

# Nomes de coluna aceitos (nas variações já vistas em exports reais do
# SIGEDUC), na ordem em que são tentados quando o nome não é informado.
COLUNAS_OFERTA_CANDIDATAS = ("OFERTA DE ENSINO", "OFERTA_ENSINO", "OFERTA ENSINO")
COLUNAS_SERIE_CANDIDATAS = ("SÉRIE", "SERIE")

COLUNA_SAIDA = "ESTUDANTE_ETAPA_DE_ENSINO"

# Marcadores ordinais aceitos depois do número da série ("1ª", "1º", "1°"),
# porque a digitação varia bastante entre planilhas.
_MARCADORES_ORDINAIS = ("ª", "º", "°")

# Códigos gerados por `calcular_etapa_de_ensino`, agrupados por modalidade
# ampla. Usado pelo Módulo C (Movimentação) para não confundir uma simples
# progressão de série (ex.: Regular 25 -> 26, todo ano isso acontece com
# quem continua na mesma modalidade) com uma troca real de modalidade
# (ex.: Regular -> EJA).
CODIGOS_EJA = ("45",)
CODIGOS_REGULAR_TECNICO = ("25", "26", "27", "28", "30", "31", "32", "33")


def agrupar_modalidade(codigo: str | None) -> str:
    """Agrupa um código de `ESTUDANTE_ETAPA_DE_ENSINO` em `'EJA'`,
    `'REGULAR_TECNICO'` ou `'OUTRO'` (inclui `None`/`'NAO_MAPEADO'`)."""
    if codigo in CODIGOS_EJA:
        return "EJA"
    if codigo in CODIGOS_REGULAR_TECNICO:
        return "REGULAR_TECNICO"
    return "OUTRO"


def _condicoes_serie_sql() -> str:
    """Monta os `WHEN` do CASE que descobrem o número da série (1 a 4) a
    partir do texto combinado de oferta + série."""
    linhas = []
    for n in (1, 2, 3, 4):
        padroes = " OR ".join(f"_desc LIKE '%{n}{marcador}%'" for marcador in _MARCADORES_ORDINAIS)
        linhas.append(f"WHEN {padroes} THEN {n}")
    return "\n                    ".join(linhas)


def _construir_sql(coluna_oferta: str, coluna_serie: str) -> str:
    oferta_sql = quotar_identificador(coluna_oferta)
    serie_sql = quotar_identificador(coluna_serie)

    return f"""
        WITH normalizado AS (
            SELECT *,
                upper(coalesce({oferta_sql}, '')) || ' ' || upper(coalesce({serie_sql}, '')) AS _desc
            FROM entrada
        ),
        classificado AS (
            SELECT *,
                _desc LIKE '%EJA%' AS _eh_eja,
                (_desc LIKE '%TÉCNIC%' OR _desc LIKE '%TECNIC%' OR _desc LIKE '%INTEGRADO%') AS _eh_tecnico,
                CASE
                    {_condicoes_serie_sql()}
                    ELSE NULL
                END AS _numero_serie
            FROM normalizado
        )
        SELECT
            * EXCLUDE (_desc, _eh_eja, _eh_tecnico, _numero_serie),
            CASE
                WHEN _eh_eja THEN '45'
                WHEN _eh_tecnico AND _numero_serie = 1 THEN '30'
                WHEN _eh_tecnico AND _numero_serie = 2 THEN '31'
                WHEN _eh_tecnico AND _numero_serie = 3 THEN '32'
                WHEN _eh_tecnico AND _numero_serie = 4 THEN '33'
                WHEN NOT _eh_tecnico AND _numero_serie = 1 THEN '25'
                WHEN NOT _eh_tecnico AND _numero_serie = 2 THEN '26'
                WHEN NOT _eh_tecnico AND _numero_serie = 3 THEN '27'
                WHEN NOT _eh_tecnico AND _numero_serie = 4 THEN '28'
                ELSE 'NAO_MAPEADO'
            END AS {COLUNA_SAIDA}
        FROM classificado
    """


def calcular_etapa_de_ensino(
    df: pl.DataFrame,
    coluna_oferta: str | None = None,
    coluna_serie: str | None = None,
) -> tuple[pl.DataFrame, RelatorioEtapaDeEnsino]:
    """Adiciona a coluna `ESTUDANTE_ETAPA_DE_ENSINO` a `df`.

    Parameters
    ----------
    df:
        DataFrame já carregado, contendo (pelo menos) as colunas de oferta
        de ensino e série.
    coluna_oferta, coluna_serie:
        Nomes explícitos das colunas a usar. Se omitidos, tenta localizar
        automaticamente entre as variações mais comuns vistas nos exports
        do SIGEDUC (`OFERTA DE ENSINO`/`OFERTA_ENSINO`, `SÉRIE`/`SERIE`).

    Returns
    -------
    tuple[pl.DataFrame, RelatorioEtapaDeEnsino]
        `df` original + a nova coluna `ESTUDANTE_ETAPA_DE_ENSINO`, e um
        relatório com a contagem de alunos em cada código (inclusive
        `'NAO_MAPEADO'`, para auditoria -- nenhum aluno é descartado por
        não bater com a regra, ele só fica marcado para revisão manual).

    Raises
    ------
    KeyError
        Se não for possível localizar a coluna de oferta ou de série.
    """
    coluna_oferta_resolvida = coluna_oferta or exigir_coluna(
        df, COLUNAS_OFERTA_CANDIDATAS, "Oferta de Ensino"
    )
    coluna_serie_resolvida = coluna_serie or exigir_coluna(df, COLUNAS_SERIE_CANDIDATAS, "Série")

    # Se já existir uma coluna de saída de uma execução anterior, ela é
    # recalculada do zero (evita erro de coluna duplicada no `SELECT *`).
    entrada = df.drop(COLUNA_SAIDA) if COLUNA_SAIDA in df.columns else df

    sql = _construir_sql(coluna_oferta_resolvida, coluna_serie_resolvida)
    con = duckdb.connect()
    con.register("entrada", entrada)
    df_final = con.sql(sql).pl()

    contagem = df_final[COLUNA_SAIDA].value_counts().sort(COLUNA_SAIDA)
    contagem_por_etapa = dict(zip(contagem[COLUNA_SAIDA].to_list(), contagem["count"].to_list()))

    relatorio = RelatorioEtapaDeEnsino(
        coluna_oferta=coluna_oferta_resolvida,
        coluna_serie=coluna_serie_resolvida,
        total_registros=df_final.height,
        contagem_por_etapa=contagem_por_etapa,
        total_nao_mapeado=contagem_por_etapa.get("NAO_MAPEADO", 0),
    )

    return df_final, relatorio


if __name__ == "__main__":
    # Auto-teste rápido -- rode com `python -m src.core.etapa_de_ensino` a
    # partir da raiz do projeto.
    amostra = pl.DataFrame(
        {
            "ESTUDANTE_CPF": [str(i) for i in range(1, 11)],
            "OFERTA DE ENSINO": [
                "EDUCACAO DE JOVENS E ADULTOS - EJA",
                "CURSO TECNICO INTEGRADO AO ENSINO MEDIO",
                "CURSO TECNICO INTEGRADO AO ENSINO MEDIO",
                "ENSINO MEDIO REGULAR",
                "ENSINO MEDIO REGULAR",
                "ENSINO MEDIO REGULAR",
                "ENSINO MEDIO REGULAR",
                "CURSO TECNICO INTEGRADO AO ENSINO MEDIO",
                "CURSO TECNICO INTEGRADO AO ENSINO MEDIO",
                "ENSINO MEDIO REGULAR",
            ],
            "SÉRIE": [
                "1ª SÉRIE",
                "1ª SÉRIE",
                "2ª SÉRIE",
                "1ª SÉRIE",
                "2ª SÉRIE",
                "3ª SÉRIE",
                "4ª SÉRIE",
                "3ª SÉRIE",
                "4ª SÉRIE",
                None,
            ],
        }
    )
    resultado, relatorio = calcular_etapa_de_ensino(amostra)
    with pl.Config(tbl_cols=-1, tbl_width_chars=140):
        print(resultado)
    print()
    print(relatorio.model_dump_json(indent=2))
