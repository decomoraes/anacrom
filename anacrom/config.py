"""Where the shard and the account come from.

Credentials are read from the environment first, then from a config file that
is created mode 0600.  The password is never echoed back by any command.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from .client import Config
from .protocol.packets import Profile

CONFIG_DIR = Path(os.environ.get("ANACROM_HOME", Path.home() / ".anacrom"))
CONFIG_PATH = CONFIG_DIR / "config.json"

DEFAULTS = {
    "host": "login.uoalive.com",
    "port": 2593,
    "account": "",
    "password": "",
    "character": None,
    "shard": None,
    "capture": None,
    "run_by_default": True,
    "mounted_speed": False,
    "uo_data": "",
    "high_seas": True,
    "container_grid_lines": True,
    "typesafe_api_key": "",
    "jev_model": "jev-latest",
}


def load_config() -> Config:
    data = dict(DEFAULTS)

    if CONFIG_PATH.exists():
        try:
            data.update(json.loads(CONFIG_PATH.read_text(encoding="utf-8")))
        except json.JSONDecodeError as exc:
            raise SystemExit(f"{CONFIG_PATH} is not valid JSON: {exc}")

    env = os.environ
    for key, name in (
        ("host", "ANACROM_HOST"),
        ("account", "ANACROM_ACCOUNT"),
        ("password", "ANACROM_PASSWORD"),
        ("character", "ANACROM_CHARACTER"),
        ("capture", "ANACROM_CAPTURE"),
        ("uo_data", "ANACROM_UO_DATA"),
    ):
        if env.get(name):
            data[key] = env[name]
    if env.get("ANACROM_PORT"):
        data["port"] = int(env["ANACROM_PORT"])

    return Config(
        host=data["host"],
        port=int(data["port"]),
        account=data["account"],
        password=data["password"],
        character=data["character"],
        shard=data["shard"],
        capture=data["capture"],
        run_by_default=bool(data["run_by_default"]),
        mounted_speed=bool(data["mounted_speed"]),
        uo_data=data["uo_data"] or None,
        profile=Profile(
            high_seas=bool(data["high_seas"]),
            container_grid_lines=bool(data["container_grid_lines"]),
        ),
    )


def load_jev_settings() -> tuple[str, str]:
    """The TypeSafe key and model, read fresh so ``uo set`` needs no restart.

    ``TYPESAFE_API_KEY`` is the name TypeSafe's own SDKs read, so a key already
    exported for them works here too.
    """
    data = dict(DEFAULTS)
    if CONFIG_PATH.exists():
        data.update(json.loads(CONFIG_PATH.read_text(encoding="utf-8")))
    key = os.environ.get("TYPESAFE_API_KEY") or data["typesafe_api_key"]
    model = os.environ.get("ANACROM_JEV_MODEL") or data["jev_model"]
    return key, model


def save_config(updates: dict) -> Path:
    data = dict(DEFAULTS)
    if CONFIG_PATH.exists():
        data.update(json.loads(CONFIG_PATH.read_text(encoding="utf-8")))
    data.update(updates)

    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    CONFIG_PATH.chmod(0o600)
    return CONFIG_PATH


def redacted() -> dict:
    config = load_config()
    jev_key, jev_model = load_jev_settings()
    return {
        "host": config.host,
        "port": config.port,
        "account": config.account or "(unset)",
        "password": "(set)" if config.password else "(unset)",
        "character": config.character or "(first character)",
        "capture": config.capture or "(off)",
        "uo_data": config.uo_data or "(unset: walking without a map)",
        "typesafe_api_key": "(set)" if jev_key else "(unset)",
        "jev_model": jev_model,
    }
