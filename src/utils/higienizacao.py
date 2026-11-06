"""Funções de higienização e padronização de dados.

Este módulo centraliza as regras de limpeza usadas em praticamente todos os
scripts legados do Escola 10 / Pé de Meia: normalização de CPF, conversão
robusta de datas para o padrão brasileiro (DD/MM/AAAA) e padronização de
colunas de texto livre (nomes de alunos, escolas, etc.).

Estilo adotado
---------------
Todas as funções recebem e devolvem uma `polars.Expr` (não um DataFrame
inteiro nem uma Series solta). Esse é o padrão idiomático do Polars: as
expressões são "preguiçosas" (só são avaliadas quando entram em um
`.select()` / `.with_columns()`), o que permite que o motor do Polars
otimize e paralelize a consulta inteira -- inclusive quando o DataFrame é
lido por um `LazyFrame` ou entregue ao DuckDB depois.

Uso típico dentro de um módulo de `/src/core`::

    import polars as pl
    from src.utils.higienizacao import (
        limpar_cpf,
        converter_data_robusta,
        padronizar_textos,
    )

    df = df.with_columns(
        ESTUDANTE_CPF=limpar_cpf(pl.col("ESTUDANTE_CPF")),
        DATA_INICIO_MATRICULA=converter_data_robusta(pl.col("DATA_INICIO_MATRICULA")),
    )

    # Padronizar várias colunas de texto de uma vez, usando um seletor de tipo:
    df = df.with_columns(padronizar_textos(pl.col(pl.Utf8)))

Cada função também funciona diretamente sobre uma `polars.Series`, sem
precisar montar um DataFrame primeiro -- útil para testar rapidamente em um
notebook/REPL::

    >>> import polars as pl
    >>> limpar_cpf(pl.Series(["123.456.789-09"])).to_list()
    ['12345678909']
"""

from __future__ import annotations

import polars as pl

__all__ = ["limpar_cpf", "converter_data_robusta", "padronizar_textos"]


def _aplicar(coluna: pl.Series | pl.Expr, construir_expr) -> pl.Series | pl.Expr:
    """Permite que as funções públicas aceitem tanto `pl.Expr` quanto `pl.Series`.

    Internamente tudo é implementado em cima de expressões (o jeito
    performático/idiomático do Polars). Quando quem chamou passou uma
    `Series` "solta" (fora de um DataFrame), montamos um DataFrame de uma
    coluna só nos bastidores, aplicamos a expressão e devolvemos a Series
    resultante -- assim quem está só testando a função no console não
    precisa saber desse detalhe de implementação.
    """
    if isinstance(coluna, pl.Series):
        nome = coluna.name or "valor"
        return (
            coluna.to_frame(nome)
            .select(construir_expr(pl.col(nome)).alias(nome))
            .to_series()
        )
    return construir_expr(coluna)


def limpar_cpf(coluna: pl.Series | pl.Expr) -> pl.Series | pl.Expr:
    """Padroniza uma coluna de CPF -- pensada especificamente para cruzar
    duas bases que representam o **mesmo** CPF de formas diferentes (ex.:
    uma planilha com pontos/traços e zero à esquerda, tipo
    ``"012.345.678-99"``, e outra só com os dígitos e sem zero à esquerda,
    tipo ``1234567899``, porque a coluna foi lida/exportada como número) --
    depois de limpar as duas, o resultado é idêntico e o cruzamento por CPF
    funciona independente de qual das duas formas cada base usa.

    Regras aplicadas, na ordem:

    1. Converte qualquer entrada para string (aceita números, floats como
       ``123.0`` vindos de leitura mal tipada de Excel, etc., e também
       notação científica tipo ``"1.23456789E9"``, que pode aparecer em CSV
       exportado de planilha quando a coluna vira número e o número é
       grande).
    2. Remove tudo que não for dígito (pontos, traços, espaços, letras).
    3. Preenche com zeros à esquerda até completar 11 dígitos (``zfill``)
       -- é este passo que resolve o caso de uma base ter o CPF sem o(s)
       zero(s) à esquerda que a outra base preservou.
    4. Invalida (retorna nulo) CPFs formados inteiramente por zeros.
    5. Proteção extra (além do pedido original): se depois da limpeza
       sobrarem *mais* de 11 dígitos, o valor claramente não é um CPF
       (ex.: telefone digitado no campo errado) -- em vez de truncar ou
       inventar um número, retornamos nulo para não mascarar erro de
       digitação como um CPF válido.

    Um CPF nulo/vazio na entrada também retorna nulo (nunca ``"00000000000"``).
    """

    def construir(expr: pl.Expr) -> pl.Expr:
        bruto = expr.cast(pl.Utf8, strict=False)

        # Notação científica (ex.: "1.23456789E9"): se deixássemos cair
        # direto no passo de remover não-dígitos, o "." e o "E" seriam
        # descartados e os dígitos da mantissa e do expoente se
        # misturariam, formando um número errado em vez de rejeitar ou
        # interpretar corretamente. Por isso, quando a entrada bate com
        # esse formato, primeiro convertemos para número e formatamos de
        # volta como inteiro (sem parte decimal, sem expoente) antes de
        # seguir com a limpeza normal.
        eh_cientifica = bruto.str.contains(r"(?i)^-?\d+(\.\d+)?e[+-]?\d+$")
        bruto_resolvido = pl.when(eh_cientifica).then(
            bruto.cast(pl.Float64, strict=False).cast(pl.Int64, strict=False).cast(pl.Utf8, strict=False)
        ).otherwise(bruto)

        texto = (
            bruto_resolvido
            .str.replace_all(r"\.0$", "")  # ex.: "123456789.0" -> "123456789"
            .str.replace_all(r"[^0-9]", "")
        )
        normalizado = texto.str.zfill(11)

        invalido = (
            texto.is_null()
            | (texto == "")
            | (normalizado == "0" * 11)
            | (texto.str.len_chars() > 11)
        )

        return pl.when(invalido).then(None).otherwise(normalizado)

    return _aplicar(coluna, construir)


def converter_data_robusta(coluna: pl.Series | pl.Expr) -> pl.Series | pl.Expr:
    """Converte uma coluna de datas em múltiplos formatos para o padrão único
    ``'DD/MM/AAAA'`` (string).

    Formatos de entrada reconhecidos, com ou sem componente de hora
    (``2026-05-13 00:00:00`` ou ``2026-05-13T00:00:00`` são aceitos, mas a
    hora é sempre descartada na saída):

    - ``AAAA-MM-DD``
    - ``DD-MM-AAAA``
    - ``DD/MM/AAAA``
    - ``MM/DD/AAAA`` (formato americano)

    Como ``DD/MM/AAAA`` e ``MM/DD/AAAA`` usam a mesma barra como separador,
    a função sempre tenta primeiro a leitura brasileira (``DD/MM/AAAA``) --
    coerente com a origem dos dados (SIGEDUC/SGP) -- e só cai para
    ``MM/DD/AAAA`` quando a leitura brasileira é matematicamente impossível
    (ex.: ``"05/25/2026"``, onde 25 não pode ser mês). Datas ambíguas, em
    que as duas leituras seriam válidas (ex.: ``"03/04/2026"``), são
    **sempre** assumidas como ``DD/MM/AAAA``.

    Valores nulos, vazios, ou que não casem com nenhum formato conhecido
    (incluindo datas "impossíveis" como ``31/02/2026``) retornam nulo.
    """

    def construir(expr: pl.Expr) -> pl.Expr:
        # 1. Normaliza para string e descarta qualquer componente de hora
        #    (tudo a partir do primeiro espaço ou "T").
        texto = (
            expr.cast(pl.Utf8, strict=False)
            .str.strip_chars()
            .str.replace(r"[ T].*$", "")
        )
        texto = (
            pl.when(texto.str.to_lowercase().is_in(["", "nan", "nat", "none", "null"]))
            .then(None)
            .otherwise(texto)
        )

        # 2. Detecta a "forma" da string (onde está o grupo de 4 dígitos do
        #    ano) para decidir qual formato tentar -- evita ambiguidade
        #    entre AAAA-MM-DD e DD-MM-AAAA que um parser genérico poderia
        #    interpretar errado.
        eh_ano_primeiro = texto.str.contains(r"^\d{4}-\d{1,2}-\d{1,2}$")
        eh_ano_por_ultimo_com_traco = texto.str.contains(r"^\d{1,2}-\d{1,2}-\d{4}$")
        eh_formato_com_barra = texto.str.contains(r"^\d{1,2}/\d{1,2}/\d{4}$")

        candidato_aaaa_mm_dd = pl.when(eh_ano_primeiro).then(
            texto.str.to_date("%Y-%m-%d", strict=False)
        )
        candidato_dd_mm_aaaa_traco = pl.when(eh_ano_por_ultimo_com_traco).then(
            texto.str.to_date("%d-%m-%Y", strict=False)
        )
        # Formato brasileiro (DD/MM/AAAA) tem prioridade sobre o americano.
        candidato_dd_mm_aaaa_barra = pl.when(eh_formato_com_barra).then(
            texto.str.to_date("%d/%m/%Y", strict=False)
        )
        candidato_mm_dd_aaaa_barra = pl.when(eh_formato_com_barra).then(
            texto.str.to_date("%m/%d/%Y", strict=False)
        )

        data_final = pl.coalesce(
            [
                candidato_aaaa_mm_dd,
                candidato_dd_mm_aaaa_traco,
                candidato_dd_mm_aaaa_barra,
                candidato_mm_dd_aaaa_barra,
            ]
        )

        return data_final.dt.strftime("%d/%m/%Y")

    return _aplicar(coluna, construir)


def padronizar_textos(coluna: pl.Series | pl.Expr) -> pl.Series | pl.Expr:
    """Padroniza uma coluna de texto livre (nome do estudante, escola, etc.):
    caixa alta, sem espaços nas pontas e sem espaços duplicados no meio.
    """

    def construir(expr: pl.Expr) -> pl.Expr:
        return (
            expr.cast(pl.Utf8, strict=False)
            .str.strip_chars()
            .str.replace_all(r"\s+", " ")
            .str.to_uppercase()
        )

    return _aplicar(coluna, construir)


if __name__ == "__main__":
    # Auto-teste rápido -- rode com `python src/utils/higienizacao.py` a
    # partir da raiz do projeto para conferir visualmente o comportamento
    # das três funções antes de usá-las em um módulo de /src/core.
    amostra = pl.DataFrame(
        {
            "CPF_SUJO": [
                "123.456.789-09",
                "00000000000",
                None,
                "1234567890.0",
                "999999999999999",
            ],
            "DATA_SUJA": [
                "2026-05-13",
                "13-05-2026",
                "13/05/2026",
                "05/25/2026",  # só é válido como MM/DD (mês 25 não existe)
                "31/02/2026",  # inválida nos dois sentidos -> nulo
            ],
            "NOME_SUJO": [
                "  joão   da  silva ",
                "MARIA  DOS SANTOS",
                "ana clara",
                None,
                "Pedro   Álvares  Cabral",
            ],
        }
    )

    resultado = amostra.with_columns(
        CPF_LIMPO=limpar_cpf(pl.col("CPF_SUJO")),
        DATA_LIMPA=converter_data_robusta(pl.col("DATA_SUJA")),
        NOME_LIMPO=padronizar_textos(pl.col("NOME_SUJO")),
    )

    with pl.Config(tbl_cols=-1, tbl_width_chars=140, fmt_str_lengths=40):
        print(resultado)

    # Duas bases representando o MESMO CPF de formas diferentes (uma
    # pontuada e já com zero à esquerda, outra só com os dígitos e sem
    # zero à esquerda -- ex.: coluna lida/exportada como número) têm que
    # limpar para o mesmo valor -- é isso que permite cruzar SGP x SIGEDUC
    # por CPF mesmo quando as duas planilhas formatam o campo diferente.
    cpf_pontuado = limpar_cpf(pl.Series(["012.345.678-99"])).to_list()[0]
    cpf_numero_sem_zero = limpar_cpf(pl.Series([1234567899])).to_list()[0]
    assert cpf_pontuado == cpf_numero_sem_zero == "01234567899", (
        cpf_pontuado, cpf_numero_sem_zero
    )

    # Notação científica (CSV mal exportado de planilha, ex.: coluna virou
    # número e o número é grande) também tem que resolver pro mesmo CPF.
    cpf_cientifica = limpar_cpf(pl.Series(["1.234567899E9"])).to_list()[0]
    assert cpf_cientifica == "01234567899", cpf_cientifica

    print(
        "\nOK: auto-teste de higienizacao.py passou (inclui tolerância a "
        "CPF pontuado/número puro/notação científica, todos pro mesmo valor)."
    )
