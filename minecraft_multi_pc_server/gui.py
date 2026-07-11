from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
import json
import os
from pathlib import Path
import queue
import socket
import subprocess
import sys
import threading
import tkinter as tk
import tomllib
import traceback
from tkinter import filedialog, messagebox, scrolledtext, ttk

from .archive import ArchiveError, resolve_latest_archive
from .config import Config, ConfigError, load_config, parse_config
from .lock import LockError, LockInfo
from .machine import activate_tailscale, detect_local_ipv4, detect_tailscale_ipv4
from .runner import LauncherError, ServerProcessControl, force_unlock, run_server, status, sync_up
from .state import StateError, write_text_atomic


COLORS = {
    "coal": "#101518",
    "stone": "#1B2428",
    "stone_light": "#253036",
    "line": "#3D4A4F",
    "redstone": "#54D17A",
    "network": "#51B9C5",
    "amber": "#F0B44D",
    "error": "#E15D5D",
    "paper": "#F4F7F2",
    "muted": "#9AA8A2",
    "ink": "#0B1113",
}

GUI_STATE_FILE = ".minecraft_launcher_gui_state.json"
GUI_LOG_FILE = "minecraft_launcher_gui.log"
DEFAULT_SYNC_IGNORE = [".tmp.drivedownload/**", ".tmp.driveupload/**", "logs/**", ".mixin.out/**"]
DEFAULT_START_COMMAND = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "start.ps1"]
DEFAULT_SERVER_IP = "blank"
DEFAULT_CONNECTION_MODE = "tailscale"


def lock_connection_detail(lock: LockInfo) -> str:
    detail = f"{lock.owner} tiene el servidor iniciado."
    if lock.connection_address:
        return f"{detail} Conectate a {lock.connection_address}"
    return f"{detail} Direccion no disponible en este lock antiguo."


class QueueWriter:
    def __init__(self, output_queue: queue.Queue[str]) -> None:
        self.output_queue = output_queue

    def write(self, text: str) -> int:
        if text:
            self.output_queue.put(text)
        return len(text)

    def flush(self) -> None:
        return None


def app_root_dir() -> Path:
    if getattr(sys, "frozen", False):
        executable_dir = Path(sys.executable).resolve().parent
        if executable_dir.name.lower() == "dist":
            return executable_dir.parent
        return executable_dir
    return Path(__file__).resolve().parents[1]


def gui_state_path(root: Path) -> Path:
    return root / GUI_STATE_FILE


def initial_config_path(root: Path) -> Path:
    state = gui_state_path(root)
    try:
        payload = json.loads(state.read_text(encoding="utf-8"))
        remembered = payload.get("last_config_path") if isinstance(payload, dict) else None
    except (FileNotFoundError, OSError, json.JSONDecodeError, TypeError):
        remembered = None
    if isinstance(remembered, str) and remembered and Path(remembered).exists():
        return Path(remembered)
    return root / "config.toml"


def save_gui_state(root: Path, config_path: Path) -> None:
    write_text_atomic(
        gui_state_path(root),
        json.dumps({"last_config_path": str(config_path.resolve())}, indent=2) + "\n",
    )


def default_drive_dir(root: Path) -> Path:
    return root / "minecraft-servers"


def default_local_server_dir(root: Path) -> Path:
    return root / "server_temp"


def default_state_dir(root: Path) -> Path:
    return default_drive_dir(root) / ".minecraft_multi_pc_state"


def drive_checkpoints_enabled() -> bool:
    return os.environ.get("MINECRAFT_LAUNCHER_SKIP_DRIVE_CHECKPOINT", "").strip() != "1"


def drive_sync_target(config: Config) -> Path | None:
    if config.remote_archive_dir is not None:
        return config.remote_archive_dir
    if config.remote_archive_file is not None:
        return config.remote_archive_file.parent
    if config.remote_server_dir is not None:
        return config.remote_server_dir
    return None


def open_drive_sync_target(target: Path) -> None:
    if os.name == "nt":
        os.startfile(target)  # type: ignore[attr-defined]
        return
    subprocess.Popen(["xdg-open", str(target)])


def find_google_drive_launcher() -> Path | None:
    if os.name != "nt":
        return None
    candidates: list[Path] = []
    for env_name in ("ProgramFiles", "ProgramFiles(x86)"):
        root_value = os.environ.get(env_name)
        if not root_value:
            continue
        drive_root = Path(root_value) / "Google" / "Drive File Stream"
        candidates.append(drive_root / "launch.bat")
        candidates.extend(sorted(drive_root.glob("*/GoogleDriveFS.exe"), reverse=True))
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def open_google_drive_app() -> bool:
    launcher = find_google_drive_launcher()
    if launcher is None:
        return False
    kwargs = {}
    if hasattr(subprocess, "CREATE_NO_WINDOW"):
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    if launcher.suffix.lower() == ".bat":
        subprocess.Popen(["cmd", "/c", str(launcher)], cwd=launcher.parent, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **kwargs)
    else:
        subprocess.Popen([str(launcher)], cwd=launcher.parent, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **kwargs)
    return True


def focus_google_drive_app_window() -> None:
    if os.name != "nt":
        return
    script = r"""
Add-Type @"
using System;
using System.Runtime.InteropServices;
public static class Win32 {
    [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr hWnd, int nCmdShow);
    [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr hWnd);
    [DllImport("user32.dll")] public static extern bool SetWindowPos(IntPtr hWnd, IntPtr hWndInsertAfter, int X, int Y, int cx, int cy, uint uFlags);
}
"@
$topMost = New-Object IntPtr -ArgumentList -1
$notTopMost = New-Object IntPtr -ArgumentList -2
$flags = 0x0001 -bor 0x0002 -bor 0x0040
for ($i = 0; $i -lt 20; $i++) {
    $process = Get-Process -Name GoogleDriveFS -ErrorAction SilentlyContinue |
        Where-Object { $_.MainWindowHandle -ne 0 } |
        Select-Object -First 1
    if ($process) {
        $hwnd = $process.MainWindowHandle
        [void][Win32]::ShowWindow($hwnd, 9)
        [void][Win32]::SetWindowPos($hwnd, $topMost, 0, 0, 0, 0, $flags)
        [void][Win32]::SetWindowPos($hwnd, $notTopMost, 0, 0, 0, 0, $flags)
        [void][Win32]::SetForegroundWindow($hwnd)
        exit 0
    }
    Start-Sleep -Milliseconds 250
}
"""
    kwargs = {}
    if hasattr(subprocess, "CREATE_NO_WINDOW"):
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        **kwargs,
    )


def close_google_drive_app_window() -> None:
    if os.name != "nt":
        return
    script = r"""
Get-Process -Name GoogleDriveFS -ErrorAction SilentlyContinue |
    Where-Object { $_.MainWindowHandle -ne 0 } |
    ForEach-Object { [void]$_.CloseMainWindow() }
"""
    kwargs = {}
    if hasattr(subprocess, "CREATE_NO_WINDOW"):
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        **kwargs,
    )


def close_drive_sync_target(target: Path) -> None:
    if os.name != "nt":
        return
    script = r"""
$resolved = Resolve-Path -LiteralPath $args[0] -ErrorAction SilentlyContinue
if (-not $resolved) { exit 0 }
$target = $resolved.Path.TrimEnd("\")
$shell = New-Object -ComObject Shell.Application
foreach ($window in @($shell.Windows())) {
    try {
        $url = [string]$window.LocationURL
        if (-not $url) { continue }
        $local = ([System.Uri]$url).LocalPath.TrimEnd("\")
        if ([System.String]::Equals($local, $target, [System.StringComparison]::OrdinalIgnoreCase) -or
            $local.StartsWith($target + "\", [System.StringComparison]::OrdinalIgnoreCase)) {
            $window.Quit()
        }
    } catch {}
}
"""
    kwargs = {}
    if hasattr(subprocess, "CREATE_NO_WINDOW"):
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script, str(target)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        **kwargs,
    )


def focus_drive_sync_target(target: Path) -> None:
    if os.name != "nt":
        return
    script = r"""
Add-Type @"
using System;
using System.Runtime.InteropServices;
public static class Win32 {
    [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr hWnd, int nCmdShow);
    [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr hWnd);
    [DllImport("user32.dll")] public static extern bool SetWindowPos(IntPtr hWnd, IntPtr hWndInsertAfter, int X, int Y, int cx, int cy, uint uFlags);
}
"@
$resolved = Resolve-Path -LiteralPath $args[0] -ErrorAction SilentlyContinue
if (-not $resolved) { exit 0 }
$target = $resolved.Path.TrimEnd("\")
$topMost = New-Object IntPtr -ArgumentList -1
$notTopMost = New-Object IntPtr -ArgumentList -2
$flags = 0x0001 -bor 0x0002 -bor 0x0040
$shell = New-Object -ComObject Shell.Application
foreach ($window in @($shell.Windows())) {
    try {
        $url = [string]$window.LocationURL
        if (-not $url) { continue }
        $local = ([System.Uri]$url).LocalPath.TrimEnd("\")
        if ([System.String]::Equals($local, $target, [System.StringComparison]::OrdinalIgnoreCase) -or
            $local.StartsWith($target + "\", [System.StringComparison]::OrdinalIgnoreCase)) {
            $hwnd = New-Object IntPtr -ArgumentList $window.HWND
            [void][Win32]::ShowWindow($hwnd, 9)
            [void][Win32]::SetWindowPos($hwnd, $topMost, 0, 0, 0, 0, $flags)
            [void][Win32]::SetWindowPos($hwnd, $notTopMost, 0, 0, 0, 0, $flags)
            [void][Win32]::SetForegroundWindow($hwnd)
            exit 0
        }
    } catch {}
}
"""
    kwargs = {}
    if hasattr(subprocess, "CREATE_NO_WINDOW"):
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script, str(target)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        **kwargs,
    )


def toml_array(values: list[str]) -> str:
    return "[" + ", ".join(json.dumps(value) for value in values) + "]"


def parse_command(value: str) -> list[str]:
    try:
        parsed = tomllib.loads(f"start_command = {value}")["start_command"]
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(
            'El comando debe ser un array TOML, por ejemplo ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "start.ps1"]'
        ) from exc
    if not isinstance(parsed, list) or not parsed or not all(isinstance(item, str) and item for item in parsed):
        raise ConfigError("start_command debe contener strings no vacios")
    return parsed


def write_config(
    path: Path,
    remote_archive_dir: str,
    local_server_dir: str,
    start_command: list[str],
    machine_name: str,
    sync_ignore: list[str],
    state_dir: str,
    stale_lock_minutes: int,
    heartbeat_seconds: float,
    archive_compression_level: int,
    server_ip: str | None = DEFAULT_SERVER_IP,
    connection_mode: str = DEFAULT_CONNECTION_MODE,
) -> None:
    if not remote_archive_dir.strip():
        raise ConfigError("Carpeta Drive no puede estar vacia")
    if not local_server_dir.strip():
        raise ConfigError("Carpeta local no puede estar vacia")
    if not start_command or not all(isinstance(part, str) and part for part in start_command):
        raise ConfigError("Arranque debe contener al menos un argumento no vacio")
    if not machine_name.strip():
        machine_name = socket.gethostname()
    if stale_lock_minutes <= 0:
        raise ConfigError("Lock min debe ser mayor que cero")
    if heartbeat_seconds <= 0:
        raise ConfigError("Heartbeat debe ser mayor que cero")
    if heartbeat_seconds >= stale_lock_minutes * 60:
        raise ConfigError("Heartbeat debe ser menor que el tiempo de lock")
    if archive_compression_level < 0 or archive_compression_level > 9:
        raise ConfigError("ZIP debe estar entre 0 y 9")
    connection_mode = connection_mode.strip().lower()
    if connection_mode not in {"manual", "tailscale"}:
        raise ConfigError("Modo conexion debe ser manual o tailscale")
    lines = [
        f"remote_archive_dir = {json.dumps(remote_archive_dir.strip())}",
        f"local_server_dir = {json.dumps(local_server_dir.strip())}",
        f"start_command = {toml_array(start_command)}",
        f"machine_name = {json.dumps(machine_name.strip() or socket.gethostname())}",
        f"connection_mode = {json.dumps(connection_mode)}",
        f"server_ip = {json.dumps((server_ip if server_ip is not None else DEFAULT_SERVER_IP).strip())}",
    ]
    if state_dir.strip():
        lines.append(f"state_dir = {json.dumps(state_dir.strip())}")
    lines.extend(
        [
            f"sync_ignore = {toml_array(sync_ignore)}",
            f"stale_lock_minutes = {int(stale_lock_minutes)}",
            f"heartbeat_seconds = {float(heartbeat_seconds)}",
            f"archive_compression_level = {int(archive_compression_level)}",
        ]
    )
    content = "\n".join(lines) + "\n"
    parsed = tomllib.loads(content)
    parse_config(parsed, base_dir=path.parent)
    write_text_atomic(path, content)


def command_for_script(script: Path, local_server_dir: Path) -> list[str]:
    suffix = script.suffix.lower()
    resolved_script = script.resolve(strict=False)
    resolved_local = local_server_dir.resolve(strict=False)
    try:
        arg = resolved_script.relative_to(resolved_local).as_posix()
    except ValueError:
        arg = resolved_script.as_posix()
    if suffix in {".bat", ".cmd"}:
        return ["cmd", "/c", arg]
    if suffix == ".ps1":
        return ["powershell", "-ExecutionPolicy", "Bypass", "-File", arg]
    raise ConfigError("El script debe ser .bat, .cmd o .ps1")


class LauncherGui:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("Minecraft Server Launcher")
        self.root.geometry("1120x820")
        self.root.minsize(1020, 740)
        self.root.configure(bg=COLORS["coal"])
        self._set_window_icon()
        self.app_root = app_root_dir()
        self.log_queue: queue.Queue[str] = queue.Queue()
        self.busy = False
        self.server_control: ServerProcessControl | None = None
        self.stop_requested = False
        self.has_lock = False
        self.lock_owner = ""
        self.lock_status = ""
        self.exit_after_work = False
        self.connection_mode = DEFAULT_CONNECTION_MODE
        self.tailscale_available = False
        self.root.report_callback_exception = self._report_callback_exception

        self.config_path = tk.StringVar(value=str(initial_config_path(self.app_root)))
        self.status_text = tk.StringVar(value="Cargando")
        self.detail_text = tk.StringVar(value="Elige config.toml")
        self.drive_text = tk.StringVar(value="")
        self.zip_text = tk.StringVar(value="")
        self.local_text = tk.StringVar(value="")
        self.command_text = tk.StringVar(value="")
        self.ip_text = tk.StringVar(value=detect_local_ipv4())
        self.connection_text = tk.StringVar(value="")
        self.tailscale_text = tk.StringVar(value="")

        self._style()
        self._build()
        self.root.protocol("WM_DELETE_WINDOW", self._safe_exit)
        self.root.after(100, self._initial_load)
        self.root.after(100, self._drain_logs)

    def _set_window_icon(self) -> None:
        icon = tk.PhotoImage(width=32, height=32)
        icon.put(COLORS["ink"], to=(2, 2, 30, 30))
        icon.put(COLORS["redstone"], to=(5, 5, 27, 12))
        icon.put("#728B66", to=(5, 12, 27, 17))
        icon.put(COLORS["stone_light"], to=(5, 17, 27, 27))
        icon.put(COLORS["line"], to=(9, 20, 14, 25))
        icon.put(COLORS["network"], to=(19, 19, 24, 24))
        self._window_icon = icon
        self.root.iconphoto(True, icon)

    def _style(self) -> None:
        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure("Root.TFrame", background=COLORS["coal"])
        style.configure("Panel.TFrame", background=COLORS["stone"])
        style.configure("Field.TFrame", background=COLORS["stone_light"])
        style.configure("TLabel", background=COLORS["coal"], foreground=COLORS["paper"], font=("Segoe UI", 10))
        style.configure("Brand.TLabel", background=COLORS["coal"], foreground=COLORS["paper"], font=("Segoe UI Semibold", 20))
        style.configure("Kicker.TLabel", background=COLORS["coal"], foreground=COLORS["network"], font=("Segoe UI Semibold", 9))
        style.configure("Muted.TLabel", background=COLORS["coal"], foreground=COLORS["muted"], font=("Segoe UI", 9))
        style.configure("Panel.TLabel", background=COLORS["stone"], foreground=COLORS["paper"], font=("Segoe UI", 10))
        style.configure("Status.TLabel", background=COLORS["stone"], foreground=COLORS["paper"], font=("Segoe UI Semibold", 23))
        style.configure("FieldLabel.TLabel", background=COLORS["stone_light"], foreground=COLORS["muted"], font=("Segoe UI Semibold", 8))
        style.configure("FieldValue.TLabel", background=COLORS["stone_light"], foreground=COLORS["paper"], font=("Segoe UI", 10))
        style.configure("LogTitle.TLabel", background=COLORS["coal"], foreground=COLORS["paper"], font=("Segoe UI Semibold", 11))
        style.configure("DialogTitle.TLabel", background=COLORS["coal"], foreground=COLORS["paper"], font=("Segoe UI Semibold", 17))
        style.configure("Section.TLabel", background=COLORS["stone"], foreground=COLORS["network"], font=("Segoe UI Semibold", 9))
        style.configure("Form.TLabel", background=COLORS["stone"], foreground=COLORS["muted"], font=("Segoe UI", 9))
        style.configure("Data.TEntry", fieldbackground=COLORS["stone_light"], foreground=COLORS["paper"], insertcolor=COLORS["network"], bordercolor=COLORS["line"], padding=7)
        style.map("Data.TEntry", fieldbackground=[("readonly", COLORS["stone_light"])], foreground=[("readonly", COLORS["muted"])])
        style.configure("Data.TCombobox", fieldbackground=COLORS["stone_light"], background=COLORS["stone_light"], foreground=COLORS["paper"], arrowcolor=COLORS["paper"], padding=7)
        style.configure("Accent.TButton", background=COLORS["redstone"], foreground=COLORS["ink"], font=("Segoe UI Semibold", 11), padding=(18, 13), borderwidth=0)
        style.map("Accent.TButton", background=[("active", "#6DE18F"), ("disabled", COLORS["line"])], foreground=[("disabled", COLORS["muted"])])
        style.configure("Tool.TButton", background=COLORS["stone_light"], foreground=COLORS["paper"], font=("Segoe UI", 10), padding=(13, 9), borderwidth=0)
        style.map("Tool.TButton", background=[("active", COLORS["line"]), ("disabled", COLORS["stone"])], foreground=[("disabled", "#66736E")])
        style.configure("Danger.TButton", background=COLORS["error"], foreground=COLORS["paper"], font=("Segoe UI Semibold", 10), padding=(13, 10), borderwidth=0)
        style.map("Danger.TButton", background=[("active", "#F06D6D"), ("disabled", COLORS["line"])], foreground=[("disabled", COLORS["muted"])])
        style.configure("Quiet.TButton", background=COLORS["coal"], foreground=COLORS["muted"], font=("Segoe UI", 9), padding=(10, 7), borderwidth=0)
        style.map("Quiet.TButton", background=[("active", COLORS["stone"])] , foreground=[("active", COLORS["paper"])])

    def _build(self) -> None:
        root = ttk.Frame(self.root, style="Root.TFrame", padding=(22, 18))
        root.pack(fill=tk.BOTH, expand=True)

        top = ttk.Frame(root, style="Root.TFrame")
        top.pack(fill=tk.X)
        brand = ttk.Frame(top, style="Root.TFrame")
        brand.pack(side=tk.LEFT)
        ttk.Label(brand, text="SHARED WORLD", style="Kicker.TLabel").pack(anchor=tk.W)
        ttk.Label(brand, text="Minecraft Server Control", style="Brand.TLabel").pack(anchor=tk.W)
        config_line = ttk.Frame(top, style="Root.TFrame")
        config_line.pack(side=tk.RIGHT)
        ttk.Button(config_line, text="Elegir config", command=self._browse_config, style="Quiet.TButton").pack(side=tk.RIGHT)
        ttk.Button(config_line, text="Mas ajustes", command=self._edit_config, style="Tool.TButton").pack(side=tk.RIGHT, padx=(0, 8))
        ttk.Label(config_line, textvariable=self.config_path, style="Muted.TLabel", wraplength=390).pack(side=tk.RIGHT, padx=(0, 14))

        status = ttk.Frame(root, style="Panel.TFrame", padding=(18, 16))
        status.pack(fill=tk.X, pady=(18, 12))
        status.columnconfigure(0, weight=1)
        status.columnconfigure(1, minsize=315)
        summary = ttk.Frame(status, style="Panel.TFrame")
        summary.grid(row=0, column=0, sticky=tk.NSEW, padx=(0, 22))
        self.status_canvas = tk.Canvas(summary, width=430, height=60, bg=COLORS["stone"], highlightthickness=0)
        self.status_canvas.pack(anchor=tk.W)
        ttk.Label(summary, textvariable=self.status_text, style="Status.TLabel").pack(anchor=tk.W, pady=(5, 0))
        ttk.Label(summary, textvariable=self.detail_text, style="Panel.TLabel", wraplength=650).pack(anchor=tk.W, pady=(3, 0))
        ttk.Label(summary, textvariable=self.zip_text, style="Panel.TLabel", foreground=COLORS["muted"], wraplength=650).pack(anchor=tk.W, pady=(5, 0))

        actions = ttk.Frame(status, style="Panel.TFrame")
        actions.grid(row=0, column=1, sticky=tk.NSEW)
        actions.columnconfigure((0, 1), weight=1, uniform="action")
        self.run_button = ttk.Button(actions, text="Iniciar servidor", command=self._run_server, style="Accent.TButton")
        self.run_button.grid(row=0, column=0, columnspan=2, sticky=tk.EW)
        self.stop_button = ttk.Button(actions, text="Parar servidor", command=self._stop_server, style="Danger.TButton")
        self.stop_button.grid(row=1, column=0, columnspan=2, sticky=tk.EW, pady=(8, 0))
        self.status_button = ttk.Button(actions, text="Actualizar", command=self._refresh_status, style="Tool.TButton")
        self.status_button.grid(row=2, column=0, sticky=tk.EW, pady=(8, 0), padx=(0, 4))
        self.sync_button = ttk.Button(actions, text="Recuperar subida", command=self._sync_up, style="Tool.TButton")
        self.sync_button.grid(row=2, column=1, sticky=tk.EW, pady=(8, 0), padx=(4, 0))
        self.unlock_button = ttk.Button(actions, text="Quitar lock", command=self._unlock, style="Danger.TButton")
        self.unlock_button.grid(row=3, column=0, sticky=tk.EW, pady=(8, 0), padx=(0, 4))
        self.exit_button = ttk.Button(actions, text="Salir seguro", command=self._safe_exit, style="Tool.TButton")
        self.exit_button.grid(row=3, column=1, sticky=tk.EW, pady=(8, 0), padx=(4, 0))

        details = ttk.Frame(root, style="Root.TFrame")
        details.pack(fill=tk.X, pady=(0, 12))
        details.columnconfigure((0, 1), weight=1, uniform="detail")
        self._field(details, "CARPETA DRIVE", self.drive_text, None, row=0, column=0)
        self._field(details, "COPIA LOCAL", self.local_text, None, row=0, column=1)
        self._field(details, "CONEXION", self.connection_text, None, row=1, column=0)
        self.tailscale_button = self._field(
            details, "TAILSCALE", self.tailscale_text, self._connect_tailscale, "Conectar", row=1, column=1
        )
        self._field(details, "COMANDO DE ARRANQUE", self.command_text, self._pick_script, "Cambiar", row=2, column=0, columnspan=2)

        log_header = ttk.Frame(root, style="Root.TFrame")
        log_header.pack(fill=tk.X)
        ttk.Label(log_header, text="Actividad", style="LogTitle.TLabel").pack(side=tk.LEFT)
        ttk.Label(log_header, text="Salida del servidor y sincronizacion", style="Muted.TLabel").pack(side=tk.LEFT, padx=(10, 0))
        self.log_text = scrolledtext.ScrolledText(root, height=12, bg="#0A0F11", fg="#DDE7E1", insertbackground=COLORS["network"], selectbackground=COLORS["line"], font=("Consolas", 10), relief=tk.FLAT, borderwidth=0, padx=12, pady=10)
        self.log_text.pack(fill=tk.BOTH, expand=True, pady=(6, 0))
        self.log_text.configure(state=tk.DISABLED)

    def _field(
        self,
        parent: ttk.Frame,
        label: str,
        variable: tk.StringVar,
        command,
        button_text: str = "Buscar",
        row: int = 0,
        column: int = 0,
        columnspan: int = 1,
    ) -> ttk.Button | None:
        frame = ttk.Frame(parent, style="Field.TFrame", padding=(12, 9))
        frame.grid(row=row, column=column, columnspan=columnspan, sticky=tk.EW, padx=(0 if column == 0 else 5, 5 if column == 0 else 0), pady=(0, 5))
        frame.columnconfigure(0, weight=1)
        ttk.Label(frame, text=label, style="FieldLabel.TLabel").grid(row=0, column=0, sticky=tk.W)
        ttk.Label(frame, textvariable=variable, style="FieldValue.TLabel", anchor=tk.W).grid(row=1, column=0, sticky=tk.EW, pady=(2, 0))
        if command:
            button = ttk.Button(frame, text=button_text, command=command, style="Tool.TButton")
            button.grid(row=0, column=1, rowspan=2, sticky=tk.E, padx=(12, 0))
            return button
        return None

    def _initial_load(self) -> None:
        if Path(self.config_path.get()).exists():
            self._refresh_status()
            if drive_checkpoints_enabled():
                self.root.after(250, self._startup_drive_checkpoint)
        else:
            self.drive_text.set(str(default_drive_dir(self.app_root)))
            self.local_text.set(str(default_local_server_dir(self.app_root)))
            self._refresh_connection_info(None)
            self.command_text.set(" ".join(DEFAULT_START_COMMAND))
            self._apply_status("Sin config", "Pulsa Mas ajustes y guarda config.", COLORS["amber"])
            self._update_buttons()

    def _load_config(self) -> Config | None:
        try:
            config = load_config(self.config_path.get())
        except (ConfigError, OSError) as exc:
            self._apply_status("Config invalida", str(exc), COLORS["error"])
            return None
        self.drive_text.set(str(config.remote_archive_dir or config.remote_archive_file or config.remote_server_dir or ""))
        self.local_text.set(str(config.local_server_dir))
        self._refresh_connection_info(config)
        self.command_text.set(" ".join(config.start_command))
        return config

    def _refresh_connection_info(self, config: Config | None) -> None:
        local_ip = detect_local_ipv4()
        tailscale_ip = detect_tailscale_ipv4()
        mode = config.connection_mode if config is not None else DEFAULT_CONNECTION_MODE
        self.connection_mode = mode
        self.tailscale_available = tailscale_ip is not None
        self.ip_text.set(local_ip)
        if mode == "tailscale":
            self.connection_text.set("Tailscale")
            if tailscale_ip:
                self.tailscale_text.set(f"Jugar: {tailscale_ip}:25565")
            else:
                self.tailscale_text.set("No activo")
            return
        self.connection_text.set("Manual")
        self.tailscale_text.set(f"LAN: {local_ip}:25565")

    def _refresh_status(self) -> None:
        config = self._load_config()
        if config is None:
            return
        try:
            payload = status(config)
            if config.remote_archive_dir is not None:
                self.zip_text.set(f"ZIP activo: {resolve_latest_archive(config.remote_archive_dir).name}")
            elif config.remote_archive_file is not None:
                self.zip_text.set(f"ZIP fijo: {config.remote_archive_file.name}")
            else:
                self.zip_text.set("Modo carpeta legacy")
        except (ArchiveError, ConfigError, LockError, LauncherError, StateError, OSError) as exc:
            self.has_lock = False
            self.lock_owner = ""
            self.lock_status = ""
            self._apply_status("Revisar", str(exc), COLORS["amber"])
            self._update_buttons()
            return
        lock = payload.get("lock")
        if isinstance(lock, dict):
            info = LockInfo.from_dict(lock)
            self.has_lock = True
            self.lock_owner = info.owner
            self.lock_status = info.status
            if info.status == "upload_failed":
                self._apply_status("Subida fallida", f"{info.owner} debe recuperar cambios.", COLORS["error"])
            else:
                self._apply_status("Servidor en uso", lock_connection_detail(info), COLORS["amber"])
        else:
            self.has_lock = False
            self.lock_owner = ""
            self.lock_status = ""
            if config.connection_mode == "tailscale" and not self.tailscale_available:
                self._apply_status(
                    "VPN desconectada",
                    "Conecta Tailscale y pulsa Actualizar estado para poder iniciar.",
                    COLORS["error"],
                )
            else:
                self._apply_status("Libre", "Listo para iniciar.", COLORS["redstone"])
        self._update_buttons()

    def _connect_tailscale(self) -> None:
        if self.busy:
            return

        def work() -> None:
            address, message = activate_tailscale()
            print(message)
            if address is None:
                self.root.after(0, lambda: messagebox.showwarning("Tailscale", message))

        self._start_work(
            "Solicitando conexion de Tailscale...\n",
            "Conectando VPN",
            "Abriendo Tailscale y esperando una IP activa.",
            COLORS["amber"],
            work,
        )

    def _startup_drive_checkpoint(self) -> None:
        config = self._load_config()
        if config is None:
            return
        self._drive_sync_checkpoint(
            config,
            "Comprobar Drive",
            "Se abrira Google Drive Desktop. Espera a que indique sincronizacion completa y pulsa Continuar.",
        )

    def _drive_sync_checkpoint(self, config: Config, title: str, message: str, hide_launcher: bool = False) -> None:
        target = drive_sync_target(config)
        if target is None:
            return
        launcher_hidden = False
        if hide_launcher:
            self.root.withdraw()
            self.root.update_idletasks()
            launcher_hidden = True
        try:
            drive_app_opened = open_google_drive_app()
        except OSError:
            drive_app_opened = False
        if not drive_app_opened:
            try:
                open_drive_sync_target(target)
            except OSError as exc:
                messagebox.showwarning(title, f"No se pudo abrir Google Drive ni la carpeta Drive:\n{exc}")
                return
            message = f"{message}\n\nNo encontre el launcher de Google Drive Desktop; abri solo la carpeta configurada."
            focus_drive_sync_target(target)
        else:
            focus_google_drive_app_window()
        try:
            self._show_continue_dialog(title, message)
        finally:
            try:
                if drive_app_opened:
                    close_google_drive_app_window()
                else:
                    close_drive_sync_target(target)
            except OSError:
                pass
            if launcher_hidden and self.root.winfo_exists():
                self.root.deiconify()

    def _show_continue_dialog(self, title: str, message: str) -> None:
        dialog = tk.Toplevel(self.root)
        dialog.title(title)
        dialog.configure(bg=COLORS["coal"])
        dialog.grab_set()
        dialog.attributes("-topmost", True)
        dialog.resizable(False, False)
        frame = ttk.Frame(dialog, style="Root.TFrame", padding=18)
        frame.pack(fill=tk.BOTH, expand=True)
        ttk.Label(frame, text=title, font=("Segoe UI", 16, "bold")).pack(anchor=tk.W)
        ttk.Label(frame, text=message, wraplength=520).pack(anchor=tk.W, pady=(10, 18))
        ttk.Button(frame, text="Continuar", command=dialog.destroy, style="Accent.TButton").pack(anchor=tk.E)
        dialog.update_idletasks()
        screen_w = dialog.winfo_screenwidth()
        screen_h = dialog.winfo_screenheight()
        x = max(screen_w - dialog.winfo_width() - 32, 0)
        y = max(min(80, screen_h - dialog.winfo_height() - 32), 0)
        dialog.geometry(f"+{x}+{y}")
        dialog.lift()
        dialog.focus_force()
        self.root.wait_window(dialog)

    def _run_server(self) -> None:
        config = self._load_config()
        if config is None:
            return
        try:
            existing_payload = status(config).get("lock")
        except (LockError, LauncherError, OSError) as exc:
            messagebox.showerror("No se pudo comprobar el lock", str(exc))
            return
        if isinstance(existing_payload, dict):
            existing = LockInfo.from_dict(existing_payload)
            self._refresh_status()
            messagebox.showinfo("Servidor ya iniciado", lock_connection_detail(existing))
            return
        if config.connection_mode == "tailscale" and not detect_tailscale_ipv4():
            self._refresh_connection_info(config)
            self._apply_status(
                "VPN desconectada",
                "Conecta Tailscale y pulsa Actualizar estado para poder iniciar.",
                COLORS["error"],
            )
            self._update_buttons()
            messagebox.showwarning(
                "Tailscale necesario",
                "No se puede iniciar el servidor hasta que Tailscale este conectado.",
            )
            return
        control = ServerProcessControl()
        self.server_control = control
        self.stop_requested = False

        def work() -> None:
            with redirect_stdout(QueueWriter(self.log_queue)), redirect_stderr(QueueWriter(self.log_queue)):
                code = run_server(config, confirm_stale_lock=self._confirm_stale_lock, process_control=control)
                print(f"Servidor terminado con codigo {code}.")

        self._start_work("Iniciando servidor...\n", "Ejecutando", "Servidor activo.", COLORS["redstone"], work)
        self.root.after(200, self._poll_server)

    def _stop_server(self, confirm: bool = True) -> None:
        control = self.server_control
        if control is None or not control.is_running():
            messagebox.showinfo("Parar servidor", "No hay servidor activo.")
            return
        if self.stop_requested or control.stop_requested:
            return
        if confirm and not messagebox.askyesno("Parar servidor", "Enviar stop y esperar subida del ZIP?"):
            return
        self.stop_requested = True
        self._apply_status("Parando", "Enviando stop; si hace falta se enviara s.", COLORS["amber"])

        def work() -> None:
            try:
                stopped = control.request_stop(confirm_delay=2.0, confirm_line="s")
                if stopped:
                    self.log_queue.put("Secuencia de parada completada.\n")
                else:
                    self.stop_requested = False
                    self.log_queue.put("El proceso sigue activo. Puedes reintentar la parada segura.\n")
                    self.root.after(
                        0,
                        lambda: messagebox.showwarning(
                            "Servidor aun activo",
                            "El script no respondio a stop, s y Ctrl+C. El servidor no se ha forzado; puedes reintentar.",
                        ),
                    )
            except Exception as exc:
                self.stop_requested = False
                self.log_queue.put(f"No se pudo parar desde GUI: {exc}\n")
            self.root.after(0, self._update_buttons)

        threading.Thread(target=work, daemon=True).start()
        self._update_buttons()

    def _sync_up(self) -> None:
        config = self._load_config()
        if config is None:
            return
        self._start_work("Recuperando subida...\n", "Subiendo", "Creando ZIP desde local.", COLORS["amber"], lambda: sync_up(config))

    def _unlock(self) -> None:
        config = self._load_config()
        if config is None:
            return
        if not self.has_lock:
            messagebox.showinfo("Quitar lock", "No hay lock activo.")
            self._update_buttons()
            return
        if not messagebox.askyesno("Quitar lock", "Quitar lock puede pisar cambios de otro PC. Continuar?"):
            return
        self._start_work("Quitando lock...\n", "Quitando lock", "Eliminando lock compartido.", COLORS["amber"], lambda: force_unlock(config))

    def _start_work(self, message: str, headline: str, detail: str, accent: str, work) -> None:
        if self.busy:
            return
        self.busy = True
        self._log(message)
        self._apply_status(headline, detail, accent)
        self._update_buttons()

        def runner() -> None:
            try:
                work()
            except Exception as exc:
                self.log_queue.put(f"Error: {exc}\n")
                if isinstance(exc, LockError):
                    self.root.after(0, lambda message=str(exc): messagebox.showinfo("Servidor ya iniciado", message))
                else:
                    self.root.after(0, lambda message=str(exc): messagebox.showerror("Operacion fallida", message))
            finally:
                self.root.after(0, self._finish_work)

        threading.Thread(target=runner, daemon=True).start()

    def _finish_work(self) -> None:
        self.busy = False
        self.server_control = None
        self.stop_requested = False
        self._refresh_status()
        if self.exit_after_work:
            self.root.after(100, self._safe_exit)

    def _poll_server(self) -> None:
        self._update_buttons()
        if self.busy:
            self.root.after(250, self._poll_server)

    def _browse_config(self) -> None:
        path = filedialog.askopenfilename(title="Elegir config.toml", filetypes=[("TOML", "*.toml"), ("Todos", "*.*")])
        if path:
            self.config_path.set(path)
            save_gui_state(self.app_root, Path(path))
            self._refresh_status()

    def _pick_script(self) -> None:
        config = self._load_config()
        initial = str(config.local_server_dir) if config else str(Path.cwd())
        path = filedialog.askopenfilename(title="Elegir script", initialdir=initial, filetypes=[("Scripts", "*.bat *.cmd *.ps1"), ("Todos", "*.*")])
        if path and config is not None:
            command = command_for_script(Path(path), config.local_server_dir)
            self.command_text.set(" ".join(command))
            self._save_command(command)

    def _edit_config(self) -> None:
        dialog = tk.Toplevel(self.root)
        dialog.title("Mas ajustes")
        dialog.configure(bg=COLORS["coal"])
        dialog.transient(self.root)
        dialog.grab_set()
        dialog.geometry("940x680")
        dialog.minsize(880, 630)

        config = self._load_config()
        remote = tk.StringVar(value=self.drive_text.get() or str(default_drive_dir(self.app_root)))
        local = tk.StringVar(value=self.local_text.get() or str(default_local_server_dir(self.app_root)))
        command = tk.StringVar(value=toml_array(config.start_command if config else DEFAULT_START_COMMAND))
        machine = tk.StringVar(value=config.machine_name if config else socket.gethostname())
        connection_mode = tk.StringVar(value=config.connection_mode if config else DEFAULT_CONNECTION_MODE)
        detected_ip = tk.StringVar(value=detect_local_ipv4())
        detected_tailscale = tk.StringVar(value=detect_tailscale_ipv4() or "No activo")
        server_ip = tk.StringVar(value=config.server_ip if config and config.server_ip is not None else DEFAULT_SERVER_IP)
        state = tk.StringVar(value=str(config.state_dir) if config and config.state_dir else str(default_state_dir(self.app_root)))
        stale = tk.StringVar(value=str(config.stale_lock_minutes if config else 30))
        heartbeat = tk.StringVar(value=str(config.heartbeat_seconds if config else 30))
        zip_level = tk.StringVar(value=str(config.archive_compression_level if config else 1))

        frame = ttk.Frame(dialog, style="Root.TFrame", padding=(20, 16))
        frame.pack(fill=tk.BOTH, expand=True)
        header = ttk.Frame(frame, style="Root.TFrame")
        header.pack(fill=tk.X, pady=(0, 14))
        ttk.Label(header, text="Configuracion", style="DialogTitle.TLabel").pack(side=tk.LEFT)
        ttk.Label(header, textvariable=self.config_path, style="Muted.TLabel", wraplength=560).pack(side=tk.RIGHT)

        body = ttk.Frame(frame, style="Root.TFrame")
        body.pack(fill=tk.BOTH, expand=True)
        body.columnconfigure((0, 1), weight=1, uniform="settings")
        body.rowconfigure(1, weight=1)

        paths = ttk.Frame(body, style="Panel.TFrame", padding=14)
        paths.grid(row=0, column=0, sticky=tk.NSEW, padx=(0, 6), pady=(0, 8))
        paths.columnconfigure(1, weight=1)
        ttk.Label(paths, text="ARCHIVOS", style="Section.TLabel").grid(row=0, column=0, columnspan=3, sticky=tk.W, pady=(0, 8))

        connection = ttk.Frame(body, style="Panel.TFrame", padding=14)
        connection.grid(row=0, column=1, sticky=tk.NSEW, padx=(6, 0), pady=(0, 8))
        connection.columnconfigure(1, weight=1)
        ttk.Label(connection, text="MAQUINA Y RED", style="Section.TLabel").grid(row=0, column=0, columnspan=2, sticky=tk.W, pady=(0, 8))

        runtime = ttk.Frame(body, style="Panel.TFrame", padding=14)
        runtime.grid(row=1, column=0, sticky=tk.NSEW, padx=(0, 6))
        runtime.columnconfigure(1, weight=1)
        ttk.Label(runtime, text="SEGURIDAD Y ZIP", style="Section.TLabel").grid(row=0, column=0, columnspan=2, sticky=tk.W, pady=(0, 8))

        ignored = ttk.Frame(body, style="Panel.TFrame", padding=14)
        ignored.grid(row=1, column=1, sticky=tk.NSEW, padx=(6, 0))
        ignored.columnconfigure(0, weight=1)
        ignored.rowconfigure(2, weight=1)
        ttk.Label(ignored, text="NO INCLUIR EN EL ZIP", style="Section.TLabel").grid(row=0, column=0, sticky=tk.W)
        ttk.Label(ignored, text="Un patron por linea", style="Form.TLabel").grid(row=1, column=0, sticky=tk.W, pady=(2, 7))

        def add_entry(parent, row: int, label: str, variable: tk.StringVar, readonly: bool = False) -> ttk.Entry:
            ttk.Label(parent, text=label, style="Form.TLabel").grid(row=row, column=0, sticky=tk.W, pady=4)
            entry = ttk.Entry(parent, textvariable=variable, style="Data.TEntry", state="readonly" if readonly else tk.NORMAL)
            entry.grid(row=row, column=1, sticky=tk.EW, padx=(10, 0), pady=4)
            return entry

        def choose_dir(variable: tk.StringVar) -> None:
            selected = filedialog.askdirectory(parent=dialog, initialdir=variable.get() or str(self.app_root))
            if selected:
                variable.set(selected)

        for row, (label, variable) in enumerate(
            (("Carpeta Drive", remote), ("Carpeta local", local), ("Carpeta estado", state), ("Comando", command)),
            start=1,
        ):
            add_entry(paths, row, label, variable)
            if label != "Comando":
                ttk.Button(paths, text="Elegir", command=lambda value=variable: choose_dir(value), style="Quiet.TButton").grid(
                    row=row, column=2, padx=(7, 0)
                )

        add_entry(connection, 1, "Nombre del PC", machine)
        ttk.Label(connection, text="Modo", style="Form.TLabel").grid(row=2, column=0, sticky=tk.W, pady=4)
        ttk.Combobox(connection, textvariable=connection_mode, values=("tailscale", "manual"), state="readonly", style="Data.TCombobox").grid(
            row=2, column=1, sticky=tk.EW, padx=(10, 0), pady=4
        )
        add_entry(connection, 3, "IP local", detected_ip, readonly=True)
        add_entry(connection, 4, "IP Tailscale", detected_tailscale, readonly=True)
        add_entry(connection, 5, "server-ip", server_ip)

        add_entry(runtime, 1, "Lock antiguo (min)", stale)
        add_entry(runtime, 2, "Heartbeat (s)", heartbeat)
        add_entry(runtime, 3, "Compresion ZIP (0-9)", zip_level)
        ttk.Label(
            runtime,
            text="Tailscale deja server-ip vacio y evita publicar el puerto del router.",
            style="Form.TLabel",
            wraplength=360,
        ).grid(row=4, column=0, columnspan=2, sticky=tk.W, pady=(10, 0))

        ignore_text = tk.Text(ignored, height=9, bg="#0A0F11", fg=COLORS["paper"], insertbackground=COLORS["network"], selectbackground=COLORS["line"], font=("Consolas", 10), relief=tk.FLAT, padx=9, pady=8)
        ignore_text.grid(row=2, column=0, sticky=tk.NSEW)
        ignore_text.insert("1.0", "\n".join(config.sync_ignore if config else DEFAULT_SYNC_IGNORE))

        def save() -> None:
            try:
                write_config(
                    Path(self.config_path.get()),
                    remote.get(),
                    local.get(),
                    parse_command(command.get()),
                    machine.get(),
                    [line.strip() for line in ignore_text.get("1.0", tk.END).splitlines() if line.strip()],
                    state.get(),
                    int(stale.get()),
                    float(heartbeat.get()),
                    int(zip_level.get()),
                    server_ip.get(),
                    connection_mode.get(),
                )
            except Exception as exc:
                messagebox.showerror("Config", str(exc), parent=dialog)
                return
            dialog.destroy()
            self._refresh_status()

        buttons = ttk.Frame(frame, style="Root.TFrame")
        buttons.pack(fill=tk.X, pady=(14, 0))
        ttk.Button(buttons, text="Guardar config", command=save, style="Accent.TButton").pack(side=tk.LEFT)
        ttk.Button(buttons, text="Cancelar", command=dialog.destroy, style="Tool.TButton").pack(side=tk.RIGHT)

    def _save_from_fields(self) -> None:
        config = self._load_config()
        try:
            write_config(
                Path(self.config_path.get()),
                self.drive_text.get(),
                self.local_text.get(),
                config.start_command if config else DEFAULT_START_COMMAND,
                config.machine_name if config else socket.gethostname(),
                config.sync_ignore if config else DEFAULT_SYNC_IGNORE,
                str(config.state_dir) if config and config.state_dir else "",
                config.stale_lock_minutes if config else 30,
                config.heartbeat_seconds if config else 30,
                config.archive_compression_level if config else 1,
                config.server_ip if config and config.server_ip is not None else DEFAULT_SERVER_IP,
                config.connection_mode if config else DEFAULT_CONNECTION_MODE,
            )
        except Exception as exc:
            messagebox.showerror("Config", str(exc))
            return
        self._refresh_status()

    def _save_command(self, command: list[str]) -> None:
        config = self._load_config()
        if config is None:
            return
        write_config(
            Path(self.config_path.get()),
            str(config.remote_archive_dir or ""),
            str(config.local_server_dir),
            command,
            config.machine_name,
            config.sync_ignore,
            str(config.state_dir) if config.state_dir else "",
            config.stale_lock_minutes,
            config.heartbeat_seconds,
            config.archive_compression_level,
            config.server_ip,
            config.connection_mode,
        )
        self._refresh_status()

    def _confirm_stale_lock(self, lock: LockInfo) -> bool:
        result: list[bool] = []
        ready = threading.Event()

        def ask() -> None:
            result.append(messagebox.askyesno("Lock antiguo", f"Lock de {lock.owner} parece antiguo. Forzar inicio?"))
            ready.set()

        self.root.after(0, ask)
        ready.wait()
        return result[0] if result else False

    def _apply_status(self, headline: str, detail: str, accent: str) -> None:
        self.status_text.set(headline)
        self.detail_text.set(detail)
        self._draw_status(accent)

    def _draw_status(self, color: str) -> None:
        canvas = self.status_canvas
        canvas.delete("all")
        headline = self.status_text.get()
        drive_known = self.zip_text.get().startswith(("ZIP activo:", "ZIP fijo:", "Modo carpeta"))
        vpn_ready = self.connection_mode != "tailscale" or self.tailscale_available
        server_running = self.server_control is not None and self.server_control.is_running()
        stages = [
            ("DRIVE", COLORS["network"] if drive_known else COLORS["line"]),
            ("VPN", COLORS["network"] if vpn_ready else COLORS["error"]),
            (
                "LOCK",
                COLORS["error"]
                if self.lock_status == "upload_failed"
                else COLORS["amber"]
                if self.has_lock
                else COLORS["redstone"],
            ),
            (
                "SERVER",
                color
                if server_running or headline in {"Ejecutando", "Parando", "Subiendo"}
                else COLORS["error"]
                if headline in {"Config invalida", "VPN desconectada", "Revisar"}
                else COLORS["line"],
            ),
        ]
        canvas.create_line(28, 31, 400, 31, fill=COLORS["line"], width=2)
        for index, (label, stage_color) in enumerate(stages):
            x = 8 + index * 104
            canvas.create_rectangle(x, 10, x + 94, 52, fill=COLORS["stone_light"], outline=stage_color, width=2)
            canvas.create_rectangle(x + 8, 22, x + 16, 40, fill=stage_color, outline="")
            canvas.create_text(
                x + 25,
                31,
                text=label,
                fill=COLORS["paper"],
                font=("Segoe UI Semibold", 9),
                anchor=tk.W,
            )

    def _update_buttons(self) -> None:
        connection_ready = self.connection_mode != "tailscale" or self.tailscale_available
        self.run_button.configure(
            state=tk.NORMAL if not self.busy and self.status_text.get() == "Libre" and connection_ready else tk.DISABLED
        )
        can_stop = self.server_control is not None and self.server_control.is_running() and not self.stop_requested
        self.stop_button.configure(state=tk.NORMAL if can_stop else tk.DISABLED)
        self.status_button.configure(state=tk.DISABLED if self.busy else tk.NORMAL)
        self.tailscale_button.configure(
            state=tk.NORMAL
            if not self.busy and self.connection_mode == "tailscale" and not self.tailscale_available
            else tk.DISABLED
        )
        self.sync_button.configure(state=tk.NORMAL if self.status_text.get() == "Subida fallida" and not self.busy else tk.DISABLED)
        self.unlock_button.configure(state=tk.NORMAL if self.has_lock and not self.busy else tk.DISABLED)
        self.exit_button.configure(state=tk.NORMAL)

    def _safe_exit(self) -> None:
        control = self.server_control
        if control is not None and control.is_running():
            if self.stop_requested:
                messagebox.showwarning("Salir", "Servidor parando. Espera a que termine subida.")
                return
            if not messagebox.askyesno(
                "Salir",
                "Servidor activo. Se enviara stop y la app cerrara cuando termine subida. Continuar?",
            ):
                return
            self.exit_after_work = True
            self._stop_server(confirm=False)
            return

        if self.busy:
            messagebox.showwarning("Salir", "Operacion en curso. Espera a que termine para no cortar subida.")
            return

        self._refresh_status()
        if self.has_lock:
            if self.lock_status == "upload_failed":
                if not messagebox.askyesno(
                    "Salir",
                    "Hay lock upload_failed. Salir sin recuperar deja otro PC bloqueado. Salir igualmente?",
                ):
                    return
            elif not messagebox.askyesno(
                "Salir",
                f"Hay lock activo de {self.lock_owner} ({self.lock_status}). Salir no lo quita. Salir igualmente?",
            ):
                return

        config = self._load_config()
        if config is not None and drive_checkpoints_enabled():
            self._drive_sync_checkpoint(
                config,
                "Comprobar Drive antes de salir",
                "Comprueba que Google Drive ya termino de sincronizar los ZIPs y pulsa Continuar para cerrar el launcher.",
            )
        self.root.destroy()

    def _log(self, text: str) -> None:
        self.log_text.configure(state=tk.NORMAL)
        self.log_text.insert(tk.END, text)
        self.log_text.see(tk.END)
        self.log_text.configure(state=tk.DISABLED)
        try:
            with (self.app_root / GUI_LOG_FILE).open("a", encoding="utf-8") as file:
                file.write(text)
        except OSError:
            pass

    def _report_callback_exception(self, exc_type, exc_value, exc_traceback) -> None:
        details = "".join(traceback.format_exception(exc_type, exc_value, exc_traceback))
        self._log(f"Error interno de interfaz:\n{details}\n")
        try:
            messagebox.showerror("Error interno", f"La operacion no pudo completarse:\n{exc_value}")
        except tk.TclError:
            pass

    def _drain_logs(self) -> None:
        while True:
            try:
                text = self.log_queue.get_nowait()
            except queue.Empty:
                break
            self._log(text)
        self.root.after(100, self._drain_logs)


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if "--smoke-test" in arguments:
        root_dir = app_root_dir()
        error_log = root_dir / "build" / "gui-smoke-error.log"
        try:
            config = load_config(initial_config_path(root_dir))
            status(config)
            if config.remote_archive_dir is not None:
                resolve_latest_archive(config.remote_archive_dir)
            error_log.unlink(missing_ok=True)
            return 0
        except Exception:
            error_log.parent.mkdir(parents=True, exist_ok=True)
            error_log.write_text(traceback.format_exc(), encoding="utf-8")
            return 1
    root = tk.Tk()
    LauncherGui(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
