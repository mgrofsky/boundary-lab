"""A garak probe that sends the prompts in a file you supply.

garak imports this module through the file that redline garak-install writes at
garak/probes/redline.py. It needs garak, so nothing else in Redline imports it.
"""

import json
from pathlib import Path

from garak import _config
from garak.exception import PluginConfigurationError
from garak.probes.base import Probe, Tier


def read_prompts(path) -> list[str]:
    """Read prompts from a file, in order.

    A file whose name ends in .jsonl holds one JSON object per line, and the prompt is its
    "prompt" field. Any other file holds one prompt per line. Blank lines are skipped.
    """
    path = Path(path)
    lines = enumerate(path.read_text(encoding="utf-8").splitlines(), start=1)
    lines = [(number, line) for number, line in lines if line.strip()]
    if path.suffix != ".jsonl":
        return [line.strip() for _, line in lines]
    prompts = []
    for number, line in lines:
        try:
            prompt = json.loads(line)["prompt"]
        except (json.JSONDecodeError, KeyError, TypeError):
            prompt = None
        if not isinstance(prompt, str) or not prompt.strip():
            raise ValueError(f'{path}: line {number} is not a JSON object with a "prompt" text')
        prompts.append(prompt)
    return prompts


class PromptFile(Probe):
    """Send each prompt in a file you supply

    Reads prompts from the file named by the prompt_file parameter and sends each one
    as written. A file ending in .jsonl holds one JSON object per line with a "prompt"
    field. Any other file holds one prompt per line. Use it to send prompts written
    against your own policy rules, and let the redline.PolicyRules detector check
    the answers. garak's prompt cap applies unless follow_prompt_cap is false.
    """

    # See the note on PolicyRules: garak finds a plugin by the module its class names.
    __module__ = "garak.probes.redline"

    DEFAULT_PARAMS = Probe.DEFAULT_PARAMS | {
        "prompt_file": None,  # path to a .jsonl or text file of prompts, required to run
    }

    lang = "en"
    active = False
    goal = "make the assistant break one of its written policy rules"
    intent = "T009ignore"
    doc_uri = "https://github.com/mgrofsky/boundary-lab/blob/main/docs/garak.md"
    tier = Tier.UNLISTED
    primary_detector = "redline.PolicyRules"

    def __init__(self, config_root=_config):
        super().__init__(config_root=config_root)
        self.prompts = []
        if self.prompt_file:
            try:
                self.prompts = read_prompts(self.prompt_file)
            except (OSError, ValueError) as exc:
                raise PluginConfigurationError(f"Could not read prompts: {exc}") from exc
            self._prune_data()

    def probe(self, generator):
        if not self.prompts:
            raise PluginConfigurationError(
                "The Redline probe has no prompts. Set the prompt_file parameter, for example "
                '--probe_options \'{"redline": {"PromptFile": {"prompt_file": "prompts.jsonl"}}}\''
            )
        return super().probe(generator)
