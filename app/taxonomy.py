"""Fixed Hebrew category taxonomy (cuisines and meal types)."""

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml

TAXONOMY_PATH = Path(__file__).with_name("taxonomy.yaml")
OTHER = "אחר"


@dataclass(frozen=True)
class Taxonomy:
    cuisines: list[str]
    meal_types: list[str]


@lru_cache
def load_taxonomy(path: Path = TAXONOMY_PATH) -> Taxonomy:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return Taxonomy(cuisines=list(data["cuisines"]), meal_types=list(data["meal_types"]))
