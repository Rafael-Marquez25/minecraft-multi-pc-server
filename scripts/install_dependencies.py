"""Small Windows dependency installer shipped next to the launcher executable."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import messagebox, ttk
from typing import Callable


PACKAGES = (
    ("Tailscale", "Tailscale.Tailscale"),
    ("Google Drive Desktop", "Google.GoogleDrive"),
)


def winget_install_command(package_id: str) -> list[str]:
    return [
        "winget",
        "install",
        "--id",
        package_id,
        "--exact",
        "--source",
        "winget",
        "--accept-package-agreements",
        "--accept-source-agreements",
        "--disable-interactivity",
    ]


def find_winget() -> str | None:
    executable = shutil.which("winget")
    if executable:
        return executable
    candidate = Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft/WindowsApps/winget.exe"
    return str(candidate) if candidate.is_file() else None


def tailscale_installed() -> bool:
    candidates = (
        Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "Tailscale/tailscale.exe",
        Path(os.environ.get("ProgramFiles(x86)", "C:/Program Files (x86)")) / "Tailscale/tailscale.exe",
    )
    return shutil.which("tailscale") is not None or any(path.is_file() for path in candidates)


def google_drive_installed() -> bool:
    roots = (
        Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "Google/Drive File Stream",
        Path(os.environ.get("LOCALAPPDATA", "")) / "Google/DriveFS",
    )
    for root in roots:
        if (root / "launch.bat").is_file() or (root / "GoogleDriveFS.exe").is_file():
            return True
        if root.is_dir() and any(root.glob("*/GoogleDriveFS.exe")):
            return True
    return False


CHECKS: dict[str, Callable[[], bool]] = {
    "Tailscale": tailscale_installed,
    "Google Drive Desktop": google_drive_installed,
}


def installed_state() -> dict[str, bool]:
    return {name: CHECKS[name]() for name, _package_id in PACKAGES}


def run_install(package_id: str, output: Callable[[str], None]) -> int:
    winget = find_winget()
    if not winget:
        raise RuntimeError(
            "No se encontro winget. Instala 'App Installer' desde Microsoft Store "
            "y vuelve a ejecutar este instalador."
        )
    command = winget_install_command(package_id)
    command[0] = winget
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=creationflags,
    )
    assert process.stdout is not None
    for line in process.stdout:
        output(line.rstrip())
    return process.wait()


class InstallerWindow:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("Dependencias del launcher")
        self.root.geometry("680x470")
        self.root.minsize(620, 420)
        self.root.configure(bg="#151A1E")
        self.busy = False
        self.variables: dict[str, tk.BooleanVar] = {}
        self.status_labels: dict[str, ttk.Label] = {}
        self._build()
        self.root.protocol("WM_DELETE_WINDOW", self._safe_close)
        self.refresh()

    def _build(self) -> None:
        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure("Panel.TFrame", background="#283138")
        style.configure("Panel.TLabel", background="#283138", foreground="#EEF2EA", font=("Segoe UI", 10))
        style.configure("Title.TLabel", background="#151A1E", foreground="#EEF2EA", font=("Segoe UI Semibold", 18))
        style.configure("Status.TLabel", background="#283138", foreground="#D99A2B", font=("Segoe UI Semibold", 10))
        style.configure("TCheckbutton", background="#283138", foreground="#EEF2EA", font=("Segoe UI", 10))
        style.configure("Accent.TButton", font=("Segoe UI Semibold", 10), padding=(14, 9))

        ttk.Label(self.root, text="Dependencias", style="Title.TLabel").pack(anchor=tk.W, padx=18, pady=14)
        container = ttk.Frame(self.root, style="Panel.TFrame", padding=18)
        container.pack(fill=tk.BOTH, expand=True, padx=14, pady=(0, 14))

        ttk.Label(
            container,
            text="Instala las aplicaciones necesarias para sincronizar y conectar el servidor.",
            style="Panel.TLabel",
        ).pack(anchor=tk.W, pady=(0, 12))

        for name, _package_id in PACKAGES:
            row = ttk.Frame(container, style="Panel.TFrame")
            row.pack(fill=tk.X, pady=4)
            variable = tk.BooleanVar(value=True)
            self.variables[name] = variable
            ttk.Checkbutton(row, text=name, variable=variable).pack(side=tk.LEFT)
            label = ttk.Label(row, text="Comprobando", style="Status.TLabel")
            label.pack(side=tk.RIGHT)
            self.status_labels[name] = label

        self.log = tk.Text(
            container,
            height=11,
            bg="#0E1215",
            fg="#DCE4DC",
            insertbackground="#EEF2EA",
            font=("Consolas", 9),
            relief=tk.FLAT,
            padx=8,
            pady=8,
            state=tk.DISABLED,
        )
        self.log.pack(fill=tk.BOTH, expand=True, pady=12)

        actions = ttk.Frame(container, style="Panel.TFrame")
        actions.pack(fill=tk.X)
        self.install_button = ttk.Button(actions, text="Instalar seleccionadas", style="Accent.TButton", command=self.install)
        self.install_button.pack(side=tk.LEFT)
        self.refresh_button = ttk.Button(actions, text="Comprobar", command=self.refresh)
        self.refresh_button.pack(side=tk.LEFT, padx=8)
        self.close_button = ttk.Button(actions, text="Cerrar", command=self._safe_close)
        self.close_button.pack(side=tk.RIGHT)

    def write(self, line: str) -> None:
        self.root.after(0, self._write_now, line)

    def _write_now(self, line: str) -> None:
        self.log.configure(state=tk.NORMAL)
        self.log.insert(tk.END, line + "\n")
        self.log.see(tk.END)
        self.log.configure(state=tk.DISABLED)

    def refresh(self) -> None:
        states = installed_state()
        for name, present in states.items():
            self.status_labels[name].configure(
                text="Instalado" if present else "No instalado",
                foreground="#42C46E" if present else "#D99A2B",
            )
            if present:
                self.variables[name].set(False)

    def install(self) -> None:
        selected = [(name, package_id) for name, package_id in PACKAGES if self.variables[name].get()]
        if not selected:
            messagebox.showinfo("Dependencias", "No hay dependencias pendientes seleccionadas.", parent=self.root)
            return
        if not find_winget():
            messagebox.showerror("winget no disponible", "Instala App Installer desde Microsoft Store.", parent=self.root)
            return
        self.busy = True
        self.install_button.configure(state=tk.DISABLED)
        self.refresh_button.configure(state=tk.DISABLED)
        self.close_button.configure(state=tk.DISABLED)
        threading.Thread(target=self._install_worker, args=(selected,), daemon=True).start()

    def _install_worker(self, selected: list[tuple[str, str]]) -> None:
        failures: list[str] = []
        for name, package_id in selected:
            self.write(f"\nInstalando {name}...")
            try:
                result = run_install(package_id, self.write)
            except Exception as exc:
                self.write(f"Error: {exc}")
                failures.append(name)
                continue
            if result:
                self.write(f"Error: winget termino con codigo {result}.")
                failures.append(name)
            else:
                self.write(f"{name} instalado correctamente.")
        self.root.after(0, self._finish, failures)

    def _finish(self, failures: list[str]) -> None:
        self.busy = False
        self.install_button.configure(state=tk.NORMAL)
        self.refresh_button.configure(state=tk.NORMAL)
        self.close_button.configure(state=tk.NORMAL)
        self.refresh()
        if failures:
            messagebox.showwarning("Instalacion incompleta", "No se pudo instalar: " + ", ".join(failures), parent=self.root)
        else:
            messagebox.showinfo(
                "Dependencias listas",
                "Instalacion terminada. Abre Tailscale y Google Drive para iniciar sesion.",
                parent=self.root,
            )

    def _safe_close(self) -> None:
        if self.busy:
            messagebox.showwarning(
                "Instalacion en curso",
                "Espera a que termine la instalacion para no dejar winget a medias.",
                parent=self.root,
            )
            return
        self.root.destroy()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Instala las dependencias externas del launcher.")
    parser.add_argument("--check", action="store_true", help="solo muestra el estado y termina")
    args = parser.parse_args(argv)
    if args.check:
        for name, present in installed_state().items():
            print(f"{name}: {'instalado' if present else 'no instalado'}")
        return 0
    if sys.platform != "win32":
        parser.error("este instalador solo funciona en Windows")
    root = tk.Tk()
    InstallerWindow(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
