"""Reconciliação cronológica das matrículas de EJA Médio entre SIGEDUC e SGP."""
from __future__ import annotations

from collections import defaultdict
from datetime import date
import unicodedata

import polars as pl

from src.core.etapa_de_ensino import calcular_etapa_de_ensino
from src.core.movimentacao import _montar_cadastro
from src.utils.higienizacao import converter_data_robusta, limpar_cpf


def _date_expr(col: str) -> pl.Expr:
    return converter_data_robusta(
        pl.col(col).cast(pl.Utf8, strict=False).str.strip_chars()
        .str.replace(r"^['’]+\s*", "")
    ).str.to_date("%d/%m/%Y", strict=False)


def _norm(value: object) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", str(value or "").upper())
                   if not unicodedata.combining(c)).strip()


def _txt(value: object) -> str:
    return "" if value is None else str(value).strip()


def _code(value: object) -> str:
    raw = _txt(value)
    return raw[:-2] if raw.endswith(".0") else raw


def _key(value: object) -> str:
    return _code(value).lstrip("0") or "0"


def _resultado(status: str, aprovacoes_depois: int) -> str | None:
    if status in {"APROVADO", "APROVADO EM MODULO"}:
        return "11" if aprovacoes_depois == 4 else "10"
    if status.startswith("REPROVADO") or status.startswith("REPR."):
        return "12"
    if status == "ABANDONO":
        return "7"
    if status == "CANCELADO":
        return "cancelado"
    if status == "MATRICULADO":
        return "0"
    return None


def _export_movement(row: dict, code: str, encerramento: date | None, columns: list[str]) -> dict:
    # Mantém o layout SGP do módulo F e a coluna de resultado que o importador exige.
    record = {c: _txt(row.get(c)) for c in columns}
    if "ESTUDANTE_CPF" in record and row.get("_cpf"):
        record["ESTUDANTE_CPF"] = row["_cpf"]
    record["ESTUDANTE_MATRICULA_SITUACAO"] = code
    record["DATA_FIM_OU_APROVACAO"] = encerramento.strftime("%d/%m/%Y") if encerramento and code != "1" else ""
    if code == "11" and encerramento:
        record["DATA_CONCLUSAO_ENSINO_MEDIO"] = encerramento.strftime("%d/%m/%Y")
    return record


def reconciliar_eja(
    sgp_atual: pl.DataFrame, sgp_historico: pl.DataFrame, sigeduc: pl.DataFrame, *,
    cpf_atual: str, data_atual: str, terco_atual: str, situacao_atual: str,
    id_atual: str, escola_atual: str,
    cpf_historico: str, data_historico: str, terco_historico: str,
    situacao_historico: str, id_historico: str, escola_historico: str,
    cpf_sigeduc: str, data_sigeduc: str, encerramento_sigeduc: str,
    situacao_sigeduc: str, censo_sigeduc: str, oferta_sigeduc: str,
    serie_sigeduc: str, foco: pl.DataFrame | None = None,
    cpf_foco: str | None = None, data_foco: str | None = None,
    terco_foco: str | None = None,
    corrigir_pendentes_foco: bool = False,
    situacao_conclusao_foco: str | None = None,
    data_conclusao_foco: str | None = None,
) -> tuple[pl.DataFrame, pl.DataFrame, pl.DataFrame, dict[str, int]]:
    """Reconcilia matrículas desde a data de foco, uma pendência por CPF e rodada.

    Todo o histórico SIGEDUC conta para determinar o terço. Antes da data de
    foco, nenhuma matrícula é corrigida. Código 1 no SGP invalida o registro.
    """
    for label, df, cols in (
        ("SGP atual", sgp_atual, [cpf_atual, data_atual, terco_atual, situacao_atual, id_atual, escola_atual]),
        ("histórico SGP", sgp_historico, [cpf_historico, data_historico, terco_historico,
                                         situacao_historico, id_historico, escola_historico]),
        ("SIGEDUC", sigeduc, [cpf_sigeduc, data_sigeduc, encerramento_sigeduc,
                              situacao_sigeduc, censo_sigeduc, oferta_sigeduc, serie_sigeduc]),
    ):
        missing = [c for c in cols if c not in df.columns]
        if missing:
            raise KeyError(f"{label}: faltam as colunas {', '.join(missing)}")
    if foco is not None and (cpf_foco is None or cpf_foco not in foco.columns):
        raise KeyError("Selecione a coluna de CPF da planilha de foco.")
    if foco is not None and data_foco is not None and data_foco not in foco.columns:
        raise KeyError("Selecione uma coluna de data válida na planilha de foco.")
    if foco is not None and terco_foco is not None and terco_foco not in foco.columns:
        raise KeyError("Selecione uma coluna de terço válida na planilha de foco.")
    if corrigir_pendentes_foco:
        if foco is None or not all((cpf_foco, data_foco, terco_foco,
                                    situacao_conclusao_foco, data_conclusao_foco)):
            raise ValueError("Para corrigir os pendentes, selecione CPF, matrícula, terço, "
                             "situação e data de conclusão na planilha de foco.")
        for col in (situacao_conclusao_foco, data_conclusao_foco):
            if col not in foco.columns:
                raise KeyError(f"Planilha de foco: falta a coluna {col}")

    atual = sgp_atual.with_row_index("_ordem_atual").with_columns(
        limpar_cpf(pl.col(cpf_atual)).alias("_cpf"),
        _date_expr(data_atual).alias("_data"),
    ).filter(pl.col("_cpf").is_not_null())
    if "ESTUDANTE_ETAPA_DE_ENSINO" in atual.columns:
        atual = atual.filter(pl.col("ESTUDANTE_ETAPA_DE_ENSINO").cast(pl.Utf8).str.replace(r"\.0$", "") == "45")
    cpfs_foco = None
    datas_foco: dict[str, date] = {}
    tercos_foco: dict[str, str] = {}
    conclusoes_foco: dict[str, date] = {}
    if foco is not None:
        cpfs_foco = foco.select(limpar_cpf(pl.col(cpf_foco)).alias("_cpf")).filter(pl.col("_cpf").is_not_null()).unique()
        atual = atual.join(cpfs_foco, on="_cpf", how="semi")
        if data_foco is not None:
            alvos = foco.select(
                limpar_cpf(pl.col(cpf_foco)).alias("_cpf"),
                _date_expr(data_foco).alias("_data_alvo"),
                (pl.col(terco_foco).cast(pl.Utf8, strict=False) if terco_foco
                 else pl.lit(None, dtype=pl.Utf8)).alias("_terco_alvo"),
            )
            alvos = alvos.filter(pl.col("_cpf").is_not_null() & pl.col("_data_alvo").is_not_null())
            alvos = alvos.sort(["_cpf", "_data_alvo"]).unique("_cpf", keep="last")
            datas_foco = {cpf: dia for cpf, dia, _ in alvos.iter_rows()}
            tercos_foco = {cpf: _code(terco) for cpf, _, terco in alvos.iter_rows()}
        if corrigir_pendentes_foco:
            aprovacoes_foco = foco.select(
                limpar_cpf(pl.col(cpf_foco)).alias("_cpf"),
                pl.col(situacao_conclusao_foco).cast(pl.Utf8, strict=False).alias("_situacao"),
                _date_expr(data_conclusao_foco).alias("_conclusao"),
            ).filter(pl.col("_cpf").is_not_null() & pl.col("_conclusao").is_not_null())
            for cpf, situacao, conclusao in aprovacoes_foco.iter_rows():
                if _norm(situacao) in {"APROVADO", "APROVADO EM MODULO"}:
                    if cpf not in conclusoes_foco or conclusao > conclusoes_foco[cpf]:
                        conclusoes_foco[cpf] = conclusao
    # Quando o SGP traz mais de uma linha, a matrícula em andamento mais nova
    # é a referência; as demais continuam visíveis no histórico.
    atual = atual.sort(["_cpf", "_data", "_ordem_atual"], nulls_last=True).unique("_cpf", keep="last")
    hist = sgp_historico.with_columns(
        limpar_cpf(pl.col(cpf_historico)).alias("_cpf"),
        _date_expr(data_historico).alias("_data"),
    ).filter(pl.col("_cpf").is_not_null())
    if "ESTUDANTE_ETAPA_DE_ENSINO" in hist.columns:
        hist = hist.filter(pl.col("ESTUDANTE_ETAPA_DE_ENSINO").cast(pl.Utf8).str.replace(r"\.0$", "") == "45")
    if cpfs_foco is not None:
        hist = hist.join(cpfs_foco, on="_cpf", how="semi")
    cpfs = pl.concat([atual.select("_cpf"), hist.select("_cpf")]).unique()
    sig = sigeduc.with_row_index("_ordem_sig").with_columns(
        limpar_cpf(pl.col(cpf_sigeduc)).alias("_cpf"),
        _date_expr(data_sigeduc).alias("_data"),
        _date_expr(encerramento_sigeduc).alias("_fim"),
    ).filter(pl.col("_cpf").is_not_null() & pl.col("_data").is_not_null())
    sig = sig.filter(pl.col(oferta_sigeduc).cast(pl.Utf8).str.to_uppercase().str.contains("EJA") &
                     pl.col(oferta_sigeduc).cast(pl.Utf8).str.to_uppercase().str.contains("MEDIO|MÉDIO"))
    sig = sig.join(cpfs, on="_cpf", how="semi")
    # Mesmo CPF, escola e início representam uma única matrícula. Em exportações
    # repetidas, preserva a situação cuja data de encerramento é mais recente.
    sig = sig.sort(["_cpf", "_data", "_fim", "_ordem_sig"], nulls_last=False).unique(
        ["_cpf", "_data", censo_sigeduc], keep="last", maintain_order=True
    ).sort(["_cpf", "_data", "_ordem_sig"])

    by_hist: dict[str, list[dict]] = defaultdict(list)
    for row in hist.to_dicts():
        by_hist[row["_cpf"]].append(row)
    by_sig: dict[str, list[dict]] = defaultdict(list)
    for row in sig.to_dicts():
        by_sig[row["_cpf"]].append(row)
    current = {r["_cpf"]: r for r in atual.to_dicts()}

    diagnostics: list[dict] = []
    movement: list[dict] = []
    cadastros_por_linha: dict[int, int] = {}
    sem_matricula_posterior = 0
    for cpf in cpfs["_cpf"].to_list():
        a = current.get(cpf)
        events = by_sig.get(cpf, [])
        prior = 0
        prepared: list[dict] = []
        for s in events:
            status = _norm(s[situacao_sigeduc])
            approved = status in {"APROVADO", "APROVADO EM MODULO"}
            after = prior + int(approved)
            stage = 1 if prior < 2 else 2 if prior == 2 else 3
            result = _resultado(status, after)
            prepared.append(dict(sig=s, status=status, before=prior, after=after,
                                 stage=stage, result=result))
            prior = after

        # As matrículas anteriores ao foco só definem a contagem de aprovações.
        # Sem foco, a matrícula atual (ou a última do histórico SGP) é o início.
        data_inicio = datas_foco.get(cpf)
        if data_inicio is None:
            data_inicio = a["_data"] if a is not None else None
        if data_inicio is None:
            datas_historicas = [h["_data"] for h in by_hist.get(cpf, []) if h["_data"]]
            data_inicio = max(datas_historicas) if datas_historicas else (
                prepared[0]["sig"]["_data"] if prepared else None
            )
        data_inicio_original = data_inicio
        terco_alvo = tercos_foco.get(cpf) or (_code(a[terco_atual]) if a is not None else "")
        if not terco_alvo and data_inicio_original is not None:
            anteriores = [h for h in by_hist.get(cpf, []) if h["_data"] == data_inicio_original]
            if anteriores:
                terco_alvo = _code(anteriores[-1][terco_historico])
        # Fora do foco especial, uma data de terço anterior desloca o
        # início para a primeira matrícula do terço indicado. No foco
        # especial, a matrícula atual é corrigida antes da posterior.
        sem_terco_alvo = False
        mesma_data = [e for e in prepared if e["sig"]["_data"] == data_inicio_original]
        posteriores = [e for e in prepared if data_inicio_original is not None
                       and e["sig"]["_data"] > data_inicio_original]
        corrigir_matricula_atual = (
            corrigir_pendentes_foco and cpf in datas_foco
            and terco_alvo == "3" and bool(posteriores)
        )
        if (not corrigir_matricula_atual and data_inicio_original is not None
                and terco_alvo in {"1", "2", "3"} and len(mesma_data) == 1):
            if mesma_data[0]["stage"] < int(terco_alvo):
                primeira_matricula_terco = next((e for e in prepared
                                                if e["stage"] == int(terco_alvo)
                                                and e["sig"]["_data"] > data_inicio_original), None)
                if primeira_matricula_terco is not None:
                    data_inicio = primeira_matricula_terco["sig"]["_data"]
                else:
                    sem_terco_alvo = True
        eventos_escopo = (not sem_terco_alvo and data_inicio is not None
                          and any(e["sig"]["_data"] >= data_inicio for e in prepared))

        # A exportação atual prevalece sobre uma cópia antiga do mesmo ID no
        # histórico. Código 1 não representa uma matrícula válida.
        sgp_por_id: dict[str, dict] = {}
        for indice, row in enumerate(by_hist.get(cpf, [])):
            chave = _code(row[id_historico]) or f"historico_{indice}"
            sgp_por_id[chave] = row
        if a is not None:
            sgp_por_id[_code(a[id_atual]) or "atual"] = a
        sgp_rows = list(sgp_por_id.values())

        # Exceção de conclusão: quando a última matrícula SIGEDUC está
        # aprovada e coincide com a matrícula atual do 3º terço no SGP,
        # informa a conclusão nessa matrícula. O histórico incompleto de
        # aprovações não deve forçar um cadastro posterior inexistente.
        conclusao_final = next((e for e in mesma_data
                                if sem_terco_alvo and terco_alvo == "3"
                                and a is not None and a["_data"] == data_inicio_original
                                and _code(a[terco_atual]) == "3"
                                and _code(a[situacao_atual]) in {"0", "11"}
                                and _code(a[id_atual])
                                and _key(a[escola_atual]) == _key(e["sig"][censo_sigeduc])
                                and e["status"] in {"APROVADO", "APROVADO EM MODULO"}
                                and e["sig"]["_fim"] is not None
                                and not any(outro["sig"]["_data"] > data_inicio_original
                                            for outro in prepared)), None)
        if conclusao_final is not None:
            s = conclusao_final["sig"]
            ja_concluido = _code(a[situacao_atual]) == "11"
            if not ja_concluido:
                movement.append(_export_movement(a, "11", s["_fim"], sgp_atual.columns))
            if cpf in datas_foco:
                sem_matricula_posterior += 1
            diagnostics.append({
                "CPF": cpf,
                "DATA_MATRICULA_SIGEDUC": s["_data"].strftime("%d/%m/%Y"),
                "DATA_INICIO_CORRECAO": data_inicio_original.strftime("%d/%m/%Y"),
                "DATA_MATRICULA_SGP": a["_data"].strftime("%d/%m/%Y"),
                "DATA_ENCERRAMENTO_SIGEDUC": s["_fim"].strftime("%d/%m/%Y"),
                "SITUACAO_SIGEDUC": _txt(s[situacao_sigeduc]),
                "APROVACOES_ANTES": conclusao_final["before"],
                "APROVACOES_ATE_AQUI": conclusao_final["after"],
                "TERCO_NA_PLANILHA_FOCO": tercos_foco.get(cpf, ""),
                "TERCO_ESPERADO": conclusao_final["stage"],
                "CODIGO_ESPERADO": "11", "ID_SGP_ENCONTRADO": _txt(a[id_atual]),
                "CODIGO_SGP": _code(a[situacao_atual]),
                "ESTADO": "CONCLUÍDO" if ja_concluido else "RESULTADO",
                "ACAO_DESTA_RODADA": "" if ja_concluido else "INFORMAR CONCLUSÃO",
                "CADASTRO_DESTA_RODADA": "",
                "MOTIVO": ("Última matrícula aprovada no SIGEDUC; conclusão do 3º terço "
                           "na mesma data e escola do SGP, sem matrícula posterior"),
            })
            continue

        # A aprovação anterior confirmada no foco encerra o 3º terço quando
        # as matrículas posteriores só registram outras situações. A data
        # de conclusão precisa coincidir com o SIGEDUC.
        data_conclusao = conclusoes_foco.get(cpf)
        aprovacao_confirmada = next((e for e in reversed(prepared)
                                    if data_conclusao is not None
                                    and e["status"] in {"APROVADO", "APROVADO EM MODULO"}
                                    and e["sig"]["_fim"] == data_conclusao), None)
        situacao_da_data = [e for e in mesma_data
                            if a is not None
                            and _key(a[escola_atual]) == _key(e["sig"][censo_sigeduc])]
        concluir_sem_aprovacao_posterior = (
            corrigir_pendentes_foco and data_conclusao is not None
            and cpf in datas_foco and terco_alvo == "3"
            and a is not None and a["_data"] == data_inicio_original
            and _code(a[terco_atual]) == "3"
            and _code(a[situacao_atual]) in {"0", "11"} and _code(a[id_atual])
            and data_conclusao >= data_inicio_original
            and aprovacao_confirmada is not None
            and bool(posteriores)
            and not any(e["status"] in {"APROVADO", "APROVADO EM MODULO"}
                        for e in posteriores)
        )
        if concluir_sem_aprovacao_posterior:
            s = aprovacao_confirmada["sig"]
            ja_concluido = _code(a[situacao_atual]) == "11"
            if not ja_concluido:
                movement.append(_export_movement(a, "11", data_conclusao, sgp_atual.columns))
            diagnostics.append({
                "CPF": cpf,
                "DATA_MATRICULA_SIGEDUC": s["_data"].strftime("%d/%m/%Y"),
                "DATA_INICIO_CORRECAO": data_inicio_original.strftime("%d/%m/%Y"),
                "DATA_MATRICULA_SGP": a["_data"].strftime("%d/%m/%Y"),
                "DATA_ENCERRAMENTO_SIGEDUC": data_conclusao.strftime("%d/%m/%Y"),
                "SITUACAO_SIGEDUC": _txt(s[situacao_sigeduc]),
                "APROVACOES_ANTES": aprovacao_confirmada["before"],
                "APROVACOES_ATE_AQUI": aprovacao_confirmada["after"],
                "TERCO_NA_PLANILHA_FOCO": tercos_foco.get(cpf, ""),
                "TERCO_ESPERADO": aprovacao_confirmada["stage"],
                "CODIGO_ESPERADO": "11", "ID_SGP_ENCONTRADO": _txt(a[id_atual]),
                "CODIGO_SGP": _code(a[situacao_atual]),
                "ESTADO": "CONCLUÍDO" if ja_concluido else "RESULTADO",
                "ACAO_DESTA_RODADA": "" if ja_concluido else "INFORMAR CONCLUSÃO PELO FOCO",
                "CADASTRO_DESTA_RODADA": "",
                "MOTIVO": ("Conclusão confirmada por aprovação anterior no SIGEDUC; "
                           "matrículas posteriores sem nova aprovação"),
            })
            continue

        # Transferência da matrícula atual para outra escola, seguida
        # imediatamente de aprovação: derruba o ID antigo e cadastra a
        # matrícula da escola de destino antes de informar seu resultado.
        transferencia_atual = next((e for e in situacao_da_data
                                    if e["status"] == "TRANSFERIDO"), None)
        aprovacao_destino = posteriores[0] if posteriores else None
        corrigir_transferencia = (
            corrigir_pendentes_foco and cpf in datas_foco and terco_alvo == "3"
            and a is not None and a["_data"] == data_inicio_original
            and _code(a[terco_atual]) == "3"
            and _code(a[situacao_atual]) == "0" and _code(a[id_atual])
            and transferencia_atual is not None
            and aprovacao_destino is not None
            and aprovacao_destino["status"] in {"APROVADO", "APROVADO EM MODULO"}
            and data_conclusao is not None
            and aprovacao_destino["sig"]["_fim"] == data_conclusao
        )
        if corrigir_transferencia:
            movement.append(_export_movement(a, "1", None, sgp_atual.columns))
            destino = aprovacao_destino["sig"]
            cadastros_por_linha[destino["_ordem_sig"]] = aprovacao_destino["stage"]
            for e, estado, acao, motivo in (
                (transferencia_atual, "DERRUBAR", "DERRUBAR MATRÍCULA ATUAL",
                 "Transferência da matrícula atual; nova aprovação em outra escola"),
                (aprovacao_destino, "CADASTRO", "CADASTRAR MATRÍCULA",
                 "Primeira matrícula posterior aprovada na escola de destino"),
            ):
                s = e["sig"]
                diagnostics.append({
                    "CPF": cpf,
                    "DATA_MATRICULA_SIGEDUC": s["_data"].strftime("%d/%m/%Y"),
                    "DATA_INICIO_CORRECAO": data_inicio_original.strftime("%d/%m/%Y"),
                    "DATA_MATRICULA_SGP": (a["_data"].strftime("%d/%m/%Y")
                                           if e is transferencia_atual else ""),
                    "DATA_ENCERRAMENTO_SIGEDUC": (s["_fim"].strftime("%d/%m/%Y")
                                                   if s["_fim"] else ""),
                    "SITUACAO_SIGEDUC": _txt(s[situacao_sigeduc]),
                    "APROVACOES_ANTES": e["before"], "APROVACOES_ATE_AQUI": e["after"],
                    "TERCO_NA_PLANILHA_FOCO": tercos_foco.get(cpf, ""),
                    "TERCO_ESPERADO": e["stage"], "CODIGO_ESPERADO": e["result"] or "",
                    "ID_SGP_ENCONTRADO": _txt(a[id_atual]) if e is transferencia_atual else "",
                    "CODIGO_SGP": _code(a[situacao_atual]) if e is transferencia_atual else "",
                    "ESTADO": estado, "ACAO_DESTA_RODADA": acao,
                    "CADASTRO_DESTA_RODADA": ("CADASTRAR MATRÍCULA"
                                                if e is aprovacao_destino else ""),
                    "MOTIVO": motivo,
                })
            continue

        evento_atual = next((e for e in prepared
                             if a is not None
                             and a["_data"] == e["sig"]["_data"]
                             and _key(a[escola_atual]) == _key(e["sig"][censo_sigeduc])
                             and _code(a[terco_atual]) == str(e["stage"])), None)
        codigo_atual = _code(a[situacao_atual]) if a is not None else ""
        resultado_errado = (
            evento_atual is not None and codigo_atual not in {"0", "1"}
            and evento_atual["result"] is not None
            and codigo_atual != evento_atual["result"]
        )
        bad_current = (
            eventos_escopo and a is not None and codigo_atual != "1" and data_inicio is not None
            and a["_data"] is not None and a["_data"] >= data_inicio_original
            and (evento_atual is None or resultado_errado)
        )
        id_invalido = _code(a[id_atual]) if bad_current else None
        chaves_sig = {(e["sig"]["_data"], _key(e["sig"][censo_sigeduc]), str(e["stage"]))
                      for e in prepared}
        esperado_por_chave = {
            (e["sig"]["_data"], _key(e["sig"][censo_sigeduc]), str(e["stage"])): e["result"]
            for e in prepared
        }
        ids_resultado_errado: set[str] = set()
        for row in sgp_rows:
            dia = row["_data"]
            if dia is None or data_inicio is None or dia < data_inicio:
                continue
            chave = (dia, _key(row.get(escola_historico) or row.get(escola_atual)),
                     _code(row.get(terco_historico) or row.get(terco_atual)))
            esperado = esperado_por_chave.get(chave)
            codigo = _code(row.get(situacao_historico) or row.get(situacao_atual))
            if esperado is not None and codigo not in {"0", "1", esperado}:
                ids_resultado_errado.add(_code(row.get(id_historico) or row.get(id_atual)))
        grupos_sgp: dict[tuple, list[dict]] = defaultdict(list)
        for row in sgp_rows:
            dia = row["_data"]
            codigo = _code(row.get(situacao_historico) or row.get(situacao_atual))
            row_id = _code(row.get(id_historico) or row.get(id_atual))
            if (codigo == "1" or row_id == id_invalido or row_id in ids_resultado_errado or dia is None
                    or (data_inicio is not None and dia < data_inicio)):
                continue
            chave = (dia, _key(row.get(escola_historico) or row.get(escola_atual)),
                     _code(row.get(terco_historico) or row.get(terco_atual)))
            if chave in chaves_sig:
                grupos_sgp[chave].append(row)
        ids_duplicados: set[str] = set()
        for chave, rows in grupos_sgp.items():
            if len(rows) < 2:
                continue
            esperado = esperado_por_chave[chave]
            def preferencia(row: dict) -> tuple[bool, bool, str]:
                codigo = _code(row.get(situacao_historico) or row.get(situacao_atual))
                row_id = _code(row.get(id_historico) or row.get(id_atual))
                correto = codigo == esperado or (esperado is None and codigo in {"2", "3", "4", "5"})
                return correto, a is not None and row_id == _code(a[id_atual]), row_id
            manter = max(rows, key=preferencia)
            ids_duplicados.update(_code(row.get(id_historico) or row.get(id_atual))
                                  for row in rows if row is not manter)

        def correspondencias(e: dict) -> list[dict]:
            s = e["sig"]
            return [row for row in sgp_rows
                    if _code(row.get(id_historico) or row.get(id_atual)) != id_invalido
                    and _code(row.get(id_historico) or row.get(id_atual)) not in ids_resultado_errado
                    and _code(row.get(id_historico) or row.get(id_atual)) not in ids_duplicados
                    and _code(row.get(situacao_historico) or row.get(situacao_atual)) != "1"
                    and row["_data"] == s["_data"]
                    and _key(row.get(escola_historico) or row.get(escola_atual)) == _key(s[censo_sigeduc])
                    and _code(row.get(terco_historico) or row.get(terco_atual)) == str(e["stage"])]

        info_eventos: list[tuple[dict, dict | None, str, str, str]] = []
        for e in prepared:
            s = e["sig"]
            matches = correspondencias(e)
            h = sorted(matches, key=lambda r: _code(r.get(id_historico) or r.get(id_atual)))[-1] if matches else None
            existing = _code(h.get(situacao_historico) or h.get(situacao_atual)) if h else ""
            expected = e["result"]
            transferencia_substituida = (
                corrigir_pendentes_foco and e["status"] == "TRANSFERIDO"
                and s["_data"] == data_inicio_original and terco_alvo == "3"
                and aprovacao_destino is not None
                and aprovacao_destino["status"] in {"APROVADO", "APROVADO EM MODULO"}
                and aprovacao_destino["sig"]["_fim"] == data_conclusao
                and any(row["_data"] == s["_data"]
                        and _key(row.get(escola_historico) or row.get(escola_atual)) == _key(s[censo_sigeduc])
                        and _code(row.get(situacao_historico) or row.get(situacao_atual)) == "1"
                        for row in sgp_rows)
            )
            if data_inicio is not None and s["_data"] < data_inicio:
                state, reason = "HISTÓRICO", "Anterior ao início da correção; conta apenas para o terço"
            elif sem_terco_alvo:
                state, reason = "CONFERIR", f"Nenhuma matrícula posterior do {terco_alvo}º terço no SIGEDUC"
            elif e["after"] > 4:
                state, reason = "CONFERIR", "Mais de quatro aprovações no percurso enviado"
            elif len(matches) > 1:
                state, reason = "CONFERIR", "Mais de uma matrícula SGP válida para a mesma data e terço"
            elif transferencia_substituida:
                state, reason = "CONCLUÍDO", "Transferência anterior derrubada para cadastro na escola de destino"
            elif h is None:
                state, reason = "CADASTRO", "Data do SIGEDUC ainda não cadastrada no SGP"
            elif expected is None and e["status"] == "TRANSFERIDO" and existing in {"2", "3", "4", "5"}:
                state, reason = "CONCLUÍDO", "Transferência já consta no SGP"
            elif expected is None:
                state, reason = "CONFERIR", "Situação sem código automático confirmado"
            elif existing == expected:
                state, reason = "CONCLUÍDO", "Matrícula e resultado já constam no SGP"
            elif existing == "0" and expected == "0":
                state, reason = "CADASTRADO", "Matrícula em andamento no SGP"
            elif existing == "0" and s["_fim"] is None:
                state, reason = "CONFERIR", "Resultado sem data de encerramento"
            elif existing == "0":
                state, reason = "RESULTADO", "Matrícula existe; informar resultado"
            else:
                state, reason = "CONFERIR", f"SGP tem código {existing}; esperado {expected}"
            info_eventos.append((e, h, existing, state, reason))

        pendencias = [info for info in info_eventos
                     if info[3] not in {"HISTÓRICO", "CONCLUÍDO", "CADASTRADO"}]
        proxima = pendencias[0] if pendencias else None
        orfaos = []
        for row in sgp_rows:
            if not eventos_escopo:
                continue
            dia = row["_data"]
            codigo = _code(row.get(situacao_historico) or row.get(situacao_atual))
            if codigo == "1" or dia is None or data_inicio is None or dia < data_inicio:
                continue
            chave = (dia, _key(row.get(escola_historico) or row.get(escola_atual)),
                     _code(row.get(terco_historico) or row.get(terco_atual)))
            row_id = _code(row.get(id_historico) or row.get(id_atual))
            if chave not in chaves_sig or row_id in ids_duplicados or row_id in ids_resultado_errado:
                orfaos.append(row)
        orfaos.sort(key=lambda row: (row["_data"], _code(row.get(id_historico) or row.get(id_atual))))
        orfao_acao = None
        if not bad_current and orfaos and (
            proxima is None or orfaos[0]["_data"] <= proxima[0]["sig"]["_data"]
        ):
            candidato = orfaos[0]
            if _code(candidato.get(id_historico) or candidato.get(id_atual)):
                orfao_acao = candidato
                movement.append(_export_movement(candidato, "1", None, sgp_atual.columns))
        if bad_current:
            movement.append(_export_movement(a, "1", None, sgp_atual.columns))

        # Um cadastro por CPF e rodada. Depois de informar o resultado da
        # matrícula atual, o cadastro da próxima data já pode ser preparado:
        # ele usa o SIGEDUC e não depende do novo ID gerado pelo SGP.
        cadastro_evento = None
        resultado_evento = None
        cadastro_bloqueado_por_atual = False
        if proxima is not None:
            e, h, existing, state, reason = proxima
            orfao_impede = orfao_acao is not None and orfao_acao["_data"] < e["sig"]["_data"]
            if state == "CADASTRO" and not orfao_impede:
                atual_aberta_diferente = (
                    a is not None and not bad_current and codigo_atual == "0"
                    and (a["_data"] != e["sig"]["_data"]
                         or _code(a[terco_atual]) != str(e["stage"]))
                )
                cadastro_bloqueado_por_atual = atual_aberta_diferente
                if not atual_aberta_diferente:
                    cadastro_evento = e
                    cadastros_por_linha[e["sig"]["_ordem_sig"]] = e["stage"]
            elif state == "RESULTADO" and not bad_current and orfao_acao is None:
                resultado_evento = e
                movement.append(_export_movement(h, e["result"], e["sig"]["_fim"], sgp_atual.columns))
                if e["result"] != "11" and len(pendencias) > 1:
                    seguinte, _, _, estado_seguinte, _ = pendencias[1]
                    if (estado_seguinte == "CADASTRO"
                            and seguinte["sig"]["_data"] > e["sig"]["_data"]):
                        cadastro_evento = seguinte
                        cadastros_por_linha[seguinte["sig"]["_ordem_sig"]] = seguinte["stage"]

        sem_posterior_neste_cpf = (
            cpf in datas_foco and not any(e["sig"]["_data"] > data_inicio for e in prepared)
        )
        if sem_posterior_neste_cpf:
            sem_matricula_posterior += 1

        for e, h, existing, state, reason in info_eventos:
            s = e["sig"]
            action = ""
            if e is cadastro_evento:
                action = "CADASTRAR MATRÍCULA"
            elif e is resultado_evento:
                action = "INFORMAR RESULTADO"
            if proxima is not None and e is proxima[0]:
                if state == "CADASTRO" and cadastro_bloqueado_por_atual:
                    state, reason = "CONFERIR", "Há outra matrícula atual em andamento; confira antes do cadastro"
            if bad_current and e is evento_atual:
                action = "DERRUBAR E CADASTRAR MATRÍCULA" if e is cadastro_evento else "DERRUBAR MATRÍCULA ATUAL"
            diagnostics.append({
                "CPF": cpf, "DATA_MATRICULA_SIGEDUC": s["_data"].strftime("%d/%m/%Y"),
                "DATA_INICIO_CORRECAO": data_inicio.strftime("%d/%m/%Y") if data_inicio else "",
                "DATA_MATRICULA_SGP": h["_data"].strftime("%d/%m/%Y") if h and h["_data"] else "",
                "DATA_ENCERRAMENTO_SIGEDUC": s["_fim"].strftime("%d/%m/%Y") if s["_fim"] else "",
                "SITUACAO_SIGEDUC": _txt(s[situacao_sigeduc]), "APROVACOES_ANTES": e["before"],
                "APROVACOES_ATE_AQUI": e["after"], "TERCO_NA_PLANILHA_FOCO": tercos_foco.get(cpf, ""),
                "TERCO_ESPERADO": e["stage"],
                "CODIGO_ESPERADO": e["result"] or "",
                "ID_SGP_ENCONTRADO": _txt(h.get(id_historico) or h.get(id_atual)) if h else "",
                "CODIGO_SGP": existing, "ESTADO": state, "ACAO_DESTA_RODADA": action,
                "CADASTRO_DESTA_RODADA": (
                    "CADASTRAR MATRÍCULA" if e is cadastro_evento
                    else "SEM MATRÍCULA POSTERIOR" if sem_posterior_neste_cpf and s["_data"] == data_inicio
                    else ""
                ),
                "MOTIVO": reason,
            })

        # Um registro SGP sem CPF + data + escola + terço no SIGEDUC é uma
        # matrícula excedente dentro do período da correção.
        for row in orfaos:
            codigo = _code(row.get(situacao_historico) or row.get(situacao_atual))
            dia = row["_data"]
            is_current = a is not None and _code(row.get(id_historico) or row.get(id_atual)) == _code(a[id_atual])
            derrubar_nesta_rodada = row is orfao_acao or (is_current and bad_current)
            diagnostics.append({
                "CPF": cpf, "DATA_MATRICULA_SIGEDUC": "",
                "DATA_INICIO_CORRECAO": data_inicio.strftime("%d/%m/%Y") if data_inicio else "",
                "DATA_MATRICULA_SGP": dia.strftime("%d/%m/%Y"),
                "DATA_ENCERRAMENTO_SIGEDUC": "", "SITUACAO_SIGEDUC": "",
                "APROVACOES_ANTES": 0, "APROVACOES_ATE_AQUI": 0,
                "TERCO_NA_PLANILHA_FOCO": tercos_foco.get(cpf, ""),
                "TERCO_ESPERADO": 0, "CODIGO_ESPERADO": "",
                "ID_SGP_ENCONTRADO": _txt(row.get(id_historico) or row.get(id_atual)),
                "CODIGO_SGP": codigo,
                "ESTADO": "DERRUBAR" if derrubar_nesta_rodada else "CONFERIR",
                "ACAO_DESTA_RODADA": "DERRUBAR MATRÍCULA SGP" if derrubar_nesta_rodada else "",
                "CADASTRO_DESTA_RODADA": "",
                "MOTIVO": (
                    "Matrícula SGP duplicada para a mesma data, escola e terço"
                    if _code(row.get(id_historico) or row.get(id_atual)) in ids_duplicados
                    else "Resultado final SGP diferente do SIGEDUC"
                    if _code(row.get(id_historico) or row.get(id_atual)) in ids_resultado_errado
                    else "Matrícula SGP sem correspondência exata de data, escola e terço no SIGEDUC"
                ),
            })
        if bad_current and a["_data"] < data_inicio:
            diagnostics.append({
                "CPF": cpf, "DATA_MATRICULA_SIGEDUC": "",
                "DATA_INICIO_CORRECAO": data_inicio.strftime("%d/%m/%Y"),
                "DATA_MATRICULA_SGP": a["_data"].strftime("%d/%m/%Y"),
                "DATA_ENCERRAMENTO_SIGEDUC": "", "SITUACAO_SIGEDUC": "",
                "APROVACOES_ANTES": 0, "APROVACOES_ATE_AQUI": 0,
                "TERCO_NA_PLANILHA_FOCO": tercos_foco.get(cpf, ""),
                "TERCO_ESPERADO": 0, "CODIGO_ESPERADO": "",
                "ID_SGP_ENCONTRADO": _txt(a[id_atual]), "CODIGO_SGP": codigo_atual,
                "ESTADO": "DERRUBAR", "ACAO_DESTA_RODADA": "DERRUBAR MATRÍCULA ATUAL",
                "CADASTRO_DESTA_RODADA": "",
                "MOTIVO": "Data atual do SGP pertence a terço anterior; cadastro correto inicia no terço selecionado",
            })
        if not events:
            diagnostics.append({"CPF": cpf, "DATA_MATRICULA_SIGEDUC": "", "DATA_INICIO_CORRECAO": "",
                                "DATA_ENCERRAMENTO_SIGEDUC": "",
                                "DATA_MATRICULA_SGP": "",
                                "SITUACAO_SIGEDUC": "", "APROVACOES_ANTES": 0, "APROVACOES_ATE_AQUI": 0,
                                "TERCO_NA_PLANILHA_FOCO": tercos_foco.get(cpf, ""),
                                "TERCO_ESPERADO": 0, "CODIGO_ESPERADO": "", "ID_SGP_ENCONTRADO": "",
                                "CODIGO_SGP": "", "ESTADO": "CONFERIR", "ACAO_DESTA_RODADA": "",
                                "CADASTRO_DESTA_RODADA": "",
                                "MOTIVO": "CPF sem histórico EJA Médio no SIGEDUC enviado"})
        elif not eventos_escopo:
            diagnostics.append({"CPF": cpf, "DATA_MATRICULA_SIGEDUC": "", "DATA_INICIO_CORRECAO": "",
                                "DATA_ENCERRAMENTO_SIGEDUC": "",
                                "DATA_MATRICULA_SGP": "",
                                "SITUACAO_SIGEDUC": "", "APROVACOES_ANTES": prior, "APROVACOES_ATE_AQUI": prior,
                                "TERCO_NA_PLANILHA_FOCO": tercos_foco.get(cpf, ""),
                                "TERCO_ESPERADO": 0, "CODIGO_ESPERADO": "", "ID_SGP_ENCONTRADO": "",
                                "CODIGO_SGP": "", "ESTADO": "CONFERIR", "ACAO_DESTA_RODADA": "",
                                "CADASTRO_DESTA_RODADA": "",
                                "MOTIVO": f"SIGEDUC sem matrícula a partir de {data_inicio:%d/%m/%Y}"})

    diagnostic = pl.DataFrame(diagnostics) if diagnostics else pl.DataFrame()
    if movement:
        move = pl.DataFrame(movement).select([*sgp_atual.columns[:sgp_atual.columns.index("DATA_CONCLUSAO_ENSINO_MEDIO")],
                                               "DATA_FIM_OU_APROVACAO", "DATA_CONCLUSAO_ENSINO_MEDIO"])
    else:
        move = pl.DataFrame(schema={**{c: pl.Utf8 for c in sgp_atual.columns}, "DATA_FIM_OU_APROVACAO": pl.Utf8})
    if cadastros_por_linha:
        chosen = sig.filter(pl.col("_ordem_sig").is_in(list(cadastros_por_linha))).with_columns(
            pl.col("_data").dt.strftime("%d/%m/%Y").alias("_data_matricula_str"),
            pl.when(pl.col("_cpf").is_not_null()).then(pl.col("_cpf")).otherwise(pl.lit("")).alias(cpf_sigeduc),
        )
        stages = [str(cadastros_por_linha[r["_ordem_sig"]]) for r in chosen.to_dicts()]
        chosen = chosen.with_columns(pl.Series("_ano_periodo_terco", stages))
        chosen, _ = calcular_etapa_de_ensino(chosen, oferta_sigeduc, serie_sigeduc)
        cad = _montar_cadastro(chosen, cpf_sigeduc, censo_sigeduc, situacao_sigeduc)
    else:
        cad = pl.DataFrame()
    summary = {"alunos_sgp": cpfs.height, "matriculas_sigeduc": sig.height,
               "derrubar": sum(x["ESTUDANTE_MATRICULA_SITUACAO"] == "1" for x in movement),
               "resultados": sum(x["ESTUDANTE_MATRICULA_SITUACAO"] != "1" for x in movement),
               "cadastros": len(cadastros_por_linha),
               "sem_matricula_posterior": sem_matricula_posterior,
               "conferir": diagnostic.filter(pl.col("ESTADO") == "CONFERIR").height if diagnostic.height else 0,
               "matriculas_no_escopo": diagnostic.filter(
                   (pl.col("DATA_MATRICULA_SIGEDUC") != "") & (pl.col("ESTADO") != "HISTÓRICO")
               ).height if diagnostic.height else 0,
               "datas_pendentes": diagnostic.filter(pl.col("ESTADO") == "CADASTRO").height if diagnostic.height else 0,
               "matriculas_excedentes": diagnostic.filter(
                   (pl.col("DATA_MATRICULA_SIGEDUC") == "") & (pl.col("DATA_MATRICULA_SGP") != "")
               ).height if diagnostic.height else 0}
    return diagnostic, move, cad, summary
