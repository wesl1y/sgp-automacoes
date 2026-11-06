"""Schemas Pydantic do módulo de Cálculo de Frequência
(`src/core/frequencia.py`)."""

from __future__ import annotations

from pydantic import BaseModel, Field


class RelatorioFrequencia(BaseModel):
    """Resumo do cruzamento entre a base do SGP e a base do SIGEDUC.

    `total_com_correspondencia_sigeduc` + `total_sem_correspondencia_sigeduc`
    == `total_sgp` (nenhuma linha do SGP é descartada -- só os valores de
    frequência de quem tem correspondência no SIGEDUC podem mudar).

    `total_alterados` == `total_subiu` + `total_desceu_abaixo_80` +
    `total_desceu_acima_80` + `total_manteve_percentual`.
    """

    coluna_cpf_sgp: str
    coluna_cpf_sigeduc: str
    coluna_ofertada: str
    coluna_presente: str
    coluna_ofertada_sigeduc: str = Field(
        description="Coluna de HL_OFERTADA_PERIODO na base do SIGEDUC -- lida direto, sem "
        "calcular (desde 03/09/2026 o SIGEDUC já manda esse valor pronto)."
    )
    coluna_presente_sigeduc: str = Field(
        description="Coluna de HL_PRESENTE_PERIODO na base do SIGEDUC -- lida direto, sem "
        "calcular a partir de HORA_AULA_DADA/FALTAS_ALUNO_HORA (ver coluna_ofertada_sigeduc)."
    )
    coluna_matricula_sgp: str = Field(
        description="Coluna de matrícula do aluno na base do SGP (ID_SGP_MATRICULA) -- usada "
        "junto com o CPF no cruzamento, para pegar a frequência da matrícula exata do aluno."
    )
    coluna_matricula_sigeduc: str = Field(
        description="Coluna de matrícula do aluno na base do SIGEDUC (Matrícula SGP). Ver "
        "coluna_matricula_sgp."
    )

    total_sgp: int = Field(ge=0, description="Linhas na planilha do SGP, como enviada.")
    total_sigeduc: int = Field(ge=0, description="Linhas na planilha do SIGEDUC, como enviada.")
    total_sigeduc_apos_limpeza: int = Field(
        ge=0,
        description="Linhas do SIGEDUC após remover CPF inválido e duplicatas "
        "(mantendo, por CPF + matrícula, a linha de maior HL_PRESENTE) -- é essa base que é "
        "usada no cruzamento.",
    )

    total_com_correspondencia_sigeduc: int = Field(ge=0)
    total_sem_correspondencia_sigeduc: int = Field(
        ge=0,
        description="Alunos/matrículas do SGP não encontrados no SIGEDUC (por CPF ausente ou "
        "inválido, matrícula que não bate, ou que simplesmente não está na base SIGEDUC "
        "enviada) -- o valor de frequência desses alunos é mantido sem alteração.",
    )

    total_alterados: int = Field(ge=0, description="Alunos cujo HL_OFERTADA e/ou HL_PRESENTE mudou.")
    total_subiu: int = Field(ge=0, description="Percentual de presença subiu.")
    total_desceu_abaixo_80: int = Field(
        ge=0, description="Percentual de presença desceu e ficou abaixo de 80%."
    )
    total_desceu_acima_80: int = Field(
        ge=0, description="Percentual de presença desceu, mas continua em 80% ou mais."
    )
    total_manteve_percentual: int = Field(
        ge=0, description="Os valores em horas mudaram, mas o percentual de presença ficou igual."
    )
    total_sem_alteracao: int = Field(ge=0, description="Nada mudou (inclui quem não tem correspondência no SIGEDUC).")


class RelatorioFrequenciaSigeduc(BaseModel):
    """Resumo da geração de frequência **do zero**, só a partir da base
    bruta do SIGEDUC (sem precisar de nenhuma frequência já lançada no
    SGP) -- reproduz o script legado `FREQUENCIA_2026`.

    `total_sigeduc_apos_limpeza` é o número de linhas da planilha de
    frequência gerada (uma por CPF válido e único)."""

    coluna_cpf_sigeduc: str
    coluna_entidade: str
    coluna_oferta: str
    coluna_serie: str
    coluna_mes_referencia: str
    coluna_hora_aula_dada: str
    coluna_faltas_aluno_hora: str
    ano: int = Field(description="Usado em NU_ANO_MATRICULA e ANO_REFERENCIA na saída.")

    coluna_cpf_terco: str | None = Field(
        default=None,
        description="Coluna de CPF da planilha opcional de Terços. None se essa planilha não "
        "foi enviada -- nesse caso ESTUDANTE_ANO_PERIODO sai em branco.",
    )
    coluna_ano_periodo_terco: str | None = Field(
        default=None, description="Coluna de ano/período (terço) da planilha opcional de Terços. Ver coluna_cpf_terco."
    )
    coluna_cpf_id_sgp: str | None = Field(
        default=None,
        description="Coluna de CPF da planilha opcional de ID_SGP. None se essa planilha não "
        "foi enviada -- nesse caso ID_SGP_MATRICULA sai em branco.",
    )
    coluna_id_sgp_matricula: str | None = Field(
        default=None, description="Coluna de ID_SGP_MATRICULA da planilha opcional de ID_SGP. Ver coluna_cpf_id_sgp."
    )

    total_sigeduc: int = Field(ge=0, description="Linhas na planilha do SIGEDUC, como enviada.")
    total_sigeduc_apos_limpeza: int = Field(
        ge=0,
        description="Linhas após limpar CPF inválido e deduplicar (mantendo a última ocorrência "
        "por CPF) -- é o total de linhas da planilha de frequência gerada.",
    )
    total_etapa_nao_mapeada: int = Field(
        ge=0,
        description="Quantos registros da planilha gerada ficaram com ESTUDANTE_ETAPA_DE_ENSINO "
        "= 'NAO_MAPEADO' (Oferta de Ensino / Série não reconhecida -- ver Módulo B). Vale "
        "conferir manualmente.",
    )
    total_sem_terco: int = Field(
        ge=0,
        description="CPFs da planilha gerada sem correspondência na planilha de Terços -- 0 "
        "quando essa planilha não foi enviada (coluna_cpf_terco is None) ou quando todo mundo bateu.",
    )
    total_sem_id_sgp: int = Field(
        ge=0,
        description="CPFs da planilha gerada sem correspondência na planilha de ID_SGP -- 0 "
        "quando essa planilha não foi enviada (coluna_cpf_id_sgp is None) ou quando todo mundo bateu.",
    )
