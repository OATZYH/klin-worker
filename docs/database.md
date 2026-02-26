# Database Schema

Klin-Worker v0.2.0 uses a local **SQLite** database (`~/.klin/klin.db`) via SQLAlchemy async + aiosqlite.

All primary keys are **UUID v4** strings. Timestamps are **UTC ISO-8601**.

---

## ER Diagram

### Mermaid

```mermaid
erDiagram
    categories {
        String id PK
        Text name UK "NOT NULL"
        Text description "NOT NULL"
        String color "NOT NULL, #6366f1"
        Text destination_path "NULLABLE"
        Text embedding "NULLABLE, JSON float[]"
        Boolean is_active "NOT NULL, true"
        DateTime created_at "NOT NULL, UTC"
        DateTime updated_at "NOT NULL, UTC"
    }

    files {
        String id PK
        Text original_path UK "NOT NULL"
        String hash "NOT NULL, SHA-256"
        Integer size "NOT NULL"
        String extension "NOT NULL"
        DateTime created_at "NOT NULL, UTC"
    }

    file_analysis {
        String id PK
        String file_id FK "NOT NULL, UNIQUE"
        Text summary "NULLABLE"
        Text suggested_name "NULLABLE"
        DateTime processed_at "NOT NULL, UTC"
    }

    category_scores {
        String id PK
        String file_id FK "NOT NULL"
        String category_id FK "NOT NULL"
        Float score "NOT NULL, 0.0"
    }

    history_logs {
        String id PK
        String file_id FK "NOT NULL"
        String action "NOT NULL"
        Text metadata_json "NULLABLE, JSON"
        DateTime created_at "NOT NULL, UTC"
    }

    files ||--o| file_analysis : "has"
    files ||--o{ category_scores : "scored in"
    files ||--o{ history_logs : "tracked by"
    categories ||--o{ category_scores : "applied to"
```

### ASCII

```
┌──────────────┐       ┌─────────────────┐       ┌──────────────────┐
│  categories  │       │      files      │       │  file_analysis   │
├──────────────┤       ├─────────────────┤       ├──────────────────┤
│ id (PK)      │       │ id (PK)         │──1:1──│ file_id (FK)     │
│ name         │       │ original_path   │       │ id (PK)          │
│ description  │       │ hash            │       │ summary          │
│ color        │       │ size            │       │ suggested_name   │
│ dest_path    │       │ extension       │       │ processed_at     │
│ embedding    │       │ created_at      │       └──────────────────┘
│ is_active    │       └────────┬────────┘
│ created_at   │                │
│ updated_at   │                │ 1:N
└───────┬──────┘                │
        │              ┌───────────────────┐
        │   N:1        │  category_scores  │
        └──────────────│ category_id (FK)  │
                       │ file_id (FK)      │
                       │ id (PK)           │
                       │ score             │
                       └───────────────────┘

                       ┌───────────────────┐
          files 1:N    │   history_logs    │
          ─────────────│ file_id (FK)      │
                       │ id (PK)           │
                       │ action            │
                       │ metadata_json     │
                       │ created_at        │
                       └───────────────────┘
```

---

## Tables

### `categories`

User-defined classification buckets. Each category has an embedding vector used for cosine-similarity scoring against files.

| Column             | Type         | Constraints             | Default          | Description                                  |
| ------------------ | ------------ | ----------------------- | ---------------- | -------------------------------------------- |
| `id`               | `String`     | **PK**                  | UUID v4          | Unique identifier                            |
| `name`             | `Text`       | NOT NULL, UNIQUE        | —                | Display name (e.g. "Invoices")               |
| `description`      | `Text`       | NOT NULL                | `""`             | Human description used for embedding          |
| `color`            | `String(7)`  | NOT NULL                | `"#6366f1"`      | Hex color for UI display                     |
| `destination_path` | `Text`       | NULLABLE                | `NULL`           | Optional target folder for organized files   |
| `embedding`        | `Text`       | NULLABLE                | `NULL`           | JSON-serialised float list (768-dim vector)  |
| `is_active`        | `Boolean`    | NOT NULL                | `true`           | Soft-delete / disable toggle                 |
| `created_at`       | `DateTime`   | NOT NULL                | UTC now          | Row creation timestamp                       |
| `updated_at`       | `DateTime`   | NOT NULL                | UTC now          | Last modification timestamp (auto-updated)   |

**Relationships:**

- `scores` → `CategoryScore[]` (cascade delete)

---

### `files`

Scanned file metadata. One row per unique file path.

| Column          | Type         | Constraints             | Default  | Description                          |
| --------------- | ------------ | ----------------------- | -------- | ------------------------------------ |
| `id`            | `String`     | **PK**                  | UUID v4  | Unique identifier                    |
| `original_path` | `Text`       | NOT NULL, UNIQUE        | —        | Absolute path on disk                |
| `hash`          | `String(64)` | NOT NULL                | —        | SHA-256 content hash                 |
| `size`          | `Integer`    | NOT NULL                | —        | File size in bytes                   |
| `extension`     | `String(32)` | NOT NULL                | —        | File extension (e.g. `.pdf`, `.png`) |
| `created_at`    | `DateTime`   | NOT NULL                | UTC now  | Row creation timestamp               |

**Relationships:**

- `analysis` → `FileAnalysis` (1:1, cascade delete)
- `scores` → `CategoryScore[]` (1:N, cascade delete)
- `history` → `HistoryLog[]` (1:N, cascade delete)

---

### `file_analysis`

AI-generated summary and rename suggestion for a file. One row per file.

| Column           | Type       | Constraints                      | Default  | Description                              |
| ---------------- | ---------- | -------------------------------- | -------- | ---------------------------------------- |
| `id`             | `String`   | **PK**                           | UUID v4  | Unique identifier                        |
| `file_id`        | `String`   | **FK → files.id**, NOT NULL, UQ  | —        | Associated file                          |
| `summary`        | `Text`     | NULLABLE                         | `NULL`   | One-paragraph AI-generated summary       |
| `suggested_name` | `Text`     | NULLABLE                         | `NULL`   | AI-suggested descriptive filename        |
| `processed_at`   | `DateTime` | NOT NULL                         | UTC now  | When the analysis was generated          |

**Relationships:**

- `file` → `File` (N:1)

**On delete:** Cascades when parent `File` is deleted.

---

### `category_scores`

AI classification score for each file × category pair. Represents how well a file matches a category based on cosine similarity of embeddings.

| Column        | Type     | Constraints                    | Default  | Description                              |
| ------------- | -------- | ------------------------------ | -------- | ---------------------------------------- |
| `id`          | `String` | **PK**                         | UUID v4  | Unique identifier                        |
| `file_id`     | `String` | **FK → files.id**, NOT NULL    | —        | Associated file                          |
| `category_id` | `String` | **FK → categories.id**, NOT NULL | —      | Associated category                      |
| `score`       | `Float`  | NOT NULL                       | `0.0`    | Cosine similarity score (0.0 – 1.0)     |

**Relationships:**

- `file` → `File` (N:1)
- `category` → `Category` (N:1)

**On delete:** Cascades when parent `File` or `Category` is deleted.

---

### `history_logs`

Append-only audit trail of every action performed on a file.

| Column          | Type         | Constraints                  | Default  | Description                                  |
| --------------- | ------------ | ---------------------------- | -------- | -------------------------------------------- |
| `id`            | `String`     | **PK**                       | UUID v4  | Unique identifier                            |
| `file_id`       | `String`     | **FK → files.id**, NOT NULL  | —        | Associated file                              |
| `action`        | `String(64)` | NOT NULL                     | —        | Action type (e.g. `"organized"`, `"renamed"`) |
| `metadata_json` | `Text`       | NULLABLE                     | `NULL`   | Arbitrary JSON payload with action details   |
| `created_at`    | `DateTime`   | NOT NULL                     | UTC now  | When the action occurred                     |

**Relationships:**

- `file` → `File` (N:1)

**On delete:** Cascades when parent `File` is deleted.
