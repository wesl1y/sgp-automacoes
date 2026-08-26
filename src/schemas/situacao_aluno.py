"""Schemas Pydantic do módulo de Situação de Matrícula e Aprovações
(`src/core/situacao_aluno.py`)."""

from __future__ import annotations

from pydantic import BaseModel, Field


class RelatorioSituacaoAluno(BaseModel):
    """Resumo do cruzamento entre a base bruta do SIGEDUC (situação de
    matrícula/aprovação) e a base de destino (Pé de Meia/SGP a atualizar).

    `total_com_correspondencia_sigeduc` + `total_sem_correspondencia_sigeduc`
    == `total_destino` (nenhuma linha do destino é descartada pelo
    cruzamento em si -- só pode ser removida depois, e apenas por
    divergência de Censo, se essa checagem estiver ligada).
    """

    coluna_cpf_sigeduc: str
    coluna_cpf_destino: str
    coluna_data_fim: str
    coluna_data_encerramento: str = Field(
        description="Coluna usada para decidir qual registro é a 'situação "
        "mais recente' de cada CPF (prioriza data real; sem nenhuma data "
        "válida, mantém o último registro na ordem original da planilha)."
    )
    coluna_periodo: str
    coluna_situacao: str
    status_aprovado: str = Field(
        description="Texto (case-insensitive) usado para contar "
        "'QTD_MODULOS_APROVADOS' -- conta toda linha do SIGEDUC cuja "
        "situação contenha esse texto, não só a situação mais recente."
    )

    aplicar_transferencia_externa: bool = Field(
        description="Se True, um CPF cuja situação mais recente é "
        "'TRANSFERIDO' e que nunca teve nenhuma outra situação no "
        "histórico do SIGEDUC enviado é reclassificado para "
        "'TRANSFERÊNCIA EXTERNA' (aluno que veio transferido de fora, "
        "nunca chegou a ter uma matrícula própria registrada aqui)."
    )
    aplicar_checagem_censo: bool = Field(
        description="Se True, compara o código de Censo/INEP do SIGEDUC "
        "com o CO_ENTIDADE do destino para cada CPF encontrado; quem "
        "divergir é removido da base principal e devolvido à parte."
    )
    coluna_censo: str | None = Field(
        default=None, description="Coluna de Censo/INEP usada (base SIGEDUC). None se a checagem estiver desligada."
    )
    coluna_co_entidade: str | None = Field(
        default=None, description="Coluna de CO_ENTIDADE usada (base destino). None se a checagem estiver desligada."
    )

    total_sigeduc: int = Field(ge=0, description="Linhas na planilha do SIGEDUC, como enviada.")
    total_sigeduc_apos_limpeza: int = Field(
        ge=0, description="Linhas do SIGEDUC após remover CPF inválido -- é essa base que é usada no cruzamento."
    )
    total_destino: int = Field(ge=0, description="Linhas na planilha de destino, como enviada.")

    total_com_correspondencia_sigeduc: int = Field(
        ge=0, description="CPFs do destino encontrados na base SIGEDUC (tiveram algum registro)."
    )
    total_sem_correspondencia_sigeduc: int = Field(
        ge=0,
        description="CPFs do destino não encontrados no SIGEDUC enviado -- "
        "esses registros mantêm os valores que já tinham no destino, "
        "exceto QTD_MODULOS_APROVADOS, que fica 0.",
    )
    total_com_aprovacao: int = Field(
        ge=0, description="CPFs do destino com QTD_MODULOS_APROVADOS maior que zero."
    )

    total_reclassificados_transferencia_externa: int = Field(
        ge=0,
        description="CPFs cuja situação mais recente foi trocada para "
        "'TRANSFERÊNCIA EXTERNA'. Sempre 0 se aplicar_transferencia_externa for False.",
    )

    total_divergentes_censo: int = Field(
        ge=0,
        description="CPFs removidos da base principal por ter Censo (SIGEDUC) "
        "diferente do CO_ENTIDADE (destino) -- ficam em df_divergentes, não em "
        "df_atualizado. Sempre 0 se aplicar_checagem_censo for False.",
    )
