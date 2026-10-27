
# -*- coding: utf-8 -*-
"""Simplebuild Launcher – Ausbaustufe 3.

Plattformen:
    Windows + WSL2/Ubuntu
    Linux mit unterstütztem Terminal-Emulator

Tools:
    s3 / s3_gorgone
    s3_simplebuild / s3_speedy005
    s4
    ncam

Sicherheit:
    - Keine automatischen reset/clean/checkout/pull-Operationen.
    - Keine automatische Änderung von Git-Remote-URLs.
    - Keine automatische Paketinstallation.
    - Keine automatische Löschung von Logs.
    - Keine Klone in nicht leere, unbekannte Verzeichnisse.
    - Diagnosefunktionen starten keine Builds.
    - Mehrdeutige Startskripte führen zum Abbruch.

Build-Status:
    Der Status wird anhand der Build-Logs geschätzt.
    Ein fehlender Endmarker beweist nicht, dass ein Build noch läuft.
"""

from __future__ import annotations

import base64
import os
import platform
import re
import shlex
import shutil
import subprocess
import time
import uuid

from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse


# ---------------------------------------------------------------------------
# Konfiguration
# ---------------------------------------------------------------------------

REPOSITORIES = {
    "s3": "https://github.com/gorgone/s3_releases.git",
    "s3_gorgone": "https://github.com/gorgone/s3_releases.git",
    "s3_simplebuild": (
        "https://github.com/speedy005/speedy005-S3-Simplebuild.git"
    ),
    "s3_speedy005": (
        "https://github.com/speedy005/speedy005-S3-Simplebuild.git"
    ),
    "s4": "https://git.streamboard.tv/common/simplebuild4.git",
    "ncam": "https://github.com/speedy005/s3_ncam_bonecrew_test.git",
}

TOOL_DIRECTORIES = {
    "s3": "s3_gorgone",
    "s3_gorgone": "s3_gorgone",
    "s3_simplebuild": "s3_speedy005",
    "s3_speedy005": "s3_speedy005",
    "s4": "s4",
    "ncam": "ncam",
}

TOOL_ALIASES = {
    "s3": "s3_gorgone",
    "s3_gorgone": "s3_gorgone",
    "s3_simplebuild": "s3_speedy005",
    "s3_speedy005": "s3_speedy005",
    "s4": "s4",
    "ncam": "ncam",
}

WSL_DISTRO = "Ubuntu"
LOG_DIRECTORY_NAME = "build_logs"

# Bei False bleibt ein Repository auch dann verwendbar, wenn fetch scheitert.
# Bei True wird der Fehler weitergereicht; lokale Dateien werden nicht geändert.
FETCH_FAILURE_IS_FATAL = False

BUILD_LOG_PATTERN = re.compile(
    r"^(?P<tool>.+)_build_(?P<stamp>\d{8}_\d{6}_\d+)_"
    r"(?P<id>[a-f0-9]+)\.log$"
)


# ---------------------------------------------------------------------------
# Allgemeine Hilfsfunktionen
# ---------------------------------------------------------------------------

def _log_func(log=None):
    return log if callable(log) else print


def _log(log, message):
    try:
        log(str(message))
    except Exception:
        print(str(message))


def _normalise_tool(tool: str) -> str:
    name = str(tool).strip().lower()
    if name not in TOOL_ALIASES:
        raise ValueError(
            f"Unbekanntes Tool {name!r}. "
            f"Erlaubt: {', '.join(sorted(TOOL_ALIASES))}"
        )
    return TOOL_ALIASES[name]


def bash_quote(value) -> str:
    return shlex.quote(str(value))


def _is_windows() -> bool:
    return platform.system().lower() == "windows"


def _command_display(command) -> str:
    return subprocess.list2cmdline([str(x) for x in command])


def _run(
    command,
    *,
    log=None,
    check=True,
    cwd=None,
    capture=True,
    env=None,
    timeout=None,
):
    """Führt einen Prozess aus und protokolliert dessen Ausgabe."""
    log = _log_func(log)
    command = [str(x) for x in command]
    _log(log, "Starte: " + _command_display(command))

    try:
        result = subprocess.run(
            command,
            cwd=str(cwd) if cwd else None,
            env=env,
            text=True,
            stdout=subprocess.PIPE if capture else None,
            stderr=subprocess.STDOUT if capture else None,
            errors="replace",
            check=False,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(
            f"Zeitlimit überschritten: {_command_display(command)}"
        ) from exc
    except OSError as exc:
        raise RuntimeError(
            f"Befehl konnte nicht gestartet werden: "
            f"{_command_display(command)}\n{exc}"
        ) from exc

    output = (result.stdout or "").strip() if capture else ""
    if output:
        _log(log, output)

    if check and result.returncode != 0:
        raise RuntimeError(
            f"Befehl fehlgeschlagen (Exit-Code {result.returncode}):\n"
            f"{_command_display(command)}\n{output or '(keine Ausgabe)'}"
        )
    return result


def _clean_wsl_output(value) -> str:
    return (
        (value or "")
        .replace("\x00", "")
        .replace("\ufeff", "")
        .replace("\r", "")
        .strip()
    )


def _normalise_repo_url(url: str) -> str:
    """Normalisiert gängige HTTPS-, SSH- und Git-Remote-URLs."""
    value = (url or "").strip()
    if not value:
        return ""

    if value.startswith("git@") and ":" in value:
        host_path = value.split("@", 1)[1]
        host, path = host_path.split(":", 1)
    else:
        parsed = urlparse(value)
        host = parsed.hostname or parsed.netloc
        path = parsed.path

    host = host.lower().rstrip(".")
    path = path.strip("/").removesuffix(".git").lower()
    if not host or not path:
        return ""
    return f"{host}/{path}"


# ---------------------------------------------------------------------------
# WSL2 und Ubuntu
# ---------------------------------------------------------------------------

def _find_wsl() -> str:
    if not _is_windows():
        raise RuntimeError("WSL wird nur unter Windows benötigt.")

    candidates = [
        shutil.which("wsl.exe"),
        str(
            Path(os.environ.get("WINDIR", r"C:\Windows"))
            / "System32" / "wsl.exe"
        ),
    ]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return candidate

    raise RuntimeError("wsl.exe wurde nicht gefunden.")


def _wsl(arguments, *, log=None, check=True, capture=True):
    return _run(
        [_find_wsl(), *arguments],
        log=log,
        check=check,
        capture=capture,
    )


def _ask_install_wsl(log, reason: str) -> bool:
    question = (
        f"{reason}\n\n"
        "Soll die Installation von WSL2 und Ubuntu angefordert werden?"
    )
    try:
        import tkinter as tk
        from tkinter import messagebox

        root = tk.Tk()
        root.withdraw()
        try:
            root.attributes("-topmost", True)
        except Exception:
            pass
        try:
            return bool(messagebox.askyesno(
                "WSL2 und Ubuntu",
                question,
                parent=root,
                icon="question",
            ))
        finally:
            root.destroy()
    except Exception as exc:
        _log(log, f"GUI-Abfrage nicht verfügbar: {exc}")
        print(question)
        try:
            answer = input("Jetzt installieren? [j/N]: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            return False
        return answer in {"j", "ja", "y", "yes"}


def _request_wsl_install(log, reason: str) -> None:
    if not _ask_install_wsl(log, reason):
        raise RuntimeError(
            "Installation abgebrochen. WSL/Ubuntu wurde nicht installiert."
        )

    result = _run(
        [_find_wsl(), "--install", "-d", WSL_DISTRO],
        log=log,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(
            "WSL/Ubuntu konnte nicht zur Installation angefordert werden."
        )
    raise RuntimeError(
        "WSL/Ubuntu wurde zur Installation angefordert. "
        "Schließe die Einrichtung ab und starte das Modul erneut."
    )


def ensure_wsl(log=None) -> None:
    if not _is_windows():
        return

    log = _log_func(log)
    wsl_exe = _find_wsl()

    status = _run([wsl_exe, "--status"], log=log, check=False)
    if status.returncode != 0:
        _request_wsl_install(log, "WSL ist nicht eingerichtet oder nicht verfügbar.")

    result = _wsl(["--list", "--quiet"], log=log, check=False)
    if result.returncode != 0:
        raise RuntimeError("WSL-Distributionen konnten nicht gelesen werden.")

    distros = [
        line.strip().lstrip("*").strip()
        for line in _clean_wsl_output(result.stdout).splitlines()
        if line.strip()
    ]
    if not any(x.lower() == WSL_DISTRO.lower() for x in distros):
        _request_wsl_install(log, "Ubuntu wurde in WSL nicht gefunden.")

    result = _wsl(["--list", "--verbose"], log=log, check=False)
    if result.returncode != 0:
        raise RuntimeError("Der WSL-Status konnte nicht ermittelt werden.")

    ubuntu_line = next(
        (
            line.strip()
            for line in _clean_wsl_output(result.stdout).splitlines()
            if line.strip().lstrip("*").split()
            and line.strip().lstrip("*").split()[0].lower()
            == WSL_DISTRO.lower()
        ),
        "",
    )
    if not ubuntu_line:
        raise RuntimeError("Ubuntu konnte nicht eindeutig erkannt werden.")

    columns = ubuntu_line.lstrip("*").split()
    if not columns or columns[-1] not in {"1", "2"}:
        raise RuntimeError(f"Unbekannte WSL-Version: {ubuntu_line!r}")

    if columns[-1] != "2":
        _log(log, "Ubuntu wird auf WSL2 umgestellt.")
        _wsl(["--set-version", WSL_DISTRO, "2"], log=log)

    ready = _wsl(
        ["-d", WSL_DISTRO, "--exec", "bash", "-lc", "printf WSL_READY"],
        log=log,
        check=False,
    )
    if ready.returncode != 0 or "WSL_READY" not in _clean_wsl_output(ready.stdout):
        raise RuntimeError(
            "Ubuntu ist noch nicht vollständig eingerichtet. "
            "Öffne Ubuntu und schließe die Ersteinrichtung ab."
        )

    _log(log, "WSL2 und Ubuntu sind verfügbar.")


# ---------------------------------------------------------------------------
# Zielordner und Startskripte
# ---------------------------------------------------------------------------

def get_tool_destination(base_dir, tool: str) -> Path:
    return (
        Path(base_dir).expanduser().resolve()
        / TOOL_DIRECTORIES[_normalise_tool(tool)]
    )


def _script_candidates(tool: str):
    return ("s4",) if _normalise_tool(tool) == "s4" else ("s3",)


def _find_script(root: Path, tool: str):
    root = Path(root)
    folders = ("", "simplebuild4", "s3_ncam_bonecrew", "s3")
    matches = []

    for folder in folders:
        base = root / folder if folder else root
        for name in _script_candidates(tool):
            candidate = base / name
            if candidate.is_file():
                matches.append(candidate.resolve())

    matches = list(dict.fromkeys(matches))
    if len(matches) > 1:
        raise RuntimeError(
            "Mehrere mögliche Startskripte gefunden. Abbruch:\n"
            + "\n".join(str(x) for x in matches)
        )
    return matches[0] if matches else None


# ---------------------------------------------------------------------------
# Repository-Prüfung
# ---------------------------------------------------------------------------

def _git_command(git: str, destination: Path, *args):
    return [git, "-C", str(destination), *args]


def _git_status(git: str, destination: Path, log=None) -> str:
    result = _run(
        _git_command(
            git, destination, "status", "--porcelain=v1",
            "--untracked-files=normal",
        ),
        log=log,
    )
    return result.stdout or ""


def _inspect_repository(git, destination, expected_url, log=None):
    _run(
        _git_command(git, destination, "rev-parse", "--is-inside-work-tree"),
        log=log,
    )

    remote = _run(
        _git_command(git, destination, "remote", "get-url", "origin"),
        log=log,
        check=False,
    )
    current_url = (remote.stdout or "").strip() if remote.returncode == 0 else ""

    if not current_url:
        raise RuntimeError(
            f"Keine lesbare origin-URL im Repository: {destination}"
        )

    current = _normalise_repo_url(current_url)
    expected = _normalise_repo_url(expected_url)
    if not current or not expected or current != expected:
        raise RuntimeError(
            "Repository-Identität stimmt nicht überein.\n"
            f"Ziel: {destination}\n"
            f"Vorhanden: {current_url}\n"
            f"Erwartet: {expected_url}"
        )

    status = _git_status(git, destination, log)
    if status:
        _log(
            log,
            "WARNUNG: Lokale Änderungen oder unversionierte Dateien "
            "gefunden; sie werden nicht verändert.",
        )
        _log(log, status)

    branch = _run(
        _git_command(git, destination, "branch", "--show-current"),
        log=log,
    )
    commit = _run(
        _git_command(git, destination, "rev-parse", "--short", "HEAD"),
        log=log,
    )
    _log(log, f"Branch: {(branch.stdout or '').strip() or '(detached HEAD)'}")
    _log(log, f"Commit: {(commit.stdout or '').strip()}")
    return current_url


def ensure_repository(tool, destination, log=None, repo_url=None) -> Path:
    log = _log_func(log)
    canonical = _normalise_tool(tool)
    selected_url = (repo_url or REPOSITORIES[canonical]).strip()

    if not selected_url or not _normalise_repo_url(selected_url):
        raise ValueError("Die Repository-URL ist leer oder ungültig.")

    dest = Path(destination).expanduser().resolve()
    git = shutil.which("git") or shutil.which("git.exe")
    if not git:
        raise RuntimeError("Git wurde nicht gefunden. Keine Installation ausgeführt.")

    if dest.exists() and not dest.is_dir():
        raise RuntimeError(f"Das Ziel ist keine Verzeichnisstruktur: {dest}")

    if dest.exists() and (dest / ".git").exists():
        _log(log, f"Prüfe Repository: {dest}")
        _inspect_repository(git, dest, selected_url, log)

        # Fetch aktualisiert Remote-Referenzen, nicht ausgecheckte Dateien.
        # --prune wird bewusst nicht verwendet.
        try:
            _run(
                _git_command(git, dest, "fetch", "origin"),
                log=log,
            )
        except RuntimeError as exc:
            _log(log, f"WARNUNG: Fetch fehlgeschlagen: {exc}")
            if FETCH_FAILURE_IS_FATAL:
                raise
        return dest

    if dest.exists() and any(dest.iterdir()):
        script = _find_script(dest, canonical)
        if script is None:
            raise RuntimeError(
                f"Nicht leeres, unbekanntes Zielverzeichnis: {dest}. "
                "Es wurde nichts verändert."
            )
        _log(log, f"Verwende Quellen ohne Git-Metadaten: {dest}")
        _log(log, "WARNUNG: Repository-Identität kann nicht geprüft werden.")
        return dest

    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() and any(dest.iterdir()):
        raise RuntimeError(f"Ziel wurde während der Prüfung verändert: {dest}")

    _run(
        [git, "clone", "--depth", "1", selected_url, str(dest)],
        log=log,
    )
    _inspect_repository(git, dest, selected_url, log)
    return dest


def prepare_tool(
    tool,
    destination=None,
    log=None,
    repo_url=None,
    *,
    base_dir=None,
) -> Path:
    log = _log_func(log)
    canonical = _normalise_tool(tool)

    if destination is None:
        if base_dir is None:
            raise ValueError("Gib destination oder base_dir an.")
        destination = get_tool_destination(base_dir, canonical)

    if _is_windows():
        ensure_wsl(log=log)

    result = ensure_repository(
        canonical, destination, log=log, repo_url=repo_url
    )
    _log(log, f"{canonical} ist bereit: {result}")
    return result


# ---------------------------------------------------------------------------
# Diagnose
# ---------------------------------------------------------------------------

def diagnose_system(log=None) -> dict:
    """Diagnostiziert die lokale Umgebung ohne Builds oder Installationen."""
    log = _log_func(log)
    report = {
        "timestamp": datetime.now().astimezone().isoformat(),
        "platform": platform.platform(),
        "python": platform.python_version(),
        "git": shutil.which("git") or shutil.which("git.exe"),
        "wsl": None,
        "native_terminals": {},
    }

    _log(log, f"Plattform: {report['platform']}")
    _log(log, f"Python: {report['python']}")
    _log(log, f"Git: {report['git'] or 'nicht gefunden'}")

    if _is_windows():
        try:
            result = _run(
                [_find_wsl(), "--list", "--verbose"],
                log=log,
                check=False,
            )
            report["wsl"] = {
                "available": result.returncode == 0,
                "output": _clean_wsl_output(result.stdout),
                "returncode": result.returncode,
            }
        except Exception as exc:
            report["wsl"] = {"available": False, "error": str(exc)}
        _log(log, f"WSL verfügbar: {report['wsl']['available']}")
    else:
        for name in (
            "x-terminal-emulator",
            "gnome-terminal",
            "konsole",
            "xfce4-terminal",
        ):
            report["native_terminals"][name] = shutil.which(name)
        report["script"] = shutil.which("script")
        _log(log, f"Terminal-Emulatoren: {report['native_terminals']}")
        _log(log, f"script: {report['script'] or 'nicht gefunden'}")

    return report


def diagnose_tool(tool, base_dir=None, destination=None, log=None) -> dict:
    """Prüft ein Tool, ohne es zu klonen, zu fetchen oder zu starten."""
    log = _log_func(log)
    canonical = _normalise_tool(tool)

    if destination is None:
        if base_dir is None:
            raise ValueError("Gib base_dir oder destination an.")
        destination = get_tool_destination(base_dir, canonical)

    dest = Path(destination).expanduser().resolve()
    result = {
        "tool": canonical,
        "destination": str(dest),
        "exists": dest.exists(),
        "repository": "nicht geprüft",
        "script": None,
        "git_status": None,
        "origin": None,
        "errors": [],
    }

    _log(log, f"Diagnose: {canonical}")
    _log(log, f"Ziel: {dest}")

    if not dest.exists():
        result["repository"] = "fehlt"
        result["errors"].append("Zielverzeichnis existiert nicht.")
        _log(log, "Zielverzeichnis fehlt; Diagnose beendet.")
        return result

    if not dest.is_dir():
        result["repository"] = "kein Verzeichnis"
        result["errors"].append("Ziel ist kein Verzeichnis.")
        return result

    try:
        script = _find_script(dest, canonical)
        result["script"] = str(script) if script else None
        if script:
            _log(log, f"Startskript: {script}")
        else:
            result["errors"].append("Kein eindeutiges Startskript gefunden.")
    except Exception as exc:
        result["errors"].append(str(exc))

    git = shutil.which("git") or shutil.which("git.exe")
    if (dest / ".git").exists():
        if not git:
            result["repository"] = "Git fehlt"
            result["errors"].append("Git wurde nicht gefunden.")
        else:
            try:
                remote = _run(
                    _git_command(git, dest, "remote", "get-url", "origin"),
                    log=log,
                    check=False,
                )
                result["origin"] = (
                    (remote.stdout or "").strip()
                    if remote.returncode == 0 else None
                )

                expected = REPOSITORIES[canonical]
                if (
                    not result["origin"]
                    or _normalise_repo_url(result["origin"])
                    != _normalise_repo_url(expected)
                ):
                    result["repository"] = "Identität nicht bestätigt"
                    result["errors"].append(
                        "origin fehlt oder stimmt nicht mit der "
                        "konfigurierten Repository-URL überein."
                    )
                else:
                    result["repository"] = "Git-Remote stimmt überein"

                status = _git_status(git, dest, log)
                result["git_status"] = status
                if status:
                    _log(log, "Lokale Änderungen/unversionierte Dateien gefunden.")
                else:
                    _log(log, "Git-Arbeitsverzeichnis ist sauber.")
            except Exception as exc:
                result["errors"].append(str(exc))
    else:
        result["repository"] = "ohne Git-Metadaten"
        _log(log, "WARNUNG: Repository-Identität kann nicht geprüft werden.")

    return result


# ---------------------------------------------------------------------------
# Build-Logs und Statusübersicht
# ---------------------------------------------------------------------------

def _log_directory() -> Path:
    return Path(__file__).resolve().parent / LOG_DIRECTORY_NAME


def list_build_logs(tool=None) -> list[dict]:
    """Listet vorhandene Logs auf. Löscht oder verändert keine Dateien."""
    log_dir = _log_directory()
    if not log_dir.is_dir():
        return []

    canonical = _normalise_tool(tool) if tool is not None else None
    entries = []

    for path in log_dir.glob("*.log"):
        match = BUILD_LOG_PATTERN.match(path.name)
        if not match:
            # Fremde oder ältere Logformate bleiben sichtbar.
            if canonical is None or path.name.startswith(canonical + "_"):
                try:
                    entries.append({
                        "tool": canonical or "unbekannt",
                        "path": str(path),
                        "modified": path.stat().st_mtime,
                        "status": "Format nicht erkannt",
                        "exit_code": None,
                    })
                except OSError:
                    continue
            continue

        tool_name = match.group("tool")
        if canonical is not None and tool_name != canonical:
            continue

        try:
            content = path.read_text(encoding="utf-8", errors="replace")
            modified = path.stat().st_mtime
        except OSError:
            continue

        status, exit_code = _status_from_log(content)
        entries.append({
            "tool": tool_name,
            "path": str(path),
            "modified": modified,
            "status": status,
            "exit_code": exit_code,
        })

    return sorted(entries, key=lambda item: item["modified"], reverse=True)


def _status_from_log(content: str) -> tuple[str, int | None]:
    """Ermittelt Status nur anhand expliziter Logmarker."""
    matches = re.findall(
        r"SIMPLEBUILD_FINISHED:exit=(-?\d+)",
        content,
    )
    if matches:
        exit_code = int(matches[-1])
        return (
            ("erfolgreich beendet" if exit_code == 0 else "fehlgeschlagen"),
            exit_code,
        )

    if "SIMPLEBUILD_STARTED" in content:
        return "läuft oder unerwartet beendet – nicht eindeutig feststellbar", None

    return "Status nicht feststellbar", None


def get_build_status(tool=None) -> list[dict]:
    """Gibt den aus den Logs ermittelten Status zurück.

    Ein fehlender Endmarker wird nicht als Beweis für einen laufenden Build
    interpretiert.
    """
    return list_build_logs(tool)


def analyse_build_log(log_path) -> dict:
    """Liefert eine einfache, heuristische Fehleranalyse eines Logs."""
    path = Path(log_path).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(f"Logdatei nicht gefunden: {path}")

    content = path.read_text(encoding="utf-8", errors="replace")
    lines = content.splitlines()
    patterns = (
        "error:",
        "fatal:",
        "failed",
        "failure",
        "no such file",
        "permission denied",
        "command not found",
        "not found",
        "undefined reference",
        "cannot",
        "fehler",
        "fehlgeschlagen",
    )

    hits = []
    for index, line in enumerate(lines, start=1):
        lower = line.lower()
        if any(pattern in lower for pattern in patterns):
            hits.append({"line": index, "text": line[:1000]})

    status, exit_code = _status_from_log(content)
    return {
        "path": str(path),
        "status": status,
        "exit_code": exit_code,
        "line_count": len(lines),
        "matches": hits,
        "note": (
            "Heuristische Suche; Treffer sind keine garantierte "
            "Root-Cause-Diagnose."
        ),
    }


def write_diagnostic_report(report, destination=None) -> Path:
    """Schreibt einen einfachen Diagnosebericht als Textdatei."""
    target = (
        Path(destination).expanduser().resolve()
        if destination is not None
        else _log_directory() / (
            "diagnose_" + datetime.now().strftime("%Y%m%d_%H%M%S_%f") + ".txt"
        )
    )
    target.parent.mkdir(parents=True, exist_ok=True)

    lines = [
        "Simplebuild Diagnosebericht",
        f"Erstellt: {datetime.now().astimezone().isoformat()}",
        "=" * 70,
    ]
    for key, value in report.items():
        lines.append(f"{key}: {value}")

    with target.open("x", encoding="utf-8", errors="replace") as file:
        file.write("\n".join(lines) + "\n")
    return target


# ---------------------------------------------------------------------------
# Pfadkonvertierung
# ---------------------------------------------------------------------------

def _wsl_path(path: Path, log=None) -> str:
    if not _is_windows():
        return str(Path(path).expanduser().resolve())

    log = _log_func(log)
    windows_path = str(Path(path).expanduser().resolve())
    encoded = base64.b64encode(windows_path.encode("utf-8")).decode("ascii")

    result = _run(
        [
            _find_wsl(), "-d", WSL_DISTRO, "--exec", "bash", "-lc",
            'decoded=$(printf "%s" "$1" | base64 -d) && wslpath -a "$decoded"',
            "wslpath", encoded,
        ],
        log=log,
        check=False,
    )
    converted = _clean_wsl_output(result.stdout)

    if result.returncode != 0 or not converted.startswith("/"):
        raise RuntimeError(
            f"Windows-Pfad konnte nicht konvertiert werden: {windows_path}\n"
            f"Ausgabe: {converted!r}"
        )
    return converted


# ---------------------------------------------------------------------------
# Build-Start und interaktives Logging
# ---------------------------------------------------------------------------

def _start_command(tool: str) -> str:
    return "./s4" if _normalise_tool(tool) == "s4" else "./s3 menu"


def _create_build_log(tool: str, source: Path, log) -> Path:
    canonical = _normalise_tool(tool)
    directory = _log_directory()
    directory.mkdir(parents=True, exist_ok=True)

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    unique = uuid.uuid4().hex[:8]
    path = directory / f"{canonical}_build_{stamp}_{unique}.log"

    with path.open("x", encoding="utf-8", errors="replace") as file:
        file.write(f"{canonical} Build-Log\n")
        file.write(f"Start: {datetime.now().astimezone().isoformat()}\n")
        file.write(f"Quellpfad: {source}\n")
        file.write(f"Befehl: {_start_command(canonical)}\n")
        file.write("=" * 78 + "\n")

    _log(log, f"Build-Log: {path}")
    return path


def _build_shell_command(tool, script_dir, script_name, log_path) -> str:
    canonical = _normalise_tool(tool)
    start = _start_command(canonical)

    inner = (
        f"cd {bash_quote(script_dir)} || exit 90; "
        f"if [ ! -f {bash_quote('./' + script_name)} ]; then "
        f"echo {bash_quote('FEHLER: Startdatei fehlt: ' + script_name)}; "
        "exit 91; fi; "
        f"chmod +x {bash_quote(script_name)} || exit 92; "
        "printf '\\nSIMPLEBUILD_STARTED\\n'; "
        f"{start}; result=$?; "
        "printf '\\nSIMPLEBUILD_FINISHED:exit=%s\\n' \"$result\"; "
        "exit \"$result\""
    )

    # -a erhält den vorhandenen Logkopf. -e liefert den Exit-Code weiter.
    # Die abschließende Shell bleibt offen, ohne den Build-Status zu fälschen.
    return (
        "if ! command -v script >/dev/null 2>&1; then "
        "echo 'FEHLER: util-linux script fehlt.'; "
        f"echo {bash_quote('Logdatei: ' + log_path)}; "
        "echo 'Bitte util-linux separat installieren.'; exec bash; fi; "
        f"script -q -e -a -c {bash_quote(inner)} {bash_quote(log_path)}; "
        "script_result=$?; "
        "printf '\\nTerminal-Sitzung beendet (Exit-Code: %s)\\n' "
        "\"$script_result\"; exec bash"
    )


def _launch_native_linux(tool, source, log):
    canonical = _normalise_tool(tool)
    script = _find_script(source, canonical)
    if script is None:
        raise FileNotFoundError(
            f"Kein eindeutiges Startskript für {canonical} gefunden."
        )

    log_path = _create_build_log(canonical, source, log)
    shell = _build_shell_command(
        canonical, str(script.parent), script.name, str(log_path)
    )

    terminals = [
        ("x-terminal-emulator", ["-e", "bash", "-lc", shell]),
        ("gnome-terminal", ["--", "bash", "-lc", shell]),
        ("konsole", ["-e", "bash", "-lc", shell]),
        (
            "xfce4-terminal",
            ["--command", f"bash -lc {shlex.quote(shell)}"],
        ),
    ]

    errors = []
    for executable, args in terminals:
        path = shutil.which(executable)
        if not path:
            continue
        try:
            process = subprocess.Popen(
                [path, *args],
                start_new_session=True,
                close_fds=True,
            )
            _log(log, f"Terminal gestartet: {path}, PID={process.pid}")
            _log(log, f"Build-Log: {log_path}")
            return
        except OSError as exc:
            errors.append(f"{executable}: {exc}")

    raise RuntimeError(
        "Kein Terminal konnte gestartet werden.\n"
        + ("\n".join(errors) if errors else "Kein unterstützter Emulator gefunden.")
    )


def launch_tool_menu(tool: str, source: Path, log=None) -> None:
    """Startet ein interaktives Menü in einem neuen Terminal."""
    log = _log_func(log)
    canonical = _normalise_tool(tool)
    source = Path(source).expanduser().resolve()

    script = _find_script(source, canonical)
    if script is None:
        raise FileNotFoundError(
            f"Kein eindeutiges Startskript für {canonical} in {source} gefunden."
        )

    if not _is_windows():
        _launch_native_linux(canonical, source, log)
        return

    ensure_wsl(log=log)
    wsl_dir = _wsl_path(script.parent, log)
    log_path = _create_build_log(canonical, source, log)
    wsl_log = _wsl_path(log_path, log)

    shell = _build_shell_command(
        canonical, wsl_dir, script.name, wsl_log
    )
    command = [
        _find_wsl(), "-d", WSL_DISTRO, "--", "bash", "-lc", shell
    ]

    _log(log, f"Tool: {canonical}")
    _log(log, f"WSL-Quellpfad: {wsl_dir}")
    _log(log, f"Startbefehl: {_start_command(canonical)}")
    _log(log, f"Build-Log: {log_path}")

    try:
        process = subprocess.Popen(
            command,
            creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0),
        )
    except OSError as exc:
        try:
            with log_path.open("a", encoding="utf-8") as file:
                file.write(f"\nSTARTFEHLER: {exc}\n")
        except OSError:
            pass
        raise RuntimeError(f"WSL konnte nicht gestartet werden: {exc}") from exc

    _log(log, f"WSL-Prozess gestartet, PID={process.pid}")


# ---------------------------------------------------------------------------
# Gemeinsamer Einstiegspunkt und Rückwärtskompatibilität
# ---------------------------------------------------------------------------

def run_tool(
    tool,
    base_dir,
    *,
    destination=None,
    repo_url=None,
    log=None,
) -> Path:
    log = _log_func(log)
    canonical = _normalise_tool(tool)
    target = (
        Path(destination).expanduser().resolve()
        if destination is not None
        else get_tool_destination(base_dir, canonical)
    )
    source = prepare_tool(
        canonical, target, log=log, repo_url=repo_url
    )
    launch_tool_menu(canonical, source, log=log)
    return source


def launch_simplebuild(tool_path, menu_arg="menu", log=None) -> None:
    path = Path(tool_path).expanduser().resolve()
    if menu_arg != "menu":
        raise ValueError("Der unterstützte Menüparameter ist 'menu'.")

    source = path if path.is_dir() else path.parent
    lower = str(source).lower()

    if "simplebuild4" in lower or "s4" in lower:
        tool = "s4"
    elif "ncam" in lower or "bonecrew" in lower:
        tool = "ncam"
    elif "speedy005" in lower or "s3_simplebuild" in lower:
        tool = "s3_speedy005"
    else:
        tool = "s3_gorgone"

    launch_tool_menu(tool, source, log=log)


def run_simplebuild(tool: str, base_dir, log=None) -> Path:
    return run_tool(tool, base_dir, log=log)


# ---------------------------------------------------------------------------
# Selbsttest
# ---------------------------------------------------------------------------

def self_test(log=None) -> bool:
    """Prüft Konfiguration und Hilfsfunktionen, startet aber keine Builds."""
    log = _log_func(log)
    checks = []

    try:
        aliases = {
            "s3": "s3_gorgone",
            "s3_gorgone": "s3_gorgone",
            "s3_simplebuild": "s3_speedy005",
            "s3_speedy005": "s3_speedy005",
            "s4": "s4",
            "ncam": "ncam",
        }
        for alias, expected in aliases.items():
            assert _normalise_tool(alias) == expected
        checks.append("Tool-Aliase")

        assert TOOL_DIRECTORIES["s3_gorgone"] != TOOL_DIRECTORIES["s3_speedy005"]
        checks.append("Getrennte S3-Ziele")

        assert _normalise_repo_url(
            "https://github.com/example/repo.git"
        ) == _normalise_repo_url(
            "git@github.com:example/repo.git"
        )
        checks.append("Git-URL-Normalisierung")

        assert _status_from_log(
            "SIMPLEBUILD_STARTED\nSIMPLEBUILD_FINISHED:exit=0"
        ) == ("erfolgreich beendet", 0)
        assert _status_from_log(
            "SIMPLEBUILD_STARTED\nSIMPLEBUILD_FINISHED:exit=2"
        ) == ("fehlgeschlagen", 2)
        checks.append("Build-Statusmarker")

        for item in checks:
            _log(log, f"OK: {item}")
        _log(log, "Selbsttest erfolgreich.")
        return True

    except Exception as exc:
        _log(log, f"SELBSTTEST FEHLGESCHLAGEN: {exc}")
        return False


if __name__ == "__main__":
    print("Simplebuild Launcher – Ausbaustufe 3")
    print("=" * 50)
    print("Der Selbsttest startet keine Builds und verändert keine Repositorys.")
    raise SystemExit(0 if self_test() else 1)
