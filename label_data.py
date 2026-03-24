"""Shared helpers for label_nutrition.json matching."""
from __future__ import annotations

import json
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parent
LABEL_JSON = PACKAGE_ROOT / "label_nutrition.json"


def load_foods() -> list[dict]:
    data = json.loads(LABEL_JSON.read_text(encoding="utf-8"))
    return data["foods"]


def match_food(name: str, foods: list[dict] | None = None) -> dict | None:
    if foods is None:
        foods = load_foods()
    low = name.lower()
    best = None
    best_len = 0
    for spec in foods:
        keys = spec["match"]
        if all(k in low for k in keys):
            score = sum(len(k) for k in keys)
            if score > best_len:
                best = spec
                best_len = score
    return best
