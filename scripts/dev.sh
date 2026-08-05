#!/usr/bin/env bash
# DEV mode (sem Docker p/ app). Back + Front como processos locais.
#
#   ./scripts/dev.sh              # hot reload + Postgres/pgvector RAG (default)
#   ./scripts/dev.sh --no-rag     # sem Postgres — retriever keyword em processo
#   ./scripts/dev.sh --o11y       # + Splunk auto-instrumentação (sem --reload no backend)
set -e
ROOT="$(cd "$(dirname "$0")/.." && pwd)"

O11Y=0
RAG=1
while [ $# -gt 0 ]; do
  case "$1" in
    --o11y) O11Y=1; shift ;;
    --no-rag) RAG=0; shift ;;
    --rag) shift ;;  # legacy no-op — RAG é default desde F-RAG-LIVE
    -h|--help)
      cat <<'EOF'
usage: dev.sh [--no-rag] [--o11y]

  (default)  Postgres/pgvector + index automático + backend --reload + frontend hot reload
  --no-rag   Skip Postgres; keyword retriever only (RAG_ENABLED=0 no .env recomendado)
  --o11y     OTel Collector (Docker) + opentelemetry-instrument (ver .env)

  Modo --o11y: o backend NÃO usa --reload — o worker filho do reload não herda
  auto-instrumentação LangChain/gen_ai.*. Após mudar código Python, pare (Ctrl+C)
  e rode ./scripts/dev.sh --o11y de novo. O frontend (next dev) continua com hot reload.
EOF
      exit 0
      ;;
    *)
      echo "unknown option: $1" >&2
      exit 2
      ;;
  esac
done

"$ROOT/scripts/setup-wizard.sh" --mode dev --if-needed
set -a
[ -f "$ROOT/.env" ] && . "$ROOT/.env"
set +a

# shellcheck disable=SC1091
. "$ROOT/scripts/lib/fresh-state.sh"
fresh_sqlite_host

if [ "$RAG" = "1" ]; then
  echo "→ postgres (profile rag — pgvector)"
  if [ "${RAG_ENABLED:-0}" = "1" ]; then
    fresh_rag_postgres -f "$ROOT/docker-compose.yml" --profile rag
  fi
  (cd "$ROOT" && docker compose --profile rag up -d postgres)
  "$ROOT/scripts/lib/rag-init.sh"
fi

if [ "$O11Y" = "1" ]; then
  echo "→ otel-collector (${OTEL_EXPORTER_OTLP_ENDPOINT:-http://localhost:4317}) → Splunk o11y (realm=${SPLUNK_O11Y_REALM:-us1})"
  (cd "$ROOT" && docker compose --profile o11y up -d --force-recreate otel-collector)
  export OTEL_SERVICE_NAME="${OTEL_SERVICE_NAME:-vega-concierge}"
  export OTEL_EXPORTER_OTLP_ENDPOINT="${OTEL_EXPORTER_OTLP_ENDPOINT:-http://localhost:4317}"
  export OTEL_RESOURCE_ATTRIBUTES="${OTEL_RESOURCE_ATTRIBUTES:-deployment.environment=${DEPLOYMENT_ENVIRONMENT:-dev},service.version=local}"
  export SPLUNK_PROFILER_ENABLED="${SPLUNK_PROFILER_ENABLED:-true}"
  export OTEL_PYTHON_LOGGING_AUTO_INSTRUMENTATION_ENABLED="${OTEL_PYTHON_LOGGING_AUTO_INSTRUMENTATION_ENABLED:-false}"
  export OTEL_PYTHON_LOG_CORRELATION="${OTEL_PYTHON_LOG_CORRELATION:-true}"
  export OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT="${OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT:-SPAN_AND_EVENT}"
  export OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT_MODE="${OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT_MODE:-SPAN_AND_EVENT}"
  export OTEL_INSTRUMENTATION_GENAI_EMITTERS="${OTEL_INSTRUMENTATION_GENAI_EMITTERS:-span_metric_event,splunk}"
  export OTEL_INSTRUMENTATION_GENAI_CONTEXT_PROPAGATION="${OTEL_INSTRUMENTATION_GENAI_CONTEXT_PROPAGATION:-true}"
  export OTEL_INSTRUMENTATION_GENAI_CAPTURE_TOOL_DEFINITIONS="${OTEL_INSTRUMENTATION_GENAI_CAPTURE_TOOL_DEFINITIONS:-true}"
  export OTEL_INSTRUMENTATION_GENAI_ROOT_SPAN_AS_WORKFLOW="${OTEL_INSTRUMENTATION_GENAI_ROOT_SPAN_AS_WORKFLOW:-true}"
  export OTEL_INSTRUMENTATION_GENAI_CONTEXT_INCLUDE_IN_METRICS="${OTEL_INSTRUMENTATION_GENAI_CONTEXT_INCLUDE_IN_METRICS:-gen_ai.conversation.id}"
  export SPLUNK_EVALUATION_RESULTS_MESSAGE_CONTENT="${SPLUNK_EVALUATION_RESULTS_MESSAGE_CONTENT:-true}"
  export OTEL_INSTRUMENTATION_GENAI_EVALS_EVALUATORS="${OTEL_INSTRUMENTATION_GENAI_EVALS_EVALUATORS:-Deepeval(LLMInvocation(bias,toxicity,hallucination,relevance,sentiment))}"
  export OTEL_INSTRUMENTATION_GENAI_EVALS_INTERVAL="${OTEL_INSTRUMENTATION_GENAI_EVALS_INTERVAL:-5.0}"
  export OTEL_INSTRUMENTATION_GENAI_EVALS_QUEUE_SIZE="${OTEL_INSTRUMENTATION_GENAI_EVALS_QUEUE_SIZE:-500}"
  export OTEL_INSTRUMENTATION_GENAI_EVALS_CONCURRENT="${OTEL_INSTRUMENTATION_GENAI_EVALS_CONCURRENT:-true}"
  export OTEL_INSTRUMENTATION_GENAI_EVALS_WORKERS="${OTEL_INSTRUMENTATION_GENAI_EVALS_WORKERS:-4}"
  export OTEL_INSTRUMENTATION_GENAI_EVALS_USE_SINGLE_METRIC="${OTEL_INSTRUMENTATION_GENAI_EVALS_USE_SINGLE_METRIC:-false}"
  export OTEL_INSTRUMENTATION_GENAI_EVALS_MONITORING="${OTEL_INSTRUMENTATION_GENAI_EVALS_MONITORING:-true}"
  export OTEL_INSTRUMENTATION_GENAI_EVALS_RESULTS_AGGREGATION="${OTEL_INSTRUMENTATION_GENAI_EVALS_RESULTS_AGGREGATION:-true}"
  export OTEL_INSTRUMENTATION_GENAI_EVALUATION_SAMPLE_RATE="${OTEL_INSTRUMENTATION_GENAI_EVALUATION_SAMPLE_RATE:-1.0}"
  export OTEL_INSTRUMENTATION_GENAI_EVALUATION_RATE_LIMIT_ENABLE="${OTEL_INSTRUMENTATION_GENAI_EVALUATION_RATE_LIMIT_ENABLE:-true}"
  export OTEL_INSTRUMENTATION_GENAI_EVALUATION_RATE_LIMIT_RPS="${OTEL_INSTRUMENTATION_GENAI_EVALUATION_RATE_LIMIT_RPS:-1}"
  export OTEL_INSTRUMENTATION_GENAI_EVALUATION_RATE_LIMIT_BURST="${OTEL_INSTRUMENTATION_GENAI_EVALUATION_RATE_LIMIT_BURST:-4}"
  export DEEPEVAL_EVALUATION_MODEL="${DEEPEVAL_EVALUATION_MODEL:-gpt-4o-mini}"
  export OPENAI_API_KEY="${OPENAI_API_KEY:-}"
  export DEEPEVAL_TELEMETRY_OPT_OUT="${DEEPEVAL_TELEMETRY_OPT_OUT:-1}"
  export OTEL_INSTRUMENTATION_GENAI_UPLOAD_HOOK="${OTEL_INSTRUMENTATION_GENAI_UPLOAD_HOOK:-}"
  export OTEL_INSTRUMENTATION_GENAI_UPLOAD_BASE_PATH="${OTEL_INSTRUMENTATION_GENAI_UPLOAD_BASE_PATH:-}"
  export OTEL_TRACES_EXPORTER="${OTEL_TRACES_EXPORTER:-otlp}"
  export OTEL_METRICS_EXPORTER="${OTEL_METRICS_EXPORTER:-otlp}"
  export OTEL_EXPORTER_OTLP_METRICS_TEMPORALITY_PREFERENCE="${OTEL_EXPORTER_OTLP_METRICS_TEMPORALITY_PREFERENCE:-delta}"
  export OTEL_LOGS_EXPORTER="${OTEL_LOGS_EXPORTER:-otlp}"
  export OTEL_EXPORTER_OTLP_PROTOCOL="${OTEL_EXPORTER_OTLP_PROTOCOL:-grpc}"
fi

if [ "$O11Y" = "1" ]; then
  echo "→ backend (opentelemetry-instrument uvicorn :8000, sem --reload)"
else
  echo "→ backend (uvicorn --reload :8000)"
fi
cd "$ROOT/backend"
python3 -m venv .venv 2>/dev/null || true
source .venv/bin/activate
pip install -q -r requirements.txt
if [ "$RAG" = "1" ] || [ "${RAG_ENABLED:-0}" = "1" ]; then
  pip install -q -r requirements-rag.txt
fi
if [ "$O11Y" = "1" ]; then
  pip install -q -r requirements-o11y.txt
  if ! pip check >/dev/null 2>&1; then
    echo "⚠ pip check reportou conflitos de o11y — veja: pip check" >&2
  fi
fi

DEPLOYMENT_ENVIRONMENT="${DEPLOYMENT_ENVIRONMENT:-dev}"
if [ "$O11Y" = "1" ]; then
  opentelemetry-instrument uvicorn app.api:app --port 8000 &
else
  uvicorn app.api:app --reload --port 8000 &
fi
BACK=$!

echo "→ frontend (next dev :3000)"
cd "$ROOT/frontend"
[ -d node_modules ] || npm install
npm run dev &
FRONT=$!

trap "kill $BACK $FRONT 2>/dev/null" EXIT
if [ "$O11Y" = "1" ]; then
  echo "→ http://localhost:3000  (API :8000, service=${OTEL_SERVICE_NAME}). Ctrl+C para parar."
  echo "  o11y: mudanças no backend exigem reinício (Ctrl+C → ./scripts/dev.sh --o11y). Frontend segue com hot reload."
  echo "  Spans: docker compose logs -f otel-collector  (dispare POST /api/run, não só /api/health)"
else
  echo "→ http://localhost:3000  (API em :8000). Ctrl+C para parar."
  if [ "$RAG" = "1" ]; then
    echo "  rag: pgvector default — use --no-rag p/ keyword-only"
  fi
fi
wait
