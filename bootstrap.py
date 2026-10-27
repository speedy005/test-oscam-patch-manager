"""OSCam Patch Manager module fragment: Startup checks, dependency bootstrap, and system helpers."""

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

from paths import CONFIG_FILE

import os
import sys
import platform
import importlib.util 
# ============================================================
# QT / PLATTFORM-UMGEBUNG
# ============================================================
# Windows benötigt für problematische OpenGL-Treiber den Software-Renderer.
# Unter Linux darf QT_QPA_PLATFORM NICHT auf "windows" gesetzt werden.
# Wenn der Benutzer QT_QPA_PLATFORM bereits gesetzt hat, bleibt diese Wahl
# unangetastet.
if platform.system() == "Windows":
    os.environ.setdefault("QT_QUICK_BACKEND", "software")
    os.environ.setdefault("QT_OPENGL", "software")
    os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "0")
#!/usr/bin/env python3
# =====================================================================
#  OSCam Emu Patch Generator
#
#  Copyright (c) 2026 speedy005
#
#  Author: speedy005
#
#  License: MIT
#
#  IMPORTANT NOTICE:
#  This project is Open Source under the MIT License.
#  The author name "speedy005" must NOT be removed, changed,
#  or replaced in any modified or redistributed version.
#
#  Any redistribution or modification must retain this
#  copyright notice and author attribution.
#
#  THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND,
#  EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF
#  MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT.
# =====================================================================
import sys
import os
import json
import shutil
# ============================================================
# FULL BOOTSTRAP CLEAN VERSION
# ============================================================

import sys
import os
import platform
import subprocess
import shutil
import threading
import ctypes
import json
import zipfile
import io

# --- GLOBALE VARIABLEN & CONFIG ---
HAS_SOUND_SUPPORT = False
CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")
_SETTINGS_CACHE = {}  # Speicher-Cache für bessere Performance

ONLINE_PATCHES = {
    "speedy005 (Master)": "https://raw.githubusercontent.com/speedy005/oscam-emu-patch/refs/heads/master/oscam-emu.patch",
    "OSCam-Mirror (Master)": "https://raw.githubusercontent.com/oscam-mirror/oscam-emu-patch/refs/heads/master/oscam-emu.patch",
}

REQUIRED_PACKAGES = [
    "PyQt6",
    "requests",
    "packaging",
    "psutil",
    "urllib3",
]

REQUIRED_TOOLS = [
    "git",
    "patch",
    "zip",
    "ssh",
]

# ============================================================
# SYSTEM & ENVIRONMENT
# ============================================================

def check_python():
    if sys.version_info < (3, 9):
        # Falls Windows, geben wir dem User den passenden winget-Befehl zur Selbsthilfe aus
        if platform.system() == "Windows":
            print("\n" + "="*60)
            print("⚠️ [FEHLER] Python 3.9 oder höher wird zwingend benötigt!")
            print(f"Aktuelle Version: {sys.version_info.major}.{sys.version_info.minor}")
            print("\nTipp für Windows 11: Öffne die Eingabeaufforderung (cmd) als Admin")
            print("und tippe folgenden Befehl ein, um Python zu aktualisieren:")
            print("winget install --id Python.Python.3.11")
            print("="*60 + "\n")
        else:
            print("Python 3.9+ required")
        sys.exit(1)

def ensure_pip():
    try:
        import pip
    except ImportError:
        import ensurepip
        ensurepip.bootstrap()

def check_git_windows():
    """
    Spezifischer Vorab-Check für Windows.
    Installiert Git vollautomatisch im Hintergrund via winget, falls es fehlt.
    """
    if platform.system() != "Windows":
        return

    # Prüfen, ob Git bereits über die Standard-Erkennung erreichbar ist
    if shutil.which("git"):
        return

    print("\n" + "="*60)
    print("[AUTO-INSTALL] Git für Windows fehlt und wird jetzt installiert...")
    print("[AUTO-INSTALL] Bitte warten, dieser Vorgang dauert ca. 1-2 Minuten...")
    print("="*60 + "\n")

    # Winget-Befehl für eine absolut unbemerkte (silent) Hintergrund-Installation
    winget_cmd = [
        "winget", "install", 
        "--id", "Git.Git", 
        "--exact", 
        "--silent", 
        "--force", 
        "--disable-interactivity", 
        "--accept-source-agreements", 
        "--accept-package-agreements"
    ]

    try:
        # Führt die Installation im Hintergrund aus (erfordert Adminrechte)
        subprocess.run(winget_cmd, capture_output=True, text=True, check=True)
        print("[✓] Git für Windows erfolgreich installiert!")
        print("[SYSTEM] Aktualisiere Umgebungsvariablen im RAM...")

        # Windows-Pfad live in der laufenden Python-Sitzung aktualisieren, 
        # damit Git SOFORT einsatzbereit ist (kein Programm-Neustart nötig).
        git_default_path = "C:\\Program Files\\Git\\cmd"
        if git_default_path not in os.environ["PATH"]:
            os.environ["PATH"] = git_default_path + os.pathsep + os.environ["PATH"]

        # Ruft deine Pfad-Fix-Funktion auf, falls sie im Skript existiert
        if "fix_windows_path" in globals():
            fix_windows_path()

        time.sleep(2)  # Kurze Atempause für die Festplatte

    except subprocess.CalledProcessError as e:
        print("[!] Automatische Git-Installation via winget fehlgeschlagen.")
        print(f"[!] Fehler-Details: {e.stderr}")
        print("[!] Bitte installiere Git manuell über https://git-scm.com")
    except Exception as e:
        print(f"[!] Unerwarteter Fehler bei der Git-Installation: {e}")

def install_python_packages():
    # WICHTIG: Bevor wir Python-Pakete prüfen oder installieren, 
    # stellen wir sicher, dass Git auf Windows-Systemen vorhanden ist!
    check_git_windows()

    missing = []
    for p in REQUIRED_PACKAGES:
        if importlib.util.find_spec(p) is None:
            missing.append(p)

    if not missing:
        return

    print("Installing missing Python packages:", missing)
    
    # Pip aktualisieren
    try:
        subprocess.check_call([sys.executable, "-m", "pip", "install", "--upgrade", "pip"])
    except Exception:
        print("[WARNUNG] Konnte pip nicht aktualisieren, fahre mit Paketinstallation fort...")

    # Fehlende Pakete installieren
    for p in missing:
        subprocess.check_call([sys.executable, "-m", "pip", "install", p])

    # Skript nach erfolgreicher Installation neu starten, damit die Module geladen werden können
    os.execv(sys.executable, [sys.executable] + sys.argv)

def fix_windows_path():
    """Fügt Standard-Installationspfade bekannter Tools zum System-PATH hinzu (Windows)."""
    if platform.system() != "Windows":
        return

    bases = [
        os.environ.get("ProgramFiles", "C:\\Program Files"),
        os.environ.get("ProgramFiles(x86)", "C:\\Program Files (x86)"),
        "C:\\Windows\\System32",
    ]
    subs = ["Git\\bin", "7-Zip", "Wireshark", "Nmap"]

    path = os.environ.get("PATH", "")
    added = []

    for b in bases:
        for s in subs:
            full = os.path.join(b, s)
            if os.path.isdir(full) and full not in path:
                added.append(full)

    if added:
        os.environ["PATH"] = os.pathsep.join(added) + os.pathsep + path

def check_tools():
    missing = []
    for t in REQUIRED_TOOLS:
        if not shutil.which(t):
            missing.append(t)
    if missing:
        print("Missing tools:", missing)

def is_admin():
    try:
        return ctypes.windll.shell32.IsUserAnAdmin()
    except Exception:
        return False

def raise_file_limit():
    """Erhöht das Limit für gleichzeitig geöffnete Dateien (Linux)."""
    try:
        import resource
        soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
        target = min(4096, hard)
        if soft < target:
            resource.setrlimit(resource.RLIMIT_NOFILE, (target, hard))
            print(f"[SYSTEM] File-Limit von {soft} auf {target} erhöht.")
    except ImportError:
        # Dummy-Klasse für Windows
        class resource:
            RLIMIT_NOFILE = 0
            @staticmethod
            def getrlimit(*args): return (0, 0)
            @staticmethod
            def setrlimit(*args): pass
    except Exception as e:
        print(f"[WARNUNG] Konnte File-Limit nicht erhöhen: {e}")

# ============================================================
# SETTINGS
# ============================================================

def load_settings():
    """Lädt die Einstellungen einmalig in den Cache."""
    global _SETTINGS_CACHE
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r") as f:
                _SETTINGS_CACHE = json.load(f)
        except Exception:
            _SETTINGS_CACHE = {}

def get_setting(key, default=True):
    return _SETTINGS_CACHE.get(key, default)

def save_setting(key, value):
    _SETTINGS_CACHE[key] = value
    try:
        with open(CONFIG_FILE, "w") as f:
            json.dump(_SETTINGS_CACHE, f, indent=4)
    except Exception as e:
        print(f"Fehler beim Speichern: {e}")

# ============================================================
# FONT INSTALLER
# ============================================================
def auto_install_emoji_font():
    """Installiert Noto Color Emoji plattformübergreifend."""
    def _run_install():
        url = "https://github.com/speedy005/Oscam-Emu-patch-Manager/raw/master/Noto_Color_Emoji.zip"
        os_type = platform.system()
        try:
            print("[SYSTEM] Prüfe Emoji-Schriftart...")
            import requests

            response = requests.get(url, timeout=20)
            if response.status_code != 200:
                return

            with zipfile.ZipFile(io.BytesIO(response.content)) as z:
                ttf_name = next((f for f in z.namelist() if f.endswith(".ttf")), None)
                if not ttf_name: return
                font_data = z.read(ttf_name)

            if os_type == "Windows":
                font_dir = os.path.join(os.environ['LOCALAPPDATA'], "Microsoft", "Windows", "Fonts")
                os.makedirs(font_dir, exist_ok=True)
                target_path = os.path.join(font_dir, "NotoColorEmoji.ttf")
                if not os.path.exists(target_path):
                    with open(target_path, "wb") as f:
                        f.write(font_data)
                    ctypes.windll.gdi32.AddFontResourceW(target_path)
                    ctypes.windll.user32.SendMessageW(0xFFFF, 0x001D, 0, 0)
                    print("[✓] Font installiert (Windows User-Level)")

            elif os_type == "Linux":
                font_dir = os.path.expanduser("~/.local/share/fonts")
                os.makedirs(font_dir, exist_ok=True)
                target_path = os.path.join(font_dir, "NotoColorEmoji.ttf")
                if not os.path.exists(target_path):
                    with open(target_path, "wb") as f:
                        f.write(font_data)
                    subprocess.run(["fc-cache", "-f"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    print("[✓] Font installiert (Linux Local-Level)")

        except Exception as e:
            print(f"[!] Font-Installation Fehler: {e}")

    threading.Thread(target=_run_install, daemon=True).start()

# ============================================================
# TOOL VERIFICATION
# ============================================================

def verify_tools(tools_to_check):
    """Prüft Tools auf PATH und Startbarkeit."""
    results = {}
    is_win = platform.system() == "Windows"

    check_commands = {
        "git": ["git", "--version"],
        "nmap": ["nmap", "--version"],
        "wireshark": ["capinfos", "-h"],
        "7z": ["7z", "-h"],
        "zip": ["7z", "-h"],
        "patch": ["patch", "--version"],
        "ssh": ["ssh", "-V"],
        "hashcat": ["hashcat", "--version"] if shutil.which("hashcat") else ["hashcat64", "--version"]
    }

    print("\n[ ] Starte Funktionstest der Tools...")

    for tool in tools_to_check:
        cmd = check_commands.get(tool, [tool, "--version"])
        if not shutil.which(cmd[0]):
            results[tool] = False
            print(f"[!] {tool} nicht im PATH gefunden.")
            continue
        try:
            process = subprocess.run(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=5,
                creationflags=subprocess.CREATE_NO_WINDOW if is_win else 0
            )
            results[tool] = process.returncode in [0, 1]
            print(f"{'[✓]' if results[tool] else '[!]'} {tool}")
        except (subprocess.TimeoutExpired, OSError):
            results[tool] = False
            print(f"[!] {tool} Fehler: Start fehlgeschlagen.")
    return results

# ============================================================
# DEPENDENCIES & SOUND
# ============================================================

def ensure_dependencies():
    """Installiert fehlende Python-Pakete und prüft optional Sound."""
    global HAS_SOUND_SUPPORT

    missing = [p for p in REQUIRED_PACKAGES if importlib.util.find_spec(p) is None]
    if missing:
        print("Missing python packages:", missing)
        subprocess.check_call([sys.executable, "-m", "pip", "install", "--upgrade", "pip"])
        for p in missing:
            subprocess.check_call([sys.executable, "-m", "pip", "install", p])
        os.execv(sys.executable, [sys.executable] + sys.argv)

    # optional sound
    # Sound-Unterstützung ist bewusst plattformspezifisch.
    # winsound darf niemals unter Linux importiert werden.
    if platform.system() == "Linux":
        HAS_SOUND_SUPPORT = shutil.which("paplay") is not None
    elif platform.system() == "Windows":
        HAS_SOUND_SUPPORT = True
    else:
        HAS_SOUND_SUPPORT = False

    return True

# ============================================================
# MAIN BOOTSTRAP
# ============================================================

def bootstrap():
    check_python()
    ensure_pip()
    ensure_dependencies()
    fix_windows_path()
    check_tools()
    load_settings()
    auto_install_emoji_font()
    if platform.system() == "Linux":
        raise_file_limit()
    print("[SYSTEM] Bootstrap abgeschlossen ✅")
    
from datetime import datetime, timezone
from PyQt6.QtGui import QFont, QColor, QTextCursor, QIcon
from PyQt6.QtWidgets import (
    QApplication,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QPushButton,
    QTextEdit,
    QLabel,
    QMessageBox,
    QDialog,
    QFormLayout,
    QLineEdit,
    QDialogButtonBox,
    QFileDialog,
    QGroupBox,
    QSizePolicy,
    QCheckBox,
)
from PyQt6.QtCore import Qt, QTimer, QDateTime, QSize
import subprocess
import sys
import importlib.util
from PyQt6.QtWidgets import QMessageBox

from PyQt6.QtCore import QThread, pyqtSignal, QPropertyAnimation, QEasingCurve
from PyQt6.QtWidgets import QGraphicsColorizeEffect

