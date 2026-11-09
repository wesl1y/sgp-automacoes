"""Escola 10 / Pé de Meia — Automação de Dados (SEDUC)
=======================================================

Ponto de entrada da aplicação Streamlit. Este arquivo é propositalmente
enxuto: toda a lógica de negócio vive em `src/core`, a validação de
schemas em `src/schemas`, as funções de limpeza em `src/utils` e os
componentes visuais reutilizáveis em `src/ui`. O `app.py` só monta a
navegação e delega o trabalho pesado para esses módulos.

Execução local:

    pip install -r requirements.txt
    streamlit run app.py
"""

from __future__ import annotations

import datetime
import importlib
import inspect

import duckdb
import polars as pl
import streamlit as st

from src.core.ajuste_tercos_eja import preparar_ajuste_tercos_eja
from src.core.cadastro_proxima_matricula import gerar_cadastro_proxima_matricula
from src.core.reconciliacao_eja import reconciliar_eja
from src.core.deduplicacao import (
    COLUNAS_ALOCACAO_CANDIDATAS,
    COLUNAS_ATUALIZACAO_CANDIDATAS,
    COLUNAS_DATA_CANDIDATAS,
    COLUNAS_ENCERRAMENTO_CANDIDATAS,
    deduplicar_matricula_mais_recente,
)
from src.core.comparacao_situacao import comparar_situacoes
from src.core.comparacao_serie import comparar_series
from src.core.etapa_de_ensino import (
    COLUNAS_OFERTA_CANDIDATAS,
    COLUNAS_SERIE_CANDIDATAS,
    calcular_etapa_de_ensino,
)
from src.core.exclusao_movimentacao import excluir_cpfs_movimentacao
from src.core.exportacao import ORDEM_PADRAO_MEC, exportar_padrao_mec
from src.core.frequencia import (
    COLUNA_FALTAS_ALUNO_HORA,
    COLUNA_HORA_AULA_DADA,
    COLUNAS_ANO_PERIODO_TERCO_CANDIDATAS as COLUNAS_ANO_PERIODO_TERCO_FREQ_CANDIDATAS,
    COLUNAS_CPF_ID_SGP_CANDIDATAS,
    COLUNAS_CPF_SGP_CANDIDATAS,
    COLUNAS_CPF_SIGEDUC_CANDIDATAS,
    COLUNAS_CPF_TERCO_CANDIDATAS as COLUNAS_CPF_TERCO_FREQ_CANDIDATAS,
    COLUNAS_ENTIDADE_SIGEDUC_CANDIDATAS,
    COLUNAS_ID_SGP_MATRICULA_CANDIDATAS,
    COLUNAS_MATRICULA_SGP_CANDIDATAS,
    COLUNAS_MATRICULA_SIGEDUC_CANDIDATAS,
    COLUNAS_MES_REFERENCIA_CANDIDATAS,
    COLUNAS_OFERTADA_CANDIDATAS,
    COLUNAS_PRESENTE_CANDIDATAS,
    calcular_frequencia,
    gerar_frequencia_do_sigeduc,
)
from src.core._colunas import encontrar_coluna
from src.core.movimentacao import (
    COLUNAS_ANO_PERIODO_TERCO_CANDIDATAS,
    COLUNAS_CENSO_SIGEDUC_CANDIDATAS,
    COLUNAS_CPF_SGP_CANDIDATAS as COLUNAS_CPF_SGP_MOV_CANDIDATAS,
    COLUNAS_CPF_SIGEDUC_CANDIDATAS as COLUNAS_CPF_SIGEDUC_MOV_CANDIDATAS,
    COLUNAS_CPF_TERCO_CANDIDATAS,
    COLUNAS_DATA_ENCERRAMENTO_SIGEDUC_CANDIDATAS,
    COLUNAS_DATA_MATRICULA_SIGEDUC_CANDIDATAS,
    COLUNAS_ENTIDADE_SGP_CANDIDATAS,
    COLUNAS_ETAPA_SGP_CANDIDATAS,
    COLUNAS_SITUACAO_SIGEDUC_CANDIDATAS,
    reconciliar_sgp_sigeduc,
)
from src.core.situacao_aluno import (
    COLUNAS_CENSO_CANDIDATAS,
    COLUNAS_CO_ENTIDADE_CANDIDATAS,
    COLUNAS_CPF_DESTINO_CANDIDATAS,
    COLUNAS_CPF_SIGEDUC_CANDIDATAS as COLUNAS_CPF_SIGEDUC_SITUACAO_CANDIDATAS,
    COLUNAS_DATA_ENCERRAMENTO_CANDIDATAS,
    COLUNAS_DATA_MATRICULA_DESTINO_CANDIDATAS,
    COLUNAS_DATA_MATRICULA_SIGEDUC_CANDIDATAS,
    COLUNAS_DATA_FIM_CANDIDATAS,
    COLUNAS_PERIODO_CANDIDATAS,
    COLUNAS_SITUACAO_CANDIDATAS,
    STATUS_APROVADO_PADRAO,
    calcular_situacao_aluno,
)
from src.core.situacao_sigeduc import resumir_situacao_sigeduc
from src.ui.downloads import botoes_download, para_bytes_excel
from src.ui.uploader import carregar_planilha
from src.utils.higienizacao import limpar_cpf, padronizar_textos

# O Streamlit reexecuta app.py após uma edição, mas pode manter módulos de
# src.core já importados na mesma sessão. Atualiza a função se a sessão
# ainda estiver com a assinatura anterior do módulo F.
if "coluna_data_matricula_sigeduc" not in inspect.signature(calcular_situacao_aluno).parameters:
    calcular_situacao_aluno = importlib.reload(
        importlib.import_module("src.core.situacao_aluno")
    ).calcular_situacao_aluno

# O Streamlit pode manter a lógica antiga em uma sessão já aberta mesmo
# quando a assinatura não muda. Atualiza o módulo M em cada execução da tela.
reconciliar_eja = importlib.reload(
    importlib.import_module("src.core.reconciliacao_eja")
).reconciliar_eja

st.set_page_config(
    page_title="Escola 10 | Pé de Meia — Automação SEDUC",
    page_icon="📊",
    layout="wide",
)

# Os módulos de regra de negócio de `/src/core` são implementados um de
# cada vez. Os que ainda não têm código aparecem como "em breve"; os que
# já têm ganham uma função de página própria (veja `PAGINAS_IMPLEMENTADAS`
# mais abaixo).
MODULOS_CORE: dict[str, str | None] = {
    "🏠 Início": None,
    "🔁 A. Deduplicação e Matrícula Mais Recente": "dedup",
    "🎓 B. Motor de Etapa de Ensino": "etapa",
    "🔀 C. Discrepâncias e Movimentação": "movimentacao",
    "📅 D. Cálculo de Frequência": "frequencia",
    "📤 E. Exportação Padrão MEC": "exportacao",
    "✅ F. Situação de Matrícula e Aprovações": "situacao_aluno",
    "📥 G. Situação de Matrícula (Somente SIGEDUC)": "situacao_sigeduc",
    "🔎 H. Comparar Situação SIGEDUC × SGP": "comparacao_situacao",
    "📚 I. Comparar Série/Ano SIGEDUC × SGP": "comparacao_serie",
    "🧩 J. Ajuste de Terços EJA": "ajuste_tercos_eja",
    "🧹 K. Retirar Movimentações do SGP": "exclusao_movimentacao",
    "📥 L. Cadastro da Próxima Matrícula": "cadastro_proxima_matricula",
    "🔄 M. Reconciliação Completa EJA": "reconciliacao_eja",
}


def pagina_inicio() -> None:
    st.title("📊 Escola 10 & Pé de Meia — Automação de Dados")
    st.markdown(
        """
Bem-vindo(a)! Esta aplicação vai substituir a coleção atual de scripts
Python soltos (Colab/Pandas) usados nas rotinas do **Escola 10** e do
**Pé de Meia** por uma interface web única, construída sobre:

- **Streamlit** para upload/download de planilhas e visualização dos relatórios;
- **Polars** para processar as bases em memória rapidamente;
- **DuckDB** para rodar consultas SQL analíticas direto sobre os DataFrames do Polars;
- **Pydantic** para validar o formato das planilhas de entrada e saída.

Use o menu à esquerda para navegar entre os módulos conforme forem implementados.
"""
    )

    st.divider()
    st.subheader("🧪 Teste rápido do ambiente")
    st.caption(
        "Envie uma planilha (.csv ou .xlsx) para confirmar que upload, Polars, "
        "DuckDB e as funções de higienização já estão funcionando antes de "
        "implementarmos as próximas regras de negócio."
    )

    arquivo = st.file_uploader("Planilha de teste", type=["csv", "xlsx", "xls"], key="upload_inicio")
    if arquivo is None:
        st.caption("Nenhum arquivo enviado ainda.")
        return

    try:
        df = carregar_planilha(arquivo)
    except Exception as exc:  # noqa: BLE001 — qualquer erro de leitura deve virar mensagem para o usuário
        st.error(f"Não foi possível ler o arquivo: {exc}")
        return

    col_a, col_b, col_c = st.columns(3)
    col_a.metric("Linhas", f"{df.height:,}".replace(",", "."))
    col_b.metric("Colunas", df.width)
    col_c.metric("Tamanho em memória", f"{df.estimated_size('mb'):.2f} MB")

    st.write("**Pré-visualização (Polars):**")
    st.dataframe(df.head(20), width="stretch")

    with st.expander("🦆 Consulta de exemplo via DuckDB sobre o DataFrame do Polars"):
        st.code('duckdb.sql("SELECT * FROM df LIMIT 5").pl()', language="python")
        amostra_duckdb = duckdb.sql("SELECT * FROM df LIMIT 5").pl()
        st.dataframe(amostra_duckdb, width="stretch")

    colunas_texto = [c for c, dtype in df.schema.items() if dtype == pl.Utf8]
    colunas_cpf = [c for c in df.columns if "CPF" in c.upper()]

    if colunas_texto or colunas_cpf:
        st.write("**Pré-visualização da higienização (`src/utils`):**")
        aplicar = st.checkbox(
            "Aplicar padronização de texto e limpeza de CPF nas colunas detectadas"
        )
        if aplicar:
            transformacoes = [
                padronizar_textos(pl.col(c)).alias(c) for c in colunas_texto if c not in colunas_cpf
            ]
            transformacoes += [limpar_cpf(pl.col(c)).alias(c) for c in colunas_cpf]
            df_limpo = df.with_columns(transformacoes)
            st.dataframe(df_limpo.head(20), width="stretch")


def pagina_dedup() -> None:
    st.title("🔁 A. Deduplicação e Matrícula Mais Recente")
    st.markdown(
        """
Recebe uma base do SIGEDUC e mantém **apenas um registro por CPF**. Para
cada linha, usa a **data de encerramento** ou, quando ela estiver vazia,
a **data de atualização do cadastro**. Vence a linha cuja data de referência
é mais recente. Em empate, prefere o registro com encerramento. Sem essas
duas datas, prefere um registro com alocação; depois compara a data de
matrícula e, em empate, a data de alocação.
"""
    )

    arquivo = st.file_uploader("Planilha do SIGEDUC (.csv ou .xlsx)", type=["csv", "xlsx", "xls"], key="upload_dedup")
    if arquivo is None:
        st.caption("Nenhum arquivo enviado ainda.")
        return

    try:
        df = carregar_planilha(arquivo)
    except Exception as exc:  # noqa: BLE001
        st.error(f"Não foi possível ler o arquivo: {exc}")
        return

    st.write(f"Arquivo lido: **{df.height} linhas** × **{df.width} colunas**.")

    def indice_sugerido(candidatas: tuple[str, ...]) -> int | None:
        encontrada = encontrar_coluna(df, candidatas)
        return df.columns.index(encontrada) if encontrada else None

    col1, col2 = st.columns(2)
    coluna_cpf = col1.selectbox(
        "Coluna de CPF do estudante",
        options=df.columns,
        index=indice_sugerido(("ESTUDANTE_CPF", "CPF", "NU_CPF")),
        placeholder="Selecione a coluna de CPF",
    )
    coluna_data = col2.selectbox(
        "Coluna de data de matrícula",
        options=df.columns,
        index=indice_sugerido(COLUNAS_DATA_CANDIDATAS),
        placeholder="Selecione a data de matrícula",
    )
    col3, col4 = st.columns(2)
    indice_encerramento = indice_sugerido(COLUNAS_ENCERRAMENTO_CANDIDATAS)
    indice_atualizacao = indice_sugerido(COLUNAS_ATUALIZACAO_CANDIDATAS)
    coluna_encerramento = col3.selectbox(
        "Coluna de data de encerramento (se existir)",
        options=["", *df.columns],
        index=indice_encerramento + 1 if indice_encerramento is not None else 0,
        format_func=lambda valor: "Não existe nesta planilha" if valor == "" else valor,
    )
    coluna_atualizacao = col4.selectbox(
        "Coluna de data de atualização do cadastro (se existir)",
        options=["", *df.columns],
        index=indice_atualizacao + 1 if indice_atualizacao is not None else 0,
        format_func=lambda valor: "Não existe nesta planilha" if valor == "" else valor,
    )
    coluna_alocacao = st.selectbox(
        "Coluna de data de alocação",
        options=df.columns,
        index=indice_sugerido(COLUNAS_ALOCACAO_CANDIDATAS),
        placeholder="Selecione a data de alocação",
    )

    if not st.button("▶️ Deduplicar", type="primary"):
        return
    if None in (coluna_cpf, coluna_data, coluna_alocacao):
        st.error("Selecione as colunas de CPF, matrícula e alocação.")
        return
    if not coluna_encerramento and not coluna_atualizacao:
        st.error("Selecione a data de encerramento ou a data de atualização do cadastro.")
        return

    try:
        df_final, relatorio = deduplicar_matricula_mais_recente(
            df, coluna_cpf, coluna_data,
            coluna_data_encerramento=coluna_encerramento,
            coluna_data_alocacao=coluna_alocacao,
            coluna_data_atualizacao=coluna_atualizacao,
        )
    except (KeyError, ValueError) as exc:
        st.error(str(exc))
        return

    st.success("Deduplicação concluída.")

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Recebidos", relatorio.total_registros_recebidos)
    m2.metric("CPF inválido removido", relatorio.total_cpf_invalido_removido)
    m3.metric("Duplicatas removidas", relatorio.total_duplicatas_removidas)
    m4.metric("Final (1 por CPF)", relatorio.total_registros_final)

    if relatorio.total_data_invalida_mantida:
        st.warning(
            f"{relatorio.total_data_invalida_mantida} registro(s) foram mantidos mesmo "
            "sem nenhuma das datas reconhecida (para não perder o aluno) — "
            "vale conferir manualmente."
        )

    st.write("**Resultado (uma linha por CPF, conforme a prioridade das datas):**")
    st.dataframe(df_final.head(50), width="stretch")

    botoes_download(df_final, nome_base="matriculas_deduplicadas", key_prefix="dedup")


def pagina_etapa() -> None:
    st.title("🎓 B. Motor de Etapa de Ensino")
    st.markdown(
        """
Lê as colunas de **Oferta de Ensino** e **Série** e calcula a coluna
`ESTUDANTE_ETAPA_DE_ENSINO`:

| Modalidade | 1ª série | 2ª série | 3ª série | 4ª série |
|---|---|---|---|---|
| EJA | 45 (mesmo código nas 4 séries) | | | |
| Técnico / Integrado | 30 | 31 | 32 | 33 |
| Regular | 25 | 26 | 27 | 28 |

Registros que não batem com nenhuma regra ficam marcados como
`NAO_MAPEADO` — nenhum aluno é descartado silenciosamente. Implementado
como uma consulta SQL (CASE WHEN) no DuckDB, em `src/core/etapa_de_ensino.py`.
"""
    )

    arquivo = st.file_uploader(
        "Planilha do SIGEDUC (.csv ou .xlsx)", type=["csv", "xlsx", "xls"], key="upload_etapa"
    )
    if arquivo is None:
        st.caption("Nenhum arquivo enviado ainda.")
        return

    try:
        df = carregar_planilha(arquivo)
    except Exception as exc:  # noqa: BLE001
        st.error(f"Não foi possível ler o arquivo: {exc}")
        return

    st.write(f"Arquivo lido: **{df.height} linhas** × **{df.width} colunas**.")

    col_oferta_padrao = next((c for c in COLUNAS_OFERTA_CANDIDATAS if c in df.columns), df.columns[0])
    col_serie_padrao = next((c for c in COLUNAS_SERIE_CANDIDATAS if c in df.columns), df.columns[0])

    col1, col2 = st.columns(2)
    coluna_oferta = col1.selectbox(
        "Coluna de Oferta de Ensino",
        options=df.columns,
        index=df.columns.index(col_oferta_padrao),
    )
    coluna_serie = col2.selectbox(
        "Coluna de Série",
        options=df.columns,
        index=df.columns.index(col_serie_padrao),
    )

    if not st.button("▶️ Calcular Etapa de Ensino", type="primary"):
        return

    try:
        df_final, relatorio = calcular_etapa_de_ensino(df, coluna_oferta, coluna_serie)
    except KeyError as exc:
        st.error(str(exc))
        return

    st.success("Coluna 'ESTUDANTE_ETAPA_DE_ENSINO' calculada.")

    if relatorio.total_nao_mapeado:
        st.warning(
            f"{relatorio.total_nao_mapeado} de {relatorio.total_registros} registro(s) não "
            "bateram com nenhuma regra (ficaram como 'NAO_MAPEADO') — vale conferir manualmente "
            "os valores de Oferta de Ensino / Série desses casos."
        )

    st.write("**Distribuição por código:**")
    contagem_df = pl.DataFrame(
        {
            "ESTUDANTE_ETAPA_DE_ENSINO": list(relatorio.contagem_por_etapa.keys()),
            "quantidade": list(relatorio.contagem_por_etapa.values()),
        }
    )
    col_tabela, col_grafico = st.columns([1, 2])
    col_tabela.dataframe(contagem_df, width="stretch", hide_index=True)
    col_grafico.bar_chart(contagem_df, x="ESTUDANTE_ETAPA_DE_ENSINO", y="quantidade")

    st.write("**Pré-visualização do resultado:**")
    st.dataframe(df_final.head(50), width="stretch")

    botoes_download(df_final, nome_base="com_etapa_de_ensino", key_prefix="etapa")


def pagina_movimentacao() -> None:
    st.title("🔀 C. Discrepâncias e Movimentação")
    st.markdown(
        """
Reconcilia a base **SGP atual** (quem já está cadastrado) com uma ou mais
planilhas brutas do **SIGEDUC** (a fonte de verdade) e devolve dois
relatórios:

- **Cadastro** — CPFs que aparecem no SIGEDUC mas ainda não existem no SGP,
  já formatados no layout oficial de cadastro (24 colunas), prontos para
  importar como aluno novo, mais uma 25ª coluna extra `SITUACAO_ALUNO`
  (fora do layout oficial) com a situação de matrícula no SIGEDUC, só
  para conferência. Enviando a planilha opcional de **Terços**, a coluna
  `ESTUDANTE_ANO_PERIODO` (terço do EJA) também sai preenchida, cruzada
  por CPF.
- **Movimentação** — só os CPFs já cadastrados que tiveram
  **`trans. escola`** (mudou de CO_ENTIDADE) ou **`trans. mod`** (mesma
  escola, mas cruzou entre EJA e Regular/Técnico). Quem não teve nenhuma
  mudança, ou teve a matrícula **cancelada** (mudança de modalidade sem
  data de encerramento registrada, com histórico de cancelamento no
  SIGEDUC), não entra nesta planilha — mas os dois casos continuam
  contados nos avisos abaixo dela.

Implementado em `src/core/movimentacao.py`.
"""
    )

    col_up1, col_up2 = st.columns(2)
    arquivo_sgp = col_up1.file_uploader(
        "Base SGP (atual)", type=["csv", "xlsx", "xls"], key="upload_mov_sgp"
    )
    arquivos_sigeduc = col_up2.file_uploader(
        "Planilha(s) do SIGEDUC",
        type=["csv", "xlsx", "xls"],
        accept_multiple_files=True,
        key="upload_mov_sigeduc",
    )
    arquivo_tercos = st.file_uploader(
        "Planilha de Terços (SIGEDUC) — opcional",
        type=["csv", "xlsx", "xls"],
        key="upload_mov_tercos",
        help="Só CPF + ano/período (terço) do EJA -- ex. colunas ESTUDANTE_CPF e "
        "ESTUDANTE_ANO_PERIODO. Cruzada por CPF só para preencher essa coluna no "
        "Cadastro; sem ela, ESTUDANTE_ANO_PERIODO sai em branco.",
    )
    if arquivo_sgp is None or not arquivos_sigeduc:
        st.caption("Envie a base do SGP e ao menos uma planilha do SIGEDUC para continuar.")
        return

    try:
        df_sgp = carregar_planilha(arquivo_sgp)
        partes_sigeduc = [
            carregar_planilha(arquivo).with_columns(pl.lit(arquivo.name).alias("ORIGEM_ARQUIVO"))
            for arquivo in arquivos_sigeduc
        ]
        df_sigeduc = pl.concat(partes_sigeduc, how="diagonal_relaxed")
        df_tercos = carregar_planilha(arquivo_tercos) if arquivo_tercos is not None else None
    except Exception as exc:  # noqa: BLE001
        st.error(f"Não foi possível ler um dos arquivos: {exc}")
        return

    st.write(
        f"SGP: **{df_sgp.height} linhas** × **{df_sgp.width} colunas** · "
        f"SIGEDUC: **{df_sigeduc.height} linhas** de **{len(arquivos_sigeduc)} arquivo(s)** "
        f"× **{df_sigeduc.width} colunas**."
    )

    # Cada coluna é detectada de forma independente em cada base -- o
    # índice pré-selecionado é só um "melhor palpite" tolerante a
    # acento/espaço/caixa; `reconciliar_sgp_sigeduc` reconfere tudo (e
    # avisa com uma mensagem clara se algo obrigatório não bater).
    def _indice_padrao(df: pl.DataFrame, candidatos: tuple[str, ...]) -> int:
        nome = encontrar_coluna(df, candidatos)
        return df.columns.index(nome) if nome is not None else 0

    with st.expander("⚙️ Colunas detectadas (ajuste se necessário)"):
        st.caption("Base SGP atual:")
        col1, col2, col3 = st.columns(3)
        coluna_cpf_sgp = col1.selectbox(
            "Coluna de CPF",
            options=df_sgp.columns,
            index=_indice_padrao(df_sgp, COLUNAS_CPF_SGP_MOV_CANDIDATAS),
            key="mov_cpf_sgp",
        )
        coluna_entidade_sgp = col2.selectbox(
            "Coluna de CO_ENTIDADE",
            options=df_sgp.columns,
            index=_indice_padrao(df_sgp, COLUNAS_ENTIDADE_SGP_CANDIDATAS),
            key="mov_entidade_sgp",
        )
        coluna_etapa_sgp = col3.selectbox(
            "Coluna de Etapa de Ensino",
            options=df_sgp.columns,
            index=_indice_padrao(df_sgp, COLUNAS_ETAPA_SGP_CANDIDATAS),
            key="mov_etapa_sgp",
        )

        st.caption("SIGEDUC:")
        col4, col5, col6, col7 = st.columns(4)
        coluna_cpf_sigeduc = col4.selectbox(
            "Coluna de CPF",
            options=df_sigeduc.columns,
            index=_indice_padrao(df_sigeduc, COLUNAS_CPF_SIGEDUC_MOV_CANDIDATAS),
            key="mov_cpf_sigeduc",
        )
        coluna_censo_sigeduc = col5.selectbox(
            "Coluna de CENSO",
            options=df_sigeduc.columns,
            index=_indice_padrao(df_sigeduc, COLUNAS_CENSO_SIGEDUC_CANDIDATAS),
            key="mov_censo_sigeduc",
        )
        coluna_situacao_sigeduc = col6.selectbox(
            "Coluna de Situação Matrícula",
            options=df_sigeduc.columns,
            index=_indice_padrao(df_sigeduc, COLUNAS_SITUACAO_SIGEDUC_CANDIDATAS),
            key="mov_situacao_sigeduc",
        )
        coluna_data_encerramento_sigeduc = col7.selectbox(
            "Coluna de Data Encerramento",
            options=df_sigeduc.columns,
            index=_indice_padrao(df_sigeduc, COLUNAS_DATA_ENCERRAMENTO_SIGEDUC_CANDIDATAS),
            key="mov_data_encerramento_sigeduc",
        )

        col8, col9, col10 = st.columns(3)
        coluna_data_matricula_sigeduc = col8.selectbox(
            "Coluna de Data Matrícula",
            options=df_sigeduc.columns,
            index=_indice_padrao(df_sigeduc, COLUNAS_DATA_MATRICULA_SIGEDUC_CANDIDATAS),
            key="mov_data_matricula_sigeduc",
        )
        coluna_oferta_sigeduc = col9.selectbox(
            "Coluna de Oferta de Ensino",
            options=df_sigeduc.columns,
            index=_indice_padrao(df_sigeduc, COLUNAS_OFERTA_CANDIDATAS),
            key="mov_oferta_sigeduc",
        )
        coluna_serie_sigeduc = col10.selectbox(
            "Coluna de Série",
            options=df_sigeduc.columns,
            index=_indice_padrao(df_sigeduc, COLUNAS_SERIE_CANDIDATAS),
            key="mov_serie_sigeduc",
        )

        if df_tercos is not None:
            st.caption("Terços (opcional):")
            col_terco1, col_terco2 = st.columns(2)
            coluna_cpf_terco = col_terco1.selectbox(
                "Coluna de CPF",
                options=df_tercos.columns,
                index=_indice_padrao(df_tercos, COLUNAS_CPF_TERCO_CANDIDATAS),
                key="mov_cpf_terco",
            )
            coluna_ano_periodo_terco = col_terco2.selectbox(
                "Coluna de Ano/Período (terço)",
                options=df_tercos.columns,
                index=_indice_padrao(df_tercos, COLUNAS_ANO_PERIODO_TERCO_CANDIDATAS),
                key="mov_ano_periodo_terco",
            )
        else:
            coluna_cpf_terco = None
            coluna_ano_periodo_terco = None

    if not st.button("▶️ Reconciliar bases", type="primary"):
        return

    try:
        df_cadastro, df_movimentacao, relatorio = reconciliar_sgp_sigeduc(
            df_sgp,
            df_sigeduc,
            coluna_cpf_sgp=coluna_cpf_sgp,
            coluna_entidade_sgp=coluna_entidade_sgp,
            coluna_etapa_sgp=coluna_etapa_sgp,
            coluna_cpf_sigeduc=coluna_cpf_sigeduc,
            coluna_censo_sigeduc=coluna_censo_sigeduc,
            coluna_situacao_sigeduc=coluna_situacao_sigeduc,
            coluna_data_encerramento_sigeduc=coluna_data_encerramento_sigeduc,
            coluna_data_matricula_sigeduc=coluna_data_matricula_sigeduc,
            coluna_oferta_sigeduc=coluna_oferta_sigeduc,
            coluna_serie_sigeduc=coluna_serie_sigeduc,
            df_tercos=df_tercos,
            coluna_cpf_terco=coluna_cpf_terco,
            coluna_ano_periodo_terco=coluna_ano_periodo_terco,
        )
    except KeyError as exc:
        st.error(str(exc))
        return

    st.success("Reconciliação concluída.")

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Base SGP", relatorio.total_sgp)
    m2.metric("Cadastro (novos)", relatorio.total_cadastro)
    m3.metric("Trans. escola", relatorio.total_trans_escola)
    m4.metric("Trans. mod", relatorio.total_trans_mod)

    if relatorio.total_sem_alteracao:
        st.caption(
            f"ℹ️ {relatorio.total_sem_alteracao} CPF(s) da base SGP não tiveram nenhuma mudança "
            "e por isso não aparecem na planilha de Movimentação abaixo."
        )
    if relatorio.total_cancelado:
        st.caption(
            f"ℹ️ {relatorio.total_cancelado} CPF(s) tiveram a matrícula classificada como "
            "'cancelado' — também não aparecem na planilha de Movimentação abaixo."
        )
    if relatorio.total_sgp_sem_correspondencia_sigeduc:
        st.caption(
            f"ℹ️ {relatorio.total_sgp_sem_correspondencia_sigeduc} CPF(s) da base SGP não têm "
            "nenhuma matrícula ativa correspondente nas planilhas do SIGEDUC enviadas."
        )
    if relatorio.coluna_data_atualizacao_sigeduc is None:
        st.caption(
            "ℹ️ Não encontrei uma coluna de 'Data Atualização do Cadastro' no SIGEDUC -- "
            "usei só a Data Encerramento como referência de data."
        )
    if relatorio.coluna_cpf_terco is not None and relatorio.total_cadastro_sem_terco:
        st.caption(
            f"ℹ️ {relatorio.total_cadastro_sem_terco} de {relatorio.total_cadastro} CPF(s) do "
            "Cadastro não têm terço correspondente na planilha de Terços enviada -- "
            "ESTUDANTE_ANO_PERIODO ficou em branco para eles."
        )

    aba_cad, aba_mov = st.tabs(
        [f"📥 Cadastro ({df_cadastro.height})", f"🔀 Movimentação ({df_movimentacao.height})"]
    )
    with aba_cad:
        st.dataframe(df_cadastro.head(50), width="stretch")
        botoes_download(df_cadastro, nome_base="cadastro", key_prefix="cad")
    with aba_mov:
        st.dataframe(df_movimentacao.head(50), width="stretch")
        botoes_download(df_movimentacao, nome_base="movimentacao", key_prefix="mov")


def pagina_frequencia() -> None:
    st.title("📅 D. Cálculo de Frequência")
    st.markdown(
        """
Dois modos, dependendo do que você já tem disponível:

- **🔄 Atualizar frequência existente** — cruza uma base do SGP que **já
  tem** frequência lançada com a base bruta do SIGEDUC (pelo CPF **e**
  pela matrícula exata do aluno) e fica com o maior valor entre os dois.
  Não descarta nenhuma linha do SGP.
- **🆕 Gerar frequência nova** — monta a planilha de frequência **do
  zero**, só a partir da base bruta do SIGEDUC (sem precisar de nenhuma
  frequência já lançada no SGP) — pensado para a primeira carga do
  período.
"""
    )

    modo = st.radio(
        "Modo",
        options=[
            "🔄 Atualizar frequência existente (SGP + SIGEDUC)",
            "🆕 Gerar frequência nova (a partir do SIGEDUC)",
        ],
        key="freq_modo",
    )

    def _padrao(colunas: list[str], candidatos: tuple[str, ...]) -> str:
        return next((c for c in candidatos if c in colunas), colunas[0])

    if modo.startswith("🔄"):
        st.markdown(
            """
1. Lê `HL_OFERTADA_PERIODO`/`HL_PRESENTE_PERIODO` direto da base do SIGEDUC
   (já vêm prontos na fonte).
2. Cruza SGP × SIGEDUC pelo CPF **e** pela matrícula exata (`ID_SGP_MATRICULA`
   no SGP / `Matrícula SGP` no SIGEDUC) — não basta o CPF bater, porque o
   mesmo aluno pode ter mais de uma matrícula, e cruzar só por CPF podia
   pegar a frequência de uma matrícula errada.
3. Para cada matrícula encontrada nas duas bases, fica sempre com o
   **maior** valor entre o que já estava no SGP e o que veio do SIGEDUC —
   a frequência lançada nunca diminui por causa deste cruzamento. Quem não
   aparece no SIGEDUC (mesmo CPF e matrícula) mantém o valor do SGP sem
   alteração.
4. Quem teve o valor alterado é classificado conforme o percentual de
   presença (`HL_PRESENTE / HL_OFERTADA`): **subiu**, **desceu (abaixo de
   80%)**, **desceu (acima de 80%)**, ou **manteve o percentual** (só a
   carga horária mudou).

Nenhum aluno do SGP é descartado — a planilha "Frequência Atualizada" sai
sempre com o mesmo número de linhas da planilha do SGP enviada, pronta para
reenvio. Implementado com um `LEFT JOIN` em SQL no DuckDB, em
`src/core/frequencia.py`.
"""
        )

        col_up1, col_up2 = st.columns(2)
        arquivo_sgp = col_up1.file_uploader(
            "Base SGP (atual)", type=["csv", "xlsx", "xls"], key="upload_freq_sgp"
        )
        arquivo_sigeduc = col_up2.file_uploader(
            "Base SIGEDUC (bruta)", type=["csv", "xlsx", "xls"], key="upload_freq_sigeduc"
        )
        if arquivo_sgp is None or arquivo_sigeduc is None:
            st.caption("Envie as duas planilhas (SGP e SIGEDUC) para continuar.")
            return

        try:
            df_sgp = carregar_planilha(arquivo_sgp)
            df_sigeduc = carregar_planilha(arquivo_sigeduc)
        except Exception as exc:  # noqa: BLE001
            st.error(f"Não foi possível ler um dos arquivos: {exc}")
            return

        st.write(
            f"SGP: **{df_sgp.height} linhas** × **{df_sgp.width} colunas** · "
            f"SIGEDUC: **{df_sigeduc.height} linhas** × **{df_sigeduc.width} colunas**."
        )

        col1, col2 = st.columns(2)
        coluna_cpf_sgp = col1.selectbox(
            "Coluna de CPF (base SGP)",
            options=df_sgp.columns,
            index=df_sgp.columns.index(_padrao(df_sgp.columns, COLUNAS_CPF_SGP_CANDIDATAS)),
        )
        coluna_cpf_sigeduc = col2.selectbox(
            "Coluna de CPF (base SIGEDUC)",
            options=df_sigeduc.columns,
            index=df_sigeduc.columns.index(_padrao(df_sigeduc.columns, COLUNAS_CPF_SIGEDUC_CANDIDATAS)),
        )

        col_mat1, col_mat2 = st.columns(2)
        coluna_matricula_sgp = col_mat1.selectbox(
            "Coluna de matrícula do aluno (ID_SGP_MATRICULA, base SGP)",
            options=df_sgp.columns,
            index=df_sgp.columns.index(_padrao(df_sgp.columns, COLUNAS_MATRICULA_SGP_CANDIDATAS)),
        )
        coluna_matricula_sigeduc = col_mat2.selectbox(
            "Coluna de matrícula do aluno (Matrícula SGP, base SIGEDUC)",
            options=df_sigeduc.columns,
            index=df_sigeduc.columns.index(_padrao(df_sigeduc.columns, COLUNAS_MATRICULA_SIGEDUC_CANDIDATAS)),
        )

        col3, col4 = st.columns(2)
        coluna_ofertada = col3.selectbox(
            "Coluna de horas ofertadas no período (base SGP)",
            options=df_sgp.columns,
            index=df_sgp.columns.index(_padrao(df_sgp.columns, COLUNAS_OFERTADA_CANDIDATAS)),
        )
        coluna_presente = col4.selectbox(
            "Coluna de horas de presença no período (base SGP)",
            options=df_sgp.columns,
            index=df_sgp.columns.index(_padrao(df_sgp.columns, COLUNAS_PRESENTE_CANDIDATAS)),
        )

        col5, col6 = st.columns(2)
        coluna_ofertada_sigeduc = col5.selectbox(
            "Coluna de horas ofertadas no período (base SIGEDUC)",
            options=df_sigeduc.columns,
            index=df_sigeduc.columns.index(_padrao(df_sigeduc.columns, COLUNAS_OFERTADA_CANDIDATAS)),
        )
        coluna_presente_sigeduc = col6.selectbox(
            "Coluna de horas de presença no período (base SIGEDUC)",
            options=df_sigeduc.columns,
            index=df_sigeduc.columns.index(_padrao(df_sigeduc.columns, COLUNAS_PRESENTE_CANDIDATAS)),
        )

        if not st.button("▶️ Calcular Frequência", type="primary"):
            return

        try:
            df_atualizado, df_alterados, relatorio = calcular_frequencia(
                df_sgp,
                df_sigeduc,
                coluna_cpf_sgp,
                coluna_cpf_sigeduc,
                coluna_ofertada,
                coluna_presente,
                coluna_ofertada_sigeduc,
                coluna_presente_sigeduc,
                coluna_matricula_sgp,
                coluna_matricula_sigeduc,
            )
        except KeyError as exc:
            st.error(str(exc))
            return

        st.success("Frequência calculada.")

        m1, m2, m3, m4 = st.columns(4)
        m1.metric("SGP", relatorio.total_sgp)
        m2.metric("SIGEDUC (após limpeza)", relatorio.total_sigeduc_apos_limpeza)
        m3.metric("Com correspondência", relatorio.total_com_correspondencia_sigeduc)
        m4.metric("Sem correspondência", relatorio.total_sem_correspondencia_sigeduc)

        m5, m6, m7, m8, m9 = st.columns(5)
        m5.metric("Alterados", relatorio.total_alterados)
        m6.metric("Subiu", relatorio.total_subiu)
        m7.metric("Desceu < 80%", relatorio.total_desceu_abaixo_80)
        m8.metric("Desceu ≥ 80%", relatorio.total_desceu_acima_80)
        m9.metric("Manteve %", relatorio.total_manteve_percentual)

        if relatorio.total_sem_correspondencia_sigeduc:
            st.caption(
                f"ℹ️ {relatorio.total_sem_correspondencia_sigeduc} aluno(s)/matrícula(s) do SGP não "
                "encontraram correspondência (por CPF + matrícula) na base SIGEDUC enviada — a "
                "frequência desses alunos foi mantida sem alteração."
            )
        if relatorio.total_desceu_abaixo_80:
            st.warning(
                f"⚠️ {relatorio.total_desceu_abaixo_80} aluno(s) tiveram o percentual de presença "
                "reduzido para abaixo de 80% — vale conferir antes de reenviar."
            )

        aba_atualizada, aba_alterados = st.tabs(
            [f"📋 Frequência Atualizada ({df_atualizado.height})", f"🔎 Alterados ({df_alterados.height})"]
        )
        with aba_atualizada:
            st.caption("Base do SGP inteira, com a frequência já corrigida — pronta para reenvio.")
            st.dataframe(df_atualizado.head(50), width="stretch")
            botoes_download(df_atualizado, nome_base="frequencia_atualizada", key_prefix="freq_atual")
        with aba_alterados:
            st.caption("Somente quem teve o valor alterado, com o valor anterior para conferência.")
            st.dataframe(df_alterados.head(50), width="stretch")
            botoes_download(df_alterados, nome_base="frequencia_alterados", key_prefix="freq_alt")
        return

    # --- 🆕 Gerar frequência nova (a partir do SIGEDUC) ---
    st.markdown(
        """
Monta a frequência **do zero** a partir da base bruta do SIGEDUC — sem
precisar de nenhuma frequência já lançada no SGP. `ESTUDANTE_ETAPA_DE_ENSINO`
é calculada como no **Módulo B** (Oferta de Ensino + Série). Opcionalmente,
cruza por CPF com planilhas à parte de **Terços** e de **ID_SGP** para
preencher `ESTUDANTE_ANO_PERIODO`/`ID_SGP_MATRICULA`. Reproduz o script
legado `FREQUENCIA_2026`, em `src/core/frequencia.py`.
"""
    )

    arquivo_sigeduc_novo = st.file_uploader(
        "Base SIGEDUC (bruta)", type=["csv", "xlsx", "xls"], key="upload_freq_novo_sigeduc"
    )

    col_aux1, col_aux2 = st.columns(2)
    arquivo_tercos_novo = col_aux1.file_uploader(
        "Planilha de Terços (SIGEDUC) — opcional",
        type=["csv", "xlsx", "xls"],
        key="upload_freq_novo_tercos",
        help="Só CPF + ano/período (terço) do EJA -- cruzada por CPF só para preencher "
        "ESTUDANTE_ANO_PERIODO; sem ela, essa coluna sai em branco.",
    )
    arquivo_id_sgp_novo = col_aux2.file_uploader(
        "Planilha de ID_SGP — opcional",
        type=["csv", "xlsx", "xls"],
        key="upload_freq_novo_id_sgp",
        help="Só CPF + ID_SGP_MATRICULA -- cruzada por CPF só para preencher "
        "ID_SGP_MATRICULA; sem ela, essa coluna sai em branco.",
    )

    if arquivo_sigeduc_novo is None:
        st.caption("Envie a planilha do SIGEDUC para continuar.")
        return

    try:
        df_sigeduc_novo = carregar_planilha(arquivo_sigeduc_novo)
        df_tercos_novo = carregar_planilha(arquivo_tercos_novo) if arquivo_tercos_novo is not None else None
        df_id_sgp_novo = carregar_planilha(arquivo_id_sgp_novo) if arquivo_id_sgp_novo is not None else None
    except Exception as exc:  # noqa: BLE001
        st.error(f"Não foi possível ler um dos arquivos: {exc}")
        return

    st.write(f"SIGEDUC: **{df_sigeduc_novo.height} linhas** × **{df_sigeduc_novo.width} colunas**.")

    col1, col2 = st.columns(2)
    coluna_cpf_novo = col1.selectbox(
        "Coluna de CPF",
        options=df_sigeduc_novo.columns,
        index=df_sigeduc_novo.columns.index(_padrao(df_sigeduc_novo.columns, COLUNAS_CPF_SIGEDUC_CANDIDATAS)),
        key="freq_novo_cpf",
    )
    coluna_entidade_novo = col2.selectbox(
        "Coluna do código da entidade/escola",
        options=df_sigeduc_novo.columns,
        index=df_sigeduc_novo.columns.index(_padrao(df_sigeduc_novo.columns, COLUNAS_ENTIDADE_SIGEDUC_CANDIDATAS)),
        key="freq_novo_entidade",
    )

    col3, col4 = st.columns(2)
    coluna_oferta_novo = col3.selectbox(
        "Coluna de Oferta de Ensino",
        options=df_sigeduc_novo.columns,
        index=df_sigeduc_novo.columns.index(_padrao(df_sigeduc_novo.columns, COLUNAS_OFERTA_CANDIDATAS)),
        key="freq_novo_oferta",
    )
    coluna_serie_novo = col4.selectbox(
        "Coluna de Série",
        options=df_sigeduc_novo.columns,
        index=df_sigeduc_novo.columns.index(_padrao(df_sigeduc_novo.columns, COLUNAS_SERIE_CANDIDATAS)),
        key="freq_novo_serie",
    )

    col5, col6, col7 = st.columns(3)
    coluna_mes_novo = col5.selectbox(
        "Coluna de Mês de Referência",
        options=df_sigeduc_novo.columns,
        index=df_sigeduc_novo.columns.index(_padrao(df_sigeduc_novo.columns, COLUNAS_MES_REFERENCIA_CANDIDATAS)),
        key="freq_novo_mes",
    )
    coluna_hora_dada_novo = col6.selectbox(
        "Coluna de Horas de Aula Dada",
        options=df_sigeduc_novo.columns,
        index=df_sigeduc_novo.columns.index(_padrao(df_sigeduc_novo.columns, COLUNA_HORA_AULA_DADA)),
        key="freq_novo_hora_dada",
    )
    coluna_faltas_novo = col7.selectbox(
        "Coluna de Faltas do Aluno (horas)",
        options=df_sigeduc_novo.columns,
        index=df_sigeduc_novo.columns.index(_padrao(df_sigeduc_novo.columns, COLUNA_FALTAS_ALUNO_HORA)),
        key="freq_novo_faltas",
    )

    ano_novo = st.number_input(
        "Ano (NU_ANO_MATRICULA / ANO_REFERENCIA)",
        min_value=2000,
        max_value=2100,
        value=datetime.date.today().year,
        step=1,
        key="freq_novo_ano",
    )

    if df_tercos_novo is not None:
        st.caption("Terços (opcional):")
        col_t1, col_t2 = st.columns(2)
        coluna_cpf_terco_novo = col_t1.selectbox(
            "Coluna de CPF (Terços)",
            options=df_tercos_novo.columns,
            index=df_tercos_novo.columns.index(
                _padrao(df_tercos_novo.columns, COLUNAS_CPF_TERCO_FREQ_CANDIDATAS)
            ),
            key="freq_novo_cpf_terco",
        )
        coluna_ano_periodo_terco_novo = col_t2.selectbox(
            "Coluna de Ano/Período (Terços)",
            options=df_tercos_novo.columns,
            index=df_tercos_novo.columns.index(
                _padrao(df_tercos_novo.columns, COLUNAS_ANO_PERIODO_TERCO_FREQ_CANDIDATAS)
            ),
            key="freq_novo_ano_periodo_terco",
        )
    else:
        coluna_cpf_terco_novo = None
        coluna_ano_periodo_terco_novo = None

    if df_id_sgp_novo is not None:
        st.caption("ID_SGP (opcional):")
        col_i1, col_i2 = st.columns(2)
        coluna_cpf_id_sgp_novo = col_i1.selectbox(
            "Coluna de CPF (ID_SGP)",
            options=df_id_sgp_novo.columns,
            index=df_id_sgp_novo.columns.index(_padrao(df_id_sgp_novo.columns, COLUNAS_CPF_ID_SGP_CANDIDATAS)),
            key="freq_novo_cpf_id_sgp",
        )
        coluna_id_sgp_matricula_novo = col_i2.selectbox(
            "Coluna de ID_SGP_MATRICULA",
            options=df_id_sgp_novo.columns,
            index=df_id_sgp_novo.columns.index(
                _padrao(df_id_sgp_novo.columns, COLUNAS_ID_SGP_MATRICULA_CANDIDATAS)
            ),
            key="freq_novo_id_sgp_matricula",
        )
    else:
        coluna_cpf_id_sgp_novo = None
        coluna_id_sgp_matricula_novo = None

    if not st.button("▶️ Gerar Frequência Nova", type="primary"):
        return

    try:
        df_frequencia_nova, relatorio_novo = gerar_frequencia_do_sigeduc(
            df_sigeduc_novo,
            ano=int(ano_novo),
            coluna_cpf_sigeduc=coluna_cpf_novo,
            coluna_entidade=coluna_entidade_novo,
            coluna_oferta=coluna_oferta_novo,
            coluna_serie=coluna_serie_novo,
            coluna_mes_referencia=coluna_mes_novo,
            coluna_hora_aula_dada=coluna_hora_dada_novo,
            coluna_faltas_aluno_hora=coluna_faltas_novo,
            df_tercos=df_tercos_novo,
            coluna_cpf_terco=coluna_cpf_terco_novo,
            coluna_ano_periodo_terco=coluna_ano_periodo_terco_novo,
            df_id_sgp=df_id_sgp_novo,
            coluna_cpf_id_sgp=coluna_cpf_id_sgp_novo,
            coluna_id_sgp_matricula=coluna_id_sgp_matricula_novo,
        )
    except KeyError as exc:
        st.error(str(exc))
        return

    st.success("Frequência nova gerada.")

    m1, m2, m3 = st.columns(3)
    m1.metric("SIGEDUC (enviado)", relatorio_novo.total_sigeduc)
    m2.metric("Frequência gerada", relatorio_novo.total_sigeduc_apos_limpeza)
    m3.metric("Etapa não mapeada", relatorio_novo.total_etapa_nao_mapeada)

    if relatorio_novo.total_etapa_nao_mapeada:
        st.warning(
            f"⚠️ {relatorio_novo.total_etapa_nao_mapeada} registro(s) ficaram com "
            "ESTUDANTE_ETAPA_DE_ENSINO = 'NAO_MAPEADO' — vale conferir Oferta de Ensino / "
            "Série desses casos."
        )
    if relatorio_novo.coluna_cpf_terco is not None and relatorio_novo.total_sem_terco:
        st.caption(
            f"ℹ️ {relatorio_novo.total_sem_terco} de {relatorio_novo.total_sigeduc_apos_limpeza} "
            "CPF(s) não têm terço correspondente na planilha de Terços enviada — "
            "ESTUDANTE_ANO_PERIODO ficou em branco para eles."
        )
    if relatorio_novo.coluna_cpf_id_sgp is not None and relatorio_novo.total_sem_id_sgp:
        st.caption(
            f"ℹ️ {relatorio_novo.total_sem_id_sgp} de {relatorio_novo.total_sigeduc_apos_limpeza} "
            "CPF(s) não têm ID_SGP correspondente na planilha enviada — ID_SGP_MATRICULA "
            "ficou em branco para eles."
        )

    st.write("**Pré-visualização do resultado:**")
    st.dataframe(df_frequencia_nova.head(50), width="stretch")
    botoes_download(df_frequencia_nova, nome_base="frequencia_nova", key_prefix="freq_novo")


def pagina_exportacao() -> None:
    st.title("📤 E. Exportação Padrão MEC")
    st.markdown(
        f"""
Recebe qualquer planilha (idealmente já processada pelos módulos A-D) e
força o **layout oficial de {len(ORDEM_PADRAO_MEC)} colunas** usado para
subir alunos no SGP:

- Colunas do modelo que **não existirem** na planilha são criadas vazias.
- Colunas da planilha que **não fazem parte** do modelo são descartadas.
- O resultado sai sempre na ordem exata do modelo.

Implementado em `src/core/exportacao.py`, a partir da lista de colunas
confirmada no script legado `SCRIPT_PE_DE_MEIA`.
"""
    )
    with st.expander(f"Ver as {len(ORDEM_PADRAO_MEC)} colunas do modelo, na ordem oficial"):
        st.dataframe(
            pl.DataFrame({"#": range(1, len(ORDEM_PADRAO_MEC) + 1), "COLUNA": list(ORDEM_PADRAO_MEC)}),
            width="stretch",
            hide_index=True,
        )

    arquivo = st.file_uploader(
        "Planilha a padronizar (.csv ou .xlsx)", type=["csv", "xlsx", "xls"], key="upload_exportacao"
    )
    if arquivo is None:
        st.caption("Nenhum arquivo enviado ainda.")
        return

    try:
        df = carregar_planilha(arquivo)
    except Exception as exc:  # noqa: BLE001
        st.error(f"Não foi possível ler o arquivo: {exc}")
        return

    st.write(f"Arquivo lido: **{df.height} linhas** × **{df.width} colunas**.")

    if not st.button("▶️ Aplicar Layout Padrão MEC", type="primary"):
        return

    df_final, relatorio = exportar_padrao_mec(df)

    st.success("Layout aplicado.")

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Linhas", relatorio.total_linhas)
    m2.metric("Colunas do modelo", relatorio.total_colunas_modelo)
    m3.metric("Criadas vazias", len(relatorio.colunas_criadas_vazias))
    m4.metric("Descartadas", len(relatorio.colunas_descartadas))

    if relatorio.colunas_criadas_vazias:
        with st.expander(f"⚠️ {len(relatorio.colunas_criadas_vazias)} coluna(s) criada(s) vazia(s) — provavelmente precisam ser preenchidas manualmente"):
            st.write(", ".join(relatorio.colunas_criadas_vazias))
    if relatorio.colunas_descartadas:
        with st.expander(f"ℹ️ {len(relatorio.colunas_descartadas)} coluna(s) da planilha enviada não fazem parte do modelo e foram descartadas"):
            st.write(", ".join(relatorio.colunas_descartadas))

    st.write("**Pré-visualização do resultado:**")
    st.dataframe(df_final.head(50), width="stretch")

    botoes_download(df_final, nome_base="padrao_mec", key_prefix="export")


def pagina_situacao_aluno() -> None:
    st.title("✅ F. Situação de Matrícula e Aprovações")
    st.markdown(
        """
Cruza SIGEDUC e destino pelo **CPF e pela data de matrícula**. A situação,
o período e as datas principais vêm da matrícula correspondente. Se houver
outra matrícula posterior do mesmo aluno, sua data e situação aparecem em
colunas adicionais, sem substituir a situação principal. `QTD_MODULOS_APROVADOS`
continua contando as aprovações de todo o histórico do CPF.

Um CPF do destino que não aparece no SIGEDUC enviado mantém os valores que
já tinha (menos `QTD_MODULOS_APROVADOS`, que sempre reflete só a rodada
atual — nenhuma linha do destino é descartada por falta de
correspondência). Duas checagens opcionais abaixo replicam as duas
variações mais recentes do script legado `GERANDO_APROVADOS`. Implementado
com Polars + DuckDB em `src/core/situacao_aluno.py`.
"""
    )

    col_up1, col_up2 = st.columns(2)
    arquivo_sigeduc = col_up1.file_uploader(
        "Base SIGEDUC (situação/aprovação bruta)", type=["csv", "xlsx", "xls"], key="upload_sit_sigeduc"
    )
    arquivo_destino = col_up2.file_uploader(
        "Base de destino (Pé de Meia/SGP a atualizar)", type=["csv", "xlsx", "xls"], key="upload_sit_destino"
    )
    if arquivo_sigeduc is None or arquivo_destino is None:
        st.caption("Envie as duas planilhas (SIGEDUC e destino) para continuar.")
        return

    try:
        df_sigeduc = carregar_planilha(arquivo_sigeduc)
        df_destino = carregar_planilha(arquivo_destino)
    except Exception as exc:  # noqa: BLE001
        st.error(f"Não foi possível ler um dos arquivos: {exc}")
        return

    st.write(
        f"SIGEDUC: **{df_sigeduc.height} linhas** × **{df_sigeduc.width} colunas** · "
        f"Destino: **{df_destino.height} linhas** × **{df_destino.width} colunas**."
    )

    def _padrao(colunas: list[str], candidatos: tuple[str, ...]) -> str:
        return next((c for c in candidatos if c in colunas), colunas[0])

    col1, col2 = st.columns(2)
    coluna_cpf_sigeduc = col1.selectbox(
        "Coluna de CPF (base SIGEDUC)",
        options=df_sigeduc.columns,
        index=df_sigeduc.columns.index(_padrao(df_sigeduc.columns, COLUNAS_CPF_SIGEDUC_SITUACAO_CANDIDATAS)),
    )
    coluna_cpf_destino = col2.selectbox(
        "Coluna de CPF (base destino)",
        options=df_destino.columns,
        index=df_destino.columns.index(_padrao(df_destino.columns, COLUNAS_CPF_DESTINO_CANDIDATAS)),
    )

    col_mat1, col_mat2 = st.columns(2)
    coluna_data_matricula_sigeduc = col_mat1.selectbox(
        "Coluna de Data de Matrícula (SIGEDUC)",
        options=df_sigeduc.columns,
        index=df_sigeduc.columns.index(_padrao(df_sigeduc.columns, COLUNAS_DATA_MATRICULA_SIGEDUC_CANDIDATAS)),
    )
    coluna_data_matricula_destino = col_mat2.selectbox(
        "Coluna de Data de Matrícula (destino)",
        options=df_destino.columns,
        index=df_destino.columns.index(_padrao(df_destino.columns, COLUNAS_DATA_MATRICULA_DESTINO_CANDIDATAS)),
    )

    col3, col4, col5 = st.columns(3)
    coluna_data_fim = col3.selectbox(
        "Coluna de Data Fim (SIGEDUC)",
        options=df_sigeduc.columns,
        index=df_sigeduc.columns.index(_padrao(df_sigeduc.columns, COLUNAS_DATA_FIM_CANDIDATAS)),
    )
    coluna_data_encerramento = col4.selectbox(
        "Coluna de Data Encerramento (SIGEDUC)",
        options=df_sigeduc.columns,
        index=df_sigeduc.columns.index(_padrao(df_sigeduc.columns, COLUNAS_DATA_ENCERRAMENTO_CANDIDATAS)),
        help="Se o SIGEDUC tiver registros repetidos do mesmo CPF e data de matrícula, "
        "esta data decide qual registro dessa matrícula usar.",
    )
    coluna_periodo = col5.selectbox(
        "Coluna de Período (SIGEDUC)",
        options=df_sigeduc.columns,
        index=df_sigeduc.columns.index(_padrao(df_sigeduc.columns, COLUNAS_PERIODO_CANDIDATAS)),
    )

    col6, col7 = st.columns(2)
    coluna_situacao = col6.selectbox(
        "Coluna de Situação Matrícula (SIGEDUC)",
        options=df_sigeduc.columns,
        index=df_sigeduc.columns.index(_padrao(df_sigeduc.columns, COLUNAS_SITUACAO_CANDIDATAS)),
    )
    status_aprovado = col7.text_input(
        "Texto que identifica 'módulo aprovado' (não diferencia maiúsculas/minúsculas)",
        value=STATUS_APROVADO_PADRAO,
        help="Conta, para cada CPF, quantos registros do SIGEDUC têm a situação "
        "contendo este texto — não só a situação mais recente.",
    )

    st.divider()
    st.write("**Checagens opcionais** (replicam as duas variações mais recentes do script legado):")

    col_op1, col_op2 = st.columns(2)
    aplicar_transferencia_externa = col_op1.checkbox(
        "Reclassificar 'Transferência Externa'",
        value=True,
        help="Um CPF cuja situação da matrícula correspondente é 'TRANSFERIDO' e que nunca teve "
        "nenhuma outra situação no histórico do SIGEDUC enviado é reclassificado "
        "para 'TRANSFERÊNCIA EXTERNA' — ele nunca chegou a se matricular de fato "
        "aqui, só existe por já ter vindo transferido de outra rede/escola.",
    )
    aplicar_checagem_censo = col_op2.checkbox(
        "Checar divergência de Censo × CO_ENTIDADE",
        value=False,
        help="Compara o Censo/INEP da matrícula correspondente no SIGEDUC com o "
        "CO_ENTIDADE do destino; quem divergir é removido da base principal e "
        "devolvido à parte, para conferência manual.",
    )

    coluna_censo: str | None = None
    coluna_co_entidade: str | None = None
    if aplicar_checagem_censo:
        col_censo1, col_censo2 = st.columns(2)
        coluna_censo = col_censo1.selectbox(
            "Coluna de Censo/INEP (SIGEDUC)",
            options=df_sigeduc.columns,
            index=df_sigeduc.columns.index(_padrao(df_sigeduc.columns, COLUNAS_CENSO_CANDIDATAS)),
        )
        coluna_co_entidade = col_censo2.selectbox(
            "Coluna de CO_ENTIDADE (destino)",
            options=df_destino.columns,
            index=df_destino.columns.index(_padrao(df_destino.columns, COLUNAS_CO_ENTIDADE_CANDIDATAS)),
        )

    if not st.button("▶️ Calcular Situação do Aluno", type="primary"):
        return

    try:
        df_atualizado, df_divergentes, relatorio = calcular_situacao_aluno(
            df_sigeduc,
            df_destino,
            coluna_cpf_sigeduc=coluna_cpf_sigeduc,
            coluna_cpf_destino=coluna_cpf_destino,
            coluna_data_matricula_sigeduc=coluna_data_matricula_sigeduc,
            coluna_data_matricula_destino=coluna_data_matricula_destino,
            coluna_data_fim=coluna_data_fim,
            coluna_data_encerramento=coluna_data_encerramento,
            coluna_periodo=coluna_periodo,
            coluna_situacao=coluna_situacao,
            status_aprovado=status_aprovado,
            aplicar_transferencia_externa=aplicar_transferencia_externa,
            aplicar_checagem_censo=aplicar_checagem_censo,
            coluna_censo=coluna_censo,
            coluna_co_entidade=coluna_co_entidade,
        )
    except KeyError as exc:
        st.error(str(exc))
        return

    st.success("Situação do aluno atualizada.")

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("SIGEDUC", relatorio.total_sigeduc)
    m2.metric("Destino", relatorio.total_destino)
    m3.metric("Com correspondência", relatorio.total_com_correspondencia_sigeduc)
    m4.metric("Sem correspondência", relatorio.total_sem_correspondencia_sigeduc)

    m5, m6, m7 = st.columns(3)
    m5.metric("Com aprovação", relatorio.total_com_aprovacao)
    m6.metric("Transferência Externa", relatorio.total_reclassificados_transferencia_externa)
    m7.metric("Divergentes (Censo)", relatorio.total_divergentes_censo)

    if relatorio.total_sem_correspondencia_sigeduc:
        st.caption(
            f"ℹ️ {relatorio.total_sem_correspondencia_sigeduc} linha(s) do destino não tiveram "
            "CPF e data de matrícula correspondentes no SIGEDUC — mantiveram os valores que já tinham "
            "(exceto QTD_MODULOS_APROVADOS, que ficou 0)."
        )
    if relatorio.total_divergentes_censo:
        st.warning(
            f"⚠️ {relatorio.total_divergentes_censo} aluno(s) tiveram Censo (SIGEDUC) "
            "diferente do CO_ENTIDADE (destino) e foram removidos da base principal — "
            "confira a aba 'Divergentes' antes de decidir o que fazer com eles."
        )

    if aplicar_checagem_censo:
        aba_atualizada, aba_divergentes = st.tabs(
            [f"📋 Atualizado ({df_atualizado.height})", f"⚠️ Divergentes Censo ({df_divergentes.height})"]
        )
        with aba_atualizada:
            st.dataframe(df_atualizado.head(50), width="stretch")
            botoes_download(df_atualizado, nome_base="situacao_aluno_atualizado", key_prefix="sit_atual")
        with aba_divergentes:
            st.caption("Alunos removidos da base principal por divergência de Censo × CO_ENTIDADE.")
            st.dataframe(df_divergentes.head(50), width="stretch")
            botoes_download(df_divergentes, nome_base="situacao_aluno_divergentes_censo", key_prefix="sit_div")
    else:
        st.write("**Pré-visualização do resultado:**")
        st.dataframe(df_atualizado.head(50), width="stretch")
        botoes_download(df_atualizado, nome_base="situacao_aluno_atualizado", key_prefix="sit_atual")


def pagina_situacao_sigeduc() -> None:
    st.title("📥 G. Situação de Matrícula (Somente SIGEDUC)")
    st.markdown(
        """
Variação do **Módulo F** para quando você não tem (ou não quer usar) uma
base de destino para cruzar: envie só a planilha **bruta do SIGEDUC** e
recebe, para cada CPF encontrado, a **linha inteira** do registro mais
recente (todas as colunas originais da planilha — nome, escola,
nascimento, o que ela tiver) mais a coluna `QTD_MODULOS_APROVADOS`.

Neste módulo, "mais recente" prioriza a `DATA ENCERRAMENTO` mais nova;
sem nenhuma data válida em qualquer registro do CPF, mantém o último
registro na ordem original da planilha.

Diferenças em relação ao Módulo F: o resultado só traz CPFs que **existem**
no SIGEDUC enviado (não há uma base de destino para preservar quem não
tiver correspondência), e não tem a checagem de divergência de Censo ×
CO_ENTIDADE (que precisa de uma base de destino para comparar — use o
Módulo F se precisar dela). A reclassificação de **Transferência Externa**
continua disponível, porque não depende de destino nenhum. Implementado em
`src/core/situacao_sigeduc.py`, reaproveitando as mesmas funções do
Módulo F.
"""
    )

    arquivo_sigeduc = st.file_uploader(
        "Base SIGEDUC (situação/aprovação bruta)", type=["csv", "xlsx", "xls"], key="upload_sitsig_sigeduc"
    )
    if arquivo_sigeduc is None:
        st.caption("Nenhum arquivo enviado ainda.")
        return

    try:
        df_sigeduc = carregar_planilha(arquivo_sigeduc)
    except Exception as exc:  # noqa: BLE001
        st.error(f"Não foi possível ler o arquivo: {exc}")
        return

    st.write(f"Arquivo lido: **{df_sigeduc.height} linhas** × **{df_sigeduc.width} colunas**.")

    def _padrao(colunas: list[str], candidatos: tuple[str, ...]) -> str:
        return next((c for c in candidatos if c in colunas), colunas[0])

    col1, col2, col3 = st.columns(3)
    coluna_cpf = col1.selectbox(
        "Coluna de CPF (base SIGEDUC)",
        options=df_sigeduc.columns,
        index=df_sigeduc.columns.index(_padrao(df_sigeduc.columns, COLUNAS_CPF_SIGEDUC_SITUACAO_CANDIDATAS)),
    )
    coluna_data_fim = col2.selectbox(
        "Coluna de Data Fim",
        options=df_sigeduc.columns,
        index=df_sigeduc.columns.index(_padrao(df_sigeduc.columns, COLUNAS_DATA_FIM_CANDIDATAS)),
    )
    coluna_data_encerramento = col3.selectbox(
        "Coluna de Data Encerramento",
        options=df_sigeduc.columns,
        index=df_sigeduc.columns.index(_padrao(df_sigeduc.columns, COLUNAS_DATA_ENCERRAMENTO_CANDIDATAS)),
        help="Decide qual registro é a 'situação mais recente' de cada CPF.",
    )

    col4, col5 = st.columns(2)
    coluna_periodo = col4.selectbox(
        "Coluna de Período",
        options=df_sigeduc.columns,
        index=df_sigeduc.columns.index(_padrao(df_sigeduc.columns, COLUNAS_PERIODO_CANDIDATAS)),
    )
    coluna_situacao = col5.selectbox(
        "Coluna de Situação Matrícula",
        options=df_sigeduc.columns,
        index=df_sigeduc.columns.index(_padrao(df_sigeduc.columns, COLUNAS_SITUACAO_CANDIDATAS)),
    )

    status_aprovado = st.text_input(
        "Texto que identifica 'módulo aprovado' (não diferencia maiúsculas/minúsculas)",
        value=STATUS_APROVADO_PADRAO,
        help="Conta, para cada CPF, quantos registros do SIGEDUC têm a situação "
        "contendo este texto — não só a situação mais recente.",
    )
    aplicar_transferencia_externa = st.checkbox(
        "Reclassificar 'Transferência Externa'",
        value=True,
        help="Um CPF cuja situação mais recente é 'TRANSFERIDO' e que nunca teve "
        "nenhuma outra situação no histórico do SIGEDUC enviado é reclassificado "
        "para 'TRANSFERÊNCIA EXTERNA' — ele nunca chegou a se matricular de fato "
        "aqui, só existe por já ter vindo transferido de outra rede/escola.",
    )

    if not st.button("▶️ Gerar Situação do SIGEDUC", type="primary"):
        return

    try:
        df_resultado, relatorio = resumir_situacao_sigeduc(
            df_sigeduc,
            coluna_cpf=coluna_cpf,
            coluna_data_fim=coluna_data_fim,
            coluna_data_encerramento=coluna_data_encerramento,
            coluna_periodo=coluna_periodo,
            coluna_situacao=coluna_situacao,
            status_aprovado=status_aprovado,
            aplicar_transferencia_externa=aplicar_transferencia_externa,
        )
    except KeyError as exc:
        st.error(str(exc))
        return

    st.success("Situação gerada a partir do SIGEDUC.")

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("SIGEDUC (linhas)", relatorio.total_sigeduc)
    m2.metric("Com CPF válido", relatorio.total_sigeduc_cpf_valido)
    m3.metric("CPFs únicos (resultado)", relatorio.total_cpfs_unicos)
    m4.metric("Com aprovação", relatorio.total_com_aprovacao)

    if relatorio.total_reclassificados_transferencia_externa:
        st.caption(
            f"ℹ️ {relatorio.total_reclassificados_transferencia_externa} CPF(s) "
            "reclassificado(s) para 'TRANSFERÊNCIA EXTERNA'."
        )

    st.write("**Pré-visualização do resultado (1 linha por CPF, a mais recente):**")
    st.dataframe(df_resultado.head(50), width="stretch")

    botoes_download(df_resultado, nome_base="situacao_sigeduc", key_prefix="sitsig")


def pagina_comparacao_situacao() -> None:
    st.title("🔎 H. Comparar Situação SIGEDUC × SGP")
    st.write(
        "Envie as duas planilhas e escolha as colunas de CPF, data da matrícula e situação. "
        "No SIGEDUC, escolha também a série/ano. A comparação usa o CPF e a "
        "mesma data de matrícula nas duas bases e mostra apenas situações diferentes."
    )
    with st.expander("Ver equivalências usadas na comparação"):
        st.table(pl.DataFrame({
            "Texto no SIGEDUC": [
                "TRANSFERIDO", "ABANDONO", "APROVADO / PROGRESSÃO PARCIAL / AVANÇO DE ESTUDOS",
                "APROVADO no 3º ou 4º ano/série", "CONCLUINTE", "REPROVADO / REPR. FREQUÊNCIA",
            ],
            "Código equivalente no SGP": ["2, 3, 4 ou 5", "7", "10", "11", "11", "12"],
        }))
    st.caption(
        "O resultado mantém todas as colunas da planilha SGP na ordem original. "
        "Ao final, acrescenta o CPF normalizado e os dados da comparação."
    )

    esquerda, direita = st.columns(2)
    arquivo_sigeduc = esquerda.file_uploader(
        "Planilha SIGEDUC", type=["csv", "xlsx", "xls"], key="comp_sigeduc"
    )
    arquivo_sgp = direita.file_uploader(
        "Planilha SGP", type=["csv", "xlsx", "xls"], key="comp_sgp"
    )
    if arquivo_sigeduc is None or arquivo_sgp is None:
        st.caption("Envie as duas planilhas para configurar a comparação.")
        return

    try:
        sigeduc = carregar_planilha(arquivo_sigeduc)
        sgp = carregar_planilha(arquivo_sgp)
    except Exception as exc:  # noqa: BLE001
        st.error(f"Não foi possível ler uma das planilhas: {exc}")
        return
    if not sigeduc.columns or not sgp.columns:
        st.error("As duas planilhas precisam ter cabeçalhos de coluna.")
        return

    def selecionar(titulo: str, df: pl.DataFrame, candidatos: tuple[str, ...], chave: str) -> str:
        sugerida = encontrar_coluna(df, candidatos)
        return st.selectbox(
            titulo, df.columns,
            index=df.columns.index(sugerida) if sugerida else 0,
            key=f"comp_{chave}",
        )

    with esquerda:
        st.subheader("SIGEDUC")
        cpf_sig = selecionar("CPF", sigeduc, ("ESTUDANTE_CPF", "CPF", "NU_CPF"), "cpf_sig")
        data_sig = selecionar("Data de matrícula", sigeduc, ("DATA_INICIO_MATRICULA", "DATA MATRICULA", "DATA DE MATRÍCULA"), "data_sig")
        situacao_sig = selecionar("Situação final", sigeduc, ("SITUACAO_MATRICULA", "SITUAÇÃO MATRÍCULA", "SITUACAO_FINAL", "SITUACAO", "SITUAÇÃO"), "situacao_sig")
        serie_sig = selecionar("Série/ano", sigeduc, ("SERIE", "SÉRIE", "ANO_SERIE", "ANO/SÉRIE", "PERIODO"), "serie_sig")
    with direita:
        st.subheader("SGP")
        cpf_sgp = selecionar("CPF", sgp, ("ESTUDANTE_CPF", "CPF", "NU_CPF"), "cpf_sgp")
        data_sgp = selecionar("Data de matrícula", sgp, ("DATA_INICIO_MATRICULA", "DATA MATRICULA", "DATA DE MATRÍCULA"), "data_sgp")
        situacao_sgp = selecionar("Código da situação final", sgp, ("ESTUDANTE_SITUACAO_MATRICULA", "SITUACAO_MATRICULA", "SITUAÇÃO MATRÍCULA", "SITUACAO_FINAL", "CODIGO_SITUACAO", "SITUACAO", "SITUAÇÃO"), "situacao_sgp")

    st.caption(
        "Somente matrículas com CPF e data iguais nas duas planilhas entram na comparação. "
        "Se houver linhas repetidas com o mesmo CPF e data, vale a última linha do arquivo. "
        "Datas vazias ou inválidas ficam fora. "
        "APROVADO no 3º ou 4º ano corresponde ao código 11 no SGP."
    )
    if not st.button("▶️ Comparar situações", type="primary", key="comparar_situacoes"):
        return

    try:
        divergencias, resumo = comparar_situacoes(
            sigeduc, sgp,
            cpf_sigeduc=cpf_sig, data_sigeduc=data_sig,
            situacao_sigeduc=situacao_sig, serie_sigeduc=serie_sig,
            cpf_sgp=cpf_sgp, data_sgp=data_sgp, situacao_sgp=situacao_sgp,
        )
    except (KeyError, ValueError) as exc:
        st.error(str(exc))
        return

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("CPFs no SIGEDUC com data válida", resumo["sigeduc_unicos"])
    col2.metric("CPFs presentes nas duas", resumo["cpfs_em_ambas"])
    col3.metric("Matrículas com mesma data", resumo["matriculas_em_ambas"])
    col4.metric("Situações diferentes", resumo["divergencias"])
    if divergencias.is_empty():
        st.success("Nenhuma divergência encontrada entre as matrículas com CPF e data iguais.")
        return
    st.dataframe(divergencias, width="stretch", hide_index=True)
    st.download_button(
        "⬇️ Baixar divergências em XLSX",
        data=para_bytes_excel(divergencias),
        file_name="divergencias_situacao_sigeduc_sgp.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        key="comp_download_xlsx",
    )


def pagina_comparacao_serie() -> None:
    st.title("📚 I. Comparar Série/Ano SIGEDUC × SGP")
    st.write(
        "Envie as planilhas, escolha as colunas de CPF e série/ano de cada uma "
        "e veja apenas os alunos presentes nas duas bases com séries diferentes."
    )
    esquerda, direita = st.columns(2)
    arquivo_sigeduc = esquerda.file_uploader(
        "Planilha SIGEDUC", type=["csv", "xlsx", "xls"], key="serie_upload_sigeduc"
    )
    arquivo_sgp = direita.file_uploader(
        "Planilha SGP", type=["csv", "xlsx", "xls"], key="serie_upload_sgp"
    )
    if arquivo_sigeduc is None or arquivo_sgp is None:
        st.caption("Envie as duas planilhas para configurar a comparação.")
        return
    try:
        sigeduc = carregar_planilha(arquivo_sigeduc)
        sgp = carregar_planilha(arquivo_sgp)
    except Exception as exc:  # noqa: BLE001
        st.error(f"Não foi possível ler uma das planilhas: {exc}")
        return
    if not sigeduc.columns or not sgp.columns:
        st.error("As duas planilhas precisam ter cabeçalhos de coluna.")
        return

    def selecionar(titulo: str, df: pl.DataFrame, candidatos: tuple[str, ...], chave: str) -> str:
        sugerida = encontrar_coluna(df, candidatos)
        return st.selectbox(
            titulo, options=df.columns,
            index=df.columns.index(sugerida) if sugerida else 0,
            key=f"serie_{chave}",
        )

    with esquerda:
        st.subheader("SIGEDUC")
        cpf_sig = selecionar("Coluna de CPF", sigeduc, ("ESTUDANTE_CPF", "CPF", "NU_CPF"), "cpf_sig")
        serie_sig = selecionar("Coluna de série/ano", sigeduc, ("SÉRIE", "SERIE", "ANO_SERIE", "ANO/SÉRIE"), "serie_sig")
    with direita:
        st.subheader("SGP")
        cpf_sgp = selecionar("Coluna de CPF", sgp, ("ESTUDANTE_CPF", "CPF", "NU_CPF"), "cpf_sgp")
        serie_sgp = selecionar("Coluna de série/ano ou etapa", sgp, ("SÉRIE", "SERIE", "ANO_SERIE", "ANO/SÉRIE", "ESTUDANTE_ETAPA_DE_ENSINO"), "serie_sgp")

    with st.expander("CPF repetido: escolher a matrícula mais recente (opcional)"):
        st.caption(
            "Se você selecionar uma data em cada planilha, vale o registro mais recente "
            "daquele CPF. Sem data, ou em empate, vale a última linha do arquivo."
        )
        col1, col2 = st.columns(2)
        def selecionar_data(container, titulo: str, df: pl.DataFrame, chave: str) -> str | None:
            sugerida = encontrar_coluna(df, COLUNAS_DATA_CANDIDATAS)
            opcoes = [None, *df.columns]
            return container.selectbox(
                titulo, options=opcoes,
                index=opcoes.index(sugerida) if sugerida else 0,
                format_func=lambda valor: "Não usar data" if valor is None else valor,
                key=f"serie_{chave}",
            )
        data_sig = selecionar_data(col1, "Data de matrícula SIGEDUC", sigeduc, "data_sig")
        data_sgp = selecionar_data(col2, "Data de matrícula SGP", sgp, "data_sgp")

    st.caption(
        "A comparação ignora diferenças de escrita como '3ª série' e '3º ano'. "
        "Se a coluna do SGP for ESTUDANTE_ETAPA_DE_ENSINO, os códigos 25–28 e 30–33 "
        "são convertidos para a série correspondente. O código 45 (EJA) não informa série."
    )
    if not st.button("▶️ Comparar séries", type="primary", key="serie_comparar"):
        return
    try:
        divergencias, resumo = comparar_series(
            sigeduc, sgp,
            cpf_sigeduc=cpf_sig, serie_sigeduc=serie_sig,
            cpf_sgp=cpf_sgp, serie_sgp=serie_sgp,
            data_sigeduc=data_sig, data_sgp=data_sgp,
        )
    except (KeyError, ValueError) as exc:
        st.error(str(exc))
        return

    col1, col2, col3 = st.columns(3)
    col1.metric("CPFs únicos no SIGEDUC", resumo["sigeduc_unicos"])
    col2.metric("CPFs presentes nas duas", resumo["cpfs_em_ambas"])
    col3.metric("Séries diferentes", resumo["divergencias"])
    if resumo["sem_serie_comparavel"]:
        st.warning(
            f"{resumo['sem_serie_comparavel']} CPF(s) em ambas as bases não puderam ser "
            "comparados porque a série está vazia ou o SGP usa o código 45 (EJA)."
        )
    if divergencias.is_empty():
        st.success("Nenhuma divergência de série/ano encontrada entre os CPFs comparáveis.")
        return
    st.dataframe(divergencias, width="stretch", hide_index=True)
    st.download_button(
        "⬇️ Baixar divergências em XLSX",
        data=para_bytes_excel(divergencias),
        file_name="divergencias_serie_sigeduc_sgp.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        key="serie_download_xlsx",
    )


def pagina_ajuste_tercos_eja() -> None:
    st.title("🧩 J. Ajuste de Terços EJA")
    st.write(
        "Para alunos registrados no 1º terço que já têm três aprovações em módulo "
        "no SIGEDUC, gera dois resultados: a planilha de dados corretos com as "
        "datas da 1ª e 2ª aprovação e a planilha de cadastro com as datas da 3ª."
    )
    col1, col2, col3 = st.columns(3)
    arquivo_sigeduc = col1.file_uploader(
        "SIGEDUC (histórico de aprovações)", type=["csv", "xlsx", "xls"], key="eja_sigeduc"
    )
    arquivo_corretos = col2.file_uploader(
        "Alunos com dados corretos (1º terço)", type=["csv", "xlsx", "xls"], key="eja_corretos"
    )
    arquivo_cadastro = col3.file_uploader(
        "Cadastro dos alunos", type=["csv", "xlsx", "xls"], key="eja_cadastro"
    )
    if not all((arquivo_sigeduc, arquivo_corretos, arquivo_cadastro)):
        st.caption("Envie as três planilhas para escolher as colunas e gerar os resultados.")
        return
    try:
        sigeduc = carregar_planilha(arquivo_sigeduc)
        corretos = carregar_planilha(arquivo_corretos)
        cadastro = carregar_planilha(arquivo_cadastro)
    except Exception as exc:  # noqa: BLE001
        st.error(f"Não foi possível ler uma das planilhas: {exc}")
        return
    if not all((sigeduc.columns, corretos.columns, cadastro.columns)):
        st.error("As três planilhas precisam ter cabeçalhos de coluna.")
        return

    def selecionar(titulo: str, df: pl.DataFrame, candidatos: tuple[str, ...], chave: str) -> str | None:
        sugerida = encontrar_coluna(df, candidatos)
        return st.selectbox(
            titulo, options=df.columns,
            index=df.columns.index(sugerida) if sugerida else None,
            placeholder="Selecione a coluna",
            key=f"eja_{chave}",
        )

    st.subheader("Colunas do SIGEDUC")
    sig1, sig2, sig3, sig4 = st.columns(4)
    with sig1:
        cpf_sig = selecionar("CPF", sigeduc, ("ESTUDANTE_CPF", "NU CPF", "CPF"), "cpf_sig")
    with sig2:
        matricula_sig = selecionar("Data de matrícula", sigeduc, COLUNAS_DATA_CANDIDATAS, "mat_sig")
    with sig3:
        encerramento_sig = selecionar("Data de encerramento", sigeduc, COLUNAS_ENCERRAMENTO_CANDIDATAS, "fim_sig")
    with sig4:
        situacao_sig = selecionar(
            "Situação da matrícula", sigeduc,
            ("SITUAÇÃO MATRÍCULA", "SITUACAO MATRÍCULA", "SITUACAO_MATRICULA", "SITUACAO"),
            "situacao_sig",
        )

    st.subheader("Alunos com dados corretos")
    corr1, corr2 = st.columns(2)
    with corr1:
        cpf_corretos = selecionar("CPF", corretos, ("ESTUDANTE_CPF", "NU CPF", "CPF"), "cpf_corretos")
    with corr2:
        terco_sugerido = encontrar_coluna(
            corretos, COLUNAS_ANO_PERIODO_TERCO_FREQ_CANDIDATAS
        )
        opcoes_terco = [None, *corretos.columns]
        coluna_terco = st.selectbox(
            "Coluna do terço (opcional se esta planilha já contém só o 1º terço)",
            options=opcoes_terco,
            index=opcoes_terco.index(terco_sugerido) if terco_sugerido else 0,
            format_func=lambda valor: "Planilha já filtrada para 1º terço" if valor is None else valor,
            key="eja_coluna_terco",
        )
    valor_terco = None
    if coluna_terco is not None:
        valores = sorted({
            str(valor).strip() for valor in corretos[coluna_terco].to_list()
            if valor is not None and str(valor).strip()
        })
        if not valores:
            st.error("A coluna de terço selecionada não tem valores preenchidos.")
            return
        indice_primeiro = next(
            (i for i, valor in enumerate(valores)
             if valor.upper().replace("º", "").replace("°", "").startswith("1")), 0
        )
        valor_terco = st.selectbox(
            "Valor que representa o 1º terço", options=valores,
            index=indice_primeiro, key="eja_valor_terco",
        )

    st.subheader("Cadastro")
    cad1, cad2 = st.columns(2)
    with cad1:
        cpf_cadastro = selecionar("CPF", cadastro, ("ESTUDANTE_CPF", "NU CPF", "CPF"), "cpf_cadastro")
    with cad2:
        data_cadastro_sugerida = encontrar_coluna(cadastro, COLUNAS_DATA_CANDIDATAS)
        opcoes_data = [None, *cadastro.columns]
        matricula_cadastro = st.selectbox(
            "Data de matrícula da 3ª aprovação (opcional para conferir a linha do cadastro)",
            options=opcoes_data,
            index=opcoes_data.index(data_cadastro_sugerida) if data_cadastro_sugerida else 0,
            format_func=lambda valor: "Cruzar cadastro somente pelo CPF" if valor is None else valor,
            key="eja_data_cadastro",
        )

    filtrar_ano = st.checkbox(
        "Limitar aprovações ao ano da matrícula", value=False, key="eja_filtrar_ano",
        help="Útil se o histórico do SIGEDUC inclui aprovações de anos anteriores.",
    )
    ano = st.number_input(
        "Ano da matrícula", min_value=2000, max_value=2100,
        value=datetime.date.today().year, step=1, key="eja_ano",
    ) if filtrar_ano else None
    st.caption(
        "As aprovações são ordenadas pela data de encerramento. Registros repetidos "
        "com as mesmas datas contam uma vez. Só entram alunos com três aprovações válidas."
    )
    if not st.button("▶️ Gerar ajustes dos terços", type="primary", key="eja_gerar"):
        return
    if None in (cpf_sig, matricula_sig, encerramento_sig, situacao_sig, cpf_corretos, cpf_cadastro):
        st.error("Selecione as colunas de CPF, matrícula, encerramento e situação necessárias.")
        return
    try:
        ajuste, cadastro_final, resumo = preparar_ajuste_tercos_eja(
            sigeduc, corretos, cadastro,
            cpf_sigeduc=cpf_sig,
            matricula_sigeduc=matricula_sig,
            encerramento_sigeduc=encerramento_sig,
            situacao_sigeduc=situacao_sig,
            cpf_dados_corretos=cpf_corretos,
            cpf_cadastro=cpf_cadastro,
            coluna_terco_dados=coluna_terco,
            valor_primeiro_terco=valor_terco,
            matricula_cadastro=matricula_cadastro,
            ano_referencia=int(ano) if ano is not None else None,
        )
    except (KeyError, ValueError) as exc:
        st.error(str(exc))
        return

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("CPFs do 1º terço", resumo["cpfs_primeiro_terco"])
    m2.metric("Com três aprovações", resumo["cpfs_com_tres_aprovacoes"])
    m3.metric("Ajustes do 1º terço", resumo["ajustes_primeiro_terco"])
    m4.metric("Cadastros da 3ª aprovação", resumo["cadastros_terceiro_terco"])
    if resumo["aprovacoes_sem_data"]:
        st.warning(
            f"{resumo['aprovacoes_sem_data']} aprovação(ões) sem matrícula ou "
            "encerramento válido ficaram fora da contagem."
        )
    if resumo["sem_cadastro_correspondente"]:
        st.warning(
            f"{resumo['sem_cadastro_correspondente']} aluno(s) do ajuste não têm "
            "linha correspondente no cadastro enviado."
        )
    aba_ajuste, aba_cadastro = st.tabs([
        f"1º terço ({ajuste.height})", f"Cadastro da 3ª aprovação ({cadastro_final.height})"
    ])
    with aba_ajuste:
        st.caption("Colunas originais dos dados corretos + matrícula da 1ª e encerramento da 2ª aprovação.")
        st.dataframe(ajuste, width="stretch", hide_index=True)
        if not ajuste.is_empty():
            botoes_download(ajuste, nome_base="ajuste_primeiro_terco_eja", key_prefix="eja_ajuste")
    with aba_cadastro:
        st.caption("Colunas originais do cadastro + matrícula e encerramento da 3ª aprovação.")
        st.dataframe(cadastro_final, width="stretch", hide_index=True)
        if not cadastro_final.is_empty():
            botoes_download(cadastro_final, nome_base="cadastro_terceiro_terco_eja", key_prefix="eja_cadastro")


def pagina_exclusao_movimentacao() -> None:
    st.title("🧹 K. Retirar Movimentações do SGP")
    st.write(
        "Envie a planilha do SGP e a de movimentação. O resultado mantém todas "
        "as colunas e linhas do SGP, exceto as dos CPFs presentes na movimentação."
    )
    coluna_sgp, coluna_movimentacao = st.columns(2)
    arquivo_sgp = coluna_sgp.file_uploader(
        "Planilha do SGP", type=["csv", "xlsx", "xls"], key="exclusao_sgp"
    )
    arquivo_movimentacao = coluna_movimentacao.file_uploader(
        "Planilha de movimentação", type=["csv", "xlsx", "xls"], key="exclusao_mov"
    )
    if arquivo_sgp is None or arquivo_movimentacao is None:
        st.caption("Envie as duas planilhas para selecionar as colunas de CPF.")
        return
    try:
        sgp = carregar_planilha(arquivo_sgp)
        movimentacao = carregar_planilha(arquivo_movimentacao)
    except Exception as exc:  # noqa: BLE001
        st.error(f"Não foi possível ler uma das planilhas: {exc}")
        return
    if not sgp.columns or not movimentacao.columns:
        st.error("As duas planilhas precisam ter cabeçalhos de coluna.")
        return

    with coluna_sgp:
        sugestao_sgp = encontrar_coluna(sgp, COLUNAS_CPF_SGP_MOV_CANDIDATAS)
        cpf_sgp = st.selectbox(
            "Coluna de CPF no SGP", sgp.columns,
            index=sgp.columns.index(sugestao_sgp) if sugestao_sgp else None,
            placeholder="Selecione a coluna", key="exclusao_cpf_sgp",
        )
    with coluna_movimentacao:
        sugestao_mov = encontrar_coluna(
            movimentacao, COLUNAS_CPF_SIGEDUC_MOV_CANDIDATAS
        )
        cpf_mov = st.selectbox(
            "Coluna de CPF na movimentação", movimentacao.columns,
            index=movimentacao.columns.index(sugestao_mov) if sugestao_mov else None,
            placeholder="Selecione a coluna", key="exclusao_cpf_mov",
        )
    if not st.button("▶️ Gerar SGP sem movimentações", type="primary", key="exclusao_gerar"):
        return
    if cpf_sgp is None or cpf_mov is None:
        st.error("Selecione a coluna de CPF em cada planilha.")
        return
    try:
        resultado, resumo = excluir_cpfs_movimentacao(
            sgp, movimentacao, cpf_sgp=cpf_sgp, cpf_movimentacao=cpf_mov
        )
    except (KeyError, ValueError) as exc:
        st.error(str(exc))
        return

    m1, m2, m3 = st.columns(3)
    m1.metric("Linhas do SGP", resumo["linhas_sgp"])
    m2.metric("Linhas removidas", resumo["linhas_removidas"])
    m3.metric("Linhas restantes", resumo["linhas_restantes"])
    st.dataframe(resultado, width="stretch", hide_index=True)
    botoes_download(resultado, nome_base="sgp_sem_movimentacoes", key_prefix="exclusao_mov")


def pagina_cadastro_proxima_matricula() -> None:
    st.title("📥 L. Cadastro da Próxima Matrícula")
    st.write(
        "Use o resultado do módulo F e o histórico bruto do SIGEDUC. Para cada "
        "CPF e data de matrícula do resultado F, localiza a primeira matrícula "
        "posterior com situação MATRICULADO no SIGEDUC e gera o cadastro no "
        "mesmo formato do módulo C."
    )
    col_base, col_sig = st.columns(2)
    arquivo_base = col_base.file_uploader(
        "Resultado do módulo F (situação_aluno_atualizado)",
        type=["csv", "xlsx", "xls"], key="proxima_base",
    )
    arquivos_sigeduc = col_sig.file_uploader(
        "Planilha(s) bruta(s) do SIGEDUC", type=["csv", "xlsx", "xls"],
        accept_multiple_files=True, key="proxima_sigeduc",
    )
    if arquivo_base is None or not arquivos_sigeduc:
        st.caption("Envie o resultado do módulo F e ao menos uma planilha do SIGEDUC.")
        return
    try:
        base = carregar_planilha(arquivo_base)
        sigeduc = pl.concat(
            [carregar_planilha(arquivo) for arquivo in arquivos_sigeduc],
            how="diagonal_relaxed",
        )
    except Exception as exc:  # noqa: BLE001
        st.error(f"Não foi possível ler uma das planilhas: {exc}")
        return
    if not base.columns or not sigeduc.columns:
        st.error("As planilhas precisam ter cabeçalhos de coluna.")
        return

    def escolher(rotulo: str, df: pl.DataFrame, candidatos: tuple[str, ...], chave: str) -> str | None:
        sugestao = encontrar_coluna(df, candidatos)
        return st.selectbox(
            rotulo, options=df.columns,
            index=df.columns.index(sugestao) if sugestao else None,
            placeholder="Selecione a coluna", key=f"proxima_{chave}",
        )

    st.subheader("Colunas do resultado F")
    col1, col2 = st.columns(2)
    with col1:
        cpf_base = escolher("CPF", base, COLUNAS_CPF_DESTINO_CANDIDATAS, "cpf_base")
    with col2:
        data_base = escolher(
            "Data de matrícula da matrícula original", base,
            COLUNAS_DATA_MATRICULA_DESTINO_CANDIDATAS, "data_base",
        )

    st.subheader("Colunas do SIGEDUC")
    col3, col4, col5 = st.columns(3)
    with col3:
        cpf_sig = escolher("CPF", sigeduc, COLUNAS_CPF_SIGEDUC_MOV_CANDIDATAS, "cpf_sig")
    with col4:
        data_sig = escolher("Data de matrícula", sigeduc, COLUNAS_DATA_MATRICULA_SIGEDUC_CANDIDATAS, "data_sig")
    with col5:
        censo_sig = escolher("Censo/INEP", sigeduc, COLUNAS_CENSO_SIGEDUC_CANDIDATAS, "censo_sig")
    col6, col7, col8 = st.columns(3)
    with col6:
        situacao_sig = escolher("Situação da matrícula", sigeduc, COLUNAS_SITUACAO_SIGEDUC_CANDIDATAS, "situacao_sig")
    with col7:
        oferta_sig = escolher("Oferta de ensino", sigeduc, COLUNAS_OFERTA_CANDIDATAS, "oferta_sig")
    with col8:
        serie_sig = escolher("Série", sigeduc, COLUNAS_SERIE_CANDIDATAS, "serie_sig")

    col9, col10 = st.columns(2)
    with col9:
        sugestao_encerramento = encontrar_coluna(
            sigeduc, COLUNAS_DATA_ENCERRAMENTO_SIGEDUC_CANDIDATAS
        )
        opcoes_encerramento = [None, *sigeduc.columns]
        encerramento_sig = st.selectbox(
            "Data de encerramento (desempate opcional)", opcoes_encerramento,
            index=opcoes_encerramento.index(sugestao_encerramento) if sugestao_encerramento else 0,
            format_func=lambda valor: "Não usar" if valor is None else valor,
            key="proxima_encerramento_sig",
        )
    with col10:
        sugestao_terco = encontrar_coluna(sigeduc, COLUNAS_ANO_PERIODO_TERCO_CANDIDATAS)
        opcoes_terco = [None, *sigeduc.columns]
        terco_sig = st.selectbox(
            "Terço da nova matrícula (opcional)", opcoes_terco,
            index=opcoes_terco.index(sugestao_terco) if sugestao_terco else 0,
            format_func=lambda valor: "Deixar vazio" if valor is None else valor,
            key="proxima_terco_sig",
        )
    st.caption(
        "A data escolhida é a menor data do SIGEDUC estritamente posterior "
        "à data da linha do módulo F entre as matrículas com situação "
        "MATRICULADO. Matrículas sem data válida ficam fora."
    )
    if not st.button("▶️ Gerar cadastro da próxima matrícula", type="primary", key="proxima_gerar"):
        return
    if any(valor is None for valor in (
        cpf_base, data_base, cpf_sig, data_sig, censo_sig,
        situacao_sig, oferta_sig, serie_sig,
    )):
        st.error("Selecione todas as colunas obrigatórias.")
        return
    try:
        cadastro, resumo = gerar_cadastro_proxima_matricula(
            base, sigeduc,
            cpf_resultado=cpf_base, matricula_resultado=data_base,
            cpf_sigeduc=cpf_sig, matricula_sigeduc=data_sig,
            censo_sigeduc=censo_sig, situacao_sigeduc=situacao_sig,
            oferta_sigeduc=oferta_sig, serie_sigeduc=serie_sig,
            encerramento_sigeduc=encerramento_sig, terco_sigeduc=terco_sig,
        )
    except (KeyError, ValueError) as exc:
        st.error(str(exc))
        return

    m1, m2, m3 = st.columns(3)
    m1.metric("Matrículas válidas na base", resumo["matriculas_base_validas"])
    m2.metric("Cadastros gerados", resumo["cadastros_gerados"])
    m3.metric("Sem matrícula posterior como MATRICULADO", resumo["sem_matricula_posterior"])
    st.dataframe(cadastro, width="stretch", hide_index=True)
    botoes_download(
        cadastro, nome_base="cadastro_proxima_matricula", key_prefix="proxima_cadastro"
    )


def pagina_reconciliacao_eja() -> None:
    st.title("🔄 M. Reconciliação Completa EJA")
    st.write(
        "Compara CPF, data, escola e terço de cada matrícula do EJA Médio. "
        "O histórico completo conta as aprovações para definir o terço; "
        "as correções começam na data escolhida na planilha de foco "
        "(ou na matrícula atual do SGP, sem essa data). Se a data indicada "
        "ainda pertence ao terço anterior, começa na primeira matrícula "
        "do terço que está sendo corrigido. Gera uma matrícula por aluno "
        "e rodada, sempre na data exata do SIGEDUC. Quando a matrícula atual "
        "tem resultado e existe outra matrícula pendente, também prepara "
        "o cadastro da data seguinte."
    )
    c1, c2, c3 = st.columns(3)
    arquivo_atual = c1.file_uploader("SGP atual", type=["xlsx", "xls", "csv"], key="eja_atual")
    arquivo_historico = c2.file_uploader("Histórico SGP 2025 e 2026", type=["xlsx", "xls", "csv"], key="eja_historico")
    arquivo_sig = c3.file_uploader("SIGEDUC bruto 2025 e 2026", type=["xlsx", "xls", "csv"], key="eja_sig")
    arquivo_foco = st.file_uploader(
        "Planilha com CPFs para limitar a análise (opcional; por exemplo, resultado F)",
        type=["xlsx", "xls", "csv"], key="eja_foco"
    )
    if not all((arquivo_atual, arquivo_historico, arquivo_sig)):
        st.caption("Envie as três planilhas para configurar as colunas.")
        return
    try:
        atual = carregar_planilha(arquivo_atual)
        historico = carregar_planilha(arquivo_historico)
        sigeduc = carregar_planilha(arquivo_sig)
        foco = carregar_planilha(arquivo_foco) if arquivo_foco else None
    except Exception as exc:  # noqa: BLE001
        st.error(f"Não foi possível ler as planilhas: {exc}")
        return

    def escolher(df: pl.DataFrame, titulo: str, padrao: str, chave: str) -> str:
        opcoes = df.columns
        return st.selectbox(
            titulo, opcoes, index=opcoes.index(padrao) if padrao in opcoes else 0,
            key=f"eja_{chave}",
        )

    st.subheader("Colunas do SGP atual")
    c = st.columns(3)
    cpf_atual = c[0].selectbox("CPF atual", atual.columns, index=atual.columns.index("ESTUDANTE_CPF") if "ESTUDANTE_CPF" in atual.columns else 0, key="eja_cpf_atual")
    data_atual = escolher(atual, "Data de matrícula atual", "DATA_INICIO_MATRICULA", "data_atual")
    terco_atual = escolher(atual, "Terço atual", "ESTUDANTE_ANO_PERIODO", "terco_atual")
    situacao_atual = escolher(atual, "Código de situação atual", "ESTUDANTE_MATRICULA_SITUACAO", "situacao_atual")
    id_atual = escolher(atual, "ID da matrícula atual", "ID_SGP_MATRICULA", "id_atual")
    escola_atual = escolher(atual, "Código da escola atual", "CO_ENTIDADE", "escola_atual")

    st.subheader("Colunas do histórico SGP")
    cpf_historico = escolher(historico, "CPF histórico", "ESTUDANTE_CPF", "cpf_historico")
    data_historico = escolher(historico, "Data de matrícula histórica", "DATA_INICIO_MATRICULA", "data_historico")
    terco_historico = escolher(historico, "Terço histórico", "ESTUDANTE_ANO_PERIODO", "terco_historico")
    situacao_historico = escolher(historico, "Código de situação histórica", "ESTUDANTE_MATRICULA_SITUACAO", "situacao_historico")
    id_historico = escolher(historico, "ID da matrícula histórica", "ID_SGP_MATRICULA", "id_historico")
    escola_historico = escolher(historico, "Código da escola histórica", "CO_ENTIDADE", "escola_historico")

    st.subheader("Colunas do SIGEDUC")
    cpf_sigeduc = escolher(sigeduc, "CPF SIGEDUC", "NU CPF", "cpf_sigeduc")
    data_sigeduc = escolher(sigeduc, "Data de matrícula SIGEDUC", "DATA MATRÍCULA", "data_sigeduc")
    encerramento_sigeduc = escolher(sigeduc, "Data de encerramento SIGEDUC", "DATA ENCERRAMENTO", "encerramento_sigeduc")
    situacao_sigeduc = escolher(sigeduc, "Situação SIGEDUC", "SITUACAO MATRÍCULA", "situacao_sigeduc")
    censo_sigeduc = escolher(sigeduc, "Censo/INEP da escola SIGEDUC", "CENSO", "censo_sigeduc")
    oferta_sigeduc = escolher(sigeduc, "Oferta de ensino SIGEDUC", "OFERTA DE ENSINO", "oferta_sigeduc")
    serie_sigeduc = escolher(sigeduc, "Série SIGEDUC", "SÉRIE", "serie_sigeduc")
    cpf_foco = escolher(foco, "CPF da planilha de foco", "ESTUDANTE_CPF", "cpf_foco") if foco is not None else None
    data_foco = None
    terco_foco = None
    corrigir_pendentes_foco = False
    situacao_conclusao_foco = None
    data_conclusao_foco = None
    if foco is not None:
        opcoes_data_foco = [None, *foco.columns]
        padrao_data_foco = "DATA_INICIO_MATRICULA" if "DATA_INICIO_MATRICULA" in foco.columns else None
        data_foco = st.selectbox(
            "Data da matrícula a corrigir (planilha de foco, opcional)",
            opcoes_data_foco, index=opcoes_data_foco.index(padrao_data_foco),
            format_func=lambda valor: "Não usar" if valor is None else valor,
            key="eja_data_foco",
        )
        opcoes_terco_foco = [None, *foco.columns]
        padrao_terco_foco = "ESTUDANTE_ANO_PERIODO" if "ESTUDANTE_ANO_PERIODO" in foco.columns else None
        terco_foco = st.selectbox(
            "Terço da matrícula a corrigir (planilha de foco, opcional)",
            opcoes_terco_foco, index=opcoes_terco_foco.index(padrao_terco_foco),
            format_func=lambda valor: "Não usar" if valor is None else valor,
            key="eja_terco_foco",
        )
        nome_foco_eja = getattr(arquivo_foco, "name", "")
        if (st.session_state.get("eja_foco_nome_anterior") != nome_foco_eja
                or "eja_corrigir_pendentes_foco" not in st.session_state):
            st.session_state["eja_corrigir_pendentes_foco"] = (
                nome_foco_eja.casefold() == "aprovados-3terco.xlsx"
            )
            st.session_state["eja_foco_nome_anterior"] = nome_foco_eja
        corrigir_pendentes_foco = st.checkbox(
            "Corrigir as matrículas pendentes desta planilha de foco em ordem",
            key="eja_corrigir_pendentes_foco",
        )
        if corrigir_pendentes_foco:
            situacao_conclusao_foco = escolher(
                foco, "Situação final confirmada no foco",
                "SITUACAO_MATRICULA_RECENTE", "situacao_conclusao_foco"
            )
            data_conclusao_foco = escolher(
                foco, "Data de conclusão confirmada no foco",
                "DATA_CONCLUSAO_ENSINO_MEDIO", "data_conclusao_foco"
            )
            st.caption(
                "Quando existe matrícula posterior, corrige primeiro a matrícula atual "
                "no terço calculado pelo SIGEDUC. Se a matrícula atual já está correta "
                "e só falta o resultado, o cadastro da data seguinte sai nesta rodada. "
                "As demais datas entram em outras rodadas. "
                "Sem aprovação posterior, o foco pode confirmar a conclusão por uma "
                "aprovação anterior com a mesma data de encerramento."
            )

    st.caption(
        "Código 1 derruba a matrícula incorreta. Resultados confirmados: "
        "10 aprovado, 11 concluinte na quarta aprovação, 12 reprovado, "
        "7 abandono e texto cancelado. Transferências sem código definido vão para conferência. "
        "A data de encerramento preenche DATA_FIM_OU_APROVACAO. "
        "O terço da planilha de foco identifica qual matrícula corrigir; "
        "o terço correto é calculado pelas aprovações do SIGEDUC. "
        "Se a última matrícula do SIGEDUC está aprovada, tem a mesma data e escola "
        "da matrícula atual do 3º terço no SGP e não existe matrícula posterior, "
        "o módulo informa a conclusão (código 11) sem gerar cadastro."
    )
    if not st.button("▶️ Analisar e gerar próxima ação", type="primary", key="eja_gerar"):
        return
    try:
        diagnostico, movimentacao, cadastro, resumo = reconciliar_eja(
            atual, historico, sigeduc,
            cpf_atual=cpf_atual, data_atual=data_atual, terco_atual=terco_atual,
            situacao_atual=situacao_atual, id_atual=id_atual, escola_atual=escola_atual,
            cpf_historico=cpf_historico, data_historico=data_historico,
            terco_historico=terco_historico, situacao_historico=situacao_historico,
            id_historico=id_historico, escola_historico=escola_historico,
            cpf_sigeduc=cpf_sigeduc, data_sigeduc=data_sigeduc,
            encerramento_sigeduc=encerramento_sigeduc, situacao_sigeduc=situacao_sigeduc,
            censo_sigeduc=censo_sigeduc, oferta_sigeduc=oferta_sigeduc,
            serie_sigeduc=serie_sigeduc, foco=foco, cpf_foco=cpf_foco,
            data_foco=data_foco, terco_foco=terco_foco,
            corrigir_pendentes_foco=corrigir_pendentes_foco,
            situacao_conclusao_foco=situacao_conclusao_foco,
            data_conclusao_foco=data_conclusao_foco,
        )
    except (KeyError, ValueError) as exc:
        st.error(str(exc))
        return
    metrics = st.columns(6)
    metrics[0].metric("Alunos analisados", resumo["alunos_sgp"])
    metrics[1].metric("Movimentações geradas", movimentacao.height)
    metrics[2].metric("Derrubar (código 1)", resumo["derrubar"])
    metrics[3].metric("Informar resultado", resumo["resultados"])
    metrics[4].metric("Cadastros gerados", resumo["cadastros"])
    metrics[5].metric("Linhas para conferir", resumo["conferir"])
    pendencias = st.columns(3)
    pendencias[0].metric("Matrículas SIGEDUC desde o início", resumo["matriculas_no_escopo"])
    pendencias[1].metric("Datas ainda faltando no SGP", resumo["datas_pendentes"])
    pendencias[2].metric("Matrículas SGP sem correspondência", resumo["matriculas_excedentes"])
    st.caption(
        "Matrículas anteriores ao início da correção servem apenas para contar "
        "aprovações. As pendências do período são processadas em ordem de data."
    )
    if movimentacao.height or cadastro.height:
        st.info(
            "Importe primeiro a movimentação desta rodada, incluindo códigos 1 "
            "e resultados da matrícula atual. Depois importe os cadastros gerados. "
            "Baixe novamente o SGP atual e o histórico para obter os novos IDs "
            "e execute o módulo para tratar as matrículas cadastradas e as datas seguintes."
        )
    if resumo["sem_matricula_posterior"]:
        st.warning(
            f"{resumo['sem_matricula_posterior']} aluno(s) não têm matrícula posterior "
            "à data de início da correção no SIGEDUC enviado. Isso não impede "
            "o cadastro ou o resultado da matrícula nessa data."
        )
    if movimentacao.height:
        st.subheader("Movimentação desta rodada para importar no SGP")
        botoes_download(movimentacao, nome_base="movimentacao_eja", key_prefix="eja_mov")
        st.dataframe(movimentacao, width="stretch", hide_index=True)
    if cadastro.height:
        st.subheader("Cadastro da matrícula correta ou posterior")
        botoes_download(cadastro, nome_base="cadastro_eja", key_prefix="eja_cad")
        st.dataframe(cadastro, width="stretch", hide_index=True)
    st.subheader("Histórico e próxima ação")
    if diagnostico.height:
        botoes_download(diagnostico, nome_base="diagnostico_eja", key_prefix="eja_diag")
    st.dataframe(diagnostico, width="stretch", hide_index=True)


def pagina_em_breve(titulo: str) -> None:
    st.title(titulo)
    st.info(
        "Este módulo ainda não foi implementado — construímos o projeto "
        "por partes. Em breve a regra de negócio correspondente aparece aqui.",
        icon="🚧",
    )


PAGINAS_IMPLEMENTADAS = {
    None: pagina_inicio,
    "dedup": pagina_dedup,
    "etapa": pagina_etapa,
    "movimentacao": pagina_movimentacao,
    "frequencia": pagina_frequencia,
    "exportacao": pagina_exportacao,
    "situacao_aluno": pagina_situacao_aluno,
    "situacao_sigeduc": pagina_situacao_sigeduc,
    "comparacao_situacao": pagina_comparacao_situacao,
    "comparacao_serie": pagina_comparacao_serie,
    "ajuste_tercos_eja": pagina_ajuste_tercos_eja,
    "exclusao_movimentacao": pagina_exclusao_movimentacao,
    "cadastro_proxima_matricula": pagina_cadastro_proxima_matricula,
    "reconciliacao_eja": pagina_reconciliacao_eja,
}

with st.sidebar:
    st.title("📊 Escola 10")
    st.caption("Automação de dados — Pé de Meia (SEDUC)")
    pagina = st.radio("Módulo", list(MODULOS_CORE.keys()), index=0)
    st.divider()
    st.caption(
        "Os módulos A-M estão implementados em `src/core`. "
        "Cada um valida as colunas que recebe e mostra um erro claro se "
        "alguma estiver faltando."
    )

chave_pagina = MODULOS_CORE[pagina]
funcao_pagina = PAGINAS_IMPLEMENTADAS.get(chave_pagina)

if funcao_pagina is not None:
    funcao_pagina()
else:
    pagina_em_breve(pagina)
