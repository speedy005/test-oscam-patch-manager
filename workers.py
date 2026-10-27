"""OSCam Patch Manager module fragment: Background workers and installation helpers."""

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

class OSCamUpdateWorker(QThread):
    """Prüft im Hintergrund, ob im Streamboard-Git neue Commits vorliegen."""

    status_signal = pyqtSignal(bool, str)  # (Update verfügbar?, Neuer Hash)

    def __init__(self, remote_url, local_path):
        super().__init__()
        self.remote_url = remote_url
        self.local_path = local_path

    def run(self):
        try:
            # 1. Remote HEAD Hash abrufen
            cmd_remote = ["git", "ls-remote", self.remote_url, "HEAD"]
            remote_out = subprocess.check_output(
                cmd_remote, text=True, stderr=subprocess.DEVNULL
            ).split()
            if not remote_out:
                return
            remote_hash = remote_out[0]

            # 2. Lokalen Hash prüfen
            local_hash = ""
            # Prüfen ob Pfad existiert UND ein Git-Repo ist
            if os.path.exists(self.local_path) and os.path.exists(
                os.path.join(self.local_path, ".git")
            ):
                cmd_local = ["git", "-C", self.local_path, "rev-parse", "HEAD"]
                local_hash = subprocess.check_output(
                    cmd_local, text=True, stderr=subprocess.DEVNULL
                ).strip()
            else:
                # Falls Ordner nicht existiert -> Update (Installation) ist definitiv nötig
                local_hash = "NOT_INSTALLED"

            # 3. Vergleich & Signal senden
            update_available = remote_hash != local_hash
            self.status_signal.emit(update_available, remote_hash)
        except Exception as e:
            print(f"Update-Check Fehler: {e}")
            self.status_signal.emit(False, "")

def check_and_install_dependencies(required_packages):
    missing_packages = []
    for pkg in required_packages:
        if importlib.util.find_spec(pkg) is None:
            missing_packages.append(pkg)

    if missing_packages:
        # Falls PyQt6 fehlt, können wir keine MessageBox zeigen!
        if "PyQt6" in missing_packages:
            print(f"\n⚠️ Fehlende Komponenten: {', '.join(missing_packages)}")
            choice = input("Möchten Sie diese jetzt via pip installieren? (j/n): ")
            if choice.lower() == "j":
                for pkg in missing_packages:
                    subprocess.check_call([sys.executable, "-m", "pip", "install", pkg])
                # Neustart des Skripts
                os.execv(sys.executable, [sys.executable] + sys.argv)
                return True
            return False

        # Wenn PyQt6 vorhanden ist, aber andere (wie requests) fehlen:
        from PyQt6.QtWidgets import QApplication, QMessageBox

        temp_app = QApplication.instance() or QApplication(sys.argv)

        msg = QMessageBox()
        msg.setIcon(QMessageBox.Icon.Information)
        msg.setWindowTitle("Fehlende Komponenten")
        msg.setText(f"Folgende Bibliotheken fehlen: {', '.join(missing_packages)}")
        msg.setInformativeText("Möchten Sie diese jetzt installieren?")
        msg.setStandardButtons(
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )

        if msg.exec() == QMessageBox.StandardButton.Yes:
            for pkg in missing_packages:
                subprocess.check_call([sys.executable, "-m", "pip", "install", pkg])
            # Neustart des Skripts nach Installation
            os.execv(sys.executable, [sys.executable] + sys.argv)
            return True
    return False

# ===================== GLOBALE SOUND-SICHERHEIT=====================
import glob
import os
import platform
import shutil
import subprocess

# 'pwd' nur unter Linux importieren, um den Windows-Fehler zu verhindern
if platform.system() == "Linux":
    import pwd
else:
    pwd = None

HAS_PAPLAY = shutil.which("paplay") is not None


def safe_play(sound_name):
    if platform.system() != "Linux" or pwd is None:
        return

    s_path = f"/usr/share/sounds/freedesktop/stereo/{sound_name}"

    if not (HAS_PAPLAY and os.path.exists(s_path)):
        return

    try:
        # Normaler Benutzer
        if os.geteuid() != 0:
            subprocess.Popen(["paplay", s_path])
            return

        # Als Root -> aktiven Desktop-Benutzer suchen
        for runtime in glob.glob("/run/user/*"):
            uid_str = os.path.basename(runtime)
            if not uid_str.isdigit():
                continue

            uid = int(uid_str)

            # Systembenutzer überspringen
            if uid < 1000:
                continue

            try:
                # Holt den Benutzernamen (fängt KeyError ab, falls UID ungültig ist)
                user = pwd.getpwuid(uid).pw_name
            except KeyError:
                continue  # Überspringen, falls der Benutzer nicht (mehr) existiert

            env = os.environ.copy()
            env["XDG_RUNTIME_DIR"] = runtime

            subprocess.Popen(
                ["runuser", "-u", user, "--", "paplay", s_path],
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return

    except Exception as e:
        print("safe_play:", e)

import os
import shutil
import platform
import subprocess
import tempfile
from PyQt6.QtCore import QThread, pyqtSignal



def _windows_msys2_log_adapter(worker):
    """Gibt MSYS2-Installationsmeldungen aus und nutzt optional ein Worker-Logsignal."""
    def _log(message):
        message = str(message).strip()
        if not message:
            return
        signal = getattr(worker, "log_signal", None)
        if signal is not None and hasattr(signal, "emit"):
            try:
                signal.emit(message)
            except Exception:
                pass
        print(f"[MSYS2] {message}")
    return _log


def _windows_prepare_or_reuse(tool, base_target_dir, log=None, repo_url=None):
    """Bereitet das ausgewählte Repository im richtigen Projektordner vor.

    Wenn der Benutzer den gemeinsamen Basisordner auswählt, wird das Repository
    in <Basisordner>/<Projektordner> geklont. Wenn bereits der Projektordner
    selbst ausgewählt wurde, wird genau dieser Ordner verwendet.

    Für S3 Simplebuild ergibt sich z. B.:
        <Basisordner>/s3/
            support/       (Support-Dateien aus dem Repository)
            s3             (Startskript, sofern im Repository vorhanden)
            weitere Dateien und Ordner aus dem Repository
    """
    from pathlib import Path
    from windows_msys2_setup import prepare_tool

    log = log or (lambda _message: None)
    base = Path(base_target_dir).expanduser().resolve()
    # Der Installationsdialog soll den Basisordner bestimmen, nicht den
    # Repository-Unterordner "support". Falls support gewählt wurde, auf
    # den S3-Projektordner bzw. dessen Basis zurückgehen.
    if base.name.lower() == "support":
        if base.parent.name.lower() == "s3":
            base = base.parent
        else:
            base = base.parent
    folder_names = {
        "s3": "s3",
        "s3_speedy005": "s3_speedy005",
        "ncam": "s3_ncam_bonecrew",
        "s4": "simplebuild4",
    }
    if tool not in folder_names:
        raise ValueError(f"Unbekanntes Tool: {tool}")

    project_name = folder_names[tool]
    # Ein direkt ausgewählter Projektordner darf nicht nochmals verschachtelt
    # werden. Andernfalls wird unter dem gewählten Basisordner ein Projektordner
    # angelegt (insbesondere <Basisordner>/s3 für S3).
    target = base if base.name.lower() == project_name.lower() else base / project_name

    def is_ready(path):
        if not path.is_dir():
            return False
        if tool == "s4":
            return any((path / name).is_file() for name in
                       ("simplebuild", "simplebuild.exe", "s4", "s4.exe", "simplebuild.sh"))
        return any((path / name).is_file() for name in ("s3", "s3.exe", "simplebuild"))

    if is_ready(target):
        log(f"Vorhandener Projektordner erkannt: {target}")
    else:
        log(f"Repository-Zielordner: {target}")
        log("Das Repository wird im Projektordner abgelegt; Unterordner wie 'support' bleiben Unterordner.")

    # prepare_tool/ensure_repository erwartet den konkreten Zielordner des
    # Repositorys, nicht dessen übergeordneten Basisordner.
    result = prepare_tool(tool, str(target), log=log, repo_url=repo_url)

    if target.is_dir():
        log(f"Projektpfad: {target}")
        return target
    return result



class S3InstallWorker(QThread):
    """Installiert S3 aus einem auswählbaren Git-Repository."""

    finished_signal = pyqtSignal(bool, str)
    log_signal = pyqtSignal(str)

    # Repository-Name -> Git-URL. Jede Quelle wird in einen eigenen
    # Projektordner installiert; die Git-Struktur bleibt direkt im Zielordner.
    REPOSITORIES = {
        "S3 Simplebuild (Original)": "https://github.com/gorgone/s3_releases.git",
        "S3 Simplebuild speeddy005": "https://github.com/speedy005/speedy005-S3-Simplebuild.git",
    }
    TARGET_FOLDERS = {
        "S3 Simplebuild (Original)": "s3",
        "S3 Simplebuild speeddy005": "s3_speedy005",
    }
    WINDOWS_TOOLS = {
        "S3 Simplebuild (Original)": "s3",
        "S3 Simplebuild speeddy005": "s3_speedy005",
    }

    def __init__(
        self,
        base_target_dir,
        repo_url=None,
        project_name=None,
        sub_folder="s3",
        parent=None,
    ):
        super().__init__(parent)

        self.base_target_dir = os.path.normpath(
            os.path.expanduser(base_target_dir)
        )

        self.repo_url = (
            repo_url or self.REPOSITORIES["S3 Standard (Original)"]
        )

        self.project_name = project_name or self._get_project_name()
        self.sub_folder = sub_folder

    def _get_project_name(self):
        for name, url in self.REPOSITORIES.items():
            if url == self.repo_url:
                return name
        return "S3"

    def _log(self, message):
        message = str(message)
        print(f"[S3] {message}")
        self.log_signal.emit(message)

    def run(self):
        temp_clone = None

        try:
            self._log(f"Projekt: {self.project_name}")
            self._log(f"Repository: {self.repo_url}")
            self._log(f"Zielbasis: {self.base_target_dir}")

            # Unter Windows zunächst das Bootstrap-Modul verwenden.
            if platform.system() == "Windows":
                selected_tool = self.WINDOWS_TOOLS.get(self.project_name, "s3")
                installed_path = _windows_prepare_or_reuse(
                    selected_tool,
                    self.base_target_dir,
                    log=_windows_msys2_log_adapter(self),
                    repo_url=self.repo_url,
                )

                self.finished_signal.emit(
                    True,
                    f"{self.project_name} erfolgreich installiert!\n"
                    f"Repository: {self.repo_url}\n"
                    f"Pfad: {installed_path}",
                )
                return

            # Linux und weitere Unix-Systeme
            # Die Auswahl kann entweder der gemeinsame Basisordner oder
            # direkt der Projektordner sein. In beiden Fällen soll die
            # Repository-Struktur direkt in <Basisordner>/s3 liegen.
            target_folder = self.TARGET_FOLDERS.get(self.project_name, self.sub_folder)
            if os.path.basename(os.path.normpath(self.base_target_dir)).lower() == target_folder.lower():
                final_destination = self.base_target_dir
            else:
                final_destination = os.path.join(self.base_target_dir, target_folder)

            os.makedirs(self.base_target_dir, exist_ok=True)

            temp_clone = tempfile.mkdtemp(
                prefix="s3_install_clone_"
            )

            self._log("Git-Repository wird geklont ...")

            result = subprocess.run(
                [
                    "git",
                    "clone",
                    "--depth",
                    "1",
                    self.repo_url,
                    temp_clone,
                ],
                shell=False,
                capture_output=True,
                text=True,
            )

            if result.returncode != 0:
                details = (result.stderr or result.stdout or "").strip()
                raise RuntimeError(
                    f"Git Clone fehlgeschlagen:\n{details}"
                )

            self._log("Repository erfolgreich geklont.")

            # Git-Metadaten aus der Installation entfernen.
            git_dir = os.path.join(temp_clone, ".git")

            if os.path.isdir(git_dir):
                shutil.rmtree(git_dir)

            # Zielverzeichnis anlegen und Projektdateien kopieren.
            os.makedirs(final_destination, exist_ok=True)

            for item in os.listdir(temp_clone):
                source = os.path.join(temp_clone, item)
                destination = os.path.join(final_destination, item)

                if os.path.isdir(source):
                    shutil.copytree(
                        source,
                        destination,
                        dirs_exist_ok=True,
                    )
                else:
                    shutil.copy2(source, destination)

            self._log("Dateien wurden kopiert.")

            # Ausführungsrechte setzen.
            if platform.system() == "Linux":
                result = subprocess.run(
                    [
                        "chmod",
                        "-R",
                        "755",
                        final_destination,
                    ],
                    capture_output=True,
                    text=True,
                )

                if result.returncode != 0:
                    self._log(
                        "Warnung: Dateirechte konnten nicht vollständig "
                        "gesetzt werden."
                    )

            self.finished_signal.emit(
                True,
                f"{self.project_name} erfolgreich installiert!\n"
                f"Repository: {self.repo_url}\n"
                f"Pfad: {final_destination}",
            )

        except Exception as e:
            error_msg = str(e)

            if (
                "Permission denied" in error_msg
                or "[Errno 13]" in error_msg
                or "WinError 5" in error_msg
            ):
                error_msg += (
                    "\n\nTipp: Wählen Sie einen anderen "
                    "Installationsordner oder prüfen Sie die "
                    "Dateiberechtigungen."
                )

            self.finished_signal.emit(
                False,
                f"{self.project_name} Fehler:\n{error_msg}",
            )

        finally:
            if temp_clone and os.path.exists(temp_clone):
                shutil.rmtree(temp_clone, ignore_errors=True)


import os
import shutil
import platform
import subprocess
import tempfile
import time
from PyQt6.QtCore import QThread, pyqtSignal

class NcamBonecrewInstallWorker(QThread):
    finished_signal = pyqtSignal(bool, str)

    def __init__(self, base_target_dir):
        super().__init__()
        self.base_target_dir = os.path.normpath(base_target_dir)

    def run(self):
        # Windows verwendet das separate Bootstrap-Modul; Linux bleibt beim
        # bisherigen Installationsablauf in dieser Worker-Klasse.
        if platform.system() == "Windows":
            try:
                installed_path = _windows_prepare_or_reuse(
                    "ncam", self.base_target_dir, log=_windows_msys2_log_adapter(self)
                )
                self.finished_signal.emit(
                    True, f"NCam Bonecrew erfolgreich installiert!\nPfad: {installed_path}"
                )
            except Exception as e:
                error_msg = str(e)
                if "Permission denied" in error_msg or "[Errno 13]" in error_msg:
                    error_msg += "\n\nTipp: Wählen Sie einen anderen Installationsort oder prüfen Sie die Windows-Berechtigungen."
                self.finished_signal.emit(False, f"NCam Bonecrew Fehler:\n{error_msg}")
            return

        temp_clone = None
        proj_name = "NCam Bonecrew"
        sub_folder = "s3_ncam_bonecrew"
        repo_url = "https://github.com/speedy005/s3_ncam_bonecrew_test"
        
        try:
            final_destination = os.path.join(self.base_target_dir, sub_folder)
            temp_clone = tempfile.mkdtemp(prefix="s3_ncam_clone_")
            
            time.sleep(0.5)
            is_win = platform.system() == "Windows"
            
            # WINDOWS-FIX: Wir deaktivieren den NTFS-Pfadschutz für diesen Klon-Vorgang.
            # Dadurch ignoriert Git die Logdateien mit ":" im Namen, anstatt abzubrechen.
            cmd = [
                "git", 
                "-c", "credential.helper=", 
                "-c", "core.protectNTFS=false", 
                "clone", 
                "--depth", "1", 
                repo_url, 
                temp_clone
            ]
            
            result = subprocess.run(
                cmd,
                shell=False,
                capture_output=True,
                text=True
            )
            
            # Ein Rückkehrwert von 0 ist Erfolg. Unter Windows kann Git hier 
            # trotz der Warnung "Clone succeeded" mit Code 128 (oder ungleich 0) abbrechen.
            # Deshalb prüfen wir, ob die wichtige Hauptdatei 's3' trotzdem erzeugt wurde.
            if result.returncode != 0 and not os.path.exists(os.path.join(temp_clone, "s3")):
                git_err = result.stderr.strip() if result.stderr else f"Exit-Code {result.returncode}"
                raise Exception(f"Git-Fehler: {git_err}")

            # 2. .git Ordner bereinigen (Windows Fix)
            git_dir = os.path.join(temp_clone, ".git")
            if os.path.exists(git_dir):
                if is_win:
                    subprocess.run(['attrib', '-R', os.path.join(git_dir, '*'), '/S', '/D'], 
                                   shell=False, capture_output=True)
                shutil.rmtree(git_dir, ignore_errors=True)

            # 3. Zielordner erstellen und kopieren
            os.makedirs(final_destination, exist_ok=True)
            for item in os.listdir(temp_clone):
                s = os.path.join(temp_clone, item)
                d = os.path.join(final_destination, item)
                if os.path.isdir(s):
                    shutil.copytree(s, d, dirs_exist_ok=True)
                else:
                    shutil.copy2(s, d)

            # 4. Linux Rechte setzen
            if platform.system() == "Linux":
                try:
                    subprocess.call(["chmod", "-R", "755", final_destination])
                except:
                    pass

            self.finished_signal.emit(True, f"{proj_name} erfolgreich in '{sub_folder}' installiert!")

        except Exception as e:
            error_msg = str(e)
            if "Permission denied" in error_msg or "[Errno 13]" in error_msg:
                error_msg += "\n\nTipp: Starten Sie das Programm als Admin oder wählen Sie einen anderen Installationsort."
            
            self.finished_signal.emit(False, f"{proj_name} Fehler:\n{error_msg}")

        finally:
            if temp_clone and os.path.exists(temp_clone):
                shutil.rmtree(temp_clone, ignore_errors=True)

from PyQt6.QtCore import QThread, pyqtSignal
from PyQt6.QtWidgets import QFileDialog, QMessageBox, QApplication
import os, platform, shutil, subprocess, tempfile
import os, shutil, platform, subprocess, tempfile, re
from PyQt6.QtCore import QThread, pyqtSignal
import os, shutil, platform, subprocess, tempfile, re

class S4InstallWorker(QThread):
    finished_signal = pyqtSignal(bool, str)

    def __init__(self, base_target_dir):
        super().__init__()
        # Das ist der vom User gewählte Basis-Pfad (z.B. C:\opt oder /opt)
        self.base_target_dir = os.path.normpath(base_target_dir)

    def run(self):
        # Windows verwendet das separate Bootstrap-Modul; Linux bleibt beim
        # bisherigen Installationsablauf in dieser Worker-Klasse.
        if platform.system() == "Windows":
            try:
                installed_path = _windows_prepare_or_reuse(
                    "s4", self.base_target_dir, log=_windows_msys2_log_adapter(self)
                )
                self.finished_signal.emit(
                    True, f"SimpleBuild 4 erfolgreich installiert!\nPfad: {installed_path}"
                )
            except Exception as e:
                error_msg = str(e)
                if "Permission denied" in error_msg or "[Errno 13]" in error_msg:
                    error_msg += "\n\nTipp: Wählen Sie einen anderen Installationsort oder prüfen Sie die Windows-Berechtigungen."
                self.finished_signal.emit(False, f"SimpleBuild 4 Fehler:\n{error_msg}")
            return

        temp_clone = None
        proj_name = "SimpleBuild 4"
        sub_folder = "simplebuild4"
        repo_url = "https://git.streamboard.tv/common/simplebuild4.git"
        
        try:
            # 1. Zielverzeichnis festlegen
            final_destination = os.path.join(self.base_target_dir, sub_folder)

            # 2. Temporärer Clone im Benutzer-TEMP
            temp_clone = tempfile.mkdtemp(prefix="s4_clone_gen_")

            # 3. Git Clone mit --depth 1
            is_win = platform.system() == "Windows"
            subprocess.check_call(
                ["git", "clone", "--depth", "1", repo_url, temp_clone],
                shell=False
            )

            # 4. FIX FÜR PERMISSION DENIED (Windows):
            git_dir = os.path.join(temp_clone, ".git")
            if os.path.exists(git_dir):
                if is_win:
                    subprocess.run(['attrib', '-R', os.path.join(git_dir, '*'), '/S', '/D'], 
                                   shell=False, capture_output=True)
                shutil.rmtree(git_dir, ignore_errors=True)

            # 5. Ziel-Unterordner erstellen
            os.makedirs(final_destination, exist_ok=True)

            # 6. Inhalte aus dem Temp-Clone kopieren
            for item in os.listdir(temp_clone):
                s = os.path.join(temp_clone, item)
                d = os.path.join(final_destination, item)
                
                if os.path.isdir(s):
                    shutil.copytree(s, d, dirs_exist_ok=True)
                else:
                    shutil.copy2(s, d)

            # 7. Linux Rechte setzen
            if platform.system() == "Linux":
                try:
                    subprocess.call(["chmod", "-R", "755", final_destination])
                except:
                    pass

            self.finished_signal.emit(True, f"{proj_name} erfolgreich in '{sub_folder}' installiert!")

        except Exception as e:
            error_msg = str(e)
            if "Permission denied" in error_msg or "[Errno 13]" in error_msg:
                error_msg += "\n\nTipp: Starten Sie das Programm als Admin oder wählen Sie einen anderen Installationsort."
            
            self.finished_signal.emit(False, f"{proj_name} Fehler: {error_msg}")

        finally:
            if temp_clone and os.path.exists(temp_clone):
                shutil.rmtree(temp_clone, ignore_errors=True)
# ===================== VERSION HANDLING (Optimiert) =====================
try:
    from packaging.version import Version, InvalidVersion
except (ImportError, ModuleNotFoundError):
    class Version:
        def __init__(self, vstring):
            # Extrahiert Versionen wie 'v1.2.3' -> [1, 2, 3]
            cleaned = "".join(re.findall(r'[0-9.]', str(vstring)))
            self.v = [int(x) for x in cleaned.split(".") if x.isdigit()]

        def __gt__(self, other): return self.v > other.v
        def __lt__(self, other): return self.v < other.v
        def __ge__(self, other): return self.v >= other.v
        def __le__(self, other): return self.v <= other.v
        def __eq__(self, other): return self.v == other.v

    class InvalidVersion(Exception):
        pass

# ===================== ENV SETUP =====================
# Git Fehler unterdrücken
if platform.system() == "Windows":
    os.environ["GIT_REDIRECT_STDERR"] = "2>nul"
else:
    os.environ["GIT_REDIRECT_STDERR"] = "2>/dev/null"
# ===================== SCRIPT DIR =====================
PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))

def ensure_executable_self():
    """Setzt Ausführungsrechte für das eigene Skript (Linux/Unix)."""
    try:
        st = os.stat(__file__)
        if not (st.st_mode & stat.S_IXUSR):
            os.chmod(__file__, st.st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    except Exception as e:
        print(f"[WARN] Konnte Rechte nicht setzen: {e}")

# ===================== ZEIT =====================
