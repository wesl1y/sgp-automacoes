"""Prepara as datas do 1º e do 3º terço a partir das aprovações do SIGEDUC."""

from __future__ import annotations

from datetime import date

import polars as pl

from src.utils.higienizacao import converter_data_robusta, limpar_cpf


STATUS_APROVACAO_MODULO = "APROVADO EM MODULO"


def _data(coluna: str) -> pl.Expr:
    return converter_data_robusta(
        pl.col(coluna).cast(pl.Utf8, strict=False)
        .str.strip_chars()
        .str.replace(r"^['’]+\s*", "")
    ).str.to_date("%d/%m/%Y", strict=False)


def _nome_novo(base: str, ocupados: set[str]) -> str:
    nome = base
    if nome in ocupados:
        nome = f"{base} (calculada)"
    while nome in ocupados:
        nome += "_"
    ocupados.add(nome)
    return nome


def _resultado(base: pl.DataFrame, linhas: list[dict[str, object]], extras: list[str]) -> pl.DataFrame:
    colunas = [*base.columns, *extras]
    if linhas:
        return pl.DataFrame(linhas, schema=colunas, orient="row", infer_schema_length=None)
    return base.head(0).with_columns(pl.lit(None, dtype=pl.Utf8).alias(nome) for nome in extras)


def _ultimo_por_cpf(df: pl.DataFrame, coluna_cpf: str) -> pl.DataFrame:
    return (
        df.with_row_index("_ordem_entrada")
        .with_columns(limpar_cpf(pl.col(coluna_cpf)).alias("_cpf_normalizado"))
        .filter(pl.col("_cpf_normalizado").is_not_null())
        .sort("_ordem_entrada")
        .unique(subset=["_cpf_normalizado"], keep="last", maintain_order=True)
    )


def preparar_ajuste_tercos_eja(
    sigeduc: pl.DataFrame,
    dados_corretos: pl.DataFrame,
    cadastro: pl.DataFrame,
    *,
    cpf_sigeduc: str,
    matricula_sigeduc: str,
    encerramento_sigeduc: str,
    situacao_sigeduc: str,
    cpf_dados_corretos: str,
    cpf_cadastro: str,
    coluna_terco_dados: str | None = None,
    valor_primeiro_terco: str | None = None,
    matricula_cadastro: str | None = None,
    ano_referencia: int | None = None,
) -> tuple[pl.DataFrame, pl.DataFrame, dict[str, int]]:
    """Gera o ajuste do 1º terço e o cadastro da 3ª aprovação.

    Conta aprovações distintas por CPF, em ordem de encerramento. Só entram
    alunos da base correta com três aprovações válidas. A primeira saída
    preserva as colunas da base correta e acrescenta matrícula da 1ª e
    encerramento da 2ª aprovação. A segunda preserva as colunas do cadastro
    e acrescenta matrícula e encerramento da 3ª aprovação. Quando a data de
    matrícula do cadastro é selecionada, ela também precisa coincidir com
    a matrícula da 3ª aprovação.
    """
    for df, colunas, origem in (
        (sigeduc, (cpf_sigeduc, matricula_sigeduc, encerramento_sigeduc, situacao_sigeduc), "SIGEDUC"),
        (dados_corretos, (cpf_dados_corretos, coluna_terco_dados), "dados corretos"),
        (cadastro, (cpf_cadastro, matricula_cadastro), "cadastro"),
    ):
        faltantes = [coluna for coluna in colunas if coluna is not None and coluna not in df.columns]
        if faltantes:
            raise KeyError(f"Coluna(s) não encontrada(s) em {origem}: {', '.join(faltantes)}")
    if coluna_terco_dados is not None and valor_primeiro_terco is None:
        raise ValueError("Selecione o valor que representa o 1º terço na planilha de dados corretos.")

    situacao_normalizada = (
        pl.col(situacao_sigeduc).cast(pl.Utf8, strict=False)
        .str.strip_chars().str.to_uppercase()
        .str.replace_all("Ó", "O")
        .str.replace_all(r"\s+", " ")
    )
    aprovacoes = (
        sigeduc.with_row_index("_ordem_entrada")
        .with_columns(
            limpar_cpf(pl.col(cpf_sigeduc)).alias("_cpf_normalizado"),
            _data(matricula_sigeduc).alias("_matricula_data"),
            _data(encerramento_sigeduc).alias("_encerramento_data"),
            situacao_normalizada.alias("_situacao_normalizada"),
        )
        .filter(pl.col("_cpf_normalizado").is_not_null())
        .filter(pl.col("_situacao_normalizada") == STATUS_APROVACAO_MODULO)
    )
    ignoradas_data = aprovacoes.filter(
        pl.col("_matricula_data").is_null() | pl.col("_encerramento_data").is_null()
    ).height
    aprovacoes = aprovacoes.filter(
        pl.col("_matricula_data").is_not_null() & pl.col("_encerramento_data").is_not_null()
    )
    if ano_referencia is not None:
        aprovacoes = aprovacoes.filter(pl.col("_matricula_data").dt.year() == ano_referencia)
    aprovacoes = (
        aprovacoes.sort("_ordem_entrada")
        .unique(
            subset=["_cpf_normalizado", "_matricula_data", "_encerramento_data"],
            keep="last", maintain_order=True,
        )
        .sort(["_cpf_normalizado", "_encerramento_data", "_matricula_data", "_ordem_entrada"])
    )

    historico: dict[str, list[tuple[date, date]]] = {}
    for cpf, inicio, fim in aprovacoes.select(
        "_cpf_normalizado", "_matricula_data", "_encerramento_data"
    ).iter_rows():
        historico.setdefault(cpf, []).append((inicio, fim))
    com_tres = {cpf: datas[:3] for cpf, datas in historico.items() if len(datas) >= 3}

    dados_primeiro_terco = dados_corretos
    if coluna_terco_dados is not None:
        dados_primeiro_terco = dados_primeiro_terco.filter(
            pl.col(coluna_terco_dados).cast(pl.Utf8, strict=False).str.strip_chars()
            == valor_primeiro_terco.strip()
        )
    dados_unicos = _ultimo_por_cpf(dados_primeiro_terco, cpf_dados_corretos)

    nomes_dados = set(dados_corretos.columns)
    extras_dados = [
        _nome_novo("DATA_MATRICULA_1_APROVACAO", nomes_dados),
        _nome_novo("DATA_ENCERRAMENTO_2_APROVACAO", nomes_dados),
    ]
    linhas_dados: list[dict[str, object]] = []
    cpfs_aptos: set[str] = set()
    for linha in dados_unicos.iter_rows(named=True):
        cpf = linha["_cpf_normalizado"]
        datas = com_tres.get(cpf)
        if datas is None:
            continue
        cpfs_aptos.add(cpf)
        linhas_dados.append({
            **{coluna: linha[coluna] for coluna in dados_corretos.columns},
            extras_dados[0]: datas[0][0].strftime("%d/%m/%Y"),
            extras_dados[1]: datas[1][1].strftime("%d/%m/%Y"),
        })
    ajuste_primeiro_terco = _resultado(dados_corretos, linhas_dados, extras_dados)

    cadastro_preparado = cadastro.with_columns(
        limpar_cpf(pl.col(cpf_cadastro)).alias("_cpf_normalizado")
    ).filter(pl.col("_cpf_normalizado").is_not_null())
    if matricula_cadastro is not None:
        cadastro_preparado = cadastro_preparado.with_columns(
            _data(matricula_cadastro).alias("_matricula_cadastro_data")
        )
    nomes_cadastro = set(cadastro.columns)
    extras_cadastro = [
        _nome_novo("DATA_MATRICULA_3_APROVACAO", nomes_cadastro),
        _nome_novo("DATA_ENCERRAMENTO_3_APROVACAO", nomes_cadastro),
    ]
    cadastro_por_cpf: dict[str, dict[str, object]] = {}
    for linha in cadastro_preparado.iter_rows(named=True):
        cpf = linha["_cpf_normalizado"]
        if cpf not in cpfs_aptos:
            continue
        terceira = com_tres[cpf][2]
        if matricula_cadastro is not None and linha["_matricula_cadastro_data"] != terceira[0]:
            continue
        cadastro_por_cpf[cpf] = linha  # último registro correspondente do arquivo
    linhas_cadastro: list[dict[str, object]] = []
    for cpf, linha in cadastro_por_cpf.items():
        terceira = com_tres[cpf][2]
        linhas_cadastro.append({
            **{coluna: linha[coluna] for coluna in cadastro.columns},
            extras_cadastro[0]: terceira[0].strftime("%d/%m/%Y"),
            extras_cadastro[1]: terceira[1].strftime("%d/%m/%Y"),
        })
    cadastro_terceiro_terco = _resultado(cadastro, linhas_cadastro, extras_cadastro)

    return ajuste_primeiro_terco, cadastro_terceiro_terco, {
        "aprovacoes_validas": aprovacoes.height,
        "aprovacoes_sem_data": ignoradas_data,
        "cpfs_com_tres_aprovacoes": len(com_tres),
        "cpfs_primeiro_terco": dados_unicos.height,
        "ajustes_primeiro_terco": ajuste_primeiro_terco.height,
        "cadastros_terceiro_terco": cadastro_terceiro_terco.height,
        "sem_cadastro_correspondente": len(cpfs_aptos) - cadastro_terceiro_terco.height,
    }
