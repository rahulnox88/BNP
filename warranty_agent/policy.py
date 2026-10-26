from __future__ import annotations

import json
from pathlib import Path

from .domain import WarrantyPolicy


class PolicyRepository:
    """Loads a versioned business policy from a local JSON configuration."""

    def __init__(self, path: Path):
        self.path = path

    def load(self) -> WarrantyPolicy:
        with self.path.open(encoding="utf-8") as handle:
            return WarrantyPolicy.model_validate(json.load(handle))
