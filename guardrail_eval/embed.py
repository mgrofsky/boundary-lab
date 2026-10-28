"""Turn text into normalized vectors with a small sentence embedding model."""

import numpy as np

DEFAULT_MODEL = "sentence-transformers/all-MiniLM-L6-v2"


def text(prompt: str, response: str) -> str:
    """Join a prompt and its answer into the one string we embed."""
    return f"User: {prompt}\nAssistant: {response}"


class Embedder:
    """Wraps the embedding model. The model loads on the first call to encode."""

    def __init__(self, model_name: str = DEFAULT_MODEL):
        self.model_name = model_name
        self._model = None

    def encode(self, texts: list[str]) -> np.ndarray:
        """Return one row per text, each scaled to length 1."""
        if self._model is None:
            # Imported here because loading the library takes a few seconds.
            from sentence_transformers import SentenceTransformer

            self._model = SentenceTransformer(self.model_name)
        vectors = self._model.encode(list(texts), normalize_embeddings=True)
        return np.asarray(vectors, dtype=np.float32)
