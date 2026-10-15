"""Production WSGI entry point: Kanban V1 HTTP API over PostgreSQL.

The PostgreSQL DSN is read exclusively from the explicit KANBAN_PG_DSN
environment variable. There is no default and no credential is ever embedded;
a missing or blank variable fails fast at import time.

Serve it with any WSGI server, for example:

    KANBAN_PG_DSN=postgresql://user@host/db gunicorn kanban_pg_wsgi:application

or run it in the foreground for a local check:

    KANBAN_PG_DSN=postgresql://user@host/db python -m kanban_pg_wsgi

This module is additive integration glue: it composes the approved HTTP API
(kanban_http/, unmodified) with the approved PostgreSQL adapter (kanban_pg.py,
unmodified) through PgCardStoreAdapter.
"""

from __future__ import annotations

import argparse
import os
import sys
from typing import Optional
from wsgiref.simple_server import make_server

from kanban_http.app import create_app

from kanban_pg_adapter import PgCardStoreAdapter

DSN_ENV_VAR = "KANBAN_PG_DSN"


class ConfigError(RuntimeError):
    """Raised when required configuration (the PostgreSQL DSN) is absent."""


def load_dsn(environ=None) -> str:
    """Return the DSN from the explicit environment variable or raise."""
    source = os.environ if environ is None else environ
    dsn = source.get(DSN_ENV_VAR)
    if dsn is None or not dsn.strip():
        raise ConfigError(
            f"environment variable {DSN_ENV_VAR} is not set; it must contain "
            f"the PostgreSQL DSN (there is no default)"
        )
    return dsn


def create_application(dsn: Optional[str] = None):
    """Build the WSGI application over a PostgreSQL-backed CardStore."""
    if dsn is None:
        dsn = load_dsn()
    return create_app(PgCardStoreAdapter(dsn))


application = create_application()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="kanban_pg_wsgi",
        description="Kanban V1 HTTP API over PostgreSQL (WSGI, wsgiref runner)",
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8080)
    args = parser.parse_args(argv)
    try:
        load_dsn()
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    with make_server(args.host, args.port, application) as server:
        print(f"kanban_pg_wsgi on http://{args.host}:{args.port} (PostgreSQL)")
        server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
