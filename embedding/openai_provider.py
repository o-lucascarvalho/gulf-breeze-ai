from __future__ import annotations

from openai import APIConnectionError, APIStatusError, APITimeoutError, InternalServerError, OpenAI, RateLimitError

from embedding.exceptions import EmbeddingValidationError, TransientEmbeddingProviderError


class OpenAIEmbeddingProvider:
    def __init__(self, api_key: str, model_name: str) -> None:
        if not api_key:
            raise EmbeddingValidationError("OPENAI_API_KEY is required for OpenAI embedding provider")
        self._client = OpenAI(api_key=api_key)
        self.model_name = model_name

    def embed(self, texts: list[str], timeout: float) -> list[list[float]]:
        if not texts:
            return []

        try:
            response = self._client.embeddings.create(
                model=self.model_name,
                input=texts,
                timeout=timeout,
            )
        except (APIConnectionError, APITimeoutError, InternalServerError, RateLimitError) as exc:
            raise TransientEmbeddingProviderError(str(exc)) from exc
        except APIStatusError as exc:
            if exc.status_code >= 500:
                raise TransientEmbeddingProviderError(str(exc)) from exc
            raise EmbeddingValidationError(f"OpenAI API rejected embedding request: {exc}") from exc

        if not hasattr(response, "data") or response.data is None:
            raise EmbeddingValidationError("Embedding provider response missing data")

        ordered_items = sorted(response.data, key=lambda item: getattr(item, "index", 0))
        vectors: list[list[float]] = []
        for item in ordered_items:
            embedding = getattr(item, "embedding", None)
            if not isinstance(embedding, list):
                raise EmbeddingValidationError("Embedding provider returned malformed embedding payload")
            vectors.append(embedding)

        if len(vectors) != len(texts):
            raise EmbeddingValidationError(
                f"Embedding count mismatch: expected {len(texts)}, got {len(vectors)}"
            )

        return vectors
