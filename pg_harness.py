"""Disposable PostgreSQL container harness for integration tests.

Starts the cached official PostgreSQL image as a one-shot container whose
data directory is a tmpfs mount (memory-only, disposed when the container
stops; never a named volume), publishes a random port bound to 127.0.0.1
only, and uses trust authentication so no password or secret exists.

Existing containers, images, databases, credentials, and volumes are never
touched: every command addresses only the uniquely named container created
by this harness. The image is never pulled (--pull never); if it is not in
the local cache the harness raises PostgresUnavailable with the exact
prerequisite command.
"""

import os
import shutil
import subprocess
import time
import uuid

import psycopg

POSTGRES_IMAGE = "docker.io/library/postgres:18-bookworm"
START_TIMEOUT_SECONDS = 60.0


class PostgresUnavailable(RuntimeError):
    """Raised when a disposable PostgreSQL container cannot be started."""


class TemporaryPostgres:
    """Context manager yielding a dsn for an isolated throwaway PostgreSQL."""

    def __init__(self, image: str = POSTGRES_IMAGE):
        self.image = image
        self.name = None
        self.dsn = None

    def start(self) -> "TemporaryPostgres":
        if shutil.which("docker") is None:
            raise PostgresUnavailable(
                "docker CLI not found; install docker to run the PostgreSQL "
                "integration tests"
            )
        self._require_cached_image()
        self.name = f"kanban-pg-test-{os.getpid()}-{uuid.uuid4().hex[:8]}"
        result = subprocess.run(
            [
                "docker", "run", "-d", "--rm", "--pull", "never",
                "--name", self.name,
                "--tmpfs", "/var/lib/postgresql:rw,mode=1777,size=512m",
                "-p", "127.0.0.1::5432",
                "-e", "POSTGRES_HOST_AUTH_METHOD=trust",
                self.image,
            ],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            self.name = None
            raise PostgresUnavailable(
                f"failed to start disposable PostgreSQL: {result.stderr.strip()}"
            )
        try:
            self.dsn = f"postgresql://postgres@127.0.0.1:{self._host_port()}/postgres"
            self._wait_until_ready()
        except Exception:
            self.stop()
            raise
        return self

    def stop(self) -> None:
        if self.name is not None:
            subprocess.run(
                ["docker", "stop", "--time", "5", self.name],
                capture_output=True,
                text=True,
            )
        self.name = None
        self.dsn = None

    def __enter__(self) -> "TemporaryPostgres":
        return self.start()

    def __exit__(self, *exc_info) -> bool:
        self.stop()
        return False

    def _require_cached_image(self) -> None:
        result = subprocess.run(
            ["docker", "image", "inspect", self.image],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise PostgresUnavailable(
                f"PostgreSQL image {self.image!r} is not in the local docker "
                f"cache; run: docker pull {self.image}"
            )

    def _host_port(self) -> int:
        result = subprocess.run(
            ["docker", "port", self.name, "5432/tcp"],
            capture_output=True,
            text=True,
        )
        mapping = result.stdout.strip()
        if result.returncode != 0 or ":" not in mapping:
            raise PostgresUnavailable(
                f"could not determine published port: {mapping or result.stderr.strip()}"
            )
        return int(mapping.rsplit(":", 1)[1])

    def _wait_until_ready(self) -> None:
        deadline = time.monotonic() + START_TIMEOUT_SECONDS
        last_error = "no connection attempt made"
        while time.monotonic() < deadline:
            if not self._is_running():
                raise PostgresUnavailable(
                    "disposable PostgreSQL container exited during startup; "
                    f"logs:\n{self._logs()}"
                )
            try:
                with psycopg.connect(self.dsn, connect_timeout=2) as conn:
                    conn.execute("SELECT 1")
                return
            except psycopg.OperationalError as exc:
                last_error = str(exc).strip()
                time.sleep(0.5)
        raise PostgresUnavailable(
            f"disposable PostgreSQL not ready within {START_TIMEOUT_SECONDS:.0f}s "
            f"(last error: {last_error}); logs:\n{self._logs()}"
        )

    def _is_running(self) -> bool:
        result = subprocess.run(
            ["docker", "inspect", "--format", "{{.State.Running}}", self.name],
            capture_output=True,
            text=True,
        )
        return result.stdout.strip() == "true"

    def _logs(self) -> str:
        result = subprocess.run(
            ["docker", "logs", "--tail", "50", self.name],
            capture_output=True,
            text=True,
        )
        return (result.stdout + result.stderr).strip()
