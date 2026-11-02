"""Create .env from .env.example, with a random value for every secret.

    make env        (or: python3 scripts/new_env.py)

Every `change_me...` value of .env.example becomes a random string, and the
Airflow Fernet key a new valid key. An existing .env is never overwritten:
it holds the passwords of databases that already exist.
Standard library only, so it runs before `make install`.
"""

import base64
import os
import re
import secrets
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def new_fernet_key() -> str:
    """32 random bytes, url-safe base64: the format Airflow requires."""
    return base64.urlsafe_b64encode(os.urandom(32)).decode()


def create_env(example: Path, target: Path) -> None:
    if target.exists():
        raise FileExistsError(f"{target} already exists: delete it first to start over")
    text = example.read_text(encoding="utf-8")
    # token_urlsafe: letters, digits, - and _ only, safe inside a connection URL.
    text = re.sub(r"=change_me\w*", lambda _: "=" + secrets.token_urlsafe(16), text)
    text = re.sub(
        r"^AIRFLOW_FERNET_KEY=.*$", "AIRFLOW_FERNET_KEY=" + new_fernet_key(), text, flags=re.M
    )
    target.write_text(text, encoding="utf-8")


def main() -> int:
    try:
        create_env(ROOT / ".env.example", ROOT / ".env")
    except FileExistsError as exc:
        print(exc, file=sys.stderr)
        return 1
    print(".env created with random secrets. Airflow login: see AIRFLOW_ADMIN_* in .env")
    return 0


if __name__ == "__main__":
    sys.exit(main())
