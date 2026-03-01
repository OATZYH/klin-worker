# 🔄 The Full Organize Pipeline (Step-by-Step)

When the frontend sends `POST /api/organize` with `{"filepaths": ["/path/to/file.pdf"]}`:

```
Step 1 — SCAN
├── ScannerService.scan("/path/to/file.pdf")
├── Check security (not in /System, etc.)
├── Check file exists and is readable
├── Extract: name="file.pdf", ext=".pdf", size=1024000
└── Compute SHA-256 hash → "a1b2c3..."

Step 2 — UPSERT FILE RECORD
├── Check if file already exists in DB (by original_path)
├── If yes → update hash/size/extension
└── If no → create new File record

Step 3 — RAG INGEST
├── RagService.ingest("/path/to/file.pdf")
├── RAG-Anything parses the PDF
├── LLM extracts entities and relationships
└── Stored in local knowledge graph (.storage/rag_storage/)

Step 4 — AI SUMMARY
├── SummaryService.summarise("/path/to/file.pdf")
├── Query RAG for "Content of file file.pdf" → get context
├── Send context + prompt to llama-cpp-python (gemma-3-1b-it)
└── Returns: "This PDF contains a quarterly financial report..."

Step 5 — AI RENAME
├── RenameService.suggest_name("file.pdf", ".pdf", summary)
├── Send prompt to llama-cpp-python: "suggest a descriptive filename"
├── Sanitize LLM output with regex
└── Returns: "quarterly_financial_report.pdf"

Step 6 — STORE ANALYSIS
├── Create/update FileAnalysis record
└── Save summary + suggested_name to DB

Step 7 — AI CLASSIFY
├── ClassificationService.classify(file_id, path, db, summary=summary_text)
├── Embed the file: "file .pdf. <AI summary>" → [0.23, -0.15, ...]
│   (falls back to filename-only if no summary)
├── Load all category embeddings from DB
│   (each category was embedded from name + description + keywords_text)
├── For each category: cosine_similarity(file_vec, cat_vec)
├── Sort by score descending
├── Keep top 5 (classification_top_k)
├── Delete old scores for this file (upsert)
└── Save new CategoryScore records to DB

Step 8 — LOG HISTORY
├── HistoryService.log("organized", {top_category, score, ...})
└── Saved to history_logs table

RETURN — OrganizeResponse
├── filepath, file_id
├── analysis: {summary, suggested_name}
├── categories: [{category_id, name, score}, ...]
└── top_category: {category_id, name, score, destination_path}
```

---

## How AI Classification Actually Works

Let's walk through a concrete example:

### Setup: 12 categories are auto-seeded at boot

On first startup, `seed_service.py` inserts 12 default categories with rich keywords. For example:

```
"Finance & Invoices" (is_default=true)
→ Embed text: "Finance & Invoices. Financial records, transactions, billing
   documents, payment confirmations. invoice, receipt, billing statement,
   bank statement, tax document, ใบเสร็จ, ใบกำกับภาษี, .pdf .xlsx .csv"
→ Stored as [0.31, -0.22, 0.78, ...] (768 numbers) in DB
```

```
"Education & Learning" (is_default=true)
→ Embed text: "Education & Learning. Learning materials, course content,
   academic documents. lecture notes, study material, textbook, syllabus,
   โน้ตเรียน, หนังสือเรียน, งานวิจัย, วิทยานิพนธ์"
→ Stored as [0.12, 0.45, -0.33, ...] (768 numbers) in DB
```

You can also create custom categories via the API — they work the same way.

### Classify: You organize a file

```
POST /api/organize
{ "filepaths": ["/Users/you/Downloads/amazon_order_receipt.pdf"] }
```

**Classification step:**

```
1. AI Summary (Step 4): "This PDF is an Amazon order receipt for electronics..."

2. Embed the file: "amazon_order_receipt .pdf. This PDF is an Amazon order
   receipt for electronics..." → [0.29, -0.20, 0.75, ...]
   (summary enriches the embedding beyond just the filename!)

3. Compare to "Finance & Invoices" (embedded with keywords: invoice, receipt, ใบเสร็จ...):
   cosine_similarity([0.29, ...], [0.31, ...]) = 0.95  ✅ High!

4. Compare to "Education & Learning" (embedded with keywords: lecture, textbook...):
   cosine_similarity([0.29, ...], [0.12, ...]) = 0.23  ❌ Low

5. Compare to all other 10 categories...

6. Result: [{name: "Finance & Invoices", score: 0.95}, ...top 5 categories]
7. Top category: "Finance & Invoices"
```

### ✅ Summary-Enhanced Embeddings

File embedding now combines **filename + AI summary** when available:
```python
# classification_service.py
if summary:
    query_text = f"{p.stem} {p.suffix}. {summary}"  # rich semantic signal
else:
    query_text = f"{p.stem} {p.suffix}"              # filename-only fallback
```

Even a file named `document.pdf` gets classified well — the AI summary provides the semantic content.

**Fallback:** If the summary step fails (e.g. RAG not ready), it falls back to filename-only.

---

**Next:** [TODO / Improvements →](./07-todos.md)
