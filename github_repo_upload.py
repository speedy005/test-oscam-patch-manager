"""OSCam Patch Manager module fragment: Repository upload and remote integration."""

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

from translations import TEXTS
from workers import safe_play
from patch_format import load_github_config
from paths import PATCH_EMU_GIT_DIR

# ===================== GITHUB UPLOAD OSCAM-EMU FOLDER =====================
def github_upload_oscam_emu_folder(
    gui_instance=None,
    info_widget=None,
    progress_callback=None
):
    """
    Lädt den kompletten lokalen OSCam-EMU-Git-Ordner nach:

        https://github.com/speedy005/Oscam-emu

    Sicherheit:
    - GitHub-Token wird NICHT in die Remote-URL geschrieben.
    - Token wird NICHT in .git/config gespeichert.
    - Authentifizierung erfolgt temporär über GIT_ASKPASS.
    - GIT_TERMINAL_PROMPT=0 verhindert interaktive Passwortabfragen.
    - Token wird nicht absichtlich geloggt.
    - Fehlerausgaben werden zusätzlich maskiert.
    - Temporäre ASKPASS-Dateien werden nach dem Vorgang gelöscht.
    - Push erfolgt mit --force-with-lease statt --force.

    Eigenschaften:
    - DE/EN Support
    - PyQt6 ProgressBar
    - Neon-Regenbogen-ProgressBar
    - kompletter Ordner wird synchronisiert
    - keine leeren Commits
    - keine unnötigen Pushes
    """

    from PyQt6.QtWidgets import QTextEdit, QApplication

    import os
    import sys
    import subprocess
    import tempfile
    import shutil
    import stat

    # ============================================================
    # FINAL LABEL VERSTECKEN
    # ============================================================
    if gui_instance and hasattr(gui_instance, "hide_final_label"):
        try:
            gui_instance.hide_final_label()
        except Exception:
            pass

    # ============================================================
    # REFERENZEN / SPRACHE
    # ============================================================
    widget = info_widget or getattr(
        gui_instance,
        "info_text",
        None
    )

    pbar = getattr(
        gui_instance,
        "progress_bar",
        None
    )

    lang = str(
        getattr(gui_instance, "LANG", "de")
    ).lower()[:2]

    is_de = lang == "de"

    # ============================================================
    # GITHUB ZIEL
    # ============================================================
    TARGET_REPO_URL = (
        "https://github.com/speedy005/Oscam-emu.git"
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
            background-color: {chunk_color};
            border-radius: 6px;
        }}
    """

    RAINBOW_GRADIENT = (
        "qlineargradient("
        "spread:pad, "
        "x1:0, y1:0, x2:1, y2:0, "
        "stop:0 #FF00FF, "
        "stop:0.5 #00FFFF, "
        "stop:1 #39FF14)"
    )

    last_progress = 0

    # ============================================================
    # ÜBERSETZUNG
    # ============================================================
    def tr(key, de_text, en_text):

        texts = globals().get(
            "TEXTS",
            {}
        )

        try:

            lang_dict = texts.get(
                lang,
                {}
            )

            value = lang_dict.get(
                key
            )

            if value:
                return value

        except Exception:
            pass

        return (
            de_text
            if is_de
            else en_text
        )

    # ============================================================
    # PROGRESSBAR
    # ============================================================
    def update_p(
        val,
        txt=None,
        is_err=False
    ):

        nonlocal last_progress

        last_progress = val

        try:

            if pbar:

                if is_err:

                    chunk = (
                        "qlineargradient("
                        "spread:pad, "
                        "x1:0, y1:0, x2:1, y2:0, "
                        "stop:0 #800000, "
                        "stop:1 #FF0000)"
                    )

                    text_color = "#FF0000"

                else:

                    chunk = RAINBOW_GRADIENT
                    text_color = "black"

                pbar.setStyleSheet(
                    STYLE_BASE.format(
                        text_color=text_color,
                        chunk_color=chunk
                    )
                )

                pbar.setValue(
                    int(val)
                )

                if txt:
                    pbar.setFormat(
                        txt
                    )
                else:
                    pbar.setFormat(
                        f"{val}%"
                    )

                pbar.show()

        except Exception:
            pass

        if progress_callback:

            try:
                progress_callback(
                    val
                )
            except Exception:
                pass

        try:
            QApplication.processEvents()
        except Exception:
            pass

    # ============================================================
    # LOGGING
    # ============================================================
    def log(
        text,
        level="info"
    ):

        try:

            if gui_instance and hasattr(
                gui_instance,
                "append_info"
            ):

                gui_instance.append_info(
                    widget,
                    text,
                    level
                )

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

                widget.append(
                    f'<span style="color:{color}">'
                    f'{text}'
                    f'</span>'
                )

        except Exception:
            pass

        try:
            QApplication.processEvents()
        except Exception:
            pass

    # ============================================================
    # SOUND
    # ============================================================
    def play_sound(
        success=True
    ):

        try:

            if "safe_play" in globals():

                safe_play(
                    "complete.oga"
                    if success
                    else "dialog-error.oga"
                )

        except Exception:
            pass

    # ============================================================
    # CREDENTIALS MASKIEREN
    # ============================================================
    def mask_sensitive(
        text,
        username="",
        token=""
    ):

        if not text:
            return ""

        safe = str(text)

        if token:
            safe = safe.replace(
                token,
                "***TOKEN***"
            )

        if username and token:

            safe = safe.replace(
                f"{username}:{token}",
                f"{username}:***TOKEN***"
            )

        return safe

    # ============================================================
    # GIT COMMAND HELPER
    # ============================================================
    def run_git(
        args,
        cwd,
        env=None,
        check=False
    ):

        return subprocess.run(
            ["git"] + list(args),
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            check=check
        )

    # ============================================================
    # ASKPASS VARIABLEN
    # ============================================================
    askpass_dir = None
    askpass_script = None
    askpass_launcher = None

    # ============================================================
    # ASKPASS ERSTELLEN
    # ============================================================
    def create_askpass():

        nonlocal askpass_dir
        nonlocal askpass_script
        nonlocal askpass_launcher

        askpass_dir = tempfile.mkdtemp(
            prefix="oscam_git_"
        )

        # --------------------------------------------------------
        # Python ASKPASS
        # --------------------------------------------------------
        askpass_script = os.path.join(
            askpass_dir,
            "askpass.py"
        )

        askpass_code = r'''import os
import sys

prompt = " ".join(sys.argv[1:]).lower()

if "username" in prompt or "user" in prompt:
    value = os.environ.get(
        "OSC_GITHUB_USERNAME",
        ""
    )
else:
    value = os.environ.get(
        "OSC_GITHUB_TOKEN",
        ""
    )

sys.stdout.write(value)
'''

        with open(
            askpass_script,
            "w",
            encoding="utf-8"
        ) as f:

            f.write(
                askpass_code
            )

        # --------------------------------------------------------
        # WINDOWS
        # --------------------------------------------------------
        if os.name == "nt":

            askpass_launcher = os.path.join(
                askpass_dir,
                "askpass.cmd"
            )

            launcher_code = (
                "@echo off\r\n"
                f'"{sys.executable}" '
                f'"{askpass_script}" %*\r\n'
            )

            with open(
                askpass_launcher,
                "w",
                encoding="utf-8"
            ) as f:

                f.write(
                    launcher_code
                )

        # --------------------------------------------------------
        # LINUX / MACOS
        # --------------------------------------------------------
        else:

            askpass_launcher = os.path.join(
                askpass_dir,
                "askpass.sh"
            )

            launcher_code = (
                "#!/bin/sh\n"
                f'exec "{sys.executable}" '
                f'"{askpass_script}" "$@"\n'
            )

            with open(
                askpass_launcher,
                "w",
                encoding="utf-8"
            ) as f:

                f.write(
                    launcher_code
                )

            os.chmod(
                askpass_launcher,
                stat.S_IRUSR
                | stat.S_IWUSR
                | stat.S_IXUSR
            )

        return askpass_launcher

    # ============================================================
    # ASKPASS AUFRÄUMEN
    # ============================================================
    def cleanup_askpass():

        nonlocal askpass_dir
        nonlocal askpass_script
        nonlocal askpass_launcher

        try:

            if (
                askpass_dir
                and os.path.isdir(askpass_dir)
            ):

                shutil.rmtree(
                    askpass_dir,
                    ignore_errors=True
                )

        except Exception:
            pass

        askpass_dir = None
        askpass_script = None
        askpass_launcher = None

    # ============================================================
    # START
    # ============================================================
    update_p(
        5,
        tr(
            "github_upload_start",
            "⏳ GitHub-Upload wird gestartet...",
            "⏳ Starting GitHub upload..."
        )
    )

    # ============================================================
    # CONFIG LADEN
    # ============================================================
    cfg_func = globals().get(
        "load_github_config"
    )

    if not cfg_func:

        update_p(
            last_progress,
            tr(
                "github_upload_failed",
                "❌ GitHub-Konfiguration fehlt",
                "❌ GitHub configuration missing"
            ),
            is_err=True
        )

        log(
            "Konfigurations-Ladefunktion fehlt!",
            "error"
        )

        play_sound(False)
        return

    try:

        cfg = cfg_func()

        if not isinstance(
            cfg,
            dict
        ):

            raise ValueError(
                "GitHub-Konfiguration ist ungültig."
            )

    except Exception as exc:

        update_p(
            last_progress,
            tr(
                "github_upload_failed",
                "❌ GitHub-Konfiguration konnte nicht geladen werden",
                "❌ GitHub configuration could not be loaded"
            ),
            is_err=True
        )

        log(
            "GitHub-Konfiguration konnte nicht geladen werden: "
            + mask_sensitive(
                str(exc)
            ),
            "error"
        )

        play_sound(False)
        return

    # ============================================================
    # CREDENTIALS
    # ============================================================
    username = str(
        cfg.get("username") or ""
    ).strip()

    token = str(
        cfg.get("token") or ""
    ).strip()

    user_name = str(
        cfg.get("user_name") or ""
    ).strip()

    user_email = str(
        cfg.get("user_email") or ""
    ).strip()

    branch = str(
        cfg.get(
            "emu_branch",
            "master"
        ) or "master"
    ).strip()

    # ============================================================
    # CREDENTIALS PRÜFEN
    # ============================================================
    if not username or not token:

        update_p(
            last_progress,
            tr(
                "github_upload_auth_error",
                "❌ GitHub-Zugangsdaten fehlen",
                "❌ GitHub credentials missing"
            ),
            is_err=True
        )

        log(
            "GitHub Username oder Token fehlt in der Config.",
            "error"
        )

        play_sound(False)
        return

    # ============================================================
    # GIT USER PRÜFEN
    # ============================================================
    if not user_name or not user_email:

        update_p(
            last_progress,
            tr(
                "github_upload_git_error",
                "❌ Git-Konfiguration fehlt",
                "❌ Git configuration missing"
            ),
            is_err=True
        )

        log(
            "Git user.name oder user.email fehlt in der Config.",
            "error"
        )

        play_sound(False)
        return

    # ============================================================
    # BRANCH PRÜFEN
    # ============================================================
    if (
        not branch
        or any(
            char in branch
            for char in (
                " ",
                "\n",
                "\r",
                "\t"
            )
        )
    ):

        update_p(
            last_progress,
            (
                "❌ Ungültiger Git-Branch"
                if is_de
                else
                "❌ Invalid Git branch"
            ),
            is_err=True
        )

        log(
            "Der konfigurierte Branch enthält ungültige Zeichen.",
            "error"
        )

        play_sound(False)
        return

    # ============================================================
    # LOKALES REPOSITORY
    # ============================================================
    target_dir = globals().get(
        "PATCH_EMU_GIT_DIR"
    )

    if not target_dir:

        update_p(
            last_progress,
            tr(
                "github_upload_folder_error",
                "❌ OSCam-Emu-Git-Ordner fehlt",
                "❌ OSCam-Emu-Git folder missing"
            ),
            is_err=True
        )

        log(
            "PATCH_EMU_GIT_DIR ist nicht definiert.",
            "error"
        )

        play_sound(False)
        return

    target_dir = os.path.abspath(
        os.path.expanduser(
            str(target_dir)
        )
    )

    if not os.path.isdir(
        target_dir
    ):

        update_p(
            last_progress,
            tr(
                "github_upload_folder_error",
                "❌ OSCam-Emu-Git-Ordner fehlt",
                "❌ OSCam-Emu-Git folder missing"
            ),
            is_err=True
        )

        log(
            f"Zielordner existiert nicht:\n{target_dir}",
            "error"
        )

        play_sound(False)
        return

    # ============================================================
    # SICHERHEITS-LOG
    # ============================================================
    log(
        "GitHub-Ziel:"
    )

    log(
        TARGET_REPO_URL
    )

    log(
        f"Lokaler Ordner:\n{target_dir}"
    )

    log(
        f"Branch: {branch}"
    )

    # ============================================================
    # GIT SETUP
    # ============================================================
    update_p(
        20,
        tr(
            "github_upload_git_setup",
            "🔧 Git wird vorbereitet...",
            "🔧 Preparing Git..."
        )
    )

    git_dir = os.path.join(
        target_dir,
        ".git"
    )

    # ============================================================
    # AUTH ENVIRONMENT
    # ============================================================
    silent_env = os.environ.copy()

    silent_env[
        "OSC_GITHUB_USERNAME"
    ] = username

    silent_env[
        "OSC_GITHUB_TOKEN"
    ] = token

    silent_env[
        "GIT_TERMINAL_PROMPT"
    ] = "0"

    silent_env[
        "GIT_ASKPASS_REQUIRE"
    ] = "force"

    # Git kann diese Variable zusätzlich verwenden.
    silent_env[
        "GIT_USERNAME"
    ] = username

    # ============================================================
    # ASKPASS ERSTELLEN
    # ============================================================
    try:

        create_askpass()

        silent_env[
            "GIT_ASKPASS"
        ] = askpass_launcher

    except Exception as exc:

        update_p(
            last_progress,
            tr(
                "github_upload_failed",
                "❌ Sichere Git-Authentifizierung konnte nicht vorbereitet werden",
                "❌ Secure Git authentication could not be prepared"
            ),
            is_err=True
        )

        log(
            f"ASKPASS-Fehler: {mask_sensitive(str(exc), username, token)}",
            "error"
        )

        cleanup_askpass()
        play_sound(False)
        return

    # ============================================================
    # GIT OPERATIONEN
    # ============================================================
    try:

        # ========================================================
        # REPOSITORY INITIALISIEREN
        # ========================================================
        if not os.path.exists(
            git_dir
        ):

            log(
                "Git-Repository wird initialisiert...",
                "info"
            )

            run_git(
                [
                    "init"
                ],
                cwd=target_dir,
                env=silent_env,
                check=True
            )

            # ----------------------------------------------------
            # Remote OHNE TOKEN
            # ----------------------------------------------------
            run_git(
                [
                    "remote",
                    "add",
                    "origin",
                    TARGET_REPO_URL
                ],
                cwd=target_dir,
                env=silent_env,
                check=True
            )

            # ----------------------------------------------------
            # Branch erstellen
            # ----------------------------------------------------
            run_git(
                [
                    "checkout",
                    "-b",
                    branch
                ],
                cwd=target_dir,
                env=silent_env,
                check=True
            )

        # ========================================================
        # VORHANDENES REPOSITORY
        # ========================================================
        else:

            log(
                "Vorhandenes Git-Repository gefunden.",
                "info"
            )

            # ----------------------------------------------------
            # Prüfen, ob origin existiert
            # ----------------------------------------------------
            remote_check = run_git(
                [
                    "remote",
                    "get-url",
                    "origin"
                ],
                cwd=target_dir,
                env=silent_env
            )

            if remote_check.returncode == 0:

                current_remote = (
                    remote_check.stdout.strip()
                )

                # ------------------------------------------------
                # Sicherheitsprüfung:
                # Token darf niemals im Remote stehen.
                # ------------------------------------------------
                if token and token in current_remote:

                    log(
                        "⚠️ Unsicherer Token in Git-Remote erkannt. "
                        "Remote wird ersetzt.",
                        "warning"
                    )

                run_git(
                    [
                        "remote",
                        "set-url",
                        "origin",
                        TARGET_REPO_URL
                    ],
                    cwd=target_dir,
                    env=silent_env,
                    check=True
                )

            else:

                run_git(
                    [
                        "remote",
                        "add",
                        "origin",
                        TARGET_REPO_URL
                    ],
                    cwd=target_dir,
                    env=silent_env,
                    check=True
                )

            # ----------------------------------------------------
            # Prüfen, ob Branch existiert
            # ----------------------------------------------------
            branch_check = run_git(
                [
                    "branch",
                    "--list",
                    branch
                ],
                cwd=target_dir,
                env=silent_env
            )

            if branch_check.stdout.strip():

                run_git(
                    [
                        "checkout",
                        branch
                    ],
                    cwd=target_dir,
                    env=silent_env,
                    check=True
                )

            else:

                run_git(
                    [
                        "checkout",
                        "-b",
                        branch
                    ],
                    cwd=target_dir,
                    env=silent_env,
                    check=True
                )

        # ========================================================
        # GIT CONFIG
        # ========================================================
        update_p(
            35,
            tr(
                "github_upload_git_config",
                "📝 Git wird konfiguriert...",
                "📝 Configuring Git..."
            )
        )

        run_git(
            [
                "config",
                "user.name",
                user_name
            ],
            cwd=target_dir,
            env=silent_env,
            check=True
        )

        run_git(
            [
                "config",
                "user.email",
                user_email
            ],
            cwd=target_dir,
            env=silent_env,
            check=True
        )

        # ========================================================
        # REMOTE NOCHMALS SICHERHEITSPRÜFEN
        # ========================================================
        remote_verify = run_git(
            [
                "remote",
                "get-url",
                "origin"
            ],
            cwd=target_dir,
            env=silent_env
        )

        if remote_verify.returncode != 0:

            raise RuntimeError(
                "GitHub-Remote konnte nicht überprüft werden."
            )

        remote_url = (
            remote_verify.stdout.strip()
        )

        if token and token in remote_url:

            raise RuntimeError(
                "Sicherheitsfehler: GitHub-Token "
                "befindet sich in der Git-Remote-URL."
            )

        if username and token:

            credential_string = (
                f"{username}:{token}"
            )

            if credential_string in remote_url:

                raise RuntimeError(
                    "Sicherheitsfehler: "
                    "Credentials befinden sich in der "
                    "Git-Remote-URL."
                )

        # ========================================================
        # DATEIEN HINZUFÜGEN
        # ========================================================
        update_p(
            55,
            tr(
                "github_upload_files",
                "📦 OSCam-Emu-Dateien werden vorbereitet...",
                "📦 Preparing OSCam-Emu files..."
            )
        )

        log(
            "Alle Dateien werden zu Git hinzugefügt..."
        )

        run_git(
            [
                "add",
                "."
            ],
            cwd=target_dir,
            env=silent_env,
            check=True
        )

        # ========================================================
        # PRÜFEN, OB ES ÄNDERUNGEN GIBT
        # ========================================================
        staged_check = run_git(
            [
                "diff",
                "--cached",
                "--quiet"
            ],
            cwd=target_dir,
            env=silent_env
        )

        if staged_check.returncode == 0:

            final_txt = tr(
                "github_upload_no_changes",
                "ℹ️ Keine Änderungen vorhanden – kein Upload notwendig.",
                "ℹ️ No changes detected – nothing to upload."
            )

            update_p(
                100,
                final_txt
            )

            log(
                final_txt,
                "warning"
            )

            play_sound(True)
            return

        if staged_check.returncode != 1:

            error_text = (
                staged_check.stderr.strip()
                or staged_check.stdout.strip()
                or "Git konnte den Änderungsstatus nicht prüfen."
            )

            error_text = mask_sensitive(
                error_text,
                username,
                token
            )

            raise RuntimeError(
                error_text
            )

        # ========================================================
        # COMMIT-NACHRICHT
        # ========================================================
        commit_msg_final = (
            "Sync OSCam-Emu folder"
        )

        header_func = globals().get(
            "get_patch_header"
        )

        if header_func:

            try:

                raw = header_func()

                if raw:

                    first_line = raw.splitlines()

                    if first_line:

                        candidate = (
                            first_line[0].strip()
                        )

                        if candidate:

                            commit_msg_final = (
                                candidate
                            )

            except Exception:
                pass

        # --------------------------------------------------------
        # Commit-Nachricht bereinigen
        # --------------------------------------------------------
        commit_msg_final = (
            commit_msg_final
            .replace("\r", " ")
            .replace("\n", " ")
            .strip()
        )

        if not commit_msg_final:

            commit_msg_final = (
                "Sync OSCam-Emu folder"
            )

        # ========================================================
        # COMMIT
        # ========================================================
        update_p(
            70,
            tr(
                "github_upload_commit",
                "💾 Änderungen werden committed...",
                "💾 Committing changes..."
            )
        )

        log(
            f"Commit: {commit_msg_final}"
        )

        commit = run_git(
            [
                "commit",
                "-m",
                commit_msg_final
            ],
            cwd=target_dir,
            env=silent_env
        )

        if commit.returncode != 0:

            commit_error = (
                commit.stderr.strip()
                or commit.stdout.strip()
                or "Git Commit fehlgeschlagen."
            )

            commit_error = mask_sensitive(
                commit_error,
                username,
                token
            )

            raise RuntimeError(
                f"Git Commit Fehler:\n{commit_error}"
            )

        # ========================================================
        # PUSH
        # ========================================================
        update_p(
            85,
            tr(
                "github_upload_push",
                "🚀 OSCam-Emu wird zu GitHub hochgeladen...",
                "🚀 Uploading OSCam-Emu to GitHub..."
            )
        )

        log(
            "Push zu GitHub gestartet...",
            "warning"
        )

        push = run_git(
            [
                "push",
                "--force-with-lease",
                "origin",
                branch
            ],
            cwd=target_dir,
            env=silent_env
        )

        # ========================================================
        # PUSH ERFOLGREICH
        # ========================================================
        if push.returncode == 0:

            final_txt = tr(
                "github_upload_success",
                "✅ OSCam-Emu erfolgreich zu GitHub hochgeladen",
                "✅ OSCam-Emu successfully uploaded to GitHub"
            )

            update_p(
                100,
                final_txt
            )

            log(
                final_txt,
                "success"
            )

            log(
                "Repository: https://github.com/speedy005/Oscam-emu",
                "success"
            )

            play_sound(True)
            return

        # ========================================================
        # PUSH FEHLER
        # ========================================================
        error_text = (
            push.stderr.strip()
            or push.stdout.strip()
            or "Unbekannter GitHub-Fehler."
        )

        safe_error = mask_sensitive(
            error_text,
            username,
            token
        )

        fail_txt = tr(
            "github_upload_failed",
            "❌ GitHub-Upload fehlgeschlagen",
            "❌ GitHub upload failed"
        )

        update_p(
            last_progress,
            fail_txt,
            is_err=True
        )

        log(
            f"GitHub Error:\n{safe_error}",
            "error"
        )

        play_sound(False)

    # ============================================================
    # GIT FEHLER
    # ============================================================
    except subprocess.CalledProcessError as exc:

        error_text = (
            getattr(
                exc,
                "stderr",
                None
            )
            or getattr(
                exc,
                "stdout",
                None
            )
            or str(exc)
        )

        safe_error = mask_sensitive(
            error_text,
            username,
            token
        )

        fail_txt = tr(
            "github_upload_failed",
            "❌ GitHub-Upload fehlgeschlagen",
            "❌ GitHub upload failed"
        )

        update_p(
            last_progress,
            fail_txt,
            is_err=True
        )

        log(
            f"Git-Fehler:\n{safe_error}",
            "error"
        )

        play_sound(False)

    # ============================================================
    # ALLGEMEINER FEHLER
    # ============================================================
    except Exception as exc:

        fail_txt = tr(
            "github_upload_failed",
            "❌ GitHub-Upload fehlgeschlagen",
            "❌ GitHub upload failed"
        )

        update_p(
            last_progress,
            fail_txt,
            is_err=True
        )

        safe_error = mask_sensitive(
            str(exc),
            username,
            token
        )

        log(
            f"Kritischer Fehler:\n{safe_error}",
            "error"
        )

        play_sound(False)

    # ============================================================
    # CLEANUP
    # ============================================================
    finally:

        # --------------------------------------------------------
        # Token aus Environment-Dict entfernen
        # --------------------------------------------------------
        try:

            silent_env.pop(
                "OSC_GITHUB_TOKEN",
                None
            )

            silent_env.pop(
                "OSC_GITHUB_USERNAME",
                None
            )

            silent_env.pop(
                "GIT_USERNAME",
                None
            )

            silent_env.pop(
                "GIT_ASKPASS",
                None
            )

        except Exception:
            pass

        # --------------------------------------------------------
        # Temporäre ASKPASS-Dateien entfernen
        # --------------------------------------------------------
        cleanup_askpass()



# =====================
# GITHUB CONFIG DIALOG
# =====================

# Kompatibilitäts-Export: Die GUI verwendet run_bash aus diesem Modul.
# Die Implementierung liegt zentral in patch_format, damit sie nicht dupliziert wird.
from patch_format import run_bash  # noqa: E402
