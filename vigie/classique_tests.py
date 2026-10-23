"""Suite complète de l'équipe et contrôles classiques, sans réseau."""
import os
import sys


def deny_network(event, args):
    if event in {'socket.connect', 'socket.connect_ex', 'socket.bind', 'socket.getaddrinfo'}:
        raise RuntimeError('Réseau interdit pendant pytest')


if __name__ == '__main__':
    os.environ['HORIZON_NO_LLM'] = '1'
    sys.addaudithook(deny_network)
    import pytest
    raise SystemExit(pytest.main(['tests', *sys.argv[1:]]))
