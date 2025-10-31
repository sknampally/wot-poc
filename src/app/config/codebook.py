from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Any, Tuple, Optional
import json
import os

try:
    import yaml  # type: ignore
except Exception as e:  # pragma: no cover
    # Friendly guidance if PyYAML isn’t installed
    raise RuntimeError(
        "PyYAML is required. Try:\n"
        "  ./.venv/bin/python -m pip install pyyaml\n"
        "or add `PyYAML` to requirements.txt and reinstall."
    ) from e


# ---------- Defaults used if no codebook file exists ----------
_DEFAULT_STATUS_ENUMS: List[str] = ["Announced", "Pilot", "Launched", "Discontinued"]

_DEFAULT_TERNARY_ENUMS: List[str] = ["True", "False", "Failed to disclose"]

_DEFAULT_YEAR_FIELDS: List[str] = [
    "Announcement", "Launch",
    "Project Announcement Date",
    "Project Launch Date",
]

# Simple normalization maps (lowercased keys) -> canonical value
_DEFAULT_NORMALIZE: Dict[str, Dict[str, str]] = {
    "status": {
        "announced": "Announced",
        "pilot": "Pilot",
        "piloting": "Pilot",
        "launched": "Launched",
        "live": "Launched",
        "ga": "Launched",
        "discontinued": "Discontinued",
        "sunset": "Discontinued",
    },
    "ternary": {
        "true": "True",
        "yes": "True",
        "y": "True",
        "✅": "True",
        "false": "False",
        "no": "False",
        "n": "False",
        "❌": "False",
        "failed to disclose": "Failed to disclose",
        "unknown": "Failed to disclose",
        "n/a": "Failed to disclose",
        "na": "Failed to disclose",
    },
}

# Optional synonyms (used by extractors/mappers if you wire them)
_DEFAULT_FIELD_SYNONYMS: Dict[str, List[str]] = {
    "Product Name": ["Project Name", "Name", "Initiative", "Program"],
    "Website": ["URL", "Homepage"],
}


@dataclass
class Codebook:
    status_enums: List[str] = field(default_factory=lambda: list(_DEFAULT_STATUS_ENUMS))
    ternary_enums: List[str] = field(default_factory=lambda: list(_DEFAULT_TERNARY_ENUMS))
    year_fields: List[str] = field(default_factory=lambda: list(_DEFAULT_YEAR_FIELDS))
    normalize: Dict[str, Dict[str, str]] = field(default_factory=lambda: dict(_DEFAULT_NORMALIZE))
    field_synonyms: Dict[str, List[str]] = field(default_factory=lambda: dict(_DEFAULT_FIELD_SYNONYMS))

    @staticmethod
    def from_dict(d: Dict[str, Any]) -> "Codebook":
        return Codebook(
            status_enums=list(d.get("status_enums", _DEFAULT_STATUS_ENUMS) or _DEFAULT_STATUS_ENUMS),
            ternary_enums=list(d.get("ternary_enums", _DEFAULT_TERNARY_ENUMS) or _DEFAULT_TERNARY_ENUMS),
            year_fields=list(d.get("year_fields", _DEFAULT_YEAR_FIELDS) or _DEFAULT_YEAR_FIELDS),
            normalize=dict(d.get("normalize", _DEFAULT_NORMALIZE) or _DEFAULT_NORMALIZE),
            field_synonyms=dict(d.get("field_synonyms", _DEFAULT_FIELD_SYNONYMS) or _DEFAULT_FIELD_SYNONYMS),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status_enums": self.status_enums,
            "ternary_enums": self.ternary_enums,
            "year_fields": self.year_fields,
            "normalize": self.normalize,
            "field_synonyms": self.field_synonyms,
        }


# ---------- File helpers ----------

def _project_root() -> Path:
    # src/app/config/codebook.py -> project root is three parents up from src/
    return Path(__file__).resolve().parents[3]

def codebook_path() -> Path:
    # Store under <project>/data/codebook.yaml
    return _project_root() / "data" / "codebook.yaml"


def _load_yaml(p: Path) -> Dict[str, Any]:
    with p.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _load_json(p: Path) -> Dict[str, Any]:
    with p.open("r", encoding="utf-8") as f:
        return json.loads(f.read() or "{}")


def _write_yaml(p: Path, payload: Dict[str, Any]) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", encoding="utf-8") as f:
        yaml.safe_dump(payload, f, sort_keys=False, allow_unicode=True)


# ---------- Public API ----------

def load_codebook() -> Codebook:
    """
    Load the codebook from data/codebook.yaml (preferred) or data/codebook.json.
    If neither exists, write a default YAML file and return the defaults.
    """
    yml = codebook_path()
    jsn = yml.with_suffix(".json")

    if yml.exists():
        try:
            data = _load_yaml(yml)
            return Codebook.from_dict(data)
        except Exception:
            # fall back to defaults on parse issues
            pass

    if jsn.exists():
        try:
            data = _load_json(jsn)
            return Codebook.from_dict(data)
        except Exception:
            pass

    # Create default YAML for the first run
    cb = Codebook()
    _write_yaml(yml, cb.to_dict())
    return cb
