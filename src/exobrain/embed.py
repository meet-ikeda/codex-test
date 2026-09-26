"""Embeddings for semantic search, served by a local Ollama (spec v0.5 §8).

Vectors are derived data: they are cached in SQLite next to the projections and
can be rebuilt from the originals at any time. They never go into the event log.
If Ollama is not running, callers fall back to lexical search.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from array import array

TIMEOUT_SECONDS = 120
BATCH = 16


class EmbedUnavailable(RuntimeError):
    """Ollama is not reachable or the model is missing."""


class OllamaEmbedder:
    def __init__(self, model: str, url: str):
        self.model = model
        self.url = url.rstrip("/")

    def embed(self, texts: list[str]) -> list[list[float]]:
        out: list[list[float]] = []
        for i in range(0, len(texts), BATCH):
            out.extend(self._post(texts[i : i + BATCH]))
        return out

    def _post(self, texts: list[str]) -> list[list[float]]:
        req = urllib.request.Request(
            f"{self.url}/api/embed",
            data=json.dumps({"model": self.model, "input": texts}).encode(),
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as r:
                vectors = json.load(r)["embeddings"]
        except (urllib.error.URLError, OSError, KeyError, ValueError) as e:
            raise EmbedUnavailable(f"Ollama（{self.url}）で {self.model} を使えません: {e}") from e
        if len(vectors) != len(texts):
            raise EmbedUnavailable("埋め込みの件数が一致しません。")
        return vectors

    def status(self) -> tuple[bool, str]:
        try:
            with urllib.request.urlopen(f"{self.url}/api/tags", timeout=5) as r:
                names = {m["name"].split(":")[0] for m in json.load(r).get("models", [])}
        except (urllib.error.URLError, OSError, ValueError) as e:
            return False, f"Ollama に接続できません（{self.url}）: {e}"
        if self.model.split(":")[0] not in names:
            return False, f"Ollama にモデル {self.model} がありません（ollama pull {self.model}）"
        return True, f"Ollama・{self.model}"


def make_embedder(model: str, url: str) -> OllamaEmbedder | None:
    return OllamaEmbedder(model, url) if model else None


def pack(vec: list[float]) -> bytes:
    return array("f", vec).tobytes()


def unpack(blob: bytes) -> array:
    a = array("f")
    a.frombytes(blob)
    return a
