from __future__ import annotations

import ipaddress
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import time

from .state import write_text_atomic


def detect_local_ipv4() -> str:
    """Return the IPv4 address Windows would normally use for LAN traffic."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect(("8.8.8.8", 80))
            address = probe.getsockname()[0]
            if _is_usable_ipv4(address):
                return address
    except OSError:
        pass

    try:
        for address in socket.gethostbyname_ex(socket.gethostname())[2]:
            if _is_usable_ipv4(address):
                return address
    except OSError:
        pass

    return "127.0.0.1"


def detect_tailscale_ipv4(addresses: list[str] | None = None) -> str | None:
    """Return a Tailscale IPv4 only while the backend reports Running."""
    if addresses is None:
        status = _tailscale_cli_status()
        if status is None:
            return None
        backend_state, status_addresses = status
        if backend_state.casefold() != "running":
            return None
        candidates = status_addresses
    else:
        # Explicit candidates keep this helper deterministic for callers/tests.
        candidates = addresses

    for address in candidates:
        if is_tailscale_ipv4(address):
            return address
    return None


def find_tailscale_executable(gui: bool = False) -> Path | None:
    """Locate the Tailscale CLI or Windows tray application."""
    executable_name = "tailscale-ipn.exe" if gui else "tailscale.exe"
    if not gui:
        found = shutil.which("tailscale")
        if found:
            return Path(found)

    roots = (
        Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "Tailscale",
        Path(os.environ.get("ProgramFiles(x86)", "C:/Program Files (x86)")) / "Tailscale",
        Path(os.environ.get("LOCALAPPDATA", "")) / "Tailscale",
    )
    for root in roots:
        candidate = root / executable_name
        if candidate.is_file():
            return candidate
    return None


def open_tailscale_app() -> bool:
    """Open the Windows Tailscale client without showing a console window."""
    executable = find_tailscale_executable(gui=True)
    if executable is None:
        return False
    try:
        subprocess.Popen(
            [str(executable)],
            cwd=executable.parent,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            **_subprocess_window_kwargs(),
        )
    except OSError:
        return False
    return True


def activate_tailscale(timeout: float = 15.0, poll_interval: float = 0.5) -> tuple[str | None, str]:
    """Request Tailscale connectivity and wait for a usable VPN address."""
    existing_ip = detect_tailscale_ipv4()
    if existing_ip:
        return existing_ip, f"Tailscale ya estaba conectado: {existing_ip}"

    cli = find_tailscale_executable()
    if cli is None:
        return None, "Tailscale no esta instalado o no se encontro tailscale.exe."

    open_tailscale_app()
    try:
        result = subprocess.run(
            [str(cli), "up"],
            check=False,
            capture_output=True,
            text=True,
            timeout=max(timeout, 2.0),
            **_subprocess_window_kwargs(),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return None, f"No se pudo activar Tailscale: {exc}"

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        address = detect_tailscale_ipv4()
        if address:
            return address, f"Tailscale conectado: {address}"
        time.sleep(poll_interval)

    output = "\n".join(part.strip() for part in (result.stdout, result.stderr) if part.strip())
    detail = output or f"tailscale up termino con codigo {result.returncode}"
    return None, f"Tailscale no llego a conectarse. Revisa su ventana o inicia sesion. {detail}"


def is_tailscale_ipv4(address: str) -> bool:
    try:
        ip = ipaddress.ip_address(address.strip())
    except ValueError:
        return False
    return ip.version == 4 and ip in ipaddress.ip_network("100.64.0.0/10")


def resolve_server_ip_setting(setting: str | None, detected_ip: str | None = None) -> str | None:
    if setting is None:
        return None
    value = setting.strip()
    if value.lower() == "auto":
        return detected_ip or detect_local_ipv4()
    if value.lower() in {"blank", "empty", "vacio", "vacio", "none"}:
        return ""
    return value


def update_server_properties_ip_text(content: str, server_ip: str) -> tuple[str, bool]:
    replacement_done = False
    changed = False
    lines: list[str] = []
    for line in content.splitlines(keepends=True):
        raw = line.rstrip("\r\n")
        newline = line[len(raw) :]
        stripped = raw.lstrip()
        prefix = raw[: len(raw) - len(stripped)]
        if stripped.lower().startswith("server-ip="):
            replacement_done = True
            new_line = f"{prefix}server-ip={server_ip}{newline}"
            changed = changed or new_line != line
            lines.append(new_line)
        else:
            lines.append(line)

    if not replacement_done:
        if content and not content.endswith(("\n", "\r")):
            lines.append("\n")
        lines.append(f"server-ip={server_ip}\n")
        changed = True

    return "".join(lines), changed


def apply_server_ip_override(server_dir: Path, setting: str | None) -> str | None:
    server_ip = resolve_server_ip_setting(setting)
    if server_ip is None:
        return None
    properties_path = server_dir / "server.properties"
    try:
        content = properties_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return f"server.properties no existe; no se pudo aplicar server-ip={server_ip!r}."
    updated, changed = update_server_properties_ip_text(content, server_ip)
    if changed:
        write_text_atomic(properties_path, updated)
    return f"server.properties preparado: server-ip={server_ip or '(vacio)'}"


def read_minecraft_server_port(server_dir: Path) -> int | None:
    """Read server-port, returning the Minecraft default when properties exists."""
    properties_path = server_dir / "server.properties"
    try:
        content = properties_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.lower().startswith("server-port="):
            try:
                port = int(stripped.split("=", 1)[1].strip())
            except ValueError as exc:
                raise ValueError("server-port in server.properties must be an integer") from exc
            if not 1 <= port <= 65535:
                raise ValueError("server-port in server.properties must be between 1 and 65535")
            return port
    return 25565


def is_tcp_port_available(port: int) -> bool:
    """Return False when another local process is already listening on the port."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 0)
            probe.bind(("0.0.0.0", port))
    except OSError:
        return False
    return True


def _is_usable_ipv4(address: str) -> bool:
    return bool(address and not address.startswith("127.") and "." in address)


def _local_ipv4_candidates() -> list[str]:
    addresses: list[str] = []
    try:
        addresses.extend(socket.gethostbyname_ex(socket.gethostname())[2])
    except OSError:
        pass

    if _is_windows():
        script = "Get-NetIPAddress -AddressFamily IPv4 | Select-Object -ExpandProperty IPAddress"
        try:
            result = subprocess.run(
                ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
                check=False,
                capture_output=True,
                text=True,
                timeout=2,
                **_subprocess_window_kwargs(),
            )
        except (OSError, subprocess.TimeoutExpired):
            result = None
        if result is not None:
            addresses.extend(line.strip() for line in result.stdout.splitlines())
    return _unique_nonempty(addresses)


def _tailscale_cli_status() -> tuple[str, list[str]] | None:
    executable = find_tailscale_executable()
    if executable is None:
        return None
    try:
        result = subprocess.run(
            [str(executable), "status", "--json"],
            check=False,
            capture_output=True,
            text=True,
            timeout=2,
            **_subprocess_window_kwargs(),
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    return parse_tailscale_status(result.stdout)


def parse_tailscale_status(content: str) -> tuple[str, list[str]] | None:
    """Extract backend state and assigned addresses from `tailscale status --json`."""
    try:
        payload = json.loads(content)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(payload, dict):
        return None

    backend_state = payload.get("BackendState")
    addresses = payload.get("TailscaleIPs", [])
    if not isinstance(backend_state, str) or not isinstance(addresses, list):
        return None
    return backend_state, _unique_nonempty([value for value in addresses if isinstance(value, str)])


def _unique_nonempty(values: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        item = value.strip()
        if item and item not in seen:
            result.append(item)
            seen.add(item)
    return result


def _subprocess_window_kwargs() -> dict[str, int]:
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return {"creationflags": flags} if flags else {}


def _is_windows() -> bool:
    return os.name == "nt"
