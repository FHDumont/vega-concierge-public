"""Problem-injection flags, controlled by the GUI 'Problem Panel'. No SSH needed."""
from dataclasses import dataclass, fields, asdict

@dataclass
class ProblemFlags:
    price_hallucination: bool = False   # Pricing invents price outside catalog
    fraud_false_positive: bool = False  # Fraud blocks legitimate order
    inventory_outage: bool = False      # check_inventory fails
    latency_spike: bool = False         # catalog slow
    cost_spike: bool = False            # router makes excessive rounds
    payment_outage: bool = False        # payment gateway unavailable → checkout FAILED (F-016)
    payment_latency: bool = False       # payment gateway slow (high latency on checkout) (F-016)
    refund_false_denial: bool = False   # eligibility agent DENIES an ELIGIBLE refund (F-029)
    prompt_injection: bool = False      # agent ACCEPTS prompt injection (UC-4): honors price/policy override
    active_scenario: str = ""           # active UC preset (uc-1..uc-5); empty = none — persists until VM restart

    def to_dict(self):
        return asdict(self)

# Global singleton instance (1 user per VM → no concurrency)
FLAGS = ProblemFlags()

# UC presets for Splunk Agent Observability workshop (F-GALILEO-3) — full reset + apply avoids stale flags.
UC_PRESETS: dict[str, dict[str, bool]] = {
    "uc-1": {"price_hallucination": True},
    "uc-2": {"cost_spike": True},
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
