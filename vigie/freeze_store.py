"""Checkpoint local public : écriture atomique avant toute autorisation."""
import hashlib
import json
import os
from pathlib import Path
import tempfile

MAX_CHECKPOINT_BYTES = 4 * 1024 * 1024


def fingerprint(profile):
    return hashlib.sha256(json.dumps(profile, sort_keys=True).encode()).hexdigest()


class FileCheckpoint:
    def __init__(self, path):
        self.path = Path(path)

    def load(self, profile):
        if self.path.stat().st_size > MAX_CHECKPOINT_BYTES:
            raise ValueError("Checkpoint trop volumineux")
        envelope = json.loads(self.path.read_text())
        body = envelope['body']
        if envelope['sha256'] != fingerprint(body) or body['profile'] != fingerprint(profile):
            raise ValueError("Checkpoint altéré ou profil différent")
        return body['state']

    def bootstrap(self, state, profile):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        os.close(fd)
        self.save(state, profile)

    def save(self, state, profile):
        body = {'profile': fingerprint(profile), 'state': state}
        raw = json.dumps({'body': body, 'sha256': fingerprint(body)}, ensure_ascii=False).encode()
        if len(raw) > MAX_CHECKPOINT_BYTES:
            raise ValueError("Checkpoint trop volumineux")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(prefix=self.path.name+'.', dir=self.path.parent)
        try:
            with os.fdopen(fd, 'wb') as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(name, self.path)
            directory = os.open(self.path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            Path(name).unlink(missing_ok=True)
