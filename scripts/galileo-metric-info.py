#!/usr/bin/env python3
"""Diagnóstico de traces/spans via API do Galileo — F-WORKSHOP-STAB-4, Etapa 0.

Não é código de produção: imprime o que a API responde (trace, spans, `metric_info`
por scorer) pra medir, antes de mexer em código, se o sinal que falta em cada UC é
causa raiz de app ou do lado do Galileo (scorer em erro, por exemplo). Mesma técnica
da F-WORKSHOP-STAB-3 (`docs/history/SETUP-HISTORICO.md` § Notas).

Uso (com o venv do backend, que já tem `GALILEO_API_KEY` no `.env`):

    cd backend && .venv/bin/python ../scripts/galileo-metric-info.py [--limit N] [--log-stream NOME]

Sem argumento nenhum, lista os `--limit` (default 5) traces mais recentes do log
stream configurado. Não filtra por UC — rode o cenário na loja logo antes.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from app.obs import galileo_obs  # noqa: E402  — import cedo pra `export_to_environ()` já ter rodado


def _get(obj, key, default=None):
    """`metric_info` e seus valores chegam como `dict` puro na prática (medido ao vivo),
    mas a assinatura tipada do SDK promete objetos com `additional_properties`/atributos —
    aceita as duas formas em vez de arriscar quebrar quando o SDK mudar de versão."""
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _metric_lines(metric_info, *, only: set[str] | None = None, skip_na: bool = False) -> list[str]:
    """Cada entrada de `metric_info` é `scorer_id -> {status_type, metric_key_alias, value,
    explanation, message, ...}`. `metric_key_alias` é o nome legível do scorer (o `scorer_id`
    sozinho é um UUID).

    `value` é o que responde a pergunta que importa — "o evaluator ACUSOU?" —, então ele é o
    centro da linha; `status_type` sozinho só diz se o scorer rodou.
    """
    props = metric_info if isinstance(metric_info, dict) else getattr(metric_info, "additional_properties", None)
    if not props:
        return ["    (sem metric_info)"]
    lines = []
    for scorer_id, metric in sorted(props.items()):
        scorer = _get(metric, "metric_key_alias") or scorer_id
        if only and not any(k in scorer for k in only):
            continue
        status_type = _get(metric, "status_type", "?")
        if skip_na and status_type == "not_applicable":
            continue
        parts = [f"    scorer={scorer}", f"status={status_type}"]
        value = _get(metric, "value")
        if value is not None:
            parts.append(f"value={value!r}")
        explanation = _get(metric, "explanation")
        if explanation:
            parts.append(f"why={str(explanation)[:160]!r}")
        message = _get(metric, "message")
        if message and status_type in ("error", "failed"):
            parts.append(f"message={message!r}")
        lines.append(" ".join(parts))
    return lines


def _resolve_ids(project_name: str, log_stream_name: str) -> tuple[str, str]:
    from galileo.projects import Projects
    from galileo.log_streams import LogStreams

    project = Projects().get(name=project_name)
    if project is None:
        raise SystemExit(f"projeto {project_name!r} não encontrado no Console")
    log_stream = LogStreams().get(name=log_stream_name, project_id=project.id)
    if log_stream is None:
        raise SystemExit(f"log stream {log_stream_name!r} não encontrado no projeto {project_name!r}")
    return project.id, log_stream.id


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=5, help="quantos traces mais recentes listar")
    parser.add_argument("--log-stream", default=None, help="override de GALILEO_LOG_STREAM")
    parser.add_argument("--name", default=None, help="só traces cujo nome contém este texto")
    parser.add_argument(
        "--scorer", action="append", default=None,
        help="só estes scorers (repetível), ex.: --scorer pii --scorer tool_error",
    )
    parser.add_argument(
        "--skip-na", action="store_true",
        help="esconde métricas 'not_applicable' (ruído: cada scorer aparece em todo span)",
    )
    args = parser.parse_args()
    only = set(args.scorer) if args.scorer else None

    if not galileo_obs.is_enabled():
        raise SystemExit("GALILEO_API_KEY vazio — configure o .env do backend antes de rodar")

    from galileo import search
    from galileo.resources.models.log_records_sort_clause import LogRecordsSortClause
    from galileo.resources.models.log_records_id_filter import LogRecordsIDFilter

    project_name = galileo_obs.project()
    log_stream_name = args.log_stream or galileo_obs.log_stream()
    project_id, log_stream_id = _resolve_ids(project_name, log_stream_name)

    traces = search.get_traces(
        project_id=project_id,
        log_stream_id=log_stream_id,
        sort=LogRecordsSortClause(column_id="created_at", ascending=False),
        # Com `--name` o filtro é aplicado no cliente, então busca-se uma janela maior
        # que `--limit` pra não voltar vazio só porque os N mais recentes são de outra feature.
        limit=max(args.limit, 40) if args.name else args.limit,
    )

    records = traces.records or []
    if args.name:
        records = [r for r in records if args.name in (_get(r, "name") or "")][: args.limit]
    if not records:
        print(f"nenhum trace em {project_name}/{log_stream_name}")
        return

    for record in records:
        trace_id = _get(record, "id")
        name = _get(record, "name")
        metric_info = _get(record, "metric_info")

        spans = search.get_spans(
            project_id=project_id,
            log_stream_id=log_stream_id,
            filters=[LogRecordsIDFilter(column_id="trace_id", value=trace_id)],
            sort=LogRecordsSortClause(column_id="created_at", ascending=True),
            limit=200,
        )
        span_records = spans.records or []

        print(f"trace id={trace_id} name={name!r} spans={len(span_records)}")
        for line in _metric_lines(metric_info, only=only, skip_na=args.skip_na):
            print(line)
        for span in span_records:
            span_name = _get(span, "name")
            span_type = _get(span, "type")
            status_code = _get(span, "status_code")
            print(f"  span name={span_name!r} type={span_type} status_code={status_code}")
            for line in _metric_lines(_get(span, "metric_info"), only=only, skip_na=args.skip_na):
                print(line)
        print()


if __name__ == "__main__":
    main()
