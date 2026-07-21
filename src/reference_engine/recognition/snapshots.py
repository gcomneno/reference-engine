"""Privacy-safe immutable recognition input snapshots."""

from __future__ import annotations

import re
from typing import Any, cast

from reference_engine.recognition.canonical import string_digest
from reference_engine.recognition.types import (
    Availability,
    ProbeAcquisitionStatus,
    SafeDocumentInputSnapshot,
    SafeString,
    SafeTextProbeSnapshot,
    TechnicalDocumentInputs,
)


def project_safe_document_inputs(
    inputs: TechnicalDocumentInputs,
) -> SafeDocumentInputSnapshot:
    """Project all technical inputs without retaining sensitive string content."""

    fields: list[tuple[str, Any]] = []
    for name in (
        "mime_type",
        "original_filename",
        "byte_size",
        "source_url",
        "retrieved_at",
        "published_date",
        "page_count",
        "registered_at",
        "sha256",
    ):
        item = cast(Any, getattr(inputs, name))
        if item.availability is Availability.UNAVAILABLE:
            fields.append((name, {"availability": "unavailable"}))
            continue
        value = item.value
        if name == "mime_type" and isinstance(value, str):
            value = value.split(";", 1)[0].strip().lower()
        if name == "sha256" and isinstance(value, str):
            value = value.lower()
        if name == "original_filename" and isinstance(value, str):
            value = re.split(r"[/\\\\]", value)[-1]
        if name in {"original_filename", "source_url"} and isinstance(value, str):
            digest, length = string_digest(value)
            fields.append((name, SafeString(digest, length)))
        else:
            fields.append((name, value))

    acquisition = inputs.recognition_text_probe
    if acquisition.status is ProbeAcquisitionStatus.AVAILABLE_WITH_PROBE:
        probe = acquisition.probe
        if probe is None:
            raise ValueError("invalid recognition probe state")
        digest, length = string_digest(probe.text)
        probe_value: Any = SafeTextProbeSnapshot(
            digest, length, probe.limit, probe.truncated
        )
    else:
        probe_value = {"availability": "unavailable"}
    fields.append(("recognition_text_probe", probe_value))
    return SafeDocumentInputSnapshot(tuple(fields))
