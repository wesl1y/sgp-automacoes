"""Módulo A: Deduplicação e Matrícula Mais Recente.

Recebe uma base do SIGEDUC (ou qualquer planilha com um CPF de estudante e
uma coluna de data de matrícula/encerramento) e devolve apenas UM registro
por CPF: o de data mais recente.

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

# Colunas de data aceitas, na ordem de prioridade citada no briefing, usadas
# quando quem chama a função não informa explicitamente qual usar.
COLUNAS_DATA_CANDIDATAS = ("DATA_INICIO_MATRICULA", "DATA ENCERRAMENTO")


def deduplicar_matricula_mais_recente(
    df: pl.DataFrame,
    coluna_cpf: str = "ESTUDANTE_CPF",
    coluna_data: str | None = None,
) -> tuple[pl.DataFrame, RelatorioDeduplicacao]:
    """Mantém, para cada CPF, apenas o registro com a data mais recente.

    Passo a passo:

    1. Limpa a coluna de CPF (`limpar_cpf`, de `src/utils`) e descarta
       linhas sem um CPF válido -- não dá para deduplicar por uma chave
       que não existe.
    2. Normaliza a coluna de data (`converter_data_robusta`) para o
       padrão `'DD/MM/AAAA'` e cria uma coluna auxiliar `pl.Date` só para
       permitir a ordenação cronológica correta (a *string*
       `'DD/MM/AAAA'` não ordena certo como texto -- "01/12/2025" viria
       antes de "05/01/2026" numa ordenação alfabética ingênua).
    3. Ordena por CPF e pela data auxiliar (nulos primeiro, para uma data
       ausente nunca "vencer" um registro com data válida do mesmo CPF) e
       mantém a última linha de cada CPF -- ou seja, a matrícula mais
       recente. Um CPF cujo único registro não tem data válida ainda
       assim é mantido: preferimos manter o aluno com um alerta a fazê-lo
       desaparecer silenciosamente da base.
    4. Descarta a coluna auxiliar antes de devolver o resultado; a coluna
       de data volta como string `'DD/MM/AAAA'`, igual ao resto do
       sistema.

    Parameters
    ----------
    df:
        DataFrame já carregado (por exemplo via `carregar_planilha`).
    coluna_cpf:
        Nome da coluna de CPF do estudante. Default `'ESTUDANTE_CPF'`.
    coluna_data:
        Nome da coluna de data a usar como critério de "mais recente".
        Se omitido, tenta `'DATA_INICIO_MATRICULA'` e, se não existir,
        `'DATA ENCERRAMENTO'` -- as duas colunas citadas no briefing.

    Returns
    -------
    tuple[pl.DataFrame, RelatorioDeduplicacao]
        O DataFrame com uma linha por CPF (a mais recente) e um relatório
        com as contagens do que foi removido em cada etapa, para exibir
        na UI ou registrar em log.

    Raises
    ------
    KeyError
        Se `coluna_cpf` não existir, ou se `coluna_data` não existir (ou,
        no modo automático, se nenhuma das colunas candidatas existir).
    """
    if coluna_cpf not in df.columns:
        raise KeyError(
            f"Coluna de CPF '{coluna_cpf}' não encontrada. "
            f"Colunas disponíveis: {df.columns}"
        )

    coluna_data_resolvida = coluna_data or next(
        (c for c in COLUNAS_DATA_CANDIDATAS if c in df.columns), None
    )
    if coluna_data_resolvida is None or coluna_data_resolvida not in df.columns:
        raise KeyError(
            "Não encontrei uma coluna de data para decidir a matrícula "
            f"mais recente. Tentei {COLUNAS_DATA_CANDIDATAS!r}. "
            f"Colunas disponíveis: {df.columns}"
        )

    total_recebidos = df.height

    # 1. CPF limpo; fora quem não tem CPF válido.
    df_com_cpf_limpo = df.with_columns(limpar_cpf(pl.col(coluna_cpf)).alias(coluna_cpf))
    df_com_cpf = df_com_cpf_limpo.filter(pl.col(coluna_cpf).is_not_null())
    total_cpf_invalido = total_recebidos - df_com_cpf.height

    # 2. Data normalizada (string 'DD/MM/AAAA') + coluna auxiliar pl.Date
    #    só para conseguir ordenar cronologicamente de verdade.
    df_com_data = df_com_cpf.with_columns(
        converter_data_robusta(pl.col(coluna_data_resolvida)).alias(coluna_data_resolvida)
    ).with_columns(
        pl.col(coluna_data_resolvida)
        .str.to_date("%d/%m/%Y", strict=False)
        .alias("_data_ordenacao")
    )

    # 3. Ordena (CPF asc, data asc com nulos primeiro) e mantém a última
    #    linha de cada CPF -- ou seja, a matrícula mais recente.
    #    `maintain_order=True` é essencial: sem ele o Polars não garante
    #    que "keep='last'" respeite a ordem em que acabamos de ordenar.
    df_ordenado = df_com_data.sort([coluna_cpf, "_data_ordenacao"], nulls_last=False)
    df_dedup = df_ordenado.unique(subset=[coluna_cpf], keep="last", maintain_order=True)

    total_data_invalida_mantida = df_dedup.filter(pl.col("_data_ordenacao").is_null()).height

    df_final = df_dedup.drop("_data_ordenacao").sort(coluna_cpf)

    relatorio = RelatorioDeduplicacao(
        coluna_cpf=coluna_cpf,
        coluna_data=coluna_data_resolvida,
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
            "NOME": ["antigo", "MAIS_RECENTE", "meio", "unico", "cpf_invalido", "sem_data_mantido", "MAIS_RECENTE"],
        }
    )
    resultado, relatorio = deduplicar_matricula_mais_recente(amostra)
    print(resultado)
    print()
    print(relatorio.model_dump_json(indent=2))
