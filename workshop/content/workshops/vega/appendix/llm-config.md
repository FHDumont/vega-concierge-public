+++
title     = "Configure the LLM"
linkTitle = "LLM cascade"
weight    = 1
hidden    = true
aliases   = ["/workshops/vega/07-llm-config/"]
+++

{{< lead >}}
Workshop AMIs seed **Ollama Local** on the host (`llama3.2` chat, `nomic-embed-text` embeddings). Owners can add cloud providers in a **cascade**: try in order, stub last — changes apply per call, no restart.
{{< /lead >}}

**Admin → Global Settings → LLM Providers** (`/admin/config`).

{{< step "Add a provider" "1" "4 min" >}}
**Add provider** → Type presets: **OpenAI · Claude · Grok · Groq · OpenRouter · Amazon Bedrock · Custom**. Paste API key, pick model, enable. Cloud tokens **never** belong in `.env` on workshop VMs.
{{< /step >}}

{{< step "Order the cascade" "2" "2 min" >}}
▲▼ reorder. Typical lab: Ollama first, cheap cloud second, stub always last implicitly.
{{< /step >}}

{{< step "Test it" "3" "2 min" >}}
**Test** on a row — one real call, latency + tokens or clean error.
{{< /step >}}

**Code:** `backend/app/llm.py`, `backend/app/llm_config.py`, `backend/app/rag.py`.

{{< checkpoint "You tested a provider; cascade falls back to stub on failure" >}}
