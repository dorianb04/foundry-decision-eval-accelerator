"""Cost model. Decision-1 rate is the one given for this experiment; LLM rates are public
Global Standard list prices as reported by third-party aggregators in Nov 2026 (azurespeed.com);
they are ASSUMPTIONS for estimation, nothing here is billed data."""

DECISION_IN_PER_M = 0.042  # USD per million input tokens, output free
LLM_RATES = {  # (input, output) USD per million tokens
    "gpt-5.6-sol": (4.00, 20.00),
    "gpt-5.4-mini": (0.75, 4.50),
}


def decision_cost(in_tokens: int, out_tokens: int = 0) -> float:
    return in_tokens * DECISION_IN_PER_M / 1e6


def llm_cost(deployment: str, in_tokens: int, out_tokens: int) -> float:
    rin, rout = LLM_RATES[deployment]
    return (in_tokens * rin + out_tokens * rout) / 1e6
