"""OSCam Patch Manager module fragment: Patch cleanup, creation, backup, and application."""

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

from git_manager import find_git_executable, git_command
from paths import (
    PATCH_MODIFIER,
    PLUGIN_DIR,
    STREAMREPO,
    PATCH_EMU_GIT_DIR,
    PATCH_FILE,
    get_selected_stream_repo_url,
)


import os
import subprocess
import re

repo = STREAMREPO
commit = "51f78f6ecb4ec5a0c06b2a6106b1744cfdc893e9"

print("[DEBUG] Repository:", repo)
print("[DEBUG] Repository existiert:", os.path.isdir(repo))
print("[DEBUG] Git-Verzeichnis:", os.path.isdir(os.path.join(repo, ".git")))

result = subprocess.run(
    [
        find_git_executable(),
        "-C", repo,
        "show",
        f"{commit}:globals.h",
    ],
    capture_output=True,
    text=True,
    encoding="utf-8",
    errors="replace",
)

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

from translations import TEXTS
from workers import safe_play

def get_patch_header(
    streamboard_repo=None,
    emu_repo=None,
    lang="de",
    modifier=None,
    streamboard_commit=None,
    log=print,
):
    """
    Erstellt den OSCam-Emu-Patch-Header.

    Format:
        # patch version: 2.26.09-11968-802 (51f78f6e)

    - OSCam-Version und Streamboard-Revision stammen aus CS_VERSION
      der globals.h des exakten Streamboard-Commits.
    - Die Emulator-Version stammt aus EMU_VERSION.
    - Commit-Hash und Datum stammen aus demselben Streamboard-Commit.
    - Der Commit wird nicht fest codiert.
    """

    import os
    import re
    import subprocess
    from datetime import datetime

    # ============================================================
    # LOGGING
    # ============================================================

    def write_log(message):
        try:
            log(str(message))
        except Exception:
            try:
                print(str(message))
            except Exception:
                pass

    # ============================================================
    # REPOSITORY-PFADE
    # ============================================================

    if streamboard_repo is None:
        streamboard_repo = STREAMREPO

    if emu_repo is None:
        emu_repo = PATCH_EMU_GIT_DIR

    streamboard_repo = os.path.abspath(
        os.path.expandvars(
            os.path.expanduser(str(streamboard_repo))
        )
    )

    emu_repo = os.path.abspath(
        os.path.expandvars(
            os.path.expanduser(str(emu_repo))
        )
    )

    if not os.path.isdir(streamboard_repo):
        raise RuntimeError(
            "Streamboard-Repository existiert nicht:\n"
            f"{streamboard_repo}"
        )

    if not os.path.isdir(emu_repo):
        raise RuntimeError(
            "OSCam-Emu-Repository existiert nicht:\n"
            f"{emu_repo}"
        )

    # ============================================================
    # GIT-FUNKTION ERMITTELN
    # ============================================================

    git_function = globals().get("_git_command")

    if not callable(git_function):
        git_function = globals().get("git_command")

    def git(args, cwd, allowed_returncodes=(0,)):
        """
        Führt Git über die vorhandene Projektfunktion aus.
        Verwendet subprocess als Fallback.
        """

        write_log(
            f"[PATCH HEADER] Git: {' '.join(map(str, args))}"
        )

        if callable(git_function):
            result = git_function(
                args,
                cwd=cwd,
                allowed_returncodes=allowed_returncodes,
            )

            if result is None:
                raise RuntimeError(
                    f"Git lieferte kein Ergebnis: {args}"
                )

            if result.returncode not in allowed_returncodes:
                raise RuntimeError(
                    f"Git-Befehl fehlgeschlagen: {args}\n"
                    f"{result.stderr or result.stdout or ''}"
                )

            return result

        # Fallback: Git direkt aufrufen, falls keine Projektfunktion
        # verfügbar ist.
        result = subprocess.run(
            ["git", *map(str, args)],
            cwd=cwd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
            check=False,
        )

        if result.returncode not in allowed_returncodes:
            raise RuntimeError(
                f"Git-Befehl fehlgeschlagen: {args}\n"
                f"{result.stderr or result.stdout or ''}"
            )

        return result

    # ============================================================
    # SPRACHE UND MODIFIER
    # ============================================================

    active_modifier = str(
        modifier or PATCH_MODIFIER or "unknown"
    ).strip()

    try:
        lang_dict = TEXTS.get(
            str(lang).lower(),
            TEXTS.get("en", {}),
        )
    except Exception:
        lang_dict = {}

    if not isinstance(lang_dict, dict):
        lang_dict = {}

    # ============================================================
    # EXAKTEN STREAMBOARD-COMMIT FESTLEGEN
    # ============================================================
    #
    # Vorrang hat der Commit, auf dem der Patch basiert.
    # Wenn keiner übergeben wurde, wird das lokale HEAD verwendet.
    #
    # WICHTIG:
    # Für den jeweils aktuellen Server-Commit muss create_patch()
    # den aktuellen Remote-HEAD ermitteln und hier übergeben.
    # Diese Funktion verändert den Patch-Basis-Commit nicht.
    # ============================================================

    exact_stream_commit = str(
        streamboard_commit or ""
    ).strip().lower()

    if not exact_stream_commit:
        result = git(
            ["rev-parse", "HEAD"],
            streamboard_repo,
        )
        exact_stream_commit = (
            result.stdout or ""
        ).strip().lower()

    if not re.fullmatch(
        r"[0-9a-f]{40}",
        exact_stream_commit,
    ):
        raise RuntimeError(
            "Ungültige Streamboard-Commit-ID:\n"
            f"{exact_stream_commit!r}"
        )

    git(
        [
            "cat-file",
            "-e",
            f"{exact_stream_commit}^{{commit}}",
        ],
        streamboard_repo,
    )

    commit = exact_stream_commit[:8]

    write_log(
        f"[PATCH HEADER] Streamboard-Commit: "
        f"{exact_stream_commit}"
    )

    # ============================================================
    # DATEI AUS DEM EXAKTEN COMMIT LESEN
    # ============================================================

    def read_git_file(commit_ref, filename):
        result = git(
            ["show", f"{commit_ref}:{filename}"],
            streamboard_repo,
        )

        content = result.stdout or ""

        if not content.strip():
            raise RuntimeError(
                f"{filename} ist leer oder konnte nicht gelesen werden."
            )

        return content

    # ============================================================
    # OSCAM-VERSION UND STREAMBOARD-REVISION
    # ============================================================
    #
    # Beispiel:
    # CS_VERSION "2.26.09-11968"
    # version = "2.26.09"
    # revision = "11968"
    #
    # Die Revision wird direkt aus CS_VERSION übernommen.
    # git describe wird absichtlich nicht verwendet.
    # ============================================================

    globals_content = read_git_file(
        exact_stream_commit,
        "globals.h",
    )

    version_match = re.search(
        r'^\s*#\s*define\s+CS_VERSION\s+"([^"]+)"',
        globals_content,
        re.MULTILINE,
    )

    if not version_match:
        raise RuntimeError(
            "CS_VERSION wurde in globals.h nicht gefunden.\n"
            f"Commit: {exact_stream_commit}"
        )

    cs_version = version_match.group(1).strip()
    parts = cs_version.split("-", 1)

    version = parts[0].strip()
    revision = parts[1].strip() if len(parts) > 1 else ""

    if not version or not revision:
        raise RuntimeError(
            f"Ungültiges CS_VERSION-Format: {cs_version!r}"
        )

    if not revision.isdigit():
        raise RuntimeError(
            "Die Streamboard-Revision in CS_VERSION ist "
            f"nicht numerisch: {revision!r}"
        )

    write_log(
        f"[PATCH HEADER] OSCam-Version: {version}"
    )
    write_log(
        f"[PATCH HEADER] Streamboard-Revision: {revision}"
    )
    write_log(
        f"[PATCH HEADER] CS_VERSION: {cs_version}"
    )

    # ============================================================
    # EMULATOR-VERSION SEPARAT ERMITTELN
    # ============================================================

    emu_rev = None

    emu_header = os.path.join(
        emu_repo,
        "module-emulator-osemu.h",
    )

    if os.path.isfile(emu_header):
        try:
            with open(
                emu_header,
                "r",
                encoding="utf-8",
                errors="replace",
            ) as handle:
                emu_content = handle.read()

            emu_match = re.search(
                r'^\s*#\s*define\s+EMU_VERSION\s+"?([^"\r\n]+)"?',
                emu_content,
                re.MULTILINE,
            )

            if emu_match:
                emu_rev = emu_match.group(1).strip()

        except OSError as exc:
            write_log(
                f"[PATCH HEADER] Emu-Version nicht lesbar: {exc}"
            )

    # Fallback: Wenn keine EMU_VERSION definiert ist, die
    # Kurz-ID des aktuellen lokalen Emu-HEADs verwenden.
    if not emu_rev:
        result = git(
            ["rev-parse", "--short=8", "HEAD"],
            emu_repo,
        )
        emu_rev = (
            result.stdout or ""
        ).strip()

    if not emu_rev:
        raise RuntimeError(
            "Die OSCam-Emu-Version konnte nicht ermittelt werden."
        )

    write_log(
        f"[PATCH HEADER] Emulator-Version: {emu_rev}"
    )

    # ============================================================
    # DATUM DES EXAKTEN STREAMBOARD-COMMITS
    # ============================================================

    result = git(
        [
            "show",
            "-s",
            "--format=%cI",
            exact_stream_commit,
        ],
        streamboard_repo,
    )

    commit_date = (result.stdout or "").strip()

    if not commit_date:
        raise RuntimeError(
            "Git hat kein Commit-Datum geliefert."
        )

    try:
        dt = datetime.fromisoformat(
            commit_date.replace("Z", "+00:00")
        )
        patch_date = dt.strftime("%Y-%m-%d")
    except ValueError as exc:
        raise RuntimeError(
            f"Ungültiges Commit-Datum: {commit_date!r}"
        ) from exc

    # ============================================================
    # MODIFIER-DATUM
    # ============================================================

    mod_date = datetime.now().strftime("%d/%m/%Y")

    # ============================================================
    # HEADER-LABELS
    # ============================================================

    version_label = lang_dict.get(
        "patch_version_header",
        "patch version",
    )

    date_label = lang_dict.get(
        "patch_date",
        "patch date",
    )

    modifier_label = lang_dict.get(
        "patch_modified_by",
        "patch modified by",
    )

    # ============================================================
    # HEADER ERZEUGEN
    # ============================================================

    
    header = (
        f"{version_label}: "
        f"{version}-{revision}-{emu_rev} ({commit})\n"
        f"{date_label}: {patch_date}\n"
        f"{modifier_label} "
        f"{active_modifier} ({mod_date})\n"
        f"patch base commit: {exact_stream_commit}\n"
    )


    write_log(
        "[PATCH HEADER] Header erfolgreich erzeugt:"
    )
    write_log(header)

    return header

def ensure_git_repository(
    repo_dir,
    repo_url,
    description="Repository",
    clone_timeout=1200,
    depth=None,
    log=print,
):
    """
    Stellt sicher, dass ein gültiges Git-Repository vorhanden ist.

    Verhalten:
      - Gültiges Repository vorhanden: weiterverwenden.
      - Verzeichnis fehlt: Repository klonen.
      - Verzeichnis existiert, ist aber leer: Repository klonen.
      - Nicht leerer, ungültiger Ordner: Fehler melden, nichts löschen.

    Unterstützt Windows und Linux. Verwendet kein shell=True.
    """
    import os
    import subprocess

    if not repo_dir:
        raise RuntimeError(
            f"{description}: Repository-Pfad fehlt."
        )

    if not repo_url:
        raise RuntimeError(
            f"{description}: Repository-URL fehlt."
        )

    repo_dir = os.path.abspath(
        os.path.expanduser(
            os.path.expandvars(str(repo_dir))
        )
    )

    git_executable = find_git_executable()

    if not git_executable:
        raise RuntimeError("Git wurde nicht gefunden.")

    def is_valid_git_repo(path):
        if not os.path.isdir(path):
            return False

        try:
            result = subprocess.run(
                [
                    git_executable,
                    "-C",
                    path,
                    "rev-parse",
                    "--is-inside-work-tree",
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=60,
                shell=False,
            )

            return (
                result.returncode == 0
                and result.stdout.strip().lower() == "true"
            )

        except (OSError, subprocess.SubprocessError):
            return False

    # --------------------------------------------------------
    # Vorhandenes Repository prüfen
    # --------------------------------------------------------

    if is_valid_git_repo(repo_dir):
        log(
            f"[GIT] {description}-Repository bereits vorhanden: "
            f"{repo_dir}"
        )
        return repo_dir

    # --------------------------------------------------------
    # Vorhandenen Zielpfad prüfen
    # --------------------------------------------------------

    if os.path.exists(repo_dir):
        if not os.path.isdir(repo_dir):
            raise RuntimeError(
                f"{description}: Der Repository-Pfad ist "
                f"kein Verzeichnis:\n{repo_dir}"
            )

        try:
            has_entries = any(os.scandir(repo_dir))
        except OSError as exc:
            raise RuntimeError(
                f"{description}: Verzeichnis kann nicht gelesen werden:\n"
                f"{repo_dir}\n{exc}"
            ) from exc

        if has_entries:
            raise RuntimeError(
                f"{description}: Der Ordner ist kein gültiges "
                f"Git-Repository und nicht leer:\n\n"
                f"{repo_dir}\n\n"
                "Bitte den Inhalt sichern und den Ordner manuell "
                "prüfen. Es werden keine Dateien automatisch gelöscht."
            )

    # --------------------------------------------------------
    # Übergeordnetes Verzeichnis erstellen
    # --------------------------------------------------------

    parent_dir = os.path.dirname(repo_dir)

    if parent_dir:
        os.makedirs(parent_dir, exist_ok=True)

    # --------------------------------------------------------
    # Git-Clone vorbereiten
    # --------------------------------------------------------

    command = [
        git_executable,
        "clone",
    ]

    if depth is not None:
        try:
            depth_value = int(depth)
        except (TypeError, ValueError):
            depth_value = 0

        if depth_value > 0:
            command.extend([
                "--depth",
                str(depth_value),
            ])

    command.extend([
        str(repo_url),
        repo_dir,
    ])

    log(
        f"[GIT] {description} fehlt – klone Repository:\n"
        f"      URL: {repo_url}\n"
        f"      Ziel: {repo_dir}\n"
        f"      Clone-Tiefe: "
        f"{depth if depth is not None else 'vollständig'}"
    )

    # --------------------------------------------------------
    # Klonen ausführen
    # --------------------------------------------------------

    try:
        result = subprocess.run(
            command,
            cwd=parent_dir or None,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
            shell=False,
            timeout=clone_timeout,
        )

    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(
            f"{description}: Git-Clone hat das Zeitlimit "
            f"von {clone_timeout} Sekunden überschritten.\n"
            f"URL: {repo_url}\n"
            f"Ziel: {repo_dir}"
        ) from exc

    except OSError as exc:
        raise RuntimeError(
            f"{description}: Git-Clone konnte nicht gestartet werden.\n"
            f"{exc}"
        ) from exc

    if result.returncode != 0:
        details = (
            result.stderr
            or result.stdout
            or "Unbekannter Git-Fehler"
        ).strip()

        raise RuntimeError(
            f"{description}: Git-Clone fehlgeschlagen.\n\n"
            f"Repository: {repo_url}\n"
            f"Ziel: {repo_dir}\n\n"
            f"{details}"
        )

    # --------------------------------------------------------
    # Ergebnis prüfen
    # --------------------------------------------------------

    if not is_valid_git_repo(repo_dir):
        raise RuntimeError(
            f"{description}: Nach dem Klonen wurde kein gültiges "
            f"Git-Repository gefunden:\n{repo_dir}"
        )

    log(
        f"[GIT] {description} erfolgreich geklont: {repo_dir}"
    )

    return repo_dir



def load_github_config(*args, **kwargs):
    """Lazy bridge to patch_format to avoid an import cycle."""
    from patch_format import load_github_config as _impl
    return _impl(*args, **kwargs)

from paths import TEMP_REPO

def clean_patch_folder(
    gui_instance=None,
    info_widget=None,
    progress_callback=None
):
    """
    Löscht die Dateien und temporären Ordner der Patch-Erstellung.

    GELÖSCHT:
        - oscam-emu.patch
        - oscam-emu.patch.alt
        - oscam-emu.patch.zip
        - oscam-emu.zip
        - oscam-emu-git/
        - TEMP_REPO/
        - TEMP_PATCH_GIT/ (falls vorhanden)

    NICHT GELÖSCHT:
        - streamboard-git/ (geschütztes Repository)
    """

    import os
    import shutil
    import stat

    from PyQt6.QtWidgets import QTextEdit, QApplication

    # ---------------------------------------------------------
    # GUI
    # ---------------------------------------------------------

    if gui_instance and hasattr(gui_instance, "hide_final_label"):
        try:
            gui_instance.hide_final_label()
        except Exception:
            pass

    widget = (
        info_widget
        or getattr(gui_instance, "info_text", None)
    )

    lang = str(
        getattr(gui_instance, "LANG", "de")
    ).lower()

    is_de = lang.startswith("de")

    pbar = getattr(
        gui_instance,
        "progress_bar",
        None
    )

    # ---------------------------------------------------------
    # Fortschrittsanzeige
    # ---------------------------------------------------------

    def update_progress(value, text=None):
        if pbar:
            try:
                pbar.setValue(value)
                pbar.setFormat(text if text else "%p%")
                pbar.show()
            except Exception:
                pass

        if progress_callback:
            try:
                progress_callback(value)
            except Exception:
                pass

        try:
            QApplication.processEvents()
        except Exception:
            pass

    # ---------------------------------------------------------
    # Logging
    # ---------------------------------------------------------

    def log(text, level="info"):
        if (
            gui_instance
            and hasattr(gui_instance, "append_info")
        ):
            try:
                gui_instance.append_info(
                    widget,
                    text,
                    level
                )
                QApplication.processEvents()
                return
            except Exception:
                pass

        if isinstance(widget, QTextEdit):
            color = {
                "success": "green",
                "warning": "orange",
                "error": "red"
            }.get(level, "gray")

            try:
                widget.append(
                    f'<span style="color:{color}">'
                    f'{text}'
                    f'</span>'
                )
                QApplication.processEvents()
            except Exception:
                pass

    # ---------------------------------------------------------
    # Schreibschutz entfernen
    # ---------------------------------------------------------

    def remove_readonly(func, path, exc_info):
        try:
            os.chmod(
                path,
                stat.S_IWRITE | stat.S_IREAD
            )
            func(path)
        except Exception:
            pass

    # ---------------------------------------------------------
    # START
    # ---------------------------------------------------------

    start_text = (
        "🧹 Patch-Ordner wird geleert..."
        if is_de
        else "🧹 Cleaning patch folder..."
    )

    update_progress(5, start_text)
    log(start_text, "info")
    # Startsound
    try:
        sound_manager = getattr(
            gui_instance,
            "sound_manager",
            None
        )

        if sound_manager and sound_manager.enabled:
            sound_manager.play_start()
    except Exception as exc:
        print(f"[WARN] clean_patch Startsound fehlgeschlagen: {exc}")
    # ---------------------------------------------------------
    # ZU LÖSCHENDE DATEIEN UND ORDNER
    # ---------------------------------------------------------

    targets = []

    # ---------------------------------------------------------
    # oscam-emu.patch
    # ---------------------------------------------------------

    patch_path = globals().get("PATCH_FILE")

    if not patch_path:
        patch_path = os.path.join(
            PLUGIN_DIR,
            "oscam-emu.patch"
        )

    patch_path = os.path.abspath(patch_path)

    targets.append((patch_path, "file"))

    # ---------------------------------------------------------
    # oscam-emu.patch.alt
    # ---------------------------------------------------------

    patch_alt_path = os.path.join(
        os.path.dirname(patch_path),
        "oscam-emu.patch.alt"
    )

    targets.append((patch_alt_path, "file"))

    # ---------------------------------------------------------
    # oscam-emu.patch.zip
    # ---------------------------------------------------------

    patch_zip_path = os.path.join(
        os.path.dirname(patch_path),
        "oscam-emu.patch.zip"
    )

    targets.append((patch_zip_path, "file"))

    # ---------------------------------------------------------
    # oscam-emu.zip
    # ---------------------------------------------------------

    zip_path = globals().get("ZIP_FILE")

    if not zip_path:
        zip_path = os.path.join(
            PLUGIN_DIR,
            "oscam-emu.zip"
        )

    targets.append((zip_path, "file"))

    # ---------------------------------------------------------
    # oscam-emu-git/
    # ---------------------------------------------------------

    emu_git_path = globals().get("PATCH_EMU_GIT_DIR")

    if not emu_git_path:
        emu_git_path = os.path.join(
            PLUGIN_DIR,
            "oscam-emu-git"
        )

    targets.append((emu_git_path, "folder"))

    # ---------------------------------------------------------
    # TEMP_REPO/
    # ---------------------------------------------------------

    temp_repo = globals().get("TEMP_REPO")

    
    if temp_repo:
        temp_repo_path = os.path.abspath(temp_repo)

        targets.append((temp_repo_path, "folder"))

        log(
            f"[CLEAN] TEMP_REPO: {temp_repo_path}",
            "info"
        )
    else:
        log(
            "⚠️ TEMP_REPO ist nicht definiert und wird übersprungen.",
            "warning"
        )

    # ---------------------------------------------------------
    # TEMP_PATCH_GIT/ (falls vorhanden)
    # ---------------------------------------------------------

    temp_patch_git_path = globals().get("TEMP_PATCH_GIT")

    if temp_patch_git_path:
        targets.append((temp_patch_git_path, "folder"))

    # ---------------------------------------------------------
    # Doppelte Pfade entfernen
    # ---------------------------------------------------------

    unique_targets = []
    seen = set()

    for path, target_type in targets:
        if not path:
            continue

        path = os.path.abspath(path)

        normalized_path = os.path.normcase(
            os.path.normpath(path)
        )

        if normalized_path in seen:
            continue

        seen.add(normalized_path)
        unique_targets.append((path, target_type))

    targets = unique_targets

    # ---------------------------------------------------------
    # LÖSCHEN
    # ---------------------------------------------------------

    deleted_count = 0
    failed_count = 0
    total = len(targets)

    for index, (path, target_type) in enumerate(
        targets,
        start=1
    ):
        name = os.path.basename(path)

        try:
            if not os.path.lexists(path):
                log(
                    f"ℹ️ Nicht vorhanden: {name}",
                    "info"
                )
            else:
                log(
                    f"🗑️ Lösche: {name}",
                    "info"
                )

                if target_type == "folder":
                    if os.path.islink(path):
                        os.remove(path)
                    elif os.path.isdir(path):
                        shutil.rmtree(
                            path,
                            onerror=remove_readonly
                        )
                    else:
                        raise RuntimeError(
                            "Der Pfad ist kein Ordner."
                        )

                else:
                    if (
                        os.path.isdir(path)
                        and not os.path.islink(path)
                    ):
                        raise RuntimeError(
                            "Dateipfad verweist auf einen Ordner."
                        )

                    try:
                        os.chmod(
                            path,
                            stat.S_IWRITE | stat.S_IREAD
                        )
                    except Exception:
                        pass

                    os.remove(path)

                if os.path.lexists(path):
                    raise RuntimeError(
                        "Löschen fehlgeschlagen."
                    )

                deleted_count += 1

                log(
                    f"✓ Gelöscht: {name}",
                    "success"
                )

        except Exception as error:
            failed_count += 1

            log(
                f"❌ Konnte nicht gelöscht werden: "
                f"{name} — {error}",
                "error"
            )

        progress = 10 + int(
            85 * index / max(total, 1)
        )

        update_progress(progress)


    # ---------------------------------------------------------
    # ABSCHLUSS
    # ---------------------------------------------------------

    if failed_count == 0:
        final_text = (
            "✅ Patch-Ordner vollständig geleert "
            f"({deleted_count} entfernt)"
            if is_de
            else
            "✅ Patch folder completely cleaned "
            f"({deleted_count} removed)"
        )

        log(final_text, "success")
        update_progress(100, final_text)

        try:
            sound_manager = getattr(
                gui_instance,
                "sound_manager",
                None
            )

            if sound_manager and sound_manager.enabled:
                sound_manager.play_success()
            else:
                play_cross_platform_sound("success")

        except Exception as exc:
            print(
                f"[WARN] clean_patch Erfolgssound fehlgeschlagen: {exc}"
            )

        return True

    final_text = (
        f"⚠️ Bereinigung teilweise fehlgeschlagen "
        f"({failed_count} Fehler, "
        f"{deleted_count} entfernt)"
        if is_de
        else
        f"⚠️ Cleanup partially failed "
        f"({failed_count} errors, "
        f"{deleted_count} removed)"
    )

    log(final_text, "warning")
    update_progress(100, final_text)

    try:
        sound_manager = getattr(
            gui_instance,
            "sound_manager",
            None
        )

        if sound_manager and sound_manager.enabled:
            error_sound = getattr(
                sound_manager,
                "error_sound",
                None
            )

            if error_sound:
                error_sound.play()
            else:
                play_cross_platform_sound("error")
        else:
            play_cross_platform_sound("error")

    except Exception as exc:
        print(
            f"[WARN] clean_patch Warnsound fehlgeschlagen: {exc}"
        )

    return False

def get_streamboard_version():
    """
    Liest die OSCam-Version direkt aus globals.h
    des aktuellen Streamboard-Repositories.

    Erwartet z.B.:

        #define CS_VERSION "2.26.09-11968"

    Rückgabe:

        2.26.09-11968

    Falls die Version nicht gefunden werden kann:

        unbekannt
    """

    import os
    import re

    globals_file = os.path.join(STREAMREPO, "globals.h")

    if not os.path.isfile(globals_file):
        return "unbekannt"

    try:
        with open(
            globals_file,
            "r",
            encoding="utf-8",
            errors="replace"
        ) as handle:
            content = handle.read()

        match = re.search(
            r'#define\s+CS_VERSION\s+"([^"]+)"',
            content
        )

        if not match:
            return "unbekannt"

        version = match.group(1).strip()

        if not version:
            return "unbekannt"

        return version

    except Exception:
        return "unbekannt"


# ============================================================
# GIT-REPOSITORY HILFSFUNKTIONEN
# ============================================================



# ===================== PATCH FUNCTIONS =====================
def create_patch(
    gui_instance=None,
    info_widget=None,
    progress_callback=None
):
    import os
    import re
    import shutil
    import subprocess
    import tempfile

    
    
    # ============================================================
    # Sound – Windows + Linux
    # Start, Erfolg und Fehler
    # ============================================================

    import sys
    import os
    import subprocess
    import shutil

    def play_patch_sound(sound_type="start"):
        """Delegiert an die zentrale plattformübergreifende Soundfunktion."""
        return play_cross_platform_sound(sound_type)

    # ---------------------------------------------------------
    # STARTSOUND
    # ---------------------------------------------------------
    play_cross_platform_sound("start")



    try:
        from PyQt6.QtWidgets import QApplication
    except Exception:
        QApplication = None

    # ============================================================
    # Sprache
    # ============================================================

    current_lang = getattr(
        gui_instance,
        "LANG",
        "de"
    )

    current_lang = str(
        current_lang
    ).lower()[:2]

    is_de = current_lang == "de"

    # ============================================================
    # Übersetzungen
    # ============================================================

    def tr(key, fallback_de, fallback_en):
        try:
            texts = globals().get(
                "TEXTS",
                {}
            )

            if isinstance(texts, dict):
                value = texts.get(key)

                if isinstance(value, dict):
                    value = value.get(
                        current_lang
                    )

                    if value:
                        return str(value)

                elif value:
                    return str(value)

        except Exception:
            pass

        return (
            fallback_de
            if is_de
            else fallback_en
        )

    # ============================================================
    # Progress-Bar ermitteln
    # ============================================================

    def get_progress_bar():

        if gui_instance is None:
            return None

        for name in (
            "progress_bar",
            "progressbar",
            "progressBar"
        ):

            try:
                widget = getattr(
                    gui_instance,
                    name,
                    None
                )

                if widget is not None:
                    return widget

            except Exception:
                pass

        return None

    progress_bar = get_progress_bar()

    # ============================================================
    # GUI Events
    # ============================================================

    def process_events():

        try:
            if QApplication is not None:
                QApplication.processEvents()

        except Exception:
            pass

    # ============================================================
    # Progress
    # ============================================================

    def set_progress(
        value=None,
        text=None
    ):

        try:

            if value is not None:

                value = max(
                    0,
                    min(
                        100,
                        int(value)
                    )
                )

                if progress_bar is not None:
                    progress_bar.setValue(
                        value
                    )

            if text is not None:

                if progress_bar is not None:
                    progress_bar.setFormat(
                        str(text)
                    )

            if progress_bar is not None:
                progress_bar.show()

        except Exception:
            pass

        try:

            if progress_callback is not None:

                if value is not None:
                    progress_callback(
                        value
                    )

                elif text is not None:

                    try:
                        progress_callback(
                            None,
                            text
                        )

                    except TypeError:
                        pass

        except Exception:
            pass

        process_events()

    # ============================================================
    # Logging
    # ============================================================

    def log(message):

        try:
            print(
                str(message)
            )

        except Exception:
            pass

        try:

            if gui_instance is not None:

                append_info = getattr(
                    gui_instance,
                    "append_info",
                    None
                )

                if callable(
                    append_info
                ):

                    append_info(
                        getattr(
                            gui_instance,
                            "info_text",
                            info_widget
                        ),
                        str(message),
                        "info"
                    )

        except Exception:
            pass

    # ============================================================
    # Git Wrapper
    # ============================================================

    def run_git(
        args,
        cwd,
        check=True,
        timeout=1200
    ):

        git_executable = find_git_executable()

        if not git_executable:
            raise RuntimeError(
                tr(
                    "git_missing",
                    "Git wurde nicht gefunden.",
                    "Git was not found."
                )
            )

        command = [
            git_executable
        ] + list(args)

        result = subprocess.run(
            command,
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout
        )

        stdout = (
            result.stdout or ""
        ).strip()

        stderr = (
            result.stderr or ""
        ).strip()

        if check and result.returncode != 0:

            detail = (
                stderr
                or stdout
                or (
                    "Git-Befehl fehlgeschlagen"
                    if is_de
                    else
                    "Git command failed"
                )
            )

            raise RuntimeError(
                "git "
                + " ".join(
                    str(x)
                    for x in args
                )
                + "\n"
                + detail
            )

        return stdout

    # ============================================================
    # Repository validieren
    # ============================================================

    def validate_git_repository(
        repo_dir,
        description
    ):

        if not os.path.isdir(
            repo_dir
        ):

            raise RuntimeError(
                f"{description}: "
                "Repository-Verzeichnis fehlt."
            )

        result = run_git(
            [
                "rev-parse",
                "--is-inside-work-tree"
            ],
            cwd=repo_dir,
            check=False,
            timeout=60
        )

        if result.strip().lower() != "true":

            raise RuntimeError(
                f"{description}: "
                "kein gültiges Git-Repository."
            )

    # ============================================================
    # Lokalen HEAD ermitteln
    # ============================================================

    def get_head(repo_dir):

        return run_git(
            [
                "rev-parse",
                "HEAD"
            ],
            cwd=repo_dir,
            check=True,
            timeout=60
        ).strip()

    # ============================================================
    # Remote-Branch ermitteln
    # ============================================================

    def get_remote_branch(repo_dir):

        branch = run_git(
            [
                "symbolic-ref",
                "--short",
                "HEAD"
            ],
            cwd=repo_dir,
            check=False,
            timeout=60
        ).strip()

        if branch:
            return (
                "origin/"
                + branch
            )

        return ""

    # ============================================================
    # Ausgewählten OSCam-Emu-Branch aktualisieren
    # ============================================================

    def update_emu_repository(repo_dir, branch):
        branch = str(branch or "master").strip() or "master"
        run_git(["fetch", "origin", branch], cwd=repo_dir, check=True, timeout=1200)
        remote_ref = f"origin/{branch}"
        if not run_git(["rev-parse", "--verify", remote_ref], cwd=repo_dir, check=False, timeout=60).strip():
            raise RuntimeError(f"OSCam-Emu Branch konnte nicht gefunden werden: {branch}")
        local_branch = run_git(["symbolic-ref", "--short", "HEAD"], cwd=repo_dir, check=False, timeout=60).strip()
        if local_branch == branch:
            run_git(["reset", "--hard", remote_ref], cwd=repo_dir, check=True, timeout=1200)
        else:
            exists = run_git(["show-ref", "--verify", f"refs/heads/{branch}"], cwd=repo_dir, check=False, timeout=60).strip()
            if exists:
                run_git(["checkout", branch], cwd=repo_dir, check=True, timeout=1200)
                run_git(["reset", "--hard", remote_ref], cwd=repo_dir, check=True, timeout=1200)
            else:
                run_git(["checkout", "-B", branch, remote_ref], cwd=repo_dir, check=True, timeout=1200)
        return get_head(repo_dir)

    # ============================================================
    # Repository aktualisieren
    # ============================================================

    def update_repository(
        repo_dir,
        description
    ):

        log(
            f"[GIT] Aktualisiere {description}..."
        )

        run_git(
            [
                "fetch",
                "--all",
                "--prune"
            ],
            cwd=repo_dir,
            check=True,
            timeout=1200
        )

        remote_branch = (
            get_remote_branch(
                repo_dir
            )
        )

        if remote_branch:

            remote_exists = run_git(
                [
                    "rev-parse",
                    "--verify",
                    remote_branch
                ],
                cwd=repo_dir,
                check=False,
                timeout=60
            )

            if remote_exists:

                log(
                    f"[GIT] {description}: "
                    f"aktualisiere auf "
                    f"{remote_branch}"
                )

                run_git(
                    [
                        "reset",
                        "--hard",
                        remote_branch
                    ],
                    cwd=repo_dir,
                    check=True,
                    timeout=1200
                )

                return

        log(
            f"[GIT] {description}: "
            "kein Remote-Branch gefunden – "
            "verwende vorhandenen HEAD."
        )

    # ============================================================
    # EXAKTEN STREAMBOARD-MASTER-HEAD ermitteln
    #
    # WICHTIG:
    # Die Patch-Basis kommt direkt von refs/heads/master
    # des Streamboard-Servers und NICHT vom lokalen HEAD.
    # ============================================================

    def get_remote_head(
        repo_url,
        description
    ):
        """Ermittelt den exakten refs/heads/master-Commit direkt vom Server."""
        log(f"[GIT] Ermittle exakten {description} master-HEAD vom Server...")

        git_executable = find_git_executable()
        if not git_executable:
            raise RuntimeError(tr("git_missing", "Git wurde nicht gefunden.", "Git was not found."))

        result = subprocess.run(
            [git_executable, "ls-remote", "--exit-code", str(repo_url).strip(), "refs/heads/master"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=1200
        )
        stdout=(result.stdout or "").strip()
        stderr=(result.stderr or "").strip()
        if result.returncode != 0:
            raise RuntimeError(stderr or stdout or f"Remote-Master von {description} konnte nicht ermittelt werden.")

        remote_head=""
        for line in stdout.splitlines():
            parts=line.strip().split()
            if len(parts)>=2 and parts[1]=="refs/heads/master":
                remote_head=parts[0]
                break
        if not remote_head:
            raise RuntimeError(f"{description}: git ls-remote lieferte keinen refs/heads/master-Commit.")
        if len(remote_head) not in (40,64) or not re.fullmatch(r"[0-9a-fA-F]+", remote_head):
            raise RuntimeError(f"{description}: ungültige Remote-Master-ID: {remote_head}")
        remote_head=remote_head.lower()
        log(f"[GIT] {description} refs/heads/master:")
        log(f"      {remote_head}")
        return remote_head

    def get_emu_repository_config():
        """Liest die im GitHub-Konfigurationsdialog gewählte Emu-URL und den Branch."""
        repo_url = ""
        branch = "master"

        try:
            cfg = load_github_config()
            repo_url = str(cfg.get("emu_repo_url", "") or "").strip()
            branch = str(cfg.get("emu_branch", "master") or "master").strip() or "master"
        except Exception:
            pass

        # Eine im Tool gerade ausgewählte Repo-URL hat IMMER Vorrang
        # vor einer älteren gespeicherten Konfiguration.
        if gui_instance is not None:
            try:
                value = str(getattr(gui_instance, "selected_emu_repo_url", "") or "").strip()
                if value:
                    repo_url = value
            except Exception:
                pass
            try:
                value = str(getattr(gui_instance, "EMUREPO", "") or "").strip()
                if value:
                    repo_url = value
            except Exception:
                pass
            try:
                value = str(getattr(gui_instance, "selected_emu_repo_branch", "") or "").strip()
                if value:
                    branch = value
            except Exception:
                pass

        selected_global = str(globals().get("SELECTED_EMU_REPO_URL", "") or "").strip()
        if selected_global:
            repo_url = selected_global

        if not repo_url:
            repo_url = str(globals().get("EMUREPO_URL", "") or "").strip()

        if not repo_url:
            raise RuntimeError(
                tr(
                    "emu_repo_url_missing",
                    "Keine OSCam-Emu Repository-URL gesetzt. Bitte im GitHub-Konfigurationsdialog eintragen.",
                    "No OSCam-Emu repository URL configured. Set it in the GitHub configuration dialog."
                )
            )

        return repo_url, branch

    def get_emu_repository_url():
        return get_emu_repository_config()[0]

    # ============================================================
    # Remote URL synchronisieren
    # ============================================================

    def sync_emu_origin(
        repo_dir,
        repo_url
    ):

        current_origin = run_git(
            [
                "remote",
                "get-url",
                "origin"
            ],
            cwd=repo_dir,
            check=False,
            timeout=60
        ).strip()

        # --------------------------------------------------------
        # Noch kein origin
        # --------------------------------------------------------

        if not current_origin:

            log(
                "[GIT] OSCam-Emu: "
                "origin fehlt – füge Remote hinzu."
            )

            run_git(
                [
                    "remote",
                    "add",
                    "origin",
                    repo_url
                ],
                cwd=repo_dir,
                check=True,
                timeout=60
            )

            return

        # --------------------------------------------------------
        # URL bereits korrekt
        # --------------------------------------------------------

        if current_origin == repo_url:

            log(
                "[GIT] OSCam-Emu origin bereits korrekt:"
            )

            log(
                f"       {current_origin}"
            )

            return

        # --------------------------------------------------------
        # URL ändern
        # --------------------------------------------------------

        log(
            "[GIT] Ändere OSCam-Emu origin:"
        )

        log(
            f"       alt: {current_origin}"
        )

        log(
            f"       neu: {repo_url}"
        )

        run_git(
            [
                "remote",
                "set-url",
                "origin",
                repo_url
            ],
            cwd=repo_dir,
            check=True,
            timeout=60
        )

    # ============================================================
    # Exakten Streamboard-Commit in EMU-Repository verfügbar machen
    #
    # Kein Merge!
    # Nur das Commit-Objekt wird geholt.
    # ============================================================

    def import_streamboard_commit(
        emu_dir,
        stream_commit
    ):

        remote_name = (
            "streamboard-current"
        )

        existing_url = run_git(
            [
                "remote",
                "get-url",
                remote_name
            ],
            cwd=emu_dir,
            check=False,
            timeout=60
        ).strip()

        stream_url = get_selected_stream_repo_url()

        stream_url = str(
            stream_url
        ).strip()

        # --------------------------------------------------------
        # Remote anlegen / URL korrigieren
        # --------------------------------------------------------

        if not existing_url:

            log(
                "[GIT] Füge Streamboard-Remote hinzu:"
            )

            log(
                f"       {stream_url}"
            )

            run_git(
                [
                    "remote",
                    "add",
                    remote_name,
                    stream_url
                ],
                cwd=emu_dir,
                check=True,
                timeout=60
            )

        elif existing_url != stream_url:

            log(
                "[GIT] Aktualisiere Streamboard-Remote:"
            )

            log(
                f"       {stream_url}"
            )

            run_git(
                [
                    "remote",
                    "set-url",
                    remote_name,
                    stream_url
                ],
                cwd=emu_dir,
                check=True,
                timeout=60
            )

        # --------------------------------------------------------
        # Exakten HEAD holen
        # --------------------------------------------------------

        log(
            "[GIT] Hole exakt den benötigten Streamboard Commit "
            "ins OSCam-Emu Repository..."
        )

        # WICHTIG: Nicht nur HEAD holen. Bei einer manuell
        # ausgewählten älteren Streamboard-Revision muss genau
        # diese SHA als Git-Objekt im EMU-Repository vorhanden sein.
        run_git(
            [
                "fetch",
                remote_name,
                stream_commit
            ],
            cwd=emu_dir,
            check=True,
            timeout=1200
        )

        # --------------------------------------------------------
        # Prüfen, ob der gewünschte Commit vorhanden ist
        # --------------------------------------------------------

        available = run_git(
            [
                "cat-file",
                "-e",
                stream_commit + "^{commit}"
            ],
            cwd=emu_dir,
            check=False,
            timeout=60
        )

        # cat-file liefert bei Erfolg normalerweise
        # keinen Text.
        #
        # Deshalb wird zusätzlich rev-parse verwendet.

        resolved = run_git(
            [
                "rev-parse",
                "--verify",
                stream_commit + "^{commit}"
            ],
            cwd=emu_dir,
            check=False,
            timeout=60
        ).strip()

        if not resolved:

            raise RuntimeError(
                "Streamboard Commit konnte im "
                "OSCam-Emu Repository nicht gefunden werden.\n"
                f"Erwartet: {stream_commit}"
            )

        if resolved != stream_commit:

            raise RuntimeError(
                "Streamboard Commit wurde nicht korrekt importiert:\n"
                f"Erwartet: {stream_commit}\n"
                f"Gefunden: {resolved}"
            )

    # ============================================================
    # OSCam-Emu Metadaten an die tatsächlich ausgewählte Repo-URL
    # anpassen. Dadurch zeigt globals.h im erzeugten Patch wirklich
    # auf das Repo, das im Tool über "Repo URL" ausgewählt wurde.
    # ============================================================

    def normalize_emu_metadata(repo_dir, repo_url):
        import re

        globals_file = os.path.join(repo_dir, "globals.h")
        if not os.path.isfile(globals_file):
            return False

        raw_url = str(repo_url or "").strip()
        if not raw_url:
            return False

        canonical = raw_url[:-4] if raw_url.lower().endswith(".git") else raw_url
        canonical = canonical.rstrip("/")

        # Bei GitHub ist die Discussions-URL eindeutig aus der gewählten
        # Repository-URL ableitbar. Für andere Hosts bleibt BOARD_URL
        # unverändert, sofern sie nicht bereits auf dieses Repo zeigt.
        board_url = None
        m = re.match(r"^(https?://github\.com/[^/]+/[^/]+)(?:/)?$", canonical, re.I)
        if m:
            board_url = m.group(1) + "/discussions"

        try:
            with open(globals_file, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
        except Exception:
            return False

        original = content
        content = re.sub(
            r'(#define\s+SCM_URL\s+)"[^"]*"',
            lambda m: m.group(1) + '"' + canonical + '"',
            content,
            count=1,
        )

        if board_url:
            content = re.sub(
                r'(#define\s+BOARD_URL\s+)"[^"]*"',
                lambda m: m.group(1) + '"' + board_url + '"',
                content,
                count=1,
            )

        if content != original:
            with open(globals_file, "w", encoding="utf-8", newline="") as f:
                f.write(content)
            log("[EMU] globals.h auf die ausgewählte Repo-URL synchronisiert:")
            log(f"      SCM_URL  = {canonical}")
            if board_url:
                log(f"      BOARD_URL = {board_url}")
            return True

        return False

    def prepare_validation_target(repo_dir, emu_commit, repo_url):
        temp_dir = tempfile.mkdtemp(prefix="oscam_patch_expected_")
        run_git(
            ["worktree", "add", "--detach", temp_dir, emu_commit],
            cwd=repo_dir, check=True, timeout=1200
        )
        try:
            normalize_emu_metadata(temp_dir, repo_url)
            return temp_dir
        except Exception:
            try:
                run_git(["worktree", "remove", "--force", temp_dir], cwd=repo_dir, check=False, timeout=1200)
            except Exception:
                pass
            shutil.rmtree(temp_dir, ignore_errors=True)
            raise

    # ============================================================
    # Patch validieren
    # ============================================================

    def validate_patch_with_git_apply(
        repo_dir,
        patch_file,
        base_commit=None
    ):
        """
        Prüft einen Patch gegen den EXAKTEN Basis-Commit.

        Niemals gegen den zufälligen aktuellen Working Tree prüfen:
        genau das hatte zuvor den globals.h-Fehler verursacht.
        """

        log(
            "[PATCH] Prüfe Patch mit git apply --check..."
        )

        validation_worktree = None

        try:
            if base_commit:
                validation_worktree = tempfile.mkdtemp(
                    prefix="oscam_patch_applycheck_"
                )

                # Das Zielverzeichnis wird von git worktree add angelegt.
                # Daher den von mkdtemp erzeugten leeren Ordner entfernen.
                shutil.rmtree(
                    validation_worktree,
                    ignore_errors=True
                )

                run_git(
                    [
                        "worktree",
                        "add",
                        "--detach",
                        validation_worktree,
                        base_commit
                    ],
                    cwd=repo_dir,
                    check=True,
                    timeout=1200
                )

                validation_cwd = validation_worktree
            else:
                validation_cwd = repo_dir

            run_git(
                [
                    "apply",
                    "--check",
                    "--binary",
                    patch_file
                ],
                cwd=validation_cwd,
                check=True,
                timeout=1200
            )

            if base_commit:
                log(
                    "[PATCH] git apply --check: OK "
                    f"(Basis {base_commit})"
                )
            else:
                log(
                    "[PATCH] git apply --check: OK"
                )

        finally:
            if validation_worktree:
                try:
                    run_git(
                        [
                            "worktree",
                            "remove",
                            "--force",
                            validation_worktree
                        ],
                        cwd=repo_dir,
                        check=False,
                        timeout=1200
                    )
                except Exception:
                    pass

                shutil.rmtree(
                    validation_worktree,
                    ignore_errors=True
                )

                try:
                    run_git(
                        [
                            "worktree",
                            "prune"
                        ],
                        cwd=repo_dir,
                        check=False,
                        timeout=1200
                    )
                except Exception:
                    pass


    # ============================================================
    # Patch gegen Zielzustand validieren
    #
    # Streamboard Worktree wird exakt auf den
    # REMOTE-HEAD-Commit gesetzt.
    # ============================================================

    def validate_applied_patch(
        stream_repo_dir,
        stream_commit,
        emu_repo_dir,
        emu_commit,
        patch_file,
        emu_repo_url=None
    ):

        temp_worktree = None

        try:

            temp_worktree = tempfile.mkdtemp(
                prefix="oscam_patch_validate_"
            )

            log(
                "[PATCH] Erstelle temporären "
                "Validierungs-Worktree..."
            )

            run_git(
                [
                    "worktree",
                    "add",
                    "--detach",
                    temp_worktree,
                    stream_commit
                ],
                cwd=emu_repo_dir,
                check=True,
                timeout=1200
            )

            log(
                "[PATCH] Wende Patch im "
                "temporären Worktree an..."
            )

            run_git(
                [
                    "apply",
                    "--binary",
                    patch_file
                ],
                cwd=temp_worktree,
                check=True,
                timeout=1200
            )

            # ----------------------------------------------------
            # Alle Änderungen zum Index hinzufügen
            # ----------------------------------------------------

            run_git(
                [
                    "add",
                    "-A"
                ],
                cwd=temp_worktree,
                check=True,
                timeout=1200
            )

            # ----------------------------------------------------
            # Erwarteten Zielzustand aus dem ausgewählten OSCam-Emu-
            # Repository aufbauen. globals.h wird dabei genauso auf
            # die ausgewählte Repo-URL synchronisiert wie beim Patch.
            # ----------------------------------------------------

            expected_worktree = prepare_validation_target(
                emu_repo_dir,
                emu_commit,
                emu_repo_url
            )

            try:
                run_git(["add", "-A"], cwd=temp_worktree, check=True, timeout=1200)
                run_git(["add", "-A"], cwd=expected_worktree, check=True, timeout=1200)

                differences = run_git(
                    [
                        "diff", "--cached", "--name-status",
                        "--no-renames",
                        "--", ".",
                        ":(exclude).github/**",
                        ":(exclude).gitlab/**"
                    ],
                    cwd=temp_worktree, check=True, timeout=1200
                ).strip()

                expected_diff = run_git(
                    [
                        "diff", "--cached", "--name-status",
                        "--no-renames",
                        "--", ".",
                        ":(exclude).github/**",
                        ":(exclude).gitlab/**"
                    ],
                    cwd=expected_worktree, check=True, timeout=1200
                ).strip()

                # Robuster Vergleich über den vollständigen Tree.
                tree_a = run_git(["write-tree"], cwd=temp_worktree, check=True, timeout=1200).strip()
                tree_b = run_git(["write-tree"], cwd=expected_worktree, check=True, timeout=1200).strip()

                if tree_a != tree_b:
                    raise RuntimeError(
                        "Der angewendete Patch entspricht nicht dem "
                        "erwarteten OSCam-Emu-Zielzustand.\n\n"
                        f"Patch-Tree:    {tree_a}\n"
                        f"Erwarteter Tree: {tree_b}\n"
                        f"Patch-Status: {differences or '<keine>'}\n"
                        f"Soll-Status:  {expected_diff or '<keine>'}"
                    )
            finally:
                try:
                    run_git(["worktree", "remove", "--force", expected_worktree], cwd=emu_repo_dir, check=False, timeout=1200)
                except Exception:
                    pass
                shutil.rmtree(expected_worktree, ignore_errors=True)

            log(
                "[PATCH] Zielzustand nach Patch: OK"
            )

        finally:

            # ----------------------------------------------------
            # Worktree entfernen
            # ----------------------------------------------------

            if temp_worktree:

                try:

                    run_git(
                        [
                            "worktree",
                            "remove",
                            "--force",
                            temp_worktree
                        ],
                        cwd=emu_repo_dir,
                        check=False,
                        timeout=1200
                    )

                except Exception:
                    pass

                try:

                    if os.path.isdir(
                        temp_worktree
                    ):

                        shutil.rmtree(
                            temp_worktree,
                            ignore_errors=True
                        )

                except Exception:
                    pass

                try:

                    run_git(
                        [
                            "worktree",
                            "prune"
                        ],
                        cwd=emu_repo_dir,
                        check=False,
                        timeout=1200
                    )

                except Exception:
                    pass

    # ============================================================
    # Start
    # ============================================================

    try:

        set_progress(
            0,
            tr(
                "patch_start",
                "Starte Patch-Erstellung... %p%",
                "Starting patch creation... %p%"
            )
        )

        # --------------------------------------------------------
        # Git prüfen
        # --------------------------------------------------------

        git_executable = find_git_executable()

        if not git_executable:

            raise RuntimeError(
                tr(
                    "git_missing",
                    "Git wurde nicht gefunden.",
                    "Git was not found."
                )
            )

        log(
            "[GIT] Git:"
        )

        log(
            f"       {git_executable}"
        )

        set_progress(
            5,
            tr(
                "git_detected",
                "Git erkannt – %p%",
                "Git detected – %p%"
            )
        )

        
        # ========================================================
        # Repository-Pfade
        # ========================================================

        stream_dir = os.path.abspath(
            STREAMREPO
        )

        emu_dir = os.path.abspath(
            PATCH_EMU_GIT_DIR
        )

        patch_file = os.path.abspath(
            PATCH_FILE
        )

        if not os.path.isdir(stream_dir):
            log(
                f"[GIT] Streamboard-Verzeichnis: {stream_dir}"
            )

        log(
            f"[GIT] OSCam-Emu-Verzeichnis: {emu_dir}"
        )

        log(
            f"[PATCH] Patch-Datei: {patch_file}"
        )


        if not patch_file:

            raise RuntimeError(
                "PATCH_FILE ist nicht definiert."
            )

        patch_file = os.path.abspath(
            patch_file
        )

        # ========================================================
        # EMU URL aus GUI holen
        # ========================================================

        emu_repo_url, emu_repo_branch = get_emu_repository_config()

        # Die im GUI gewählte OSCam-Emu-Repo ist immer die verbindliche
        # Quelle für Create Patch. Eine ältere gespeicherte Config darf
        # die aktuelle Auswahl niemals überschreiben.
        if gui_instance is not None:
            try:
                gui_repo_url = str(
                    getattr(gui_instance, "selected_emu_repo_url", "") or
                    getattr(gui_instance, "EMUREPO", "") or ""
                ).strip()
                if gui_repo_url:
                    emu_repo_url = gui_repo_url

                gui_branch = str(
                    getattr(gui_instance, "selected_emu_repo_branch", "") or ""
                ).strip()
                if gui_branch:
                    emu_repo_branch = gui_branch

                gui_instance.selected_emu_repo_url = emu_repo_url
                gui_instance.selected_emu_repo_branch = emu_repo_branch
            except Exception:
                pass

        log("[GIT] Ausgewählte OSCam-Emu Repository-URL:")
        log(f"       {emu_repo_url}")
        log(f"[GIT] Ausgewählter OSCam-Emu Branch: {emu_repo_branch}")

        # ========================================================
        # Streamboard Repository URL
        # ========================================================

        stream_url = get_selected_stream_repo_url()

        stream_url = str(
            stream_url
        ).strip()

        
        # ========================================================
        # Streamboard Repository
        # ========================================================

        log("[GIT] Streamboard Repository:")
        log(f"       {stream_dir}")

        # Repository klonen, wenn es fehlt oder der Ordner leer ist.
        ensure_git_repository(
            stream_dir,
            stream_url,
            description="Streamboard",
            clone_timeout=1200
        )

        validate_git_repository(
            stream_dir,
            "Streamboard"
        )

        set_progress(
            10,
            tr(
                "stream_prepared",
                "Streamboard vorbereitet – %p%",
                "Streamboard prepared – %p%"
            )
        )


        # ========================================================
        # Streamboard-Basis bestimmen
        #
        # Wenn der Benutzer über "Streamboard Revision" einen
        # Commit ausgewählt hat, wird EXAKT dieser Commit verwendet.
        # Andernfalls wird weiterhin der echte Remote-HEAD verwendet.
        #
        # WICHTIG:
        # Bei einer manuellen Auswahl darf update_repository() NICHT
        # aufgerufen werden, weil es die Auswahl wieder überschreiben
        # würde.
        # ========================================================

        selected_stream_commit = getattr(
            gui_instance,
            "selected_streamboard_commit",
            None
        ) if gui_instance is not None else None

        if not selected_stream_commit:
            selected_stream_commit = globals().get(
                "SELECTED_STREAMBOARD_COMMIT"
            )

        selected_stream_commit = str(
            selected_stream_commit or ""
        ).strip().lower()

        if selected_stream_commit:

            if not re.fullmatch(
                r"[0-9a-f]{40}",
                selected_stream_commit
            ):
                raise RuntimeError(
                    "Die ausgewählte Streamboard-Revision ist keine "
                    "gültige 40-stellige Git-SHA."
                )

            stream_commit = selected_stream_commit

            log(
                "[STREAMBOARD] Manuell ausgewählte Basis-Revision:"
            )

            log(
                f"             {stream_commit}"
            )

            # Exakten Commit lokal sicherstellen.
            local_stream_commit = run_git(
                [
                    "rev-parse",
                    "--verify",
                    stream_commit + "^{commit}"
                ],
                cwd=stream_dir,
                check=False,
                timeout=60
            ).strip()

            if local_stream_commit != stream_commit:

                log(
                    "[GIT] Ausgewählte Revision ist lokal noch nicht "
                    "vorhanden – hole sie direkt von Streamboard."
                )

                run_git(
                    [
                        "fetch",
                        stream_url,
                        stream_commit
                    ],
                    cwd=stream_dir,
                    check=True,
                    timeout=1200
                )

                local_stream_commit = run_git(
                    [
                        "rev-parse",
                        "--verify",
                        stream_commit + "^{commit}"
                    ],
                    cwd=stream_dir,
                    check=False,
                    timeout=60
                ).strip()

            if local_stream_commit != stream_commit:
                raise RuntimeError(
                    "Die ausgewählte Streamboard-Revision konnte "
                    "nicht lokal bereitgestellt werden.\n"
                    f"Auswahl: {stream_commit}\n"
                    f"Lokal:   {local_stream_commit or '<nicht gefunden>'}"
                )

            # Sicherstellen, dass der ausgewählte Zustand weiterhin
            # ausgecheckt ist. Es wird KEIN reset --hard und KEIN clean
            # ausgeführt.
            checked_out = run_git(
                [
                    "rev-parse",
                    "HEAD"
                ],
                cwd=stream_dir,
                check=False,
                timeout=60
            ).strip()

            if checked_out != stream_commit:
                run_git(
                    [
                        "checkout",
                        "--detach",
                        stream_commit
                    ],
                    cwd=stream_dir,
                    check=True,
                    timeout=1200
                )

            log(
                "[STREAMBOARD] Verwende ausgewählten Commit als "
                "Patch-Basis – kein Remote-HEAD-Fallback."
            )

        else:

            # Keine manuelle Auswahl: den echten Streamboard Remote-HEAD
            # direkt vom Server ermitteln.
            update_repository(
                stream_dir,
                "Streamboard"
            )

            stream_commit = get_remote_head(
                stream_url,
                "Streamboard"
            )

            log(
                "[STREAMBOARD] Exakter Remote-HEAD:"
            )

            log(
                f"             {stream_commit}"
            )

            local_stream_commit = run_git(
                [
                    "rev-parse",
                    "--verify",
                    stream_commit + "^{commit}"
                ],
                cwd=stream_dir,
                check=False,
                timeout=60
            ).strip()

            if local_stream_commit != stream_commit:

                log(
                    "[GIT] Exakter Streamboard Remote-HEAD "
                    "ist lokal noch nicht vorhanden."
                )

                run_git(
                    [
                        "fetch",
                        stream_url,
                        stream_commit
                    ],
                    cwd=stream_dir,
                    check=True,
                    timeout=1200
                )

                local_stream_commit = run_git(
                    [
                        "rev-parse",
                        "--verify",
                        stream_commit + "^{commit}"
                    ],
                    cwd=stream_dir,
                    check=False,
                    timeout=60
                ).strip()

            if local_stream_commit != stream_commit:

                raise RuntimeError(
                    "Der exakte Streamboard Remote-HEAD "
                    "konnte nicht lokal bereitgestellt werden.\n"
                    f"Remote-HEAD: {stream_commit}\n"
                    f"Lokal:       {local_stream_commit or '<nicht gefunden>'}"
                )

        set_progress(
            25,
            tr(
                "stream_updated",
                "Streamboard-Basis bereit – %p%",
                "Streamboard base ready – %p%"
            )
        )

        # ========================================================
        # OSCam-Emu Repository
        # ========================================================

        log(
            "[GIT] OSCam-Emu Repository:"
        )

        log(
            f"       {emu_dir}"
        )

        # --------------------------------------------------------
        # Repository erzeugen, falls nicht vorhanden
        # --------------------------------------------------------

        if not os.path.isdir(
            emu_dir
        ):

            log(
                "[GIT] OSCam-Emu Repository fehlt – klone..."
            )

            ensure_git_repository(
                emu_dir,
                emu_repo_url,
                description="OSCam-Emu",
                clone_timeout=1200
            )

        validate_git_repository(
            emu_dir,
            "OSCam-Emu"
        )

        # --------------------------------------------------------
        # URL aus GUI übernehmen
        # --------------------------------------------------------

        sync_emu_origin(
            emu_dir,
            emu_repo_url
        )

        set_progress(
            30,
            tr(
                "emu_repo_prepared",
                "OSCam-Emu Repository vorbereitet – %p%",
                "OSCam-Emu repository prepared – %p%"
            )
        )

        # ========================================================
        # OSCam-Emu-Basis bestimmen
        #
        # Wenn über "OSCam-Emu Revision" ein Commit ausgewählt wurde,
        # wird EXAKT dieser Commit verwendet. Andernfalls bleibt das
        # bisherige Branch-Verhalten unverändert.
        # ========================================================

        selected_emu_commit = None

        if gui_instance is not None:
            selected_emu_commit = getattr(
                gui_instance,
                "selected_emu_commit",
                None
            )

        if not selected_emu_commit:
            selected_emu_commit = globals().get(
                "SELECTED_EMU_COMMIT"
            )

        selected_emu_commit = str(
            selected_emu_commit or ""
        ).strip().lower()

        # Eine gespeicherte Revision gehört immer zu genau der Repo-URL,
        # aus der sie ausgewählt wurde. Nach einem Wechsel über den
        # "Repo URL"-Button wird deshalb niemals eine alte SHA verwendet.
        selected_emu_commit_repo_url = ""
        if gui_instance is not None:
            selected_emu_commit_repo_url = str(
                getattr(gui_instance, "selected_emu_commit_repo_url", "") or ""
            ).strip()
        if not selected_emu_commit_repo_url:
            selected_emu_commit_repo_url = str(
                globals().get("SELECTED_EMU_COMMIT_REPO_URL", "") or ""
            ).strip()

        if selected_emu_commit and selected_emu_commit_repo_url:
            if selected_emu_commit_repo_url.rstrip("/") != emu_repo_url.rstrip("/"):
                log(
                    "[OSCAM-EMU] Die gespeicherte Revision gehört zu einer "
                    "anderen Repo-URL – Auswahl wird verworfen.\n"
                )
                selected_emu_commit = ""
                if gui_instance is not None:
                    gui_instance.selected_emu_commit = None
                    gui_instance.selected_emu_commit_repo_url = ""
                globals()["SELECTED_EMU_COMMIT"] = ""
                globals()["SELECTED_EMU_COMMIT_REPO_URL"] = ""

        if selected_emu_commit:
            if not re.fullmatch(
                r"[0-9a-f]{40}",
                selected_emu_commit
            ):
                raise RuntimeError(
                    "Die ausgewählte OSCam-Emu-Revision ist keine "
                    "gültige 40-stellige Git-SHA."
                )

            log("[OSCAM-EMU] Verwende manuell ausgewählte Basis-Revision:")
            log(f"             {selected_emu_commit}")

            local_emu_commit = run_git(
                [
                    "rev-parse",
                    "--verify",
                    selected_emu_commit + "^{commit}"
                ],
                cwd=emu_dir,
                check=False,
                timeout=60
            ).strip()

            if local_emu_commit != selected_emu_commit:
                run_git(
                    [
                        "fetch",
                        "origin",
                        selected_emu_commit
                    ],
                    cwd=emu_dir,
                    check=True,
                    timeout=1200
                )

                local_emu_commit = run_git(
                    [
                        "rev-parse",
                        "--verify",
                        selected_emu_commit + "^{commit}"
                    ],
                    cwd=emu_dir,
                    check=False,
                    timeout=60
                ).strip()

            if local_emu_commit != selected_emu_commit:
                raise RuntimeError(
                    "Die ausgewählte OSCam-Emu-Revision konnte "
                    "nicht lokal bereitgestellt werden."
                )

            checked_out_emu = run_git(
                [
                    "rev-parse",
                    "HEAD"
                ],
                cwd=emu_dir,
                check=False,
                timeout=60
            ).strip()

            if checked_out_emu != selected_emu_commit:
                run_git(
                    [
                        "checkout",
                        "--detach",
                        selected_emu_commit
                    ],
                    cwd=emu_dir,
                    check=True,
                    timeout=1200
                )

            emu_commit = selected_emu_commit

        else:
            # Keine manuelle OSCam-Emu-Auswahl:
            # immer den aktuellen Stand des über "Repo URL" gewählten
            # Repositories/Branches verwenden. Damit ist Create Patch
            # niemals von einer alten lokalen Revision abhängig.
            log(
                "[OSCAM-EMU] Keine feste Revision ausgewählt – "
                "verwende den aktuellen Stand des aktiven Repositories."
            )
            emu_commit = update_emu_repository(
                emu_dir,
                emu_repo_branch
            )

        log(
            "[OSCAM-EMU] Commit:"
        )

        log(
            f"             {emu_commit}"
        )

        # Die URL-Normalisierung erfolgt weiter unten ausschließlich in
        # einem temporären Ziel-Worktree. Das eigentliche OSCam-Emu-Repo
        # bleibt dadurch sauber und der Patch bekommt nur die beabsichtigte
        # Metadatenänderung.

        set_progress(
            40,
            tr(
                "emu_updated",
                "OSCam-Emu aktualisiert – %p%",
                "OSCam-Emu updated – %p%"
            )
        )

        # ========================================================
        # Alten Patch sichern
        # ========================================================

        backup_patch = (
            patch_file
            + ".alt"
        )

        if os.path.isfile(
            patch_file
        ):

            log(
                "[PATCH] Alter Patch gefunden."
            )

            log(
                "[PATCH] Sichere als:"
            )

            log(
                f"        {backup_patch}"
            )

            try:

                shutil.copy2(
                    patch_file,
                    backup_patch
                )

            except Exception as exc:

                raise RuntimeError(
                    "Alter Patch konnte nicht als "
                    f"{backup_patch} gesichert werden:\n"
                    + str(exc)
                )

            try:

                os.remove(
                    patch_file
                )

            except Exception as exc:

                raise RuntimeError(
                    "Alter Patch konnte nicht entfernt werden:\n"
                    + str(exc)
                )

        # ========================================================
        # Exakten Streamboard Commit im EMU-Repo verfügbar machen
        # ========================================================

        log(
            "[GIT] Importiere exakt den ermittelten "
            "Streamboard Remote-HEAD "
            "in das OSCam-Emu Repository..."
        )

        import_streamboard_commit(
            emu_dir,
            stream_commit
        )

        set_progress(
            50,
            tr(
                "stream_commit_imported",
                "Exakter Streamboard Commit verfügbar – %p%",
                "Exact Streamboard commit available – %p%"
            )
        )

        # ========================================================
        # Sicherheitsprüfung der Commit-Objekte
        # ========================================================

        stream_commit_verified = run_git(
            [
                "rev-parse",
                "--verify",
                stream_commit + "^{commit}"
            ],
            cwd=emu_dir,
            check=True,
            timeout=60
        ).strip()

        emu_commit_verified = run_git(
            [
                "rev-parse",
                "--verify",
                emu_commit + "^{commit}"
            ],
            cwd=emu_dir,
            check=True,
            timeout=60
        ).strip()

        if stream_commit_verified != stream_commit:

            raise RuntimeError(
                "Streamboard Commit konnte nicht "
                "exakt verifiziert werden.\n"
                f"Erwartet: {stream_commit}\n"
                f"Gefunden: {stream_commit_verified}"
            )

        if emu_commit_verified != emu_commit:

            raise RuntimeError(
                "OSCam-Emu Commit konnte nicht verifiziert werden."
            )

        # ========================================================
        # WICHTIGE ZUSATZPRÜFUNG
        #
        # Sicherstellen, dass der Commit im Streamboard-Repository
        # und im EMU-Repository identisch ist.
        # ========================================================

        stream_commit_in_stream_repo = run_git(
            [
                "rev-parse",
                "--verify",
                stream_commit + "^{commit}"
            ],
            cwd=stream_dir,
            check=True,
            timeout=60
        ).strip()

        if stream_commit_in_stream_repo != stream_commit:

            raise RuntimeError(
                "Streamboard Commit stimmt im lokalen "
                "Streamboard Repository nicht mit dem "
                "ermittelten Remote-HEAD überein.\n"
                f"Remote-HEAD: {stream_commit}\n"
                f"Lokal:       {stream_commit_in_stream_repo}"
            )

        # ========================================================
        # Patch erzeugen
        #
        # Basis:
        #     EXAKTER Streamboard Remote-HEAD
        #
        # Ziel:
        #     EXAKTER OSCam-Emu HEAD
        #
        # Kein Merge!
        # ========================================================

        log(
            "[PATCH] Erzeuge Roh-Patch:"
        )

        log(
            f"        Basis : {stream_commit}"
        )

        log(
            f"        Ziel   : {emu_commit}"
        )

        log(
            "        Modus  : git diff --binary --full-index"
        )

        target_worktree_for_patch = None

        try:
            # Exakten OSCam-Emu-Zielcommit auschecken und NUR dort die
            # beabsichtigten SCM_URL/BOARD_URL-Anpassungen durchführen.
            # Damit ist der Patch reproduzierbar: Basis = stream_commit,
            # Ziel = emu_commit plus genau diese Metadatenänderung.
            target_worktree_for_patch = prepare_validation_target(
                emu_dir,
                emu_commit,
                emu_repo_url
            )

            log(
                "[PATCH] Temporärer exakter Ziel-Worktree vorbereitet:"
            )
            log(
                f"        {target_worktree_for_patch}"
            )

            git_executable = find_git_executable()

            # Diff gegen den exakten Basis-Commit und den vorbereiteten
            # Ziel-Working-Tree. Die Metadatenänderung bleibt Bestandteil
            # des Patches, ohne den echten OSCam-Emu-Checkout zu verändern.
            result = subprocess.run(
                [
                    git_executable,
                    "diff",
                    "--binary",
                    "--full-index",
                    stream_commit,
                    "--"
                ],
                cwd=target_worktree_for_patch,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
                timeout=1200
            )

            patch_bytes = result.stdout or b""

            # Binär-Patches niemals über Text-Encoding schreiben.
            with open(patch_file, "wb") as binary_patch_output:
                binary_patch_output.write(patch_bytes)

            stderr = (
                result.stderr or b""
            ).decode("utf-8", errors="replace").strip()

        finally:
            if target_worktree_for_patch:
                try:
                    run_git(
                        [
                            "worktree",
                            "remove",
                            "--force",
                            target_worktree_for_patch
                        ],
                        cwd=emu_dir,
                        check=False,
                        timeout=1200
                    )
                except Exception:
                    pass

                shutil.rmtree(
                    target_worktree_for_patch,
                    ignore_errors=True
                )

                try:
                    run_git(
                        ["worktree", "prune"],
                        cwd=emu_dir,
                        check=False,
                        timeout=1200
                    )
                except Exception:
                    pass

        if result.returncode != 0:

            raise RuntimeError(
                "git diff konnte den Patch nicht erzeugen.\n"
                + (
                    stderr
                    if stderr
                    else
                    "Unbekannter Git-Fehler."
                )
            )

        if not os.path.isfile(
            patch_file
        ):

            raise RuntimeError(
                "Patch-Datei wurde nicht erzeugt."
            )

        raw_size = os.path.getsize(
            patch_file
        )

        if raw_size <= 0:

            raise RuntimeError(
                "Der erzeugte Patch ist leer."
            )

        log(
            "[PATCH] Roh-Patch erzeugt:"
        )

        log(
            f"        {raw_size:,} Bytes"
            .replace(",", ".")
        )

        # ========================================================
        # Roh-Patch validieren
        # ========================================================

        validate_patch_with_git_apply(
            stream_dir,
            patch_file,
            base_commit=stream_commit
        )

        set_progress(
            65,
            tr(
                "patch_created",
                "Patch erzeugt – %p%",
                "Patch created – %p%"
            )
        )

        
        
        # ========================================================
        # Header erzeugen – ohne Kommentarzeichen (#)
        # ========================================================

        header_function = globals().get("get_patch_header")

        if not callable(header_function):
            raise RuntimeError(
                "Die Funktion get_patch_header() ist nicht verfügbar."
            )

        patch_header = header_function(
            streamboard_repo=stream_dir,
            emu_repo=emu_dir,
            lang=(
                getattr(gui_instance, "LANG", "de")
                if gui_instance is not None
                else "de"
            ),
            modifier=PATCH_MODIFIER,
            streamboard_commit=stream_commit,
        )

        if not patch_header or not str(patch_header).strip():
            raise RuntimeError(
                "get_patch_header() hat einen leeren Header geliefert."
            )

        # Bereits vorhandene Header-Zeilen übernehmen.
        # Führende Kommentarzeichen werden entfernt.
        header_lines = []

        for line in str(patch_header).splitlines():
            line = line.strip()
            line = re.sub(r"^#+\s*", "", line)

            if line:
                header_lines.append(line)

        # Commit- und Repository-Informationen ergänzen.
        # Vorhandene Einträge werden nicht doppelt hinzugefügt.
        extra_header = [
            ("patch base commit:", stream_commit),
            ("patch target commit:", emu_commit),
            ("patch streamboard url:", stream_url),
            ("patch emu repository url:", emu_repo_url),
            ("patch emu branch:", emu_repo_branch),
        ]

        for key, value in extra_header:
            prefix = key.lower()
            header_lines = [
                line for line in header_lines
                if not line.lower().startswith(prefix)
            ]
            header_lines.append(f"{key} {value}")

        patch_header = "\n".join(header_lines)


        # ========================================================
        # Patch-Body lesen
        # ========================================================

        with open(
            patch_file,
            "r",
            encoding="utf-8",
            errors="replace",
            newline="",
        ) as patch_input:
            patch_body = patch_input.read()

        if not patch_body.strip():
            raise RuntimeError(
                "Der erzeugte Patch-Body ist leer."
            )

        # ========================================================
        # Finalen Patch schreiben
        # ========================================================

        final_patch = (
            patch_header
            + "\n\n"
            + patch_body.lstrip("\r\n")
        )

        with open(
            patch_file,
            "w",
            encoding="utf-8",
            newline="",
        ) as patch_output:
            patch_output.write(final_patch)

        log("[PATCH] Header eingefügt.")
        log("[PATCH] Patch-Header:")
        for line in header_lines:
            log(line)

        # ========================================================
        # Finalen Patch erneut validieren
        # ========================================================

        validate_patch_with_git_apply(
            stream_dir,
            patch_file,
            base_commit=stream_commit,
        )

        set_progress(
            80,
            tr(
                "patch_validated",
                "Patch validiert – %p%",
                "Patch validated – %p%",
            ),
        )

        # ========================================================
        # Vollständige Zustandsprüfung
        #
        # Streamboard Remote-HEAD + Patch = OSCam-Emu Zielzustand
        # ========================================================

        log(
            "[PATCH] Prüfe angewendeten Patch "
            "gegen OSCam-Emu Zielzustand..."
        )

        validate_applied_patch(
            stream_dir,
            stream_commit,
            emu_dir,
            emu_commit,
            patch_file,
            emu_repo_url,
        )

        set_progress(
            90,
            tr(
                "patch_final_validated",
                "Patch final validiert – %p%",
                "Patch finally validated – %p%",
            ),
        )

        # ========================================================
        # Endgröße
        # ========================================================

        final_size = os.path.getsize(patch_file)

        # ========================================================
        # Erfolg
        # ========================================================

        log("")
        log("========================================")
        log("PATCH ERFOLGREICH ERZEUGT")
        log("========================================")
        log(f"Streamboard Remote-HEAD: {stream_commit}")
        log(f"OSCam-Emu Commit:        {emu_commit}")

        # Revisions-Buttons aktualisieren
        if gui_instance is not None:
            try:
                gui_instance.display_streamboard_commit = stream_commit
                gui_instance.display_emu_commit = emu_commit
                gui_instance.update_revision_button_labels()
            except Exception as exc:
                log(
                    "[GUI] Revisions-Anzeige konnte nicht "
                    f"aktualisiert werden: {exc}"
                )

        log(f"OSCam-Emu URL:           {emu_repo_url}")
        log(f"Patch: {patch_file}")
        log(
            "Größe: "
            + f"{final_size:,}".replace(",", ".")
            + " Bytes"
        )
        log("========================================")

        set_progress(
            100,
            tr(
                "patch_success",
                "Patch erfolgreich erstellt – %p%",
                "Patch successfully created – %p%",
            ),
        )

        
        # ========================================================
        # Erfolgssound – Windows + Linux
        # ========================================================

        play_patch_sound("success")

        return True

    # ============================================================
    # Fehlerbehandlung
    # ============================================================

    except Exception as exc:

        error_text = str(exc).strip()

        if not error_text:
            error_text = tr(
                "unknown_error",
                "Unbekannter Fehler.",
                "Unknown error."
            )

        log("")
        log("========================================")
        log("PATCH FEHLER")
        log("========================================")
        log(error_text)
        log("========================================")

        set_progress(
            0,
            "❌ " + tr(
                "patch_error",
                "Fehler bei der Patch-Erstellung",
                "Patch creation failed"
            )
        )

        # ========================================================
        # Fehler-Sound – Windows + Linux
        # ========================================================

        play_patch_sound("error")

        # ========================================================
        # Fehler ins Info-Fenster
        # ========================================================

        try:
            if gui_instance is not None:
                append_info = getattr(
                    gui_instance,
                    "append_info",
                    None
                )

                if callable(append_info):
                    append_info(
                        getattr(
                            gui_instance,
                            "info_text",
                            info_widget
                        ),
                        "❌ " + error_text,
                        "error"
                    )

        except Exception:
            pass

        return False

def play_cross_platform_sound(sound_type="success"):
    """Play a notification sound on Windows or Linux, best-effort."""
    import os
    import sys
    import shutil
    import subprocess

    sound_type = str(sound_type or "success").strip().lower()
    if sound_type not in ("start", "success", "error"):
        sound_type = "success"

    if sys.platform == "win32":
        try:
            import winsound
            flags = {
                "start": winsound.MB_ICONASTERISK,
                "success": winsound.MB_OK,
                "error": winsound.MB_ICONHAND,
            }
            winsound.MessageBeep(flags[sound_type])
            return True
        except Exception:
            pass

    base_dir = os.path.dirname(os.path.abspath(__file__))
    own_sounds = {
        "start": ("boot.wav", "glitch.wav"),
        "success": ("success.wav", "boot.wav"),
        "error": ("error.wav", "glitch.wav"),
    }

    # Prefer the application's own sounds on Linux.
    players = ("paplay", "pw-play", "aplay", "ffplay")
    for filename in own_sounds[sound_type]:
        sound_path = os.path.join(base_dir, "sounds", filename)
        if not os.path.isfile(sound_path):
            continue

        for player in players:
            executable = shutil.which(player)
            if not executable:
                continue
            try:
                command = [executable, sound_path]
                if player == "ffplay":
                    command = [
                        executable, "-nodisp", "-autoexit",
                        "-loglevel", "quiet", sound_path
                    ]
                subprocess.Popen(
                    command,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    start_new_session=True,
                )
                return True
            except Exception:
                continue

    # Fall back to desktop notification sounds if no project sound works.
    sound_names = {
        "start": ("message-new-instant.oga", "complete.oga"),
        "success": ("complete.oga", "complete.wav"),
        "error": ("dialog-error.oga", "dialog-warning.oga"),
    }[sound_type]

    sound_dirs = (
        "/usr/share/sounds/freedesktop/stereo",
        "/usr/share/sounds/gnome/default/alerts",
        "/usr/share/sounds",
        os.path.expanduser("~/.local/share/sounds"),
    )

    for sound_dir in sound_dirs:
        for filename in sound_names:
            sound_path = os.path.join(sound_dir, filename)
            if not os.path.isfile(sound_path):
                continue
            for player in players:
                executable = shutil.which(player)
                if not executable:
                    continue
                try:
                    command = [executable, sound_path]
                    if player == "ffplay":
                        command = [
                            executable, "-nodisp", "-autoexit",
                            "-loglevel", "quiet", sound_path
                        ]
                    subprocess.Popen(
                        command,
                        stdin=subprocess.DEVNULL,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        start_new_session=True,
                    )
                    return True
                except Exception:
                    continue

    return False


# ===================== backup_old_patch =====================

def backup_old_patch(
    self,
    make_backup=True,
    info_widget=None,
    progress_callback=None
):
    """
    Sichert den bisher im eingestellten Patch-Ordner vorhandenen
    Patch als:

        alt_oscam-emu.patch

    und kopiert anschließend den aktuell erzeugten Patch:

        PATCH_FILE

    als:

        oscam-emu.patch

    in den eingestellten Patch-Ordner.

    Jeder Kopiervorgang wird anschließend physisch überprüft.
    """

    # ---------------------------------------------------------
    # FINAL LABEL VERSTECKEN
    # ---------------------------------------------------------
    if hasattr(self, "hide_final_label"):
        try:
            self.hide_final_label()
        except Exception:
            pass

    import os
    import shutil
    import re

    try:
        import winsound
    except Exception:
        winsound = None

    from PyQt6.QtWidgets import (
        QTextEdit,
        QApplication,
    )
    from PyQt6.QtCore import QTimer

    # ---------------------------------------------------------
    # WIDGET / SPRACHE / PROGRESS
    # ---------------------------------------------------------
    widget = (
        info_widget
        if isinstance(info_widget, QTextEdit)
        else getattr(self, "info_text", None)
    )

    lang = getattr(
        self,
        "LANG",
        "de"
    ).lower()

    is_de = lang == "de"

    pbar = getattr(
        self,
        "progress_bar",
        None
    )

    # ---------------------------------------------------------
    # PROGRESSBAR STYLE
    # ---------------------------------------------------------
    STYLE_TEMPLATE = """
        QProgressBar {{
            border: 2px solid #444444;
            border-radius: 8px;
            background-color: #0A0A0A;
            color: black;
            text-align: center;
            font-weight: 900;
            font-size: 20px;
            min-height: 35px;
        }}

        QProgressBar::chunk {{
            background-color: {chunk_color};
            border-radius: 6px;
        }}
    """

    RAINBOW_GRADIENT = (
        "qlineargradient("
        "spread:pad, "
        "x1:0, y1:0, "
        "x2:1, y2:0, "
        "stop:0 #FF00FF, "
        "stop:0.5 #00FFFF, "
        "stop:1 #39FF14"
        ")"
    )

    # ---------------------------------------------------------
    # PROGRESS SETZEN
    # ---------------------------------------------------------
    def set_progress(value, text=None):
        if pbar:
            try:
                pbar.setStyleSheet(
                    STYLE_TEMPLATE.format(
                        chunk_color=RAINBOW_GRADIENT
                    )
                )

                pbar.setValue(int(value))

                if text is not None:
                    pbar.setFormat(str(text))

                pbar.show()

            except Exception:
                pass

        if progress_callback:
            try:
                progress_callback(int(value))
                QApplication.processEvents()
            except Exception:
                pass

    # ---------------------------------------------------------
    # PROGRESS FERTIG
    # ---------------------------------------------------------
    def finalize_pbar(text, visible_seconds=3):
        if not pbar:
            return

        try:
            pbar.setStyleSheet(
                STYLE_TEMPLATE.format(
                    chunk_color=RAINBOW_GRADIENT
                )
            )

            pbar.setValue(100)
            pbar.setFormat(text)
            pbar.show()

            QTimer.singleShot(
                visible_seconds * 1000,
                lambda: pbar.setStyleSheet(
                    STYLE_TEMPLATE.format(
                        chunk_color="transparent"
                    )
                )
            )

            QTimer.singleShot(
                visible_seconds * 1000,
                lambda: pbar.setValue(0)
            )

        except Exception:
            pass

    # ---------------------------------------------------------
    # LOG
    # ---------------------------------------------------------
    def log(text_key, level="info", **kwargs):
        template = (
            TEXTS
            .get(lang, {})
            .get(text_key, text_key)
        )

        try:
            text = template.format(**kwargs)
        except Exception:
            text = text_key

        if isinstance(widget, QTextEdit):
            try:
                self.append_info(
                    widget,
                    text,
                    level
                )
            except Exception:
                try:
                    widget.append(str(text))
                except Exception:
                    pass

    # ---------------------------------------------------------
    # FEHLER-HILFSFUNKTION
    # ---------------------------------------------------------
    def fail(message):
        print(
            f"[BACKUP OLD PATCH] FEHLER: {message}",
            flush=True
        )

        log(
            "patch_failed",
            "error",
            path=str(message)
        )

        finalize_pbar(
            "❌ Fehler!"
            if is_de
            else "❌ Error!"
        )

        # ---------------------------------------------------------
        # FEHLERSOUND – WINDOWS UND LINUX
        # ---------------------------------------------------------
        play_cross_platform_sound("error")

        return False

    # ---------------------------------------------------------
    # START
    # ---------------------------------------------------------
    print(
        "[BACKUP OLD PATCH] START",
        flush=True
    )

    # ---------------------------------------------------------
    # STARTSOUND – WINDOWS UND LINUX
    # ---------------------------------------------------------
    play_cross_platform_sound("start")

    set_progress(
        10,
        "Vorbereiten..."
        if is_de
        else "Preparing..."
    )

    log(
        "backup_old_start",
        "info"
    )

    # ---------------------------------------------------------
    # AKTUELL ERZEUGTEN PATCH ERMITTELN
    # ---------------------------------------------------------
    new_patch = globals().get(
        "PATCH_FILE"
    )

    if not new_patch:
        return fail(
            "PATCH_FILE ist nicht definiert."
            if is_de
            else
            "PATCH_FILE is not defined."
        )

    new_patch = os.path.abspath(
        str(new_patch)
    )

    print(
        "[BACKUP OLD PATCH] Quelle:",
        flush=True
    )

    print(
        f"  {new_patch}",
        flush=True
    )

    # ---------------------------------------------------------
    # QUELLDATEI PRÜFEN
    # ---------------------------------------------------------
    if not os.path.isfile(new_patch):
        return fail(
            f"Der aktuelle Patch wurde nicht gefunden:\n"
            f"{new_patch}"
        )

    try:
        new_patch_size = os.path.getsize(new_patch)
    except Exception as exc:
        return fail(
            f"Größe des aktuellen Patches konnte nicht ermittelt "
            f"werden:\n{exc}"
        )

    if new_patch_size <= 0:
        return fail(
            f"Der aktuelle Patch ist leer:\n{new_patch}"
        )

    print(
        "[BACKUP OLD PATCH] Quellgröße:",
        f"{new_patch_size:,} Bytes".replace(",", "."),
        flush=True
    )

    # ---------------------------------------------------------
    # EINGESTELLTEN PATCH-ORDNER ERMITTELN
    # ---------------------------------------------------------
    target_dir = None

    # ---------------------------------------------------------
    # 1. GUI-ATTRIBUTE
    # ---------------------------------------------------------
    possible_attrs = (
        "OLD_PATCH_DIR",
        "old_patch_dir",
        "PATCH_DIR",
        "patch_dir",
        "OLD_PATCH_PATH",
        "old_patch_path",
    )

    for attr_name in possible_attrs:

        value = getattr(
            self,
            attr_name,
            None
        )

        if not value:
            continue

        value = str(value).strip()

        if not value:
            continue

        if os.path.splitext(value)[1].lower() == ".patch":
            value = os.path.dirname(value)

        target_dir = value

        print(
            "[BACKUP OLD PATCH] "
            f"Zielordner aus self.{attr_name}: "
            f"{target_dir}",
            flush=True
        )

        break

    # ---------------------------------------------------------
    # 2. CURRENT_CONFIG
    # ---------------------------------------------------------
    if not target_dir:

        config = getattr(
            self,
            "current_config",
            None
        )

        if isinstance(config, dict):

            possible_keys = (
                "old_patch_dir",
                "OLD_PATCH_DIR",
                "patch_dir",
                "PATCH_DIR",
                "old_patch_path",
                "OLD_PATCH_PATH",
            )

            for key in possible_keys:

                value = config.get(key)

                if not value:
                    continue

                value = str(value).strip()

                if not value:
                    continue

                if os.path.splitext(value)[1].lower() == ".patch":
                    value = os.path.dirname(value)

                target_dir = value

                print(
                    "[BACKUP OLD PATCH] "
                    f"Zielordner aus config['{key}']: "
                    f"{target_dir}",
                    flush=True
                )

                break

    # ---------------------------------------------------------
    # 3. OLD_PATCH_FILE ALS FALLBACK
    # ---------------------------------------------------------
    if not target_dir:

        existing_old_patch = getattr(
            self,
            "OLD_PATCH_FILE",
            globals().get("OLD_PATCH_FILE")
        )

        if existing_old_patch:

            existing_old_patch = os.path.abspath(
                str(existing_old_patch)
            )

            target_dir = os.path.dirname(
                existing_old_patch
            )

            print(
                "[BACKUP OLD PATCH] "
                "Zielordner aus bestehendem OLD_PATCH_FILE: "
                f"{target_dir}",
                flush=True
            )

    # ---------------------------------------------------------
    # KEIN ZIELORDNER
    # ---------------------------------------------------------
    if not target_dir:
        return fail(
            "Kein Patch-Zielordner ist eingestellt."
        )

    # ---------------------------------------------------------
    # ZIELORDNER ABSOLUT MACHEN
    # ---------------------------------------------------------
    target_dir = os.path.abspath(
        os.path.expanduser(
            target_dir
        )
    )

    # ---------------------------------------------------------
    # FESTE DATEINAMEN
    # ---------------------------------------------------------
    old_patch = os.path.join(
        target_dir,
        "oscam-emu.patch"
    )

    alt_patch = os.path.join(
        target_dir,
        "oscam-emu.patch.alt"
    )

    # ---------------------------------------------------------
    # ATTRIBUTE AKTUALISIEREN
    # ---------------------------------------------------------
    self.OLD_PATCH_DIR = target_dir
    self.OLD_PATCH_FILE = old_patch
    self.ALT_PATCH_FILE = alt_patch

    # ---------------------------------------------------------
    # AUSGABE
    # ---------------------------------------------------------
    print(
        "[BACKUP OLD PATCH] Zielordner:",
        flush=True
    )

    print(
        f"  {target_dir}",
        flush=True
    )

    print(
        "[BACKUP OLD PATCH] Ziel-Patch:",
        flush=True
    )

    print(
        f"  {old_patch}",
        flush=True
    )

    print(
        "[BACKUP OLD PATCH] Backup-Datei:",
        flush=True
    )

    print(
        f"  {alt_patch}",
        flush=True
    )

    # ---------------------------------------------------------
    # ZIELORDNER ERSTELLEN
    # ---------------------------------------------------------
    try:

        os.makedirs(
            target_dir,
            exist_ok=True
        )

    except Exception as exc:
        return fail(
            f"Zielordner konnte nicht erstellt werden:\n{exc}"
        )

    if not os.path.isdir(target_dir):
        return fail(
            f"Zielordner existiert nicht:\n{target_dir}"
        )

    # ---------------------------------------------------------
    # PATCH PRÜFEN
    # ---------------------------------------------------------
    set_progress(
        25,
        "Patch prüfen..."
        if is_de
        else "Checking patch..."
    )

    # ---------------------------------------------------------
    # ALTEN PATCH SICHERN
    # ---------------------------------------------------------
    set_progress(
        40,
        "Alten Patch sichern..."
        if is_de
        else "Backing up old patch..."
    )

    if make_backup and os.path.isfile(old_patch):

        try:

            old_patch_size = os.path.getsize(
                old_patch
            )

            print(
                "[BACKUP OLD PATCH] Alter Patch gefunden:",
                flush=True
            )

            print(
                f"  {old_patch}",
                flush=True
            )

            print(
                "[BACKUP OLD PATCH] Größe:",
                f"{old_patch_size:,} Bytes".replace(",", "."),
                flush=True
            )

            # -------------------------------------------------
            # KOPIEREN
            # -------------------------------------------------
            shutil.copy2(
                old_patch,
                alt_patch
            )

            # -------------------------------------------------
            # WICHTIG:
            # BACKUP PHYSISCH ÜBERPRÜFEN
            # -------------------------------------------------
            if not os.path.isfile(alt_patch):
                return fail(
                    "Backup wurde angeblich erstellt, "
                    "aber die Zieldatei existiert nicht:\n"
                    f"{alt_patch}"
                )

            backup_size = os.path.getsize(
                alt_patch
            )

            if backup_size != old_patch_size:
                return fail(
                    "Backup wurde erstellt, aber die "
                    "Dateigröße stimmt nicht überein.\n\n"
                    f"Quelle:\n"
                    f"{old_patch}\n"
                    f"{old_patch_size:,} Bytes\n\n"
                    f"Backup:\n"
                    f"{alt_patch}\n"
                    f"{backup_size:,} Bytes"
                    .replace(",", ".")
                )

            print(
                "[BACKUP OLD PATCH] "
                "Backup physisch verifiziert:",
                flush=True
            )

            print(
                f"  {alt_patch}",
                flush=True
            )

            print(
                "[BACKUP OLD PATCH] Backup-Größe:",
                f"{backup_size:,} Bytes".replace(",", "."),
                flush=True
            )

            # Erst JETZT Erfolg melden
            log(
                "backup_done",
                "success",
                path=alt_patch
            )

        except Exception as exc:

            print(
                "[BACKUP OLD PATCH] "
                f"Backup fehlgeschlagen: {exc}",
                flush=True
            )

            return fail(
                f"Backup konnte nicht erstellt werden:\n{exc}"
            )

    else:

        print(
            "[BACKUP OLD PATCH] "
            f"Keine alte Patch-Datei gefunden: {old_patch}",
            flush=True
        )

        log(
            "no_old_patch",
            "info"
        )

    # ---------------------------------------------------------
    # NEUEN PATCH INSTALLIEREN
    # ---------------------------------------------------------
    set_progress(
        65,
        "Neuen Patch installieren..."
        if is_de
        else "Installing new patch..."
    )

    try:

        # Falls Quelle und Ziel dieselbe Datei sind,
        # ist kein Kopieren notwendig.
        source_real = os.path.realpath(
            new_patch
        )

        target_real = os.path.realpath(
            old_patch
        )

        if source_real == target_real:

            print(
                "[BACKUP OLD PATCH] "
                "Quelle und Ziel sind identisch.",
                flush=True
            )

        else:

            shutil.copy2(
                new_patch,
                old_patch
            )

        # -----------------------------------------------------
        # NEUEN PATCH PHYSISCH ÜBERPRÜFEN
        # -----------------------------------------------------
        if not os.path.isfile(old_patch):
            return fail(
                "Der neue Patch wurde kopiert, "
                "aber die Zieldatei existiert nicht:\n"
                f"{old_patch}"
            )

        installed_size = os.path.getsize(
            old_patch
        )

        if installed_size != new_patch_size:
            return fail(
                "Der neue Patch wurde kopiert, "
                "aber die Dateigröße stimmt nicht überein.\n\n"
                f"Quelle:\n"
                f"{new_patch}\n"
                f"{new_patch_size:,} Bytes\n\n"
                f"Ziel:\n"
                f"{old_patch}\n"
                f"{installed_size:,} Bytes"
                .replace(",", ".")
            )

        print(
            "[BACKUP OLD PATCH] "
            "Neuer Patch physisch verifiziert:",
            flush=True
        )

        print(
            f"  {old_patch}",
            flush=True
        )

        print(
            "[BACKUP OLD PATCH] Größe:",
            f"{installed_size:,} Bytes".replace(",", "."),
            flush=True
        )

        set_progress(
            90,
            "Patch installiert..."
            if is_de
            else "Patch installed..."
        )

    except Exception as exc:

        print(
            "[BACKUP OLD PATCH] "
            f"Kopieren fehlgeschlagen: {exc}",
            flush=True
        )

        return fail(
            f"Neuer Patch konnte nicht installiert werden:\n{exc}"
        )

    # ---------------------------------------------------------
    # VERSION AUS HEADER LESEN
    # ---------------------------------------------------------
    patch_version = "???"

    try:

        with open(
            old_patch,
            "r",
            encoding="utf-8",
            errors="ignore"
        ) as handle:

            content = handle.read(2000)

        match = re.search(
            r"(?im)^\s*patch\s+version\s*:\s*(.+?)\s*$",
            content
        )

        if match:
            patch_version = (
                match.group(1)
                .strip()
            )

    except Exception as exc:

        print(
            "[BACKUP OLD PATCH] "
            f"Version konnte nicht gelesen werden: {exc}",
            flush=True
        )

    # ---------------------------------------------------------
    # ABSOLUTE ERFOLGSPRÜFUNG
    # ---------------------------------------------------------
    print(
        "",
        flush=True
    )

    print(
        "[BACKUP OLD PATCH] =============================",
        flush=True
    )

    print(
        "[BACKUP OLD PATCH] DATEI-CHECK:",
        flush=True
    )

    print(
        f"  Zielordner       : {target_dir}",
        flush=True
    )

    print(
        f"  Neuer Patch      : {old_patch}",
        flush=True
    )

    print(
        f"  Neuer Patch da?  : {os.path.isfile(old_patch)}",
        flush=True
    )

    print(
        f"  Neuer Patch Größe: "
        f"{os.path.getsize(old_patch):,} Bytes"
        .replace(",", "."),
        flush=True
    )

    print(
        f"  Backup           : {alt_patch}",
        flush=True
    )

    print(
        f"  Backup vorhanden?: "
        f"{os.path.isfile(alt_patch)}",
        flush=True
    )

    if os.path.isfile(alt_patch):
        print(
            f"  Backup Größe     : "
            f"{os.path.getsize(alt_patch):,} Bytes"
            .replace(",", "."),
            flush=True
        )

    print(
        "[BACKUP OLD PATCH] =============================",
        flush=True
    )

    # ---------------------------------------------------------
    # ERFOLGSMELDUNG
    # ---------------------------------------------------------
    log(
        "new_patch_installed",
        "success",
        path=(
            f"{old_patch} "
            f"(v: {patch_version})"
        )
    )

    # ---------------------------------------------------------
    # ERFOLGSSOUND – WINDOWS UND LINUX
    # ---------------------------------------------------------
    play_cross_platform_sound("success")

    

    # ---------------------------------------------------------
    # FERTIG
    # ---------------------------------------------------------
    finalize_pbar(
        "✅ Patch fertig!"
        if is_de
        else "✅ Patch done!"
    )

    print(
        "[BACKUP OLD PATCH] ERFOLGREICH",
        flush=True
    )

    print(
        "[BACKUP OLD PATCH] "
        f"Alte Datei -> {alt_patch}",
        flush=True
    )

    print(
        "[BACKUP OLD PATCH] "
        f"Neue Datei -> {old_patch}",
        flush=True
    )

    return True

def add_patch_header(
    patch_file,
    streamboard_repo,
    streamboard_commit,
    modified_by=None
):
    """
    Fügt den OSCam-Emu Patch-Header oben in PATCH_FILE ein.

    Der eigentliche Git-Diff bleibt unverändert.

    Format:

        patch version: 2.26.07-11966-802 (5905109e)
        patch date: 2026-08-01 06:43:54 UTC (+00:00)
        patch modified by speedy005 (01/08/2026)

        diff --git ...
    """

    import os
    import datetime

    # ============================================================
    # Patch prüfen
    # ============================================================

    if not os.path.isfile(patch_file):
        raise RuntimeError(
            "Patch-Datei nicht gefunden:\n"
            f"{patch_file}"
        )

    # ============================================================
    # Streamboard-Version
    # ============================================================

    version = "unbekannt"

    version_func = globals().get(
        "get_streamboard_version"
    )

    if callable(version_func):

        try:
            version = version_func()

        except Exception:
            version = "unbekannt"

    if not version:
        version = "unbekannt"

    version = str(version).strip()

    # ============================================================
    # Commit
    # ============================================================

    commit_short = "unbekannt"

    git_func = globals().get(
        "git_command"
    )

    if not callable(git_func):
        git_func = globals().get(
            "_git_command"
        )

    if callable(git_func):

        try:

            result = git_func(
                [
                    "rev-parse",
                    "--short=8",
                    streamboard_commit
                ],
                cwd=streamboard_repo
            )

            commit_short = (
                getattr(
                    result,
                    "stdout",
                    ""
                )
                or ""
            ).strip()

        except Exception:
            pass

    # ============================================================
    # Commit-Datum
    # ============================================================

    commit_date = None

    if callable(git_func):

        try:

            result = git_func(
                [
                    "show",
                    "-s",
                    "--format=%cI",
                    streamboard_commit
                ],
                cwd=streamboard_repo
            )

            raw_date = (
                getattr(
                    result,
                    "stdout",
                    ""
                )
                or ""
            ).strip()

            if raw_date:

                dt = datetime.datetime.fromisoformat(
                    raw_date.replace(
                        "Z",
                        "+00:00"
                    )
                )

                # UTC erzwingen
                dt_utc = dt.astimezone(
                    datetime.timezone.utc
                )

                commit_date = (
                    dt_utc.strftime(
                        "%Y-%m-%d %H:%M:%S UTC"
                    )
                    + " (+00:00)"
                )

        except Exception:
            commit_date = None

    if not commit_date:

        dt_utc = datetime.datetime.now(
            datetime.timezone.utc
        )

        commit_date = (
            dt_utc.strftime(
                "%Y-%m-%d %H:%M:%S UTC"
            )
            + " (+00:00)"
        )

    # ============================================================
    # Bearbeiter
    # ============================================================

    if not modified_by:

        cfg_func = globals().get(
            "load_github_config"
        )

        if callable(cfg_func):

            try:

                cfg = cfg_func() or {}

                modified_by = (
                    cfg.get("user_name")
                    or cfg.get("username")
                    or "speedy005"
                )

            except Exception:

                modified_by = "speedy005"

        else:

            modified_by = "speedy005"

    modified_by = str(
        modified_by
    ).strip()

    # ============================================================
    # Patch lesen
    # ============================================================

    with open(
        patch_file,
        "r",
        encoding="utf-8",
        errors="replace"
    ) as f:

        patch_content = f.read()

    # ============================================================
    # Bereits vorhandenen Header entfernen
    #
    # Dadurch entstehen bei wiederholtem Erstellen keine
    # doppelten Header.
    # ============================================================

    lines = patch_content.splitlines(
        keepends=True
    )

    while (
        lines
        and not lines[0].strip()
    ):
        lines.pop(0)

    if (
        len(lines) >= 3
        and lines[0].lower().startswith(
            "patch version:"
        )
        and lines[1].lower().startswith(
            "patch date:"
        )
        and lines[2].lower().startswith(
            "patch modified by"
        )
    ):

        lines = lines[3:]

        while (
            lines
            and not lines[0].strip()
        ):
            lines.pop(0)

        patch_content = "".join(
            lines
        )

    # ============================================================
    # Header
    # ============================================================

    today = datetime.datetime.now().strftime(
        "%d/%m/%Y"
    )

    header = (
        f"patch version: {version} "
        f"({commit_short})\n"
        f"patch date: {commit_date}\n"
        f"patch modified by {modified_by} "
        f"({today})\n"
        f"\n"
    )

    # ============================================================
    # Patch schreiben
    # ============================================================

    with open(
        patch_file,
        "w",
        encoding="utf-8",
        newline="\n"
    ) as f:

        f.write(header)
        f.write(patch_content)

    return header.strip()

def patch_oscam_emu_git(
    gui_instance=None,
    info_widget=None,
    progress_callback=None
):
    """
    Klont das aktuelle Streamboard-OSCam, wendet den
    oscam-emu.patch an und übernimmt das fertige Git-
    Repository nach PATCH_EMU_GIT_DIR.

    Die Fortschrittsanzeige läuft von 0 bis 100 %.

    Bei erfolgreichem Abschluss wird in der Progressbar
    ausschließlich der TEXTS-Eintrag

        patch_emu_git_success

    angezeigt.
    """

    from PyQt6.QtWidgets import QTextEdit, QApplication

    import os
    import shutil
    import subprocess
    import stat
    import time

    # ============================================================
    # GUI
    # ============================================================

    gui = gui_instance

    if gui and hasattr(gui, "hide_final_label"):
        try:
            gui.hide_final_label()
        except Exception:
            pass

    widget = (
        info_widget
        if isinstance(info_widget, QTextEdit)
        else getattr(gui, "info_text", None)
    )

    pbar = getattr(
        gui,
        "progress_bar",
        None
    )

    # ============================================================
    # SPRACHE
    # ============================================================

    lang = "de"

    try:
        lang = str(
            getattr(
                gui,
                "LANG",
                "de"
            )
        ).lower().strip()[:2]
    except Exception:
        lang = "de"

    if lang not in ("de", "en"):
        lang = "de"

    # ============================================================
    # TEXTS
    # ============================================================

    def tr(key, fallback_de, fallback_en=None):
        """
        Liest Texte aus TEXTS.

        Beispiel:
            TEXTS["de"]["patch_emu_git_success"]
        """

        if fallback_en is None:
            fallback_en = fallback_de

        try:
            language_dict = TEXTS.get(
                lang,
                {}
            )

            if isinstance(
                language_dict,
                dict
            ):
                value = language_dict.get(
                    key
                )

                if value:
                    return str(value)

        except Exception:
            pass

        return (
            fallback_en
            if lang == "en"
            else fallback_de
        )

    # ============================================================
    # PROGRESSBAR STYLE
    # ============================================================

    STYLE_BASE = """
        QProgressBar {{
            border: 2px solid #444444;
            border-radius: 8px;
            background-color: #0A0A0A;
            color: {text_color};
            text-align: center;
            font-weight: 900;
            font-size: 20px;
            min-height: 35px;
        }}

        QProgressBar::chunk {{
            background: {chunk_color};
            border-radius: 6px;
        }}
    """

    RAINBOW_GRADIENT = (
        "qlineargradient("
        "spread:pad, "
        "x1:0, y1:0, "
        "x2:1, y2:0, "
        "stop:0 #FF00FF, "
        "stop:0.5 #00FFFF, "
        "stop:1 #39FF14)"
    )

    ERROR_GRADIENT = (
        "qlineargradient("
        "spread:pad, "
        "x1:0, y1:0, "
        "x2:1, y2:0, "
        "stop:0 #800000, "
        "stop:1 #FF0000)"
    )

    # ============================================================
    # PROGRESS SETZEN
    # ============================================================

    def update_progress(
        value,
        text=None,
        error=False
    ):
        """
        Aktualisiert ausschließlich die Progressbar.

        Wichtig:
        Bei 100 % wird der übergebene Text als Abschlussmeldung
        angezeigt und nicht wieder überschrieben.
        """

        try:
            value = int(value)
        except Exception:
            value = 0

        value = max(
            0,
            min(
                100,
                value
            )
        )

        if pbar is not None:

            try:

                if error:

                    pbar.setStyleSheet(
                        STYLE_BASE.format(
                            text_color="#FF0000",
                            chunk_color=ERROR_GRADIENT
                        )
                    )

                else:

                    pbar.setStyleSheet(
                        STYLE_BASE.format(
                            text_color="black",
                            chunk_color=RAINBOW_GRADIENT
                        )
                    )

                pbar.setValue(
                    value
                )

                if text:
                    pbar.setFormat(
                        str(text)
                    )
                else:
                    pbar.setFormat(
                        f"{value}%"
                    )

                pbar.show()

            except Exception as progress_error:

                print(
                    "ProgressBar-Fehler:",
                    progress_error,
                    flush=True
                )

        # --------------------------------------------------------
        # Externer Callback
        # --------------------------------------------------------

        if progress_callback is not None:

            try:
                progress_callback(
                    value
                )
            except Exception as callback_error:

                print(
                    "Progress-Callback-Fehler:",
                    callback_error,
                    flush=True
                )

        try:
            QApplication.processEvents()
        except Exception:
            pass

    # ============================================================
    # LOG
    # ============================================================

    def log(
        text,
        level="info"
    ):

        text = str(
            text
        )

        if gui and hasattr(
            gui,
            "append_info"
        ):

            try:

                gui.append_info(
                    widget,
                    text,
                    level
                )

            except Exception:
                pass

        elif isinstance(
            widget,
            QTextEdit
        ):

            color = {
                "success": "#39FF14",
                "warning": "orange",
                "error": "red"
            }.get(
                level,
                "gray"
            )

            try:

                widget.append(
                    f'<span style="color:{color}">'
                    f'{text}'
                    f'</span>'
                )

            except Exception:
                pass

        print(
            text,
            flush=True
        )

        try:
            QApplication.processEvents()
        except Exception:
            pass

    # ============================================================
    # SOUND
    # ============================================================

    def play_sound(success=True):
        """Legacy wrapper; routes old calls through the shared sound backend."""
        return play_cross_platform_sound(
            "success" if success else "error"
        )

    # ============================================================
    # GIT
    # ============================================================

    def run_git(
        args,
        cwd,
        allowed_returncodes=(0,),
        timeout=600
    ):

        git_func = globals().get(
            "git_command"
        )

        if callable(
            git_func
        ):

            return git_func(
                args,
                cwd=cwd,
                allowed_returncodes=allowed_returncodes,
                timeout=timeout
            )

        git_func = globals().get(
            "_git_command"
        )

        if callable(
            git_func
        ):

            return git_func(
                args,
                cwd=cwd,
                allowed_returncodes=allowed_returncodes,
                timeout=timeout
            )

        result = subprocess.run(
            ["git"] + list(args),
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
            shell=False,
            timeout=timeout
        )

        if result.returncode not in allowed_returncodes:

            error_text = (
                result.stderr.strip()
                or result.stdout.strip()
                or f"Git Returncode: {result.returncode}"
            )

            raise RuntimeError(
                "Git-Befehl fehlgeschlagen:\n\n"
                + " ".join(
                    ["git"] + list(args)
                )
                + "\n\n"
                + error_text
            )

        return result

    # ============================================================
    # GIT OUTPUT
    # ============================================================

    def git_output(
        args,
        cwd
    ):

        result = run_git(
            args,
            cwd=cwd
        )

        return (
            getattr(
                result,
                "stdout",
                ""
            )
            or ""
        ).strip()

    # ============================================================
    # WINDOWS SICHERES LÖSCHEN
    # ============================================================

    def remove_readonly(
        func,
        path,
        exc_info
    ):

        try:
            os.chmod(
                path,
                stat.S_IWRITE
                | stat.S_IREAD
            )
        except Exception:
            pass

        func(
            path
        )

    # ============================================================
    # VERZEICHNIS LÖSCHEN
    # ============================================================

    def safe_remove_directory(
        directory,
        retries=5
    ):

        if not os.path.exists(
            directory
        ):
            return

        directory = os.path.abspath(
            directory
        )

        last_error = None

        # --------------------------------------------------------
        # Schreibschutz entfernen
        # --------------------------------------------------------

        try:

            subprocess.run(
                [
                    "attrib",
                    "-R",
                    "-S",
                    "-H",
                    f"{directory}\\*",
                    "/S",
                    "/D"
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                shell=False
            )

        except Exception:
            pass

        # --------------------------------------------------------
        # Dateien beschreibbar machen
        # --------------------------------------------------------

        try:

            for root, dirs, files in os.walk(
                directory
            ):

                for filename in files:

                    path = os.path.join(
                        root,
                        filename
                    )

                    try:

                        os.chmod(
                            path,
                            stat.S_IWRITE
                            | stat.S_IREAD
                        )

                    except Exception:
                        pass

        except Exception:
            pass

        # --------------------------------------------------------
        # Mehrfach versuchen
        # --------------------------------------------------------

        for attempt in range(
            1,
            retries + 1
        ):

            try:

                shutil.rmtree(
                    directory,
                    onerror=remove_readonly
                )

                if not os.path.exists(
                    directory
                ):
                    return

            except Exception as exc:

                last_error = exc

                if attempt < retries:

                    log(
                        f"⚠️ Löschen fehlgeschlagen "
                        f"(Versuch {attempt}/{retries})...",
                        "warning"
                    )

                    try:
                        QApplication.processEvents()
                    except Exception:
                        pass

                    time.sleep(
                        0.5
                    )

        raise RuntimeError(
            "Verzeichnis konnte nicht gelöscht werden.\n\n"
            f"{directory}\n\n"
            f"Fehler:\n{last_error}"
        )

    # ============================================================
    # START
    # ============================================================
    # Startsound – einheitlich für Windows und Linux.
    play_cross_platform_sound("start")


    temp_dir = None
    backup_dir = None

    try:

        # ========================================================
        # 0 %
        # ========================================================

        update_progress(
            0,
            tr(
                "patch_emu_git_start",
                "🚀 OSCam-Emu-Git Patch-Prozess wird gestartet...",
                "🚀 Starting OSCam-Emu Git patch process..."
            )
        )

        log(
            ""
        )

        log(
            "════════════════════════════════════════════"
        )

        log(
            "       OSCAM-EMU-GIT PATCHEN"
        )

        log(
            "════════════════════════════════════════════"
        )

        log(
            ""
        )

        # ========================================================
        # GLOBALE PFADE
        # ========================================================

        patch_dir = globals().get(
            "PATCH_EMU_GIT_DIR"
        )

        stream_url = get_selected_stream_repo_url()

        patch_file = globals().get(
            "PATCH_FILE"
        )

        if not patch_dir:

            raise RuntimeError(
                "PATCH_EMU_GIT_DIR ist nicht definiert."
            )

        if not patch_file:

            raise RuntimeError(
                "PATCH_FILE ist nicht definiert."
            )

        patch_dir = os.path.abspath(
            patch_dir
        )

        patch_file = os.path.abspath(
            patch_file
        )

        plugin_dir = os.path.dirname(
            patch_dir
        )

        temp_dir = os.path.join(
            plugin_dir,
            ".oscam_emu_patch_work"
        )

        # ========================================================
        # INFO
        # ========================================================

        log(
            f"📂 Zielordner: {patch_dir}"
        )

        log(
            f"🌐 Streamboard: {stream_url}"
        )

        log(
            f"📄 Patch: {patch_file}"
        )

        # ========================================================
        # PATCH PRÜFEN
        # ========================================================

        if not os.path.isfile(
            patch_file
        ):

            raise RuntimeError(
                "Die Patch-Datei wurde nicht gefunden:\n"
                f"{patch_file}"
            )

        # ========================================================
        # GIT PRÜFEN
        # ========================================================

        update_progress(
            10,
            tr(
                "patch_emu_git_check_git",
                "🔎 Git wird geprüft...",
                "🔎 Checking Git..."
            )
        )

        run_git(
            [
                "--version"
            ],
            cwd=plugin_dir
        )

        # ========================================================
        # ARBEITSUMGEBUNG
        # ========================================================

        update_progress(
            15,
            tr(
                "patch_emu_git_prepare",
                "🧹 Arbeitsumgebung wird vorbereitet...",
                "🧹 Preparing workspace..."
            )
        )

        if os.path.exists(
            temp_dir
        ):

            safe_remove_directory(
                temp_dir
            )

        os.makedirs(
            temp_dir,
            exist_ok=True
        )

        # ========================================================
        # STREAMBOARD KLONEN
        # ========================================================

        update_progress(
            25,
            tr(
                "patch_emu_git_clone",
                "🌐 Streamboard wird geklont...",
                "🌐 Cloning Streamboard..."
            )
        )

        log(
            "🌐 Klone aktuelles Streamboard-Repository..."
        )

        try:

            run_git(
                [
                    "clone",
                    "-c",
                    "http.sslVerify=false",
                    stream_url,
                    "."
                ],
                cwd=temp_dir,
                timeout=1200
            )

        except Exception as exc:

            raise RuntimeError(
                "Streamboard-Repository konnte "
                "nicht geklont werden!\n\n"
                f"URL:\n{stream_url}\n\n"
                f"Arbeitsverzeichnis:\n{temp_dir}\n\n"
                f"Fehler:\n{exc}"
            ) from exc

        log(
            "✓ Streamboard erfolgreich geklont.",
            "success"
        )

        # ========================================================
        # STREAMBOARD HEAD
        # ========================================================

        update_progress(
            35,
            tr(
                "patch_emu_git_head",
                "📌 Aktuellen Streamboard-Stand ermitteln...",
                "📌 Determining current Streamboard revision..."
            )
        )

        stream_head = git_output(
            [
                "rev-parse",
                "HEAD"
            ],
            cwd=temp_dir
        )

        if not stream_head:

            raise RuntimeError(
                "Streamboard-HEAD konnte nicht "
                "ermittelt werden."
            )

        log(
            f"✓ Streamboard HEAD: {stream_head}",
            "success"
        )

        # ========================================================
        # PATCH PRÜFEN
        # ========================================================

        update_progress(
            45,
            tr(
                "patch_emu_git_check_patch",
                "🔍 Patch wird geprüft...",
                "🔍 Checking patch..."
            )
        )

        try:

            run_git(
                [
                    "apply",
                    "--check",
                    "--binary",
                    patch_file
                ],
                cwd=temp_dir
            )

        except Exception as exc:

            raise RuntimeError(
                "Der Patch passt NICHT auf den "
                "aktuellen Streamboard-Stand.\n\n"
                f"Streamboard HEAD:\n"
                f"{stream_head}\n\n"
                f"Patch:\n"
                f"{patch_file}\n\n"
                f"Git-Fehler:\n{exc}"
            ) from exc

        log(
            "✓ Patch-Prüfung erfolgreich.",
            "success"
        )

        # ========================================================
        # PATCH ANWENDEN
        # ========================================================

        update_progress(
            58,
            tr(
                "patch_emu_git_apply",
                "🔧 OSCam-Emu-Patch wird angewendet...",
                "🔧 Applying OSCam-Emu patch..."
            )
        )

        try:

            run_git(
                [
                    "apply",
                    "--binary",
                    "--whitespace=fix",
                    patch_file
                ],
                cwd=temp_dir
            )

        except Exception as exc:

            raise RuntimeError(
                "OSCam-Emu Patch konnte nicht "
                "angewendet werden.\n\n"
                f"{exc}"
            ) from exc

        log(
            "✓ OSCam-Emu Patch erfolgreich angewendet.",
            "success"
        )

        # ========================================================
        # STATUS
        # ========================================================

        update_progress(
            68,
            tr(
                "patch_emu_git_status",
                "📋 Änderungen werden ermittelt...",
                "📋 Checking changes..."
            )
        )

        status = git_output(
            [
                "status",
                "--short"
            ],
            cwd=temp_dir
        )

        if status:

            log(
                "Geänderte Dateien:"
            )

            for line in status.splitlines():

                log(
                    f"  {line}"
                )

        else:

            log(
                "⚠️ Patch erzeugte keine Änderungen.",
                "warning"
            )

        # ========================================================
        # GIT CONFIG
        # ========================================================

        update_progress(
            75,
            tr(
                "patch_emu_git_config",
                "👤 Git wird konfiguriert...",
                "👤 Configuring Git..."
            )
        )

        cfg_func = globals().get(
            "load_github_config"
        )

        cfg = {}

        if callable(
            cfg_func
        ):

            try:

                cfg = cfg_func() or {}

            except Exception:
                cfg = {}

        user_name = (
            cfg.get(
                "user_name"
            )
            or "speedy005"
        )

        user_email = (
            cfg.get(
                "user_email"
            )
            or "patch@oscam.local"
        )

        run_git(
            [
                "config",
                "user.name",
                user_name
            ],
            cwd=temp_dir
        )

        run_git(
            [
                "config",
                "user.email",
                user_email
            ],
            cwd=temp_dir
        )

        # ========================================================
        # COMMIT
        # ========================================================

        update_progress(
            82,
            tr(
                "patch_emu_git_commit",
                "💾 Patch wird committed...",
                "💾 Committing patch..."
            )
        )

        run_git(
            [
                "add",
                "-A"
            ],
            cwd=temp_dir
        )

        commit_message = (
            "Sync OSCam-Emu patch"
        )

        header_func = globals().get(
            "get_patch_header"
        )

        if callable(
            header_func
        ):

            try:

                header = header_func()

                if header:

                    first_line = (
                        str(header)
                        .splitlines()[0]
                        .strip()
                    )

                    if first_line:
                        commit_message = first_line

            except Exception:
                pass

        run_git(
            [
                "commit",
                "-m",
                commit_message,
                "--allow-empty"
            ],
            cwd=temp_dir
        )

        revision = git_output(
            [
                "rev-parse",
                "--short",
                "HEAD"
            ],
            cwd=temp_dir
        )

        log(
            f"✓ Commit: {revision}",
            "success"
        )

        # ========================================================
        # FERTIGES REPOSITORY ÜBERNEHMEN
        # ========================================================

        update_progress(
            88,
            tr(
                "patch_emu_git_replace",
                "📦 Fertiges Repository wird übernommen...",
                "📦 Installing patched repository..."
            )
        )

        if os.path.exists(
            patch_dir
        ):

            backup_dir = (
                patch_dir
                + ".old"
            )

            if os.path.exists(
                backup_dir
            ):

                safe_remove_directory(
                    backup_dir
                )

            log(
                "📦 Altes Repository wird gesichert..."
            )

            try:

                os.replace(
                    patch_dir,
                    backup_dir
                )

            except Exception as exc:

                raise RuntimeError(
                    "Das vorhandene "
                    "PATCH_EMU_GIT_DIR konnte nicht "
                    "gesichert werden.\n\n"
                    f"{patch_dir}\n\n"
                    f"Fehler:\n{exc}"
                ) from exc

        # ========================================================
        # TEMP -> ZIEL
        # ========================================================

        try:

            os.replace(
                temp_dir,
                patch_dir
            )

            temp_dir = None

        except Exception:

            log(
                "⚠️ Direkte Übernahme nicht möglich – "
                "verwende Kopierverfahren.",
                "warning"
            )

            try:

                shutil.copytree(
                    temp_dir,
                    patch_dir,
                    dirs_exist_ok=True
                )

                safe_remove_directory(
                    temp_dir
                )

                temp_dir = None

            except Exception as copy_exc:

                raise RuntimeError(
                    "Das fertige Patch-Repository konnte "
                    "nicht nach PATCH_EMU_GIT_DIR "
                    "übernommen werden.\n\n"
                    f"Quelle:\n{temp_dir}\n\n"
                    f"Ziel:\n{patch_dir}\n\n"
                    f"Fehler:\n{copy_exc}"
                ) from copy_exc

        # ========================================================
        # ALTES BACKUP LÖSCHEN
        # ========================================================

        if backup_dir and os.path.exists(
            backup_dir
        ):

            try:

                safe_remove_directory(
                    backup_dir
                )

                backup_dir = None

            except Exception as exc:

                log(
                    "⚠️ Altes Repository konnte noch "
                    "nicht entfernt werden:\n"
                    f"{exc}",
                    "warning"
                )

        # ========================================================
        # ZIEL VALIDIEREN
        # ========================================================

        update_progress(
            94,
            tr(
                "patch_emu_git_validate",
                "🔎 Repository wird geprüft...",
                "🔎 Validating repository..."
            )
        )

        if not os.path.isdir(
            patch_dir
        ):

            raise RuntimeError(
                "PATCH_EMU_GIT_DIR wurde nicht erstellt:\n"
                f"{patch_dir}"
            )

        if not os.path.isdir(
            os.path.join(
                patch_dir,
                ".git"
            )
        ):

            raise RuntimeError(
                "Das fertige Repository besitzt "
                "kein .git-Verzeichnis:\n"
                f"{patch_dir}"
            )

        final_head = git_output(
            [
                "rev-parse",
                "HEAD"
            ],
            cwd=patch_dir
        )

        if not final_head:

            raise RuntimeError(
                "Finaler Repository-HEAD konnte "
                "nicht ermittelt werden."
            )

        log(
            f"✓ Fertiges Repository HEAD: {final_head}",
            "success"
        )

        # ========================================================
        # GUI PFAD
        # ========================================================

        if gui:

            try:
                gui.patch_repo_dir = patch_dir
            except Exception:
                pass

        # ========================================================
        # ERFOLG
        # ========================================================

        success_text = tr(
            "patch_emu_git_success",
            "✅ OSCam-Emu-Git erfolgreich gepatcht",
            "✅ OSCam-Emu Git patched successfully"
        )

        # --------------------------------------------------------
        # GANZ WICHTIG:
        # 100 % + Abschlussmeldung in EINEM Aufruf.
        #
        # Danach wird kein weiterer Fortschrittstext mehr gesetzt.
        # --------------------------------------------------------

        update_progress(
            100,
            success_text
        )

        log(
            ""
        )

        log(
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            "success"
        )

        log(
            success_text,
            "success"
        )

        log(
            f"📂 Upload-Ordner:\n{patch_dir}",
            "success"
        )

        log(
            f"📌 Streamboard HEAD:\n{stream_head}",
            "success"
        )

        log(
            f"📌 Patch-Commit:\n{revision}",
            "success"
        )

        log(
            "🚀 Der Ordner kann jetzt mit "
            "github_upload_oscam_emu_folder() "
            "zu GitHub hochgeladen werden.",
            "success"
        )

        log(
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            "success"
        )

        play_sound(
            True
        )

        return True

    # ============================================================
    # FEHLER
    # ============================================================

    except Exception as exc:

        error_text = str(
            exc
        )

        log(
            ""
        )

        log(
            "════════════════════════════════════════════",
            "error"
        )

        log(
            "❌ OSCAM-EMU-GIT PATCH FEHLGESCHLAGEN",
            "error"
        )

        log(
            "════════════════════════════════════════════",
            "error"
        )

        log(
            error_text,
            "error"
        )

        failed_text = tr(
            "patch_emu_git_failed",
            "❌ OSCam-Emu-Git konnte nicht gepatcht werden",
            "❌ OSCam-Emu Git patching failed"
        )

        update_progress(
            100,
            failed_text,
            error=True
        )

        # --------------------------------------------------------
        # TEMPORÄRES REPOSITORY AUFRÄUMEN
        # --------------------------------------------------------

        if temp_dir and os.path.exists(
            temp_dir
        ):

            try:

                safe_remove_directory(
                    temp_dir
                )

            except Exception as cleanup_exc:

                log(
                    "⚠️ Temporäres Repository "
                    "konnte nicht entfernt werden:\n"
                    f"{cleanup_exc}",
                    "warning"
                )

        play_sound(
            False
        )

        return False
