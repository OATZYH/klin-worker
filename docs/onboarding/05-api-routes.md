# API Routes Guide

This file summarizes current route contracts for developers.

## Health

### `GET /health`

Returns startup check results and app status.

Key fields include:

- `status` (`ok` or `degraded`)
- `version`
- `services` (startup checks)
- `onboarding_status`
- `onboarding_seeded`

## Organize

### `POST /api/organize`

Request:

```json
{
  "file_paths": ["/absolute/path/to/file.pdf"],
  "force": false
}
```

Response shape:

```json
{
  "results": {
    "/absolute/path/to/file.pdf": {
      "file_id": "...",
      "analysis": { "suggested_names": ["name1.pdf", "name2.pdf"] },
      "categories": [
        { "category_id": "...", "name": "Finance & Invoices", "score": 91.2 }
      ],
      "error": null
    }
  }
}
```

Notes:

1. Scores are percentages in response.
2. Per-file failures are returned as `error` in result item.
3. Organize endpoint does not mutate filesystem.

### `POST /api/organize/apply`

Used to record user-confirmed rename/move decisions.

Request:

```json
{
  "file_id": "...",
  "selected_name": "new_name",
  "selected_category": {
    "id": "...",
    "name": "Finance & Invoices",
    "score": 91.2
  }
}
```

Response:

```json
{ "success": true }
```

## Summary

### `POST /api/summary`

Request:

```json
{
  "file_paths": ["/absolute/path/to/file.pdf"],
  "force": false
}
```

Response:

```json
{
  "summary": "## Overview ...",
  "suggested_title": "Summary - file",
  "processing_time_ms": 1234
}
```

### `POST /api/summary/stream`

Server-sent events stream for progressive rendering.

Events:

1. `meta`
2. `chunk`
3. `done`

## Settings

All routes under `/api/settings`.

## Categories

1. `GET /api/settings/categories`
2. `POST /api/settings/categories`
3. `GET /api/settings/categories/{category_id}`
4. `PATCH /api/settings/categories/{category_id}`
5. `DELETE /api/settings/categories/{category_id}`
6. `POST /api/settings/categories/batch`

Field conventions in request/response models:

- `enabled`
- `folder_path`

## Base path and onboarding

1. `GET /api/settings/default-base-path`
2. `PUT /api/settings/default-base-path`
3. `GET /api/settings/onboarding`

`PUT /default-base-path` also handles first-run category seeding when table is empty.

`GET /api/settings/onboarding` includes both onboarding phase and seed state:

- `status` (`pending` / `base_path_set` / `completed`)
- `onboarding_seeded` (`true`/`false`)
- `started_at`, `seeded_at`, `completed_at`
- `should_seed_defaults`

## Auto organize settings and watched folders

1. `GET /api/settings/auto-organize`
2. `PUT /api/settings/auto-organize`
3. `GET /api/settings/auto-organize/folders`
4. `POST /api/settings/auto-organize/folders`
5. `PATCH /api/settings/auto-organize/folders/{watcher_id}`
6. `DELETE /api/settings/auto-organize/folders/{watcher_id}`

## History

1. `GET /api/history`
2. `GET /api/history/file/{file_id}`
3. `POST /api/history/note`
4. `GET /api/history/list` (mock UI helper)

## Search

### `POST /api/search/files`

Current implementation returns mock search results used by frontend integration.

Continue with [06-organize-pipeline.md](./06-organize-pipeline.md).
