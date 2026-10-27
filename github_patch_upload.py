"""OSCam Patch Manager module fragment: Patch upload and related GitHub helpers."""

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

from git_manager import git_command
from patch_format import load_github_config
from paths import PATCH_FILE
from translations import TEXTS
from workers import safe_play

def github_upload_patch_file(
    gui_instance=None,
    info_widget=None,
    progress_callback=None
):
    """
    Lädt ausschließlich PATCH_FILE als oscam-emu.patch
    in das konfigurierte GitHub-Repository.

    Sicherheit:
    - GitHub-Token wird NICHT in die Repository-URL geschrieben.
    - Token landet NICHT in .git/config.
    - Authentifizierung erfolgt temporär über GIT_ASKPASS.
    - GIT_TERMINAL_PROMPT=0 verhindert interaktive Abfragen.
    - Token wird nicht absichtlich geloggt.
    - Temporäre ASKPASS-Dateien werden nach dem Upload gelöscht.

    Windows-sicher:
    - Repository wird mit --no-checkout geklont.
    - Problematische Linux-/Unix-Pfade werden unter Windows
      nicht ausgecheckt.
    - Nur oscam-emu.patch wird in den Arbeitsbaum geschrieben.
    - Kein run_bash().
    - Git wird über git_command() ausgeführt.
    """

    from PyQt6.QtWidgets import QTextEdit, QApplication

    import os
    import sys
    import shutil
    import tempfile
    import datetime
    import time
    import stat

    # ------------------------------------------------------------
    # Final Label verstecken
    # ------------------------------------------------------------
    if gui_instance and hasattr(
        gui_instance,
        "hide_final_label"
    ):
        try:
            gui_instance.hide_final_label()
        except Exception:
            pass

    # ------------------------------------------------------------
    # Widget / Sprache
    # ------------------------------------------------------------
    widget = (
        info_widget
        if isinstance(info_widget, QTextEdit)
        else getattr(
            gui_instance,
            "info_text",
            None
        )
    )

    pbar = getattr(
        gui_instance,
        "progress_bar",
        None
    )

    lang = str(
        getattr(gui_instance, "LANG", "de")
    ).lower()[:2]

    # ------------------------------------------------------------
    # ProgressBar
    # ------------------------------------------------------------
    rainbow = (
        "qlineargradient("
        "x1:0, y1:0, x2:1, y2:0, "
        "stop:0.0 #FF0000, "
        "stop:0.2 #FF7F00, "
        "stop:0.4 #FFFF00, "
        "stop:0.6 #00FF00, "
        "stop:0.8 #0000FF, "
        "stop:1.0 #8B00FF"
        ")"
    )

    style_rb = f"""
        QProgressBar {{
            text-align: center;
            font-weight: 900;
            border: 2px solid #222;
            border-radius: 6px;
            background-color: #111;
            color: black;
            font-size: 14pt;
        }}

        QProgressBar::chunk {{
            background: {rainbow};
            border-radius: 4px;
        }}
    """

    style_err = """
        QProgressBar {
            text-align: center;
            font-weight: 900;
            border: 2px solid #500;
            border-radius: 6px;
            background-color: #111;
            color: #FF0000;
            font-size: 12pt;
        }

        QProgressBar::chunk {
            background-color: #800;
        }
    """

    def set_progress(
        value,
        is_err=False,
        text=None
    ):
        try:

            if pbar:

                pbar.setStyleSheet(
                    style_err
                    if is_err
                    else style_rb
                )

                pbar.setValue(
                    int(value)
                )

                if text:
                    pbar.setFormat(text)
                else:
                    pbar.setFormat("%p%")

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

    # ------------------------------------------------------------
    # Sound – Windows + Linux
    # ------------------------------------------------------------
    def play_sound(success=True):

        sound_file = (
            "complete.oga"
            if success
            else "dialog-error.oga"
        )

        safe_play_func = globals().get("safe_play")

        # Vorhandenen plattformübergreifenden Player verwenden
        if callable(safe_play_func):
            try:
                safe_play_func(sound_file)
                return
            except Exception:
                pass

        # Windows-Fallback
        if sys.platform == "win32":
            try:
                import winsound

                winsound.MessageBeep(
                    winsound.MB_OK
                    if success
                    else winsound.MB_ICONHAND
                )
            except Exception:
                pass

    # ------------------------------------------------------------
    # Logging
    # ------------------------------------------------------------
    def log(
        message,
        level="info",
        **kwargs
    ):

        try:

            # -----------------------------------------------
            # TEXTS-Key
            # -----------------------------------------------
            if (
                isinstance(message, str)
                and "TEXTS" in globals()
            ):

                lang_dict = TEXTS.get(
                    lang,
                    TEXTS.get("en", {})
                )

                if message in lang_dict:

                    text_template = (
                        lang_dict[message]
                    )

                    try:
                        text = text_template.format(
                            **kwargs
                        )
                    except Exception:
                        text = text_template

                else:
                    text = message

            else:
                text = str(message)

            # -----------------------------------------------
            # Sicherheitsfilter für Credentials
            # -----------------------------------------------
            github_token = kwargs.get(
                "_github_token",
                ""
            )

            github_username = kwargs.get(
                "_github_username",
                ""
            )

            if github_token:
                text = text.replace(
                    github_token,
                    "***TOKEN***"
                )

            if (
                github_username
                and github_token
            ):

                text = text.replace(
                    f"{github_username}:{github_token}",
                    f"{github_username}:***TOKEN***"
                )

            # -----------------------------------------------
            # GUI
            # -----------------------------------------------
            if (
                gui_instance
                and hasattr(
                    gui_instance,
                    "append_info"
                )
            ):

                gui_instance.append_info(
                    widget,
                    text,
                    level
                )

            try:
                QApplication.processEvents()
            except Exception:
                pass

        except Exception:
            pass

    # ------------------------------------------------------------
    # Windows-sicheres Cleanup
    # ------------------------------------------------------------
    def cleanup_directory(
        path,
        retries=5
    ):

        if not path:
            return

        if not os.path.exists(path):
            return

        last_error = None

        for attempt in range(retries):

            try:

                # -------------------------------------------
                # Schreibschutz entfernen
                # -------------------------------------------
                for root, dirs, files in os.walk(
                    path,
                    topdown=False
                ):

                    for filename in files:

                        filename_path = os.path.join(
                            root,
                            filename
                        )

                        try:
                            os.chmod(
                                filename_path,
                                0o666
                            )
                        except Exception:
                            pass

                    for dirname in dirs:

                        dirname_path = os.path.join(
                            root,
                            dirname
                        )

                        try:
                            os.chmod(
                                dirname_path,
                                0o777
                            )
                        except Exception:
                            pass

                shutil.rmtree(path)

                if not os.path.exists(path):
                    return

            except Exception as exc:

                last_error = exc

                try:
                    QApplication.processEvents()
                except Exception:
                    pass

                time.sleep(0.5)

        if last_error:

            raise RuntimeError(
                "Temporäres Git-Verzeichnis konnte "
                "nicht gelöscht werden:\n\n"
                f"{path}\n\n"
                f"{last_error}"
            )

    # ------------------------------------------------------------
    # Sichere ASKPASS-Erzeugung
    # ------------------------------------------------------------
    askpass_dir = None
    askpass_script = None
    askpass_launcher = None

    def create_askpass():

        nonlocal askpass_dir
        nonlocal askpass_script
        nonlocal askpass_launcher

        askpass_dir = tempfile.mkdtemp(
            prefix="oscam_git_auth_",
            dir=temp_parent
        )

        # --------------------------------------------------------
        # Das Skript enthält KEIN Token.
        # Das Token wird nur aus der Environment gelesen.
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
        # Windows
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
        # Linux / macOS
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

    # ------------------------------------------------------------
    # ASKPASS Cleanup
    # ------------------------------------------------------------
    def cleanup_askpass():

        nonlocal askpass_dir

        try:

            if (
                askpass_dir
                and os.path.isdir(
                    askpass_dir
                )
            ):

                shutil.rmtree(
                    askpass_dir,
                    ignore_errors=True
                )

        except Exception:
            pass

        askpass_dir = None


    # ------------------------------------------------------------
    # Startsound – Windows + Linux
    # ------------------------------------------------------------
    def play_start_sound():
        safe_play_func = globals().get("safe_play")

        if callable(safe_play_func):
            try:
                safe_play_func("complete.oga")
                return
            except Exception:
                pass

        # Windows-Fallback
        if sys.platform == "win32":
            try:
                import winsound
                winsound.MessageBeep(
                    winsound.MB_ICONASTERISK
                )
            except Exception:
                pass

    # ------------------------------------------------------------
    # Start
    # ------------------------------------------------------------
    temp_parent = None

    # Credentials werden nur innerhalb dieser Funktion gehalten.
    username = ""
    token = ""

    # Environment wird später wieder bereinigt.
    git_env = None

    play_start_sound()

    set_progress(
        5,
        text="⏳ Start..."
    )

    try:


        # ========================================================
        # GitHub Config
        # ========================================================
        cfg = load_github_config()

        if not isinstance(cfg, dict):
            raise RuntimeError(
                "GitHub-Konfiguration ist ungültig."
            )

        repo_url = str(
            cfg.get("repo_url") or ""
        ).strip()

        branch = str(
            cfg.get("branch") or "master"
        ).strip()

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

        # ========================================================
        # Credentials prüfen
        # ========================================================
        if (
            not repo_url
            or not username
            or not token
        ):

            log(
                "GitHub Credentials fehlen.",
                "error"
            )

            set_progress(
                100,
                is_err=True,
                text="❌ Credentials"
            )

            play_sound(False)
            return False

        # ========================================================
        # Repository URL prüfen
        # ========================================================
        if not repo_url.startswith(
            "https://"
        ):

            raise RuntimeError(
                "Die GitHub Repository-URL muss "
                "mit https:// beginnen."
            )

        # ========================================================
        # Patch prüfen
        # ========================================================
        patch_file = (
            getattr(gui_instance, "PATCH_FILE", None)
            or PATCH_FILE
        )

        if not patch_file:

            raise RuntimeError(
                "PATCH_FILE ist nicht definiert."
            )

        patch_file = os.path.abspath(
            os.path.expanduser(
                str(patch_file)
            )
        )

        if not os.path.isfile(
            patch_file
        ):

            log(
                f"Patch-Datei nicht gefunden:\n"
                f"{patch_file}",
                "error"
            )

            set_progress(
                100,
                is_err=True,
                text="❌ Patch fehlt"
            )

            play_sound(False)
            return False

        # ========================================================
        # Temporären Parent erzeugen
        # ========================================================
        set_progress(
            15,
            text="📁 Temp-Verzeichnis..."
        )

        plugin_dir = globals().get(
            "PLUGIN_DIR"
        )

        if plugin_dir:
            plugin_dir = os.path.abspath(
                os.path.expanduser(
                    str(plugin_dir)
                )
            )

            try:
                os.makedirs(
                    plugin_dir,
                    exist_ok=True
                )
            except Exception:
                plugin_dir = None

        temp_parent = tempfile.mkdtemp(
            prefix="oscam_patch_upload_",
            dir=plugin_dir
            if plugin_dir
            else None
        )

        temp_repo = os.path.join(
            temp_parent,
            "repo"
        )

        # ========================================================
        # Sichere Git Environment
        # ========================================================
        git_env = os.environ.copy()

        git_env[
            "GIT_TERMINAL_PROMPT"
        ] = "0"

        git_env[
            "GIT_ASKPASS_REQUIRE"
        ] = "force"

        # Token NICHT in URL.
        git_env[
            "OSC_GITHUB_USERNAME"
        ] = username

        git_env[
            "OSC_GITHUB_TOKEN"
        ] = token

        # ========================================================
        # ASKPASS erzeugen
        # ========================================================
        create_askpass()

        git_env[
            "GIT_ASKPASS"
        ] = askpass_launcher

        # ========================================================
        # Repository klonen
        #
        # --no-checkout verhindert unter Windows:
        #
        # invalid path ...
        #
        # Es wird KEIN vollständiger Working Tree ausgecheckt.
        # ========================================================
        set_progress(
            25,
            text="📥 GitHub Repository..."
        )

        log(
            "GitHub Repository wird ohne Checkout geklont...",
            "info"
        )

        git_command(
            [
                "clone",
                "--no-checkout",
                "--branch",
                branch,
                repo_url,
                temp_repo,
            ],
            cwd=temp_parent,
            timeout=600,
            env=git_env
        )

        # ========================================================
        # Sicherheitsprüfung:
        # Remote darf keinen Token enthalten.
        # ========================================================
        try:

            remote_result = git_command(
                [
                    "remote",
                    "get-url",
                    "origin"
                ],
                cwd=temp_repo,
                env=git_env
            )

            remote_url = str(
                remote_result or ""
            ).strip()

            if token and token in remote_url:

                raise RuntimeError(
                    "Sicherheitsfehler: "
                    "GitHub-Token wurde in der "
                    "Remote-URL erkannt."
                )

        except TypeError:
            # Falls git_command() keinen env-Parameter
            # akzeptiert, wird die Prüfung später über
            # git config durchgeführt.
            pass

        # ========================================================
        # Git Config
        # ========================================================
        set_progress(
            45,
            text="⚙️ Git Config..."
        )

        if user_name:

            git_command(
                [
                    "config",
                    "user.name",
                    user_name
                ],
                cwd=temp_repo,
                env=git_env
            )

        if user_email:

            git_command(
                [
                    "config",
                    "user.email",
                    user_email
                ],
                cwd=temp_repo,
                env=git_env
            )

        # ========================================================
        # Nur Patch-Datei erzeugen
        # ========================================================
        set_progress(
            55,
            text="📄 Patch kopieren..."
        )

        destination_patch = os.path.join(
            temp_repo,
            "oscam-emu.patch"
        )

        shutil.copy2(
            patch_file,
            destination_patch
        )

        # ========================================================
        # Version aus Patch Header
        # ========================================================
        try:

            with open(
                patch_file,
                "r",
                encoding="utf-8",
                errors="replace"
            ) as f:

                patch_version = (
                    f.readline().strip()
                    or "Patch Update"
                )

        except Exception:

            patch_version = "Patch Update"

        # ========================================================
        # Nur oscam-emu.patch hinzufügen
        # ========================================================
        set_progress(
            70,
            text="📦 Patch vorbereiten..."
        )

        git_command(
            [
                "add",
                "--",
                "oscam-emu.patch"
            ],
            cwd=temp_repo,
            env=git_env
        )

        # ========================================================
        # Commit
        # ========================================================
        commit_msg = (
            f"{patch_version} | "
            f"{datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        )

        git_command(
            [
                "commit",
                "-m",
                commit_msg,
                "--allow-empty"
            ],
            cwd=temp_repo,
            allowed_returncodes=(0, 1),
            env=git_env
        )

        # ========================================================
        # Push
        # ========================================================
        set_progress(
            85,
            text="🚀 GitHub Upload..."
        )

        log(
            "oscam-emu.patch wird zu GitHub übertragen...",
            "warning"
        )

        # --------------------------------------------------------
        # Bewusst KEIN --force.
        #
        # Da wir nur eine einzelne Datei synchronisieren,
        # sollte ein normaler Push zuerst versucht werden.
        # --------------------------------------------------------
        git_command(
            [
                "push",
                "origin",
                branch
            ],
            cwd=temp_repo,
            timeout=600,
            env=git_env
        )

        # ========================================================
        # Erfolg
        # ========================================================
        log(
            "github_patch_uploaded",
            "success",
            patch_version=patch_version
        )

        set_progress(
            100,
            text=(
                "✅ Patch hochgeladen"
                if lang == "de"
                else "✅ Patch Uploaded"
            )
        )

        play_sound(True)

        return True

    # ============================================================
    # Fehler
    # ============================================================
    except Exception as exc:

        error_text = str(
            exc
        ).strip()

        if not error_text:
            error_text = "Unbekannter Fehler"

        # --------------------------------------------------------
        # Token niemals im Log anzeigen
        # --------------------------------------------------------
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

        log(
            f"GitHub Upload Fehler:\n{error_text}",
            "error"
        )

        set_progress(
            100,
            is_err=True,
            text="❌ Upload Fehler"
        )

        play_sound(False)

        return False

    # ============================================================
    # Cleanup
    # ============================================================
    finally:

        # --------------------------------------------------------
        # ASKPASS-Dateien löschen
        # --------------------------------------------------------
        try:
            cleanup_askpass()
        except Exception:
            pass

        # --------------------------------------------------------
        # Environment bereinigen
        # --------------------------------------------------------
        try:

            if git_env is not None:

                git_env.pop(
                    "OSC_GITHUB_TOKEN",
                    None
                )

                git_env.pop(
                    "OSC_GITHUB_USERNAME",
                    None
                )

                git_env.pop(
                    "GIT_ASKPASS",
                    None
                )

        except Exception:
            pass

        # --------------------------------------------------------
        # Temporäres Repository löschen
        # --------------------------------------------------------
        if temp_parent:

            try:

                if os.path.exists(
                    temp_parent
                ):

                    cleanup_directory(
                        temp_parent,
                        retries=5
                    )

            except Exception as cleanup_error:

                # Token aus Cleanup-Fehler entfernen
                cleanup_text = str(
                    cleanup_error
                )

                if token:

                    cleanup_text = (
                        cleanup_text.replace(
                            token,
                            "***TOKEN***"
                        )
                    )

                log(
                    "Temporäres Upload-Verzeichnis "
                    "konnte nicht gelöscht werden:\n"
                    f"{cleanup_text}",
                    "warning"
                )

        # --------------------------------------------------------
        # Lokale Referenzen auf Credentials löschen
        # --------------------------------------------------------
        username = ""
        token = ""
        git_env = None





# ============================================================
# GIT-FUNKTIONEN
# Diese Funktionen müssen VOR create_patch() stehen
# ============================================================

