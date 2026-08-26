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

import duckdb
import polars as pl
import streamlit as st

from src.core.deduplicacao import COLUNAS_DATA_CANDIDATAS, deduplicar_matricula_mais_recente
from src.core.etapa_de_ensino import (
    COLUNAS_OFERTA_CANDIDATAS,
    COLUNAS_SERIE_CANDIDATAS,
    calcular_etapa_de_ensino,
)
from src.core.exportacao import ORDEM_PADRAO_MEC, exportar_padrao_mec
from src.core.frequencia import (
    COLUNAS_CPF_SGP_CANDIDATAS,
    COLUNAS_CPF_SIGEDUC_CANDIDATAS,
    COLUNAS_OFERTADA_CANDIDATAS,
    COLUNAS_PRESENTE_CANDIDATAS,
    calcular_frequencia,
)
from src.core._colunas import encontrar_coluna
from src.core.movimentacao import (
    COLUNAS_CENSO_SIGEDUC_CANDIDATAS,
    COLUNAS_CPF_SGP_CANDIDATAS as COLUNAS_CPF_SGP_MOV_CANDIDATAS,
    COLUNAS_CPF_SIGEDUC_CANDIDATAS as COLUNAS_CPF_SIGEDUC_MOV_CANDIDATAS,
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
    COLUNAS_DATA_FIM_CANDIDATAS,
    COLUNAS_PERIODO_CANDIDATAS,
    COLUNAS_SITUACAO_CANDIDATAS,
    STATUS_APROVADO_PADRAO,
    calcular_situacao_aluno,
)
from src.core.situacao_sigeduc import resumir_situacao_sigeduc
from src.ui.downloads import botoes_download
from src.ui.uploader import carregar_planilha
from src.utils.higienizacao import limpar_cpf, padronizar_textos

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
Recebe uma base do SIGEDUC e mantém **apenas um registro por CPF**: o da
matrícula mais recente. Por baixo dos panos: limpa o CPF, normaliza a
coluna de data escolhida e ordena por CPF + data, mantendo sempre o
registro mais novo (`keep='last'`) — exatamente a regra pedida no
briefing, implementada em `src/core/deduplicacao.py`.
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

    col_cpf_padrao = "ESTUDANTE_CPF" if "ESTUDANTE_CPF" in df.columns else df.columns[0]
    col_data_padrao = next((c for c in COLUNAS_DATA_CANDIDATAS if c in df.columns), df.columns[0])

    col1, col2 = st.columns(2)
    coluna_cpf = col1.selectbox(
        "Coluna de CPF do estudante",
        options=df.columns,
        index=df.columns.index(col_cpf_padrao),
    )
    coluna_data = col2.selectbox(
        "Coluna de data (define a matrícula 'mais recente')",
        options=df.columns,
        index=df.columns.index(col_data_padrao),
        help="O briefing cita 'DATA_INICIO_MATRICULA' ou 'DATA ENCERRAMENTO'; "
        "escolha a que existir na sua planilha.",
    )

    if not st.button("▶️ Deduplicar", type="primary"):
        return

    try:
        df_final, relatorio = deduplicar_matricula_mais_recente(df, coluna_cpf, coluna_data)
    except KeyError as exc:
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
            "com data não reconhecida na coluna escolhida (para não perder o aluno) — "
            "vale conferir manualmente."
        )

    st.write("**Resultado (uma linha por CPF, a mais recente):**")
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
  importar como aluno novo.
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
Cruza a base atual do **SGP** com a base bruta do **SIGEDUC** (pelo CPF) e
devolve a frequência corrigida:

1. A partir do SIGEDUC, calcula `HL_PRESENTE_PERIODO = HORA_AULA_DADA - FALTAS_ALUNO_HORA`.
2. Para cada aluno encontrado nas duas bases, fica sempre com o **maior**
   valor entre o que já estava no SGP e o calculado a partir do SIGEDUC —
   a frequência lançada nunca diminui por causa deste cruzamento. Quem não
   aparece no SIGEDUC mantém o valor do SGP sem alteração.
3. Quem teve o valor alterado é classificado conforme o percentual de
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

    def _padrao(colunas: list[str], candidatos: tuple[str, ...]) -> str:
        return next((c for c in candidatos if c in colunas), colunas[0])

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
    st.caption(
        "A base SIGEDUC precisa ter as colunas `HORA_AULA_DADA` e `FALTAS_ALUNO_HORA` "
        "(nomes fixos, conforme o export padrão do SIGEDUC)."
    )

    if not st.button("▶️ Calcular Frequência", type="primary"):
        return

    try:
        df_atualizado, df_alterados, relatorio = calcular_frequencia(
            df_sgp, df_sigeduc, coluna_cpf_sgp, coluna_cpf_sigeduc, coluna_ofertada, coluna_presente
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
            f"ℹ️ {relatorio.total_sem_correspondencia_sigeduc} aluno(s) do SGP não foram "
            "encontrados na base SIGEDUC enviada — a frequência desses alunos foi mantida "
            "sem alteração."
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
Cruza uma base **bruta do SIGEDUC** (situação de matrícula/aprovação, um
registro por período do aluno) com a base de **destino** (Pé de Meia/SGP a
atualizar), pelo CPF, e preenche 5 colunas a partir da situação **mais
recente** de cada aluno (critério: a `DATA ENCERRAMENTO` mais nova;
sem nenhuma data válida, mantém o último registro da planilha):

- **`DATA_FIM_OU_APROVACAO`** e **`DATA_CONCLUSAO_ENSINO_MEDIO`** — datas do registro mais recente.
- **`PERIODO`** e **`SITUACAO_MATRICULA_RECENTE`** — período e situação do registro mais recente.
- **`QTD_MODULOS_APROVADOS`** — quantos registros *daquele CPF* (não só o mais recente) têm situação de aprovação.

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
        help="Além de virar 'DATA_CONCLUSAO_ENSINO_MEDIO', esta coluna decide qual "
        "registro é a 'situação mais recente' de cada CPF.",
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
        help="Um CPF cuja situação mais recente é 'TRANSFERIDO' e que nunca teve "
        "nenhuma outra situação no histórico do SIGEDUC enviado é reclassificado "
        "para 'TRANSFERÊNCIA EXTERNA' — ele nunca chegou a se matricular de fato "
        "aqui, só existe por já ter vindo transferido de outra rede/escola.",
    )
    aplicar_checagem_censo = col_op2.checkbox(
        "Checar divergência de Censo × CO_ENTIDADE",
        value=False,
        help="Compara o código de Censo/INEP do SIGEDUC com o CO_ENTIDADE do destino "
        "para cada CPF encontrado; quem divergir é removido da base principal e "
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
            f"ℹ️ {relatorio.total_sem_correspondencia_sigeduc} CPF(s) do destino não foram "
            "encontrados na base SIGEDUC enviada — mantiveram os valores que já tinham "
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

"Mais recente" usa o mesmo critério do Módulo F: prioriza a `DATA
ENCERRAMENTO` mais nova; sem nenhuma data válida em nenhum registro do
CPF, mantém o último registro na ordem original da planilha.

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
}

with st.sidebar:
    st.title("📊 Escola 10")
    st.caption("Automação de dados — Pé de Meia (SEDUC)")
    pagina = st.radio("Módulo", list(MODULOS_CORE.keys()), index=0)
    st.divider()
    st.caption(
        "Os 7 módulos do briefing (A-G) estão implementados em `src/core`. "
        "Cada um valida as colunas que recebe e mostra um erro claro se "
        "alguma estiver faltando."
    )

chave_pagina = MODULOS_CORE[pagina]
funcao_pagina = PAGINAS_IMPLEMENTADAS.get(chave_pagina)

if funcao_pagina is not None:
    funcao_pagina()
else:
    pagina_em_breve(pagina)
