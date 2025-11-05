"""
Unified configuration loader for all JSON configuration files.

This module loads:
1. Codebook (data/codebook.json) - Field definitions and validation rules
2. Prompts (data/prompts.json) - LLM extraction prompts and field hints
3. Manual Seeds (data/manual_seeds.json) - Known websites and blurbs for projects

Usage:
    from app.config.config_loader import load_codebook, load_prompts, get_project_seed
    
    codebook = load_codebook()
    prompts = load_prompts()
    website, blurb = get_project_seed("cheqd")
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Any, Optional, Tuple
import json
import logging


# ---------- Defaults used if no codebook file exists ----------

# Valid status values (project lifecycle stage)
_DEFAULT_STATUS_ENUMS: List[str] = ["Announced", "Pilot", "Launched", "Discontinued"]

# Valid ternary (three-value) field values
_DEFAULT_TERNARY_ENUMS: List[str] = ["True", "False", "Failed to disclose"]

# Field names that contain year/date information
_DEFAULT_YEAR_FIELDS: List[str] = [
    "Announcement", "Launch",
    "Project Announcement Date",
    "Project Launch Date",
]

# Normalization maps: converts various input formats to canonical values
# Used for comparing AI-extracted data with manual data
_DEFAULT_NORMALIZE: Dict[str, Dict[str, str]] = {
    "status": {
        "announced": "Announced",
        "pilot": "Pilot",
        "piloting": "Pilot",
        "launched": "Launched",
        "live": "Launched",
        "ga": "Launched",  # General Availability
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

# Field name synonyms (alternative names for the same field)
# Used by extractors/mappers for fuzzy matching
_DEFAULT_FIELD_SYNONYMS: Dict[str, List[str]] = {
    "Product Name": ["Project Name", "Name", "Initiative", "Program"],
    "Website": ["URL", "Homepage"],
}


@dataclass
class Codebook:
    """
    Codebook dataclass containing all field definitions and normalization rules.
    
    Attributes:
        fields: List of field definitions from new codebook structure, each containing:
            - data_column: Field name (e.g., "Mission Statement")
            - response_type: Expected response type (e.g., "[text]", "[year]")
            - extraction_needed: Whether to extract this field (Y/N)
            - data_definition: LLM prompt rules / extraction guidance
            - source_column: Source URL column name (e.g., "Source Mission Statement")
            - source_needed: Whether to track source for this field (Y/N)
            - archived_column: Archived source column name
            - archive_needed: Whether to archive source (Y/N)
        status_enums: List of valid status values (e.g., ["Announced", "Pilot", ...])
        ternary_enums: List of valid ternary values (e.g., ["True", "False", "Failed to disclose"])
        year_fields: List of field names that contain year/date information
        normalize: Normalization maps for status and ternary fields
        field_synonyms: Alternative names for fields (for fuzzy matching)
    """
    fields: List[Dict[str, Any]] = field(default_factory=list)
    status_enums: List[str] = field(default_factory=lambda: list(_DEFAULT_STATUS_ENUMS))
    ternary_enums: List[str] = field(default_factory=lambda: list(_DEFAULT_TERNARY_ENUMS))
    year_fields: List[str] = field(default_factory=lambda: list(_DEFAULT_YEAR_FIELDS))
    normalize: Dict[str, Dict[str, str]] = field(default_factory=lambda: dict(_DEFAULT_NORMALIZE))
    field_synonyms: Dict[str, List[str]] = field(default_factory=lambda: dict(_DEFAULT_FIELD_SYNONYMS))

    @staticmethod
    def from_dict(d: Dict[str, Any]) -> "Codebook":
        """
        Create a Codebook instance from a dictionary (loaded from JSON).
        
        Supports both old format (status_enums, field_definitions) and new format (fields).
        
        Args:
            d: Dictionary with codebook data
        
        Returns:
            Codebook: New Codebook instance with data from dict
        """
        # New format (v2.0+) has 'fields' array
        if "fields" in d:
            return Codebook(
                fields=list(d.get("fields", [])),
                status_enums=list(d.get("status_enums", _DEFAULT_STATUS_ENUMS) or _DEFAULT_STATUS_ENUMS),
                ternary_enums=list(d.get("ternary_enums", _DEFAULT_TERNARY_ENUMS) or _DEFAULT_TERNARY_ENUMS),
                year_fields=list(d.get("year_fields", _DEFAULT_YEAR_FIELDS) or _DEFAULT_YEAR_FIELDS),
                normalize=dict(d.get("normalize", _DEFAULT_NORMALIZE) or _DEFAULT_NORMALIZE),
                field_synonyms=dict(d.get("field_synonyms", _DEFAULT_FIELD_SYNONYMS) or _DEFAULT_FIELD_SYNONYMS),
            )
        # Old format - convert to new format for backwards compatibility
        else:
            return Codebook(
                fields=[],  # Old format didn't have fields array
                status_enums=list(d.get("status_enums", _DEFAULT_STATUS_ENUMS) or _DEFAULT_STATUS_ENUMS),
                ternary_enums=list(d.get("ternary_enums", _DEFAULT_TERNARY_ENUMS) or _DEFAULT_TERNARY_ENUMS),
                year_fields=list(d.get("year_fields", _DEFAULT_YEAR_FIELDS) or _DEFAULT_YEAR_FIELDS),
                normalize=dict(d.get("normalize", _DEFAULT_NORMALIZE) or _DEFAULT_NORMALIZE),
                field_synonyms=dict(d.get("field_synonyms", _DEFAULT_FIELD_SYNONYMS) or _DEFAULT_FIELD_SYNONYMS),
            )

    def to_dict(self) -> Dict[str, Any]:
        """
        Convert Codebook instance to dictionary (for saving to JSON).
        
        Returns:
            Dict[str, Any]: Dictionary representation of codebook
        """
        return {
            "version": "2.0",
            "fields": self.fields,
            "status_enums": self.status_enums,
            "ternary_enums": self.ternary_enums,
            "year_fields": self.year_fields,
            "normalize": self.normalize,
            "field_synonyms": self.field_synonyms,
        }
    
    def get_field(self, data_column: str) -> Dict[str, Any] | None:
        """
        Get a field definition by data column name.
        
        Args:
            data_column: Field name (e.g., "Mission Statement")
        
        Returns:
            Dict with field definition or None if not found
        """
        for field in self.fields:
            if field.get("data_column") == data_column:
                return field
        return None
    
    def get_data_columns_needed(self) -> List[str]:
        """
        Get list of data columns that need extraction (extraction_needed == "Y").
        
        Returns:
            List of field names to extract
        """
        return [f.get("data_column") for f in self.fields if f.get("extraction_needed", "N") == "Y"]


# ---------- File helpers ----------

def _project_root() -> Path:
    """
    Get the project root directory.
    
    Calculates from config_loader.py location:
    src/app/config/config_loader.py -> project root is three parents up from src/
    
    Returns:
        Path: Project root directory
    """
    return Path(__file__).resolve().parents[3]

def codebook_path() -> Path:
    """
    Get the path to the codebook JSON file.
    
    Returns:
        Path: Path to data/codebook.json
    """
    return _project_root() / "data" / "codebook.json"


def _load_json(p: Path) -> Dict[str, Any]:
    """
    Load JSON file and parse to dictionary.
    
    Args:
        p: Path to JSON file
    
    Returns:
        Dict[str, Any]: Parsed JSON data (empty dict if file empty/invalid)
    """
    with p.open("r", encoding="utf-8") as f:
        return json.loads(f.read() or "{}")


def _write_json(p: Path, payload: Dict[str, Any]) -> None:
    """
    Write dictionary to JSON file with pretty formatting.
    
    Args:
        p: Path to write JSON file
        payload: Dictionary to serialize to JSON
    """
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)


# ---------- Public API ----------

def load_codebook() -> Codebook:
    """
    Load the codebook from data/codebook.json.
    
    If the file doesn't exist or can't be parsed, creates a default
    JSON file with default values and returns a default Codebook.
    
    Returns:
        Codebook: Loaded codebook instance
    
    Note:
        The codebook.json file is typically generated by running:
        python import_codebook_excel.py data/wot_data_definations.xlsx
    """
    jsn = codebook_path()

    # Try to load JSON if it exists
    if jsn.exists():
        try:
            data = _load_json(jsn)
            return Codebook.from_dict(data)
        except Exception:
            # Fall back to defaults on parse issues (corrupt JSON, etc.)
            pass

    # Create default JSON for the first run (if file missing)
    cb = Codebook()
    _write_json(jsn, cb.to_dict())
    return cb


def load_prompts() -> Dict[str, Any]:
    """
    Load field-specific prompts from data/prompts.json.
    
    Returns:
        Dict with prompt templates and field hints
    """
    prompts_path = _project_root() / "data" / "prompts.json"
    if prompts_path.exists():
        try:
            return _load_json(prompts_path)
        except Exception:
            pass
    
    # Return empty dict if file doesn't exist or can't be parsed
    return {}


def load_manual_seeds(seeds_file: Optional[Path] = None) -> Dict[str, Dict[str, str]]:
    """
    Load manual seeds (known websites and blurbs) from JSON file.
    
    Args:
        seeds_file: Path to manual_seeds.json file. If None, uses default location.
    
    Returns:
        Dict mapping project names to their seed data:
        {
            "project_name": {
                "website": "https://example.com",  # Optional
                "blurb": "Project description..."  # Optional
            }
        }
    """
    if seeds_file is None:
        seeds_file = _project_root() / "data" / "manual_seeds.json"
    
    if not seeds_file.exists():
        log = logging.getLogger(__name__)
        log.warning("[manual_seeds] File not found: %s, returning empty seeds", seeds_file)
        return {}
    
    try:
        data = _load_json(seeds_file)
        # Extract projects from JSON structure
        projects = data.get("projects", {})
        log = logging.getLogger(__name__)
        log.info("[manual_seeds] Loaded %d project seeds from %s", len(projects), seeds_file)
        return projects
    except Exception as e:
        log = logging.getLogger(__name__)
        log.error("[manual_seeds] Error loading %s: %s", seeds_file, e, exc_info=True)
        return {}


def get_project_seed(project_name: str, seeds_file: Optional[Path] = None) -> Tuple[str, str]:
    """
    Get seed data for a specific project.
    
    Args:
        project_name: Name of the project
        seeds_file: Path to manual_seeds.json file. If None, uses default location.
    
    Returns:
        Tuple of (website, blurb):
        - website: Known website URL (empty string if not found)
        - blurb: Project description/blurb (empty string if not found)
    """
    seeds = load_manual_seeds(seeds_file)
    project_data = seeds.get(project_name, {})
    
    website = project_data.get("website", "").strip()
    blurb = project_data.get("blurb", "").strip()
    
    return website, blurb
