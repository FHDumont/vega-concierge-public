"""Problem-injection flags, controlled by the GUI 'Problem Panel'. No SSH needed."""
from dataclasses import dataclass, fields, asdict

@dataclass
class ProblemFlags:
    price_hallucination: bool = False   # Pricing inventa preço fora do catálogo
    fraud_false_positive: bool = False  # Fraude bloqueia pedido legítimo
    inventory_outage: bool = False      # check_inventory falha
    latency_spike: bool = False         # catálogo lento
    cost_spike: bool = False            # router faz rounds excessivos
    payment_outage: bool = False        # gateway de pagamento indisponível → checkout FAILED (F-016)
    payment_latency: bool = False       # gateway de pagamento lento (latência alta no checkout) (F-016)
    refund_false_denial: bool = False   # agente de elegibilidade NEGA um reembolso ELEGÍVEL (F-029)
    prompt_injection: bool = False      # agente ACEITA injeção de prompt (UC-4): cumpre override de preço/política
    active_scenario: str = ""           # preset UC ativo (uc-1..uc-5); vazio = nenhum — persiste até restart da VM

    def to_dict(self):
        return asdict(self)

# instância global única (1 usuário por VM -> sem concorrência)
FLAGS = ProblemFlags()

# Presets UC do workshop Splunk Agent Observability (F-GALILEO-3) — reset total + apply evita flags stale.
UC_PRESETS: dict[str, dict[str, bool]] = {
    "uc-1": {"price_hallucination": True},
    "uc-2": {"inventory_outage": True},
    "uc-3": {"refund_false_denial": True},
    "uc-4": {"prompt_injection": True},
    "uc-5": {"price_hallucination": True},
    "clear": {},
}


def apply_preset(preset_id: str) -> ProblemFlags:
    if preset_id not in UC_PRESETS:
        raise ValueError(f"unknown preset: {preset_id}")
    preset = UC_PRESETS[preset_id]
    for f in fields(ProblemFlags):
        if f.name == "active_scenario":
            continue
        setattr(FLAGS, f.name, preset.get(f.name, False))
    FLAGS.active_scenario = "" if preset_id == "clear" else preset_id
    return FLAGS
