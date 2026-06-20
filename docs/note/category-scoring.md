# Category Scoring Explained

In the Klin-Worker API, the `score` returned for a file's categories (e.g., `73.4` for "Travel") represents the system's **confidence percentage** that the file belongs to that category. 

This percentage is derived from the **semantic similarity** between the file's contents and the category's definition.

Here is the step-by-step process of how this score is calculated, along with the relevant code references:

## 1. Embedding Generation
First, the system generates an AI "embedding" (a mathematical vector representation) for both the file and the category. 
- For the category, it embeds the category's description.
- For the file, it generates an embedding using the filename and its AI-generated summary to capture its semantic meaning.

*Reference: `app/services/organize/classification_service.py`*
```python
    async def _get_file_embedding(
        self,
        file_path: str,
        summary: str | None = None,
    ) -> list[float] | None:
        """
        Get the embedding vector for a file.

        When an AI summary is available we embed
        ``"<filename> <extension>. <summary>"`` which captures the actual
        content semantics rather than just the filename.
        """
        # ...
        if summary:
            query_text = f"{p.stem} {p.suffix}. {summary}"
        else:
            query_text = f"{p.stem} {p.suffix}"
        
        vectors = await self._rag.embed_texts([query_text])
        # ...
```

## 2. Cosine Similarity Calculation
Once both embeddings are generated, the `ClassificationService` compares them using **cosine similarity**. This mathematical formula calculates how closely the two vectors align, producing a raw score between `0.0` (completely unrelated) and `1.0` (perfect match).

*Reference: `app/services/organize/classification_service.py`*
```python
    @staticmethod
    def _cosine_similarity(a: list[float], b: list[float]) -> float:
        """Compute cosine similarity between two vectors."""
        va = np.array(a, dtype=np.float32)
        vb = np.array(b, dtype=np.float32)
        dot = np.dot(va, vb)
        norm = np.linalg.norm(va) * np.linalg.norm(vb)
        if norm == 0:
            return 0.0
        return float(dot / norm)
```

## 3. Conversion to Percentage
Finally, when the `organize` pipeline prepares the response for the API, it takes this raw cosine score (0..1) and multiplies it by 100 to convert it into a human-readable percentage format (0..100).

*Reference: `app/services/organize/organize_pipeline.py`*
```python
def _build_category_responses(
    scores: list[dict[str, Any]],
) -> list[CategoryScoreResponse]:
    """Convert raw cosine scores to API percentage responses."""
    return [
        CategoryScoreResponse(
            category_id=str(score["category_id"]),
            name=str(score["name"]),
            score=round(float(score["score"]) * 100, 1),
        )
        for score in scores
    ]
```

## Summary
A score of `73.4` means that based on the cosine similarity of their vector embeddings, the file's contents align with the category's definition with a **73.4% confidence**.
