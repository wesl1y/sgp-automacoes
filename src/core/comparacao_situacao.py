"""Compara a situação final do SIGEDUC com o código de situação do SGP."""

from __future__ import annotations

import re
import unicodedata

import polars as pl

from src.utils.higienizacao import converter_data_robusta, limpar_cpf


CODIGOS_SGP = {
    0: "Em andamento",
    1: "Informação incorreta",
    2: "Transferência na mesma rede",
    3: "Transferência para outra rede pública",
    4: "Transferência para outra rede privada",
    5: "Transferência para rede não identificada",
    6: "Evasão",
    7: "Abandono",
    8: "Óbito informado",
    9: "Reclassificação",
    10: "Aprovado",
    11: "Concluinte",
    12: "Reprovado",
    21: "Transferência entre modalidades",
    22: "Trancamento de matrícula em curso técnico",
}

# Chave: texto da situação no SIGEDUC. Valor: códigos equivalentes no SGP.
EQUIVALENCIAS_SIGEDUC_SGP = {
    "TRANSFERIDO": (2, 3, 4, 5),
    "ABANDONO": (7,),
    "APROVADO": (10,),
    "PROGRESSAO PARCIAL": (10,),
    "AVANCO DE ESTUDOS": (10,),
    "CONCLUINTE": (11,),
    "REPROVADO": (12,),
    "REPR. FREQUENCIA": (12,),
    "REPR FREQUENCIA": (12,),
}


def _texto(valor: object) -> str:
    if valor is None:
        return ""
    sem_acento = unicodedata.normalize("NFKD", str(valor)).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"\s+", " ", sem_acento.upper().strip())


def _codigos_esperados(situacao: object, serie: object) -> tuple[int, ...]:
    texto = _texto(situacao)
    if texto == "APROVADO":
        terceiro_ou_quarto = bool(re.search(r"(?<!\d)[34](?:[OA])?(?!\d)", _texto(serie)))
        if terceiro_ou_quarto:
            return (11,)
    return EQUIVALENCIAS_SIGEDUC_SGP.get(texto, ())


def _codigo_sgp(valor: object) -> int | None:
    texto = str(valor).strip() if valor is not None else ""
    if not re.fullmatch(r"\d+(?:\.0+)?", texto):
        return None
    return int(float(texto))


def _por_matricula(
    df: pl.DataFrame,
    coluna_cpf: str,
    coluna_data: str,
    chave_cpf: str,
    chave_data: str,
    ordem: str,
) -> pl.DataFrame:
    """Mantém uma linha por CPF e data de matrícula reconhecida."""
    return (
        df.with_row_index(ordem)
        .with_columns(
            limpar_cpf(pl.col(coluna_cpf)).alias(chave_cpf),
            converter_data_robusta(
                pl.col(coluna_data).cast(pl.Utf8, strict=False)
                .str.strip_chars()
                .str.replace(r"^['’]+\s*", "")
            )
            .str.to_date("%d/%m/%Y", strict=False)
            .alias(chave_data),
        )
        .filter(pl.col(chave_cpf).is_not_null() & pl.col(chave_data).is_not_null())
        .sort([chave_cpf, chave_data, ordem])
        .unique(subset=[chave_cpf, chave_data], keep="last", maintain_order=True)
    )


def comparar_situacoes(
    sigeduc: pl.DataFrame,
    sgp: pl.DataFrame,
    *,
    cpf_sigeduc: str,
    data_sigeduc: str,
    situacao_sigeduc: str,
    serie_sigeduc: str,
    cpf_sgp: str,
    data_sgp: str,
    situacao_sgp: str,
) -> tuple[pl.DataFrame, dict[str, int]]:
    """Devolve divergências de matrícula com CPF e data iguais nas duas bases.

    CPF e data são normalizados antes do cruzamento. Se houver linhas
    repetidas para o mesmo CPF e a mesma matrícula, vale a última linha
    do arquivo. Datas ausentes ou inválidas não entram no cruzamento.
    Situações sem equivalência conhecida são exibidas para revisão.
    """
    for df, colunas, origem in (
        (sigeduc, (cpf_sigeduc, data_sigeduc, situacao_sigeduc, serie_sigeduc), "SIGEDUC"),
        (sgp, (cpf_sgp, data_sgp, situacao_sgp), "SGP"),
    ):
        faltantes = [coluna for coluna in colunas if coluna not in df.columns]
        if faltantes:
            raise KeyError(f"Coluna(s) não encontrada(s) no {origem}: {', '.join(faltantes)}")

    # Nomes internos evitam renomear qualquer coluna original do SGP no join.
    nomes_usados = set(sigeduc.columns) | set(sgp.columns)
    def nome_interno(base: str) -> str:
        nome = base
        while nome in nomes_usados:
            nome += "_"
        nomes_usados.add(nome)
        return nome

    data_sig_aux = nome_interno("_comparacao_data_sigeduc")
    serie_sig_aux = nome_interno("_comparacao_serie_sigeduc")
    situacao_sig_aux = nome_interno("_comparacao_situacao_sigeduc")
    chave_cpf = nome_interno("_comparacao_cpf")
    chave_data = nome_interno("_comparacao_data_matricula")
    ordem = nome_interno("_comparacao_ordem_arquivo")
    sig = _por_matricula(
        sigeduc, cpf_sigeduc, data_sigeduc, chave_cpf, chave_data, ordem
    ).select(
        pl.col(chave_cpf), pl.col(chave_data),
        pl.col(data_sigeduc).alias(data_sig_aux),
        pl.col(serie_sigeduc).alias(serie_sig_aux),
        pl.col(situacao_sigeduc).alias(situacao_sig_aux),
    )
    sistema = _por_matricula(sgp, cpf_sgp, data_sgp, chave_cpf, chave_data, ordem)
    cruzados = sistema.join(sig, on=[chave_cpf, chave_data], how="inner").sort(
        [chave_cpf, chave_data]
    )

    # As colunas da planilha SGP ficam intactas e na mesma ordem. Se algum
    # nome do relatório já existir nela, só o campo adicional recebe sufixo.
    nomes_relatorio = [
        "CPF do aluno", "Data matrícula SIGEDUC", "Série/ano SIGEDUC", "Situação SIGEDUC",
        "Data matrícula SGP", "Código SGP", "Situação SGP",
        "Código(s) esperado(s) no SGP", "Motivo",
    ]
    colunas_adicionais = []
    nomes_ocupados = set(sgp.columns)
    for nome in nomes_relatorio:
        novo = nome
        if novo in nomes_ocupados:
            novo = f"{nome} (comparação)"
            while novo in nomes_ocupados:
                novo += "_"
        colunas_adicionais.append(novo)
        nomes_ocupados.add(novo)

    divergencias = []
    for linha in cruzados.iter_rows(named=True):
        esperados = _codigos_esperados(linha[situacao_sig_aux], linha[serie_sig_aux])
        codigo = _codigo_sgp(linha[situacao_sgp])
        if codigo in esperados:
            continue
        if not esperados:
            motivo = "Situação SIGEDUC sem equivalência cadastrada"
        elif codigo not in CODIGOS_SGP:
            motivo = "Código SGP ausente ou desconhecido"
        else:
            motivo = "Situações diferentes"
        valores_relatorio = [
            linha[chave_cpf], linha[data_sig_aux], linha[serie_sig_aux],
            linha[situacao_sig_aux], linha[data_sgp], linha[situacao_sgp],
            CODIGOS_SGP.get(codigo, "Desconhecida"),
            ", ".join(map(str, esperados)) if esperados else "Não mapeado", motivo,
        ]
        divergencias.append({
            **{coluna: linha[coluna] for coluna in sgp.columns},
            **dict(zip(colunas_adicionais, valores_relatorio)),
        })

    colunas_saida = [*sgp.columns, *colunas_adicionais]
    if divergencias:
        resultado = pl.DataFrame(divergencias, schema=colunas_saida, orient="row", infer_schema_length=None)
    else:
        resultado = sgp.head(0).with_columns(
            pl.lit(None, dtype=pl.Utf8).alias(coluna) for coluna in colunas_adicionais
        )
    return resultado, {
        "sigeduc_unicos": sig.select(pl.col(chave_cpf).n_unique()).item(),
        "sgp_unicos": sistema.select(pl.col(chave_cpf).n_unique()).item(),
        "cpfs_em_ambas": cruzados.select(pl.col(chave_cpf).n_unique()).item(),
        "matriculas_em_ambas": cruzados.height,
        "divergencias": resultado.height,
    }
