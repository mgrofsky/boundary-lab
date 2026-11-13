"""Turn text into lists of numbers, called embeddings, so the router can compare it."""

import importlib.util

import numpy as np

DEFAULT_MODEL = "sentence-transformers/all-MiniLM-L6-v2"


class MissingExtra(ImportError):
    """An embedder was asked for and the package it needs is not installed."""


def text(prompt: str, response: str) -> str:
    """Join a prompt and its answer so the model reads them together."""
    return f"User: {prompt}\nAssistant: {response}"


class Embedder:
    """Load the model when encode is first called, then reuse it."""

    name = "minilm"

    def __init__(self, model_name: str = DEFAULT_MODEL):
        # Checked here, before any work is done, and without loading the library.
        if importlib.util.find_spec("sentence_transformers") is None:
            raise MissingExtra(
                "the minilm embedder needs sentence-transformers, which is not installed. "
                'Install it with: pip install "redline[minilm]"'
            )
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


class TfidfEmbedder:
    """Score words by how often they appear, with no model to download.

    It must be fit on the training texts before encode. It needs nothing beyond
    scikit-learn and starts at once, but it only matches shared words, so it routes
    worse than the default model.
    """

    name = "tfidf"

    def __init__(self):
        self._vectorizer = None

    def fit(self, texts: list[str]) -> "TfidfEmbedder":
        """Learn the words and their weights from the training texts."""
        # Imported here so the default embedder never pays for it.
        from sklearn.feature_extraction.text import TfidfVectorizer

        try:
            self._vectorizer = TfidfVectorizer().fit(list(texts))
        except ValueError:
            # No text holds a word of two or more letters, so there is nothing to match
            # on. Every text then encodes to 0 and no text is closer to one than another.
            self._vectorizer = _NoWords()
        return self

    def encode(self, texts: list[str]) -> np.ndarray:
        """Return one row per text, each scaled to length 1. A text with no known word is all 0."""
        if self._vectorizer is None:
            raise RuntimeError("fit the tfidf embedder on the training texts before encode")
        vectors = self._vectorizer.transform(list(texts)).toarray().astype(np.float32)
        norms = np.linalg.norm(vectors, axis=1, keepdims=True)
        return np.divide(vectors, norms, out=np.zeros_like(vectors), where=norms > 0)


class _NoWords:
    """Stands in for a vectorizer that found no words: one column, always 0."""

    def transform(self, texts):
        from scipy.sparse import csr_matrix

        return csr_matrix((len(texts), 1), dtype=np.float32)


EMBEDDERS = {"minilm": Embedder, "tfidf": TfidfEmbedder}


def make(name: str):
    """Return a new embedder by name: minilm, the default model, or tfidf."""
    if name not in EMBEDDERS:
        raise ValueError(f"unknown embedder {name!r}, expected one of {', '.join(EMBEDDERS)}")
    return EMBEDDERS[name]()
