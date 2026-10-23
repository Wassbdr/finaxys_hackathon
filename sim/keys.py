"""make keys : crée .env depuis .env.example et y ajoute une clé HMAC par émetteur."""

from __future__ import annotations

from bus.message import env_key_name, generate_env_keys
from sim.config import ROOT, known_senders, load_config


def main() -> None:
    env = ROOT / ".env"
    text = env.read_text(encoding="utf-8") if env.exists() else (ROOT / ".env.example").read_text(encoding="utf-8")
    senders = [s for s in known_senders(load_config()) if env_key_name(s) + "=" not in text]
    if senders:
        text = text.rstrip("\n") + "\n\n# Clés HMAC par émetteur (générées)\n" + generate_env_keys(senders)
    env.write_text(text, encoding="utf-8")
    print(f".env : {len(senders)} clé(s) ajoutée(s)")


if __name__ == "__main__":
    main()
