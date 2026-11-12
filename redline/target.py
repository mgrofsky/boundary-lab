"""Send attack prompts to the model under test and save its answers."""

from redline.judge import http_transport


class Target:
    """Send messages to the chatbot under test through /chat/completions."""

    def __init__(self, base_url, model, api_key, system_prompt, transport=None, temperature=0.0):
        self.model = model
        self.system_prompt = system_prompt
        self.temperature = temperature
        self.transport = transport or http_transport(base_url, api_key)

    def answer(self, prompt: str) -> str:
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": self.system_prompt},
                {"role": "user", "content": prompt},
            ],
            "temperature": self.temperature,
            "max_tokens": 400,
        }
        return self.transport(payload)


def run_attacks(prompts: list[dict], target: Target, attempts: int = 1) -> list[dict]:
    """Send each attack prompt as an anonymous user and keep the answer.

    With attempts above 1 each prompt is sent that many times. Rows gain attempt, counted
    from 1. Attempt 1 keeps the prompt's id and later ones add "#2", "#3" and so on.
    """
    rows = []
    for p in prompts:
        for attempt in range(1, attempts + 1):
            row = {
                "id": p["id"] if attempt == 1 else f"{p['id']}#{attempt}",
                "kind": p["kind"],
                "target_rule_id": p["target_rule_id"],
                "user_type": "anonymous",
                "prompt": p["prompt"],
                "response": target.answer(p["prompt"]),
            }
            if attempts > 1:
                row["attempt"] = attempt
            rows.append(row)
    return rows
