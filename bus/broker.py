"""Fournit un Redis : réutilise celui qui répond, sinon lance redis-server, sinon fakeredis TCP.

Reste au premier plan (honcho arrête tout si un processus se termine).
"""

from __future__ import annotations

import os
import shutil
import signal
import sys
import time
from urllib.parse import urlparse

import redis

from sim.config import load_config


def redis_alive(url: str) -> bool:
    try:
        return bool(redis.Redis.from_url(url, socket_connect_timeout=0.5).ping())
    except redis.exceptions.RedisError:
        return False


def main() -> None:
    url = load_config()["redis"]["url"]
    parsed = urlparse(url)
    host, port = parsed.hostname or "127.0.0.1", parsed.port or 6379
    if redis_alive(url):
        print(f"[broker] Redis déjà disponible sur {url}", flush=True)
        signal.sigwait({signal.SIGINT, signal.SIGTERM})
        return
    binary = shutil.which("redis-server")
    if binary:
        print(f"[broker] lancement de redis-server sur le port {port}", flush=True)
        os.execv(binary, [binary, "--port", str(port), "--bind", host, "--save", "", "--appendonly", "no"])
    from fakeredis import TcpFakeServer

    print(f"[broker] redis-server absent : serveur fakeredis embarqué sur {host}:{port}", flush=True)
    server = TcpFakeServer((host, port), server_type="redis")
    server.daemon_threads = True
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        sys.exit(0)


if __name__ == "__main__":
    main()
