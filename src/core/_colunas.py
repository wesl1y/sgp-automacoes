"""Helper interno (não é uma regra de negócio em si) para localizar colunas
cujo nome varia entre exports diferentes do SIGEDUC/SGP -- às vezes com
acento, às vezes sem; às vezes com espaço, às vezes com underscore
(`'OFERTA DE ENSINO'` vs `'OFERTA_ENSINO'`, `'SÉRIE'` vs `'SERIE'`, etc.).

Usado por vários módulos de `/src/core`, por isso vive num arquivo próprio
em vez de duplicado dentro de cada um.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Sequence

import polars as pl


def _normalizar_nome_coluna(nome: str) -> str:
    """Maiúsculas, sem acento, sem espaço/underscore/pontuação -- só letras e números."""
    sem_acento = unicodedata.normalize("NFKD", nome).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^A-Z0-9]", "", sem_acento.upper())


def encontrar_coluna(df: pl.DataFrame, candidatos: Sequence[str]) -> str | None:
    """Devolve o nome **real** da coluna em `df` que corresponde a algum dos
    `candidatos`, comparando de forma tolerante a acento/espaço/caixa.

    `candidatos` deve listar as variações de *nome* já conhecidas (ex.:
    `("OFERTA DE ENSINO", "OFERTA_ENSINO")`) -- a normalização cuida de
    diferenças de acento/espaço/caixa dentro de cada variação, mas não
    adivinha sinônimos completamente diferentes.

    Devolve `None` se nenhum candidato for encontrado.
    """
    colunas_normalizadas = {_normalizar_nome_coluna(c): c for c in df.columns}
    for candidato in candidatos:
        nome_real = colunas_normalizadas.get(_normalizar_nome_coluna(candidato))
        if nome_real is not None:
            return nome_real
    return None


def exigir_coluna(df: pl.DataFrame, candidatos: Sequence[str], descricao: str) -> str:
    """Como `encontrar_coluna`, mas lança `KeyError` com uma mensagem
    explicando o que faltou, em vez de devolver `None`."""
    nome_real = encontrar_coluna(df, candidatos)
    if nome_real is None:
        raise KeyError(
            f"Não encontrei a coluna de {descricao}. Tentei {list(candidatos)!r}. "
            f"Colunas disponíveis: {df.columns}"
        )
    return nome_real


def quotar_identificador(nome: str) -> str:
    """Coloca um nome de coluna entre aspas duplas para uso seguro dentro
    de uma consulta SQL do DuckDB (necessário sempre que o nome tem espaço,
    acento ou outro caractere que não seja letra/underscore)."""
    return '"' + nome.replace('"', '""') + '"'
