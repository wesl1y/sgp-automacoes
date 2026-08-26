"""Módulo G: Situação de Matrícula -- Direto do SIGEDUC (sem base de destino).

Variação do Módulo F (`src/core/situacao_aluno.py`) para quando não existe
(ou não se quer usar) uma base de destino para cruzar: recebe só a base
**bruta do SIGEDUC** e devolve, para cada CPF encontrado, a linha
**inteira** do registro mais recente (todas as colunas originais da
planilha enviada -- nome, escola, nascimento, o que ela tiver) mais uma
coluna nova:

    - `QTD_MODULOS_APROVADOS` <- quantas linhas *daquele CPF* (não só a
      mais recente) têm situação contendo `status_aprovado`.

"Mais recente" usa o mesmo critério do Módulo F: prioriza a `DATA
ENCERRAMENTO` mais nova; sem nenhuma data válida em nenhum registro do
CPF, mantém o último registro na ordem original da planilha.

Diferenças em relação ao Módulo F:

    - Não recebe (nem precisa de) uma base de destino -- o resultado É a
      planilha do SIGEDUC resumida a uma linha por CPF, pronta para usar
      como uma base nova do zero (inclusive como base de destino de uma
      futura rodada do Módulo F).
    - Só devolve CPFs que **existem** no SIGEDUC enviado -- não há
      conceito de "manter quem não teve correspondência", porque não há
      nenhuma outra base para preservar.
    - Não tem a checagem de divergência de Censo x CO_ENTIDADE: essa
      checagem compara o SIGEDUC contra o CO_ENTIDADE de uma base de
      destino, que este módulo não tem. Continua disponível no Módulo F.
    - Mantém a opção de reclassificar 'TRANSFERÊNCIA EXTERNA' (não
      depende de destino nenhum).

Reaproveita as funções públicas de `src/core/situacao_aluno.py`
(`preparar_sigeduc`, `contar_aprovados`, `situacao_mais_recente`,
`reclassificar_transferencia_externa`) em vez de duplicar a lógica --
mesmo padrão de reuso já usado pelo Módulo C, que importa
`CODIGOS_EJA` do Módulo B.
"""

from __future__ import annotations

import polars as pl

from src.core._colunas import exigir_coluna
from src.core.situacao_aluno import (
    COLUNAS_CPF_SIGEDUC_CANDIDATAS,
    COLUNAS_DATA_ENCERRAMENTO_CANDIDATAS,
    COLUNAS_DATA_FIM_CANDIDATAS,
    COLUNAS_PERIODO_CANDIDATAS,
    COLUNAS_SITUACAO_CANDIDATAS,
    COLUNA_DESTINO_QTD_APROVADOS as COLUNA_QTD_APROVADOS,
    STATUS_APROVADO_PADRAO,
    contar_aprovados,
    preparar_sigeduc,
    reclassificar_transferencia_externa,
    situacao_mais_recente,
)
from src.schemas.situacao_sigeduc import RelatorioSituacaoSigeduc


def resumir_situacao_sigeduc(
    df_sigeduc: pl.DataFrame,
    coluna_cpf: str | None = None,
    coluna_data_fim: str | None = None,
    coluna_data_encerramento: str | None = None,
    coluna_periodo: str | None = None,
    coluna_situacao: str | None = None,
    status_aprovado: str = STATUS_APROVADO_PADRAO,
    aplicar_transferencia_externa: bool = True,
) -> tuple[pl.DataFrame, RelatorioSituacaoSigeduc]:
    """Resume `df_sigeduc` a uma linha por CPF (a mais recente) + contagem
    de aprovados, sem cruzar com nenhuma base de destino.

    Parameters
    ----------
    df_sigeduc:
        Base bruta do SIGEDUC -- um registro por matrícula/período, com a
        situação daquela matrícula (`'SITUAÇÃO MATRÍCULA'`).
    coluna_cpf, coluna_data_fim, coluna_data_encerramento, coluna_periodo,
    coluna_situacao:
        Nomes das colunas a usar. Se omitidos, tenta detectar
        automaticamente pelas variações mais comuns dos exports do
        SIGEDUC (as mesmas do Módulo F).
    status_aprovado:
        Texto (sem diferenciar maiúsculas/minúsculas) usado para contar
        `QTD_MODULOS_APROVADOS`: conta toda linha do SIGEDUC daquele CPF
        cuja situação contenha esse texto -- não só a situação mais
        recente. Default `'APROVADO EM MÓDULO'`.
    aplicar_transferencia_externa:
        Se True (padrão), reclassifica para `'TRANSFERÊNCIA EXTERNA'` todo
        CPF cuja situação mais recente é `'TRANSFERIDO'` e que nunca teve
        nenhuma outra situação no histórico do SIGEDUC enviado.

    Returns
    -------
    tuple[pl.DataFrame, RelatorioSituacaoSigeduc]
        `(resultado, relatorio)`:

        - `resultado`: uma linha por CPF válido encontrado no SIGEDUC --
          todas as colunas originais da planilha (do registro mais
          recente daquele CPF) mais `QTD_MODULOS_APROVADOS`. Ordenado por
          CPF.
        - `relatorio`: contagens do processamento.

    Raises
    ------
    KeyError
        Se alguma coluna obrigatória não existir em `df_sigeduc`.
    """
    coluna_cpf_r = coluna_cpf or exigir_coluna(df_sigeduc, COLUNAS_CPF_SIGEDUC_CANDIDATAS, "CPF (base SIGEDUC)")
    coluna_data_fim_r = coluna_data_fim or exigir_coluna(df_sigeduc, COLUNAS_DATA_FIM_CANDIDATAS, "Data Fim (SIGEDUC)")
    coluna_data_encerramento_r = coluna_data_encerramento or exigir_coluna(
        df_sigeduc, COLUNAS_DATA_ENCERRAMENTO_CANDIDATAS, "Data Encerramento (SIGEDUC)"
    )
    coluna_periodo_r = coluna_periodo or exigir_coluna(df_sigeduc, COLUNAS_PERIODO_CANDIDATAS, "Período (SIGEDUC)")
    coluna_situacao_r = coluna_situacao or exigir_coluna(
        df_sigeduc, COLUNAS_SITUACAO_CANDIDATAS, "Situação Matrícula (SIGEDUC)"
    )

    total_sigeduc = df_sigeduc.height

    sigeduc = preparar_sigeduc(df_sigeduc, coluna_cpf_r, coluna_data_fim_r, coluna_data_encerramento_r)
    total_sigeduc_cpf_valido = sigeduc.height

    contagem = contar_aprovados(sigeduc, coluna_cpf_r, coluna_situacao_r, status_aprovado)
    mais_recente = situacao_mais_recente(sigeduc, coluna_cpf_r, coluna_situacao_r)

    total_reclassificados = 0
    if aplicar_transferencia_externa:
        mais_recente, total_reclassificados = reclassificar_transferencia_externa(
            mais_recente, sigeduc, coluna_cpf_r, coluna_situacao_r
        )

    resultado = (
        mais_recente.drop("_data_ordenacao")
        .join(contagem, on=coluna_cpf_r, how="left")
        .with_columns(pl.col("_qtd_aprovados").fill_null(0))
        .rename({"_qtd_aprovados": COLUNA_QTD_APROVADOS})
        .sort(coluna_cpf_r)
    )

    total_com_aprovacao = int((resultado[COLUNA_QTD_APROVADOS] > 0).sum())

    relatorio = RelatorioSituacaoSigeduc(
        coluna_cpf=coluna_cpf_r,
        coluna_data_fim=coluna_data_fim_r,
        coluna_data_encerramento=coluna_data_encerramento_r,
        coluna_periodo=coluna_periodo_r,
        coluna_situacao=coluna_situacao_r,
        status_aprovado=status_aprovado,
        aplicar_transferencia_externa=aplicar_transferencia_externa,
        total_sigeduc=total_sigeduc,
        total_sigeduc_cpf_valido=total_sigeduc_cpf_valido,
        total_cpfs_unicos=resultado.height,
        total_com_aprovacao=total_com_aprovacao,
        total_reclassificados_transferencia_externa=total_reclassificados,
    )

    return resultado, relatorio


if __name__ == "__main__":
    # Auto-teste rápido -- rode com `python -m src.core.situacao_sigeduc` a
    # partir da raiz do projeto. Cobre: linha completa preservada (colunas
    # extras do SIGEDUC continuam no resultado), QTD_MODULOS_APROVADOS
    # calculado, situação mais recente por DATA ENCERRAMENTO, aluno só com
    # datas nulas, CPF inválido descartado, e transferência externa.
    sigeduc = pl.DataFrame(
        {
            "NU CPF": [
                "1", "1", "1",
                "2",
                "3", "3",
                "999999999999",
                "4", "4",
                "5", "5",
            ],
            "NOME": [
                "FULANO1", "FULANO1", "FULANO1",
                "CICLANA2",
                "BELTRANO3", "BELTRANO3",
                "CPF_INVALIDO",
                "FULANO4_TRANSF_COM_HIST", "FULANO4_TRANSF_COM_HIST",
                "FULANO5_TRANSF_EXTERNA", "FULANO5_TRANSF_EXTERNA",
            ],
            "ESCOLA": ["EEEP A"] * 11,
            "DATA FIM": [
                "01/01/2025", "01/06/2025", "01/12/2025",
                "15/03/2026",
                None, None,
                "01/01/2026",
                "01/01/2025", "01/06/2025",
                "01/01/2025", "01/07/2025",
            ],
            "DATA ENCERRAMENTO": [
                "01/02/2025", "01/07/2025", "20/12/2025",  # CPF 1: mais recente = 20/12/2025
                None,  # CPF 2: sem data válida -> mantém o único registro
                None, None,  # CPF 3: nenhuma data válida -> mantém o último (2º registro)
                "01/01/2026",  # CPF inválido (>11 dígitos), descartado
                None, "01/07/2025",  # CPF 4: TRANSFERIDO mais recente, mas teve CURSANDO antes -> continua TRANSFERIDO
                None, "01/07/2025",  # CPF 5: só teve TRANSFERIDO -> vira TRANSFERÊNCIA EXTERNA
            ],
            "PERÍODO": ["1", "2", "3", "1", "1", "2", "1", "1", "2", "1", "2"],
            "SITUACAO MATRÍCULA": [
                "APROVADO EM MÓDULO", "REPROVADO", "APROVADO EM MÓDULO",
                "APROVADO EM MÓDULO",
                "CURSANDO", "TRANSFERIDO",
                "APROVADO EM MÓDULO",
                "CURSANDO", "TRANSFERIDO",
                "TRANSFERIDO", "TRANSFERIDO",
            ],
        }
    )

    print("=== Sem Transferência Externa ===")
    resultado, relatorio = resumir_situacao_sigeduc(sigeduc, aplicar_transferencia_externa=False)
    with pl.Config(tbl_cols=-1, tbl_width_chars=220):
        print(resultado)
    print(relatorio.model_dump_json(indent=2))

    # Linha completa: as colunas originais (NOME, ESCOLA) continuam lá.
    assert set(["NOME", "ESCOLA", "NU CPF", "DATA FIM", "DATA ENCERRAMENTO", "PERÍODO", "SITUACAO MATRÍCULA", "QTD_MODULOS_APROVADOS"]) <= set(resultado.columns)
    assert relatorio.total_sigeduc == 11
    assert relatorio.total_sigeduc_cpf_valido == 10  # 1 CPF inválido descartado
    assert relatorio.total_cpfs_unicos == 5  # CPFs 1-5

    linha = lambda nome: resultado.filter(pl.col("NOME") == nome).row(0, named=True)

    assert linha("FULANO1")["SITUACAO MATRÍCULA"] == "APROVADO EM MÓDULO"  # 20/12/2025 é o mais recente
    assert linha("FULANO1")["QTD_MODULOS_APROVADOS"] == 2
    assert linha("FULANO1")["DATA ENCERRAMENTO"] == "20/12/2025"
    assert linha("CICLANA2")["QTD_MODULOS_APROVADOS"] == 1
    assert linha("BELTRANO3")["SITUACAO MATRÍCULA"] == "TRANSFERIDO"  # nenhuma data válida -> último registro
    assert linha("FULANO4_TRANSF_COM_HIST")["SITUACAO MATRÍCULA"] == "TRANSFERIDO"  # checagem desligada
    assert linha("FULANO5_TRANSF_EXTERNA")["SITUACAO MATRÍCULA"] == "TRANSFERIDO"  # checagem desligada
    assert relatorio.total_reclassificados_transferencia_externa == 0
    print("\nOK: resumo básico (sem transferência externa) passou.\n")

    print("=== Com Transferência Externa ===")
    resultado2, relatorio2 = resumir_situacao_sigeduc(sigeduc, aplicar_transferencia_externa=True)
    with pl.Config(tbl_cols=-1, tbl_width_chars=220):
        print(resultado2)
    print(relatorio2.model_dump_json(indent=2))

    linha2 = lambda nome: resultado2.filter(pl.col("NOME") == nome).row(0, named=True)
    assert linha2("FULANO4_TRANSF_COM_HIST")["SITUACAO MATRÍCULA"] == "TRANSFERIDO"  # teve CURSANDO -> não é externa
    assert linha2("FULANO5_TRANSF_EXTERNA")["SITUACAO MATRÍCULA"] == "TRANSFERÊNCIA EXTERNA"  # só teve TRANSFERIDO
    assert relatorio2.total_reclassificados_transferencia_externa == 1
    assert resultado2.height == resultado.height  # a reclassificação não muda a contagem de linhas
    print("\nOK: transferência externa passou.")

    print("\nOK: auto-teste do Módulo G passou.")
