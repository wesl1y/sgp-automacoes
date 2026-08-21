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
from src.core.movimentacao import analisar_movimentacao
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
Compara uma base **Antiga** com uma base **Nova** (as duas já passadas
pelos módulos A e B) e classifica cada CPF:

- **`trans. escola`** — CO_ENTIDADE mudou.
- **`trans. mod`** — mesma escola, mas o aluno cruzou entre EJA e Regular/Técnico
  (progressão normal de série, ex. Regular 25→26, **não** conta como transferência).
- **`avanco_terco_eja`** — aluno de EJA cujo terço/período avançou (1→2, 2→3, ou foi para o 4).
- **`novo_cadastro`** — CPF que só existe na base Nova.

Gera dois relatórios: **Movimentação** (quem já estava na base e mudou algo)
e **Cadastro** (quem é novo). Implementado com um `LEFT JOIN` em SQL no
DuckDB, em `src/core/movimentacao.py`.
"""
    )

    col_up1, col_up2 = st.columns(2)
    arquivo_antiga = col_up1.file_uploader(
        "Base Antiga", type=["csv", "xlsx", "xls"], key="upload_mov_antiga"
    )
    arquivo_nova = col_up2.file_uploader(
        "Base Nova", type=["csv", "xlsx", "xls"], key="upload_mov_nova"
    )
    if arquivo_antiga is None or arquivo_nova is None:
        st.caption("Envie as duas planilhas (Antiga e Nova) para continuar.")
        return

    try:
        df_antiga = carregar_planilha(arquivo_antiga)
        df_nova = carregar_planilha(arquivo_nova)
    except Exception as exc:  # noqa: BLE001
        st.error(f"Não foi possível ler um dos arquivos: {exc}")
        return

    st.write(
        f"Antiga: **{df_antiga.height} linhas** × **{df_antiga.width} colunas** · "
        f"Nova: **{df_nova.height} linhas** × **{df_nova.width} colunas**."
    )

    colunas_comuns = [c for c in df_nova.columns if c in df_antiga.columns]
    if not colunas_comuns:
        st.error("As duas planilhas não têm nenhuma coluna em comum — verifique os arquivos enviados.")
        return

    def _padrao(candidatos: tuple[str, ...]) -> str:
        return next((c for c in candidatos if c in colunas_comuns), colunas_comuns[0])

    col1, col2, col3 = st.columns(3)
    coluna_cpf = col1.selectbox(
        "Coluna de CPF", options=colunas_comuns, index=colunas_comuns.index(_padrao(("ESTUDANTE_CPF",)))
    )
    coluna_entidade = col2.selectbox(
        "Coluna de CO_ENTIDADE", options=colunas_comuns, index=colunas_comuns.index(_padrao(("CO_ENTIDADE",)))
    )
    coluna_etapa = col3.selectbox(
        "Coluna de Etapa de Ensino",
        options=colunas_comuns,
        index=colunas_comuns.index(_padrao(("ESTUDANTE_ETAPA_DE_ENSINO",))),
    )

    comparar_por_grupo_amplo = st.checkbox(
        "Ignorar progressão normal de série ao detectar 'trans. mod' (recomendado)",
        value=True,
        help="Com esta opção marcada, só conta como transferência de modalidade quando o "
        "aluno cruza entre EJA e Regular/Técnico. Desmarcada, qualquer mudança no código "
        "bruto de ESTUDANTE_ETAPA_DE_ENSINO conta — inclusive avanço normal de série.",
    )

    if not st.button("▶️ Comparar bases", type="primary"):
        return

    try:
        df_mov, df_cad, relatorio = analisar_movimentacao(
            df_antiga, df_nova, coluna_cpf, coluna_entidade, coluna_etapa,
            comparar_por_grupo_amplo=comparar_por_grupo_amplo,
        )
    except KeyError as exc:
        st.error(str(exc))
        return

    st.success("Comparação concluída.")

    m1, m2, m3, m4, m5, m6 = st.columns(6)
    m1.metric("Antiga", relatorio.total_antiga)
    m2.metric("Nova", relatorio.total_nova)
    m3.metric("Novo cadastro", relatorio.total_novo_cadastro)
    m4.metric("Trans. escola", relatorio.total_trans_escola)
    m5.metric("Trans. mod", relatorio.total_trans_mod)
    m6.metric("Avanço terço EJA", relatorio.total_avanco_terco_eja)

    if relatorio.total_somente_na_antiga:
        st.caption(
            f"ℹ️ {relatorio.total_somente_na_antiga} CPF(s) estavam na base Antiga e não "
            "aparecem na Nova (não incluídos em nenhum dos relatórios abaixo)."
        )
    if relatorio.coluna_terco is None:
        st.caption(
            "ℹ️ Não encontrei uma coluna de terço/período (ex.: ESTUDANTE_ANO_PERIODO) em "
            "uma das duas bases — avanço de terço EJA não foi verificado."
        )

    aba_mov, aba_cad = st.tabs(
        [f"🔀 Movimentação ({df_mov.height})", f"🆕 Cadastro ({df_cad.height})"]
    )
    with aba_mov:
        st.dataframe(df_mov.head(50), width="stretch")
        botoes_download(df_mov, nome_base="movimentacao", key_prefix="mov")
    with aba_cad:
        st.dataframe(df_cad.head(50), width="stretch")
        botoes_download(df_cad, nome_base="cadastro", key_prefix="cad")


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
}

with st.sidebar:
    st.title("📊 Escola 10")
    st.caption("Automação de dados — Pé de Meia (SEDUC)")
    pagina = st.radio("Módulo", list(MODULOS_CORE.keys()), index=0)
    st.divider()
    st.caption(
        "Os 5 módulos do briefing (A-E) estão implementados em `src/core`. "
        "Cada um valida as colunas que recebe e mostra um erro claro se "
        "alguma estiver faltando."
    )

chave_pagina = MODULOS_CORE[pagina]
funcao_pagina = PAGINAS_IMPLEMENTADAS.get(chave_pagina)

if funcao_pagina is not None:
    funcao_pagina()
else:
    pagina_em_breve(pagina)
