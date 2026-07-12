"""Typed results for document model loading."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class LoadedDocumentModel:
    """A validated model and its deterministic definition identity."""

    source_path: Path
    data: Mapping[str, object]
    normalized_data: Mapping[str, object]
    canonical_json: str
    definition_sha256: str
