"""OSCam Patch Manager module fragment: Git executable, repository, branch, and tree operations."""

# Explicit module imports (kept broad for compatibility with legacy callbacks).
import ctypes
import glob
import importlib.util
import io
import json
import locale
import os
import platform
import random
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import zipfile
from PyQt6.QtCore import QDateTime, QEasingCurve, QObject, QPoint, QPropertyAnimation, QRect, QSize, QThread, QTime, QTimer, QUrl, Qt, pyqtProperty, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QIcon, QPainter, QPen, QPolygon, QTextCursor
from PyQt6.QtWidgets import QApplication, QCheckBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QFrame, QGraphicsColorizeEffect, QGridLayout, QGroupBox, QHBoxLayout, QLabel, QLayout, QLineEdit, QMessageBox, QProgressBar, QPushButton, QSizePolicy, QSlider, QTextEdit, QVBoxLayout, QWidget, QWidgetItem
from datetime import datetime, timezone

def find_git_executable():
    """
    Findet Git unter Windows und Linux.
    """

    # 1. PATH
    git_path = shutil.which("git")

    if git_path:
        return os.path.abspath(git_path)

    # 2. Windows
    if os.name == "nt":

        candidates = []

        program_files = os.environ.get("ProgramFiles")
        program_files_x86 = os.environ.get("ProgramFiles(x86)")
        local_app_data = os.environ.get("LOCALAPPDATA")

        if program_files:
            candidates.extend([
                os.path.join(
                    program_files,
                    "Git",
                    "cmd",
                    "git.exe"
                ),
                os.path.join(
                    program_files,
                    "Git",
                    "bin",
                    "git.exe"
                ),
            ])

        if program_files_x86:
            candidates.extend([
                os.path.join(
                    program_files_x86,
                    "Git",
                    "cmd",
                    "git.exe"
                ),
                os.path.join(
                    program_files_x86,
                    "Git",
                    "bin",
                    "git.exe"
                ),
            ])

        if local_app_data:
            candidates.extend([
                os.path.join(
                    local_app_data,
                    "Programs",
                    "Git",
                    "cmd",
                    "git.exe"
                ),
                os.path.join(
                    local_app_data,
                    "Programs",
                    "Git",
                    "bin",
                    "git.exe"
                ),
            ])

        for path in candidates:
            if os.path.isfile(path):
                return os.path.abspath(path)

    # 3. Linux / macOS
    else:

        candidates = [
            "/usr/bin/git",
            "/usr/local/bin/git",
            "/bin/git",
            "/opt/homebrew/bin/git",
        ]

        for path in candidates:
            if os.path.isfile(path):
                return os.path.abspath(path)

    raise RuntimeError(
        "Git wurde nicht gefunden.\n\n"
        "Bitte Git installieren und sicherstellen, "
        "dass 'git' im PATH verfügbar ist."
    )


# ============================================================
# GIT COMMAND
# ============================================================

def git_command(
    args,
    cwd=None,
    allowed_returncodes=(0,),
    timeout=600,
    env=None
):
    """
    Sicherer Git-Aufruf ohne shell=True.

    env:
        Optionales Environment für Git.
        Wird unter anderem für GIT_ASKPASS und
        temporäre GitHub-Credentials verwendet.
    """

    import os
    import subprocess

    git_executable = find_git_executable()

    command = [
        git_executable
    ] + [
        str(arg)
        for arg in args
    ]

    # ------------------------------------------------------------
    # Environment
    # ------------------------------------------------------------
    if env is None:
        env = os.environ.copy()

    try:

        result = subprocess.run(
            command,
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
            shell=False,
            timeout=timeout,
            env=env
        )

    except FileNotFoundError as exc:

        raise RuntimeError(
            "Git konnte nicht gestartet werden.\n\n"
            f"Git-Pfad:\n{git_executable}"
        ) from exc

    except subprocess.TimeoutExpired as exc:

        raise RuntimeError(
            "Der Git-Befehl wurde wegen eines "
            "Timeouts abgebrochen."
        ) from exc

    except OSError as exc:

        raise RuntimeError(
            "Fehler beim Starten von Git:\n"
            f"{exc}"
        ) from exc

    if result.returncode not in allowed_returncodes:

        stderr = (
            result.stderr or ""
        ).strip()

        stdout = (
            result.stdout or ""
        ).strip()

        error_text = (
            stderr
            or stdout
            or f"Git Returncode: {result.returncode}"
        )

        # --------------------------------------------------------
        # Sicherheitsfilter
        #
        # Falls ein Secret versehentlich in einer Git-Fehlermeldung
        # auftaucht, wird es vor der Weitergabe entfernt.
        # --------------------------------------------------------
        try:

            token = str(
                env.get(
                    "OSC_GITHUB_TOKEN",
                    ""
                )
                or ""
            )

            username = str(
                env.get(
                    "OSC_GITHUB_USERNAME",
                    ""
                )
                or ""
            )

            if token:

                error_text = error_text.replace(
                    token,
                    "***TOKEN***"
                )

            if username and token:

                error_text = error_text.replace(
                    f"{username}:{token}",
                    f"{username}:***TOKEN***"
                )

        except Exception:
            pass

        raise RuntimeError(
            "Git-Befehl fehlgeschlagen:\n\n"
            + " ".join(command)
            + "\n\n"
            + error_text
        )

    return result


# ============================================================
# KOMPATIBILITÄTS-ALIAS
# ============================================================

def _git_command(
    args,
    cwd=None,
    allowed_returncodes=(0,),
    timeout=600,
    env=None
):
    """
    Kompatibilitäts-Wrapper für ältere Funktionen,
    die noch _git_command() verwenden.
    """

    return git_command(
        args,
        cwd=cwd,
        allowed_returncodes=allowed_returncodes,
        timeout=timeout,
        env=env,
    )


# ============================================================
# GIT VERFÜGBARKEIT
# ============================================================

def check_git_available():

    git_executable = find_git_executable()

    result = subprocess.run(
        [
            git_executable,
            "--version"
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        stdin=subprocess.DEVNULL,
        text=True,
        encoding="utf-8",
        errors="replace",
        shell=False,
        timeout=30
    )

    if result.returncode != 0:

        error_text = (
            result.stderr
            or result.stdout
            or "Unbekannter Fehler"
        ).strip()

        raise RuntimeError(
            "Git konnte nicht ausgeführt werden.\n\n"
            + error_text
        )

    return (
        git_executable,
        result.stdout.strip()
    )


# ============================================================
# GIT COMMIT-ID
# ============================================================

def git_commit_id(repo_dir, ref):
    """
    Liefert die vollständige 40-stellige Commit-ID
    eines Git-Refs.

    Beispiel:

        git_commit_id(repo, "master")
        git_commit_id(repo, "origin/master")
        git_commit_id(repo, "FETCH_HEAD")
    """

    if not repo_dir:
        raise RuntimeError(
            "Kein Git-Repository angegeben."
        )

    if not os.path.isdir(repo_dir):
        raise RuntimeError(
            "Git-Repository existiert nicht:\n"
            + str(repo_dir)
        )

    if not ref:
        raise RuntimeError(
            "Kein Git-Ref angegeben."
        )

    result = git_command(
        [
            "rev-parse",
            "--verify",
            f"{ref}^{{commit}}"
        ],
        cwd=repo_dir
    )

    commit = (
        result.stdout or ""
    ).strip()

    # Sicherheit:
    # Eine gültige SHA-1 Commit-ID besteht aus
    # genau 40 Hex-Zeichen.
    if not re.fullmatch(
        r"[0-9a-fA-F]{40}",
        commit
    ):
        raise RuntimeError(
            "Ungültige Git-Commit-ID für Ref:\n"
            f"{ref}\n\n"
            f"Ergebnis:\n{commit}"
        )

    return commit.lower()


# ============================================================
# OSCAM VERSION AUS globals.h
# ============================================================

def get_oscam_version(repo_dir, commit):

    if not repo_dir or not commit:
        return ""

    try:

        result = git_command(
            [
                "show",
                f"{commit}:globals.h"
            ],
            cwd=repo_dir
        )

        content = result.stdout or ""

    except Exception:
        return ""

    patterns = [
        r'#define\s+CS_VERSION\s+"([^"]+)"',
        r'#define\s+OSCAM_VERSION\s+"([^"]+)"',
        r'#define\s+VERSION\s+"([^"]+)"',
    ]

    for pattern in patterns:

        match = re.search(
            pattern,
            content
        )

        if match:
            return match.group(1).strip()

    return ""

def _git_remote_default_branch(url, timeout=30):
    """
    Ermittelt den Default-Branch eines Git-Repositorys.

    Priorität:
        1. git ls-remote --symref <url> HEAD
        2. Fallback auf 'master'
        3. Fallback auf 'main'

    Bei Netzwerk-/Verbindungsfehlern wird nicht unnötig mehrfach
    gegen den Server verbunden.
    """

    import re

    # ---------------------------------------------------------
    # URL prüfen
    # ---------------------------------------------------------

    if not url or not str(url).strip():
        raise RuntimeError(
            "Git-Repository-URL fehlt."
        )

    url = str(url).strip()

    # ---------------------------------------------------------
    # Default-Branch über HEAD ermitteln
    # ---------------------------------------------------------

    try:
        result = _git_command(
            [
                "ls-remote",
                "--symref",
                url,
                "HEAD"
            ],
            timeout=timeout
        )

    except Exception as exc:
        raise RuntimeError(
            "Git-Repository ist nicht erreichbar.\n"
            f"Repository: {url}\n"
            f"Timeout: {timeout}s\n"
            f"{exc}"
        ) from exc

    # ---------------------------------------------------------
    # HEAD auswerten
    # ---------------------------------------------------------

    output = (
        (result.stdout or "")
        + "\n"
        + (result.stderr or "")
    )

    match = re.search(
        r"ref:\s+refs/heads/([^\s]+)\s+HEAD",
        output
    )

    if match:
        branch = match.group(1).strip()

        if branch:
            return branch

    # ---------------------------------------------------------
    # Fallback:
    # HEAD war erreichbar, aber der Default-Branch konnte
    # daraus nicht ermittelt werden.
    #
    # Jetzt darf master/main geprüft werden.
    # ---------------------------------------------------------

    for candidate in ("master", "main"):

        try:
            result = _git_command(
                [
                    "ls-remote",
                    "--heads",
                    url,
                    f"refs/heads/{candidate}"
                ],
                timeout=timeout
            )

        except Exception:
            # Kein zweiter großer Fehlertext notwendig.
            # Wenn HEAD funktioniert hat, versuchen wir den
            # nächsten Kandidaten.
            continue

        if (
            result.returncode == 0
            and (result.stdout or "").strip()
        ):
            return candidate

    # ---------------------------------------------------------
    # Repository erreichbar, aber Branch unbekannt
    # ---------------------------------------------------------

    raise RuntimeError(
        "Default-Branch konnte nicht ermittelt werden.\n"
        f"Repository: {url}\n"
        "Git meldet keinen gültigen HEAD-Branch "
        "und weder 'master' noch 'main' konnten gefunden werden."
    )

def _git_add_remote(repo_dir, name, url):
    """
    Fügt einen Git-Remote hinzu oder aktualisiert dessen URL.

    Existiert der Remote bereits:
        git remote set-url

    Existiert er noch nicht:
        git remote add
    """

    # ---------------------------------------------------------
    # Eingaben prüfen
    # ---------------------------------------------------------

    if not repo_dir:
        raise RuntimeError(
            "Git-Repository-Verzeichnis fehlt."
        )

    if not name or not str(name).strip():
        raise RuntimeError(
            "Git-Remote-Name fehlt."
        )

    if not url or not str(url).strip():
        raise RuntimeError(
            f"Git-URL für Remote '{name}' fehlt."
        )

    name = str(name).strip()
    url = str(url).strip()

    # ---------------------------------------------------------
    # Vorhandene Remotes abfragen
    # ---------------------------------------------------------

    try:
        result = _git_command(
            ["remote"],
            cwd=repo_dir
        )
    except Exception as exc:
        raise RuntimeError(
            "Vorhandene Git-Remotes konnten nicht "
            "ermittelt werden.\n"
            f"Repository: {repo_dir}\n"
            f"{exc}"
        ) from exc

    names = {
        line.strip()
        for line in (result.stdout or "").splitlines()
        if line.strip()
    }

    # ---------------------------------------------------------
    # Remote aktualisieren
    # ---------------------------------------------------------

    if name in names:

        try:
            _git_command(
                [
                    "remote",
                    "set-url",
                    name,
                    url
                ],
                cwd=repo_dir
            )

        except Exception as exc:
            raise RuntimeError(
                f"Git-Remote '{name}' konnte nicht "
                "aktualisiert werden.\n"
                f"URL: {url}\n"
                f"{exc}"
            ) from exc

    # ---------------------------------------------------------
    # Remote neu anlegen
    # ---------------------------------------------------------

    else:

        try:
            _git_command(
                [
                    "remote",
                    "add",
                    name,
                    url
                ],
                cwd=repo_dir
            )

        except Exception as exc:
            raise RuntimeError(
                f"Git-Remote '{name}' konnte nicht "
                "angelegt werden.\n"
                f"URL: {url}\n"
                f"{exc}"
            ) from exc

def _git_fetch_branch(repo_dir, remote, branch, timeout=30):
    """
    Lädt exakt einen Branch von einem Git-Remote.

    Gibt den lokalen Remote-Ref zurück:
        refs/remotes/<remote>/<branch>

    Fehler werden mit Repository/Remote/Branch verständlich
    weitergereicht.
    """

    import re

    # ---------------------------------------------------------
    # Eingaben prüfen
    # ---------------------------------------------------------

    if not repo_dir:
        raise RuntimeError(
            "Git-Repository-Verzeichnis fehlt."
        )

    if not remote:
        raise RuntimeError(
            "Git-Remote fehlt."
        )

    if not branch:
        raise RuntimeError(
            "Git-Branch fehlt."
        )

    # Branch-Namen dürfen keine problematischen Steuerzeichen
    # enthalten.
    if "\n" in branch or "\r" in branch:
        raise RuntimeError(
            f"Ungültiger Git-Branchname: {branch!r}"
        )

    # ---------------------------------------------------------
    # Refs erzeugen
    # ---------------------------------------------------------

    refspec = (
        f"+refs/heads/{branch}:"
        f"refs/remotes/{remote}/{branch}"
    )

    ref = (
        f"refs/remotes/{remote}/{branch}"
    )

    # ---------------------------------------------------------
    # Fetch
    # ---------------------------------------------------------

    try:
        _git_command(
            [
                "fetch",
                "--no-tags",
                "--prune",
                remote,
                refspec
            ],
            cwd=repo_dir,
            timeout=timeout
        )

    except Exception as exc:
        raise RuntimeError(
            f"Git-Fetch fehlgeschlagen.\n"
            f"Remote: {remote}\n"
            f"Branch: {branch}\n"
            f"Timeout: {timeout}s\n"
            f"{exc}"
        ) from exc

    # ---------------------------------------------------------
    # Prüfen, ob der Remote-Ref wirklich vorhanden ist
    # ---------------------------------------------------------

    try:
        proc = _git_command(
            [
                "rev-parse",
                "--verify",
                ref
            ],
            cwd=repo_dir,
            timeout=10
        )

        commit = (proc.stdout or "").strip()

    except Exception as exc:
        raise RuntimeError(
            f"Der gefetchte Branch konnte nicht aufgelöst werden.\n"
            f"Remote: {remote}\n"
            f"Branch: {branch}\n"
            f"Ref: {ref}\n"
            f"{exc}"
        ) from exc

    # ---------------------------------------------------------
    # SHA prüfen
    # ---------------------------------------------------------

    if not re.fullmatch(
        r"[0-9a-fA-F]{40}",
        commit
    ):
        raise RuntimeError(
            f"Ungültiger Commit für {remote}/{branch}:\n"
            f"{commit or '(leer)'}"
        )

    return ref

def _git_tree_sha(repo_dir, ref):
    """
    Ermittelt den vollständigen Commit-SHA einer Git-Referenz.

    Beispiel:
        refs/remotes/origin/master
        -> 39e78a4688b3... (40 Zeichen)

    Es wird kein Netzwerkzugriff durchgeführt.
    """

    import re

    if not repo_dir:
        raise RuntimeError(
            "Git-Repository-Verzeichnis fehlt."
        )

    if not ref or not str(ref).strip():
        raise RuntimeError(
            "Git-Referenz fehlt."
        )

    ref = str(ref).strip()

    try:
        result = _git_command(
            [
                "rev-parse",
                "--verify",
                ref
            ],
            cwd=repo_dir,
            timeout=10
        )

    except Exception as exc:
        raise RuntimeError(
            "Git-Referenz konnte nicht aufgelöst werden.\n"
            f"Repository: {repo_dir}\n"
            f"Ref: {ref}\n"
            f"{exc}"
        ) from exc

    sha = (
        result.stdout or ""
    ).strip().splitlines()[0] if (
        result.stdout or ""
    ).strip() else ""

    if not re.fullmatch(
        r"[0-9a-fA-F]{40}",
        sha
    ):
        raise RuntimeError(
            "Ungültiger Git-Commit-SHA.\n"
            f"Ref: {ref}\n"
            f"Ergebnis: {sha or '(leer)'}"
        )

    return sha.lower()

