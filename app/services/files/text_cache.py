"""In-memory text cache bridging docling parse (Step 5) → summary (Step 6).

Entries are short-lived: set during file parsing, consumed and deleted
during summary generation within the same request lifecycle.
"""


class TextCache:
    """Lightweight key-value store for extracted file text."""

    def __init__(self) -> None:
        self._store: dict[str, str] = {}

    def set(self, filepath: str, text: str) -> None:
        self._store[filepath] = text

    def get(self, filepath: str) -> str | None:
        return self._store.get(filepath)

    def delete(self, filepath: str) -> None:
        self._store.pop(filepath, None)
