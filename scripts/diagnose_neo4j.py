#!/usr/bin/env python3
"""Quick Neo4j connectivity diagnostics for local development."""

from __future__ import annotations

import os
import socket
import ssl
import sys
from urllib.parse import urlparse

from dotenv import load_dotenv
from neo4j import GraphDatabase


def status_line(name: str, ok: bool, detail: str) -> None:
    prefix = "OK" if ok else "FAIL"
    print(f"[{prefix}] {name}: {detail}")


def main() -> int:
    load_dotenv()

    uri = os.getenv("NEO4J_URI", "")
    user = os.getenv("NEO4J_USERNAME", "")
    password = os.getenv("NEO4J_PASSWORD", "")
    database = os.getenv("NEO4J_DATABASE", "neo4j")

    print(f"Python: {sys.version.split()[0]}")
    print(f"OpenSSL: {ssl.OPENSSL_VERSION}")

    if not uri:
        status_line("Env", False, "NEO4J_URI missing")
        return 1

    parsed = urlparse(uri)
    host = parsed.hostname
    port = parsed.port or 7687

    if not host:
        status_line("URI", False, f"Invalid NEO4J_URI: {uri!r}")
        return 1

    status_line("Env", True, f"URI scheme={parsed.scheme}, host={host}, port={port}, database={database}")
    status_line("Env", bool(user), "NEO4J_USERNAME set" if user else "NEO4J_USERNAME missing")
    status_line("Env", bool(password), "NEO4J_PASSWORD set" if password else "NEO4J_PASSWORD missing")

    try:
        addresses = socket.getaddrinfo(host, port)
        sample = addresses[0][4]
        status_line("DNS", True, f"resolved {host} -> {sample}")
    except Exception as exc:  # noqa: BLE001
        status_line("DNS", False, f"{type(exc).__name__}: {exc}")
        return 2

    try:
        with socket.create_connection((host, port), timeout=5):
            pass
        status_line("TCP", True, f"connected to {host}:{port}")
    except Exception as exc:  # noqa: BLE001
        status_line("TCP", False, f"{type(exc).__name__}: {exc}")
        return 3

    driver = GraphDatabase.driver(uri, auth=(user, password))
    try:
        driver.verify_connectivity()
        status_line("Neo4j Handshake", True, "verify_connectivity succeeded")
    except Exception as exc:  # noqa: BLE001
        status_line("Neo4j Handshake", False, f"{type(exc).__name__}: {exc}")
        return 4

    try:
        with driver.session(database=database) as session:
            result = session.run("RETURN 1 AS ok").single()
            status_line("Query", True, f"RETURN 1 -> {result['ok']}")
    except Exception as exc:  # noqa: BLE001
        status_line("Query", False, f"{type(exc).__name__}: {exc}")
        return 5
    finally:
        driver.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
