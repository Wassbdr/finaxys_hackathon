"""Essais de l'équipe et de la couche 3, réseau interdit."""
import os
import sys


def deny_network(event, args):
    if event in {"socket.connect", "socket.connect_ex", "socket.getaddrinfo", "socket.bind"}:
        raise RuntimeError("Réseau interdit pendant pytest")


if __name__ == "__main__":
    os.environ["HORIZON_NO_LLM"] = "1"
    os.environ.pop('KRYS_LLM', None)
    os.environ.pop('MISTRAL_API_KEY', None)
    sys.addaudithook(deny_network)
    import pytest
    raise SystemExit(pytest.main(["tests", *sys.argv[1:]]))
