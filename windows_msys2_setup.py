
# -*- coding: utf-8 -*-
"""Cross-platform Simplebuild launcher and repository preparation helpers.

Windows:
- Prüft WSL2 und Ubuntu und fragt vor einer Installationsanforderung nach.
- Klont ausschließlich in fehlende oder leere Zielverzeichnisse.
- Verwendet passende vorhandene Git-Repositories weiter.
- Aktualisiert Git-Referenzen per fetch, ohne lokale Dateien zurückzusetzen.
- Ändert keine vorhandenen origin-URLs automatisch.
- Bricht bei unpassenden oder nicht eindeutig zuordenbaren Repositories ab.
- Startet Simplebuild in einem separaten WSL-Konsolenfenster.

Linux:
- Verwendet vorhandene Quellen und startet Simplebuild in einem nativen
  Terminal, sofern ein unterstützter Terminal-Emulator verfügbar ist.

Getrennte S3-Ziele:
- Gorgone S3:    <base_dir>/s3_gorgone
- Speedy005 S3:  <base_dir>/s3_speedy005

Startbefehle:
- S3:   ./s3 menu
- S4:   ./s4
- NCam: ./s3 menu

Dieses Modul installiert keine zusätzlichen Ubuntu-Buildpakete.
"""

from __future__ import annotations

import base64
import os
import platform
import shlex
import shutil
import subprocess
from pathlib import Path
from urllib.parse import urlparse


# ---------------------------------------------------------------------------
# Repository-Konfiguration und Rückwärtskompatibilität
# ---------------------------------------------------------------------------

REPOSITORIES = {
    # Bestehender Aufruf bleibt Gorgone S3.
    "s3": "https://github.com/gorgone/s3_releases.git",
    "s3_gorgone": "https://github.com/gorgone/s3_releases.git",

    # Speedy005 S3 erhält ein eigenes Repository und Zielverzeichnis.
    "s3_simplebuild": (
        "https://github.com/speedy005/"
        "speedy005-S3-Simplebuild.git"
    ),
    "s3_speedy005": (
        "https://github.com/speedy005/"
        "speedy005-S3-Simplebuild.git"
    ),

    "s4": "https://git.streamboard.tv/common/simplebuild4.git",
    "ncam": (
        "https://github.com/speedy005/"
        "s3_ncam_bonecrew_test.git"
    ),
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


# ---------------------------------------------------------------------------
# Allgemeine Hilfsfunktionen
# ---------------------------------------------------------------------------

def _log_func(log=None):
    return log or print


def _normalise_tool(tool: str) -> str:
    """Normalisiert Toolnamen und erhält die bisherigen Aliasnamen."""
    name = str(tool).strip().lower()

    if name not in TOOL_ALIASES:
        allowed = ", ".join(TOOL_ALIASES)
        raise ValueError(
            f"Unbekanntes Tool {name!r}. Erlaubt: {allowed}"
        )

    return TOOL_ALIASES[name]


def _run(
    command,
    *,
    log,
    check=True,
    cwd=None,
    capture=True,
    env=None,
):
    """Führt einen Prozess aus und protokolliert seine Ausgabe."""
    command = [str(item) for item in command]

    log("Starte: " + subprocess.list2cmdline(command))

    result = subprocess.run(
        command,
        cwd=str(cwd) if cwd else None,
        env=env,
        text=True,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.STDOUT if capture else None,
        errors="replace",
        check=False,
    )

    output = (result.stdout or "").strip() if capture else ""

    if output:
        log(output)

    if check and result.returncode != 0:
        raise RuntimeError(
            f"Befehl fehlgeschlagen (Exit-Code {result.returncode}):\n"
            f"{subprocess.list2cmdline(command)}\n"
            f"Ausgabe:\n{output or '(keine Ausgabe)'}"
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
    """Vergleicht HTTPS-, SSH- und Git-URLs ohne .git-Suffix."""
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

    return (
        host.lower().rstrip("/")
        + "/"
        + path.strip("/").removesuffix(".git").lower()
    )


def bash_quote(value) -> str:
    """Maskiert einen Wert für die Verwendung in einem Bash-Befehl."""
    return shlex.quote(str(value))


# ---------------------------------------------------------------------------
# WSL2 und Ubuntu
# ---------------------------------------------------------------------------

def _find_wsl() -> str:
    if platform.system() != "Windows":
        raise RuntimeError("WSL wird nur unter Windows benötigt.")

    candidates = [
        shutil.which("wsl.exe"),
        str(
            Path(os.environ.get("WINDIR", r"C:\Windows"))
            / "System32"
            / "wsl.exe"
        ),
    ]

    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return candidate

    raise RuntimeError(
        "wsl.exe wurde nicht gefunden. Installiere WSL mit:\n"
        "wsl --install -d Ubuntu"
    )


def _wsl(arguments, *, log, check=True, capture=True):
    return _run(
        [_find_wsl(), *arguments],
        log=log,
        check=check,
        capture=capture,
    )


def _ask_install_wsl(log, reason: str) -> bool:
    question = (
        f"{reason}\n\n"
        "Soll jetzt die Installation von WSL2 und Ubuntu angefordert werden?\n\n"
        "Windows kann Administratorrechte oder einen Neustart verlangen. "
        "Die Ubuntu-Ersteinrichtung muss gegebenenfalls anschließend "
        "manuell abgeschlossen werden."
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
            answer = messagebox.askyesno(
                "WSL2 und Ubuntu installieren?",
                question,
                parent=root,
                icon="question",
            )
        finally:
            root.destroy()

        return bool(answer)

    except Exception as exc:
        log(
            "GUI-Abfrage für WSL nicht verfügbar; "
            f"Konsolenabfrage: {exc}"
        )
        print(question)

        try:
            answer = input("Jetzt installieren? [j/N]: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            return False

        return answer in {"j", "ja", "y", "yes"}


def _request_wsl_install(log, reason: str) -> None:
    """Fordert die Installation nur nach ausdrücklicher Zustimmung an."""
    if not _ask_install_wsl(log, reason):
        raise RuntimeError(
            "Installation abgebrochen. WSL2 und Ubuntu wurden "
            "nicht installiert."
        )

    result = _run(
        [_find_wsl(), "--install", "-d", WSL_DISTRO],
        log=log,
        check=False,
    )

    if result.returncode != 0:
        raise RuntimeError(
            "WSL2/Ubuntu konnte nicht zur Installation angefordert werden. "
            "Prüfe Administratorrechte und führe gegebenenfalls "
            "'wsl --install -d Ubuntu' manuell aus."
        )

    raise RuntimeError(
        "Die Installation von WSL2/Ubuntu wurde angefordert. "
        "Schließe die Einrichtung ab und starte das Tool erneut."
    )


def ensure_wsl(log=None) -> None:
    """Prüft WSL, Ubuntu und die WSL2-Version."""
    if platform.system() != "Windows":
        return

    log = _log_func(log)
    wsl_exe = _find_wsl()

    status = _run(
        [wsl_exe, "--status"],
        log=log,
        check=False,
    )

    if status.returncode != 0:
        _request_wsl_install(
            log,
            "WSL ist noch nicht eingerichtet oder konnte nicht gestartet werden.",
        )

    result = _wsl(
        ["--list", "--quiet"],
        log=log,
        check=False,
    )

    if result.returncode != 0:
        raise RuntimeError(
            "WSL-Distributionen konnten nicht gelesen werden. "
            "Prüfe 'wsl --list --verbose'."
        )

    distros = [
        line.strip().lstrip("*").strip()
        for line in _clean_wsl_output(result.stdout).splitlines()
        if line.strip()
    ]

    if not any(item.lower() == WSL_DISTRO.lower() for item in distros):
        _request_wsl_install(
            log,
            "WSL ist vorhanden, aber Ubuntu wurde nicht gefunden.",
        )

    result = _wsl(
        ["--list", "--verbose"],
        log=log,
        check=False,
    )

    if result.returncode != 0:
        raise RuntimeError(
            "Der WSL-Status von Ubuntu konnte nicht ermittelt werden."
        )

    ubuntu_line = next(
        (
            line.strip()
            for line in _clean_wsl_output(result.stdout).splitlines()
            if line.strip().lstrip("*").strip().split()
            and line.strip().lstrip("*").strip().split()[0].lower()
            == WSL_DISTRO.lower()
        ),
        "",
    )

    if not ubuntu_line:
        raise RuntimeError(
            "Ubuntu konnte in der WSL-Liste nicht erkannt werden. "
            "Prüfe 'wsl --list --verbose'."
        )

    columns = ubuntu_line.lstrip("*").split()

    if not columns or columns[-1] not in {"1", "2"}:
        raise RuntimeError(
            "Die WSL-Version von Ubuntu konnte nicht zuverlässig "
            f"ermittelt werden: {ubuntu_line!r}"
        )

    if columns[-1] != "2":
        log("Ubuntu wird auf WSL2 umgestellt.")
        _wsl(
            ["--set-version", WSL_DISTRO, "2"],
            log=log,
        )

    ready = _wsl(
        [
            "-d", WSL_DISTRO,
            "--exec", "bash", "-lc",
            "printf WSL_READY",
        ],
        log=log,
        check=False,
    )

    if (
        ready.returncode != 0
        or "WSL_READY" not in _clean_wsl_output(ready.stdout)
    ):
        raise RuntimeError(
            "Ubuntu ist noch nicht vollständig eingerichtet. "
            "Öffne Ubuntu, schließe die Einrichtung ab und starte erneut."
        )

    log("WSL2 und Ubuntu sind verfügbar.")


# ---------------------------------------------------------------------------
# Zielordner und Startskripte
# ---------------------------------------------------------------------------

def get_tool_destination(base_dir, tool: str) -> Path:
    """Liefert den festen, toolabhängigen Zielordner."""
    canonical = _normalise_tool(tool)
    base = Path(base_dir).expanduser().resolve()
    return base / TOOL_DIRECTORIES[canonical]


def _script_candidates(tool: str):
    canonical = _normalise_tool(tool)
    return ("s4",) if canonical == "s4" else ("s3",)


def _find_script(root: Path, tool: str):
    """Sucht das Startskript in üblichen Repository-Unterordnern."""
    root = Path(root)
    folders = ("", "simplebuild4", "s3_ncam_bonecrew", "s3")

    for folder in folders:
        base = root / folder if folder else root

        for name in _script_candidates(tool):
            candidate = base / name

            if candidate.is_file():
                return candidate

    return None


# ---------------------------------------------------------------------------
# Git-Prüfungen und Repository-Vorbereitung
# ---------------------------------------------------------------------------

def ensure_repository(
    tool: str,
    destination,
    log=None,
    repo_url=None,
) -> Path:
    """Prüft ein Repository und klont nur in ein fehlendes/leeres Ziel.

    Sicherheit:
    - Keine Checkout-, Reset-, Pull- oder Clean-Operationen.
    - Keine automatische Änderung einer vorhandenen origin-URL.
    - Ein vorhandenes Repository mit fremder oder fehlender origin-URL
      wird nicht automatisch umkonfiguriert.
    - Fetch aktualisiert Referenzen, nicht die ausgecheckten Arbeitsdateien.
    - Nicht leere, unbekannte Ordner werden nicht verändert.
    """
    log = _log_func(log)
    canonical = _normalise_tool(tool)

    selected_url = (repo_url or REPOSITORIES[canonical]).strip()

    if not selected_url:
        raise ValueError("Die Repository-URL darf nicht leer sein.")

    dest = Path(destination).expanduser().resolve()
    git = shutil.which("git") or shutil.which("git.exe")

    if not git:
        raise RuntimeError(
            "Git wurde nicht gefunden. Installiere Git oder ergänze "
            "Git im PATH."
        )

    if dest.exists() and not dest.is_dir():
        raise RuntimeError(
            f"Das Repository-Ziel ist keine Verzeichnisstruktur: {dest}"
        )

    # Vorhandenes Git-Repository: Identität prüfen, Dateien erhalten.
    if (dest / ".git").exists():
        log(f"Vorhandenes Git-Repository: {dest}")

        _run(
            [git, "-C", str(dest), "rev-parse", "--is-inside-work-tree"],
            log=log,
        )

        remote = _run(
            [git, "-C", str(dest), "remote", "get-url", "origin"],
            log=log,
            check=False,
        )

        current_url = (
            (remote.stdout or "").strip()
            if remote.returncode == 0
            else ""
        )

        if not current_url:
            raise RuntimeError(
                "Das vorhandene Git-Repository besitzt keine origin-URL.\n"
                f"Ordner: {dest}\n"
                f"Erwartetes Repository: {selected_url}\n\n"
                "Aus Sicherheitsgründen wird origin nicht automatisch "
                "hinzugefügt. Prüfe das Repository und konfiguriere "
                "origin bei Bedarf manuell."
            )

        if (
            _normalise_repo_url(current_url)
            != _normalise_repo_url(selected_url)
        ):
            raise RuntimeError(
                "Das Zielverzeichnis enthält ein anderes Git-Repository.\n"
                f"Ordner: {dest}\n"
                f"Vorhandenes Repository: {current_url}\n"
                f"Ausgewähltes Repository: {selected_url}\n\n"
                "Wähle einen anderen Zielordner oder sichere und "
                "verschiebe den vorhandenen Ordner."
            )

        # Fetch verändert keine ausgecheckten Arbeitsdateien.
        try:
            _run(
                [git, "-C", str(dest), "fetch", "origin", "--prune"],
                log=log,
            )
            log(
                "Git-Fetch erfolgreich. Lokale Arbeitsdateien und "
                "der aktuelle Checkout wurden nicht zurückgesetzt."
            )
        except RuntimeError as exc:
            log(
                "WARNUNG: Git-Fetch fehlgeschlagen. "
                "Das vorhandene Repository bleibt erhalten."
            )
            log(str(exc))

        return dest

    # Vorhandene Quellen ohne Git-Metadaten nicht überschreiben.
    if dest.exists() and any(dest.iterdir()):
        if _find_script(dest, canonical) is not None:
            log(
                "Verwende vorhandenes Quellverzeichnis ohne Git-Metadaten: "
                f"{dest}"
            )
            log(
                "WARNUNG: Ohne .git lässt sich die Repository-Identität "
                "nicht zuverlässig überprüfen."
            )
            return dest

        raise RuntimeError(
            "Das Zielverzeichnis ist nicht leer und enthält kein "
            "erkennbares passendes Git-Repository:\n"
            f"{dest}\n"
            "Sichere den Inhalt oder wähle einen anderen Zielordner."
        )

    # Erst nach der Prüfung fehlende Verzeichnisse erzeugen.
    dest.parent.mkdir(parents=True, exist_ok=True)

    log(f"Klone {selected_url} nach {dest}")

    _run(
        [git, "clone", "--depth", "1", selected_url, str(dest)],
        log=log,
    )

    log(f"Repository wurde erfolgreich geklont: {dest}")
    return dest


def prepare_tool(
    tool: str,
    destination=None,
    log=None,
    repo_url=None,
    *,
    base_dir=None,
) -> Path:
    """Prüft gegebenenfalls WSL und bereitet das ausgewählte Tool vor.

    Rückwärtskompatibel:
        prepare_tool("s3", "/pfad/zum/ziel")
        prepare_tool("s4", "/pfad/zum/ziel")

    Neue Verwendung mit automatisch getrennten Zielordnern:
        prepare_tool("s3_gorgone", base_dir="/pfad/zu/tools")
        prepare_tool("s3_speedy005", base_dir="/pfad/zu/tools")
    """
    log = _log_func(log)
    canonical = _normalise_tool(tool)

    if destination is None:
        if base_dir is None:
            raise ValueError(
                "Gib destination oder base_dir an, um den Zielordner "
                "festzulegen."
            )
        destination = get_tool_destination(base_dir, canonical)

    if platform.system() == "Windows":
        ensure_wsl(log=log)

    path = ensure_repository(
        canonical,
        destination,
        log=log,
        repo_url=repo_url,
    )

    log(f"{canonical} ist bereit: {path}")
    return path


# ---------------------------------------------------------------------------
# Windows-Pfade nach WSL konvertieren
# ---------------------------------------------------------------------------

def _wsl_path(path: Path, log) -> str:
    windows_path = str(Path(path).expanduser().resolve())

    encoded_path = base64.b64encode(
        windows_path.encode("utf-8")
    ).decode("ascii")

    result = _run(
        [
            _find_wsl(),
            "-d", WSL_DISTRO,
            "--exec", "bash", "-lc",
            'decoded=$(printf "%s" "$1" | base64 -d) '
            '&& wslpath -a "$decoded"',
            "wslpath",
            encoded_path,
        ],
        log=log,
        check=False,
        capture=True,
    )

    converted = _clean_wsl_output(result.stdout)

    if result.returncode != 0 or not converted.startswith("/"):
        raise RuntimeError(
            "Windows-Pfad konnte nicht nach WSL konvertiert werden.\n"
            f"Windows-Pfad: {windows_path}\n"
            f"Exit-Code: {result.returncode}\n"
            f"Ausgabe: {converted!r}"
        )

    return converted


# ---------------------------------------------------------------------------
# Gemeinsame Startlogik mit interaktivem Build-Logging
# ---------------------------------------------------------------------------

from datetime import datetime


def _start_command(tool: str) -> str:
    """Gibt den Startbefehl für das ausgewählte Tool zurück."""
    canonical = _normalise_tool(tool)
    return "./s4" if canonical == "s4" else "./s3 menu"


def _create_build_log(tool: str, source: Path, log) -> Path:
    """Erzeugt eine eindeutige Build-Logdatei neben diesem Modul."""
    canonical = _normalise_tool(tool)
    log_dir = Path(__file__).resolve().parent / "build_logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    log_path = log_dir / f"{canonical}_build_{stamp}.log"

    with log_path.open(
        "x",
        encoding="utf-8",
        errors="replace",
        newline="\n",
    ) as file:
        file.write(f"{canonical} Build-Log\n")
        file.write(f"Start: {datetime.now().astimezone().isoformat()}\n")
        file.write(f"Quellpfad: {source}\n")
        file.write(f"Befehl: {_start_command(canonical)}\n")
        file.write("=" * 78 + "\n")
        file.flush()

    log(f"Build-Log erstellt: {log_path}")
    return log_path


def _build_shell_command(
    tool: str,
    script_dir: str,
    script_name: str,
    log_path: str,
) -> str:
    """Baut den Shell-Befehl für ein interaktives, protokolliertes Menü.

    util-linux script stellt ein Pseudo-Terminal bereit. Dadurch bleiben
    interaktive Menüs und Tastatureingaben funktionsfähig, während die
    Terminalausgabe in die Logdatei geschrieben wird.
    """
    canonical = _normalise_tool(tool)
    start_command = _start_command(canonical)

    # Die eigentliche Sitzung läuft in einer Bash-Unter-Shell.
    # Der Exit-Code wird unmittelbar nach Simplebuild gespeichert.
    inner_command = (
        f"cd {bash_quote(script_dir)} || exit 90; "
        f"if [ ! -f {bash_quote('./' + script_name)} ]; then "
        f"echo {bash_quote('FEHLER: Startdatei fehlt: ' + script_name)}; "
        "exit 91; "
        "fi; "
        f"chmod +x {bash_quote(script_name)} || exit 92; "
        f"{start_command}; "
        "result=$?; "
        "printf '\\nSimplebuild beendet (Exit-Code: %s)\\n' \"$result\"; "
        "exit \"$result\""
    )

    # script protokolliert die PTY-Sitzung. -a verhindert, dass der
    # zuvor geschriebene Logkopf überschrieben wird.
    # -e gibt den Exit-Code des gestarteten Befehls weiter.
    #
    # Die abschließende interaktive Shell läuft außerhalb von script.
    # Damit bleibt das Fenster nach dem Ende des Builds offen, ohne dass
    # deren Eingaben oder Ausgaben das Build-Log verfälschen.
    return (
        f"if ! command -v script >/dev/null 2>&1; then "
        f"echo {bash_quote('FEHLER: Das Programm script fehlt in Ubuntu.')}; "
        f"echo {bash_quote('Logdatei: ' + log_path)}; "
        "echo 'Installiere bei Bedarf util-linux mit: "
        "sudo apt install util-linux'; "
        "exec bash; "
        "fi; "
        f"script -q -e -a -c {bash_quote(inner_command)} "
        f"{bash_quote(log_path)}; "
        "script_result=$?; "
        "printf '\\nProtokollierung beendet (Exit-Code: %s)\\n' "
        "\"$script_result\"; "
        "exec bash"
    )


def _launch_native_linux(tool: str, source: Path, log) -> None:
    """Startet Simplebuild in einem nativen Linux-Terminal mit Build-Log."""
    canonical = _normalise_tool(tool)
    script = _find_script(source, canonical)

    if script is None:
        raise FileNotFoundError(
            f"Kein Startskript für {canonical} in {source} gefunden."
        )

    log_path = _create_build_log(canonical, source, log)

    command = _build_shell_command(
        canonical,
        str(script.parent),
        script.name,
        str(log_path),
    )

    terminals = [
        ("x-terminal-emulator", ["-e", "bash", "-lc", command]),
        ("gnome-terminal", ["--", "bash", "-lc", command]),
        ("konsole", ["-e", "bash", "-lc", command]),
        (
            "xfce4-terminal",
            ["--command", f"bash -lc {shlex.quote(command)}"],
        ),
    ]

    errors = []

    for executable, arguments in terminals:
        terminal_path = shutil.which(executable)

        if not terminal_path:
            continue

        try:
            process = subprocess.Popen(
                [terminal_path, *arguments],
                start_new_session=True,
                close_fds=True,
            )

            log(f"Terminal gestartet: {terminal_path}")
            log(f"PID: {process.pid}")
            log(f"Tool: {canonical}")
            log(f"Startbefehl: {_start_command(canonical)}")
            log(f"Build-Log: {log_path}")
            return

        except OSError as exc:
            errors.append(f"{executable}: {exc}")
            log(f"Terminalstart fehlgeschlagen: {executable}: {exc}")

    details = (
        "\n".join(errors)
        if errors
        else "Kein unterstützter Terminal-Emulator gefunden."
    )

    raise RuntimeError(
        "Simplebuild konnte nicht in einem neuen Linux-Terminal "
        "gestartet werden.\n"
        f"{details}\n"
        f"Das vorbereitete Log befindet sich unter: {log_path}"
    )


def launch_tool_menu(tool: str, source: Path, log=None) -> None:
    """Startet Simplebuild interaktiv und protokolliert die Sitzung.

    Windows:
        Separates WSL2-Konsolenfenster; Logdatei liegt unter build_logs.

    Linux:
        Separates natives Terminal; Logdatei liegt unter build_logs.

    Das Modul installiert keine Pakete automatisch.
    """
    log = _log_func(log)
    canonical = _normalise_tool(tool)
    source = Path(source).expanduser().resolve()

    script = _find_script(source, canonical)

    if script is None:
        raise FileNotFoundError(
            f"Startskript für {canonical} wurde in {source} nicht gefunden."
        )

    if platform.system() != "Windows":
        _launch_native_linux(canonical, source, log)
        return

    ensure_wsl(log=log)

    wsl_script_dir = _wsl_path(script.parent, log)
    script_name = script.name

    log_path = _create_build_log(canonical, source, log)
    wsl_log_path = _wsl_path(log_path, log)

    linux_command = _build_shell_command(
        canonical,
        wsl_script_dir,
        script_name,
        wsl_log_path,
    )

    command = [
        _find_wsl(),
        "-d", WSL_DISTRO,
        "--",
        "bash", "-lc", linux_command,
    ]

    log(f"WSL-Quellpfad: {wsl_script_dir}")
    log(f"WSL-Tool: {canonical}")
    log(f"Startbefehl: {_start_command(canonical)}")
    log(f"Build-Log: {log_path}")
    log("Starte Simplebuild in einem neuen WSL-Konsolenfenster.")

    try:
        process = subprocess.Popen(
            command,
            creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0),
        )
    except OSError as exc:
        message = f"WSL konnte nicht gestartet werden: {exc}"
        log(message)

        try:
            with log_path.open("a", encoding="utf-8", errors="replace") as file:
                file.write(f"\nSTARTFEHLER: {message}\n")
        except OSError:
            pass

        raise RuntimeError(message) from exc

    log(f"WSL-Prozess gestartet, PID={process.pid}")


# ---------------------------------------------------------------------------
# Gemeinsamer Einstiegspunkt
# ---------------------------------------------------------------------------

def run_tool(
    tool: str,
    base_dir,
    *,
    destination=None,
    repo_url=None,
    log=None,
) -> Path:
    """Bereitet das Repository vor und startet Simplebuild.

    Beispiele:
        run_tool("s3_gorgone", r"C:\\Simplebuild")
        run_tool("s3_speedy005", r"C:\\Simplebuild")
        run_tool("s4", r"C:\\Simplebuild")
        run_tool("ncam", r"C:\\Simplebuild")

    Die Quellverzeichnisse werden standardmäßig getrennt angelegt:
        s3_gorgone
        s3_speedy005
        s4
        ncam

    Rückgabe:
        Der vorbereitete Quellordner.
    """
    log = _log_func(log)
    canonical = _normalise_tool(tool)

    if destination is not None:
        target = Path(destination).expanduser().resolve()
    else:
        target = get_tool_destination(base_dir, canonical)

    source = prepare_tool(
        canonical,
        target,
        log=log,
        repo_url=repo_url,
    )

    launch_tool_menu(canonical, source, log=log)
    return source


# ---------------------------------------------------------------------------
# Rückwärtskompatible Einstiegspunkte
# ---------------------------------------------------------------------------

def launch_simplebuild(tool_path, menu_arg="menu", log=None) -> None:
    """Kompatibler Einstiegspunkt für ältere Aufrufer."""
    path = Path(tool_path).expanduser().resolve()

    if menu_arg != "menu":
        raise ValueError(
            "Der unterstützte Menüparameter ist 'menu'."
        )

    source = path if path.is_dir() else path.parent
    lower = str(source).lower()

    if "simplebuild4" in lower or "s4" in lower:
        tool = "s4"
    elif "ncam" in lower or "bonecrew" in lower:
        tool = "ncam"
    elif "speedy005" in lower or "s3_simplebuild" in lower:
        tool = "s3_speedy005"
    else:
        # Bestehendes Standardverhalten: S3 bedeutet Gorgone S3.
        tool = "s3_gorgone"

    launch_tool_menu(tool, source, log=log)


def run_simplebuild(tool: str, base_dir, log=None) -> Path:
    """Zusätzlicher Alias für den gemeinsamen Einstiegspunkt."""
    return run_tool(tool, base_dir, log=log)


if __name__ == "__main__":
    print(
        "Dieses Modul wird normalerweise von der Oberfläche importiert.\n"
        "Verwende run_tool(), prepare_tool() und launch_tool_menu()."
    )
