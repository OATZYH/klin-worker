# Klin Worker — Manual Testing Guide

## Prerequisites

1. Server is running on `http://127.0.0.1:8000`
2. The LLM model is placed under `models/` and the path is configured
3. Use any HTTP client: **curl**, **Bruno** (`klin-bruno-api/`), or Postman

> **Tip:** Open the interactive docs at `http://127.0.0.1:8000/docs` for a clickable UI.

---

## 0 — Health Check

Verify the server is alive before doing anything else.

### Request
```http
GET http://127.0.0.1:8000/health
```

### Expected Response `200 OK`
```json
{
  "status": "ok"
}
```

---

## 1 — List Categories

Check what classification buckets exist. Organize won't be useful without at least one.

### Request
```http
GET http://127.0.0.1:8000/api/settings/categories
```

Optional query param: `?active_only=false` to include disabled categories.

### Expected Response `200 OK`
```json
[
  {
    "id": "abc123",
    "name": "Invoices",
    "description": "Tax invoices, receipts, and billing documents",
    "keywords_text": "invoice receipt tax billing payment",
    "color": "#6366f1",
    "destination_path": "/Users/you/Documents/Invoices",
    "is_default": false,
    "is_active": true,
    "created_at": "2026-03-01T10:00:00",
    "updated_at": "2026-03-01T10:00:00"
  }
]
```

If the list is **empty**, create categories first (see Section 2).

---

## 2 — Create a Category

### Request
```http
POST http://127.0.0.1:8000/api/settings/categories
Content-Type: application/json
```

### Body
```json
{
  "name": "Invoices",
  "description": "Tax invoices, receipts, and billing documents",
  "keywords_text": "invoice receipt tax billing VAT payment due amount",
  "color": "#22c55e",
  "destination_path": "/Users/you/Documents/Invoices"
}
```

#### Field Reference
| Field              | Required | Notes                                                                 |
|--------------------|----------|-----------------------------------------------------------------------|
| `name`             | ✅       | 1–100 chars, must be unique                                           |
| `description`      | ❌       | Default `""`. Used to build the category embedding.                   |
| `keywords_text`    | ❌       | Freeform EN/TH keywords. Boosts embedding quality — recommended.      |
| `color`            | ❌       | Hex color `#RRGGBB`. Default `#6366f1` (indigo). UI-only.            |
| `destination_path` | ❌       | Absolute path for the UI to use when moving files. Not used by worker.|

### Expected Response `201 Created`
```json
{
  "id": "uuid-v4-generated",
  "name": "Invoices",
  "description": "Tax invoices, receipts, and billing documents",
  "keywords_text": "invoice receipt tax billing VAT payment due amount",
  "color": "#22c55e",
  "destination_path": "/Users/you/Documents/Invoices",
  "is_default": false,
  "is_active": true,
  "created_at": "2026-03-02T08:00:00",
  "updated_at": "2026-03-02T08:00:00"
}
```

> Creating a category **immediately generates an embedding** for it.  
> The richer the `keywords_text`, the better the classification accuracy.

---

## 3 — Organize Files (Core Pipeline)

This is the main endpoint. It accepts a list of **absolute file paths** on the local machine.

### Request
```http
POST http://127.0.0.1:8000/api/organize
Content-Type: application/json
```

### Body (single file)
```json
{
  "filepaths": ["/Users/sarun/Downloads/invoice_jan_2026.pdf"]
}
```

### Body (multiple files)
```json
{
  "filepaths": [
    "/Users/sarun/Downloads/invoice_jan_2026.pdf",
    "/Users/sarun/Downloads/project_notes.txt",
    "/Users/sarun/Downloads/photo_beach.jpg"
  ]
}
```

> ⚠️ Paths must be **absolute**, exist on disk, and be readable.  
> The server does **not** upload files — it reads them directly by path.

---

### Expected Response `200 OK`

```json
{
  "results": [
    {
      "filepath": "/Users/sarun/Downloads/invoice_jan_2026.pdf",
      "file_id": "d2e8f1a3-...",
      "analysis": {
        "summary": "A tax invoice from Acme Corporation Ltd. dated January 15, 2026, totalling 15,000 THB for web development services.",
        "suggested_name": "2026_acme_invoice_jan_web_dev"
      },
      "categories": [
        { "category_id": "abc123", "name": "Invoices",   "score": 0.93 },
        { "category_id": "def456", "name": "Contracts",  "score": 0.41 },
        { "category_id": "ghi789", "name": "Code Projects", "score": 0.12 }
      ],
      "top_category": {
        "category_id": "abc123",
        "name": "Invoices",
        "score": 0.93,
        "destination_path": "/Users/you/Documents/Invoices"
      },
      "error": null
    }
  ]
}
```

#### Response Field Reference
| Field                          | Type    | Description                                          |
|--------------------------------|---------|------------------------------------------------------|
| `filepath`                     | string  | The path that was submitted                          |
| `file_id`                      | string  | UUID assigned to this file in the database           |
| `analysis.summary`             | string  | LLM-generated 1–3 sentence description              |
| `analysis.suggested_name`      | string  | Clean filename stem (no extension)                   |
| `categories[]`                 | array   | All categories scored, sorted by score desc          |
| `categories[].score`           | float   | 0.0–1.0 cosine similarity                            |
| `top_category`                 | object  | Highest-scoring category (null if no categories set) |
| `error`                        | string  | Non-null if this specific file failed                |

---

### Error Response (bad path)
```json
{
  "results": [
    {
      "filepath": "/Users/sarun/nonexistent.pdf",
      "file_id": "",
      "analysis": {},
      "categories": [],
      "top_category": null,
      "error": "File does not exist."
    }
  ]
}
```

The server returns `200` even when individual files fail — check each `error` field.

---

## 4 — Update a Category

Useful to adjust keywords and trigger an embedding regeneration.

### Request
```http
PATCH http://127.0.0.1:8000/api/settings/categories/{category_id}
Content-Type: application/json
```

### Body (partial update — any field is optional)
```json
{
  "keywords_text": "invoice receipt tax billing VAT payment due amount THB USD",
  "destination_path": "/Users/you/Finance/Invoices"
}
```

> Changing `name`, `description`, or `keywords_text` **automatically regenerates the embedding**.  
> Changing only `color`, `destination_path`, or `is_active` does **not** re-embed.

### Expected Response `200 OK`
Same shape as `CategoryResponse` (see Section 2).

---

## 5 — Check History

See what was organised and when.

### All recent events
```http
GET http://127.0.0.1:8000/api/history?limit=20
```

### Filter by action type
```http
GET http://127.0.0.1:8000/api/history?action=organized&limit=50
```

### History for a specific file
```http
GET http://127.0.0.1:8000/api/history/file/{file_id}
```

### Expected Response `200 OK`
```json
[
  {
    "id": "log-uuid",
    "file_id": "d2e8f1a3-...",
    "action": "organized",
    "metadata": {
      "top_category": "Invoices",
      "top_score": 0.93,
      "suggested_name": "2026_acme_invoice_jan_web_dev",
      "all_scores": [
        { "category_id": "abc123", "name": "Invoices",   "score": 0.93 },
        { "category_id": "def456", "name": "Contracts",  "score": 0.41 }
      ]
    },
    "created_at": "2026-03-02T09:15:32"
  }
]
```

---

## 6 — Delete a Category

```http
DELETE http://127.0.0.1:8000/api/settings/categories/{category_id}
```

### Expected Response `204 No Content`
(empty body)

---

## Quick Test Order

```
0. GET  /health                                       → {"status":"ok"}
1. GET  /api/settings/categories                       → see what exists
2. POST /api/settings/categories                       → create "Invoices", "Code Projects", etc.
3. POST /api/organize   {"filepaths":[…]}              → run the pipeline
4. GET  /api/history                                   → verify the log was written
```
