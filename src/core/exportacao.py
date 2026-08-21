"""Módulo E: Exportação Padrão MEC.

Aplica o **layout oficial de colunas** usado para subir alunos no SGP a
qualquer planilha já processada pelos módulos anteriores (ou uma planilha
nova): cria vazias as colunas do modelo que não existirem e descarta
qualquer coluna que não faça parte do modelo -- sempre na ordem exata
abaixo.

`ORDEM_PADRAO_MEC` é a lista de 43 colunas extraída do script legado
`SCRIPT_PE_DE_MEIA` (célula "ORDENANDO COLUNAS PARA SUBIR ALUNOS", que traz
o comentário "Esta é a lista exata que você forneceu" -- ou seja, é o
modelo oficial confirmado, não uma dedução automática).

Diferente dos módulos A-D, este módulo não limpa CPF nem valida regra de
negócio nenhuma -- é puramente estrutural (existência + ordem das
colunas), exatamente como descrito no briefing. A limpeza dos *dados* já
deve ter acontecido nos módulos anteriores.
"""

from __future__ import annotations

from collections.abc import Sequence

import polars as pl

from src.schemas.exportacao import RelatorioExportacaoMEC

# Lista exata (e na ordem exata) do modelo oficial usado para subir alunos
# no SGP -- não editar sem confirmar a mudança no layout oficial.
ORDEM_PADRAO_MEC: tuple[str, ...] = (
    "ESTUDANTE_CPF",
    "ESTUDANTE_NU_NIS",
    "ESTUDANTE_NOME",
    "ESTUDANTE_NOME_SOCIAL",
    "ESTUDANTE_DT_NASCIMENTO",
    "ESTUDANTE_MAE_NOME",
    "CO_ENTIDADE",
    "NO_ENTIDADE",
    "CO_MATRICULA_REDE",
    "DATA_INICIO_PERIODO_LETIVO",
    "DATA_INICIO_MATRICULA",
    "NU_ANO_MATRICULA",
    "ESTUDANTE_ETAPA_DE_ENSINO",
    "TURMA_FORMA_ORGANIZACAO",
    "TURMA_ORGANIZACAO_QUANTIDADE_TOTAL",
    "ESTUDANTE_ANO_PERIODO",
    "ESTUDANTE_E_MAIL",
    "ESTUDANTE_INTEGRAL",
    "ESTUDANTE_NU_RG",
    "ESTUDANTE_ORGAO_EMISSOR_RG",
    "ESTUDANTE_NU_CNH",
    "ESTUDANTE_NU_CERTIDAO_NASCIMENTO",
    "RESPONSAVEL_NU_CPF",
    "RESPONSAVEL_NU_NIS",
    "ESTUDANTE_DS_LOGRADOURO_RES",
    "ESTUDANTE_BAIRRO_RES",
    "ESTUDANTE_NU_ENDERECO_RES",
    "ESTUDANTE_CEP_RES",
    "ESTUDANTE_CO_MUNICIPIO_RES",
    "ESTUDANTE_CO_UF_RES",
    "ESTUDANTE_RACA_COR",
    "ESTUDANTE_DEFICIENCIA_BAIXA_VISAO",
    "ESTUDANTE_DEFICIENCIA_CEGUEIRA",
    "ESTUDANTE_DEFICIENCIA_AUDITIVA",
    "ESTUDANTE_DEFICIENCIA_FISICA",
    "ESTUDANTE_DEFICIENCIA_INTELECTUAL",
    "ESTUDANTE_DEFICIENCIA_SURDEZ",
    "ESTUDANTE_DEFICIENCIA_SURDOCEGUEIRA",
    "ESTUDANTE_DEFICIENCIA_MULTIPLA",
    "ESTUDANTE_TRANSTORNO_ESPECTRO_AUTISTA",
    "ESTUDANTE_ALTAS_HABILIDADES_SUPERDOTACAO",
    "ESTUDANTE_GENERO",
    "ESTUDANTE_PPL",
)


def exportar_padrao_mec(
    df: pl.DataFrame, ordem: Sequence[str] = ORDEM_PADRAO_MEC
) -> tuple[pl.DataFrame, RelatorioExportacaoMEC]:
    """Reordena `df` para o layout `ordem` (por padrão, `ORDEM_PADRAO_MEC`).

    Colunas do modelo que não existirem em `df` são criadas vazias (nulas).
    Colunas de `df` que não fizerem parte do modelo são descartadas -- o
    resultado tem **exatamente** as colunas de `ordem`, nessa ordem.

    Parameters
    ----------
    df:
        A planilha já processada (ex.: saída dos módulos A-D) ou uma
        planilha nova qualquer.
    ordem:
        A lista de colunas do modelo, na ordem desejada. Por padrão, o
        layout oficial `ORDEM_PADRAO_MEC` (43 colunas).

    Returns
    -------
    tuple[pl.DataFrame, RelatorioExportacaoMEC]
        `(df_padronizado, relatorio)`.
    """
    colunas_existentes = set(df.columns)

    colunas_criadas = [coluna for coluna in ordem if coluna not in colunas_existentes]
    colunas_descartadas = [coluna for coluna in df.columns if coluna not in ordem]

    df_completo = df.with_columns(
        pl.lit(None, dtype=pl.Utf8).alias(coluna) for coluna in colunas_criadas
    )
    df_padronizado = df_completo.select(list(ordem))

    relatorio = RelatorioExportacaoMEC(
        total_linhas=df_padronizado.height,
        total_colunas_modelo=len(ordem),
        colunas_criadas_vazias=colunas_criadas,
        colunas_descartadas=colunas_descartadas,
    )

    return df_padronizado, relatorio


if __name__ == "__main__":
    # Auto-teste rápido -- rode com `python -m src.core.exportacao` a
    # partir da raiz do projeto.
    entrada = pl.DataFrame(
        {
            "ESTUDANTE_CPF": ["11111111111", "22222222222"],
            "ESTUDANTE_NOME": ["FULANO", "CICLANA"],
            "CO_ENTIDADE": ["100", "200"],
            "ESTUDANTE_ETAPA_DE_ENSINO": ["45", "25"],
            "MATRICULA_SITUACAO_FINAL": ["trans. escola", "sem_alteracao"],  # não faz parte do modelo
            "COLUNA_QUE_NAO_EXISTE_NO_MODELO": ["x", "y"],  # idem
        }
    )

    saida, relatorio = exportar_padrao_mec(entrada)

    print("=== Colunas de saída (deve ser igual a ORDEM_PADRAO_MEC) ===")
    print(saida.columns)
    print("\n=== Amostra ===")
    with pl.Config(tbl_cols=6, tbl_width_chars=200):
        print(saida.select(saida.columns[:6]))
    print()
    print(relatorio.model_dump_json(indent=2))

    assert saida.columns == list(ORDEM_PADRAO_MEC)
    assert saida.height == entrada.height
    assert "MATRICULA_SITUACAO_FINAL" in relatorio.colunas_descartadas
    assert "COLUNA_QUE_NAO_EXISTE_NO_MODELO" in relatorio.colunas_descartadas
    assert "ESTUDANTE_NU_NIS" in relatorio.colunas_criadas_vazias
    assert saida["ESTUDANTE_NU_NIS"].is_null().all()
    assert saida["ESTUDANTE_CPF"].to_list() == ["11111111111", "22222222222"]
    print("\nOK: auto-teste do Módulo E passou.")
