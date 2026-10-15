"""Test-only WSGI runner for the browser acceptance test over disposable PostgreSQL.

Acceptance-test scaffolding for tests/browser_pg.test.mjs. It starts the
pg_harness.TemporaryPostgres disposable container (unique name, tmpfs data
directory, random port bound to 127.0.0.1, trust auth; existing containers,
images, databases, and volumes are never touched), then serves static/ plus
the production WSGI callable built by kanban_pg_wsgi (KANBAN_PG_DSN set to the
harness DSN) on a random loopback port.

Protocol with the Node test:
  stdout  "READY port=<n> dsn=<dsn> container=<name>" when serving
  stdout  "UNAVAILABLE <reason>" and exit 3 when PostgreSQL cannot start
  SIGTERM/SIGINT or stdin EOF stops the server and the container in a finally
  block (the Node side additionally removes the container by exact name as a
  defense in case this process is killed hard).

`read` mode performs an independent psycopg read of the same disposable
database (for post-UI persistence verification) and prints JSON to stdout.

This file is test scaffolding only; it changes no product code and is never
part of the shipped server.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import threading
from pathlib import Path
from socketserver import ThreadingMixIn
from urllib.parse import unquote
from wsgiref.simple_server import WSGIServer, make_server

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from pg_harness import PostgresUnavailable, TemporaryPostgres  # noqa: E402

API_PREFIXES = ("/cards", "/board", "/progress", "/health")
MIME_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".mjs": "text/javascript; charset=utf-8",
    ".svg": "image/svg+xml",
    ".json": "application/json",
    ".png": "image/png",
    ".ico": "image/x-icon",
}


class ThreadingWSGIServer(ThreadingMixIn, WSGIServer):
    daemon_threads = True
    allow_reuse_address = True


class StaticAndApiApp:
    """Serve static/ for GET paths outside the API, delegate the rest."""

    def __init__(self, static_dir: Path, api_app):
        self.static_dir = static_dir.resolve()
        self.api_app = api_app

    def __call__(self, environ, start_response):
        method = environ.get("REQUEST_METHOD", "GET").upper()
        path = environ.get("PATH_INFO", "/") or "/"
        if method == "GET" and not path.startswith(API_PREFIXES):
            static = self._serve_static(path, start_response)
            if static is not None:
                return static
        return self.api_app(environ, start_response)

    def _serve_static(self, path, start_response):
        relative = unquote(path.lstrip("/")) or "index.html"
        target = (self.static_dir / relative).resolve()
        try:
            target.relative_to(self.static_dir)
        except ValueError:
            return None
        if not target.is_file():
            return None
        body = target.read_bytes()
        content_type = MIME_TYPES.get(target.suffix, "application/octet-stream")
        start_response(
            "200 OK",
            [
                ("Content-Type", content_type),
                ("Content-Length", str(len(body))),
                ("Cache-Control", "no-store"),
            ],
        )
        return [body]


def _watch_stdin(stop: threading.Event) -> None:
    try:
        while sys.stdin.readline():
            pass
    except Exception:
        pass
    stop.set()


def serve(args) -> int:
    postgres = TemporaryPostgres()
    try:
        postgres.start()
    except PostgresUnavailable as exc:
        print(f"UNAVAILABLE {exc}", flush=True)
        return 3

    server = None
    api_app = None
    try:
        os.environ["KANBAN_PG_DSN"] = postgres.dsn
        import kanban_pg_wsgi  # production composition; requires the env var

        api_app = kanban_pg_wsgi.application
        application = StaticAndApiApp(Path(args.static_dir), api_app)
        server = make_server(
            "127.0.0.1", 0, application, server_class=ThreadingWSGIServer
        )
        stop = threading.Event()
        signal.signal(signal.SIGTERM, lambda *_: stop.set())
        signal.signal(signal.SIGINT, lambda *_: stop.set())
        threading.Thread(target=_watch_stdin, args=(stop,), daemon=True).start()

        port = server.server_address[1]
        print(
            f"READY port={port} dsn={postgres.dsn} container={postgres.name}",
            flush=True,
        )
        threading.Thread(target=server.serve_forever, daemon=True).start()
        stop.wait()
    finally:
        if server is not None:
            try:
                server.shutdown()
            finally:
                server.server_close()
        if api_app is not None:
            api_app.store.close()
        postgres.stop()
    return 0


def read_database(dsn: str) -> int:
    import psycopg
    from psycopg.rows import dict_row

    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        cards = conn.execute(
            "SELECT id, title, type, status, parent_id FROM cards ORDER BY id"
        ).fetchall()
        dependencies = conn.execute(
            "SELECT card_id, depends_on_card_id FROM card_dependencies "
            "ORDER BY card_id, depends_on_card_id"
        ).fetchall()
    payload = {
        "cards": [
            {
                "id": str(row["id"]),
                "title": row["title"],
                "card_type": row["type"],
                "status": row["status"],
                "parent_id": None if row["parent_id"] is None else str(row["parent_id"]),
            }
            for row in cards
        ],
        "dependencies": [
            {
                "card_id": str(row["card_id"]),
                "depends_on_card_id": str(row["depends_on_card_id"]),
            }
            for row in dependencies
        ],
    }
    print(json.dumps(payload))
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="browser_pg_wsgi",
        description="Test-only static+WSGI runner over disposable PostgreSQL",
    )
    sub = parser.add_subparsers(dest="mode", required=True)
    serve_parser = sub.add_parser("serve", help="serve static/ + kanban_pg_wsgi")
    serve_parser.add_argument(
        "--static-dir", default=str(REPO_ROOT / "static"), help="static asset root"
    )
    read_parser = sub.add_parser("read", help="independent SQL read for verification")
    read_parser.add_argument("--dsn", required=True)
    args = parser.parse_args(argv)
    if args.mode == "read":
        return read_database(args.dsn)
    return serve(args)


if __name__ == "__main__":
    raise SystemExit(main())
