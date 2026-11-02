"""`make env`: every secret of .env.example is replaced, nothing is overwritten."""

import base64
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("new_env", ROOT / "scripts" / "new_env.py")
new_env = importlib.util.module_from_spec(spec)
spec.loader.exec_module(new_env)


def read_env(path: Path) -> dict[str, str]:
    lines = path.read_text(encoding="utf-8").splitlines()
    return dict(line.split("=", 1) for line in lines if line and not line.startswith("#"))


def test_every_secret_is_random_and_the_fernet_key_is_valid(tmp_path: Path) -> None:
    first, second = tmp_path / "first.env", tmp_path / "second.env"
    new_env.create_env(ROOT / ".env.example", first)
    new_env.create_env(ROOT / ".env.example", second)
    a, b = read_env(first), read_env(second)

    assert "change_me" not in first.read_text(encoding="utf-8")
    assert a.keys() == read_env(ROOT / ".env.example").keys()  # same variables, same order
    assert a["APP_DB_PASSWORD"] != b["APP_DB_PASSWORD"]  # random, not a fixed value
    assert len(base64.urlsafe_b64decode(a["AIRFLOW_FERNET_KEY"])) == 32
    assert a["AIRFLOW_FERNET_KEY"] != read_env(ROOT / ".env.example")["AIRFLOW_FERNET_KEY"]


def test_an_existing_env_is_never_overwritten(tmp_path: Path) -> None:
    target = tmp_path / ".env"
    target.write_text("APP_DB_PASSWORD=the_real_one\n", encoding="utf-8")
    with pytest.raises(FileExistsError):
        new_env.create_env(ROOT / ".env.example", target)
    assert target.read_text(encoding="utf-8") == "APP_DB_PASSWORD=the_real_one\n"
