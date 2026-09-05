from __future__ import annotations

from typing import Protocol


class EmbeddingProvider(Protocol):
    model_name: str

    def embed(self, texts: list[str], timeout: float) -> list[list[float]]:
        """Return one embedding per input text while preserving input order."""
