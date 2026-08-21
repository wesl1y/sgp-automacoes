"""Componente de upload reutilizável para o Streamlit.

A ideia é centralizar aqui, em um único lugar, a lógica "chata" de leitura
de planilha que aparecia repetida em quase todo script legado (tentar UTF-8
e cair para Latin-1, detectar `,` vs `;`, ler tudo como texto para não
perder zero à esquerda de CPF, etc.). Os módulos de `/src/core` e o
`app.py` só precisam chamar `carregar_planilha(...)`.
"""

from __future__ import annotations

import io
from typing import BinaryIO, Union

import polars as pl

# Um `st.file_uploader` devolve um `UploadedFile` (subclasse de BytesIO);
# aceitamos também `bytes` puro e qualquer objeto binário "file-like" para
# facilitar testes fora do Streamlit.
ArquivoEnviado = Union[BinaryIO, bytes]

_EXTENSOES_CSV = {"csv"}
_EXTENSOES_EXCEL = {"xlsx", "xls"}


def carregar_planilha(arquivo: ArquivoEnviado, nome_arquivo: str | None = None) -> pl.DataFrame:
    """Lê um arquivo `.csv`, `.xlsx` ou `.xls` e devolve um `polars.DataFrame`.

    Todas as colunas voltam como texto (string), de propósito: é a mesma
    filosofia "ler como texto" usada em todos os scripts legados, para que
    nenhuma leitura tenha a chance de transformar um CPF ou um código de
    escola em número e comer o zero à esquerda por engano. A tipagem
    correta (data, número, etc.) é responsabilidade explícita de cada
    módulo de `/src/core`, de acordo com o schema Pydantic da rotina.

    Parameters
    ----------
    arquivo:
        Objeto retornado por `st.file_uploader`, ou `bytes`/file-like
        equivalente (por exemplo em testes).
    nome_arquivo:
        Nome do arquivo, usado só para decidir CSV vs Excel pela extensão.
        Se omitido, tentamos usar o atributo `.name` do próprio `arquivo`
        (presente em `UploadedFile` do Streamlit).

    Raises
    ------
    ValueError
        Se a extensão não for `.csv`, `.xlsx` ou `.xls`.
    """
    nome = nome_arquivo or getattr(arquivo, "name", "") or ""
    extensao = nome.rsplit(".", 1)[-1].lower() if "." in nome else ""

    dados = arquivo.read() if hasattr(arquivo, "read") else arquivo
    if hasattr(arquivo, "seek"):
        arquivo.seek(0)  # deixa o stream original intacto para quem for reusá-lo

    if extensao in _EXTENSOES_CSV:
        return _ler_csv_como_texto(dados)
    if extensao in _EXTENSOES_EXCEL:
        return _ler_excel_como_texto(dados)

    raise ValueError(
        f"Formato de arquivo não suportado: '.{extensao or '?'}'. "
        "Envie um arquivo .csv, .xlsx ou .xls."
    )


def _decodificar_texto(dados: bytes) -> str:
    """Tenta UTF-8 primeiro (padrão); Latin-1 nunca falha e cobre os
    exports mais antigos do SIGEDUC/SGP que vêm em ISO-8859-1/CP1252."""
    try:
        return dados.decode("utf-8")
    except UnicodeDecodeError:
        return dados.decode("latin-1")


def _detectar_separador(texto: str) -> str:
    """Decide entre `,` e `;` olhando qual aparece mais na primeira linha
    (cabeçalho). Exports brasileiros de planilha frequentemente usam `;`."""
    primeira_linha = texto.splitlines()[0] if texto else ""
    return ";" if primeira_linha.count(";") > primeira_linha.count(",") else ","


def _ler_csv_como_texto(dados: bytes) -> pl.DataFrame:
    texto = _decodificar_texto(dados)
    separador = _detectar_separador(texto)
    return pl.read_csv(
        io.StringIO(texto),
        separator=separador,
        infer_schema=False,  # força todas as colunas como pl.String
    )


def _ler_excel_como_texto(dados: bytes) -> pl.DataFrame:
    df = pl.read_excel(io.BytesIO(dados))
    # O leitor de Excel já respeita o tipo original da célula (texto
    # continua texto, número continua número) -- ver docstring do módulo.
    # Ainda assim convertemos tudo para string aqui para manter a mesma
    # garantia da leitura de CSV: a partir deste ponto, toda coluna que sai
    # do uploader é texto, e cada módulo de /src/core tipa explicitamente
    # só o que precisar.
    return df.with_columns(pl.all().cast(pl.Utf8, strict=False))
