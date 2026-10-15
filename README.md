# kanban-platform-integration

Integration worktree. Composes the approved HTTP API (`kanban_http/`, one
copy; see `API-SPEC.md`) with the approved PostgreSQL adapter (`kanban_pg.py`,
`schema.sql`). Neither approved source is modified; everything here is
additive glue.

## Added here

| Path | Role |
| --- | --- |
| `kanban_pg_adapter.py` | Adapts `kanban_pg.CardStore` to the HTTP `CardStore` contract from `kanban_http/store.py`: string ids <-> integer ids, `card_type` <-> `type`, card dataclass <-> dict, `KeyError` mapping, insertion-ordered dependency ids. |
| `kanban_pg_wsgi.py` | Production WSGI entry point. Reads the PostgreSQL DSN exclusively from the explicit `KANBAN_PG_DSN` environment variable (no default). |
| `tests/` | In-process WSGI integration tests over a disposable PostgreSQL container provided by `pg_harness.py`. |
| `README.md` | This file. |

## Run the server

```sh
KANBAN_PG_DSN=postgresql://user@host:5432/db .venv/bin/python -m kanban_pg_wsgi --host 127.0.0.1 --port 8080
```

or under any WSGI server:

```sh
KANBAN_PG_DSN=postgresql://user@host:5432/db gunicorn kanban_pg_wsgi:application
```

Missing or blank `KANBAN_PG_DSN` fails fast at import with `ConfigError`.

## Run the tests

```sh
.venv/bin/python -m unittest discover -s tests -t . -v
```

The integration tests start their own disposable PostgreSQL container through
`pg_harness.py` (cached `postgres:18-bookworm` image required; never touches
existing containers, databases, or volumes) and are skipped when docker or
the cached image is unavailable.
