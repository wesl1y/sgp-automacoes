"""Componentes de download reutilizáveis para o Streamlit."""

from __future__ import annotations

import io

import polars as pl
import streamlit as st

__all__ = ["para_bytes_excel", "para_bytes_csv", "botoes_download"]


def para_bytes_excel(df: pl.DataFrame) -> bytes:
    """Serializa um DataFrame para os bytes de um `.xlsx`."""
    buffer = io.BytesIO()
    df.write_excel(buffer)
    return buffer.getvalue()


def para_bytes_csv(df: pl.DataFrame) -> bytes:
    """Serializa um DataFrame para os bytes de um `.csv` (UTF-8)."""
    return df.write_csv().encode("utf-8")


def botoes_download(df: pl.DataFrame, nome_base: str, key_prefix: str = "") -> None:
    """Desenha dois `st.download_button` lado a lado (`.xlsx` e `.csv`)
    para o mesmo DataFrame.

    Parameters
    ----------
    df:
        DataFrame já pronto para exportar (a rotina de negócio que gerou
        esse DataFrame é responsável por já ter aplicado as regras de
        higienização/ordenação necessárias).
    nome_base:
        Nome do arquivo de saída, sem extensão.
    key_prefix:
        Prefixo usado nas `key` dos botões, para evitar colisão quando a
        mesma tela desenha mais de um par de botões de download.
    """
    col_xlsx, col_csv = st.columns(2)
    col_xlsx.download_button(
        "⬇️ Baixar .xlsx",
        data=para_bytes_excel(df),
        file_name=f"{nome_base}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        key=f"{key_prefix}_xlsx",
        width="stretch",
    )
    col_csv.download_button(
        "⬇️ Baixar .csv",
        data=para_bytes_csv(df),
        file_name=f"{nome_base}.csv",
        mime="text/csv",
        key=f"{key_prefix}_csv",
        width="stretch",
    )
