"""OSCam Patch Manager module fragment: Application configuration persistence."""

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
from workers import safe_play

def save_config(cfg_updates, gui_instance=None, silent=False):
    """
    Speichert Config-Updates und synchronisiert Timer, ProgressBar, Theme sowie S3, S4 & NCam Pfade.
    Pfade werden plattformunabhängig für Windows 11 und Linux normalisiert.
    FIX: Verhindert das Vertauschen und ungewollte Grün-Werden falscher Buttons beim S4-Update.
    """
    import os, json, platform
    from PyQt6.QtCore import QTimer

    # Sicherstellen, dass CONFIG_FILE global bekannt ist
    CONFIG_FILE = globals().get("CONFIG_FILE", "config.json")

    try:
        # 0. Eine einzige, klare Schnittstelle: Updates müssen ein Dictionary sein.
        if cfg_updates is None:
            cfg_updates = {}
        if not isinstance(cfg_updates, dict):
            raise TypeError(
                f"save_config erwartet ein dict, erhalten: {type(cfg_updates).__name__}"
            )
        cfg_updates = dict(cfg_updates)

        # 1. Bestehende Config laden
        current_cfg = {}
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                    current_cfg = json.load(f)
            except Exception:
                current_cfg = {}

        # --- OS-SCHUTZ: Pfade vor dem Mergen plattformunabhängig säubern ---
        is_win = platform.system() == "Windows"
        path_keys = [
            "work_dir",
            "s3_custom_path",
            "ncam_custom_path",
            "s3_patch_path",
            "ncam_patch_path",
            "s4_patch_path",
            "s4_custom_path",
        ]
        
        for key in list(cfg_updates.keys()):
            if key in path_keys and isinstance(cfg_updates[key], str):
                path_val = cfg_updates[key]
                # Pfade nicht künstlich zwischen Betriebssystemen umschreiben.
                # Ein Windows-Pfad darf unter Linux in der Config sichtbar bleiben,
                # statt durch das Entfernen von "C:" beschädigt zu werden.
                if is_win:
                    path_val = os.path.normpath(path_val)
                cfg_updates[key] = path_val

        # 2. Mergen der neuen Updates
        current_cfg.update(cfg_updates)

        # 3. Globale Variablen setzen (mit korrigiertem simplebuild4 Fallback)
        default_s3 = "C:\\s3" if is_win else "/opt/s3"
        default_ncam = "C:\\opt\\ncam" if is_win else "/opt/s3_ncam_bonecrew"
        default_s4 = "C:\\opt\\simplebuild4" if is_win else "/opt/simplebuild4"

        globals()["S3_PATH"] = current_cfg.get("s3_custom_path", default_s3)
        globals()["NCAM_PATH"] = current_cfg.get("ncam_custom_path", default_ncam)
        globals()["S4_PATH"] = current_cfg.get("s4_custom_path", default_s4)
        globals()["THEME_MODE"] = current_cfg.get("theme_mode", "standard")
        globals()["BLINK_SPEED"] = current_cfg.get("blink_speed", 500)

        # 4. System-Werte & Pfad-Synchronisation
        if gui_instance:
            # Wir laden die Pfade IMMER frisch aus der Gesamt-Config für das GUI-Objekt.
            gui_instance.S3_PATH = current_cfg.get("s3_custom_path", default_s3)
            gui_instance.NCAM_PATH = current_cfg.get("ncam_custom_path", default_ncam)
            gui_instance.S4_PATH = current_cfg.get("s4_custom_path", default_s4)

            # Timer-Logik für LEDs/Blinken
            blink_speed = current_cfg.get("blink_speed", 500)
            led_globally_on = current_cfg.get("led_enabled", True)

            timer = getattr(
                gui_instance, "master_timer", getattr(gui_instance, "blink_timer", None)
            )
            if timer:
                if (
                    getattr(gui_instance, "is_loading", False)
                    or blink_speed >= 950
                    or not led_globally_on
                ):
                    timer.stop()
                    if hasattr(gui_instance, "force_user_leds_static"):
                        gui_instance.force_user_leds_static()
                else:
                    timer.setInterval(max(10, blink_speed))
                    if not timer.isActive():
                        timer.start()

            # --- THEME FIX ---
            # Theme nicht bei jedem beliebigen Config-Update neu anwenden.
            # Besonders wichtig bei Commit-Anzahl/Pfadänderungen: dadurch werden
            # keine unnötigen Repaints und keine QMainWindow-only Methoden aufgerufen.
            if "theme_mode" in cfg_updates or "color" in cfg_updates:
                theme = str(current_cfg.get("theme_mode", "standard")).lower()
                color = str(current_cfg.get("color", "Classics")).lower()

                if "matrix" in theme or "matrix" in color:
                    if hasattr(gui_instance, "enable_matrix_theme"):
                        gui_instance.enable_matrix_theme()
                    current_cfg["theme_mode"] = "matrix"
                else:
                    if hasattr(gui_instance, "enable_standard_theme"):
                        gui_instance.enable_standard_theme()
                    current_cfg["theme_mode"] = "standard"

        # 5. Speichern in die Datei
        with open(os.path.abspath(CONFIG_FILE), "w", encoding="utf-8") as f:
            json.dump(current_cfg, f, indent=4, ensure_ascii=False)

        # 6. UI & Feedback Logik
        if gui_instance:
            # Beide Attribute müssen auf dasselbe Dictionary zeigen.
            # Sonst arbeitet ein Teil der GUI mit einer veralteten Config.
            gui_instance.current_config = current_cfg
            gui_instance.cfg = current_cfg
            is_loading = getattr(gui_instance, "is_loading", False)
            is_closing = getattr(gui_instance, "is_closing", False)

            if not is_loading and not silent:
                if "safe_play" in globals() and not is_closing:
                    safe_play("dialog-information.oga")

                lang = getattr(gui_instance, "LANG", "de").lower()[:2]

                if is_closing:
                    msg = "✅ Beendet & Gespeichert" if lang == "de" else "✅ Exit & Saved"
                    log_color = "#FFD700"
                    pbar_style = (
                        "QProgressBar::chunk {"
                        "background: qlineargradient(x1:0, y1:0, x2:1, y2:0, "
                        "stop:0.0 #FF0000, stop:0.2 #FF7F00, stop:0.4 #FFFF00, "
                        "stop:0.6 #00FF00, stop:0.8 #0000FF, stop:1.0 #8B00FF); "
                        "border-radius:5px;}"
                    )
                else:
                    msg = "✅ Einstellungen gespeichert" if lang == "de" else "✅ Settings saved"
                    log_color = "#00FF41" if "matrix" in str(current_cfg.get("theme_mode")).lower() else "#00FFFF"
                    pbar_style = "QProgressBar::chunk { background-color: #2ecc71; border-radius: 5px; }"

                pbar = getattr(gui_instance, "progress_bar", None)
                if pbar:
                    pbar.setValue(100)
                    pbar.setFormat(msg)
                    pbar.setStyleSheet(
                        f"QProgressBar {{ text-align: center; color: black; font-weight: 900; background: #111; border: 1px solid #333; }} {pbar_style}"
                    )
                    if not is_closing:
                        if hasattr(gui_instance, "pbar_idle"):
                            QTimer.singleShot(3000, gui_instance.pbar_idle)
                        else:
                            QTimer.singleShot(3000, lambda: pbar.setStyleSheet(""))

                if hasattr(gui_instance, "log_message"):
                    gui_instance.log_message(
                        f"<span style='color:{log_color}; font-weight:700;'><b>{msg}</b></span>"
                    )

        return True

    except Exception as e:
        print(f"Fehler beim Speichern: {e}")
        return False

# ===================== CONFIG =====================
def load_config(gui_instance=None):
    """
    Lädt die Config, korrigiert Pfade für Windows/Linux, ergänzt fehlende Keys
    und synchronisiert die GUI. Optimiert für Oracle VM, Linux & Windows.
    FIX: Verhindert, dass Pfad-Normalisierungen beim Start die Buttons auf Orange zurückwerfen.
    """
    import os, json, platform
    from PyQt6.QtCore import QTimer

    is_win = platform.system() == "Windows"
    CORRECT_URL = "https://github.com/oscam-mirror/oscam-emu.git"
    CONFIG_FILE = globals().get("CONFIG_FILE", "config.json")
    
    base_patch_dir = globals().get(
        "OLD_PATCH_DIR", os.path.dirname(os.path.abspath(__file__))
    )

    # --- Dynamische Standard-Pfade je nach OS ---
    default_s3 = "C:\\s3" if is_win else "/opt/s3"
    default_ncam = "C:\\opt\\ncam" if is_win else "/opt/s3_ncam_bonecrew"
    default_s4 = "C:\\opt\\simplebuild4" if is_win else "/opt/simplebuild4"

    
    default_cfg = {
        "commit_count": 5,

        # Commit-Verlauf
        "last_stream_commit": "",
        "previous_stream_commit": "",

        "color": "Classics",
        "language": "de",
        "s3_patch_path": os.path.normpath(base_patch_dir),
        "ncam_patch_path": os.path.normpath(base_patch_dir),
        "work_dir": os.getcwd(),
        "s4_patch_path": os.path.normpath(base_patch_dir),
        "s3_custom_path": default_s3,
        "ncam_custom_path": default_ncam,
        "s4_custom_path": default_s4,
        "s3_repository_name": "S3 Standard (Original)",
        "patch_modifier": "speedy005",
        "EMUREPO": CORRECT_URL,
        "theme_mode": "standard",
        "blink_speed": 500,
        "led_enabled": True,
    }


    # --- Config-Datei erstellen, falls nicht vorhanden ---
    if not os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(default_cfg, f, indent=4, ensure_ascii=False)
        except Exception as e:
            print(f"⚠️ Fehler beim Erstellen der Config: {e}")
        return default_cfg.copy()

    try:
        # --- Config laden ---
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            cfg = json.load(f)

        if not isinstance(cfg, dict):
            cfg = default_cfg.copy()

                # --- Fehlende Keys ergänzen & Pfade normalisieren ---
        needs_save = False

        path_keys = [
            "work_dir",
            "s3_custom_path",
            "ncam_custom_path",
            "s3_patch_path",
            "ncam_patch_path",
            "s4_patch_path",
            "s4_custom_path",
        ]

        # Fehlende Standardwerte ergänzen
        for key, value in default_cfg.items():
            if key not in cfg:
                cfg[key] = value
                needs_save = True

        # Pfade unabhängig davon normalisieren, ob der Key neu ist
        for key in path_keys:
            path_value = cfg.get(key)

            if not isinstance(path_value, str) or not path_value.strip():
                continue

            is_windows_drive_path = (
                len(path_value) >= 3
                and path_value[1] == ":"
                and path_value[2] in ("\\", "/")
            )

            # Windows-Pfade unter Linux unverändert erhalten
            if not is_win and is_windows_drive_path:
                cleaned_path = path_value
            else:
                cleaned_path = os.path.normpath(path_value)

            if path_value != cleaned_path:
                cfg[key] = cleaned_path
                needs_save = True

        # --- Korrektur der EMUREPO URL ---
        current_repo = str(cfg.get("EMUREPO", "")).strip()
        if len(current_repo) < 8:
            cfg["EMUREPO"] = CORRECT_URL
            needs_save = True

        if needs_save:
            try:
                with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                    json.dump(cfg, f, indent=4, ensure_ascii=False)
            except Exception as e:
                print(f"⚠️ Fehler beim Speichern der Config: {e}")

        # --- Globale Variablen setzen ---
        globals()["EMUREPO"] = cfg["EMUREPO"]
        globals()["PATCH_MODIFIER"] = cfg["patch_modifier"]
        globals()["THEME_MODE"] = cfg.get("theme_mode", "standard")
        globals()["BLINK_SPEED"] = cfg.get("blink_speed", 500)
        globals()["S3_PATH"] = cfg["s3_custom_path"]
        globals()["NCAM_PATH"] = cfg["ncam_custom_path"]
        globals()["S4_PATH"] = cfg["s4_custom_path"]
        globals()["NCAM_PATCH_PATH"] = cfg.get("ncam_patch_path", cfg.get("s3_patch_path", base_patch_dir))
        globals()["S4_PATCH_PATH"] = cfg.get("s4_patch_path", cfg.get("s3_patch_path", base_patch_dir))

        # --- GUI-Integration ---
        if gui_instance:
            # ERZWUNGEN: Wir befüllen die Instanz-Config als ALLERERSTES,
            # damit update_ui_texts beim Zeichnen sofort darauf zugreifen kann!
            gui_instance.current_config = cfg
            gui_instance.S3_PATH = cfg["s3_custom_path"]
            gui_instance.NCAM_PATH = cfg["ncam_custom_path"]
            gui_instance.S4_PATH = cfg["s4_custom_path"]

            # Buttons Höhe setzen
            button_height = getattr(gui_instance, "BUTTON_HEIGHT", 40)
            if hasattr(gui_instance, "all_buttons"):
                for btn in gui_instance.all_buttons:
                    btn.setFixedHeight(button_height)

            # --- THEME FIX ---
            theme_color = str(cfg.get("color", "Classics")).lower()
            theme_mode = str(cfg.get("theme_mode", "standard")).lower()

            if "matrix" in theme_color or "matrix" in theme_mode:
                if hasattr(gui_instance, "enable_matrix_theme"):
                    gui_instance.enable_matrix_theme()
            else:
                if hasattr(gui_instance, "enable_standard_theme"):
                    gui_instance.enable_standard_theme()

            # LEDs / Blink-Timer synchronisieren
            led_enabled = cfg.get("led_enabled", True)
            if hasattr(gui_instance, "toggle_leds"):
                gui_instance.toggle_leds(led_enabled)
            
            blink_speed = cfg.get("blink_speed", 500)
            timer = getattr(gui_instance, "master_timer", getattr(gui_instance, "blink_timer", None))
            
            if timer:
                timer.setInterval(max(10, blink_speed))
                if led_enabled and not timer.isActive():
                    timer.start()
                elif not led_enabled:
                    timer.stop()
                    if hasattr(gui_instance, "force_user_leds_static"):
                        gui_instance.force_user_leds_static()

            # Startet die Textaktualisierung sicher nach 50ms, wenn der Splash-Screen die GUI freigibt
            if hasattr(gui_instance, "update_ui_texts"):
                QTimer.singleShot(50, gui_instance.update_ui_texts)

        return cfg

    except Exception as e:
        print(f"⚠️ Kritischer Config Fehler: {e}")
        return default_cfg.copy()

# ===================== INFOSCREEN =====================
