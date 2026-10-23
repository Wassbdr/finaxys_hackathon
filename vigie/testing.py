"""La suite du dépôt et celle de la Vigie, sans connexion réseau possible."""

import os
import sys


def deny_network(event, args):
    if event in {
        "socket.connect",
        "socket.connect_ex",
        "socket.getaddrinfo",
        "socket.bind",
    }:
        raise RuntimeError("Réseau interdit pendant pytest")


if __name__ == "__main__":
    os.environ["HORIZON_NO_LLM"] = "1"
    sys.addaudithook(deny_network)
    import pytest

    raise SystemExit(pytest.main(["tests", "vigie/tests", *sys.argv[1:]]))
