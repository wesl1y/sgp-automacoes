"""Divergências de série/ano entre os registros de CPF comuns ao SGP e SIGEDUC."""

from __future__ import annotations

import re
import unicodedata

import polars as pl

from src.utils.higienizacao import converter_data_robusta, limpar_cpf


def _nome_livre(base: str, ocupados: set[str]) -> str:
    nome = base
    while nome in ocupados:
        nome += "_"
    ocupados.add(nome)
    return nome


def _serie_normalizada(valor: object, *, codigo_etapa_sgp: bool = False) -> str | None:
    if valor is None:
        return None
    texto = str(valor).strip()
    if not texto:
        return None
    if codigo_etapa_sgp and re.fullmatch(r"\d+(?:\.0+)?", texto):
        codigo = int(float(texto))
        if 25 <= codigo <= 28:
            return str(codigo - 24)
        if 30 <= codigo <= 33:
            return str(codigo - 29)
        if codigo == 45:  # EJA não identifica uma série específica.
            return None
    texto = re.sub(r"(?<=\d)[ªº°]", "", texto)
    texto = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode("ascii")
    texto = re.sub(r"[^A-Z0-9]+", " ", texto.upper()).strip()
    if not texto:
        return None

    serie = re.fullmatch(
        r"(\d{1,2})(?:[AO])?(?:\s+(?:ANO|SERIE))?"
        r"(?:\s+(?:DO|DE)\s+ENSINO\s+(?:MEDIO|FUNDAMENTAL))?",
        texto,
    )
    if serie:
        return str(int(serie.group(1)))
    return texto


def _registro_por_cpf(
    df: pl.DataFrame,
    coluna_cpf: str,
    coluna_data: str | None,
    chave_cpf: str,
    ordem: str,
    data_ordenacao: str,
) -> pl.DataFrame:
    preparado = df.with_row_index(ordem).with_columns(
        limpar_cpf(pl.col(coluna_cpf)).alias(chave_cpf)
    ).filter(pl.col(chave_cpf).is_not_null())
    if coluna_data is not None:
        preparado = preparado.with_columns(
            converter_data_robusta(pl.col(coluna_data))
            .str.to_date("%d/%m/%Y", strict=False)
            .alias(data_ordenacao)
        )
        criterio = [chave_cpf, data_ordenacao, ordem]
    else:
        criterio = [chave_cpf, ordem]
    return preparado.sort(criterio, nulls_last=False).unique(
        subset=[chave_cpf], keep="last", maintain_order=True
    )


def comparar_series(
    sigeduc: pl.DataFrame,
    sgp: pl.DataFrame,
    *,
    cpf_sigeduc: str,
    serie_sigeduc: str,
    cpf_sgp: str,
    serie_sgp: str,
    data_sigeduc: str | None = None,
    data_sgp: str | None = None,
) -> tuple[pl.DataFrame, dict[str, int]]:
    """Compara um registro por CPF nas duas bases e retorna só séries diferentes.

    Se uma coluna de data for selecionada, usa a matrícula mais recente.
    Sem ela, ou em empate de data, usa a última linha do arquivo.
    Registros com série vazia ou código 45 no SGP não permitem comparar
    a série e ficam fora das divergências, com contagem separada.
    """
    for df, colunas, origem in (
        (sigeduc, (cpf_sigeduc, serie_sigeduc, data_sigeduc), "SIGEDUC"),
        (sgp, (cpf_sgp, serie_sgp, data_sgp), "SGP"),
    ):
        faltantes = [coluna for coluna in colunas if coluna is not None and coluna not in df.columns]
        if faltantes:
            raise KeyError(f"Coluna(s) não encontrada(s) no {origem}: {', '.join(faltantes)}")

    ocupados = set(sigeduc.columns) | set(sgp.columns)
    chave = _nome_livre("_comparacao_cpf", ocupados)
    ordem_sig = _nome_livre("_comparacao_ordem_sigeduc", ocupados)
    ordem_sgp = _nome_livre("_comparacao_ordem_sgp", ocupados)
    data_sig_aux = _nome_livre("_comparacao_data_sigeduc", ocupados)
    data_sgp_aux = _nome_livre("_comparacao_data_sgp", ocupados)
    serie_sig_aux = _nome_livre("_comparacao_serie_sigeduc", ocupados)

    sig = _registro_por_cpf(
        sigeduc, cpf_sigeduc, data_sigeduc, chave, ordem_sig, data_sig_aux
    ).select(pl.col(chave), pl.col(serie_sigeduc).alias(serie_sig_aux))
    sistema = _registro_por_cpf(
        sgp, cpf_sgp, data_sgp, chave, ordem_sgp, data_sgp_aux
    )
    cruzados = sistema.join(sig, on=chave, how="inner").sort(chave)

    nomes_adicionais = []
    usados_saida = set(sgp.columns)
    for base in ("CPF do aluno", "Série/ano SGP", "Série/ano SIGEDUC"):
        nome = base if base not in usados_saida else f"{base} (comparação)"
        nomes_adicionais.append(_nome_livre(nome, usados_saida))

    codigo_etapa_sgp = re.sub(r"[^A-Z0-9]", "", serie_sgp.upper()).endswith("ETAPADEENSINO")
    divergencias: list[dict[str, object]] = []
    sem_serie_comparavel = 0
    for linha in cruzados.iter_rows(named=True):
        serie_sgp_normalizada = _serie_normalizada(
            linha[serie_sgp], codigo_etapa_sgp=codigo_etapa_sgp
        )
        serie_sig_normalizada = _serie_normalizada(linha[serie_sig_aux])
        if serie_sgp_normalizada is None or serie_sig_normalizada is None:
            sem_serie_comparavel += 1
            continue
        if serie_sgp_normalizada == serie_sig_normalizada:
            continue
        divergencias.append({
            **{coluna: linha[coluna] for coluna in sgp.columns},
            nomes_adicionais[0]: linha[chave],
            nomes_adicionais[1]: linha[serie_sgp],
            nomes_adicionais[2]: linha[serie_sig_aux],
        })

    if divergencias:
        resultado = pl.DataFrame(
            divergencias,
            schema=[*sgp.columns, *nomes_adicionais],
            orient="row",
            infer_schema_length=None,
        )
    else:
        resultado = sgp.head(0).with_columns(
            pl.lit(None, dtype=pl.Utf8).alias(nome) for nome in nomes_adicionais
        )
    return resultado, {
        "sigeduc_unicos": sig.height,
        "sgp_unicos": sistema.height,
        "cpfs_em_ambas": cruzados.height,
        "sem_serie_comparavel": sem_serie_comparavel,
        "divergencias": resultado.height,
    }
