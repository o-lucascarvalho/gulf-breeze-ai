from __future__ import annotations

from dataclasses import dataclass


@dataclass
class EmbeddingRunReport:
    total_notes: int
    already_embedded: int
    selected: int
    processed: int
    skipped: int
    failed: int
    remaining: int
    api_batches: int
    api_inputs: int
    elapsed_seconds: float
