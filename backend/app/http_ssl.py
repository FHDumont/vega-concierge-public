"""Platform TLS trust for outbound HTTP (LLM SDKs).

httpx (used by openai/anthropic/langchain) defaults to the certifi CA bundle. On networks
with SSL inspection (corporate proxy), endpoints like api.openai.com may present a local CA
that is in the OS trust store but not in certifi — Groq can still work when its chain uses a
public CA present in both. Use the platform trust store so Test/provider calls behave like curl.
"""
import ssl

import httpx


def ssl_context() -> ssl.SSLContext:
    return ssl.create_default_context()


def sync_http_client(timeout: float) -> httpx.Client:
    return httpx.Client(verify=ssl_context(), timeout=timeout)


def async_http_client(timeout: float) -> httpx.AsyncClient:
    return httpx.AsyncClient(verify=ssl_context(), timeout=timeout)
