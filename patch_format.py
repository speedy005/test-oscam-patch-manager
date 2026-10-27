"""OSCam Patch Manager module fragment: Patch serialization, headers, and GitHub config."""

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

from git_manager import _git_command
from paths import GITHUB_CONF_FILE, PATCH_EMU_GIT_DIR, PATCH_MODIFIER, STREAMREPO
from translations import TEXTS
from workers import safe_play

def get_streamboard_version(*args, **kwargs):
    """Lazy bridge to patch_operations to avoid an import cycle."""
    from patch_operations import get_streamboard_version as _impl
    return _impl(*args, **kwargs)

def _extract_patch_diff(patch_path):
    """Entfernt den UI-Metadaten-Header und liefert ausschließlich den Unified Diff."""
    from pathlib import Path
    text = Path(patch_path).read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines(keepends=True)
    for i, line in enumerate(lines):
        if line.startswith("diff --git "):
            return "".join(lines[i:])
    return ""

def _write_patch_file(path, header, diff):
    from pathlib import Path
    target=Path(path).resolve(); target.parent.mkdir(parents=True,exist_ok=True)
    target.write_text(header.rstrip()+"\n"+diff.rstrip()+"\n",encoding="utf-8",newline="\n")
    try:
        import os; os.chmod(target,0o666)
    except Exception: pass
    return str(target)

def get_patch_header(
    streamboard_repo=None,
    emu_repo=None,
    lang="de",
    modifier=None,
    streamboard_commit=None
):
    """
    Erzeugt den Patch-Header aus dem tatsächlich verwendeten
    Streamboard- und OSCam-Emu-Git.

    Format:

        patch version: 2.26.07-11966-802 (5905109e)
        patch date: 2026-08-01 06:43:54 UTC (+00:00)
        patch modified by speedy005 (01/08/2026)

    Die Versionsinformationen werden aus dem Streamboard-
    Repository und dem OSCam-Emu-Repository gelesen.
    """

    import os
    import re
    from datetime import datetime, timezone

    # ============================================================
    # REPOSITORIES
    # ============================================================

    if streamboard_repo is None:
        streamboard_repo = STREAMREPO

    if emu_repo is None:
        emu_repo = PATCH_EMU_GIT_DIR

    streamboard_repo = os.path.abspath(
        streamboard_repo
    )

    emu_repo = os.path.abspath(
        emu_repo
    )

    # ============================================================
    # SPRACHE / MODIFIER
    # ============================================================

    active_modifier = (
        modifier
        or PATCH_MODIFIER
        or "unknown"
    )

    lang_dict = TEXTS.get(
        lang,
        TEXTS.get("en", {})
    )

    # ============================================================
    # EXAKTE STREAMBOARD-REVISION
    #
    # WICHTIG:
    # Der Header darf NICHT vom zufälligen lokalen HEAD abhängen.
    # Die Version und der Commit werden aus genau der Revision gelesen,
    # die create_patch() als Patch-Basis verwendet.
    # ============================================================

    exact_stream_commit = str(
        streamboard_commit or ""
    ).strip().lower()

    if exact_stream_commit:

        if not re.fullmatch(
            r"[0-9a-f]{40}",
            exact_stream_commit
        ):
            raise RuntimeError(
                "Ungültiger Streamboard Commit für den Patch-Header:\n"
                + exact_stream_commit
            )

    version = "unknown"
    build = "unknown"

    # globals.h direkt aus dem exakten Git-Commit lesen.
    if exact_stream_commit:

        try:

            result = _git_command(
                [
                    "show",
                    exact_stream_commit + ":globals.h"
                ],
                cwd=streamboard_repo
            )

            content = (
                result.stdout or ""
            )

            match = re.search(
                r'#define\s+CS_VERSION\s+"([^"]+)"',
                content
            )

            if match:

                cs_version = match.group(1).strip()
                parts = cs_version.split("-")

                if parts:
                    version = parts[0].strip()

                if len(parts) > 1:
                    build = parts[1].strip()

        except Exception as exc:
            log(
                "[PATCH HEADER] Konnte globals.h aus dem exakten "
                f"Streamboard Commit nicht lesen: {exc}"
            )

    # Fallback nur dann, wenn kein exakter Commit übergeben wurde.
    if (
        not exact_stream_commit
        and os.path.isfile(os.path.join(streamboard_repo, "globals.h"))
    ):

        try:

            with open(
                os.path.join(streamboard_repo, "globals.h"),
                "r",
                encoding="utf-8",
                errors="replace"
            ) as handle:
                content = handle.read()

            match = re.search(
                r'#define\s+CS_VERSION\s+"([^"]+)"',
                content
            )

            if match:

                cs_version = match.group(1).strip()
                parts = cs_version.split("-")

                if parts:
                    version = parts[0].strip()

                if len(parts) > 1:
                    build = parts[1].strip()

        except Exception:
            pass

    # ============================================================
    # FALLBACK: VORHANDENE STREAMBOARD-VERSIONSFUNKTION
    # ============================================================

    if (
        version == "unknown"
        or build == "unknown"
    ) and not exact_stream_commit:

        try:

            detected_version = get_streamboard_version()

            if detected_version:

                detected_version = str(
                    detected_version
                ).strip()

                if detected_version.lower() != "unbekannt":

                    parts = detected_version.split("-")

                    if parts:
                        version = parts[0].strip()

                    if len(parts) > 1:
                        build = parts[1].strip()

        except Exception:
            pass

    # ============================================================
    # EMU-VERSION
    # ============================================================

    emu_rev = "unknown"

    emu_header = os.path.join(
        emu_repo,
        "module-emulator-osemu.h"
    )

    if os.path.isfile(
        emu_header
    ):

        try:

            with open(
                emu_header,
                "r",
                encoding="utf-8",
                errors="replace"
            ) as handle:

                content = handle.read()

            match = re.search(
                r'^\s*#define\s+EMU_VERSION\s+(.+?)\s*$',
                content,
                re.MULTILINE
            )

            if match:

                emu_rev = (
                    match.group(1)
                    .strip()
                    .strip('"')
                )

        except Exception:
            pass

    # ============================================================
    # PATCH-VERSION COMMIT
    #
    # Der Commit in "patch version: ... (XXXXXXXX)" ist die
    # Streamboard-Basisrevision, NICHT der OSCam-Emu Zielcommit.
    # ============================================================

    commit = "unknown"

    if exact_stream_commit:

        commit = exact_stream_commit[:8]

    else:

        try:

            result = _git_command(
                [
                    "rev-parse",
                    "--short=8",
                    "HEAD"
                ],
                cwd=streamboard_repo
            )

            commit = (
                result.stdout or ""
            ).strip()

            if not commit:
                commit = "unknown"

        except Exception:
            pass

    # ============================================================
    # STREAMBOARD-COMMIT-DATUM
    # ============================================================

    utc_date = "unknown"

    try:

        date_ref = exact_stream_commit or "HEAD"

        result = _git_command(
            [
                "show",
                "-s",
                "--format=%cI",
                date_ref
            ],
            cwd=streamboard_repo
        )

        commit_date = (
            result.stdout or ""
        ).strip()

        if commit_date:

            dt = datetime.fromisoformat(
                commit_date.replace(
                    "Z",
                    "+00:00"
                )
            )

            dt_utc = dt.astimezone(
                timezone.utc
            )

            utc_date = dt_utc.strftime(
                "%Y-%m-%d %H:%M:%S UTC (+00:00)"
            )

    except Exception:

        utc_date = datetime.now(
            timezone.utc
        ).strftime(
            "%Y-%m-%d %H:%M:%S UTC (+00:00)"
        )

    # ============================================================
    # MODIFIER-DATUM
    # ============================================================

    mod_date = datetime.now().strftime(
        "%d/%m/%Y"
    )

    # ============================================================
    # HEADER
    # ============================================================

    # Der Header wird absichtlich als Kommentar geschrieben.
    # Damit bleibt die Datei sowohl für Menschen lesbar als auch
    # direkt mit `git apply`/`patch` verarbeitbar.
    return (
        f"# {lang_dict.get('patch_version_header', 'patch version')}: "
        f"{version}-{build}-{emu_rev} ({commit})\n"
        f"# {lang_dict.get('patch_date', 'patch date')}: "
        f"{utc_date}\n"
        f"# {lang_dict.get('patch_modified_by', 'patch modified by')} "
        f"{active_modifier} ({mod_date})"
    )

def load_github_config():
    if os.path.exists(GITHUB_CONF_FILE):
        try:
            cfg = json.load(open(GITHUB_CONF_FILE))
            cfg.setdefault("repo_url", "")
            cfg.setdefault("branch", "master")
            cfg.setdefault("emu_repo_url", "")
            cfg.setdefault("emu_branch", "master")
            cfg.setdefault("username", "")
            cfg.setdefault("token", "")
            cfg.setdefault("user_name", "speedy005")
            cfg.setdefault("user_email", "patch@oscam.local")
            return cfg
        except:
            return {}
    return {}


def save_github_config(cfg):
    try:
        json.dump(cfg, open(GITHUB_CONF_FILE, "w"))
    except:
        pass

# ===================== GITHUB UPLOAD =====================
def _github_upload(
    dir_path,
    repo_url,
    gui_instance=None,
    info_widget=None,
    branch="master",
    commit_msg="Apply OSCam Emu Patch",
):
    """Upload des Patch-Ordners zu GitHub mit Neon-Regenbogen-ProgressBar und schwarzer Schrift."""

    from PyQt6.QtWidgets import QApplication, QTextEdit
    import os, shutil

    # --- Final Label verstecken ---
    if gui_instance and hasattr(gui_instance, "hide_final_label"):
        gui_instance.hide_final_label()

    # --- Referenzen ---
    widget = info_widget or getattr(gui_instance, "info_text", None)
    pbar = getattr(gui_instance, "progress_bar", None)
    lang = str(getattr(gui_instance, "LANG", "de")).lower()[:2]
    is_de = lang == "de"

    # --- Styles ---
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
            background-color: {chunk_color};
            border-radius: 6px;
        }}
    """
    
    RAINBOW_GRADIENT = (
        "qlineargradient(spread:pad, x1:0, y1:0, x2:1, y2:0, "
        "stop:0 #FF00FF, stop:0.5 #00FFFF, stop:1 #39FF14)"
    )

    # --- Hilfsfunktionen ---
    def update_p(val, text=None, is_err=False):
        if pbar:
            chunk = "qlineargradient(spread:pad, x1:0, y1:0, x2:1, y2:0, stop:0 #800, stop:1 #F00)" if is_err else RAINBOW_GRADIENT
            t_color = "#FF0000" if is_err else "black"
            
            pbar.setStyleSheet(STYLE_BASE.format(text_color=t_color, chunk_color=chunk))
            pbar.setValue(val)
            pbar.setFormat(text if text else f"{val}%")
            pbar.show()
        QApplication.processEvents()

    def log(txt, level="info"):
        if gui_instance and hasattr(gui_instance, "append_info"):
            gui_instance.append_info(widget, txt, level)
        elif isinstance(widget, QTextEdit):
            color = {"success": "#39FF14", "warning": "orange", "error": "red"}.get(level, "gray")
            widget.append(f'<span style="color:{color}">{txt}</span>')
        QApplication.processEvents()

    def play_sound(sound_type="success"):
        sound = "complete.oga" if sound_type == "success" else "dialog-error.oga"
        if "safe_play" in globals():
            safe_play(sound)

    # --- Ablauf ---
    update_p(10, "🔹 GitHub Upload..." if is_de else "🔹 GitHub Upload...")
    log("GitHub Upload gestartet..." if is_de else "GitHub Upload started...")

    # Lade GitHub Config (Annahme: global verfügbar)
    cfg = globals().get("load_github_config", lambda: {})()
    update_p(30, "Config prüfen..." if is_de else "Checking config...")

    # Git Init Vorbereitung
    git_dir = os.path.join(dir_path, ".git")
    if os.path.exists(git_dir):
        shutil.rmtree(git_dir, ignore_errors=True)
    update_p(50, "Git Init..." if is_de else "Git Init...")

    # Auth Token URL
    token_url = repo_url.replace(
        "https://", f"https://{cfg.get('username', '')}:{cfg.get('token', '')}@"
    )

    # Bash Helper (Annahme: run_bash global verfügbar)
    def run(cmd):
        rb = globals().get("run_bash")
        if rb:
            return rb(cmd, cwd=dir_path, logger=lambda t, l="info": log(t, l))
        return -1

    run("git init")
    update_p(60, "Remote..." if is_de else "Remote...")
    run(f"git remote add origin {token_url}")
    run(f"git checkout -b {branch}")

    # Commit
    update_p(75, "Commit..." if is_de else "Commit...")
    run(f'git config user.name "{cfg.get("user_name", "speedy005")}"')
    run(f'git config user.email "{cfg.get("user_email", "patch@oscam.local")}"')
    run("git add -A")
    run(f'git commit -m "{commit_msg}" --allow-empty')

    # Push
    update_p(90, "Push..." if is_de else "Pushing...")
    code = run(f"git push origin {branch} --force")

    # Finale
    if code == 0:
        msg = "✅ Upload erfolgreich!" if is_de else "✅ Upload Success!"
        update_p(100, msg)
        log(msg, "success")
        play_sound("success")
    else:
        msg = "❌ Upload fehlgeschlagen!" if is_de else "❌ Upload Failed!"
        update_p(100, msg, is_err=True)
        log(msg, "error")
        play_sound("error")

# ===================== GITHUB UPLOAD PATCH FILE =====================
from PyQt6.QtWidgets import QTextEdit, QApplication
from PyQt6.QtGui import QTextCursor
import shutil, os

def run_bash(cmd, cwd=None, info_widget=None, lang="DE", logger=None):
    """
    Führt einen Bash-Befehl aus und schreibt jede Zeile live ins Info-Widget.
    Optimiert für Sprachunterstützung und ohne Terminal-Leaks.
    """
    from PyQt6.QtWidgets import QTextEdit, QApplication
    from PyQt6.QtGui import QTextCursor
    import os, subprocess

    # 1. Widget-Suche (behalten)
    if not isinstance(info_widget, QTextEdit):
        active_win = QApplication.activeWindow()
        if active_win and hasattr(active_win, "info_text"):
            info_widget = active_win.info_text

    # Sprach-Dictionary laden
    current_lang = lang.lower()
    t = TEXTS.get(current_lang, TEXTS.get("en", {}))

    def log(text, level="info"):
        colors = {
            "success": "#27ae60",
            "warning": "#f39c12",
            "error": "#e74c3c",
            "info": "#7f8c8d",
        }
        color = colors.get(level, "#7f8c8d")

        # Falls der Text ein Key ist, übersetzen, sonst Original behalten
        translated = t.get(text, text)

        if isinstance(info_widget, QTextEdit):
            info_widget.append(
                f'<span style="color:{color}; font-family: monospace;">{translated}</span>'
            )
            info_widget.moveCursor(QTextCursor.MoveOperation.End)
            QApplication.processEvents()
        else:
            # Falls kein GUI-Widget da ist (Notfall), schreibe ins Terminal
            print(f"[{level.upper()}] {translated}")

    # 2. Arbeitsverzeichnis validieren
    if cwd and not os.path.exists(cwd):
        try:
            os.makedirs(cwd, exist_ok=True)
        except Exception as e:
            log(f"Fehler: Verzeichnis konnte nicht erstellt werden {cwd}: {e}", "error")
            return -1

    # --- ÜBERSETZTER START-LOG (ERSETZT DAS ALTE 'Executing:...') ---
    exec_msg = t.get("executing_cmd", "Führe Befehl aus:")
    log(f"🚀 {exec_msg} {cmd}", "warning")

    try:
        env = os.environ.copy()
        env["GIT_TERMINAL_PROMPT"] = "0"

        # `run_bash()` wird nur für Git-Kommandos verwendet. Deshalb
        # niemals eine Shell dazwischenschalten; das ist auf Windows
        # zuverlässiger und verhindert Quoting-Probleme.
        if isinstance(cmd, str):
            import shlex
            command = shlex.split(cmd, posix=(os.name != "nt"))
        else:
            command = list(cmd)

        process = subprocess.Popen(
            command,
            shell=False,
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            encoding="utf-8",
            errors="replace",
            env=env,
        )

        # 3. Live-Ausgabe
        if process.stdout:
            for line in iter(process.stdout.readline, ""):
                line = line.strip()
                if line:
                    log(line, "info")
            process.stdout.close()

        return_code = process.wait()

        if return_code != 0:
            # Fehlermeldung übersetzen
            err_msg = t.get("cmd_failed", "Befehl fehlgeschlagen mit Code")
            log(f"❌ {err_msg} {return_code}", "error")

        return return_code

    except Exception as e:
        log(f"run_bash error: {e}", "error")
        return -1

