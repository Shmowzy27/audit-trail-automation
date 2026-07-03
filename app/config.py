from __future__ import annotations

import json
import re
from pathlib import Path


def normalize(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.lower())


def load_config(path: str | Path) -> dict:
    with open(path, "r", encoding="utf-8") as handle:
        config = json.load(handle)
    if not config.get("properties"):
        raise ValueError("The config must contain at least one property.")
    return config


def property_config(config: dict, statement_property_name: str) -> dict:
    target = normalize(statement_property_name)
    matches = [
        details
        for configured_name, details in config["properties"].items()
        if normalize(configured_name) == target
    ]
    if len(matches) != 1:
        available = ", ".join(config["properties"])
        raise ValueError(
            f"No unique property configuration for '{statement_property_name}'. "
            f"Configured properties: {available}"
        )
    return matches[0]
