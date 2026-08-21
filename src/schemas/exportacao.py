"""Schemas Pydantic do módulo de Exportação Padrão MEC
(`src/core/exportacao.py`)."""

from __future__ import annotations

from pydantic import BaseModel, Field


class RelatorioExportacaoMEC(BaseModel):
    """Resumo da aplicação do layout padrão (modelo oficial de colunas)."""

    total_linhas: int = Field(ge=0)
    total_colunas_modelo: int = Field(ge=0, description="Quantidade de colunas do modelo oficial (layout fixo).")

    colunas_criadas_vazias: list[str] = Field(
        default_factory=list,
        description="Colunas do modelo que não existiam na planilha enviada e foram "
        "criadas vazias -- provavelmente precisam ser preenchidas manualmente antes do envio.",
    )
    colunas_descartadas: list[str] = Field(
        default_factory=list,
        description="Colunas que existiam na planilha enviada mas não fazem parte do "
        "modelo -- não entram no arquivo final.",
    )
