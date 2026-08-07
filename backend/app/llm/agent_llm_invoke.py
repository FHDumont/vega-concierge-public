"""Observable LLM invoke for isolated store agents (F-WORKSHOP-ISO).

Agents import this instead of hand-rolled OpenAI/Anthropic clients or ``SimpleChatModel``
wrappers that show ``*_local`` as the model name in Galileo and drop request ``config``.
"""
from __future__ import annotations

from .llm import LLMResult
from .llm_models import invoke_chat_cascade, is_stub_output

__all__ = ["LLMResult", "invoke_chat_cascade", "is_stub_output", "invoke_feature_llm"]


def invoke_feature_llm(
    agent_name: str,
    system: str,
    prompt: str,
    *,
    run_name: str,
    max_tokens: int = 256,
    config=None,
    verbose: bool = False,
) -> LLMResult:
    """Run the agent's provider cascade through LangChain with callbacks and real model ids."""
    return invoke_chat_cascade(
        agent_name,
        system,
        prompt,
        run_name=run_name,
        max_tokens=max_tokens,
        config=config,
        verbose=verbose,
    )
