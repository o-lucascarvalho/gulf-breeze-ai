from __future__ import annotations


class SemanticRetrievalError(RuntimeError):
    """Base error for semantic retrieval failures."""


class SemanticQueryValidationError(SemanticRetrievalError):
    """Raised when search request input is invalid."""


class SemanticEmbeddingValidationError(SemanticRetrievalError):
    """Raised when query embedding is malformed or incompatible."""
