#!/usr/bin/env python3
"""Trace/span diagnostics via the Galileo API — F-WORKSHOP-STAB-4, Step 0.

Not production code: prints what the API returns (trace, spans, `metric_info`
per scorer) to measure, before touching any code, whether the signal missing in
each UC is a root cause on the app side or on Galileo's side (e.g. a scorer in
error). Same technique as F-WORKSHOP-STAB-3 (`docs/history/SETUP-HISTORICO.md` § Notes).

Usage (with the backend's venv, which already has `GALILEO_API_KEY` in `.env`):

    cd backend && .venv/bin/python ../scripts/galileo-metric-info.py [--limit N] [--log-stream NAME]

With no arguments, lists the `--limit` (default 5) most recent traces from the
configured log stream. Doesn't filter by UC — run the scenario in the store right before.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from app.obs import galileo_obs  # noqa: E402  — import early so `export_to_environ()` has already run


def _get(obj, key, default=None):
    """`metric_info` and its values arrive as a plain `dict` in practice (measured live),
    but the SDK's typed signature promises objects with `additional_properties`/attributes —
    accept both forms instead of risking breakage when the SDK changes version."""
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _metric_lines(metric_info, *, only: set[str] | None = None, skip_na: bool = False) -> list[str]:
    """Each `metric_info` entry is `scorer_id -> {status_type, metric_key_alias, value,
    explanation, message, ...}`. `metric_key_alias` is the scorer's human-readable name (the
    `scorer_id` alone is a UUID).

    `value` is what answers the question that matters — "did the evaluator flag it?" —, so it's
    the center of the line; `status_type` alone only says whether the scorer ran.
    """
    props = metric_info if isinstance(metric_info, dict) else getattr(metric_info, "additional_properties", None)
    if not props:
        return ["    (no metric_info)"]
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
        raise SystemExit(f"project {project_name!r} not found in the Console")
    log_stream = LogStreams().get(name=log_stream_name, project_id=project.id)
    if log_stream is None:
        raise SystemExit(f"log stream {log_stream_name!r} not found in project {project_name!r}")
    return project.id, log_stream.id


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=5, help="how many most-recent traces to list")
    parser.add_argument("--log-stream", default=None, help="override for GALILEO_LOG_STREAM")
    parser.add_argument("--name", default=None, help="only traces whose name contains this text")
    parser.add_argument(
        "--scorer", action="append", default=None,
        help="only these scorers (repeatable), e.g.: --scorer pii --scorer tool_error",
    )
    parser.add_argument(
        "--skip-na", action="store_true",
        help="hides 'not_applicable' metrics (noise: every scorer shows up on every span)",
    )
    args = parser.parse_args()
    only = set(args.scorer) if args.scorer else None

    if not galileo_obs.is_enabled():
        raise SystemExit("GALILEO_API_KEY empty — configure the backend's .env before running this")

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
        # With `--name` the filter is applied client-side, so we fetch a wider window
        # than `--limit` to avoid coming back empty just because the N most recent are from another feature.
        limit=max(args.limit, 40) if args.name else args.limit,
    )

    records = traces.records or []
    if args.name:
        records = [r for r in records if args.name in (_get(r, "name") or "")][: args.limit]
    if not records:
        print(f"no traces in {project_name}/{log_stream_name}")
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
