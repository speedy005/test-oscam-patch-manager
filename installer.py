#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
OSCam Patch Manager - Cross-platform installer
Windows and Linux - Python 3.9+

Downloads project files from GitHub, checks Python, installs dependencies
in the system environment when possible, and falls back to .venv.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

OWNER = "speedy005"
REPO = "oscam_patch_manager_new"
BRANCH = "master"

MIN_PYTHON = (3, 9)
TIMEOUT = 30

API_URL = (
    f"https://api.github.com/repos/{OWNER}/{REPO}"
    f"/git/trees/{urllib.parse.quote(BRANCH, safe='')}?recursive=1"
)
RAW_URL = (
    f"https://raw.githubusercontent.com/{OWNER}/{REPO}/"
    f"{urllib.parse.quote(BRANCH, safe='')}/"
)

SKIP_DIRS = {
    ".git", ".github", "__pycache__", ".pytest_cache",
    ".mypy_cache", ".venv", "venv", "env", "node_modules",
}
SKIP_FILES = {
    "installer.py", "installer.sh", "installer.bat",
    "install.py", "install.sh", "install.bat", "version.txt",
}
SKIP_EXTENSIONS = {".zip", ".php", ".png", ".md"}

LANG = "de"

TEXT = {
    "de": {
        "language": "Sprache wählen: [1] Deutsch  [2] English",
        "title": "OSCam Patch Manager - Installer",
        "path": "Installationsordner (leer = aktueller Ordner): ",
        "overwrite": "Dateien überschreiben? [j/N]: ",
        "download": "Lade Projektdateien herunter ...",
        "python": "Prüfe Python ...",
        "missing_python": "Python 3.9 oder neuer wurde nicht gefunden.",
        "system": "Prüfe System-Python und Abhängigkeiten ...",
        "venv": "Verwende virtuelle Umgebung .venv ...",
        "success": "Installation abgeschlossen.",
        "error": "FEHLER",
        "cancel": "Installation abgebrochen.",
    },
    "en": {
        "language": "Choose language: [1] Deutsch  [2] English",
        "title": "OSCam Patch Manager - Installer",
        "path": "Installation folder (empty = current folder): ",
        "overwrite": "Overwrite files? [y/N]: ",
        "download": "Downloading project files ...",
        "python": "Checking Python ...",
        "missing_python": "Python 3.9 or newer was not found.",
        "system": "Checking system Python and dependencies ...",
        "venv": "Using virtual environment .venv ...",
        "success": "Installation complete.",
        "error": "ERROR",
        "cancel": "Installation cancelled.",
    },
}


def say(key):
    print(TEXT[LANG].get(key, key))


def run(command, cwd=None, timeout=300, quiet=False):
    try:
        result = subprocess.run(
            command,
            cwd=str(cwd) if cwd else None,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
        if result.stdout and not quiet:
            print(result.stdout.rstrip())
        return result.returncode == 0
    except (OSError, subprocess.SubprocessError) as exc:
        if not quiet:
            print(f"{TEXT[LANG]['error']}: {exc}")
        return False


def python_ok(command):
    return run(
        command + [
            "-c",
            "import sys; raise SystemExit("
            "0 if sys.version_info >= (3, 9) and "
            "sys.version_info.major == 3 else 1)",
        ],
        timeout=10,
        quiet=True,
    )


def find_python():
    candidates = (
        [["py", "-3"], ["python3"], ["python"]]
        if os.name == "nt"
        else [["python3"], ["python"]]
    )
    for command in candidates:
        if python_ok(command):
            return command
    return None


def request_bytes(url):
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "OSCam-Patch-Manager-Installer",
            "Accept": "application/vnd.github+json",
        },
    )
    with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
        return response.read()


def repository_files():
    data = json.loads(request_bytes(API_URL).decode("utf-8"))
    if not isinstance(data, dict) or data.get("truncated"):
        raise RuntimeError("GitHub returned an invalid or truncated file list.")

    result = []
    for item in data.get("tree", []):
        if item.get("type") != "blob":
            continue

        name = item.get("path", "")
        if not name:
            continue

        parts = name.split("/")
        if name.startswith("/") or ".." in parts:
            continue
        if any(part in SKIP_DIRS for part in parts):
            continue

        filename = parts[-1]
        if filename in SKIP_FILES:
            continue
        if Path(filename).suffix.lower() in SKIP_EXTENSIONS:
            continue

        result.append("/".join(parts))

    if "main.py" not in result:
        raise RuntimeError("main.py was not found in the repository.")

    return sorted(set(result))


def download_one(relative, root, overwrite):
    parts = relative.split("/")
    if not parts or any(p in ("", ".", "..") for p in parts):
        raise RuntimeError(f"Unsafe path: {relative}")

    destination = root.joinpath(*parts).resolve()
    destination.relative_to(root.resolve())

    if destination.exists() and not overwrite:
        print(f"Skip: {relative}")
        return False

    destination.parent.mkdir(parents=True, exist_ok=True)
    encoded = "/".join(urllib.parse.quote(p, safe="") for p in parts)
    url = RAW_URL + encoded
    last_error = None

    for attempt in range(3):
        try:
            content = request_bytes(url)
            temporary = destination.with_name(destination.name + ".download")
            temporary.write_bytes(content)
            temporary.replace(destination)
            return True
        except (urllib.error.URLError, OSError, TimeoutError) as exc:
            last_error = exc
            if attempt < 2:
                time.sleep(attempt + 1)

    raise RuntimeError(f"Download failed for {relative}: {last_error}")


def download_project(root, overwrite):
    say("download")
    files = repository_files()
    count = 0

    # Sounds nur installieren, wenn der Ordner noch nicht existiert
    sounds_dir = root / "sounds"
    install_sounds = not sounds_dir.exists()

    if not install_sounds:
        print("Skip: sounds/ (Ordner existiert bereits)")

    # Dateien herunterladen
    for index, relative in enumerate(files, 1):
        parts = relative.split("/")

        # Sounds-Ordner vollständig überspringen, wenn er existiert
        if parts[0].lower() == "sounds" and not install_sounds:
            continue

        if download_one(relative, root, overwrite):
            count += 1

        print(f"[{index}/{len(files)}] {relative}")

    if not (root / "main.py").is_file():
        raise RuntimeError("main.py is missing after download.")

    print(f"Downloaded: {count}/{len(files)}")


def get_venv_python(venv):
    if os.name == "nt":
        return venv / "Scripts" / "python.exe"
    return venv / "bin" / "python"


def install_requirements(command, root):
    requirements = root / "requirements.txt"
    if not requirements.is_file():
        print("No requirements.txt found.")
        return True

    if not run(command + ["-m", "pip", "--version"], quiet=True):
        print("pip is unavailable.")
        return False

    # Never use sudo or request administrator elevation.
    installed = run(
        command + ["-m", "pip", "install", "-r", str(requirements)],
        cwd=root,
        timeout=900,
    )
    if not installed:
        return False

    return run(command + ["-m", "pip", "check"], cwd=root, timeout=120)


def configure_environment(root, system_python):
    venv = root / ".venv"
    say("system")

    if install_requirements(system_python, root):
        return system_python

    say("venv")

    venv_python = get_venv_python(venv)
    if not venv_python.is_file():
        if venv.exists():
            shutil.rmtree(venv)

        if not run(
            system_python + ["-m", "venv", str(venv)],
            cwd=root,
            timeout=120,
        ):
            raise RuntimeError(
                "Could not create .venv. On Linux, install the "
                "python3-venv package and rerun this installer."
            )

    if not venv_python.is_file():
        raise RuntimeError(f"Virtual environment is incomplete: {venv}")

    command = [str(venv_python)]
    if not install_requirements(command, root):
        raise RuntimeError("Dependency installation failed inside .venv.")

    return command


def write_launcher(root, command):
    venv_python = get_venv_python(root / ".venv")
    using_venv = (
        len(command) == 1
        and Path(command[0]).resolve() == venv_python.resolve()
    )

    if os.name == "nt":
        launcher = root / "start_windows.bat"

        if using_venv:
            python_line = '".venv\\Scripts\\python.exe" main.py %*'
        elif command == ["py", "-3"]:
            python_line = "py -3 main.py %*"
        elif command == ["python3"]:
            python_line = "python3 main.py %*"
        else:
            python_line = "python main.py %*"

        content = (
            "@echo off\r\n"
            "chcp 65001 >nul\r\n"
            'cd /d "%~dp0"\r\n'
            f"{python_line}\r\n"
            "if errorlevel 1 pause\r\n"
        )
        launcher.write_bytes(content.encode("utf-8"))
    else:
        launcher = root / "start_linux.sh"

        if using_venv:
            python_line = (
                'exec "$SCRIPT_DIR/.venv/bin/python" '
                '"$SCRIPT_DIR/main.py" "$@"'
            )
        else:
            if command == ["py", "-3"]:
                executable = "py -3"
            elif command == ["python3"]:
                executable = "python3"
            else:
                executable = "python"
            python_line = (
                f'exec {executable} "$SCRIPT_DIR/main.py" "$@"'
            )

        content = (
            "#!/bin/sh\n"
            'SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"\n'
            'cd "$SCRIPT_DIR" || exit 1\n'
            f"{python_line}\n"
        )
        launcher.write_bytes(content.encode("utf-8"))
        launcher.chmod(0o755)

    print(f"Launcher created: {launcher.name}")


def verify_python(command, root):
    if not run(command + ["--version"], cwd=root, timeout=15):
        raise RuntimeError("Selected Python could not be started.")

    if not run(
        command + ["-m", "py_compile", "main.py"],
        cwd=root,
        timeout=30,
    ):
        raise RuntimeError("main.py failed the syntax check.")


def main():
    global LANG

    print(TEXT["de"]["language"])
    try:
        LANG = "en" if input("> ").strip() == "2" else "de"
    except (EOFError, KeyboardInterrupt):
        return 1

    say("title")

    if sys.version_info < (3, 9) or sys.version_info.major != 3:
        say("missing_python")
        return 1

    system_python = find_python()
    if system_python is None:
        say("missing_python")
        return 1

    say("python")
    run(system_python + ["--version"], timeout=10)

    try:
        entered = input(TEXT[LANG]["path"]).strip()
    except (EOFError, KeyboardInterrupt):
        say("cancel")
        return 1

    root = (Path(entered).expanduser() if entered else Path.cwd()).resolve()
    if root.exists() and not root.is_dir():
        raise RuntimeError(f"Installation path is not a directory: {root}")
    root.mkdir(parents=True, exist_ok=True)

    overwrite = True
    if any(root.iterdir()):
        try:
            answer = input(TEXT[LANG]["overwrite"]).strip().lower()
        except (EOFError, KeyboardInterrupt):
            answer = ""
        overwrite = answer in {"j", "ja", "y", "yes"}

    download_project(root, overwrite)
    command = configure_environment(root, system_python)
    verify_python(command, root)
    write_launcher(root, command)

    say("success")
    print(f"Installation folder: {root}")
    return 0


if __name__ == "__main__":
    try:
        code = main()
    except KeyboardInterrupt:
        say("cancel")
        code = 130
    except Exception as exc:
        print(f"{TEXT[LANG]['error']}: {exc}")
        code = 1

    if code:
        try:
            input("Press Enter to exit...")
        except (EOFError, KeyboardInterrupt):
            pass

    raise SystemExit(code)
