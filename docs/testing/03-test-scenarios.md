# Klin Worker — Test Scenarios

Concrete end-to-end scenarios with inputs, expected outputs, and what to verify.

---

## Setup: Recommended Category Set

Run these before any scenario. They give the classifier a broad range to work with.

```bash
BASE=http://127.0.0.1:8000

# 1. Invoices
curl -s -X POST $BASE/api/settings/categories \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Invoices",
    "description": "Tax invoices, receipts, payment documents",
    "keywords_text": "invoice receipt tax VAT billing total amount due payment",
    "color": "#22c55e",
    "destination_path": "/Users/you/Finance/Invoices"
  }' | jq .

# 2. Code Projects
curl -s -X POST $BASE/api/settings/categories \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Code Projects",
    "description": "Source code, scripts, and technical documentation",
    "keywords_text": "code python javascript typescript script function class import module",
    "color": "#3b82f6",
    "destination_path": "/Users/you/Code"
  }' | jq .

# 3. Photos
curl -s -X POST $BASE/api/settings/categories \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Photos",
    "description": "Personal photos and images",
    "keywords_text": "photo image picture jpeg png photo camera gallery",
    "color": "#f59e0b",
    "destination_path": "/Users/you/Photos"
  }' | jq .

# 4. Contracts
curl -s -X POST $BASE/api/settings/categories \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Contracts",
    "description": "Legal agreements, NDAs, employment contracts",
    "keywords_text": "contract agreement NDA terms conditions party signature legal clause",
    "color": "#ef4444",
    "destination_path": "/Users/you/Legal/Contracts"
  }' | jq .
```

---

## Scenario 1 — Single PDF Invoice

### Input
```json
POST /api/organize
{
  "filepaths": ["/Users/sarun/Downloads/invoice_jan_2026.pdf"]
}
```

File content (what the LLM will read): A PDF containing an invoice from Acme Corp., dated January 15, 2026, 15,000 THB for web development services.

### Expected Output
```json
{
  "results": [
    {
      "filepath": "/Users/sarun/Downloads/invoice_jan_2026.pdf",
      "file_id": "<some-uuid>",
      "analysis": {
        "summary": "Tax invoice from Acme Corporation for web development services rendered in January 2026, totalling 15,000 THB.",
        "suggested_name": "2026_acme_invoice_web_dev_jan"
      },
      "categories": [
        { "category_id": "...", "name": "Invoices",      "score": 0.88 },
        { "category_id": "...", "name": "Contracts",     "score": 0.45 },
        { "category_id": "...", "name": "Code Projects", "score": 0.09 },
        { "category_id": "...", "name": "Photos",        "score": 0.04 }
      ],
      "top_category": {
        "category_id": "...",
        "name": "Invoices",
        "score": 0.88,
        "destination_path": "/Users/you/Finance/Invoices"
      },
      "error": null
    }
  ]
}
```

### What to Verify
- [ ] `error` is `null`
- [ ] `analysis.summary` is non-empty and relevant to the file
- [ ] `analysis.suggested_name` has no spaces or extension, is descriptive
- [ ] `top_category.name` is `"Invoices"` (score should be highest)
- [ ] `categories` list is sorted descending by `score`
- [ ] History entry created: `GET /api/history?limit=1`

---

## Scenario 2 — Python Source File

### Input
```json
POST /api/organize
{
  "filepaths": ["/Users/sarun/Work/Senior/klin-worker/app/services/scanner_service.py"]
}
```

### Expected Output Highlights
```json
{
  "analysis": {
    "summary": "A Python service class that scans local files, extracting metadata such as file name, extension, size, and SHA-256 hash, with security and permission checks.",
    "suggested_name": "scanner_service_file_metadata"
  },
  "top_category": {
    "name": "Code Projects",
    "score": "~0.85+"
  }
}
```

### What to Verify
- [ ] `top_category.name` is `"Code Projects"`
- [ ] Summary mentions Python / code / scanning
- [ ] `suggested_name` does not have `.py` extension
- [ ] Re-organizing the same file updates the existing DB record (same `file_id`)

---

## Scenario 3 — Non-Existent File

### Input
```json
POST /api/organize
{
  "filepaths": ["/Users/sarun/Downloads/does_not_exist.pdf"]
}
```

### Expected Output
```json
{
  "results": [
    {
      "filepath": "/Users/sarun/Downloads/does_not_exist.pdf",
      "file_id": "",
      "analysis": { "summary": null, "suggested_name": null },
      "categories": [],
      "top_category": null,
      "error": "File does not exist."
    }
  ]
}
```

### What to Verify
- [ ] HTTP status is still `200` (not `404`)
- [ ] `error` field contains a human-readable message
- [ ] No history entry is created for this file

---

## Scenario 4 — Mixed Batch (good + bad files)

### Input
```json
POST /api/organize
{
  "filepaths": [
    "/Users/sarun/Downloads/invoice_jan_2026.pdf",
    "/tmp/ghost_file.docx"
  ]
}
```

### Expected Output
```json
{
  "results": [
    {
      "filepath": "/Users/sarun/Downloads/invoice_jan_2026.pdf",
      "error": null,
      "top_category": { "name": "Invoices" }
    },
    {
      "filepath": "/tmp/ghost_file.docx",
      "error": "File does not exist.",
      "top_category": null
    }
  ]
}
```

### What to Verify
- [ ] `results` array has exactly 2 items
- [ ] First result is successful, second has `error`
- [ ] Server returns `200` overall — per-file errors are isolated

---

## Scenario 5 — Re-Organizing Same File (Idempotency)

Run `/api/organize` on the same file **twice**.

### What to Verify
- [ ] `file_id` is the **same** in both responses (no duplicate DB rows)
- [ ] Summary and suggested_name are refreshed (may differ slightly due to LLM non-determinism)
- [ ] History has **two** entries for this `file_id` (`GET /api/history/file/{file_id}`)

---

## Scenario 6 — Category CRUD Cycle

Test that creating, updating, and deleting categories works consistently.

### Step-by-step

```bash
BASE=http://127.0.0.1:8000

# Create
ID=$(curl -s -X POST $BASE/api/settings/categories \
  -H "Content-Type: application/json" \
  -d '{"name":"Test Cat","description":"Temp test","keywords_text":"test temp demo"}' \
  | jq -r .id)

echo "Created: $ID"

# Read
curl -s $BASE/api/settings/categories/$ID | jq .

# Update — change description, triggers embedding regeneration
curl -s -X PATCH $BASE/api/settings/categories/$ID \
  -H "Content-Type: application/json" \
  -d '{"keywords_text":"updated keywords for better matching"}' | jq .

# Delete
curl -s -X DELETE $BASE/api/settings/categories/$ID -o /dev/null -w "%{http_code}\n"
# Expected: 204

# Verify gone
curl -s $BASE/api/settings/categories/$ID | jq .
# Expected: {"detail":"Category not found."}
```

---

## Scenario 7 — No Categories Exist

Delete all categories, then run organize.

### Input
```json
POST /api/organize
{
  "filepaths": ["/Users/sarun/Downloads/invoice_jan_2026.pdf"]
}
```

### Expected Output
```json
{
  "results": [
    {
      "filepath": "...",
      "analysis": {
        "summary": "...",
        "suggested_name": "..."
      },
      "categories": [],
      "top_category": null,
      "error": null
    }
  ]
}
```

### What to Verify
- [ ] `categories` is an empty array (not an error)
- [ ] `top_category` is `null`
- [ ] `analysis` still contains summary and suggested name (LLM runs regardless)

---

## History Query Reference

```bash
BASE=http://127.0.0.1:8000

# Recent 20 entries
curl -s "$BASE/api/history?limit=20" | jq .

# Only "organized" actions
curl -s "$BASE/api/history?action=organized&limit=50" | jq .

# History for a specific file
curl -s "$BASE/api/history/file/{file_id}" | jq .
```

Expected `action` values:
| Value        | When                              |
|--------------|-----------------------------------|
| `"organized"` | Every successful `/api/organize` call |

---

## Score Interpretation

| Score Range | Meaning                                        |
|-------------|------------------------------------------------|
| `0.85–1.00` | Very strong match — high confidence            |
| `0.65–0.85` | Good match — likely correct                    |
| `0.45–0.65` | Possible match — review manually               |
| `0.00–0.45` | Weak/unrelated — category probably not right   |

Scores are cosine similarity values between the file's summary embedding and the category embedding.  
They are **relative** — the highest score wins regardless of absolute value.
