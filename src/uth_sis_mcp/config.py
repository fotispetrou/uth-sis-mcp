"""Settings come from environment variables, with the system keyring as a
fallback for the password so it does not have to live in a config file."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

DEFAULT_URL = "https://sis-web.uth.gr"
KEYRING_SERVICE = "uth-sis-mcp"
DEFAULT_OPENVPN_GUI = r"C:\Program Files\OpenVPN\bin\openvpn-gui.exe"
DEFAULT_VPN_PROFILE = "UTH Intranet"


class ConfigError(Exception):
    pass


def keyring_get(key: str) -> str | None:
    try:
        import keyring
    except ImportError:
        return None
    try:
        return keyring.get_password(KEYRING_SERVICE, key)
    except Exception:
        return None


@dataclass
class Settings:
    username: str
    password: str
    base_url: str = DEFAULT_URL
    profile: str | None = None
    auto_vpn: bool = True
    openvpn_gui: Path = Path(DEFAULT_OPENVPN_GUI)
    vpn_profile: str = DEFAULT_VPN_PROFILE
    vpn_wait: float = 45.0
    timeout: float = 30.0

    @classmethod
    def from_env(cls) -> "Settings":
        username = os.environ.get("UTHSIS_USERNAME") or keyring_get("username")
        password = os.environ.get("UTHSIS_PASSWORD")
        if not password and username:
            password = keyring_get(username)
        if not username or not password:
            raise ConfigError(
                "No SIS-Web credentials. Set UTHSIS_USERNAME and UTHSIS_PASSWORD, "
                "or run `uth-sis-mcp login` to store them in the system keyring."
            )
        return cls(
            username=username,
            password=password,
            base_url=os.environ.get("UTHSIS_URL", DEFAULT_URL).rstrip("/"),
            profile=os.environ.get("UTHSIS_PROFILE") or None,
            auto_vpn=os.environ.get("UTHSIS_AUTO_VPN", "1").strip().lower() not in ("0", "false", "no", "off"),
            openvpn_gui=Path(os.environ.get("UTHSIS_OPENVPN_GUI", DEFAULT_OPENVPN_GUI)),
            vpn_profile=os.environ.get("UTHSIS_VPN_PROFILE", DEFAULT_VPN_PROFILE),
            vpn_wait=float(os.environ.get("UTHSIS_VPN_WAIT", "45")),
            timeout=float(os.environ.get("UTHSIS_TIMEOUT", "30")),
        )
