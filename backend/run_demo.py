import os, sys
os.environ.setdefault("DEPLOYMENT_ENVIRONMENT", "user-42")
from app.agents import build_graph, _parse_json
from app.problems import FLAGS
from app.runnable_config import build_runnable_config, make_thread_id

# C5 — strip markdown fences from routing/JSON parse
_fence_sample = '```json\n{"next_agent":"complete"}\n```'
_parsed_fence = _parse_json(_fence_sample)
assert _parsed_fence == {"next_agent": "complete"}, f"C5 fence parse failed: {_parsed_fence}"
print("OK C5 _parse_json strips markdown fences", file=sys.stderr)

def run(label, request="a birthday gift under $300", **flags):
    for k, v in flags.items():
        setattr(FLAGS, k, v)
    print(f"\n===== RUN: {label} =====", file=sys.stderr)
    config = build_runnable_config(thread_id=make_thread_id(), feature="concierge")
    final = build_graph().invoke(
        {"request": request, "messages": [], "trace": []},
        config=config,
    )
    print("MESSAGES:", file=sys.stderr)
    for m in final.get("trace", []):
        print("  -", m, file=sys.stderr)
    print("SELECTED:", (final.get("selected") or {}).get("name"), file=sys.stderr)
    print("ANSWER:", final.get("answer"), file=sys.stderr)
    print("QUALITY:", final.get("quality"), file=sys.stderr)

    if label == "happy_path":
        cand_skus = {c["sku"] for c in final.get("candidates") or []}
        sel = final.get("selected")
        qual = final.get("quality") or {}
        assert qual.get("grounded") is True, f"happy_path: expected grounded=True, got {qual}"
        assert sel, "happy_path: expected selected product"
        assert sel.get("sku") in cand_skus, (
            f"happy_path: SKU {sel.get('sku')} not in candidates {sorted(cand_skus)}"
        )
    elif label == "price_hallucination":
        qual = final.get("quality") or {}
        assert qual.get("grounded") is False, (
            f"price_hallucination: expected grounded=False, got {qual}"
        )

    for k in flags:  # reset
        setattr(FLAGS, k, False)
    return final

run("happy_path")                                              # ReAct: search_catalog → get_price → answer
run("english_request", request="a birthday gift under $300")  # controle de idioma (en)
run("price_hallucination", price_hallucination=True)           # quote fora do catálogo → grounded=false
run("cost_spike", cost_spike=True)                             # agentes verbose (mais tokens)
