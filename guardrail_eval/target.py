"""Send attack prompts to the model under test and save its answers."""

from guardrail_eval.judge import http_transport


class Target:
    """The chat model being attacked, reached through a /chat/completions style endpoint."""

    def __init__(self, base_url, model, api_key, system_prompt, transport=None):
        self.model = model
        self.system_prompt = system_prompt
        self.transport = transport or http_transport(base_url, api_key)

    def answer(self, prompt: str) -> str:
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": self.system_prompt},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0,
            "max_tokens": 400,
        }
        return self.transport(payload)


def run_attacks(prompts: list[dict], target: Target) -> list[dict]:
    """Send each attack prompt as an anonymous user and keep the answer."""
    return [
        {
            "id": p["id"],
            "kind": p["kind"],
            "target_rule_id": p["target_rule_id"],
            "user_type": "anonymous",
            "prompt": p["prompt"],
            "response": target.answer(p["prompt"]),
        }
        for p in prompts
    ]
