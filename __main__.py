"""Demo server: serve the HTTP API over the reference in-memory store.

Usage: python -m kanban_http [--host HOST] [--port PORT]

For local manual exploration only; data is lost on exit. This is not the
production adapter. Automated tests never open sockets.
"""

from __future__ import annotations

import argparse
from wsgiref.simple_server import make_server

from .app import create_app
from .memory import InMemoryCardStore


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="kanban_http",
        description="Kanban V1 HTTP API demo server (in-memory reference store)",
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8080)
    args = parser.parse_args(argv)
    app = create_app(InMemoryCardStore())
    with make_server(args.host, args.port, app) as server:
        print(
            f"kanban_http demo on http://{args.host}:{args.port} "
            "(reference in-memory store; demo only)"
        )
        server.serve_forever()


if __name__ == "__main__":
    main()
