FROM python:3.13-slim
WORKDIR /app
ARG VEGA_VERSION=dev
ARG VEGA_GIT_SHA=local
ARG VEGA_BUILD_DATE=
ENV VEGA_VERSION=${VEGA_VERSION} VEGA_GIT_SHA=${VEGA_GIT_SHA} VEGA_BUILD_DATE=${VEGA_BUILD_DATE}
# requirements-rag.txt vem embutido (como o -o11y) pra que `up.sh --rag` funcione na imagem
# publicada. As deps ficam inertes enquanto RAG_ENABLED=0 — o default é o retriever em processo.
COPY requirements.txt requirements-o11y.txt requirements-rag.txt ./
RUN pip install --no-cache-dir -r requirements.txt -r requirements-o11y.txt -r requirements-rag.txt
COPY . .
EXPOSE 8000
CMD ["uvicorn", "app.api:app", "--host", "0.0.0.0", "--port", "8000"]
