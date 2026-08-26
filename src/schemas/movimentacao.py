"""Schemas Pydantic do módulo de Discrepâncias e Movimentação
(`src/core/movimentacao.py`)."""

from __future__ import annotations

from pydantic import BaseModel, Field


class RelatorioMovimentacao(BaseModel):
    """Resumo da reconciliação entre a base **SGP atual** (quem já está
    cadastrado) e a base bruta do **SIGEDUC** (a fonte de verdade).

    Cobre os dois relatórios devolvidos por `reconciliar_sgp_sigeduc`:

    - **Cadastro** (`total_cadastro`): CPFs que aparecem no SIGEDUC mas
      ainda não existem na base SGP -- candidatos a novo cadastro.
    - **Movimentação**: CPFs que já existem na base SGP, classificados
      conforme o que o SIGEDUC mostra sobre eles hoje. `total_sgp` se
      divide em `total_trans_escola` + `total_trans_mod` +
      `total_cancelado` + `total_sem_alteracao` +
      `total_sgp_sem_correspondencia_sigeduc`.
    """

    coluna_cpf_sgp: str
    coluna_entidade_sgp: str
    coluna_etapa_sgp: str
    coluna_cpf_sigeduc: str
    coluna_censo_sigeduc: str
    coluna_situacao_sigeduc: str
    coluna_data_encerramento_sigeduc: str
    coluna_data_atualizacao_sigeduc: str | None = Field(
        default=None,
        description="Coluna de 'Data Atualização do Cadastro' do SIGEDUC, "
        "usada como reserva quando a Data Encerramento estiver vazia. None "
        "se não encontrada -- nesse caso só a Data Encerramento é usada.",
    )
    coluna_data_matricula_sigeduc: str
    coluna_oferta_sigeduc: str
    coluna_serie_sigeduc: str

    total_sgp: int = Field(ge=0, description="Linhas na base SGP enviada.")
    total_sgp_cpf_valido: int = Field(ge=0)
    total_sigeduc: int = Field(ge=0, description="Linhas somadas de todas as planilhas do SIGEDUC enviadas.")
    total_sigeduc_cpf_valido: int = Field(ge=0)

    total_cadastro: int = Field(
        ge=0, description="CPFs do SIGEDUC que ainda não estão na base SGP -- candidatos a novo cadastro."
    )

    total_sgp_sem_correspondencia_sigeduc: int = Field(
        ge=0, description="CPFs do SGP sem nenhuma matrícula ativa encontrada no SIGEDUC enviado."
    )
    total_trans_escola: int = Field(ge=0)
    total_trans_mod: int = Field(ge=0)
    total_cancelado: int = Field(
        ge=0,
        description="Mudança de modalidade sem data de encerramento registrada na matrícula de "
        "origem, e o CPF tem histórico de alguma situação CANCELADO no SIGEDUC. Contado aqui, "
        "mas -- assim como quem não teve nenhuma alteração -- não aparece nas linhas de "
        "`df_movimentacao` (que só lista 'trans. escola'/'trans. mod').",
    )
    total_sem_alteracao: int = Field(ge=0)
