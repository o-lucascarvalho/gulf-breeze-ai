from __future__ import annotations


class EmbeddingPipelineError(RuntimeError):
    """Base error for embedding pipeline failures."""


class EmbeddingValidationError(EmbeddingPipelineError):
    """Raised when provider output is malformed or dimension-incompatible."""


class TransientEmbeddingProviderError(EmbeddingPipelineError):
    """Raised for transient provider/network errors that may be retried."""
