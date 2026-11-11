"""SIS-Web resolves to a private address (10.x), so from outside the
university it only answers through the UTH VPN. When it does not answer we
ask OpenVPN GUI to connect, which works without a prompt once the VPN
password has been saved in it ("Save password" in its login box)."""

from __future__ import annotations

import logging
import os
import socket
import subprocess
import time
from urllib.parse import urlparse

from .config import Settings

log = logging.getLogger(__name__)


class VpnError(Exception):
    pass


def reachable(url: str, timeout: float = 3.0) -> bool:
    parsed = urlparse(url)
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        with socket.create_connection((parsed.hostname, port), timeout=timeout):
            return True
    except OSError:
        return False


def _gui_running() -> bool:
    try:
        out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq openvpn-gui.exe", "/NH"],
                             capture_output=True, text=True, timeout=15).stdout
    except (OSError, subprocess.SubprocessError):
        return False
    return "openvpn-gui.exe" in out.lower()


def connect(settings: Settings) -> None:
    gui = settings.openvpn_gui
    if _gui_running():
        args = [str(gui), "--command", "connect", settings.vpn_profile]
    else:
        args = [str(gui), "--connect", settings.vpn_profile]
    log.info("asking OpenVPN GUI to connect %s", settings.vpn_profile)
    flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    # detached: the GUI has to outlive this server
    subprocess.Popen(args, creationflags=flags, close_fds=True)


def ensure(settings: Settings) -> None:
    """Make sure SIS-Web is reachable, bringing the VPN up if allowed."""
    if reachable(settings.base_url):
        return
    if not settings.auto_vpn or os.name != "nt" or not settings.openvpn_gui.exists():
        raise VpnError("SIS-Web is only reachable from the university network or through the UTH VPN; "
                       "connect the VPN and try again.")
    connect(settings)
    deadline = time.monotonic() + settings.vpn_wait
    while time.monotonic() < deadline:
        time.sleep(2)
        if reachable(settings.base_url, timeout=2):
            log.info("SIS-Web reachable through the VPN")
            return
    raise VpnError(f"asked OpenVPN to connect '{settings.vpn_profile}' but SIS-Web is still unreachable. "
                   "If OpenVPN is waiting for a password, log in once with 'Save password' ticked.")
