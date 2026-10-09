"""Which Claude model each job uses (2026-10-09). Set in ~/.exobrain/config.json under "models".

- sleep:   moving memories into the cortex. Done carefully: the owner wants this job done properly.
- summary: memory.md, the one-page reading of the cortex.
- screen:  sorting the receiving box during the day.
- ask:     `exobrain ask`.
Claude Code is given the model with --model. Prices per million tokens (input / output, 2026-10):
Opus 5.5 $4 / $20, Sonnet 5.5 $2 / $10, Haiku 5.5 $0.10 / $0.50.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .brain import Brain

DEFAULTS = {"sleep": "claude-sonnet-5-5", "summary": "claude-haiku-5-5", "screen": "claude-haiku-5-5",
            "ask": "claude-sonnet-5-5"}


def model_for(brain: Brain, job: str) -> str:
    cfg = brain.settings.home / "config.json"
    chosen = json.loads(cfg.read_text(encoding="utf-8")).get("models", {}) if cfg.exists() else {}
    return chosen.get(job) or DEFAULTS[job]
