"""Typed results for document model loading."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class LoadedDocumentModel:
    """A validated model and its deterministic definition identity."""

    source_path: Path = field(repr=False)
    data: Mapping[str, object] = field(repr=False)
    normalized_data: Mapping[str, object] = field(repr=False)
    canonical_json: str = field(repr=False)
    definition_sha256: str
