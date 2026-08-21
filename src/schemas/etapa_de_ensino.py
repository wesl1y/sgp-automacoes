"""Schemas Pydantic do módulo Motor de Etapa de Ensino (`src/core/etapa_de_ensino.py`)."""

from __future__ import annotations

from pydantic import BaseModel, Field


class RelatorioEtapaDeEnsino(BaseModel):
    """Resumo de como a coluna `ESTUDANTE_ETAPA_DE_ENSINO` foi calculada,
    para a UI do Streamlit mostrar quantos alunos caíram em cada código
    (e, principalmente, quantos ficaram como `NAO_MAPEADO`)."""

    coluna_oferta: str
    coluna_serie: str
    total_registros: int = Field(ge=0)
    contagem_por_etapa: dict[str, int] = Field(
        description="Código da etapa (ex.: '45', '30', 'NAO_MAPEADO') -> "
        "quantidade de registros classificados nele."
    )
    total_nao_mapeado: int = Field(
        ge=0,
        description="Quantos registros não bateram com nenhuma regra (nem "
        "EJA, nem técnico/regular com série 1ª-4ª reconhecida). Vale a "
        "pena revisar esses casos manualmente.",
    )
