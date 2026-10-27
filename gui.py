"""OSCam Patch Manager module fragment: Main Patch Manager GUI."""
from PyQt6.QtCore import QEvent, QObject
from PyQt6.QtWidgets import (
    QApplication,
    QPushButton,
    QToolButton,
    QAbstractButton,
)
import math
import os
import struct
import tempfile
import wave
import shlex


class ResponsiveGridReflow(QObject):
    """Reflow a grid of existing buttons whenever its container is resized."""

    def __init__(self, container, layout, min_item_width=205, parent=None):
        super().__init__(parent or container)
        self.container = container
        self.layout = layout
        self.min_item_width = min_item_width
        self._queued = False
        self._last_columns = 0
        container.installEventFilter(self)
        QTimer.singleShot(0, self.reflow)

    def eventFilter(self, obj, event):
        if obj is self.container and event.type() == QEvent.Type.Resize:
            if not self._queued:
                self._queued = True
                QTimer.singleShot(0, self._run_queued_reflow)
        return super().eventFilter(obj, event)

    def _run_queued_reflow(self):
        self._queued = False
        self.reflow()

    def reflow(self):
        if self.container is None or self.layout is None:
            return
        width = max(1, self.container.contentsRect().width())
        spacing = max(0, self.layout.spacing())
        columns = max(1, (width + spacing) // (self.min_item_width + spacing))
        widgets = []
        while self.layout.count():
            item = self.layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widgets.append(widget)
        if not widgets:
            return
        columns = min(columns, len(widgets))
        for index, widget in enumerate(widgets):
            self.layout.addWidget(widget, index // columns, index % columns)
        for col in range(max(columns, self._last_columns)):
            self.layout.setColumnStretch(col, 1 if col < columns else 0)
        self._last_columns = columns


class ButtonClickSoundFilter(QObject):
    """Spielt beim Mausklick auf einen Button den Klickton ab."""

    def __init__(self, sound_manager, parent=None):
        super().__init__(parent)
        self.sound_manager = sound_manager

    def eventFilter(self, obj, event):
        try:
            if (
                event.type() == QEvent.Type.MouseButtonPress
                and isinstance(obj, QAbstractButton)
                and obj.isEnabled()
                and self.sound_manager
                and self.sound_manager.enabled
            ):
                sound = getattr(self.sound_manager, "click_sound", None)
                if sound is not None:
                    sound.play()
        except Exception as e:
            print(f"[WARN] Button-Klicksound fehlgeschlagen: {e}")

        return super().eventFilter(obj, event)

from PyQt6.QtCore import QObject, QTimer, QUrl, Qt
from PyQt6.QtMultimedia import QSoundEffect


class SystemSoundManager(QObject):
    """Einheitlicher, dateibasierter Soundmanager für Windows und Linux.

    Alle Sounds werden als PCM-WAV erzeugt und über Qt Multimedia abgespielt.
    Dadurch hängen normale UI-Sounds nicht von Windows-Soundaliasen oder
    Linux-spezifischen OGA-Dateien/Playern ab.
    """

    def __init__(self, parent=None, volume=0.25):
        super().__init__(parent)
        self.enabled = True
        self.volume = self._clamp_volume(volume)
        self._temp_dir = tempfile.mkdtemp(prefix="oscam_sounds_")
        self._effects = {}
        self._closed = False

        # Tonfolgen: (Frequenz, Dauer in ms); None erzeugt eine kurze Pause.
        patterns = {
            "start": [(523.25, 95), (659.25, 95)],
            "success": [(659.25, 85), (783.99, 85), (1046.50, 130)],
            "revision": [(880.00, 65)],
            "exit": [(783.99, 100), (659.25, 100), (523.25, 150)],
            "click": [(1046.50, 80)],
            "info": [(740.00, 70)],
            "warning": [(660.00, 90), (520.00, 120)],
            "error": [(440.00, 130), (330.00, 180)],
            "login": [(523.25, 65), (783.99, 100)],
            "logout": [(783.99, 65), (523.25, 100)],
        }
        for name, pattern in patterns.items():
            self._effects[name] = self._create_effect(name, pattern)

        # Rückwärtskompatible Attribute, die an anderen Stellen direkt
        # verwendet werden (z. B. success_sound.play()).
        self.start_sound = self._effects["start"]
        self.success_sound = self._effects["success"]
        self.revision_sound = self._effects["revision"]
        self.exit_sound = self._effects["exit"]
        self.click_sound = self._effects["click"]

        self.revision_timer = QTimer(self)
        self.revision_timer.setTimerType(Qt.TimerType.PreciseTimer)
        self.revision_timer.setInterval(1200)
        self.revision_timer.timeout.connect(self.play_revision)

    @staticmethod
    def _clamp_volume(volume):
        try:
            return max(0.0, min(1.0, float(volume)))
        except (TypeError, ValueError):
            return 0.25

    def _write_tone(self, path, pattern):
        """Schreibt 16-bit-PCM-WAV mit kurzen Fade-ins/-outs."""
        sample_rate = 22050
        frames = bytearray()
        for frequency, duration_ms in pattern:
            count = max(1, int(sample_rate * duration_ms / 1000))
            if frequency is None:
                frames.extend(b"\x00\x00" * count)
                continue
            for i in range(count):
                fade_in = min(1.0, i / max(1, int(sample_rate * 0.008)))
                fade_out = min(1.0, (count - 1 - i) / max(1, int(sample_rate * 0.012)))
                envelope = max(0.0, min(fade_in, fade_out))
                sample = int(6500 * envelope * math.sin(2 * math.pi * frequency * i / sample_rate))
                frames.extend(struct.pack("<h", sample))
            frames.extend(b"\x00\x00" * int(sample_rate * 0.018))
        with wave.open(path, "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(sample_rate)
            wav.writeframes(frames)


    def _create_effect(self, name, pattern):
        path = os.path.join(self._temp_dir, f"{name}.wav")
        self._write_tone(path, pattern)

        effect = QSoundEffect(self)
        effect.setSource(QUrl.fromLocalFile(path))

        # Klickton lauter als die übrigen UI-Sounds.
        volume = min(1.0, self.volume * 2.0) if name == "click" else self.volume
        effect.setVolume(volume)

        effect.statusChanged.connect(
            lambda e=effect, n=name: self._log_effect_status(n, e)
        )
        return effect


    @staticmethod
    def _log_effect_status(name, effect):
        # Debug-Ausgabe nur bei Ladefehlern; kein Log-Spam beim normalen Betrieb.
        try:
            if effect.status() == QSoundEffect.Status.Error:
                print(f"[SOUND] Sound '{name}' konnte nicht geladen werden.")
        except Exception:
            pass

    def set_enabled(self, enabled):
        self.enabled = bool(enabled)
        if not self.enabled:
            self.stop_revision_blink_sound()
            for effect in self._effects.values():
                try:
                    effect.stop()
                except Exception:
                    pass

    def set_volume(self, volume):
        self.volume = self._clamp_volume(volume)
        for effect in self._effects.values():
            effect.setVolume(self.volume)

    def play(self, name):
        """Spielt einen benannten Sound; unbekannte Namen werden ignoriert."""
        if not self.enabled or self._closed:
            return False
        effect = self._effects.get(str(name).lower())
        if effect is None:
            return False
        try:
            effect.stop()
            effect.play()
            return True
        except Exception as exc:
            print(f"[SOUND] Abspielen von '{name}' fehlgeschlagen: {exc}")
            return False

    def play_start(self): return self.play("start")
    def play_success(self): return self.play("success")
    def play_revision(self): return self.play("revision")
    def play_error(self): return self.play("error")
    def play_info(self): return self.play("info")
    def play_warning(self): return self.play("warning")
    def play_login(self): return self.play("login")
    def play_logout(self): return self.play("logout")

    def play_legacy_name(self, filename):
        """Übersetzt bisherige Freedesktop-Dateinamen in portable Sounds."""
        name = os.path.basename(str(filename)).lower()
        if any(word in name for word in ("error", "failed", "failure")):
            return self.play_error()
        if "warning" in name or "exclamation" in name:
            return self.play_warning()
        if "login" in name or "service-start" in name:
            return self.play_login()
        if "logout" in name or "service-stop" in name:
            return self.play_logout()
        if "complete" in name or "success" in name:
            return self.play_success()
        if "information" in name or "asterisk" in name:
            return self.play_info()
        if "button" in name or "click" in name:
            return self.play("click")
        return self.play_info()

    def start_revision_blink_sound(self):
        # Der Ton wird beim Blink-Ereignis direkt ausgelöst, nicht per Timer.
        self.revision_timer.stop()

    def stop_revision_blink_sound(self):
        self.revision_timer.stop()

    def shutdown(self):
        if self._closed:
            return
        self._closed = True
        self.revision_timer.stop()
        for effect in self._effects.values():
            try:
                effect.stop()
            except Exception:
                pass
        try:
            import shutil
            shutil.rmtree(self._temp_dir, ignore_errors=True)
        except Exception:
            pass


_ACTIVE_SOUND_MANAGER = None


def safe_play(filename):
    """Kompatibler Ersatz für workers.safe_play, zentral und plattformneutral."""
    manager = _ACTIVE_SOUND_MANAGER
    if manager is not None:
        return manager.play_legacy_name(filename)
    # Falls vor Erstellung der GUI ein Sound angefordert wird, einfach still bleiben.
    return False


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

from bootstrap import ONLINE_PATCHES, ensure_dependencies, get_setting, save_setting
from config_manager import load_config, save_config
from dialogs_splash import GithubConfigDialog, TaskWorker
from git_manager import find_git_executable, get_oscam_version, git_command, git_commit_id
from github_patch_upload import github_upload_patch_file
from github_repo_upload import github_upload_oscam_emu_folder, run_bash
from patch_format import load_github_config
from patch_operations import backup_old_patch, clean_patch_folder, create_patch, ensure_git_repository, patch_oscam_emu_git
from paths import ALT_PATCH_FILE, APP_VERSION, CONFIG_FILE, OLD_PATCH_DIR, OLD_PATCH_FILE, PATCH_FILE, PATCH_EMU_GIT_DIR, STREAMREPO, STREAM_REPO_CHOICES, TEMP_REPO, get_selected_stream_repo_name, get_selected_stream_repo_url, now
from system_check import SystemCheckWorker
from themes import DIFF_COLORS, current_color_name, current_diff_colors
from translations import LANG, TEXTS
from ui_components import StreamToGui
from workers import NcamBonecrewInstallWorker, OSCamUpdateWorker, S3InstallWorker, S4InstallWorker
from packaging.version import Version

class PatchManagerGUI(QWidget):
    def __init__(self):
        # 1. IMPORTS & INITIALER SCHUTZ
        from PyQt6.QtGui import QFont, QFontMetrics
        from PyQt6.QtGui import QColor, QFont, QTextCursor, QIcon
        from PyQt6.QtCore import Qt, QTimer, QDateTime, QSize, QUrl
        from PyQt6.QtWidgets import QLabel, QTextEdit, QVBoxLayout, QComboBox
        import subprocess, platform, shutil, os, re, sys

        # Build-Log-Verzeichnis bereits beim Programmstart anlegen. So ist
        # sichtbar, ob genau diese gui.py ausgeführt wird, auch wenn später
        # kein S3/S4/NCam-Terminal gestartet werden kann.
        try:
            self.build_log_dir = os.path.join(
                os.path.dirname(os.path.abspath(__file__)), "build_logs"
            )
            os.makedirs(self.build_log_dir, exist_ok=True)
        except OSError as log_setup_error:
            self.build_log_dir = None
            print(f"[BUILD-LOG] Verzeichnis konnte nicht angelegt werden: {log_setup_error}")

        self.is_loading = True  # Schutz vor Timer während Setup

        # 2. ATTR-BOOTSTRAP
        self.btn_matrix = None
        self.slider_speed = None
        self.color_box = None
        self.status_leds = []  # System-Status LEDs
        self.user_leds = []  # User-Blink-LEDs
        self.all_buttons = []
        self.option_buttons = {}
        self.buttons = {}

        # Keine feste Revision beim Programmstart.
        # Create Patch verwendet dadurch automatisch den aktuellen
        # Streamboard-HEAD und den aktuellen Stand des gewählten
        # OSCam-Emu-Repositories, solange der Benutzer nichts auswählt.
        self.selected_streamboard_commit = None
        self.selected_emu_commit = None
        self.selected_emu_commit_repo_url = ""
        # Nur Anzeige des zuletzt tatsächlich gebauten Patches.
        # Diese Werte sind KEINE feste Auswahl und werden bei Create Patch
        # nicht als Basis erzwungen.
        self.display_streamboard_commit = None
        self.display_emu_commit = None
        globals()["SELECTED_STREAMBOARD_COMMIT"] = ""
        globals()["SELECTED_EMU_COMMIT"] = ""
        globals()["SELECTED_EMU_COMMIT_REPO_URL"] = ""

        self._blink_state = True

        super().__init__()
        self.current_config = self.load_config_from_file()
        self.s3_repository_options = {
            "S3 Simplebuild (Original)": "https://github.com/gorgone/s3_releases.git",
            "S3 Simplebuild speeddy005": "https://github.com/speedy005/speedy005-S3-Simplebuild.git",
        }

        self.selected_s3_repository = "S3 Simplebuild (Original)"
        self.s3_repo_combo = None
        self.s3_install_worker = None
        self.sound_manager = SystemSoundManager(self, volume=0.45)
        global _ACTIVE_SOUND_MANAGER
        _ACTIVE_SOUND_MANAGER = self.sound_manager
        app = QApplication.instance()
        if app is not None:
            app.aboutToQuit.connect(self.sound_manager.shutdown)
        self.sound_manager.play_login()
        # Globalen Klicksound-Filter registrieren
        self.button_click_sound_filter = ButtonClickSoundFilter(
            self.sound_manager,
            self
        )

        QApplication.instance().installEventFilter(
            self.button_click_sound_filter
        )
        # --- TIMER-SEKTION ---
        # 3. INFOSCREEN & REDIRECTOR FIX
        self.current_rev = getattr(
            self, "current_rev", "11943"
        )  # Falls nicht schon gesetzt
        self.info_text = QTextEdit()
        self.info_text.setReadOnly(True)
        try:
            self.stdout_redir = StreamToGui()
            self.stdout_redir.callback = lambda msg: self.append_info(
                self.info_text, msg, "info"
            )
            sys.stdout = self.stdout_redir
            self.stderr_redir = StreamToGui()
            self.stderr_redir.callback = lambda msg: self.append_info(
                self.info_text, msg, "error"
            )
            sys.stderr = self.stderr_redir
        except Exception:
            pass

        
        # S3-Repository-Auswahl und Worker initialisieren
        self.s3_repository_options = {
            "S3 Simplebuild (Original)": "https://github.com/gorgone/s3_releases.git",
            "S3 Simplebuild speeddy005": "https://github.com/speedy005/speedy005-S3-Simplebuild.git",
        }

        self.selected_s3_repository = self.current_config.get(
            "s3_repository_name",
            "S3 Simplebuild (Original)"
        )

        
        self.current_config = self.load_config_from_file()

        self.s3_repository_options = {
            "S3 Simplebuild (Original)": "https://github.com/gorgone/s3_releases.git",
            "S3 Simplebuild speeddy005": "https://github.com/speedy005/speedy005-S3-Simplebuild.git",
        }

        self.selected_s3_repository = self.current_config.get(
            "s3_repository_name",
            "S3 Simplebuild (Original)"
        )

        if self.selected_s3_repository not in self.s3_repository_options:
            self.selected_s3_repository = "S3 Simplebuild (Original)"

        self.s3_install_worker = None

        # Die ComboBox hier erstellen und NICHT wieder auf None setzen.
        self.s3_repo_combo = QComboBox()

        for name, url in self.s3_repository_options.items():
            self.s3_repo_combo.addItem(name, url)

        index = self.s3_repo_combo.findText(self.selected_s3_repository)
        if index >= 0:
            self.s3_repo_combo.setCurrentIndex(index)

        self.s3_repo_combo.currentTextChanged.connect(
            self.on_s3_repository_changed
        )


        # ComboBox erst nach der Initialisierung erstellen
        self.s3_repo_combo = QComboBox()

        for name, url in self.s3_repository_options.items():
            self.s3_repo_combo.addItem(name, url)

        index = self.s3_repo_combo.findText(
            self.selected_s3_repository
        )

        if index >= 0:
            self.s3_repo_combo.setCurrentIndex(index)

        self.s3_repo_combo.currentTextChanged.connect(
            self.on_s3_repository_changed
        )

        if self.selected_s3_repository not in self.s3_repository_options:
            self.selected_s3_repository = "S3 Simplebuild (Original)"

        self.s3_repo_combo = None
        self.s3_install_worker = None

        ## --- Pfad für Standard S3 ---
        stored_s3 = self.current_config.get("s3_custom_path", "/opt/s3")
        self.S3_PATH = os.path.normpath(stored_s3)

        # --- Pfad für NCam Bonecrew (EIGENE VARIABLE!) ---
        stored_ncam = self.current_config.get(
            "ncam_custom_path", "/opt/s3_ncam_bonecrew_test"
        )
        self.NCAM_PATH = os.path.normpath(stored_ncam)

        # --- NEU: SOFORT-CHECK BEIM START ---
        # Wir triggern die Prüfung kurz nach dem Start, damit der Button
        # von "Install" auf "OK" springt, falls S3 gefunden wurde.
        QTimer.singleShot(100, self.update_ui_texts)

        stored_lang = str(self.current_config.get("language", "de")).lower()
        self.LANG = stored_lang if stored_lang in ["en", "de"] else "de"

        # TEXT muss VOR init_ui() verfügbar sein.
        # init_ui() erzeugt bereits Buttons/Labels und verwendet dafür
        # self.TEXT.get(...). update_language() wird erst nach init_ui()
        # aufgerufen und kann daher nicht die Erstinitialisierung übernehmen.
        self.TEXT = TEXTS.get(
            self.LANG,
            TEXTS.get("en", {})
        )

        self.patch_modifier = self.current_config.get("patch_modifier", "Default")
        self.base_dir = os.path.dirname(os.path.abspath(__file__))
        self.PLUGIN_DIR = self.base_dir  # Plugin-Verzeichnis

        stored_work_dir = self.current_config.get("work_dir", self.base_dir)
        if not isinstance(stored_work_dir, str) or not stored_work_dir.strip():
            stored_work_dir = self.base_dir

        self.WORK_DIR = os.path.normpath(stored_work_dir)
        self.TEMP_REPO = os.path.join(self.base_dir, "streamboard-git")  # Temp-Repo Ordner
        self.PATCH_EMU_GIT_DIR = os.path.join(
            self.base_dir, "oscam-emu-git"
        )  # Emu-Git Ordner
        current_path = self.current_config.get(
            "s3_patch_path", globals().get("OLD_PATCH_DIR", "./")
        )
        self.OLD_PATCH_DIR = os.path.normpath(current_path)
        self.OLD_PATCH_FILE = os.path.join(self.OLD_PATCH_DIR, "oscam-emu.patch")
        self.ALT_PATCH_FILE = os.path.join(self.OLD_PATCH_DIR, "oscam-emu.altpatch")
        # --- Direkt nach ALT_PATCH_FILE einfügen ---
        target_dirs = [
            self.PLUGIN_DIR,
            self.TEMP_REPO,
            self.PATCH_EMU_GIT_DIR,
            self.OLD_PATCH_DIR,
        ]

        for d in target_dirs:
            if d and not os.path.exists(d):
                try:
                    os.makedirs(d, exist_ok=True)
                except:
                    pass

        # Basis-Variablen
        self.active_button_key = ""
        self.main_grid_layout = None
        self.latest_version = (
            globals().get("APP_VERSION", "1.0.0").replace("v", "").strip()
        )
        self.BUTTON_RADIUS = 5

        # 5. HAUPT-UI AUFBAUEN
        self.live_indicator = QLabel()
        # Initialer Text
        self.live_indicator.setText(
            "<span style='color:#ff0000;'>●</span> <span style='color:#ffffff;'>LIVE | System Monitor</span>"
        )
        self.live_indicator.setStyleSheet(
            """
            font-weight: bold; 
            font-family: 'Courier New', monospace;
            font-size: 22px;
            background: transparent;
        """
        )

        self.init_ui()
        self._fit_all_button_texts()
        # 6. TIMER VORBEREITEN (Nur für User-LEDs, nicht starten)
        self.blink_timer = QTimer(self)
        self.blink_timer.timeout.connect(self.animate_status_bar)

        # 7. THEME & FARBEN
        saved_color = self.current_config.get("color", "Classics")
        if hasattr(self, "color_box") and self.color_box:
            self.color_box.blockSignals(True)
            index = self.color_box.findText(saved_color)
            if index != -1:
                self.color_box.setCurrentIndex(index)
            self.color_box.blockSignals(False)
            self.color_box.currentIndexChanged.connect(self.change_colors)
        self.color_box.setToolTip(
            "Farbschema der Oberfläche auswählen"
            if str(getattr(self, "LANG", self.cfg.get("language", "de"))).lower() in ("de", "0")
            else "Choose the interface color theme"
        )
        self.color_box.setToolTipDuration(10000)

        self.update_language()
        #self.change_colors()

        # Theme-Status ohne Blinken anwenden
        target_theme = self.current_config.get("theme_mode", "standard")
        if hasattr(self, "toggle_matrix"):
            self.toggle_matrix(force_state=target_theme)

        # ----------------------------
        # 8. FINALER SLIDER- & LED-FIX
        # ----------------------------
        current_speed = self.current_config.get("blink_speed", 0)

        if hasattr(self, "slider_speed") and self.slider_speed:
            self.slider_speed.blockSignals(True)
            self.slider_speed.setValue(current_speed)
            self.slider_speed.blockSignals(False)

            if hasattr(self, "update_label_only"):
                self.update_label_only(current_speed)

            if hasattr(self, "update_blink_speed"):
                self.slider_speed.valueChanged.connect(self.update_blink_speed)

        # System-Status LEDs immer statisch setzen
        if hasattr(self, "set_system_leds_static"):
            self.set_system_leds_static()

        # User-LED Timer beim Start stoppen
        if hasattr(self, "blink_timer"):
            self.blink_timer.stop()

        if hasattr(self, "update_blink_speed"):
            self.update_blink_speed(current_speed)

        if hasattr(self, "update_plugin_button_state"):
            self.update_plugin_button_state()

        # --- LED STATUS WIEDERHERSTELLEN ---
        # 1. Den Wert aus der geladenen Config holen (Standard: True)
        saved_led_enabled = self.current_config.get("led_enabled", True)

        # 2. Die Checkbox visuell synchronisieren (ohne Signale zu feuern)
        if hasattr(self, "led_checkbox"):
            self.led_checkbox.blockSignals(True)
            self.led_checkbox.setChecked(saved_led_enabled)
            self.led_checkbox.blockSignals(False)

        # 3. Die tatsächliche Blink-Logik/Timer starten oder stoppen
        # WICHTIG: Wir blockieren kurzzeitig das Speichern, damit beim Start
        # nicht unnötig in die config.json geschrieben wird (Performance & Loop-Schutz)
        self._block_config_save = True
        self.toggle_leds(saved_led_enabled)
        self._block_config_save = False

        # 4. Falls die LEDs aus sind, sofort den dunklen Zustand erzwingen
        if not saved_led_enabled:
            if hasattr(self, "force_user_leds_static"):
                self.force_user_leds_static()

        lang = getattr(self, "LANG", "de").lower()
        init_msg = (
            "Initialisiere Generator..."
            if lang == "de"
            else "Initializing Generator..."
        )

        # Styling: Rot (#FF0000), Schriftstärke 900 (Extra Bold)
        self.info_text.setHtml(
            f"<div style='margin: 20px; font-family: sans-serif; text-align: center;'>"
            f"<b style='color:#FF0000; font-size:22pt; font-weight:900;'>{init_msg}</b>"
            f"</div>"
        )
        # Ganz am Ende der __init__ einfügen:

        # 1. Wert aus Config holen
        saved_led_enabled = self.current_config.get("led_enabled", True)

        # 2. Checkbox finden und setzen (WICHTIG: Name prüfen!)
        # Falls die Checkbox bei dir anders heißt, hier anpassen:
        target_cb = getattr(self, "led_checkbox", None)
        if target_cb:
            target_cb.blockSignals(True)
            target_cb.setChecked(saved_led_enabled)
            target_cb.blockSignals(False)

        # 3. Die Logik final erzwingen
        self.toggle_leds(saved_led_enabled)

        # 4. Falls der Timer trotzdem läuft, obwohl er aus sein soll:
        if not saved_led_enabled and hasattr(self, "blink_timer"):
            self.blink_timer.stop()
            if hasattr(self, "force_user_leds_static"):
                self.force_user_leds_static()

        # 1. Willkommens-Info (500ms)
        # QTimer.singleShot(500, self.show_welcome_info)

        # 2. System-Check (2000ms) - Injiziert sich in den Anker
        QTimer.singleShot(2000, lambda: self.run_full_system_check(clear_log=False))

        # 3. Update-Check (6000ms) - Wartet, bis der Check oben fertig "eingezogen" ist
        QTimer.singleShot(4000, self.check_for_update_on_start)

        # 4. OSCam Monitor (8500ms)
        QTimer.singleShot(4500, self.start_oscam_update_check)
        self.init_button_signals()

        self.is_loading = False  # Initialisierung abgeschlossen

        # Beim Start direkt den gesamten verfügbaren Bildschirm nutzen.
        # singleShot stellt sicher, dass Qt das Fenster erst nach dem Aufbau
        # der Widgets und Layouts maximiert/vollflächig anzeigt.
        QTimer.singleShot(0, self.showFullScreen)

    # =====================================================================
    # ANIMATIONS-LOGIK (Verhindert AttributeError)
    # =====================================================================
    def pbar_idle(self):
        """
        Pulsier-Logik für die ProgressBar im Leerlauf.
        Features: Akku-Erkennung, Sleep-Timer (10 Min), Dynamic Style.
        """
        import psutil
        from PyQt6.QtCore import QVariantAnimation, QEasingCurve, QTimer

        pb = getattr(self, "progress_bar", None)
        if not pb or pb.value() > 0:
            return

        # 1. Energie-Status & Geschwindigkeit festlegen
        battery = psutil.sensors_battery()
        is_on_battery = battery.power_plugged is False if battery else False
        duration = 2800 if is_on_battery else 1500  # Langsamer im Akku-Modus

        # 2. Alte Animation & Timer säubern
        if hasattr(self, "_idle_anim"):
            self._idle_anim.stop()
        if hasattr(self, "_idle_sleep_timer"):
            self._idle_sleep_timer.stop()

        # 3. Sleep-Timer: Schaltet Animation nach 10 Min Inaktivität aus
        self._idle_sleep_timer = QTimer(self)
        self._idle_sleep_timer.setSingleShot(True)
        self._idle_sleep_timer.timeout.connect(
            lambda: self._idle_anim.stop() if hasattr(self, "_idle_anim") else None
        )
        self._idle_sleep_timer.start(600000)  # 10 Minuten (600.000 ms)

        # 4. Animation erstellen
        self._idle_anim = QVariantAnimation(self)
        self._idle_anim.setStartValue(30)  # Start-Helligkeit
        self._idle_anim.setEndValue(85)  # Ziel-Helligkeit
        self._idle_anim.setDuration(duration)
        self._idle_anim.setEasingCurve(QEasingCurve.Type.InOutSine)
        self._idle_anim.setLoopCount(-1)

        def update_style(value):
            if pb.value() == 0:
                color = current_diff_colors.get("fg", "#00FF00")
                pb.setStyleSheet(
                    f"""
                    QProgressBar {{
                        border: 1px solid {color};
                        border-radius: 8px;
                        background-color: #000;
                        text-align: center;
                        color: {color};
                        font-weight: bold;
                    }}
                    QProgressBar::chunk {{
                        background-color: {color};
                        opacity: {value / 100.0};
                        border-radius: 7px;
                    }}
                """
                )
            else:
                self._idle_anim.stop()  # Sofort-Stop bei Fortschritt

        self._idle_anim.valueChanged.connect(update_style)
        self._idle_anim.start()

    from PyQt6.QtMultimedia import QMediaPlayer, QAudioOutput
    from PyQt6.QtCore import QUrl

    def change_language(self, index=None):
        """Wechselt die Sprache, aktualisiert die UI und führt die Abschlussanimation aus."""
        import os
        import platform
        import re

        from PyQt6.QtCore import QTimer, QRect, Qt
        from PyQt6.QtGui import QFont, QColor, QPixmap
        from PyQt6.QtWidgets import QApplication, QGroupBox, QLabel

        # Schutz vor mehrfacher Ausführung
        if not getattr(self, "language_box", None):
            return
        if getattr(self, "_block_language_change", False):
            return
        self._block_language_change = True

        pbar = getattr(self, "progress_bar", None)
        finished = {"value": False}

        def log_error(stage, exc):
            print(f"[change_language] {stage}: {exc}")

        def release_lock():
            self._block_language_change = False

        def update_pbar(value, text):
            if pbar is not None:
                pbar.setValue(value)
                pbar.setFormat(f"{text} %p%")
            QApplication.processEvents()

        def strip_icons(text):
            return re.sub(r"^[^\w\s]+", "", str(text)).strip()

        def blink_widget(widget, times=6, interval=300):
            if widget is None:
                return
            widget.show()
            widget.raise_()
            state = {"count": 0}

            def toggle():
                widget.setVisible(not widget.isVisible())
                state["count"] += 1
                if state["count"] < times * 2:
                    QTimer.singleShot(interval, toggle)
                else:
                    widget.setVisible(True)

            toggle()

        def after_animation():
            """Aktualisiert die verbleibenden Labels und startet den Abschluss."""
            try:
                lang_dict = getattr(self, "TEXT", {}) or {}
                is_de = getattr(self, "LANG", "en") == "de"

                if hasattr(self, "commit_label"):
                    self.commit_label.setText(
                        lang_dict.get(
                            "commit_count_label",
                            "Anzahl Commits:" if is_de else "Commit Count:",
                        )
                    )

                if hasattr(self, "color_label"):
                    self.color_label.setText(
                        lang_dict.get("color_label", "Farbe:" if is_de else "Color:")
                    )

                if hasattr(self, "log_button"):
                    self.log_button.setText(
                        lang_dict.get(
                            "log_button_text",
                            " Log speichern" if is_de else " Save Log",
                        )
                    )

                # Live-Konsole: neue Werkzeugbuttons der gewählten Sprache anpassen.
                if hasattr(self, "copy_log_button"):
                    self.copy_log_button.setText(
                        lang_dict.get("copy_log_button", "LOG KOPIEREN" if is_de else "COPY LOG")
                    )
                    self.copy_log_button.setToolTip(
                        lang_dict.get(
                            "copy_log_tooltip",
                            "Gesamte sichtbare Konsolenausgabe in die Zwischenablage kopieren"
                            if is_de else "Copy all visible console output to the clipboard",
                        )
                    )
                if hasattr(self, "clear_console_button"):
                    self.clear_console_button.setText(
                        lang_dict.get("clear_console_button", "KONSOLE LEEREN" if is_de else "CLEAR CONSOLE")
                    )
                    self.clear_console_button.setToolTip(
                        lang_dict.get(
                            "clear_console_tooltip",
                            "Nur die sichtbare Konsolenausgabe leeren; Logdateien bleiben unverändert"
                            if is_de else "Clear only the visible console output; log files remain unchanged",
                        )
                    )
                # BUILD-LOG-Analyse und Systemdiagnose ebenfalls live umschalten.
                if hasattr(self, "analyze_build_logs_button"):
                    self.analyze_build_logs_button.setText(
                        lang_dict.get("analyze_build_logs_button", "BUILD-LOG ANALYSIEREN" if is_de else "ANALYZE BUILD LOG")
                    )
                    self.analyze_build_logs_button.setToolTip(
                        lang_dict.get("analyze_build_logs_tooltip", "Das neueste Build-Protokoll auf typische Fehler und Warnungen prüfen" if is_de else "Check the newest build log for common errors and warnings")
                    )
                if hasattr(self, "diagnostics_button"):
                    self.diagnostics_button.setText(
                        lang_dict.get("system_diagnostics_button", "SYSTEM-DIAGNOSE" if is_de else "SYSTEM DIAGNOSTICS")
                    )
                    self.diagnostics_button.setToolTip(
                        lang_dict.get("system_diagnostics_tooltip", "System, Tool-Verzeichnisse und vorhandene Build-Logs prüfen; startet keinen Build" if is_de else "Check system, tool directories and existing build logs; does not start a build")
                    )

                if hasattr(self, "header_label"):
                    self.header_label.setText(
                        strip_icons(
                            lang_dict.get(
                                "settings_header",
                                "Einstellungen" if is_de else "Settings",
                            )
                        )
                    )

                # OSCam-Status kurz hervorheben
                status_label = getattr(self, "status_label", None)
                if status_label:
                    timestamp = getattr(self, "last_timestamp", "--:--:--")
                    rev = getattr(self, "current_rev", "----")
                    msg = lang_dict.get(
                        "oscam_uptodate",
                        "OSCam ist aktuell." if is_de else "OSCam is up to date.",
                    )
                    state = {"count": 0}

                    def blink_status():
                        color = "#FF0000" if state["count"] % 2 == 0 else "#00FF00"
                        status_label.setText(
                            f"✅ <span style='font-size:24px;color:#FF0000;font-weight:bold;'>"
                            f"[{timestamp}]</span> "
                            f"<span style='font-size:24px;color:#00FF00;font-weight:bold;'>"
                            f"{msg}</span> "
                            f"<span style='font-size:24px;color:{color};font-weight:bold;'>"
                            f"{rev}</span>"
                        )
                        state["count"] += 1
                        if state["count"] < 6:
                            QTimer.singleShot(300, blink_status)

                    blink_status()

                # Gruppentitel an die gewählte Sprache anpassen
                for box in self.findChildren(QGroupBox):
                    title = box.title()
                    if "Settings" in title or "Einstellungen" in title:
                        box.setTitle("Einstellungen" if is_de else "Settings")
                    if any(term in title for term in ("Configuration", "Konfiguration", "GitHub")):
                        box.setTitle(
                            "GitHub Konfiguration" if is_de else "GitHub Configuration"
                        )

                if hasattr(self, "btn_modifier"):
                    self.btn_modifier.setText(
                        f"👤 {strip_icons(lang_dict.get('modifier_button_text', 'Patch Autor' if is_de else 'Patch Author'))}"
                    )

                if hasattr(self, "btn_patch_online"):
                    self.btn_patch_online.setText(
                        f"🌐 {strip_icons(lang_dict.get('patch_online_download', 'Patches aus Git laden' if is_de else 'Load Patches from Git'))}"
                    )

                # Theme und Tooltips nach den Textänderungen erneut anwenden
                try:
                    if hasattr(self, "apply_global_button_style"):
                        self.apply_global_button_style()
                except Exception as exc:
                    log_error("Button-Theme/Tooltips konnten nicht aktualisiert werden", exc)

                # Button-Texte nach der Layout-Neuberechnung anpassen
                try:
                    if hasattr(self, "_fit_all_button_texts"):
                        layout = self.layout()
                        if layout is not None:
                            layout.activate()
                        QTimer.singleShot(0, self._fit_all_button_texts)
                        QTimer.singleShot(250, self._fit_all_button_texts)
                except Exception as exc:
                    log_error("Button-Text-Anpassung fehlgeschlagen", exc)

                # Finales Label vorbereiten und zunächst verstecken
                if hasattr(self, "final_label") and self.final_label:
                    self.final_label.setText(
                        lang_dict.get(
                            "final_label",
                            "🛠️ Was bauen wir heute?" if is_de else "🛠️ What are we building today?",
                        )
                    )
                    self.final_label.hide()

                # Systemcheck nach dem Sprachwechsel starten
                if hasattr(self, "run_full_system_check"):
                    QTimer.singleShot(
                        200,
                        lambda: self.run_full_system_check(clear_log=True),
                    )

                def final_blink():
                    try:
                        final_geom = pbar.geometry() if pbar is not None else QRect(20, 20, 400, 40)
                        final_label = getattr(self, "final_label", None)

                        if final_label is None:
                            final_label = QLabel(
                                lang_dict.get(
                                    "final_label",
                                    "🛠️ Was bauen wir heute?" if is_de else "🛠️ What are we building today?",
                                ),
                                self,
                            )
                            self.final_label = final_label

                        final_label.setAlignment(
                            Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter
                        )
                        final_label.setGeometry(final_geom)
                        final_label.setStyleSheet(
                            "QLabel { border: 2px solid cyan; background-color: black; "
                            "color: cyan; font-weight: bold; font-size: 24px; "
                            "font-family: Arial, Segoe UI, sans-serif; border-radius: 6px; }"
                        )
                        final_label.show()
                        final_label.raise_()

                        blink_colors = ["transparent", "cyan", "transparent", "cyan", "transparent", "cyan"]
                        for delay, color in zip((0, 300, 600, 900, 1200, 1500), blink_colors):
                            QTimer.singleShot(
                                delay,
                                lambda c=color: final_label.setStyleSheet(
                                    "QLabel { border: 2px solid cyan; background-color: black; "
                                    f"color: {c}; font-weight: bold; font-size: 24px; "
                                    "font-family: Arial, Segoe UI, sans-serif; border-radius: 6px; }"
                                ),
                            )

                        if pbar is not None:
                            pbar.setValue(100)
                            pbar.setFormat("✅ OK")
                            QTimer.singleShot(5000, lambda: pbar.setFormat("%p%"))

                        try:
                            if hasattr(self, "hide_language_overlay"):
                                self.hide_language_overlay()
                        except Exception as exc:
                            log_error("Overlay konnte nicht ausgeblendet werden", exc)
                    except Exception as exc:
                        log_error("Abschlussanimation fehlgeschlagen", exc)
                    finally:
                        finished["value"] = True
                        release_lock()

                QTimer.singleShot(500, final_blink)

            except Exception as exc:
                log_error("Fehler beim Abschluss des Sprachwechsels", exc)
                try:
                    if hasattr(self, "hide_language_overlay"):
                        self.hide_language_overlay()
                except Exception as hide_exc:
                    log_error("Overlay konnte nicht ausgeblendet werden", hide_exc)
                finally:
                    finished["value"] = True
                    release_lock()

        try:
            selected = self.language_box.currentText().upper()
            self.LANG = (
                "de"
                if any(token in selected for token in ("DE", "DEU", "DEUTSCH"))
                else "en"
            )
            # Persist language immediately so it survives a normal restart.
            # Keep both config aliases in sync for older code paths.
            try:
                config_obj = getattr(self, "cfg", None)
                if not isinstance(config_obj, dict):
                    config_obj = getattr(self, "current_config", None)
                if not isinstance(config_obj, dict):
                    config_obj = {}
                config_obj["language"] = self.LANG
                if hasattr(self, "cfg"):
                    self.cfg = config_obj
                if hasattr(self, "current_config"):
                    self.current_config = config_obj
                save_config_func = globals().get("save_config")
                if callable(save_config_func) and not getattr(self, "is_loading", False):
                    save_config_func(config_obj, gui_instance=self, silent=True)
            except Exception as save_exc:
                print(f"[change_language] Sprache konnte nicht gespeichert werden: {save_exc}")
            is_de = self.LANG == "de"

            overlay_text = "Sprache wird angepasst..." if is_de else "Switching language..."
            update_pbar(10, f"⏳ {overlay_text}")

            if hasattr(self, "loading_overlay") and hasattr(self, "loading_label"):
                self.loading_overlay.setGeometry(self.rect())
                self.loading_label.setText(overlay_text)
                self.loading_overlay.show()
                self.loading_overlay.raise_()
                QApplication.processEvents()

            safe_play_func = globals().get("safe_play")
            if callable(safe_play_func):
                try:
                    safe_play_func("service-logout.oga")
                except Exception as exc:
                    log_error("Sprachwechsel-Sound fehlgeschlagen", exc)

            # Schreibrechte prüfen/reparieren, falls die Methode vorhanden ist
            perm_text = "Schreibrechte werden geprüft..." if is_de else "Checking write permissions..."
            update_pbar(25, f"🔐 {perm_text}")
            fix_permissions = getattr(self, "fix_all_tool_permissions", None)
            if callable(fix_permissions):
                try:
                    fix_permissions()
                except Exception as exc:
                    log_error("Rechteprüfung fehlgeschlagen", exc)

            load_text = "Texte werden geladen..." if is_de else "Loading texts..."
            update_pbar(30, f"⏳ {load_text}")

            all_texts = globals().get("TEXTS", {})
            if not all_texts:
                try:
                    all_texts = ensure_dependencies()
                except Exception as exc:
                    log_error("Sprachdateien konnten nicht geladen werden", exc)
                    all_texts = {}

            self.TEXT = all_texts.get(self.LANG, {})
            lang_dict = self.TEXT

            
            ui_text = "UI wird aktualisiert..." if is_de else "Updating UI..."
            update_pbar(50, f"⏳ {ui_text}")

            update_language = getattr(self, "update_language", None)
            if callable(update_language):
                update_language()

            # Alle UI-Texte aktualisieren, einschließlich "Rechte fixen"
            update_ui_texts = getattr(self, "update_ui_texts", None)
            if callable(update_ui_texts):
                try:
                    update_ui_texts()
                except Exception as exc:
                    log_error("UI-Texte konnten nicht aktualisiert werden", exc)


            # Status der Installationsbuttons prüfen und Beschriftung aktualisieren
            button_config = (
                ("btn_s3", "S3", "S3_PATH", "/opt/s3", "s3"),
                ("btn_s4", "S4", "S4_PATH", "/opt/simplebuild4", "s4"),
                ("btn_ncam", "NCam", "NCAM_PATH", "/opt/ncam", "ncam"),
            )

            for btn_attr, display_name, path_attr, default_path, executable in button_config:
                btn = getattr(self, btn_attr, None)
                if btn is None:
                    continue

                exe = executable + (".exe" if platform.system() == "Windows" else "")
                path = getattr(self, path_attr, default_path)
                exists = os.path.exists(os.path.join(path, exe))

                label = (
                    f"{display_name} OK"
                    if exists
                    else (
                        f"{display_name} installieren"
                        if is_de
                        else f"Install {display_name}"
                    )
                )

                # Grün = vorhanden, Orange = fehlt
                color = "#2ecc71" if exists else "#f39c12"
                btn.setText(f"🚀 {label}")
                btn._original_text = f"🚀 {label}"
                btn.setStyleSheet(
                    f"""
                    QPushButton {{
                        text-align: left;
                        padding-left: 8px;
                        font-weight: bold;
                        color: {color};
                        background-color: #3d3d3d;
                        border: 1px solid {color};
                        border-radius: 8px;
                    }}
                    QPushButton:hover {{
                        background-color: {color};
                        color: black;
                    }}
                    """
                )

            try:
                if hasattr(self, "apply_global_button_style"):
                    self.apply_global_button_style()
            except Exception as exc:
                log_error("Installationsbutton-Theme fehlgeschlagen", exc)

            try:
                if hasattr(self, "_fit_all_button_texts"):
                    layout = self.layout()
                    if layout is not None:
                        layout.activate()
                    QTimer.singleShot(0, self._fit_all_button_texts)
                    QTimer.singleShot(250, self._fit_all_button_texts)
            except Exception as exc:
                log_error("Button-Text-Anpassung fehlgeschlagen", exc)

            anim_text = "Animation & Systemcheck..." if is_de else "Animation & system check..."
            update_pbar(80, f"⏳ {anim_text}")

            show_animation = getattr(self, "show_language_animation", None)
            if callable(show_animation):
                show_animation(self.LANG, callback=after_animation)
            else:
                after_animation()

            QApplication.processEvents()

        except Exception as exc:
            log_error("Sprachwechsel fehlgeschlagen", exc)
            try:
                if hasattr(self, "hide_language_overlay"):
                    self.hide_language_overlay()
            except Exception as hide_exc:
                log_error("Overlay konnte nicht ausgeblendet werden", hide_exc)
            finally:
                if not finished["value"]:
                    release_lock()

    def on_s3_repository_changed(self, repo_name):
        try:
            if repo_name not in self.s3_repository_options:
                return

            self.selected_s3_repository = repo_name

            if not isinstance(getattr(self, "current_config", None), dict):
                self.current_config = load_config()

            self.current_config["s3_repository_name"] = repo_name

            success = save_config(
                {"s3_repository_name": repo_name},
                gui_instance=self,
                silent=True
            )

            if not success:
                print("Fehler: S3-Repository konnte nicht gespeichert werden.")

        except Exception as exc:
            import traceback
            print(f"Fehler bei der S3-Git-Auswahl: {exc}")
            traceback.print_exc()


    def start_s3_installation(self):
        repo_name = self.s3_repo_combo.currentText()
        repo_url = self.s3_repo_combo.currentData()

        if not repo_url:
            QMessageBox.warning(
                self,
                "S3-Installation",
                "Bitte wählen Sie ein Git-Repository aus.",
            )
            return

        if (
            self.s3_install_worker is not None
            and self.s3_install_worker.isRunning()
        ):
            QMessageBox.information(
                self,
                "S3-Installation",
                "Eine S3-Installation läuft bereits.",
            )
            return

        self.selected_s3_repository = repo_name
        self.current_config["s3_repository_name"] = repo_name

        try:
            save_config(self.current_config)
        except Exception as e:
            QMessageBox.warning(
                self,
                "Konfiguration",
                f"Auswahl konnte nicht gespeichert werden:\n{e}",
            )

        # Build-Log für genau diesen S3-Start vorbereiten. Der S3-Button
        # startet S3InstallWorker (kein externes Terminal über open_terminal);
        # deshalb müssen die vom Worker gesendeten Log-Signale hier in eine
        # Datei geschrieben werden.
        try:
            log_dir = getattr(self, "build_log_dir", None)
            if not log_dir:
                log_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "build_logs")
                os.makedirs(log_dir, exist_ok=True)
                self.build_log_dir = log_dir
            else:
                os.makedirs(log_dir, exist_ok=True)
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            self.s3_build_log_file = os.path.join(log_dir, f"S3_build_{stamp}.log")
            with open(self.s3_build_log_file, "w", encoding="utf-8") as log_file:
                log_file.write(f"S3 Build-Log gestartet: {datetime.now().isoformat(timespec='seconds')}\n")
                log_file.write(f"Repository: {repo_name}\nURL: {repo_url}\nZielpfad: {self.S3_PATH}\n\n")
            print(f"[BUILD-LOG] S3-Logdatei: {self.s3_build_log_file}")
        except Exception as log_error:
            self.s3_build_log_file = None
            print(f"[BUILD-LOG] S3-Logdatei konnte nicht erstellt werden: {log_error}")

        self.s3_install_worker = S3InstallWorker(
            base_target_dir=self.S3_PATH,
            repo_url=repo_url,
            project_name=repo_name,
        )

        self.s3_install_worker.finished_signal.connect(
            self.on_s3_install_finished
        )

        self.s3_install_worker.log_signal.connect(
            self.append_s3_install_log
        )

        self.s3_install_worker.start()


    def on_s3_install_finished(self, success, message):
        if success:
            QMessageBox.information(
                self,
                "S3-Installation erfolgreich",
                message,
            )
        else:
            QMessageBox.critical(
                self,
                "S3-Installationsfehler",
                message,
            )

        self.update_ui_texts()


    def append_s3_install_log(self, message):
        """Zeigt Worker-Meldungen an und schreibt sie in das aktive S3-Log."""
        line = f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] [S3] {message}"
        print(line)
        log_path = getattr(self, "s3_build_log_file", None)
        if log_path:
            try:
                with open(log_path, "a", encoding="utf-8", errors="replace") as log_file:
                    log_file.write(line + "\n")
                    log_file.flush()
            except OSError as log_error:
                print(f"[BUILD-LOG] Schreiben fehlgeschlagen ({log_path}): {log_error}")


    
    def run_full_system_check(self, clear_log=True):
        """
        Startet den Systemcheck ohne die GUI zu blockieren.

        Alle langsamen/blockierenden Operationen laufen im Worker-Thread.
        Die GUI wird ausschließlich über Qt-Signale aktualisiert.
        """

        from PyQt6.QtCore import QThread

        # ------------------------------------------------------------
        # Doppelstart verhindern
        # ------------------------------------------------------------

        if getattr(self, "_checking_active", False):
            return

        self._checking_active = True
        self.is_loading = True

        # ------------------------------------------------------------
        # UI vorbereiten
        # ------------------------------------------------------------

        if hasattr(self, "hide_final_label"):
            try:
                self.hide_final_label()
            except Exception:
                pass
        elif hasattr(self, "final_label"):
            try:
                if self.final_label:
                    self.final_label.hide()
            except Exception:
                pass

        if clear_log and hasattr(self, "log_window"):
            try:
                self.log_window.clear()
            except Exception:
                pass

        widget = getattr(self, "info_text", None)
        pbar = getattr(self, "progress_bar", None)

        if widget is None:
            self.is_loading = False
            self._checking_active = False
            return

        # ------------------------------------------------------------
        # Progressbar
        # ------------------------------------------------------------

        if pbar:

            pbar.setRange(0, 100)
            pbar.setValue(0)
            pbar.setTextVisible(True)

            pbar.setStyleSheet(
                """
                QProgressBar {
                    border: 2px solid #444444;
                    border-radius: 8px;
                    background-color: #0A0A0A;
                    color: black;
                    text-align: center;
                    font-weight: 900;
                    font-size: 20px;
                    min-height: 35px;
                }

                QProgressBar::chunk {
                    background-color: qlineargradient(
                        spread:pad,
                        x1:0,
                        y1:0,
                        x2:1,
                        y2:0,
                        stop:0 #FF00FF,
                        stop:0.5 #00FFFF,
                        stop:1 #39FF14
                    );
                    border-radius: 6px;
                }
                """
            )

            pbar.setFormat(
                "🚀 Initialisiere System...   %p%"
            )
            pbar.show()

        # ------------------------------------------------------------
        # Info-Widget leeren
        # ------------------------------------------------------------

        if clear_log:
            widget.setHtml("")

        # ------------------------------------------------------------
        # HTML-Puffer
        # ------------------------------------------------------------

        self._system_check_html = []

        def append_html(fragment):
            """
            Dieser Slot läuft im GUI-Thread.
            """

            if not fragment:
                return

            self._system_check_html.append(fragment)

            widget.setHtml(
                "".join(self._system_check_html)
            )

            try:
                from PyQt6.QtGui import QTextCursor

                widget.moveCursor(
                    QTextCursor.MoveOperation.End
                )
            except Exception:
                pass

        def update_progress(value, text):
            """
            Dieser Slot läuft im GUI-Thread.
            """

            if not pbar:
                return

            value = max(
                0,
                min(100, int(value))
            )

            pbar.setValue(value)
            pbar.setFormat(
                f"{text}   %p%"
            )

        # ------------------------------------------------------------
        # Worker / Thread erstellen
        # ------------------------------------------------------------

        self._system_check_thread = QThread(self)

        # ------------------------------------------------------------
        # Eingestellte Repo-URL aus deiner Anwendung holen
        #
        # Passe die Attributnamen hier nur an, falls deine URL anders
        # gespeichert wird.
        # ------------------------------------------------------------

        repo_url = ""

        possible_repo_attributes = (
            "repo_url",
            "repository_url",
            "github_repo_url",
            "REPO_URL",
            "GITHUB_REPO_URL",
            "repo",
        )

        for attr_name in possible_repo_attributes:

            try:
                value = getattr(
                    self,
                    attr_name,
                    "",
                )

                if isinstance(value, str) and value.strip():
                    repo_url = value.strip()
                    break

            except Exception:
                pass

        # ------------------------------------------------------------
        # Worker
        # ------------------------------------------------------------

        self._system_check_worker = SystemCheckWorker(
            lang=getattr(
                self,
                "LANG",
                "de",
            ),
            repo_url=repo_url,
        )

        self._system_check_worker.moveToThread(
            self._system_check_thread
        )

        # ------------------------------------------------------------
        # Signale
        # ------------------------------------------------------------

        self._system_check_worker.html.connect(
            append_html
        )

        self._system_check_worker.progress.connect(
            update_progress
        )

        self._system_check_worker.error.connect(
            lambda message: append_html(
                "<br>"
                "<div style='"
                "text-align:center;"
                "color:#FF0000;"
                "font-family:Arial Black;"
                "font-size:18pt;"
                "'>"
                f"❌ System Check Error:<br>{message}"
                "</div>"
            )
        )

        self._system_check_worker.finished.connect(
            self._system_check_finished
        )

        # ------------------------------------------------------------
        # Worker nach Beendigung aufräumen
        # ------------------------------------------------------------

        self._system_check_worker.finished.connect(
            self._system_check_thread.quit
        )

        self._system_check_worker.finished.connect(
            self._system_check_worker.deleteLater
        )

        self._system_check_thread.finished.connect(
            self._system_check_thread.deleteLater
        )

        # ------------------------------------------------------------
        # Thread starten
        # ------------------------------------------------------------

        self._system_check_thread.started.connect(
            self._system_check_worker.run
        )
        if hasattr(self, "sound_manager"):
            self.sound_manager.play_start()

        self._system_check_thread.start()
            
        
    def _system_check_finished(self):
        """
        Wird nach erfolgreichem oder fehlerhaftem Worker-Ende
        im GUI-Thread ausgeführt.
        """
        if (
            not getattr(self, "_system_check_had_error", False)
            and hasattr(self, "sound_manager")
        ):
            self.sound_manager.play_success()
        pbar = getattr(
            self,
            "progress_bar",
            None,
        )

        if pbar:
            pbar.setValue(100)

            is_de = (
                getattr(
                    self,
                    "LANG",
                    "de",
                ).lower()
                == "de"
            )

            pbar.setFormat(
                (
                    "✅ System Check abgeschlossen ✔   %p%"
                    if is_de
                    else
                    "✅ System check completed ✔   %p%"
                )
            )

        # ------------------------------------------------------------
        # Status zurücksetzen
        # ------------------------------------------------------------

        self.is_loading = False
        self._checking_active = False

        # ------------------------------------------------------------
        # Vorhandene Update-Prüfung beibehalten
        #
        # Wichtig:
        # Sie läuft jetzt NACH dem Systemcheck.
        # ------------------------------------------------------------

        try:
            self.check_for_update_on_start()
        except Exception as e:
            widget = getattr(
                self,
                "info_text",
                None,
            )

            if widget:
                widget.append(
                    "<br>"
                    "<span style='"
                    "color:#F57A08;"
                    "font-weight:bold;"
                    "'>"
                    f"⚠️ Update check: {e}"
                    "</span>"
                )

        # ------------------------------------------------------------
        # Referenzen behalten wir bis zum Thread-Ende.
        # Danach können sie freigegeben werden.
        # ------------------------------------------------------------

        try:
            thread = getattr(
                self,
                "_system_check_thread",
                None,
            )

            if thread and not thread.isRunning():
                self._system_check_thread = None
                self._system_check_worker = None

        except Exception:
            pass
    
    def show_commits(self, info_widget=None, progress_callback=None, num_commits=None):
        """
        Zeigt die letzten Commits an.

        Ablauf:
        1. TEMP_REPO prüfen.
        2. Wenn TEMP_REPO ein gültiges Git-Repository ist:
           -> lokale Commits lesen.
        3. Wenn TEMP_REPO nicht existiert oder kein Git-Repository ist:
           -> Repository temporär online klonen.
        4. Commits anzeigen.
        5. Progressbar und Sound aktualisieren.
        6. Temporäres Repository immer wieder löschen.

        Unterstützt:
        - PyQt6 QTextEdit
        - QSpinBox / QDoubleSpinBox / Integer für num_commits
        - Deutsch / Englisch
        - progress_callback
        - Regenbogen-Progressbar
        - Fehlerstatus
        """

        # ------------------------------------------------------------
        # Final Label verstecken
        # ------------------------------------------------------------
        if hasattr(self, "hide_final_label"):
            try:
                self.hide_final_label()
            except Exception:
                pass

        # ------------------------------------------------------------
        # Imports
        # ------------------------------------------------------------
        from PyQt6.QtWidgets import QTextEdit, QApplication
        from PyQt6.QtCore import QTimer

        import os
        import tempfile
        import shutil
        import subprocess

        # ------------------------------------------------------------
        # Sprache
        # ------------------------------------------------------------
        lang = getattr(self, "LANG", "de").lower()
        is_de = lang.startswith("de")

        # ------------------------------------------------------------
        # Info-Widget bestimmen
        # ------------------------------------------------------------
        if not isinstance(info_widget, QTextEdit):
            info_widget = getattr(self, "info_text", None)

        # ------------------------------------------------------------
        # Progressbar
        # ------------------------------------------------------------
        pbar = getattr(self, "progress_bar", None)

        # ------------------------------------------------------------
        # Anzahl Commits bestimmen
        #
        # Erlaubt:
        #   num_commits = 10
        #   num_commits = QSpinBox
        #   num_commits = None -> self.commit_spin
        # ------------------------------------------------------------
        try:
            if hasattr(num_commits, "value") and callable(num_commits.value):
                total_commits = int(num_commits.value())

            elif num_commits is not None:
                total_commits = int(num_commits)

            else:
                commit_spin = getattr(self, "commit_spin", None)

                if hasattr(commit_spin, "value") and callable(commit_spin.value):
                    total_commits = int(commit_spin.value())
                else:
                    total_commits = 10

        except (TypeError, ValueError):
            total_commits = 10

        # Sinnvolle Grenzen
        total_commits = max(1, min(total_commits, 1000))

        # ------------------------------------------------------------
        # Hilfsfunktion: Logging
        # ------------------------------------------------------------
        def log(text, level="info"):
            try:
                if info_widget is not None and hasattr(self, "append_info"):
                    self.append_info(info_widget, text, level)
                else:
                    print(f"[{level.upper()}] {text}")
            except Exception:
                print(f"[{level.upper()}] {text}")

        # ------------------------------------------------------------
        # Hilfsfunktion: Progressbar
        # ------------------------------------------------------------
        def set_progress(value, text=None, is_err=False):
            if pbar is None:
                return

            try:
                value = max(0, min(100, int(value)))

                rainbow = (
                    "qlineargradient("
                    "x1:0, y1:0, x2:1, y2:0,"
                    "stop:0 #FF0000,"
                    "stop:0.2 #FF7F00,"
                    "stop:0.4 #FFFF00,"
                    "stop:0.6 #00FF00,"
                    "stop:0.8 #0000FF,"
                    "stop:1 #8B00FF"
                    ")"
                )

                if is_err:
                    style = """
                        QProgressBar {
                            text-align: center;
                            font-weight: bold;
                            border: 2px solid #500;
                            border-radius: 6px;
                            background-color: #111;
                            color: #FF0000;
                            font-size: 15pt;
                        }

                        QProgressBar::chunk {
                            background-color: #800;
                            border-radius: 4px;
                        }
                    """
                else:
                    style = f"""
                        QProgressBar {{
                            text-align: center;
                            font-weight: bold;
                            border: 2px solid #222;
                            border-radius: 6px;
                            background-color: #111;
                            color: black;
                            font-size: 15pt;
                        }}

                        QProgressBar::chunk {{
                            background: {rainbow};
                            border-radius: 4px;
                        }}
                    """

                pbar.setStyleSheet(style)
                pbar.setValue(value)

                if text:
                    pbar.setFormat(str(text))
                else:
                    pbar.setFormat("%p%")

                pbar.show()

                QApplication.processEvents()

                if progress_callback:
                    try:
                        progress_callback(value)
                    except Exception:
                        pass

            except Exception as e:
                print(f"[WARNING] Progressbar error: {e}")

        # ------------------------------------------------------------
        # Hilfsfunktion: Progressbar am Ende
        # ------------------------------------------------------------
        def finalize_pbar(text, visible_seconds=3):
            if pbar is None:
                return

            try:
                pbar.setValue(100)
                pbar.setFormat(str(text))
                pbar.show()

                QApplication.processEvents()

                def reset_progress():
                    try:
                        pbar.setStyleSheet(
                            """
                            QProgressBar {
                                text-align: center;
                                font-weight: bold;
                                border: 2px solid #222;
                                border-radius: 6px;
                                background-color: #111;
                                color: black;
                                font-size: 15pt;
                            }

                            QProgressBar::chunk {
                                background-color: transparent;
                            }
                            """
                        )

                        pbar.setValue(0)
                        pbar.setFormat("%p%")

                        if progress_callback:
                            try:
                                progress_callback(0)
                            except Exception:
                                pass

                    except Exception:
                        pass

                QTimer.singleShot(
                    int(visible_seconds * 1000),
                    reset_progress
                )

            except Exception:
                pass

        # ------------------------------------------------------------
        # Hilfsfunktion: Sound
        # ------------------------------------------------------------
        def play_commit_sound(success=True):
            try:
                sound_file = (
                    "message-new-instant.oga"
                    if success
                    else
                    "dialog-error.oga"
                )

                if "safe_play" in globals():
                    safe_play(sound_file)

                elif hasattr(self, "safe_play"):
                    self.safe_play(sound_file)

            except Exception:
                pass

        # ------------------------------------------------------------
        # Hilfsfunktion: Git-Befehl
        # ------------------------------------------------------------
        def run_git_command(args, cwd=None):
            """
            Führt einen Git-Befehl aus und gibt:
                success, stdout, stderr
            zurück.
            """

            try:
                result = subprocess.run(
                    args,
                    cwd=cwd,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    check=False,
                )

                stdout = result.stdout.strip()
                stderr = result.stderr.strip()

                # Fehlerdiagnose: Exit-Code und verständlicher Fallback,
                # damit fehlgeschlagene Git-Schritte leichter erkennbar sind.
                if result.returncode != 0:
                    detail = stderr or stdout or (
                        "Git hat keine Fehlerdetails ausgegeben."
                        if is_de
                        else "Git did not provide error details."
                    )
                    stderr = (
                        f"Git-Befehl fehlgeschlagen (Exit-Code {result.returncode}): {detail}"
                        if is_de
                        else f"Git command failed (exit code {result.returncode}): {detail}"
                    )

                return (
                    result.returncode == 0,
                    stdout,
                    stderr,
                )

            except FileNotFoundError:
                return (
                    False,
                    "",
                    "Git wurde nicht gefunden. Bitte Git installieren."
                    if is_de
                    else "Git was not found. Please install Git.",
                )

            except Exception as e:
                return (
                    False,
                    "",
                    str(e),
                )

        # ------------------------------------------------------------
        # START
        # ------------------------------------------------------------
        set_progress(
            5,
            "Bereite Commit-Anzeige vor..."
            if is_de
            else
            "Preparing commit display..."
        )

        log(
            TEXTS.get(lang, {}).get(
                "loading_commits",
                "Lade Commits..."
                if is_de
                else
                "Loading commits..."
            ),
            "warning",
        )

        # ------------------------------------------------------------
        # Repository-Daten
        # ------------------------------------------------------------
        repo_url = "https://git.streamboard.tv/common/oscam.git"
        branch = "master"

        temp_dir = None
        output = ""

        # ------------------------------------------------------------
        # TEMP_REPO aus globals / self holen
        # ------------------------------------------------------------
        try:
            local_repo = TEMP_REPO
        except NameError:
            local_repo = getattr(self, "TEMP_REPO", None)

        # ------------------------------------------------------------
        # Prüfen, ob TEMP_REPO gültig ist
        # ------------------------------------------------------------
        local_repo_valid = False

        if local_repo and os.path.isdir(local_repo):

            set_progress(
                15,
                "Prüfe lokales Repository..."
                if is_de
                else
                "Checking local repository..."
            )

            success, stdout, stderr = run_git_command(
                [
                    "git",
                    "-C",
                    local_repo,
                    "rev-parse",
                    "--is-inside-work-tree",
                ]
            )

            if success and stdout.lower() == "true":
                local_repo_valid = True

        # ============================================================
        # HAUPTVERARBEIT
        # ============================================================
        try:

            # ========================================================
            # FALL 1: Lokales Git-Repository
            # ========================================================
            if local_repo_valid:

                log(
                    "📁 Lokales Git-Repository gefunden."
                    if is_de
                    else
                    "📁 Local Git repository found.",
                    "info",
                )

                set_progress(
                    35,
                    "Lese lokale Commits..."
                    if is_de
                    else
                    "Reading local commits..."
                )

                success, stdout, stderr = run_git_command(
                    [
                        "git",
                        "-C",
                        local_repo,
                        "log",
                        f"-n{total_commits}",
                        "--oneline",
                    ]
                )

                if not success:

                    log(
                        f"❌ Git-Fehler: {stderr or 'Unbekannter Fehler'}",
                        "error",
                    )

                    set_progress(
                        100,
                        "❌ Git-Fehler"
                        if is_de
                        else
                        "❌ Git error",
                        is_err=True,
                    )

                    play_commit_sound(False)
                    return

                output = stdout

            # ========================================================
            # FALL 2: Kein gültiges lokales Repository
            #         -> Online Clone
            # ========================================================
            else:

                if local_repo and os.path.exists(local_repo):

                    log(
                        "⚠ TEMP_REPO existiert, ist aber kein gültiges "
                        "Git-Repository."
                        if is_de
                        else
                        "⚠ TEMP_REPO exists but is not a valid "
                        "Git repository.",
                        "warning",
                    )

                    log(
                        "🌐 Verwende Online-Repository."
                        if is_de
                        else
                        "🌐 Using online repository.",
                        "info",
                    )

                else:

                    log(
                        "🌐 Kein lokales Repository gefunden."
                        if is_de
                        else
                        "🌐 No local repository found.",
                        "info",
                    )

                # ----------------------------------------------------
                # Temporäres Verzeichnis erstellen
                # ----------------------------------------------------
                temp_dir = tempfile.mkdtemp(
                    prefix="oscam_git_"
                )

                log(
                    f"📂 Temporäres Verzeichnis: {temp_dir}",
                    "info",
                )

                set_progress(
                    35,
                    "Klone Repository..."
                    if is_de
                    else
                    "Cloning repository..."
                )

                # ----------------------------------------------------
                # Online Clone
                # ----------------------------------------------------
                success, stdout, stderr = run_git_command(
                    [
                        "git",
                        "clone",
                        "--depth",
                        str(total_commits),
                        "--branch",
                        branch,
                        repo_url,
                        temp_dir,
                    ]
                )

                if not success:

                    error_text = (
                        stderr
                        or stdout
                        or (
                            "Unbekannter Fehler beim Klonen."
                            if is_de
                            else
                            "Unknown clone error."
                        )
                    )

                    log(
                        f"❌ Fehler beim Klonen: {error_text}",
                        "error",
                    )

                    set_progress(
                        100,
                        "❌ Clone Fehler"
                        if is_de
                        else
                        "❌ Clone error",
                        is_err=True,
                    )

                    play_commit_sound(False)
                    return

                # ----------------------------------------------------
                # Clone erfolgreich
                # ----------------------------------------------------
                set_progress(
                    65,
                    "Repository geladen..."
                    if is_de
                    else
                    "Repository loaded..."
                )

                log(
                    "✅ Repository erfolgreich geklont."
                    if is_de
                    else
                    "✅ Repository cloned successfully.",
                    "success",
                )

                # ----------------------------------------------------
                # Commits lesen
                # ----------------------------------------------------
                set_progress(
                    75,
                    "Lese Online-Commits..."
                    if is_de
                    else
                    "Reading online commits..."
                )

                success, stdout, stderr = run_git_command(
                    [
                        "git",
                        "-C",
                        temp_dir,
                        "log",
                        f"-n{total_commits}",
                        "--oneline",
                    ]
                )

                if not success:

                    error_text = (
                        stderr
                        or stdout
                        or (
                            "Unbekannter Git-Fehler."
                            if is_de
                            else
                            "Unknown Git error."
                        )
                    )

                    log(
                        f"❌ Fehler beim Lesen der Commits: {error_text}",
                        "error",
                    )

                    set_progress(
                        100,
                        "❌ Fehler"
                        if is_de
                        else
                        "❌ Error",
                        is_err=True,
                    )

                    play_commit_sound(False)
                    return

                output = stdout

            # ========================================================
            # AUSGABE VERARBEITEN
            # ========================================================
            set_progress(
                90,
                "Verarbeite Commits..."
                if is_de
                else
                "Processing commits..."
            )

            lines = [
                line.strip()
                for line in output.splitlines()
                if line.strip()
            ]

            # --------------------------------------------------------
            # Keine Commits
            # --------------------------------------------------------
            if not lines:

                log(
                    "⚠ Keine Commits gefunden."
                    if is_de
                    else
                    "⚠ No commits found.",
                    "warning",
                )

                set_progress(
                    100,
                    "⚠ Keine Commits"
                    if is_de
                    else
                    "⚠ No commits",
                    is_err=True,
                )

                play_commit_sound(False)

                return

            # --------------------------------------------------------
            # Commits anzeigen
            # --------------------------------------------------------
            for line in lines:
                log(
                    f"• {line}",
                    "info"
                )

            # --------------------------------------------------------
            # Erfolg
            # --------------------------------------------------------
            loaded_text = TEXTS.get(lang, {}).get(
                "commits_loaded",
                "Commits erfolgreich geladen"
                if is_de
                else
                "Commits loaded successfully"
            )

            log(
                f"✅ {loaded_text} ({len(lines)})",
                "success",
            )

            set_progress(
                100,
                "✅ Commits geladen"
                if is_de
                else
                "✅ Commits loaded"
            )

            play_commit_sound(True)

            finalize_pbar(
                "✅ Fertig!"
                if is_de
                else
                "✅ Done!",
                visible_seconds=3
            )

        # ============================================================
        # ALLGEMEINER FEHLER
        # ============================================================
        except Exception as e:

            error_text = str(e)

            log(
                f"❌ Fehler: {error_text}",
                "error"
            )

            set_progress(
                100,
                f"❌ Fehler: {error_text}",
                is_err=True
            )

            play_commit_sound(False)

        # ============================================================
        # CLEANUP
        # ============================================================
        finally:

            if temp_dir and os.path.exists(temp_dir):

                try:

                    shutil.rmtree(
                        temp_dir,
                        ignore_errors=True
                    )

                    log(
                        "🧹 Temporäres Repository gelöscht."
                        if is_de
                        else
                        "🧹 Temporary repository removed.",
                        "info",
                    )

                except Exception as cleanup_error:

                    log(
                        f"⚠ Cleanup-Fehler: {cleanup_error}",
                        "warning",
                    )

    # ===================== OSCam-EMU BUTTON WRAPPERS =====================
    
    def oscam_emu_git_clear(self, info_widget=None, progress_callback=None):
        """Zentrales Logging für die Emu-Git Bereinigung – mit Neon-Regenbogen & Abschluss-Puls."""

        from PyQt6.QtWidgets import QApplication
        from PyQt6.QtCore import QTimer

        # --- Final Label verstecken ---
        if hasattr(self, "hide_final_label"):
            self.hide_final_label()

        info_widget = info_widget or getattr(self, "info_text", None)
        lang = getattr(self, "LANG", "de").lower()[:2]
        pbar = getattr(self, "progress_bar", None)

        # --- Neon-Styles ---
        STYLE_BASE = """
            QProgressBar {{
                border: 2px solid #444444;
                border-radius: 8px;
                background-color: {bg_color};
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

        # --- Hilfsfunktion für ProgressBar Updates ---
        def update_p(val, is_err=False, text=None):
            if pbar:
                chunk = "qlineargradient(spread:pad, x1:0, y1:0, x2:1, y2:0, stop:0 #800, stop:1 #F00)" if is_err else RAINBOW_GRADIENT
                t_color = "#FF0000" if is_err else "black"
            
                pbar.setStyleSheet(STYLE_BASE.format(bg_color="#0A0A0A", text_color=t_color, chunk_color=chunk))
                pbar.setValue(val)
                pbar.setFormat(text if text else f"{val}%")
                pbar.show()
            if progress_callback:
                try: progress_callback(val)
                except: pass
            QApplication.processEvents()

        # --- Startmeldung ---
        update_p(10)
        # Annahme: TEXTS ist global
        start_msg = TEXTS.get(lang, {}).get("oscam_emu_git_clearing", "🔹 Emu-Git wird geleert...")
        if hasattr(self, "append_info"):
            self.append_info(info_widget, start_msg, "info")

        try:
            # --- Bereinigung starten ---
            update_p(50)
            # Annahme: clean_oscam_emu_git existiert global
            result = globals().get("clean_oscam_emu_git", lambda **x: "success")(progress_callback=progress_callback)

            # --- Ergebnis auswerten ---
            if result == "success":
                msg = TEXTS.get(lang, {}).get("oscam_emu_git_cleared", "✅ Bereinigung erfolgreich!")
                if hasattr(self, "append_info"):
                    self.append_info(info_widget, msg, "success")
            
                bar_txt = "✅ Ordner geleert" if lang == "de" else "✅ Folder cleared"

                # Sanfter Grüner Puls am Ende
                def pulse_green(times=4):
                    state = {"i": 0}

                    def toggle():
                        # Wechselt zwischen Neon-Grün und dem dunklen Hintergrund
                        bg = "#39FF14" if state["i"] % 2 == 0 else "#0A0A0A"
                        if pbar:
                            pbar.setStyleSheet(STYLE_BASE.format(bg_color=bg, text_color="black", chunk_color=RAINBOW_GRADIENT))
                    
                        state["i"] += 1
                        if state["i"] < times * 2:
                            QTimer.singleShot(200, toggle)
                        else:
                            if pbar: pbar.setFormat(bar_txt)

                    toggle()

                pulse_green()
                update_p(100)
                if "safe_play" in globals(): safe_play("complete.oga")

            elif result == "not_found":
                msg = "ℹ️ " + ("Ordner bereits leer." if lang == "de" else "Folder already empty.")
                if hasattr(self, "append_info"):
                    self.append_info(info_widget, msg, "info")
            
                bar_txt = "ℹ️ Bereits leer" if lang == "de" else "ℹ️ Already empty"
                update_p(100, text=bar_txt)
                if "safe_play" in globals(): safe_play("dialog-information.oga")

            else:
                raise Exception("Deletion failed")

        except Exception as e:
            if hasattr(self, "append_info"):
                self.append_info(info_widget, f"❌ Fehler: {e}", "error")
            bar_txt = "❌ Fehler" if lang == "de" else "❌ Error"
            update_p(100, is_err=True, text=bar_txt)
            if "safe_play" in globals(): safe_play("dialog-error.oga")

        finally:
            QApplication.processEvents()
    
    def _run_grid_action(self, key, func):
        """Führt eine Grid-Aktion nach einem echten Button-Klick aus."""

        print(f"[GRID CLICK] START: {key}", flush=True)

        # ---------------------------------------------------------
        # Aktiven Button setzen
        # ---------------------------------------------------------
        try:
            self.set_active_button(key)
            print(f"[GRID CLICK] set_active_button OK: {key}", flush=True)
        except Exception as e:
            print(
                f"[GRID CLICK] set_active_button Fehler bei {key}: {e}",
                flush=True
            )

        # ---------------------------------------------------------
        # Callback prüfen
        # ---------------------------------------------------------
        if func is None:
            print(
                f"[GRID CLICK] FEHLER: Kein Callback für {key}",
                flush=True
            )
            return

        print(
            f"[GRID CLICK] Callback: {getattr(func, '__name__', repr(func))}",
            flush=True
        )

        # ---------------------------------------------------------
        # Aktion ausführen
        # ---------------------------------------------------------
        try:
            result = func()

            print(
                f"[GRID CLICK] ENDE: {key} -> {result!r}",
                flush=True
            )

            return result

        except Exception as e:
            print(
                f"[GRID CLICK] Aktion {key} fehlgeschlagen: {e}",
                flush=True
            )

            import traceback
            traceback.print_exc()

            return None
    
    def create_buttons(self):
        self.btn_s3 = QPushButton("🚀 Install S3")
        self.btn_s4 = QPushButton("🚀 Install S4")
        self.btn_ncam = QPushButton("🚀 Install NCam-speedy")
    
    def _responsive_button_height(self):
        """Berechnet eine einheitliche Buttonhöhe (30–50 px) anhand der Bildschirmhöhe."""
        from PyQt6.QtWidgets import QApplication

        app = QApplication.instance()
        screen = self.screen() if hasattr(self, "screen") else None
        if screen is None and app is not None:
            screen = app.primaryScreen()
        screen_height = screen.availableGeometry().height() if screen else 900
        # 720 px -> 30 px, 1080 px -> 40 px, 1440 px -> 50 px.
        return max(30, min(50, int(round(30 + (screen_height - 720) * 20 / 720))))

    def _fit_all_button_texts(self):
        """Zentriert und skaliert alle Buttons nach jeder Größen-/Textänderung."""
        from PyQt6.QtWidgets import QPushButton, QToolButton, QSizePolicy
        from PyQt6.QtGui import QFont, QFontMetrics

        height = self._responsive_button_height()
        buttons = list(self.findChildren(QPushButton)) + list(self.findChildren(QToolButton))
        # Auch eventuell separat verwaltete Buttons berücksichtigen.
        for collection_name in ("all_buttons", "buttons", "option_buttons"):
            collection = getattr(self, collection_name, None)
            values = collection.values() if isinstance(collection, dict) else (collection or [])
            for value in values:
                if isinstance(value, (tuple, list)):
                    value = value[0] if value else None
                if isinstance(value, (QPushButton, QToolButton)):
                    buttons.append(value)

        # Options-Buttons haben eine bewusst gesetzte Mindestbreite. Die bisherige
        # globale setMinimumWidth(0)-Zeile hat diese Vorgabe bei jedem Resize
        # wieder aufgehoben.
        option_button_ids = set()
        option_buttons = getattr(self, "option_buttons", {})
        if isinstance(option_buttons, dict):
            for value in option_buttons.values():
                candidate = value[0] if isinstance(value, (tuple, list)) and value else value
                if isinstance(candidate, (QPushButton, QToolButton)):
                    option_button_ids.add(id(candidate))

        seen = set()
        for btn in buttons:
            try:
                if id(btn) in seen:
                    continue
                seen.add(id(btn))
                text = " ".join(btn.text().replace("\n", " ").split())
                btn.setFixedHeight(height)
                if id(btn) in option_button_ids:
                    btn.setMinimumWidth(220)
                else:
                    btn.setMinimumWidth(0)
                btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

                # Nicht bei jedem Resize dieselbe CSS-Regel erneut anhängen.
                # Das verhinderte zwar keinen sichtbaren Fehler, ließ den
                # Stylesheet-String aber bei jeder Fensteränderung anwachsen.
                style = btn.styleSheet()
                center_rule = "QPushButton, QToolButton { text-align: center; }"
                if "text-align: center;" not in style:
                    btn.setStyleSheet(style + "\n" + center_rule)

                if text:
                    btn.setToolTip(btn.toolTip() or text)
                    font = QFont(btn.font())
                    font.setBold(True)
                    available = max(45, btn.contentsRect().width() - 16)
                    if btn.width() <= 1:
                        available = max(45, btn.sizeHint().width() - 16)
                    # So groß wie möglich, aber so klein wie nötig, damit der
                    # komplette Text einzeilig in die vorhandene Buttonbreite passt.
                    min_point_size = 8 if id(btn) in option_button_ids else 7
                    for point_size in range(min(14, max(min_point_size, font.pointSize())), min_point_size - 1, -1):
                        font.setPointSize(point_size)
                        if QFontMetrics(font).horizontalAdvance(text) <= available:
                            break
                    btn.setFont(font)
                btn.updateGeometry()
            except RuntimeError:
                continue
            except Exception as exc:
                print(f"[_fit_all_button_texts] Button-Anpassung fehlgeschlagen: {exc}")

    def fix_all_tool_permissions(self, **kwargs):
        """Setzt rekursiv Schreibrechte mit ProgressBar, Sound und Sprachprüfung."""
        import os
        from PyQt6.QtWidgets import QApplication

        # --- START SOUND & SPRACHE ---
        if "safe_play" in globals():
            safe_play("dialog-information.oga")

        # Aktuelle Sprache live ermitteln
        lang = str(getattr(self, "LANG", "de")).lower()[:2]
        is_de = lang == "de"
        
        base_path = os.path.dirname(os.path.realpath(__file__))
        pbar = getattr(self, "progress_bar", None)

        start_msg = f"<b>🔧 Starte Rechte-Reparatur in:</b> {base_path}" if is_de else f"<b>🔧 Starting permission fix in:</b> {base_path}"
        if hasattr(self, "info_text"):
            self.info_text.append(start_msg)
        QApplication.processEvents()

        # 1. Dateien zählen (mit Ausschluss von .git für Speed & Stabilität)
        all_items = []
        exclude_dirs = {".git", "__pycache__", ".github"}
        
        try:
            for root, dirs, files in os.walk(base_path):
                # Verzeichnisse im Set ausschließen
                dirs[:] = [d for d in dirs if d not in exclude_dirs]
                
                for name in dirs + files:
                    all_items.append(os.path.join(root, name))
                
                QApplication.processEvents() # Verhindert "Keine Rückmeldung"
        except Exception as e:
            if hasattr(self, "info_text"):
                self.info_text.append(f"<span style='color:red'>Error: {e}</span>")

        total = len(all_items)
        if total == 0:
            if pbar: pbar.hide()
            return

        # 2. Rechte setzen
        count = 0
        for path in all_items:
            try:
                # 0o777 für Ordner, 0o666 für Dateien
                os.chmod(path, 0o777 if os.path.isdir(path) else 0o666)
                count += 1
                
                # Progress Update
                if pbar and count % 2 == 0:
                    val = int((count / total) * 100)
                    pbar.setValue(val)
                    msg = "Fixe Rechte..." if is_de else "Fixing perms..."
                    pbar.setFormat(f"🔐 {msg} {val}%")
                    QApplication.processEvents() 
            except:
                continue

        # 3. ABSCHLUSS
        if pbar:
            pbar.setValue(100)
            pbar.setFormat("✅ Rechte fixiert!" if is_de else "✅ Rights fixed!")
        
        if "safe_play" in globals():
            safe_play("complete.oga")

        final_msg = f"✅ Fertig! {count} Objekte angepasst." if is_de else f"✅ Done! {count} objects adjusted."
        if hasattr(self, "info_text"):
            self.info_text.append(f"<span style='color:#39FF14'><b>{final_msg}</b></span>")
            self.info_text.ensureCursorVisible()
    
    def check_ncam_updates(self):
        """
        Startet den Update-Check für NCam Bonecrew.
        Arbeitet plattformübergreifend (Linux/Windows) mit NCAM_PATH.
        """
        import os

        # Remote Git-URL (Beispiel)
        ncam_git_url = "https://github.com/speedy005/NCam-speedy_dany"

        # Lokaler Pfad
        local_path = getattr(self, "NCAM_PATH", "/opt/s3_ncam_bonecrew_test")
        if not os.path.exists(local_path):
            print(f"[NCAM] Lokaler Pfad existiert nicht: {local_path}")
            return

        # Worker starten (OSCUpdateWorker plattformunabhängig implementiert)
        self.ncam_update_worker = OSCamUpdateWorker(ncam_git_url, local_path)
        self.ncam_update_worker.status_signal.connect(self.on_ncam_update_status)
        self.ncam_update_worker.start()

    def on_ncam_update_status(self, update_available, remote_hash):
        """Reagiert auf das Ergebnis des NCam-Update-Checks."""
        from PyQt6.QtWidgets import QPushButton

        btn = getattr(self, "btn_ncam", None)
        if not btn:
            return

        if update_available:
            print(f"[NCAM] Update verfügbar! Neuer Hash: {remote_hash[:7]}")
            btn.setText("🚀 NCam Update!")
            btn.setStyleSheet(
                "background-color: orange; color: black; font-weight: bold; border-radius: 8px;"
            )
        else:
            print("[NCAM] Aktuell.")
            btn.setText("NCam OK")
            btn.setStyleSheet(
                "background-color: #00FF00; color: black; font-weight: bold; border-radius: 8px;"
            )

        # Auch nach einer asynchronen NCam-Statusänderung gilt das ausgewählte
        # Farbschema für alle Buttons; der Status-Text bleibt dabei erhalten.
        try:
            if hasattr(self, "apply_global_button_style"):
                self.apply_global_button_style()
        except Exception as exc:
            print(f"[NCAM] Globales Button-Theme konnte nicht angewendet werden: {exc}")

        # Animation beenden, ohne einen zusätzlichen Ton auszulösen
        def finish_blink():
            if sound_manager:
                sound_manager.revision_timer.stop()

        QTimer.singleShot(2200, finish_blink)


        # Sound erst nach Abschluss der Animation stoppen
        def finish_blink():
            if sound_manager:
                sound_manager.stop_revision_blink_sound()

        QTimer.singleShot(2200, finish_blink)


    def hide_final_label(self):
        """Blendet das finale Status-Label aus."""
        if hasattr(self, "final_label") and self.final_label:
            self.final_label.hide()
            from PyQt6.QtWidgets import QApplication

            QApplication.processEvents()

    # ========================== NCam ==========================
    import os
    import platform
    import shutil

    def auto_detect_ncam_path(self):
        """Sucht den NCam Bonecrew Pfad automatisch auf Linux & Windows."""
        if hasattr(self, "hide_final_label"):
            self.hide_final_label()

        is_win = platform.system() == "Windows"
        project = "s3_ncam_bonecrew_test"
    
        # Basis-Pfade definieren
        if is_win:
            base_candidates = ["C:\\opt", "D:\\opt", os.path.expanduser("~")]
        else:
            base_candidates = ["/opt", os.path.expanduser("~")]

        search_paths = [os.path.join(b, project) for b in base_candidates]
        search_paths.append(os.getcwd()) # Aktuelles Verzeichnis einbeziehen

        # Suche nach der 's3' Datei (mit .exe unter Windows)
        binary = "s3.exe" if is_win else "s3"
    
        for p in search_paths:
            full_binary_path = os.path.normpath(os.path.join(p, binary))
            if os.path.exists(full_binary_path):
                self.NCAM_PATH = os.path.normpath(p)
                print(f"[AUTO-DETECT] NCam gefunden: {self.NCAM_PATH}")
                return True
            
        print("[AUTO-DETECT] NCam konnte nicht gefunden werden.")
        return False

    # ========================== SimpleBuild 4 ==========================
    import os
    import platform
    import shutil

    def auto_detect_s3_path(self):
        """Sucht S3 Installation automatisch auf Linux & Windows."""
        is_win = platform.system() == "Windows"
        binary = "s3.exe" if is_win else "s3"

        # Standard-Installationsorte
        if is_win:
            candidates = ["C:\\s3", "C:\\opt\\s3", os.path.join(os.path.expanduser("~"), "s3")]
        else:
            candidates = ["/opt/s3", "/usr/local/bin", "/usr/bin", os.path.expanduser("~/s3")]

        for c in candidates:
            # 1. Pfad ist ein Ordner, der die Binary enthält
            if os.path.isdir(c) and os.path.exists(os.path.join(c, binary)):
                self.S3_PATH = os.path.normpath(c)
                return True
            # 2. Pfad ist direkt die Binary selbst
            if os.path.isfile(c) and (c.endswith(binary)):
                self.S3_PATH = os.path.normpath(os.path.dirname(c))
                return True

        return False
    def auto_detect_s4_path(self):
        """Sucht SimpleBuild 4 Installation automatisch auf Linux & Windows."""
        is_win = platform.system() == "Windows"
        binary = "s4.exe" if is_win else "s4"

        # Standard-Installationsorte für SimpleBuild 4
        if is_win:
            candidates = [
                "C:\\simplebuild4", 
                "C:\\opt\\simplebuild4", 
                os.path.join(os.path.expanduser("~"), "simplebuild4")
            ]
        else:
            candidates = [
                "/opt/simplebuild4", 
                "/usr/local/bin", 
                "/usr/bin", 
                os.path.expanduser("~/simplebuild4")
            ]

        for c in candidates:
            # 1. Pfad ist ein Ordner, der die Binary enthält
            if os.path.isdir(c) and os.path.exists(os.path.join(c, binary)):
                self.S4_PATH = os.path.normpath(c)
                return True
            # 2. Pfad ist direkt die Binary selbst
            if os.path.isfile(c) and (c.endswith(binary)):
                self.S4_PATH = os.path.normpath(os.path.dirname(c))
                return True

        return False
    
    
    def find_existing_path(possible_paths):
        """Prüft eine Liste von Pfaden und gibt den ersten existierenden zurück."""
        import os

        for path in possible_paths:
            try:
                # Prüft, ob der Pfad existiert
                if os.path.exists(path):
                    # Wenn es ein Verzeichnis ist, prüfen ob es Dateien enthält
                    if os.path.isdir(path):
                        if os.listdir(path):
                            return path
                    # Wenn es eine Datei ist, direkt zurückgeben
                    else:
                        return path
            except Exception:
                continue
        return None

    def select_ncam_path_manually(self):
        """Dialog zur manuellen Auswahl des NCam Bonecrew Pfads."""
        from PyQt6.QtWidgets import QFileDialog, QMessageBox
        import os, platform

        lang = getattr(self, "LANG", "de").lower()[:2]
        t = {
            "de": {
                "hint": "NCam Bonecrew Ordner wählen", 
                "ok": "NCam Pfad gesetzt", 
                "err": "Datei 's3' nicht im Ordner gefunden!"
            },
            "en": {
                "hint": "Select NCam Bonecrew folder", 
                "ok": "NCam path set", 
                "err": "File 's3' not found in the selected folder!"
            },
        }.get(lang, {"hint": "Select NCam Bonecrew folder", "ok": "NCam path set", "err": "File 's3' not found!"})

        is_win = platform.system() == "Windows"
        default_start = "C:\\opt\\ncam" if is_win else "/opt/s3_ncam_bonecrew"
        start_path = getattr(self, "NCAM_PATH", default_start)
        
        chosen_dir = QFileDialog.getExistingDirectory(self, t["hint"], start_path)
        if not chosen_dir:
            return

        chosen_dir = os.path.normpath(chosen_dir)
        sub_folder = "s3_ncam_bonecrew"

        # KORREKTUR: Flexibler Check für Windows (s3 und s3.exe abdecken)
        possible_exes = ["s3", "s3.exe"] if is_win else ["s3"]
        exists = False

        for exe in possible_exes:
            path_direct = os.path.join(chosen_dir, exe)
            path_sub = os.path.join(chosen_dir, sub_folder, exe)

            if os.path.exists(path_direct) or os.path.exists(path_sub):
                # Wenn der User den Basis-Ordner gewählt hat, hängen wir den Unterordner an
                if os.path.exists(path_sub) and not chosen_dir.lower().endswith(sub_folder.lower()):
                    chosen_dir = os.path.join(chosen_dir, sub_folder)
                exists = True
                break

        if exists:
            self.NCAM_PATH = os.path.normpath(chosen_dir)
            save_config({"ncam_custom_path": self.NCAM_PATH}, gui_instance=self)
            
            if hasattr(self, "update_ui_texts"):
                self.update_ui_texts()
                
            QMessageBox.information(self, "OK", f"{t['ok']}:\n{self.NCAM_PATH}")
        else:
            QMessageBox.warning(self, "Error", t["err"])

    def start_ncam_install(self):
        """Startet die NCam Installation."""
        from PyQt6.QtWidgets import QFileDialog
        import os, platform
        
        if hasattr(self, "hide_final_label"):
            self.hide_final_label()

        is_de = getattr(self, "LANG", "de") == "de"
        default_start = "C:\\" if platform.system() == "Windows" else "/opt"
        start_path = getattr(self, "NCAM_PATH", default_start)

        chosen_dir = QFileDialog.getExistingDirectory(
            self, 
            "Installationsordner wählen" if is_de else "Select Installation Folder",
            start_path
        )

        if not chosen_dir:
            return 

        self.NCAM_PATH = os.path.normpath(chosen_dir)
    
        try:
            os.makedirs(self.NCAM_PATH, exist_ok=True)
            self.btn_ncam.setEnabled(False)
            self.btn_ncam.setText("⏳ ..." if is_de else "⏳ Busy...")

            # Nutzt den dedizierten NCam-Worker
            self.ncam_worker = NcamBonecrewInstallWorker(self.NCAM_PATH)
            self.ncam_worker.finished_signal.connect(self.on_ncam_finished)
            self.ncam_worker.start()
        except Exception as e:
            self.on_ncam_finished(False, f"Fehler beim Erstellen des Ordners: {str(e)}")

    def on_ncam_finished(self, success, message):
        """Nach Abschluss des Workers für NCam."""
        import os
        from PyQt6.QtWidgets import QMessageBox

        is_de = getattr(self, "LANG", "de") == "de"
        self.btn_ncam.setEnabled(True)

        if success:
            # 1. Pfad-Logik: Der Worker nutzt jetzt "s3_ncam_bonecrew"
            sub_folder = "s3_ncam_bonecrew"
            
            # Wir prüfen, ob der aktuelle NCAM_PATH den Unterordner bereits enthält
            current_base = getattr(self, "NCAM_PATH", "")
            if not current_base.lower().endswith(sub_folder.lower()):
                new_path = os.path.normpath(os.path.join(current_base, sub_folder))
            else:
                new_path = os.path.normpath(current_base)

            # 2. Instanz-Variable und Config permanent aktualisieren
            self.NCAM_PATH = new_path
            # Speichert den Pfad in der json, damit er beim nächsten Start direkt da ist
            save_config({"ncam_custom_path": self.NCAM_PATH}, gui_instance=self)

            # 3. Visuelles Feedback bei Erfolg
            self.btn_ncam.setStyleSheet(
                """
                QPushButton {
                    background-color: green;
                    color: white;
                    font-weight: bold;
                    border-radius: 5px;
                    text-align: center;
                }
                """
            )
            self.btn_ncam.setText("✅ Fertig" if is_de else "✅ Done")

            # UI-Texte aktualisieren, falls die Funktion existiert
            if hasattr(self, "update_ui_texts"):
                self.update_ui_texts()

            QMessageBox.information(
                self,
                "NCam Installation" if is_de else "NCam Setup",
                f"{message}\n\nPfad: {self.NCAM_PATH}"
                if is_de
                else f"{message}\n\nPath: {self.NCAM_PATH}"
            )
        else:
            # Fehler-Zustand: Roter Button
            self.btn_ncam.setStyleSheet(
                """
                QPushButton {
                    background-color: #e74c3c;
                    color: white;
                    font-weight: bold;
                    border-radius: 5px;
                    text-align: center;
                }
                """
            )
            self.btn_ncam.setText("❌ Fehler" if is_de else "❌ Error")

            QMessageBox.critical(
                self,
                "NCam Installation" if is_de else "NCam Setup",
                message
            )

            # Standard-Buttontext wiederherstellen
            if hasattr(self, "update_ui_texts"):
                self.update_ui_texts()

    def select_s4_path_manually(self, pos=None):
        """
        Dialog zur manuellen Auswahl des SimpleBuild 4 Pfads.
        Validiert flexibel auf 'simplebuild' und setzt den Pfad direkt auf den Zielordner.
        """
        from PyQt6.QtWidgets import QFileDialog, QMessageBox
        import os, platform

        lang = getattr(self, "LANG", "de").lower()[:2]
        t = {
            "de": {
                "hint": "SimpleBuild 4 Ordner wählen", 
                "ok": "S4 Pfad gesetzt",
                "err_title": "Fehler",
                "err_msg": "Die ausführbare S4-Datei wurde in diesem Ordner nicht gefunden!"
            },
            "en": {
                "hint": "Select S4 folder", 
                "ok": "S4 path set",
                "err_title": "Error",
                "err_msg": "The executable S4 file was not found in this folder!"
            },
        }.get(lang, {
            "hint": "Select SimpleBuild 4 folder", 
            "ok": "S4 path set",
            "err_title": "Error",
            "err_msg": "The executable S4 file was not found in this folder!"
        })

        is_win = platform.system() == "Windows"

        # Startpfad ermitteln (Fallback auf sinnvollen OS-Standard)
        default_start = "C:\\opt" if is_win else "/opt"
        start_path = getattr(self, "S4_PATH", default_start)
        if not os.path.exists(start_path):
            start_path = os.path.expanduser("~")

        chosen_dir = QFileDialog.getExistingDirectory(self, t["hint"], start_path)

        if not chosen_dir:
            return  # Abbruch durch den Nutzer

        chosen_dir = os.path.normpath(chosen_dir)
        sub_folder = "simplebuild4"
        possible_exes = ["simplebuild", "simplebuild.exe", "s4", "s4.exe"]
        
        final_path = None

        # Schleife prüft beide Ebenen separat
        for exe in possible_exes:
            path_direct = os.path.join(chosen_dir, exe)
            path_sub = os.path.join(chosen_dir, sub_folder, exe)
            
            # Fall A: User steht direkt im Ordner 'simplebuild4'
            if os.path.exists(path_direct):
                final_path = chosen_dir
                break
            # Fall B: User hat den übergeordneten Ordner gewählt
            elif os.path.exists(path_sub):
                final_path = os.path.join(chosen_dir, sub_folder)
                break

        if final_path:
            # Wir erzwingen, dass der Pfad sauber im Speicher landet
            self.S4_PATH = os.path.normpath(final_path)
            
            # Zentrales Speichern in der Config
            if "save_config" in globals():
                save_config({"s4_custom_path": self.S4_PATH}, gui_instance=self, silent=True)
                
            # UI direkt aktualisieren, damit der Button sofort grün wird ("S4 OK")
            if hasattr(self, "update_ui_texts"):
                self.update_ui_texts()
                
            QMessageBox.information(self, "OK", f"{t['ok']}:\n{self.S4_PATH}")
        else:
            QMessageBox.warning(self, t["err_title"], t["err_msg"])

    def start_s4_install(self):
        """Startet die SimpleBuild 4 Installation über Worker, Pfad wählbar und OS-optimiert."""
        import os, platform
        from PyQt6.QtWidgets import QFileDialog, QMessageBox, QInputDialog

        if hasattr(self, "hide_final_label"):
            self.hide_final_label()

        is_de = getattr(self, "LANG", "de") == "de"
        is_win = platform.system() == "Windows"

        # 1. Intelligenten Startpfad für den Dialog wählen
        # Falls S4_PATH noch nicht gesetzt ist, nehmen wir einen sinnvollen Standard
        default_base = "C:\\opt" if is_win else "/opt"
        start_path = getattr(self, "S4_PATH", os.path.expanduser("~"))
    
        # Falls der aktuelle Pfad nicht existiert, nimm die Basis
        if not os.path.exists(start_path):
            start_path = default_base if os.path.exists(default_base) else os.path.expanduser("~")

        chosen_dir = QFileDialog.getExistingDirectory(
            self,
            "Wähle S4 Installationsordner" if is_de else "Select S4 Installation Folder",
            start_path,
        )

        if not chosen_dir:
            return  # Abbruch durch Nutzer

        # --- WINDOWS & VM FIX: Pfad normalisieren ---
        # Macht aus / und \ den jeweils richtigen Trenner für das OS
        self.S4_PATH = os.path.normpath(chosen_dir)

        try:
            # Ordner erstellen (Dank deines Admin-Elevators klappt das auch auf C:\)
            os.makedirs(self.S4_PATH, exist_ok=True)
        
            # Test-Schreibzugriff (nur um sicherzugehen)
            test_file = os.path.join(self.S4_PATH, ".permissions_test")
            with open(test_file, "w") as f:
                f.write("test")
            os.remove(test_file)

        except Exception as e:
            QMessageBox.critical(
                self, 
                "Error", 
                f"Keine Schreibrechte in diesem Ordner!\n{str(e)}" if is_de 
                else f"No write permissions in this folder!\n{str(e)}"
            )
            return

        # 🚨 FIX: Den neu gewählten Pfad sofort dauerhaft in der config.json sichern 🚨
        if "save_config" in globals():
            save_config({"s4_custom_path": self.S4_PATH}, gui_instance=self, silent=True)

        # 2. UI Feedback mit korrekter deutscher Übersetzung
        self.btn_s4.setEnabled(False)
        busy_text = "⏳ Installation läuft..." if is_de else "⏳ Installing..."
        self.btn_s4.setText(busy_text)
    
        if hasattr(self, "log"): # Falls deine GUI eine Log-Funktion hat
           self.log("s4_patch_create_clone_start", "info") 

        # 3. Worker starten
        self.s4_worker = S4InstallWorker(self.S4_PATH)
        self.s4_worker.finished_signal.connect(self.on_s4_finished)
        self.s4_worker.start()

    def on_s4_finished(self, success, message):
        """Nach Abschluss des Workers für SimpleBuild 4."""
        import os
        from PyQt6.QtWidgets import QMessageBox

        is_de = getattr(self, "LANG", "de") == "de"
        self.btn_s4.setEnabled(True)

        if success:
            # 1. Pfad-Logik: Der Worker hat den spezifischen Unterordner erstellt
            sub_folder = "simplebuild4"
            
            # Wir prüfen, ob der aktuelle S4_PATH den Unterordner bereits enthält
            current_base = getattr(self, "S4_PATH", "")
            if not current_base.lower().endswith(sub_folder.lower()):
                new_path = os.path.normpath(os.path.join(current_base, sub_folder))
            else:
                new_path = os.path.normpath(current_base)

            # 2. Instanz-Variable und Config permanent aktualisieren
            self.S4_PATH = new_path
            # Speichert den Pfad in der json, damit er beim nächsten Start direkt da ist
            if "save_config" in globals():
                save_config({"s4_custom_path": self.S4_PATH}, gui_instance=self, silent=True)
            
            # 3. Visuelles Feedback über die zentrale UI-Logik steuern
            # Das sorgt für den korrekten "S4 OK" Text und das perfekte grüne Design
            if hasattr(self, "update_ui_texts"):
                self.update_ui_texts()

            QMessageBox.information(
                self, 
                "S4 Installation" if is_de else "S4 Setup", 
                f"{message}\n\nPfad: {self.S4_PATH}" if is_de else f"{message}\n\nPath: {self.S4_PATH}"
            )
        else:
            # Fehler-Zustand: Wir updaten das UI, damit der Button orange bleibt (bereit für neuen Versuch)
            if hasattr(self, "update_ui_texts"):
                self.update_ui_texts()
                
            QMessageBox.critical(
                self, 
                "SimpleBuild 4 Installation" if is_de else "SimpleBuild 4 Setup", 
                message
            )

    # ========================== S3 ==========================
    
    
    def select_s3_path_manually(self):
        """Dialog zur manuellen Auswahl des S3-Pfads."""
        from PyQt6.QtWidgets import QFileDialog, QMessageBox
        import os
        import platform
        import traceback

        lang = getattr(self, "LANG", "de").lower()[:2]

        if lang == "en":
            hint = "Select S3 folder"
            ok_text = "S3 path set"
            err_title = "Error"
            err_msg = "The S3 executable (s3 or s3.exe) was not found in this folder."
        else:
            hint = "S3-Ordner wählen"
            ok_text = "S3-Pfad gesetzt"
            err_title = "Fehler"
            err_msg = "Die ausführbare S3-Datei wurde in diesem Ordner nicht gefunden!"

        is_win = platform.system() == "Windows"
        default_start = "C:\\s3" if is_win else "/opt/s3"
        start_path = getattr(self, "S3_PATH", default_start)

        if not os.path.exists(start_path):
            start_path = os.path.expanduser("~")

        chosen_dir = QFileDialog.getExistingDirectory(
            self, hint, start_path
        )

        if not chosen_dir:
            return

        chosen_dir = os.path.normpath(chosen_dir)

        # Ausführbare Datei direkt oder im Unterordner s3 suchen
        found_path = None

        for exe in ("s3.exe", "s3"):
            direct = os.path.join(chosen_dir, exe)
            sub = os.path.join(chosen_dir, "s3", exe)

            if os.path.isfile(direct):
                found_path = chosen_dir
                break

            if os.path.isfile(sub):
                found_path = os.path.join(chosen_dir, "s3")
                break

        if found_path is None:
            QMessageBox.warning(self, err_title, err_msg)
            return

        old_path = getattr(self, "S3_PATH", default_start)
        self.S3_PATH = os.path.normpath(found_path)

        try:
            # Nur den Pfad speichern; keine unnötigen UI-Effekte auslösen.
            success = save_config(
                {"s3_custom_path": self.S3_PATH},
                gui_instance=None,
                silent=True
            )

            if not success:
                self.S3_PATH = old_path
                QMessageBox.critical(
                    self,
                    err_title,
                    "Der S3-Pfad konnte nicht gespeichert werden."
                )
                return

            # Die gespeicherte Konfiguration mit der GUI synchronisieren.
            if not isinstance(getattr(self, "current_config", None), dict):
                self.current_config = {}

            self.current_config["s3_custom_path"] = self.S3_PATH

            if hasattr(self, "update_ui_texts"):
                self.update_ui_texts()

            QMessageBox.information(
                self, "OK", f"{ok_text}:\n{self.S3_PATH}"
            )

        except Exception as exc:
            traceback.print_exc()

            QMessageBox.critical(
                self,
                err_title,
                f"Fehler beim Setzen des S3-Pfads:\n{exc}"
            )


    def start_s3_install(self):
        """Startet die S3 Installation über Worker, Pfad wählbar und OS-optimiert."""
        import os, platform
        from PyQt6.QtWidgets import QFileDialog, QMessageBox, QInputDialog

        if hasattr(self, "hide_final_label"):
            self.hide_final_label()

        is_de = getattr(self, "LANG", "de") == "de"
        is_win = platform.system() == "Windows"

        # 1. Intelligenten Startpfad für den Dialog wählen
        # Falls S3_PATH noch nicht gesetzt ist, nehmen wir einen sinnvollen Standard
        default_base = "C:\\opt" if is_win else "/opt"
        start_path = getattr(self, "S3_PATH", os.path.expanduser("~"))
    
        # Falls der aktuelle Pfad nicht existiert, nimm die Basis
        if not os.path.exists(start_path):
            start_path = default_base if os.path.exists(default_base) else os.path.expanduser("~")

        chosen_dir = QFileDialog.getExistingDirectory(
            self,
            "Wähle S3 Installationsordner" if is_de else "Select S3 Installation Folder",
            start_path,
        )

        if not chosen_dir:
            return  # Abbruch durch Nutzer

        # Nicht den Repository-Unterordner "support" als Installationsbasis
        # verwenden. Wenn .../s3/support gewählt wurde, auf .../s3 gehen;
        # andernfalls den Ordner oberhalb von support als Basis nehmen.
        chosen_path = os.path.normpath(chosen_dir)
        if os.path.basename(chosen_path).lower() == "support":
            parent_path = os.path.dirname(chosen_path)
            if os.path.basename(parent_path).lower() == "s3":
                chosen_path = parent_path
            else:
                chosen_path = os.path.dirname(chosen_path)

        # S3_PATH bezeichnet den gemeinsamen Basisordner; der Worker legt
        # das Repository direkt in <Basisordner>/s3 ab.
        self.S3_PATH = os.path.normpath(chosen_path)

        try:
            # Ordner erstellen (Dank deines Admin-Elevators klappt das auch auf C:\)
            os.makedirs(self.S3_PATH, exist_ok=True)
        
            # Test-Schreibzugriff (nur um sicherzugehen)
            test_file = os.path.join(self.S3_PATH, ".permissions_test")
            with open(test_file, "w") as f:
                f.write("test")
            os.remove(test_file)

        except Exception as e:
            QMessageBox.critical(
                self, 
                "Error", 
                f"Keine Schreibrechte in diesem Ordner!\n{str(e)}" if is_de 
                else f"No write permissions in this folder!\n{str(e)}"
            )
            return

        # 2. UI Feedback
        self.btn_s3.setEnabled(False)
        # Nutze ein Icon oder Text-Update
        busy_text = "⏳ Installation läuft..." if is_de else "⏳ Installing..."
        self.btn_s3.setText(busy_text)
    
        if hasattr(self, "log"): # Falls deine GUI eine Log-Funktion hat
            self.log("patch_create_clone_start", "info") 

        # 3. Worker starten
        # WICHTIG: Der S3InstallWorker muss intern os.path.join nutzen!
        # Repository auswählen
        repositories = list(S3InstallWorker.REPOSITORIES.keys())

        selected_repo, ok = QInputDialog.getItem(
            self,
            "S3 Installation",
            "Welches S3-Repository möchtest du installieren?",
            repositories,
            0,
            False,
        )

        if not ok or not selected_repo:
            self.btn_s3.setEnabled(True)
            self.btn_s3.setText("S3")
            return

        # Ausgewählte Repository-URL ermitteln
        repo_url = S3InstallWorker.REPOSITORIES[selected_repo]

        # Build-Logging am tatsächlich verwendeten S3-Button aktivieren.
        # Der Button ist mit start_s3_install() verbunden (nicht mit
        # start_s3_installation()). Deshalb muss das Log hier angelegt werden.
        from datetime import datetime
        try:
            log_dir = getattr(self, "build_log_dir", None)
            if not log_dir:
                log_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "build_logs")
                self.build_log_dir = log_dir
            os.makedirs(log_dir, exist_ok=True)
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            self.s3_build_log_file = os.path.join(log_dir, f"S3_build_{stamp}.log")
            with open(self.s3_build_log_file, "w", encoding="utf-8") as log_file:
                log_file.write(f"S3 Build-Log gestartet: {datetime.now().isoformat(timespec='seconds')}\n")
                log_file.write(f"Repository: {selected_repo}\nURL: {repo_url}\nZielpfad: {self.S3_PATH}\n")
                log_file.write("Status: S3-Worker wurde angefordert.\n\n")
            print(f"[BUILD-LOG] S3-Logdatei: {self.s3_build_log_file}")
        except Exception as log_error:
            self.s3_build_log_file = None
            print(f"[BUILD-LOG] S3-Logdatei konnte nicht erstellt werden: {log_error}")

        # Worker mit dem ausgewählten Repository starten
        self.s3_worker = S3InstallWorker(
            base_target_dir=self.S3_PATH,
            repo_url=repo_url,
            project_name=selected_repo,
            sub_folder="s3",
            parent=self,
        )

        self.s3_worker.finished_signal.connect(self.on_s3_finished)
        # Falls der Worker Logmeldungen bereitstellt, dauerhaft in dieselbe
        # Datei schreiben. Nicht jeder Worker besitzt dieses Signal.
        if hasattr(self.s3_worker, "log_signal"):
            self.s3_worker.log_signal.connect(self.append_s3_install_log)
        self.s3_worker.start()

    def on_s3_finished(self, success, message):
        """Nach Abschluss des Workers für S3."""
        from PyQt6.QtWidgets import QMessageBox

        is_de = getattr(self, "LANG", "de") == "de"
        log_path = getattr(self, "s3_build_log_file", None)
        if log_path:
            try:
                with open(log_path, "a", encoding="utf-8") as log_file:
                    log_file.write(f"\n[{datetime.now().isoformat(timespec='seconds')}] Ergebnis: {'ERFOLG' if success else 'FEHLER'}\n")
                    log_file.write(str(message) + "\n")
            except OSError as log_error:
                print(f"[BUILD-LOG] Abschluss konnte nicht geschrieben werden: {log_error}")
        self.btn_s3.setEnabled(True)

        # Visuelles Feedback über die zentrale UI-Logik steuern.
        # Das sucht nach der s3 Binary und setzt das perfekte grüne oder orange Design auf.
        if hasattr(self, "update_ui_texts"):
            self.update_ui_texts()

        if success:
            QMessageBox.information(
                self, 
                "S3 Installation" if is_de else "S3 Setup", 
                message
            )
        else:
            QMessageBox.critical(
                self, 
                "S3 Installation" if is_de else "S3 Setup", 
                message
            )


        from PyQt6.QtCore import QTimer

    def check_git_installed():
        """Prüft, ob Git im System-Pfad verfügbar ist."""
        git_path = shutil.which("git")
        if git_path:
            print(f"[SYSTEM] Git gefunden: {git_path}")
            return True
        else:
            print("[!] FEHLER: Git ist nicht installiert oder nicht im PATH.")
            return False
    
    def smooth_progress(pbar, start, end, duration=500):
        """
        Animiert die ProgressBar von start bis end in `duration` ms.
        """
        steps = max(abs(end - start), 1)
        interval = duration // steps
        value = start

        def update():
            nonlocal value
            if value >= end:
                timer.stop()
                return
            value += 1
            pbar.setValue(value)

        timer = QTimer()
        timer.timeout.connect(update)
        timer.start(interval)

    def animate_status_bar(self):
        """Wechselt den Zustand der User-LEDs für den Blink-Effekt mit Theme-Support.
        System-Status LEDs bleiben immer statisch.
        """
        # --- 0. NOTBREMSE: Verhindert Blinken beim Start ---
        if getattr(self, "is_loading", False):
            return

        if not hasattr(self, "user_leds") or not self.user_leds:
            return

        # Blink-Status umschalten
        self._blink_state = getattr(self, "_blink_state", False)
        self._blink_state = not self._blink_state

        # 1️⃣ Theme-Check
        is_matrix = self.current_config.get("theme_mode") == "matrix"

        # 2️⃣ Farben & Rahmen definieren
        if self._blink_state:
            # AN-Zustand
            color = "#00FF41" if is_matrix else "cyan"
            border = "1px solid #008F11" if is_matrix else "1px solid white"
        else:
            # AUS-Zustand
            color = "#002200" if is_matrix else "#222"
            border = "1px solid #004400" if is_matrix else "1px solid #444"

        # 3️⃣ Stylesheet auf User-LEDs anwenden
        style = f"background-color: {color}; border-radius: 5px; border: {border};"
        for led in self.user_leds:
            try:
                led.setStyleSheet(style)
            except:
                continue

    def _run_blink_sequence(self):
        """Führt genau 10 Farbwechsel (5 Blinks) aus und stoppt dann."""
        pbar = getattr(self, "progress_bar", None)
        if not pbar:
            return

        # Prüfen, ob wir fertig sind (10 Toggles = 5 Mal blinken)
        if self.blink_count >= 10:
            self.idle_blink_timer.stop()
            # Finaler "Steady" Style (Kein Grau!)
            self._apply_pbar_style(final=True)
            return

        # Farbwechsel
        self.blink_count += 1
        is_on = self.blink_count % 2 == 0
        self._apply_pbar_style(is_on)

    def _apply_pbar_style(self, is_on=True, final=False):
        """Hilfsfunktion für das Styling ohne Code-Duplikate."""
        pbar = getattr(self, "progress_bar", None)

        # Mode erkennen
        current_style = self.styleSheet()
        is_matrix = "#00ff00" in current_style.lower()

        if is_matrix:
            color = "#00FF00" if is_on else "#002200"
            bg = "#050505"
            border = "#00FF00"
        else:
            # Gold-Modus: Gold vs. Dunkel-Orange
            color = "#050504" if is_on else "#8B4513"
            bg = "#111111"
            border = "#444"

        # Falls wir fertig sind, setzen wir die Textfarbe fest auf Gold/Grün
        if final:
            color = "#0BF641" if not is_matrix else "#00FF00"

        pbar.setStyleSheet(
            f"""
            QProgressBar {{ 
                border: 2px solid {border}; border-radius: 8px; background-color: {bg}; 
                color: {color}; text-align: center; font-weight: 900; font-size: 20px; 
            }}
            QProgressBar::chunk {{ background: transparent; }}
        """
        )

    def open_custom_folder(self, path):
        """Öffnet Ordner mit Regenbogen-Progress und Sound beim Start & Ende."""

        # --- Final Label ausblenden ---
        if hasattr(self, "hide_final_label"):
            self.hide_final_label()
        elif hasattr(self, "final_label") and self.final_label:
            self.final_label.hide()
        import os, platform, subprocess
        from PyQt6.QtWidgets import QApplication
        from PyQt6.QtCore import QTimer

        # --- 1. SOUND BEIM ÖFFNEN (Start) ---
        if "safe_play" in globals():
            safe_play("service-login.oga")

        # Setup & Sprach-Check
        pbar = getattr(self, "progress_bar", None)
        lang = str(getattr(self, "LANG", "de")).lower()[:2]
        is_de = lang == "de"

        T_OPEN = "Öffne Ordner..." if is_de else "Opening folder..."
        T_DONE = "Ordner offen!" if is_de else "Folder opened!"

        if pbar:
            # REGENBOGEN-PROGRESS Setup
            rainbow = (
                "qlineargradient(x1:0, y1:0, x2:1, y2:0, "
                "stop:0.0 #FF0000, stop:0.2 #FF7F00, stop:0.4 #FFFF00, "
                "stop:0.6 #00FF00, stop:0.8 #0000FF, stop:1.0 #8B00FF);"
            )
            pbar.setStyleSheet(
                f"""
                QProgressBar {{
                    text-align: center; font-weight: 700; border: 2px solid #222;
                    border-radius: 6px; background-color: #111; color: black; font-size: 15pt;
                }}
                QProgressBar::chunk {{ background-color: {rainbow}; border-radius: 4px; }}
            """
            )
            pbar.setFormat(f"📂 {T_OPEN} %p%")
            pbar.setValue(20)
            pbar.show()
            QApplication.processEvents()

        # 2. Ordner-Logik: Windows und WSL unterstützen
        try:
            import shutil

            raw_path = os.path.expanduser(str(path or "").strip())

            if not raw_path:
                raise ValueError("Der Ordnerpfad ist leer.")

            is_windows_path = (
                len(raw_path) >= 3
                and raw_path[1] == ":"
                and raw_path[2] in ("\\", "/")
            ) or raw_path.startswith("\\\\")

            is_wsl = (
                platform.system() == "Linux"
                and (
                    "microsoft" in platform.release().lower()
                    or "WSL_DISTRO_NAME" in os.environ
                )
            )

            
            
            if is_wsl:
                explorer = shutil.which("explorer.exe") or "/mnt/c/Windows/explorer.exe"

                if not os.path.isfile(explorer):
                    raise FileNotFoundError(
                        "explorer.exe wurde in WSL nicht gefunden."
                    )

                if is_windows_path:
                    result = subprocess.run(
                        ["wslpath", "-u", raw_path],
                        capture_output=True,
                        text=True,
                        check=True,
                    )
                    linux_path = result.stdout.strip()
                else:
                    linux_path = os.path.abspath(raw_path)

                if not os.path.isdir(linux_path):
                    raise FileNotFoundError(
                        f"Der Ordner existiert nicht: {linux_path}"
                    )

                result = subprocess.run(
                    ["wslpath", "-w", linux_path],
                    capture_output=True,
                    text=True,
                    check=True,
                )
                windows_path = result.stdout.strip()

                subprocess.Popen(
                    [explorer, windows_path],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )

            else:
                folder = os.path.abspath(raw_path)

                if not os.path.isdir(folder):
                    raise FileNotFoundError(
                        f"Der Ordner existiert nicht: {folder}"
                    )

                if platform.system() == "Windows":
                    os.startfile(folder)
                elif platform.system() == "Linux":
                    subprocess.Popen(["xdg-open", folder])
                else:
                    subprocess.Popen(["open", folder])


            if pbar:
                pbar.setValue(100)

        except Exception as exc:
            QMessageBox.warning(
                self,
                "Ordner öffnen" if is_de else "Open folder",
                f"Ordner konnte nicht geöffnet werden:\n{exc}",
            )

            # --- 2. SOUND NACH DEM ÖFFNEN (Abschluss) ---
            if "safe_play" in globals():
                # Signalisiert, dass der Befehl durch ist
                safe_play("complete.oga")

            if pbar:
                pbar.setValue(100)
                pbar.setFormat(f"✅ {T_DONE} 100%")

        except Exception:
            if "safe_play" in globals():
                safe_play("dialog-error.oga")
            if pbar:
                pbar.setValue(0)

        # 3. AUTO-RESET (3 Sekunden) -> Zurück zu Orange/Gold
        if pbar:

            def restore_pbar():
                pbar.setValue(0)
                pbar.setFormat("%p%")
                pbar.setStyleSheet(
                    """
                    QProgressBar { border: 1px solid #444; border-radius: 8px; background-color: #1A1A1A; 
                    color: black; text-align: center; font-weight: bold; }
                    QProgressBar::chunk { background-color: qlineargradient(x1:0, y1:0, x2:1, y2:0, 
                    stop:0 #F37804, stop:1 #FFD700); border-radius: 8px; }
                """
                )

            QTimer.singleShot(3000, restore_pbar)

    
    
    def start_s3_menu(self):
        """Startet das gewählte S3-Menü unter Windows/WSL2 oder Linux."""

        if hasattr(self, "hide_final_label"):
            self.hide_final_label()
        elif hasattr(self, "final_label") and self.final_label:
            self.final_label.hide()

        import os
        import shutil
        import platform

        s3_exec = None
        is_de = getattr(self, "LANG", "de") == "de"
        is_win = platform.system() == "Windows"

        # ---------------------------------------------------------
        # WINDOWS: Variante auswählen und über WSL2 starten
        # ---------------------------------------------------------
        
        if is_win:
            from PyQt6.QtWidgets import QApplication, QMessageBox
            from windows_msys2_setup import prepare_tool, launch_tool_menu

            # Aktuelle Sprache sicher aus TEXTS laden
            lang = str(getattr(self, "LANG", "de")).lower()[:2]
            T = TEXTS.get(lang, TEXTS.get("de", {}))

            choice = QMessageBox(self)
            choice.setWindowTitle(
                T.get("s3_choice_title", "S3 Simplebuild auswählen")
            )
            choice.setText(
                T.get(
                    "s3_choice_text",
                    "Welches S3-Menü möchtest du starten?",
                )
            )
            choice.setInformativeText(
                T.get(
                    "s3_choice_info",
                    "Gorgone verwendet den Ordner 's3'.\n"
                    "Speedy005 verwendet den Ordner 's3_speedy005'.",
                )
            )

            gorgone_btn = choice.addButton(
                T.get("s3_choice_gorgone", "S3 Gorgone"),
                QMessageBox.ButtonRole.AcceptRole,
            )
            speedy_btn = choice.addButton(
                T.get("s3_choice_speedy005", "S3 Speedy005"),
                QMessageBox.ButtonRole.ActionRole,
            )
            choice.addButton(
                T.get("button_cancel", "Abbrechen"),
                QMessageBox.ButtonRole.RejectRole,
            )

            choice.exec()
            selected = choice.clickedButton()

            if selected not in (gorgone_btn, speedy_btn):
                return


            try:
                def _setup_log(line):
                    if hasattr(self, "info_text") and self.info_text:
                        safe_line = (
                            str(line)
                            .replace("&", "&amp;")
                            .replace("<", "&lt;")
                            .replace(">", "&gt;")
                        )
                        self.info_text.append(safe_line)
                    QApplication.processEvents()

                # Fester Projektpfad: nicht aus S3_PATH ableiten,
                # weil dieser Pfad aus der alten Konfiguration stammen kann.
                project_dir = os.path.dirname(os.path.abspath(__file__))

                if selected is gorgone_btn:
                    tool_name = "s3"
                    folder_name = "s3"
                else:
                    tool_name = "s3_speedy005"
                    folder_name = "s3_speedy005"

                variant_path = os.path.join(project_dir, folder_name)

                _setup_log(f"S3-Variante: {tool_name}")
                _setup_log(f"Zielordner: {variant_path}")

                # Vorhandenes Ziel niemals automatisch umbenennen,
                # löschen oder auf ein anderes Repository umstellen.
                s3_path = str(
                    prepare_tool(
                        tool_name,
                        variant_path,
                        log=_setup_log,
                    )
                )

                self.S3_PATH = s3_path

                if not isinstance(
                    getattr(self, "current_config", None), dict
                ):
                    self.current_config = {}

                self.current_config["s3_custom_path"] = s3_path

                if "save_config" in globals():
                    save_config(
                        {"s3_custom_path": s3_path},
                        gui_instance=self,
                        silent=True,
                    )

                launch_tool_menu(
                    tool_name,
                    s3_path,
                    log=_setup_log,
                )
                return

            except Exception as exc:
                QMessageBox.critical(
                    self,
                    "S3 / WSL2",
                    "WSL2-Einrichtung oder S3-Start fehlgeschlagen:\n"
                    f"{exc}",
                )
                return

        # ---------------------------------------------------------
        # LINUX: Bisherigen nativen Startablauf beibehalten
        # ---------------------------------------------------------
        default_start = "/opt/s3"
        s3_path = getattr(self, "S3_PATH", default_start)
        sub_folder = "s3"

        possible_names = ["s3"]
        possible_paths = []

        for name in possible_names:
            possible_paths.append(os.path.join(s3_path, name))
            possible_paths.append(
                os.path.join(s3_path, sub_folder, name)
            )

        possible_paths.extend([
            "/opt/s3_neu/s3",
            "/opt/s3/s3",
            os.path.expanduser("~/s3/s3"),
            shutil.which("s3"),
        ])

        for path in possible_paths:
            if path and os.path.exists(path) and not os.path.isdir(path):
                if os.access(path, os.X_OK):
                    s3_exec = os.path.normpath(path)

                    if (
                        sub_folder in path.lower()
                        and not s3_path.lower().endswith(sub_folder.lower())
                    ):
                        self.S3_PATH = os.path.normpath(
                            os.path.join(s3_path, sub_folder)
                        )
                    else:
                        self.S3_PATH = os.path.normpath(s3_path)

                    success = save_config(
                        {"s3_custom_path": self.S3_PATH},
                        gui_instance=None,
                        silent=True,
                    )

                    if not success:
                        from PyQt6.QtWidgets import QMessageBox
                        QMessageBox.warning(
                            self,
                            "Fehler",
                            "Der S3-Pfad konnte nicht gespeichert werden.",
                        )
                        return

                    if not isinstance(
                        getattr(self, "current_config", None), dict
                    ):
                        self.current_config = {}

                    self.current_config["s3_custom_path"] = self.S3_PATH
                    break

        if s3_exec:
            if hasattr(self, "append_info") and hasattr(self, "info_text"):
                msg = f"🚀 S3 Menü: {s3_exec}"
                self.append_info(self.info_text, msg, "info")

            self.open_terminal(
                s3_path=s3_exec,
                use_sudo=True,
            )
        else:
            err_msg = (
                "❌ Fehler: S3-Startdatei nicht gefunden!"
                if is_de
                else "❌ Error: S3 executable not found!"
            )

            if hasattr(self, "info_text") and self.info_text:
                self.info_text.append(
                    f'<br><span style="color:red;"><b>{err_msg}</b></span>'
                )

            self.open_terminal()



    def start_s4_menu(self):
        """Startet SimpleBuild 4 unter Windows in WSL2 und unter Linux nativ."""

        # --- Final Label ausblenden ---
        if hasattr(self, "hide_final_label"):
            self.hide_final_label()
        elif hasattr(self, "final_label") and self.final_label:
            self.final_label.hide()
        
        import os, shutil, platform, subprocess

        s4_exec = None
        is_de = getattr(self, "LANG", "de") == "de"
        is_win = platform.system() == "Windows"

        # 1. Basis-Pfad holen für S4
        default_start = "C:\\opt\\simplebuild4" if is_win else "/opt/simplebuild4"
        s4_path = getattr(self, "S4_PATH", default_start)
        if is_win:
            try:
                from windows_msys2_setup import prepare_tool
                from PyQt6.QtWidgets import QApplication, QMessageBox
                def _setup_log(line):
                    if hasattr(self, "info_text") and self.info_text:
                        self.info_text.append(str(line).replace("&", "&amp;").replace("<", "&lt;"))
                    QApplication.processEvents()
                s4_path = str(prepare_tool("s4", s4_path, log=_setup_log))
                self.S4_PATH = s4_path
                if "save_config" in globals():
                    save_config({"s4_custom_path": self.S4_PATH}, gui_instance=self, silent=True)
                from windows_msys2_setup import launch_tool_menu
                launch_tool_menu("s4", s4_path, log=_setup_log)
                return
            except Exception as exc:
                QMessageBox.critical(self, "S4 / WSL2", f"WSL2-Einrichtung oder S4-Start fehlgeschlagen:\n{exc}")
                return
        sub_folder = "simplebuild4"

        # Mögliche Dateinamen für das S4 Skript
        possible_names = ["simplebuild", "simplebuild.exe", "s4", "s4.exe"]
        possible_paths = []

        # Suchliste aufbauen
        for name in possible_names:
            possible_paths.append(os.path.join(s4_path, name))
            possible_paths.append(os.path.join(s4_path, sub_folder, name))

        # System-Fallbacks
        if is_win:
            possible_paths.extend([
                "C:\\opt\\simplebuild4\\simplebuild",
                "C:\\opt\\simplebuild4\\simplebuild.exe",
                "C:\\s4\\simplebuild",
                "C:\\s4\\simplebuild.exe",
                os.path.join(os.path.expanduser("~"), "Downloads", "simplebuild4", "s4")
            ])
        else:
            possible_paths.extend([
                "/opt/simplebuild4/simplebuild",
                "/opt/s4/simplebuild",
                os.path.expanduser("~/simplebuild4/simplebuild"),
                shutil.which("simplebuild"),
                shutil.which("s4"),
            ])

        # 2. Den ersten Treffer validieren
        for path in possible_paths:
            if path and os.path.exists(path) and not os.path.isdir(path):
                if is_win or os.access(path, os.X_OK):
                    s4_exec = os.path.normpath(path)
                
                    if sub_folder in path.lower() and not s4_path.lower().endswith(sub_folder.lower()):
                       self.S4_PATH = os.path.normpath(os.path.join(s4_path, sub_folder))
                       if "save_config" in globals():
                            save_config({"s4_custom_path": self.S4_PATH}, gui_instance=self, silent=True)
                    break

        # 3. Ausführung
        if s4_exec:
            if hasattr(self, "append_info") and hasattr(self, "info_text"):
                msg = f"🚀 S4 Menü: {s4_exec}"
                self.append_info(self.info_text, msg, "info")

            # --- BC FIX FÜR DIE GLOBALEN USER-PROFILEDATEIEN ---
            # --- FORCIERTER BASH-AUFRUF UNTER WINDOWS ---
            if is_win:
                # Suchen nach Git Bash oder MSYS2 Bash
                bash_executable = None
                for potential_bash in [
                    os.environ.get("MSYS2_BASH", r"C:\msys64\usr\bin\bash.exe"),
                    os.path.join(os.environ.get("MSYS2_ROOT", r"C:\msys64"), "usr", "bin", "bash.exe"),
                    r"C:\tools\msys64\usr\bin\bash.exe",
                ]:
                    if potential_bash and os.path.exists(potential_bash):
                        bash_executable = potential_bash
                        break
                
                if bash_executable:
                    # Unix-konforme Pfade erzeugen
                    s4_unix = s4_exec.replace("\\", "/")
                    exec_dir = os.path.dirname(s4_exec).replace("\\", "/")
                    
                    # Startet ein neues, echtes Konsolenfenster direkt in der Bash mit dem Menü-Parameter
                    # Dadurch interpretiert Windows die Datei niemals wieder als Python-Datei!
                    from windows_msys2_setup import bash_environment
                    s4_cmd = (f'export MSYSTEM=UCRT64; export PATH=/ucrt64/bin:/usr/bin:/bin; '
                              f'cd "{exec_dir}" || exit 2; ./{os.path.basename(s4_unix)} menu; exec bash -i')
                    subprocess.Popen(
                        [bash_executable, "--login", "-i", "-c", s4_cmd],
                        env=bash_environment(), creationflags=subprocess.CREATE_NEW_CONSOLE
                    )
                    return

            # Normaler Linux-Start oder Fallback, falls oben keine Bash gefunden wurde
            self.open_terminal(s3_path=s4_exec, use_sudo=not is_win)
            
        else:
            err_msg = (
                "❌ Fehler: SimpleBuild 4 Startdatei nicht gefunden!"
                if is_de
                else "❌ Error: SimpleBuild 4 executable not found!"
            )
            if hasattr(self, "info_text") and self.info_text:
                self.info_text.append(f'<br><span style="color:red;"><b>{err_msg}</b></span>')

            self.open_terminal()

    def start_ncam_menu(self):
        """Startet NCam Bonecrew unter Windows in WSL2 und unter Linux nativ."""

        # --- Final Label ausblenden ---
        if hasattr(self, "hide_final_label"):
            self.hide_final_label()
        elif hasattr(self, "final_label") and self.final_label:
            self.final_label.hide()

        import os, shutil, platform, subprocess, urllib.request, tarfile

        is_de = getattr(self, "LANG", "de") == "de"
        is_win = platform.system() == "Windows"

        # 1. Pfade ermitteln für NCam / S3
        default_start = "C:\\opt\\ncam" if is_win else "/opt/s3_ncam_bonecrew"
        ncam_path = getattr(self, "NCAM_PATH", default_start)
        if is_win:
            try:
                from windows_msys2_setup import prepare_tool
                from PyQt6.QtWidgets import QApplication, QMessageBox
                def _setup_log(line):
                    if hasattr(self, "info_text") and self.info_text:
                        self.info_text.append(str(line).replace("&", "&amp;").replace("<", "&lt;"))
                    QApplication.processEvents()
                ncam_path = str(prepare_tool("ncam", ncam_path, log=_setup_log))
                self.NCAM_PATH = ncam_path
                if "save_config" in globals():
                    save_config({"ncam_custom_path": self.NCAM_PATH}, gui_instance=self, silent=True)
                from windows_msys2_setup import launch_tool_menu
                launch_tool_menu("ncam", ncam_path, log=_setup_log)
                return
            except Exception as exc:
                QMessageBox.critical(self, "NCam Bonecrew / WSL2", f"WSL2-Einrichtung oder NCam-Start fehlgeschlagen:\n{exc}")
                return
        sub_folder = "s3_ncam_bonecrew"

        possible_names = ["s3", "s3.exe"] if is_win else ["s3"]
        ncam_exec = None

        for name in possible_names:
            path_direct = os.path.join(ncam_path, name)
            path_sub = os.path.join(ncam_path, sub_folder, name)

            if os.path.exists(path_direct) and not os.path.isdir(path_direct):
                ncam_exec = os.path.normpath(path_direct)
                break
            elif os.path.exists(path_sub) and not os.path.isdir(path_sub):
                ncam_exec = os.path.normpath(path_sub)
                if sub_folder in path_sub.lower() and not ncam_path.lower().endswith(sub_folder.lower()):
                    self.NCAM_PATH = os.path.normpath(os.path.join(ncam_path, sub_folder))
                    if "save_config" in globals():
                        save_config({"ncam_custom_path": self.NCAM_PATH}, gui_instance=self, silent=True)
                break

        # 2. Ausführung
        if ncam_exec:
            if hasattr(self, "append_info") and hasattr(self, "info_text"):
                msg = f"🚀 NCam Menü: {ncam_exec}"
                self.append_info(self.info_text, msg, "info")

            # --- BC FIX ÜBER DIE GLOBALEN USER-PROFILEDATEIEN ---
            # Startet Terminal exakt wie das funktionierende S3-Menü
            self.open_terminal(s3_path=ncam_exec, use_sudo=not is_win)
        else:
            err_msg = f"❌ NCam nicht gefunden in: {ncam_path}"
            if hasattr(self, "info_text") and self.info_text:
                self.info_text.append(f'<br><span style="color:orange;"><b>{err_msg}</b></span>')

    def find_s3_executable(self):
        """Sucht automatisch nach der s3-Startdatei an bekannten Orten."""
        import os, shutil, platform

        is_win = platform.system() == "Windows"
        
        # 1. Prüfen, ob s3 global im System-PATH bekannt ist
        system_path = shutil.which("s3")
        if system_path:
            return system_path

        # 2. Dynamische Liste der wahrscheinlichsten Verzeichnisse generieren
        search_dirs = []

        # Füge die aktuell in der Instanz konfigurierten Pfade als höchste Priorität hinzu
        for path_attr in ["S3_PATH", "NCAM_PATH", "S4_PATH"]:
            current_val = getattr(self, path_attr, None)
            if current_val:
                search_dirs.append(current_val)

        # Standard-Fallback-Verzeichnisse für Linux und Windows
        if is_win:
            search_dirs.extend([
                "C:\\s3",
                "C:\\opt\\ncam",
                "C:\\opt\\s4",
                "C:\\opt\\s3",
            ])
        else:
            search_dirs.extend([
                "/opt/s3",
                "/opt/s3_ncam_bonecrew",
                "/opt/s4",
                os.path.expanduser("~/s3"),
                os.path.expanduser("~/simplebuild"),
                "/var/lib/s3",
            ])

        # Unterordner-Strukturen, die von den Workern angelegt werden
        sub_folders = ["", "s3", "s3_ncam_bonecrew", "s4"]
        
        # Mögliche Dateinamen (S4 nutzt simplebuild/s4, die anderen s3)
        possible_exes = ["s3", "s3.exe", "s4", "s4.exe", "simplebuild", "simplebuild.exe"]

        # 3. Such-Schleife über alle Pfad-Kombinationen
        for base_dir in search_dirs:
            if not base_dir or not os.path.exists(base_dir):
                continue
                
            for sub in sub_folders:
                for exe in possible_exes:
                    full_path = os.path.normpath(os.path.join(base_dir, sub, exe))
                    
                    if os.path.exists(full_path) and not os.path.isdir(full_path):
                        # Rechteprüfung: os.X_OK nur auf Linux/macOS erzwingen
                        if is_win or os.access(full_path, os.X_OK):
                            return full_path

        return None  # Nichts gefunden


    def apply_global_button_style(self, text_color=None):
        """Wendet das aktuelle Farbschema auf alle QPushButtons an; Stats bleibt unberührt."""
        from PyQt6.QtWidgets import QPushButton
        from PyQt6.QtCore import Qt

        global current_diff_colors
        colors = globals().get("current_diff_colors", {}) or {}
        # Ein explizit übergebener Textfarbwert wird absichtlich ignoriert, wenn
        # ein gültiges Theme existiert: so gewinnt immer das ausgewählte Schema.
        fg = colors.get("fg", colors.get("text", text_color or "#FFFFFF"))
        bg = colors.get("bg", "#2F2F2F")
        hover = colors.get("hover", "#444444")
        active = colors.get("active", "#222222")
        is_de = str(getattr(self, "LANG", "de")).lower().startswith("de")

        # Widget-Attribute finden, damit Tooltips bei jedem Sprachwechsel neu
        # gesetzt werden und keine Texte der vorherigen Sprache zurückbleiben.
        attr_by_id = {}
        try:
            for attr_name, value in vars(self).items():
                if isinstance(value, QPushButton):
                    attr_by_id[id(value)] = attr_name
        except Exception:
            pass

        tooltip_texts = {
            "btn_patch_online": (
                "Lädt verfügbare Patches aus dem Git-Repository und zeigt deren Versionen an."
                if is_de else
                "Loads available patches from the Git repository and shows their versions."
            ),
            "telemetry_cb": (
                "Anonyme Nutzungsstatistik aktivieren oder deaktivieren."
                if is_de else
                "Enable or disable anonymous usage statistics."
            ),
            "log_button": (
                "Speichert den aktuellen Log-/Protokollinhalt in einer Datei."
                if is_de else
                "Saves the current log output to a file."
            ),
            "btn_modifier": (
                "Ändert den Namen des Patch-Autors."
                if is_de else
                "Changes the patch author name."
            ),
            "btn_repo_url": (
                "Zeigt oder ändert die URL des OSCam-Emu-Repositories."
                if is_de else
                "Shows or changes the OSCam-Emu repository URL."
            ),
            "btn_open_work": (
                "Öffnet den Arbeitsordner."
                if is_de else "Opens the working folder."
            ),
            "btn_open_temp": (
                "Öffnet den lokalen temporären Git-Clone."
                if is_de else "Opens the local temporary Git clone."
            ),
            "btn_open_emu": (
                "Öffnet das OSCam-Emu-Repository."
                if is_de else "Opens the OSCam-Emu repository."
            ),
            "btn_check_tools": (
                "Prüft Compiler und benötigte Abhängigkeiten."
                if is_de else "Checks compilers and required dependencies."
            ),
            "btn_check_commit": (
                "Prüft den aktuellen Git-Commit."
                if is_de else "Checks the current Git commit."
            ),
            "header_btn_s3_ncam": (
                "Wählt den Patch-Ordner für S3/NCam aus."
                if is_de else "Selects the S3/NCam patch folder."
            ),
            "header_btn_s4": (
                "Wählt den S4-Patch-Ordner aus."
                if is_de else "Selects the S4 patch folder."
            ),
            "header_btn_patch_ncam": (
                "Kopiert den Patch in den konfigurierten S3/NCam-Ordner."
                if is_de else "Copies the patch to the configured S3/NCam folder."
            ),
            "header_btn_patch_s4": (
                "Kopiert den Patch in den konfigurierten S4-Ordner."
                if is_de else "Copies the patch to the configured S4 folder."
            ),
            "btn_s3": (
                "Linksklick: S3 öffnen/starten. Rechtsklick: Installationspfad ändern."
                if is_de else "Left-click: open/start S3. Right-click: change installation path."
            ),
            "btn_s4": (
                "Linksklick: S4 öffnen/starten. Rechtsklick: Installationspfad ändern."
                if is_de else "Left-click: open/start S4. Right-click: change installation path."
            ),
            "btn_ncam": (
                "Linksklick: NCam öffnen/starten. Rechtsklick: Installationspfad ändern."
                if is_de else "Left-click: open/start NCam. Right-click: change installation path."
            ),
            "info_button": (
                "Zeigt Informationen zum Programm."
                if is_de else "Shows information about the application."
            ),
            "matrix_btn": (
                "Schaltet den Matrix-Modus um."
                if is_de else "Toggles Matrix mode."
            ),
            "oscam_source_button": (
                "Öffnet oder ändert die OSCam-Quellrepository-URL."
                if is_de else "Opens or changes the OSCam source repository URL."
            ),
            "patch_emu_git_button": (
                "Verwaltet das OSCam-Emu-Git-Repository und den Patch."
                if is_de else "Manages the OSCam-Emu Git repository and patch."
            ),
            "github_upload_patch_button": (
                "Lädt den aktuellen Patch zu GitHub hoch."
                if is_de else "Uploads the current patch to GitHub."
            ),
            "clean_emu_button": (
                "Bereinigt das lokale OSCam-Emu-Git-Repository."
                if is_de else "Cleans the local OSCam-Emu Git repository."
            ),
        }

        for btn in self.findChildren(QPushButton):
            try:
                attr_name = attr_by_id.get(id(btn), "")
                label = btn.text().replace("&", "").replace("\n", " ").strip()
                label = " ".join(label.split())
                if attr_name in tooltip_texts:
                    tooltip = tooltip_texts[attr_name]
                elif label:
                    tooltip = (f"Aktion: {label}" if is_de else f"Action: {label}")
                else:
                    tooltip = ("Aktion ausführen" if is_de else "Perform action")
                btn.setToolTip(tooltip)
                btn.setToolTipDuration(10000)
                btn.setCursor(Qt.CursorShape.PointingHandCursor)

                # Ein einheitliches Theme für alle Buttons. Stats bleibt ein
                # Toggle: aktiv rote Schrift, inaktiv die Theme-Textfarbe.
                if btn is getattr(self, "telemetry_cb", None):
                    btn.setStyleSheet(f"""
                        QPushButton {{ color: {fg}; background-color: {bg};
                            border: 2px solid {fg}; border-radius: 8px;
                            padding: 3px 8px; font-weight: bold; }}
                        QPushButton:hover {{ color: {fg}; background-color: {hover};
                            border: 2px solid {fg}; }}
                        QPushButton:checked {{ color: #FF0000; background-color: {bg};
                            border: 2px solid #FF0000; }}
                        QPushButton:checked:hover {{ color: #FF0000;
                            background-color: {hover}; border: 2px solid #FF0000; }}
                        QPushButton:pressed {{ background-color: {active}; }}
                        QPushButton:disabled {{ color: #777777; background-color: {active}; }}
                        QToolTip {{ color: {fg}; background-color: {bg};
                            border: 2px solid {fg}; padding: 5px; }}
                    """)
                else:
                    btn.setStyleSheet(f"""
                        QPushButton {{ color: {fg}; background-color: {bg};
                            border: 2px solid {fg}; border-radius: 8px;
                            padding: 5px; font-weight: bold; }}
                        QPushButton:hover {{ color: {fg}; background-color: {hover};
                            border: 2px solid {fg}; }}
                        QPushButton:pressed {{ color: {fg}; background-color: {active};
                            border: 2px solid {fg}; }}
                        QPushButton:disabled {{ color: #777777; background-color: {active}; }}
                        QToolTip {{ color: {fg}; background-color: {bg};
                            border: 2px solid {fg}; padding: 5px; }}
                    """)
            except RuntimeError:
                continue

    def on_telemetry_changed(self, checked):
        """Schaltet Stats um und speichert den Zustand dauerhaft."""
        try:
            checked = bool(checked)
            save_setting("allow_telemetry", checked)
            is_de = str(getattr(self, "LANG", "de")).lower().startswith("de")
            status = ("aktiviert" if checked else "deaktiviert") if is_de else ("enabled" if checked else "disabled")
            if hasattr(self, "info_text") and self.info_text is not None:
                message = f"Stats: {status}"
                if hasattr(self, "append_info"):
                    self.append_info(self.info_text, message, "success")
                else:
                    self.info_text.append(message)
            print(f"Stats {status}; allow_telemetry={checked}")
            # Die Status-Infobox wird bei jeder Änderung angezeigt.
            title = "Stats-Einstellung" if is_de else "Stats Settings"
            detail = (
                f"Anonyme Nutzungsstatistik ist jetzt {status}.\n\n"
                "Die Einstellung wird gespeichert und beim nächsten Start beibehalten."
                if is_de else
                f"Anonymous usage statistics are now {status}.\n\n"
                "This setting is saved and kept for the next launch."
            )
            QMessageBox.information(self, title, detail)
        except Exception as exc:
            print(f"Stats-Einstellung konnte nicht gespeichert werden: {exc}")
            try:
                QMessageBox.warning(self, "Stats", str(exc))
            except Exception:
                pass

    def choose_oscam_source(self):
        """Öffnet eine separate Auswahlbox für das OSCam-Quellrepository."""
        from PyQt6.QtWidgets import QInputDialog
        is_de = str(getattr(self, "LANG", "de")).lower().startswith("de")
        names = list(STREAM_REPO_CHOICES.keys())
        current = get_selected_stream_repo_name()
        selected, accepted = QInputDialog.getItem(
            self,
            "OSCam-Quelle auswählen" if is_de else "Select OSCam source",
            "Repository für Patch-Erstellung:" if is_de else "Repository for patch creation:",
            names,
            names.index(current) if current in names else 0,
            False,
        )
        if not accepted or selected not in STREAM_REPO_CHOICES:
            return
        try:
            config_path = globals().get("CONFIG_FILE", "config.json")
            config = {}
            if os.path.isfile(config_path):
                with open(config_path, "r", encoding="utf-8") as handle:
                    loaded = json.load(handle)
                    if isinstance(loaded, dict):
                        config = loaded
            config["oscam_source"] = selected
            with open(config_path, "w", encoding="utf-8") as handle:
                json.dump(config, handle, indent=4, ensure_ascii=False)
            # Aktuelle Laufzeit-Workflows verwenden ebenfalls die Auswahl.
            globals()["STREAMREPO_URL"] = STREAM_REPO_CHOICES[selected]
            self.oscam_source_button.setText("Oscam Git URL")
            message = f"OSCam-Quelle eingestellt: {selected}\n{STREAM_REPO_CHOICES[selected]}"
            if hasattr(self, "append_info"):
                self.append_info(self.info_text, message, "success")
            elif hasattr(self, "info_text"):
                self.info_text.append(message)
        except Exception as exc:
            QMessageBox.critical(
                self,
                "OSCam-Quelle",
                f"Einstellung konnte nicht gespeichert werden:\n{exc}",
            )

    def export_log(self):
        """Speichert Log als Textdatei mit originalem Sound-Code, Regenbogen und Gold-Reset."""
        import shutil, subprocess, os
        from datetime import datetime
        from PyQt6.QtWidgets import QFileDialog, QApplication
        from PyQt6.QtCore import QTimer

        # --- 1. ORIGINAL SOUND BEIM STARTEN (Klick) ---
        for player in ["paplay", "pw-play", "aplay"]:
            if shutil.which(player):
                start_sound = (
                    "/usr/share/sounds/freedesktop/stereo/message-new-instant.oga"
                )
                if os.path.exists(start_sound):
                    subprocess.Popen(
                        [player, start_sound],
                        stderr=subprocess.DEVNULL,
                        stdout=subprocess.DEVNULL,
                    )
                break

        text = self.info_text.toPlainText()
        if not text.strip():
            return

        lang = str(getattr(self, "LANG", "de")).lower()[:2]
        is_de = lang == "de"
        pbar = getattr(self, "progress_bar", None)

        # 2. REGENBOGEN-PROGRESS AKTIVIEREN
        if pbar:
            rainbow = (
                "qlineargradient(x1:0, y1:0, x2:1, y2:0, "
                "stop:0.0 #FF0000, stop:0.2 #FF7F00, stop:0.4 #FFFF00, "
                "stop:0.6 #00FF00, stop:0.8 #0000FF, stop:1.0 #8B00FF);"
            )
            pbar.setStyleSheet(
                f"""
                QProgressBar {{
                    text-align: center; font-weight: 900; border: 2px solid #222;
                    border-radius: 8px; background-color: #111; color: black; font-size: 14pt;
                }}
                QProgressBar::chunk {{ background: {rainbow}; border-radius: 6px; }}
            """
            )
            pbar.setFormat("📝 " + ("Speichere Log..." if is_de else "Saving Log..."))
            pbar.setValue(30)
            pbar.show()
            QApplication.processEvents()

        # 3. DATEI-DIALOG
        t_title = "Log speichern" if is_de else "Save Log"
        t_filter = "Textdateien (*.txt)" if is_de else "Text Files (*.txt)"
        filename = (
            f"oscam_patch_manager_log_{datetime.now().strftime('%Y%m%d_%H%M')}.txt"
        )

        file_path, _ = QFileDialog.getSaveFileName(self, t_title, filename, t_filter)

        if file_path:
            try:
                with open(file_path, "w", encoding="utf-8") as f:
                    f.write(text)

                # Erfolgsmeldung im Log-Fenster
                if hasattr(self, "append_info"):
                    self.append_info(
                        self.info_text, f"✅ Log gespeichert: {file_path}", "success"
                    )

                # --- 4. ORIGINAL SOUND BEIM ERFOLG ---
                for player in ["paplay", "pw-play", "aplay"]:
                    if shutil.which(player):
                        success_sound = (
                            "/usr/share/sounds/freedesktop/stereo/complete.oga"
                        )
                        if os.path.exists(success_sound):
                            subprocess.Popen(
                                [player, success_sound],
                                stderr=subprocess.DEVNULL,
                                stdout=subprocess.DEVNULL,
                            )
                        break

                if pbar:
                    pbar.setValue(100)
                    pbar.setFormat("✅ " + ("Gespeichert!" if is_de else "Saved!"))

            except Exception as e:
                if hasattr(self, "append_info"):
                    self.append_info(self.info_text, f"❌ Fehler: {e}", "error")
        else:
            # Abgebrochen
            if pbar:
                pbar.setValue(0)

        # 5. AUTO-RESET ZU DEINEM GOLD-BLINKEN (pbar_idle)
        if hasattr(self, "pbar_idle"):
            QTimer.singleShot(2500, self.pbar_idle)

    def copy_to_clipboard(self):
        """Kopiert den Log in die Zwischenablage."""
        QApplication.clipboard().setText(self.info_text.toPlainText())
        # Einheitlicher Feedback-Ton auf Windows und Linux.
        safe_play("dialog-information.oga")

    def _play_sys_sound(self, sound_type="standard"):
        """Spielt Systemfeedback über den gemeinsamen Windows/Linux-Backend."""
        manager = getattr(self, "sound_manager", None) or _ACTIVE_SOUND_MANAGER
        if manager is None:
            return False
        kind = str(sound_type).lower()
        if kind in ("error", "critical", "failure"):
            return manager.play_error()
        if kind in ("warning", "exclamation"):
            return manager.play_warning()
        if kind in ("click", "button"):
            return manager.play("click")
        return manager.play_info()

    def init_button_signals(self):
        """Verknüpft Ordner- und Funktions-Buttons."""
        import os
        from PyQt6.QtCore import Qt
        from PyQt6.QtWidgets import QMenu, QFileDialog
        from config_manager import save_config

        path_map = {
            "btn_open_temp": getattr(
                self, "TEMP_REPO", os.path.join(os.getcwd(), "oscam-svn")
            ),
            "btn_open_emu": getattr(
                self, "PATCH_EMU_GIT_DIR",
                os.path.join(os.getcwd(), "oscam-emu-patch")
            ),
        }

        # Arbeitsordner: immer den aktuell gespeicherten Wert verwenden
        work_button = getattr(self, "btn_open_work", None)
        if work_button:
            try:
                work_button.clicked.disconnect()
            except (TypeError, RuntimeError):
                pass

            work_button.clicked.connect(
                lambda checked=False: self.open_custom_folder(
                    getattr(self, "WORK_DIR", os.getcwd())
                )
            )

            work_button.setContextMenuPolicy(
                Qt.ContextMenuPolicy.CustomContextMenu
            )

            # Vorherige Kontextmenü-Verbindung möglichst entfernen
            try:
                work_button.customContextMenuRequested.disconnect()
            except (TypeError, RuntimeError):
                pass

            def choose_work_dir(position):
                menu = QMenu(work_button)
                action_choose = menu.addAction(
                    "Anderen Arbeitsordner auswählen …"
                )
                selected = menu.exec(work_button.mapToGlobal(position))

                if selected != action_choose:
                    return

                current_dir = getattr(self, "WORK_DIR", os.getcwd())
                folder = QFileDialog.getExistingDirectory(
                    self,
                    "Arbeitsordner auswählen",
                    current_dir
                )

                if not folder:
                    return

                folder = os.path.normpath(folder)
                self.WORK_DIR = folder

                if not isinstance(getattr(self, "current_config", None), dict):
                    self.current_config = {}

                self.current_config["work_dir"] = folder

                save_config(
                    self.current_config,
                    gui_instance=self,
                    silent=True
                )

            work_button.customContextMenuRequested.connect(choose_work_dir)

        # Temp-Repo und EMU-Repo
        for btn_name, path in path_map.items():
            btn = getattr(self, btn_name, None)
            if not btn:
                continue

            try:
                btn.clicked.disconnect()
            except (TypeError, RuntimeError):
                pass

            btn.clicked.connect(
                lambda checked=False, p=path: self.open_custom_folder(p)
            )

            if hasattr(self, "_play_sys_sound"):
                btn.clicked.connect(
                    lambda checked=False: self._play_sys_sound("standard")
                )

        # Funktions-Buttons
        func_names = {
            "btn_check_tools": "run_full_system_check",
            "restart_tool_button": "restart_app",
            "clean_emu_button": "clean_emu_data",
            "patch_emu_git_button": "run_patch_process",
            "btn_edit_patch": "edit_patch_header",
        }

        for btn_attr, method_name in func_names.items():
            btn = getattr(self, btn_attr, None)
            method = getattr(self, method_name, None)

            if btn and method:
                try:
                    btn.clicked.disconnect()
                except (TypeError, RuntimeError):
                    pass
                btn.clicked.connect(method)
            elif btn:
                btn.setEnabled(False)
                btn.setToolTip(
                    f"Fehler: Methode '{method_name}' fehlt!"
                )

    def setup_online_patch_ui(self, parent_layout):
        """Erstellt die UI für den Online-Download inklusive Sound-Feedback."""
        from PyQt6.QtWidgets import (
            QGroupBox,
            QVBoxLayout,
            QComboBox,
            QPushButton,
            QLabel,
        )
        import locale

        # Spracherkennung
        try:
            loc = locale.getlocale()[0] or locale.getdefaultlocale()[0] or "en"
            lang = loc[:2].lower()
        except:
            lang = "en"
        self.lang = lang  # Speichern für handle_online_patch_button

        # Lokalisierung
        if lang == "de":
            self.T_PATCH_TITLE = "🌐 Online Patch-Downloader"
            self.T_PATCH_SEL = "Quelle wählen:"
            self.T_PATCH_BTN = "Patch laden & prüfen"
        else:
            self.T_PATCH_TITLE = "🌐 Online Patch-Downloader"
            self.T_PATCH_SEL = "Select Source:"
            self.T_PATCH_BTN = "Download & Verify Patch"

        # UI Komponenten
        self.group_online = QGroupBox(self.T_PATCH_TITLE)
        layout_online = QVBoxLayout()

        self.combo_source = QComboBox()
        self.combo_source.addItems(ONLINE_PATCHES.keys())
        self.combo_source.setMinimumHeight(50)

        self.btn_online = QPushButton(self.T_PATCH_BTN)
        self.btn_online.setMinimumHeight(50)
        self.btn_online.setStyleSheet(
            "font-weight: bold; background-color: #2c3e50; color: white;"
        )

        # FIX: Hier wurde der Funktionsname korrigiert von download_and_apply... zu handle_online_patch_button
        self.btn_online.clicked.connect(self.handle_online_patch_button)

        layout_online.addWidget(QLabel(self.T_PATCH_SEL))
        layout_online.addWidget(self.combo_source)
        layout_online.addWidget(self.btn_online)
        self.group_online.setLayout(layout_online)
        parent_layout.addWidget(self.group_online)

        # Sound-Feedback
        safe_play_func = globals().get("safe_play")
        if safe_play_func:
            safe_play_func("dialog-information.oga")

    def handle_online_patch_button(self):
        """Online-Patch herunterladen mit Statusmeldungen und echtem Download-Fortschritt."""

        from PyQt6.QtWidgets import QInputDialog, QMessageBox, QApplication
        from PyQt6.QtCore import QTimer
        import requests
        import re
        import os

        # ---------------------------------------------------------
        # Hilfsfunktionen
        # ---------------------------------------------------------

        def _(key, default):
            return getattr(self, "get_t", lambda x, y: y)(key, default)

        def set_status(text, value=None, color=None):
            """Progressbar aktualisieren und GUI sofort neu zeichnen."""
            if not pbar:
                return

            if value is not None:
                pbar.setValue(max(0, min(100, int(value))))

            pbar.setFormat(text)

            if color:
                pbar.setStyleSheet(
                    f"""
                    QProgressBar {{
                        text-align: center;
                        font-weight: 900;
                        border: 2px solid {color};
                        border-radius: 6px;
                        background-color: #111;
                        color: {color};
                        font-size: 13pt;
                    }}
                    QProgressBar::chunk {{
                        background-color: {color};
                        border-radius: 4px;
                    }}
                    """
                )

            pbar.show()
            pbar.repaint()
            QApplication.processEvents()

        def restore_style():
            if pbar:
                pbar.setValue(0)
                pbar.setFormat("%p%")
                pbar.setStyleSheet(
                    """
                    QProgressBar {
                        border: 1px solid #444;
                        border-radius: 8px;
                        background-color: #1A1A1A;
                        color: black;
                        text-align: center;
                        font-weight: bold;
                    }

                    QProgressBar::chunk {
                        background-color: qlineargradient(
                            x1:0, y1:0, x2:1, y2:0,
                            stop:0 #F37804,
                            stop:1 #FFD700
                        );
                        border-radius: 8px;
                    }
                    """
                )

        def play_sound(filename):
            safe_play_func = globals().get("safe_play")
            if safe_play_func:
                try:
                    safe_play_func(filename)
                except Exception:
                    pass

        # ---------------------------------------------------------
        # Sprache
        # ---------------------------------------------------------

        lang = str(
            getattr(
                self,
                "lang",
                getattr(self, "LANG", "en")
            )
        ).lower()[:2]

        is_de = lang == "de"

        # ---------------------------------------------------------
        # Progressbar
        # ---------------------------------------------------------

        pbar = getattr(self, "progress_bar", None)

        rainbow = (
            "qlineargradient("
            "x1:0, y1:0, x2:1, y2:0, "
            "stop:0.0 #FF0000, "
            "stop:0.2 #FF7F00, "
            "stop:0.4 #FFFF00, "
            "stop:0.6 #00FF00, "
            "stop:0.8 #0000FF, "
            "stop:1.0 #8B00FF"
            ");"
        )

        if pbar:
            pbar.setStyleSheet(
                f"""
                QProgressBar {{
                    text-align: center;
                    font-weight: 900;
                    border: 2px solid #222;
                    border-radius: 6px;
                    background-color: #111;
                    color: black;
                    font-size: 13pt;
                }}

                QProgressBar::chunk {{
                    background-color: {rainbow};
                    border-radius: 4px;
                }}
                """
            )

            pbar.setValue(0)
            pbar.setFormat(
                "🔍 " +
                _("patch_dl_wait", "Prüfe verfügbare Patches...")
            )
            pbar.show()
            pbar.repaint()
            QApplication.processEvents()

        # ---------------------------------------------------------
        # Texte
        # ---------------------------------------------------------

        T_CHECKING = (
            "🔍 " +
            _("patch_dl_wait", "Prüfe verfügbare Patches...")
        )

        T_FOUND = (
            "📦 " +
            _("patch_dl_found", "Patches gefunden")
        )

        T_START = (
            "🌐 " +
            _("patch_dl_start", "Download wird gestartet...")
        )

        T_DOWNLOADING = (
            "⬇️ " +
            _("patch_dl_downloading", "Download")
        )

        T_SAVING = (
            "💾 " +
            _("patch_dl_saving", "Speichere Patch...")
        )

        T_DONE = (
            "✅ " +
            _("patch_dl_done", "Patch erfolgreich heruntergeladen!")
        )

        T_CANCELLED = (
            "❌ " +
            _("patch_dl_cancelled", "Download abgebrochen.")
        )

        T_ERROR = (
            "❌ " +
            _("patch_dl_error", "Fehler beim Download")
        )

        # ---------------------------------------------------------
        # Sound + Status
        # ---------------------------------------------------------

        play_sound("dialog-information.oga")
        set_status(T_CHECKING, 10)

        # ---------------------------------------------------------
        # Patch-Liste holen
        # ---------------------------------------------------------

        patch_list = globals().get(
            "ONLINE_PATCHES",
            getattr(self, "ONLINE_PATCHES", {})
        )

        if not patch_list:
            play_sound("dialog-error.oga")

            set_status(
                "❌ " +
                _("patch_dl_no_patches", "Keine Patches verfügbar."),
                0,
                "#FF3333"
            )

            QMessageBox.warning(
                self,
                _("patch_dl_title", "Patch Download"),
                _("patch_dl_no_patches", "Es wurden keine Online-Patches gefunden.")
            )

            QTimer.singleShot(3000, restore_style)
            return

        # ---------------------------------------------------------
        # Versionen prüfen
        # ---------------------------------------------------------

        display_items = {}

        total_patches = len(patch_list)

        for index, (name, url) in enumerate(patch_list.items(), start=1):

            progress = 10 + int((index / total_patches) * 25)

            set_status(
                f"🔍 Prüfe {name}... {progress}%",
                progress
            )

            try:
                res = requests.get(
                    url,
                    headers={"Range": "bytes=0-511"},
                    timeout=5
                )

                res.raise_for_status()

                match = re.search(
                    r"patch version:\s*([\d\.-]+)",
                    res.text,
                    re.IGNORECASE
                )

                version = match.group(1) if match else "unknown"

                display_items[
                    f"{name} [{version}]"
                ] = (
                    url,
                    version,
                    name
                )

            except requests.RequestException:
                display_items[
                    f"{name} [Offline]"
                ] = (
                    url,
                    "unknown",
                    name
                )

            except Exception:
                display_items[
                    f"{name} [Fehler]"
                ] = (
                    url,
                    "unknown",
                    name
                )

        # ---------------------------------------------------------
        # Keine Ergebnisse
        # ---------------------------------------------------------

        if not display_items:
            play_sound("dialog-error.oga")

            set_status(
                "❌ Keine Patches gefunden",
                0,
                "#FF3333"
            )

            QMessageBox.warning(
                self,
                _("patch_dl_title", "Patch Download"),
                _("patch_dl_none", "Es konnten keine Patches gefunden werden.")
            )

            QTimer.singleShot(3000, restore_style)
            return

        set_status(
            f"📦 {T_FOUND}: {len(display_items)}",
            40
        )

        # ---------------------------------------------------------
        # Patch auswählen
        # ---------------------------------------------------------

        dialog = QInputDialog(self)

        dialog.setWindowTitle(
            _("patch_dl_title", "Patch Download")
        )

        dialog.setLabelText(
            _("patch_dl_select", "Wähle einen Patch:")
        )

        dialog.setComboBoxItems(
            list(display_items.keys())
        )

        dialog.setOkButtonText("OK")

        dialog.setCancelButtonText(
            "Abbrechen" if is_de else "Cancel"
        )

        QApplication.processEvents()

        # ---------------------------------------------------------
        # Abbruch
        # ---------------------------------------------------------

        if not dialog.exec():

            play_sound("dialog-warning.oga")

            set_status(
                T_CANCELLED,
                100,
                "#FF3333"
            )

            QTimer.singleShot(
                500,
                lambda: (
                    set_status(T_CANCELLED, 0, "#FF3333")
                )
            )

            QTimer.singleShot(
                3000,
                restore_style
            )

            return

        # ---------------------------------------------------------
        # Auswahl auslesen
        # ---------------------------------------------------------

        item = dialog.textValue()

        if item not in display_items:
            play_sound("dialog-error.oga")

            set_status(
                "❌ Ungültige Patch-Auswahl",
                0,
                "#FF3333"
            )

            QTimer.singleShot(3000, restore_style)
            return

        url, version, original_name = display_items[item]

        # ---------------------------------------------------------
        # Download vorbereiten
        # ---------------------------------------------------------

        set_status(
            f"{T_START} {original_name}",
            45
        )

        QApplication.processEvents()

        # Dateiname sicher erzeugen
        clean_name = re.sub(
            r"[^a-zA-Z0-9_-]+",
            "_",
            original_name
        ).strip("_")

        if not clean_name:
            clean_name = "patch"

        if version == "unknown":
            file_name = f"{clean_name}.patch"
        else:
            file_name = f"{clean_name}_v{version}.patch"

        # ---------------------------------------------------------
        # Download
        # ---------------------------------------------------------

        try:

            with requests.get(
                url,
                timeout=30,
                stream=True
            ) as response:

                response.raise_for_status()

                total_size = int(
                    response.headers.get(
                        "content-length",
                        0
                    )
                )

                downloaded = 0

                # Datei binär schreiben.
                # Dadurch funktioniert es auch mit ungewöhnlichen
                # Patch-Dateien.
                with open(file_name, "wb") as file:

                    for chunk in response.iter_content(
                        chunk_size=64 * 1024
                    ):

                        if not chunk:
                            continue

                        file.write(chunk)

                        downloaded += len(chunk)

                        # -----------------------------------------
                        # Echter Prozentfortschritt
                        # -----------------------------------------

                        if total_size > 0:

                            percent = int(
                                downloaded * 100 / total_size
                            )

                            downloaded_mb = (
                                downloaded / 1024 / 1024
                            )

                            total_mb = (
                                total_size / 1024 / 1024
                            )

                            status = (
                                f"{T_DOWNLOADING}: "
                                f"{percent}%  "
                                f"({downloaded_mb:.1f} / "
                                f"{total_mb:.1f} MB)"
                            )

                            set_status(
                                status,
                                percent
                            )

                        else:

                            downloaded_mb = (
                                downloaded / 1024 / 1024
                            )

                            set_status(
                                f"{T_DOWNLOADING}: "
                                f"{downloaded_mb:.1f} MB",
                                50
                            )

            # -----------------------------------------------------
            # Speichern erfolgreich
            # -----------------------------------------------------

            set_status(
                T_SAVING,
                100
            )

            QApplication.processEvents()

            # Prüfen, ob Datei wirklich existiert
            if not os.path.isfile(file_name):
                raise IOError(
                    "Die Patch-Datei konnte nicht erstellt werden."
                )

            file_size = os.path.getsize(file_name)

            if file_size == 0:
                raise IOError(
                    "Der Server hat eine leere Patch-Datei geliefert."
                )

            # -----------------------------------------------------
            # Erfolg
            # -----------------------------------------------------

            play_sound("complete.oga")

            set_status(
                f"✅ {T_DONE}  "
                f"{os.path.basename(file_name)}",
                100,
                "#00FF66"
            )

            QMessageBox.information(
                self,
                _("patch_dl_success_title", "Patch Download"),
                (
                    _("patch_dl_success", "Patch erfolgreich gespeichert.")
                    + "\n\n"
                    + f"📄 {os.path.abspath(file_name)}"
                    + "\n"
                    + f"📦 {file_size / 1024 / 1024:.2f} MB"
                )
            )

            # Nach Meldung wieder normale Progressbar
            QTimer.singleShot(
                3000,
                restore_style
            )

        # ---------------------------------------------------------
        # HTTP / Netzwerkfehler
        # ---------------------------------------------------------

        except requests.exceptions.Timeout:

            play_sound("dialog-error.oga")

            error_text = (
                _("patch_dl_timeout", "Zeitüberschreitung beim Download.")
            )

            set_status(
                f"❌ {error_text}",
                0,
                "#FF3333"
            )

            QMessageBox.critical(
                self,
                _("patch_dl_error_title", "Download-Fehler"),
                error_text
            )

            QTimer.singleShot(
                3000,
                restore_style
            )

        except requests.exceptions.HTTPError as e:

            play_sound("dialog-error.oga")

            error_text = (
                f"{T_ERROR}: HTTP {e.response.status_code}"
                if e.response is not None
                else str(e)
            )

            set_status(
                f"❌ {error_text}",
                0,
                "#FF3333"
            )

            QMessageBox.critical(
                self,
                _("patch_dl_error_title", "Download-Fehler"),
                error_text
            )

            QTimer.singleShot(
                3000,
                restore_style
            )

        except requests.exceptions.RequestException as e:

            play_sound("dialog-error.oga")

            error_text = (
                _("patch_dl_network_error", "Netzwerkfehler:")
                + f"\n{e}"
            )

            set_status(
                "❌ Netzwerkfehler",
                0,
                "#FF3333"
            )

            QMessageBox.critical(
                self,
                _("patch_dl_error_title", "Download-Fehler"),
                error_text
            )

            QTimer.singleShot(
                3000,
                restore_style
            )

        # ---------------------------------------------------------
        # Sonstige Fehler
        # ---------------------------------------------------------

        except Exception as e:

            play_sound("dialog-error.oga")

            error_text = (
                _("patch_dl_unknown_error", "Unerwarteter Fehler:")
                + f"\n{e}"
            )

            set_status(
                "❌ Fehler beim Patch-Download",
                0,
                "#FF3333"
            )

            QMessageBox.critical(
                self,
                _("patch_dl_error_title", "Download-Fehler"),
                error_text
            )

            QTimer.singleShot(
                3000,
                restore_style
            )

    def run_patch_process(self, patch_data, is_dry_run=True):
        """Führt einen Patch-Test oder die echte Anwendung mit Diagnose aus."""

        # --- Final Label sofort ausblenden ---
        if hasattr(self, "hide_final_label"):
            self.hide_final_label()
        elif hasattr(self, "final_label") and self.final_label:
            self.final_label.hide()

        import subprocess
        import shutil

        is_de = str(getattr(self, "LANG", "de")).lower().startswith("de")
        action = "Patch-Test" if is_dry_run else "Patch-Anwendung"
        action_en = "Patch dry-run" if is_dry_run else "Patch application"
        cmd = ["patch", "-p1"]
        if is_dry_run:
            cmd.append("--dry-run")

        target_dir = os.path.abspath(
            os.path.expanduser(str(getattr(self, "local_oscam_path", os.getcwd())))
        )

        def report(level, de_text, en_text=None):
            self.log(de_text if is_de or en_text is None else en_text, level)

        if not shutil.which("patch"):
            report(
                "error",
                "❌ Das Programm 'patch' wurde nicht gefunden. Unter Debian/Ubuntu/WSL: sudo apt update && sudo apt install patch",
                "❌ The 'patch' program was not found. On Debian/Ubuntu/WSL: sudo apt update && sudo apt install patch",
            )
            return False

        if not os.path.isdir(target_dir):
            report(
                "error",
                f"❌ OSCam-Zielordner existiert nicht: {target_dir}",
                f"❌ OSCam target directory does not exist: {target_dir}",
            )
            return False

        if not isinstance(patch_data, str) or not patch_data.strip():
            report(
                "error",
                "❌ Patch-Datei ist leer oder konnte nicht gelesen werden.",
                "❌ Patch data is empty or could not be read.",
            )
            return False

        report(
            "info",
            f"🔎 {action} gestartet · Ziel: {target_dir}",
            f"🔎 {action_en} started · target: {target_dir}",
        )

        try:
            process = subprocess.Popen(
                cmd,
                cwd=target_dir,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            stdout, stderr = process.communicate(input=patch_data, timeout=120)
            output = "\n".join(part.strip() for part in (stdout, stderr) if part and part.strip())

            if process.returncode == 0:
                report(
                    "success",
                    f"✅ {action} erfolgreich abgeschlossen.",
                    f"✅ {action_en} completed successfully.",
                )
                if output:
                    self.log(output, "info")
                return True

            details = output or ("Keine Details vom patch-Befehl erhalten." if is_de else "The patch command returned no details.")
            details_lower = details.lower()
            if any(term in details_lower for term in ("reversed (or previously applied)", "previously applied", "reversed patch")):
                hint = ("Hinweis: Der Patch könnte bereits angewendet sein oder in umgekehrter Richtung vorliegen." if is_de else "Hint: The patch may already be applied or may be reversed.")
            elif any(term in details_lower for term in ("can't find file", "cannot find file", "no file to patch")):
                hint = ("Hinweis: Prüfe den Zielordner und den Patch-Level (-p1)." if is_de else "Hint: Check the target directory and patch level (-p1).")
            elif any(term in details_lower for term in ("hunk failed", "failed hunk", "malformed patch")):
                hint = ("Hinweis: Der Quellcode passt möglicherweise nicht zur Patch-Version; prüfe Revision und Konflikte." if is_de else "Hint: The source may not match this patch version; check the revision and conflicts.")
            else:
                hint = ("Hinweis: Prüfe Zielpfad, Quellcode-Revision und Patch-Datei." if is_de else "Hint: Check the target path, source revision, and patch file.")

            report(
                "error",
                f"❌ {action} fehlgeschlagen (Exit-Code {process.returncode}).\n{hint}\nZielordner: {target_dir}",
                f"❌ {action_en} failed (exit code {process.returncode}).\n{hint}\nTarget directory: {target_dir}",
            )
            if output:
                self.log(output, "error")
            return False

        except subprocess.TimeoutExpired:
            try:
                process.kill()
                stdout, stderr = process.communicate()
            except Exception:
                stdout, stderr = "", ""
            report(
                "error",
                f"❌ {action} nach 120 Sekunden abgebrochen. Zielordner: {target_dir}",
                f"❌ {action_en} timed out after 120 seconds. Target directory: {target_dir}",
            )
            output = "\n".join(part.strip() for part in (stdout, stderr) if part and part.strip())
            if output:
                self.log(output, "error")
            return False
        except FileNotFoundError as exc:
            report(
                "error",
                f"❌ Benötigter Befehl oder Zielpfad nicht gefunden: {exc}",
                f"❌ Required command or target path not found: {exc}",
            )
            return False
        except Exception as exc:
            report(
                "error",
                f"🚨 Fehler beim Ausführen des Patch-Befehls: {type(exc).__name__}: {exc}",
                f"🚨 Error while running patch command: {type(exc).__name__}: {exc}",
            )
            return False


    def get_language_flag(self):
        """Gibt die passende Flagge zur Systemsprache zurück."""
        try:
            import locale

            # Holen der Sprache (z.B. 'de_DE' oder 'en_US')
            loc = locale.getlocale()[0] or locale.getdefaultlocale()[0] or "en"
            lang = loc[:2].lower()
        except:
            lang = "en"

        # Mapping von Sprachcode zu Flaggen-Emoji
        flags = {
            "de": "🇩🇪",
            "en": "🇺🇸",
            "fr": "🇫🇷",
            "it": "🇮🇹",
            "es": "🇪🇸",
            "ru": "🇷🇺",
            "tr": "🇹🇷",
        }
        return flags.get(lang, "🌐")  # Weltkugel als Fallback

    def force_user_leds_static(self):
        """User-LEDs statisch setzen (NUR User-LEDs, Farbe passt sich dem Theme an!)"""
        global current_diff_colors

        # 1. Aktuelle Akzentfarbe (z.B. Gold oder Matrix-Grün) holen
        fg = current_diff_colors.get("fg", "#EAFF00")

        # 2. Status prüfen: Sollen die LEDs leuchten (AN) oder schlafen (AUS)?
        is_enabled = getattr(self, "current_config", {}).get("led_enabled", True)

        if not is_enabled:
            # Falls AUS: Akzentfarbe auf 20% Helligkeit dimmen (deine neue Funktion!)
            target_color = self.adjust_color(fg, 0.2)
            border_color = "#111"
        else:
            # Falls AN: Volle Akzentfarbe (aber statisch, kein Blinken)
            target_color = fg
            border_color = "#555"

        # 3. Timer sofort stoppen (Wichtig gegen Ghost-Blinking)
        if hasattr(self, "blink_timer"):
            self.blink_timer.stop()

        # 4. Stylesheet auf alle User-LEDs anwenden
        for led in getattr(self, "user_leds", []):
            try:
                led.setStyleSheet(
                    f"""
                    QLabel {{
                        background-color: {target_color};
                        border: 1px solid {border_color};
                        border-radius: 4px;
                    }}
                """
                )
            except:
                continue

        # Internen Status zurücksetzen
        self._blink_state = False

    def toggle_leds(self, enable: bool):
        """
        User-LEDs an/aus, Timer starten/stoppen, Config speichern.
        System-LEDs bleiben statisch.
        """
        # 1. Status synchronisieren
        self.current_config["led_enabled"] = enable
        self.led_enabled = enable  # Hilfsvariable für schnellen Zugriff

        # 2. Timer-Logik
        if hasattr(self, "blink_timer") and self.blink_timer:
            if enable:
                speed = (
                    self.slider_speed.value() if hasattr(self, "slider_speed") else 500
                )

                # Blinken nur in der Range 10ms bis 949ms
                if 10 <= speed < 950:
                    if not self.blink_timer.isActive():
                        self.blink_timer.start(speed)
                else:
                    # Falls Speed auf "Statisch" (>=950) steht
                    self.blink_timer.stop()
                    self.force_user_leds_static()
            else:
                # GLOBAL AUS
                self.blink_timer.stop()
                # WICHTIG: Sofort den statischen (dunklen) Zustand erzwingen
                if hasattr(self, "force_user_leds_static"):
                    self.force_user_leds_static()

        # 3. Permanent speichern (Config-Update)
        if "save_config" in globals():
            save_config({"led_enabled": enable}, gui_instance=self, silent=True)

    from PyQt6.QtWidgets import (
        QLabel,
        QGraphicsOpacityEffect,
        QGraphicsDropShadowEffect,
    )
    from PyQt6.QtCore import QPropertyAnimation, QRect, QEasingCurve, QTimer
    from PyQt6.QtGui import QFont, QColor

    def show_language_animation(self, lang_code, callback=None):
        from PyQt6.QtWidgets import (
            QLabel,
            QGraphicsOpacityEffect,
            QGraphicsDropShadowEffect,
        )
        from PyQt6.QtCore import (
            QPropertyAnimation,
            QRect,
            QEasingCurve,
            QTimer,
            QVariantAnimation,
        )
        from PyQt6.QtGui import QFont, QFontInfo, QColor
        from PyQt6.QtCore import QPropertyAnimation, QRect, QEasingCurve, QTimer
        from PyQt6.QtGui import QFont, QColor

        # 1. Cleanup
        if hasattr(self, "text_label") and self.text_label:
            self.text_label.deleteLater()
        if hasattr(self, "flag_label") and self.flag_label:
            self.flag_label.deleteLater()
        if hasattr(self, "final_label") and self.final_label:
            self.final_label.hide()

        is_de = lang_code.lower() == "de"
        text_msg = "Sprache wird umgestellt..." if is_de else "Switching language..."
        flag_char = "🇩🇪" if is_de else "🇺🇸"

        # 2. Text Label Setup (Neon Glow vorbereitet)
        self.text_label = QLabel(text_msg, self)
        self.text_label.setFont(QFont("Segoe UI", 32, QFont.Weight.Bold))
        self.text_label.setStyleSheet(
            "color: rgba(255, 111, 0, 0); background: transparent;"
        )
        self.text_label.adjustSize()
        t_x, t_y = (self.width() - self.text_label.width()) // 2, (
            self.height() - self.text_label.height()
        ) // 2 + 30
        self.text_label.move(t_x, t_y)

        self.shadow = QGraphicsDropShadowEffect(self.text_label)
        self.shadow.setColor(QColor("#FF6F00"))
        self.shadow.setOffset(0, 0)
        self.shadow.setBlurRadius(0)
        self.text_label.setGraphicsEffect(self.shadow)
        self.text_label.show()

        # 3. Flag Label Setup (Slide & Fade)
        from PyQt6.QtGui import QFont

        self.flag_label = QLabel(flag_char, self)

        font = QFont("Noto Color Emoji", 64)
        font.setFamily("NotoColorEmoji")
        font.setPointSize(64)

        self.flag_label.setFont(font)
        self.flag_label.adjustSize()

        self.flag_label.adjustSize()
        f_x, f_y = (
            self.width() - self.flag_label.width()
        ) // 2, t_y - self.flag_label.height() - 20

        flag_opacity = QGraphicsOpacityEffect(self.flag_label)
        flag_opacity.setOpacity(0)
        self.flag_label.setGraphicsEffect(flag_opacity)
        self.flag_label.show()

        # 4. Animationen: Neon-Puls & Fade-Out
        def fade_out():
            # Text und Flagge sanft ausblenden
            self.out_anim = QVariantAnimation(self)
            self.out_anim.setDuration(600)
            self.out_anim.setStartValue(QColor(255, 111, 0, 255))
            self.out_anim.setEndValue(QColor(255, 111, 0, 0))
            self.out_anim.valueChanged.connect(
                lambda c: self.text_label.setStyleSheet(
                    f"color: {c.name(QColor.NameFormat.HexArgb)}; background: transparent;"
                )
            )

            self.f_out = QPropertyAnimation(flag_opacity, b"opacity")
            self.f_out.setDuration(600)
            self.f_out.setStartValue(1)
            self.f_out.setEndValue(0)

            self.f_out.finished.connect(
                lambda: (
                    self.text_label.hide(),
                    self.flag_label.hide(),
                    callback() if callback else None,
                )
            )
            self.out_anim.start()
            self.f_out.start()

        def start_glow_pulse(times=6):
            state = {"i": 0}

            def toggle():
                val = 5 + (state["i"] % 2) * 25
                self.shadow.setBlurRadius(val)
                state["i"] += 1
                if state["i"] < times * 2:
                    QTimer.singleShot(180, toggle)
                else:
                    fade_out()

            toggle()

        # 5. Start: Text-Farbe Animation (Echter Fade-In)
        self.text_fade_in = QVariantAnimation(self)
        self.text_fade_in.setDuration(900)
        self.text_fade_in.setStartValue(QColor(255, 111, 0, 0))
        self.text_fade_in.setEndValue(QColor(255, 111, 0, 255))
        self.text_fade_in.valueChanged.connect(
            lambda c: self.text_label.setStyleSheet(
                f"color: {c.name(QColor.NameFormat.HexArgb)}; background: transparent;"
            )
        )

        # Flagge Fade & Slide
        self.flag_fade_in = QPropertyAnimation(flag_opacity, b"opacity")
        self.flag_fade_in.setDuration(900)
        self.flag_fade_in.setStartValue(0)
        self.flag_fade_in.setEndValue(1)

        self.flag_slide = QPropertyAnimation(self.flag_label, b"geometry")
        self.flag_slide.setDuration(900)
        self.flag_slide.setStartValue(
            QRect(f_x, f_y + 50, self.flag_label.width(), self.flag_label.height())
        )
        self.flag_slide.setEndValue(
            QRect(f_x, f_y, self.flag_label.width(), self.flag_label.height())
        )
        self.flag_slide.setEasingCurve(QEasingCurve.Type.OutBack)

        self.flag_fade_in.finished.connect(
            lambda: QTimer.singleShot(100, start_glow_pulse)
        )

        self.text_fade_in.start()
        self.flag_fade_in.start()
        self.flag_slide.start()

    def show_tool_help(self, tool_id):
        """Zeigt Status-Infos (Grün) oder Hilfe (Rot) für System-Tools an."""
        from PyQt6.QtWidgets import QMessageBox, QApplication
        import platform
        import shutil

        lang = getattr(self, "LANG", "de").lower()

        # 1. LIVE-CHECK: Ist das Tool installiert?
        path = shutil.which(tool_id)
        # Spezialfälle für PY und QT6, die nicht als binärer Pfad gesucht werden
        is_installed = path is not None or tool_id in ["py", "qt6"]

        # Mapping der Installations-Befehle
        commands = {
            "snd": "sudo apt update && sudo apt install pulseaudio-utils",
            "git": "sudo apt update && sudo apt install git",
            "patch": "sudo apt update && sudo apt install patch",
            "zip": "sudo apt update && sudo apt install zip",
            "gcc": "sudo apt update && sudo apt install build-essential",
            "make": "sudo apt update && sudo apt install make",
            "ssl": "sudo apt update && sudo apt install libssl-dev",
            "usb": "sudo apt update && sudo apt install libusb-1.0-0-dev",
        }

        cmd = commands.get(tool_id, "N/A")
        title = "Tool Status" if lang == "de" else "Tool Status"

        # 2. TEXT-ZUSAMMENSTELLUNG (WEICHE)
        if is_installed:
            # FALL: TOOL IST INSTALLIERT (GRÜN)
            tool_path = path if path else "System / Python"
            if lang == "de":
                msg = f"Das Tool <b style='color:#00FF00;'>{tool_id.upper()}</b> ist bereits installiert.<br><br>"
                msg += f"Pfad: <code style='background-color:#222; color:#00ADFF; padding:3px;'>{tool_path}</code>"
            else:
                msg = f"Tool <b style='color:#00FF00;'>{tool_id.upper()}</b> is already installed.<br><br>"
                msg += f"Path: <code style='background-color:#222; color:#00ADFF; padding:3px;'>{tool_path}</code>"
        else:
            # FALL: TOOL FEHLT (ROT)
            if lang == "de":
                msg = f"Das Tool <b style='color:#F37804;'>{tool_id.upper()}</b> wurde nicht im System gefunden.<br><br>"
                if platform.system() == "Linux" and cmd != "N/A":
                    msg += f"Installieren Sie es über das Terminal mit:<br><br><code style='background-color:#222; color:#00FF00; padding:5px;'>{cmd}</code>"
                else:
                    msg += "Bitte installieren Sie das erforderliche Paket manuell."
            else:
                msg = f"Tool <b style='color:#F37804;'>{tool_id.upper()}</b> not found in system.<br><br>"
                if platform.system() == "Linux" and cmd != "N/A":
                    msg += f"Install it via terminal using:<br><br><code style='background-color:#222; color:#00FF00; padding:5px;'>{cmd}</code>"
                else:
                    msg += "Please install the required package manually."

        # 3. MESSAGEBOX KONFIGURIEREN
        box = QMessageBox(self)
        box.setWindowTitle(title)
        # Icon-Weiche: Information bei Grün, Warnung bei Rot
        box.setIcon(
            QMessageBox.Icon.Information if is_installed else QMessageBox.Icon.Warning
        )
        box.setText(msg)

        # Buttons: Kopieren nur anzeigen, wenn das Tool fehlt
        copy_btn = None
        if not is_installed and cmd != "N/A":
            copy_btn = box.addButton(
                "Befehl kopieren" if lang == "de" else "Copy Command",
                QMessageBox.ButtonRole.ActionRole,
            )

        box.addButton(QMessageBox.StandardButton.Ok)
        box.exec()

        # 4. LOGIK FÜR DEN KOPIER-BUTTON
        if copy_btn and box.clickedButton() == copy_btn:
            QApplication.clipboard().setText(cmd)
            if hasattr(self, "append_info"):
                log_msg = f"📋 {tool_id.upper()} " + (
                    "Befehl kopiert" if lang == "de" else "Command copied"
                )
                self.append_info(getattr(self, "info_text", None), log_msg, "status")

    def update_label_only(self, value):
        """
        Aktualisiert Text und Farbe des Speed-Labels beim Programmstart.
        Optimiert für das dunkle UI-Design.
        """
        if not hasattr(self, "lbl_speed_val") or not self.lbl_speed_val:
            return

        # Farbcodes passend zum System-Check
        C_GREEN, C_RED, C_GRAY = "#2ecc71", "#FF0000", "#AAAAAA"

        if value >= 950:
            txt = "STATIC"
            style = f"color: {C_GREEN}; font-weight: bold; font-size: 18px; font-family: 'Segoe UI', sans-serif;"
        elif value <= 50:
            txt = "TURBO"
            style = f"color: {C_RED}; font-weight: bold; font-size: 18px; font-family: 'Segoe UI', sans-serif;"
        else:
            # Zeigt die Millisekunden an, falls kein Extremwert vorliegt
            txt = f"{value}ms"
            style = f"color: {C_GRAY}; font-weight: bold; font-size: 18px; font-family: 'Segoe UI', sans-serif;"

        self.lbl_speed_val.setText(txt)
        self.lbl_speed_val.setStyleSheet(style)

    def force_leds_static(self):
        """Kombinierte LED-Steuerung: Setzt Status-Farben und Design-Stile ohne Blinken."""
        # 1. Sicherheitscheck
        if not hasattr(self, "status_leds") or not self.status_leds:
            return

        # 2. Design-Modus ermitteln (Matrix oder Standard)
        is_matrix = False
        try:
            # Prüft erst den Button-Text, falls vorhanden, sonst die Config
            if hasattr(self, "btn_matrix") and self.btn_matrix:
                is_matrix = "EXIT" in self.btn_matrix.text()
            else:
                is_matrix = self.cfg.get("theme_mode") == "matrix"
        except:
            is_matrix = False

        # 3. LEDs aktualisieren
        for led in self.status_leds:
            try:
                led.show()  # Sicherstellen, dass sie sichtbar sind

                # Tool-Status ermitteln (Grün wenn vorhanden, sonst Rot)
                exists = getattr(led, "tool_exists", False)

                if is_matrix:
                    # Im Matrix-Modus: Hellgrün für OK, Dunkelgrün/Grau für Fehlend
                    color = "#00FF41" if exists else "#003311"
                    border = "1px solid #00FF41"
                else:
                    # Im Standard-Modus: Kräftiges Rot/Grün
                    color = "#27ae60" if exists else "#c0392b"
                    border = "2px solid #555"

                led.setStyleSheet(
                    f"""
                    background-color: {color}; 
                    border-radius: 6px; 
                    border: {border};
                """
                )
            except:
                continue

    def load_config_from_file(self):
        if os.path.exists(CONFIG_FILE):
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        return {}  # Fallback

    def update_blink_speed(self, value):
        """
        Nur User-LEDs blinken lassen, System-LEDs bleiben statisch.
        0=OFF, >=950=STATIC, sonst blinkend (nur wenn led_enabled in Config).
        """
        timer = getattr(self, "blink_timer", None)
        if not timer or getattr(self, "is_loading", False):
            return

        # 1. UI Feedback (Texte & Farben des Labels)
        if hasattr(self, "lbl_speed_val"):
            if value <= 0:
                self.lbl_speed_val.setText("OFF")
                self.lbl_speed_val.setStyleSheet(
                    "color: #777; font-weight: bold; font-size: 12px;"
                )
            elif value >= 950:
                self.lbl_speed_val.setText("STATIC")
                self.lbl_speed_val.setStyleSheet(
                    "color: #2ecc71; font-weight: bold; font-size: 12px;"
                )
            elif value <= 50:
                self.lbl_speed_val.setText("TURBO")
                self.lbl_speed_val.setStyleSheet(
                    "color: #FF0000; font-weight: bold; font-size: 12px;"
                )
            else:
                self.lbl_speed_val.setText(f"{value}ms")
                self.lbl_speed_val.setStyleSheet(
                    "color: #aaa; font-weight: bold; font-size: 12px;"
                )

        # 2. Logik: Soll überhaupt geblinkt werden?
        # Wir prüfen den globalen "An/Aus"-Schalter aus der Config
        is_led_globally_enabled = self.current_config.get("led_enabled", True)
        config_updates = {"blink_speed": value}

        # Bedingung zum STOPPEN:
        # Slider auf 0 ODER Slider auf Max ODER User hat Blinken global deaktiviert
        if value <= 0 or value >= 950 or not is_led_globally_enabled:
            timer.stop()
            if hasattr(self, "force_user_leds_static"):
                self.force_user_leds_static()

            # Nur wenn der Slider selbst auf 0/Max steht, ändern wir led_enabled in der Config
            if value <= 0 or value >= 950:
                config_updates["led_enabled"] = value > 0
        else:
            # Bedingung zum STARTEN/AKTUALISIEREN:
            timer.setInterval(max(10, value))
            if not timer.isActive():
                timer.start()
            config_updates["led_enabled"] = True

        # 3. Speichern & System-Status
        # Falls du die Checkbox in der UI hast, hier ggf. synchronisieren:
        # if hasattr(self, "led_checkbox"): self.led_checkbox.setChecked(config_updates.get("led_enabled", is_led_globally_enabled))

        save_config(config_updates, gui_instance=self, silent=True)

    def set_system_leds_static(self):
        """System-LEDs immer statisch lassen"""
        for led in getattr(self, "status_leds", []):
            led.setStyleSheet("background-color: green; border-radius: 4px;")

    
    def get_matrix_style(self):
        """Gibt das neon-grüne Matrix-Stylesheet zurück."""
        return """
            QWidget {
                background-color: #0D0D0D;
                color: #00FF41;
                font-family: 'Segoe UI', 'Ubuntu', sans-serif;
            }

            QGroupBox {
                border: 2px solid #008F11;
                border-radius: 8px;
                margin-top: 1ex;
                font-weight: bold;
                color: #00FF41;
            }

            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 4px;
            }

            QTextEdit {
                background-color: #000000;
                border: 1px solid #00FF41;
                color: #00FF41;
                font-family: 'Consolas', 'Ubuntu Mono', monospace;
                font-size: 20px;
            }

            QPushButton {
                background-color: #1A1A1A;
                color: #00FF41;
                border: 1px solid #008F11;
                border-radius: 5px;
                padding: 6px;
                font-weight: bold;
            }

            QPushButton:hover {
                background-color: #008F11;
                color: #000000;
                border: 1px solid #00FF41;
            }

            QPushButton:pressed {
                background-color: #005F0A;
                color: #FFFFFF;
            }

            QPushButton:disabled {
                background-color: #111111;
                color: #555555;
                border-color: #333333;
            }

            QLabel {
                color: #00FF41;
                background: transparent;
                border: none;
            }

            QLineEdit {
                background-color: #1A1A1A;
                border: 1px solid #008F11;
                color: #00FF41;
                padding: 3px;
                selection-background-color: #008F11;
                selection-color: #FFFFFF;
            }

            QProgressBar {
                background-color: #111111;
                border: 1px solid #008F11;
                border-radius: 5px;
                text-align: center;
                color: #FFFFFF;
            }

            QProgressBar::chunk {
                background-color: #008F11;
                border-radius: 3px;
            }

            QSlider::groove:horizontal {
                border: 1px solid #008F11;
                height: 6px;
                background: #000000;
                border-radius: 3px;
            }

            QSlider::handle:horizontal {
                background: #00FF41;
                border: 1px solid #008F11;
                width: 14px;
                height: 14px;
                margin: -5px 0;
                border-radius: 7px;
            }

            QCheckBox, QRadioButton {
                color: #00FF41;
                spacing: 6px;
            }

            QComboBox, QSpinBox, QDoubleSpinBox {
                background-color: #1A1A1A;
                color: #00FF41;
                border: 1px solid #008F11;
                padding: 3px;
            }

            QComboBox QAbstractItemView {
                background-color: #111111;
                color: #00FF41;
                selection-background-color: #008F11;
            }

            QToolTip {
                background-color: #000000;
                color: #00FF41;
                border: 1px solid #008F11;
                border-radius: 4px;
                padding: 5px;
                font-size: 10pt;
                font-weight: bold;
            }
        """


    def toggle_matrix(self, force_state=None):
        """
        Schaltet den Matrix-Modus ein oder aus.

        force_state:
            None       -> Modus anhand des Button-Texts umschalten
            "matrix"   -> Matrix-Modus einschalten
            "standard" -> Standarddesign einschalten
        """
        import traceback

        print(
            "DEBUG: toggle_matrix aufgerufen:",
            "force_state =", repr(force_state),
            flush=True
        )

        # 1. Final-Label ausblenden
        try:
            hide_label = getattr(self, "hide_final_label", None)

            if callable(hide_label):
                hide_label()
            else:
                final_label = getattr(self, "final_label", None)
                if final_label is not None:
                    final_label.hide()

        except Exception as exc:
            print(
                f"DEBUG: Final-Label konnte nicht ausgeblendet werden: {exc}",
                flush=True
            )

        # 2. Button prüfen
        btn = getattr(self, "btn_matrix", None)

        if btn is None:
            print(
                "FEHLER: self.btn_matrix ist nicht vorhanden!",
                flush=True
            )
            return

        # 3. Sprache und Übersetzungen
        lang_code = str(getattr(self, "LANG", "de")).lower()
        translations = globals().get("TEXTS", {})

        if not isinstance(translations, dict):
            translations = {}

        t = translations.get(
            lang_code,
            translations.get("en", {})
        )

        if not isinstance(t, dict):
            t = {}

        # 4. Konfiguration absichern
        cfg = getattr(self, "current_config", None)

        if not isinstance(cfg, dict):
            cfg = {}
            self.current_config = cfg

        # 5. Aktuellen Modus bestimmen
        current_mode = str(
            cfg.get("theme_mode", "standard")
        ).strip().lower()

        button_text = str(btn.text()).strip().lower()

        # Bei normalem Klick entscheidet der sichtbare Button-Text.
        # "EXIT MATRIX" bedeutet: Matrix ist aktiv -> Standard einschalten.
        # Andernfalls: Matrix einschalten.
        if force_state is None:
            if (
                "exit matrix" in button_text
                or "matrix verlassen" in button_text
                or "matrix beenden" in button_text
            ):
                target_mode = "standard"
            else:
                target_mode = "matrix"
        else:
            target_mode = str(force_state).strip().lower()

        if target_mode not in ("matrix", "standard"):
            print(
                f"FEHLER: Ungültiger Matrix-Modus: {target_mode!r}",
                flush=True
            )
            return

        print(
            "DEBUG: Zustand vor Umschaltung:",
            "config =", repr(current_mode),
            "| button =", repr(btn.text()),
            "| ziel =", repr(target_mode),
            flush=True
        )

        # 6. Standard-Styles nur einmal sichern
        # So wird der Matrix-Style nicht versehentlich als Standard gespeichert.
        if not hasattr(self, "_standard_stylesheet"):
            self._standard_stylesheet = self.styleSheet()

        header = getattr(self, "header_widget", None)

        if (
            header is not None
            and not hasattr(self, "_standard_header_stylesheet")
        ):
            self._standard_header_stylesheet = header.styleSheet()

        slider = getattr(
            self,
            "blink_slider",
            getattr(self, "slider_speed", None)
        )

        if (
            slider is not None
            and not hasattr(self, "_standard_slider_stylesheet")
        ):
            self._standard_slider_stylesheet = slider.styleSheet()

        is_loading = bool(getattr(self, "is_loading", False))

        # 7. Gewünschten Modus anwenden
        try:
            if target_mode == "matrix":

                # Matrix-Stylesheet holen
                matrix_style_func = getattr(
                    self,
                    "get_matrix_style",
                    None
                )

                if not callable(matrix_style_func):
                    raise AttributeError(
                        "get_matrix_style() fehlt oder ist keine "
                        "Methode von PatchManagerGUI."
                    )

                matrix_style = matrix_style_func()

                if not isinstance(matrix_style, str):
                    raise TypeError(
                        "get_matrix_style() muss einen String zurückgeben."
                    )

                self.setStyleSheet(matrix_style)

                # Button zeigt ab jetzt die Aktion zum Verlassen an
                btn.setText(
                    t.get("matrix_btn_exit", "🔙 EXIT MATRIX")
                )

                cfg["theme_mode"] = "matrix"
                self.current_config = cfg

                # Header anpassen
                if header is not None:
                    header.setStyleSheet("""
                        QWidget {
                            background-color: #000000;
                            color: #00FF41;
                            border: 1px solid #008F11;
                        }
                    """)

                print(
                    "DEBUG: Matrix-Modus AKTIVIERT",
                    "| config =", repr(cfg.get("theme_mode")),
                    "| button =", repr(btn.text()),
                    flush=True
                )

                # Aktivierungsnachricht
                info = getattr(self, "info_text", None)

                if info is not None and not is_loading:
                    sys_tag = t.get("matrix_sys", "System")
                    message = t.get(
                        "matrix_on",
                        "Matrix-Modus aktiviert."
                    )

                    info.append(
                        '<br><span style="color:#00FF41; '
                        'font-family:monospace;">'
                        f'<b>[{sys_tag}]</b> {message}'
                        '</span>'
                    )

                # Optionaler Sound
                safe_play_func = globals().get("safe_play")

                if callable(safe_play_func) and not is_loading:
                    try:
                        safe_play_func("dialog-information.oga")
                    except Exception as exc:
                        print(
                            f"DEBUG: Sound übersprungen: {exc}",
                            flush=True
                        )

            else:

                # Standard-Styles wiederherstellen
                self.setStyleSheet(
                    self._standard_stylesheet
                )

                # Button zeigt wieder die Aktivierungsaktion an
                btn.setText(
                    t.get("matrix_btn_enter", "📟 MATRIX MODE")
                )

                cfg["theme_mode"] = "standard"
                self.current_config = cfg

                # Header wiederherstellen
                if header is not None:
                    header.setStyleSheet(
                        self._standard_header_stylesheet
                    )

                # Slider wiederherstellen
                if slider is not None:
                    slider.setStyleSheet(
                        self._standard_slider_stylesheet
                    )

                print(
                    "DEBUG: Standard-Modus AKTIVIERT",
                    "| config =", repr(cfg.get("theme_mode")),
                    "| button =", repr(btn.text()),
                    flush=True
                )

                # Deaktivierungsnachricht
                info = getattr(self, "info_text", None)

                if info is not None and not is_loading:
                    sys_tag = t.get("matrix_sys", "System")
                    message = t.get(
                        "matrix_off",
                        "Matrix-Modus deaktiviert."
                    )

                    info.append(
                        '<br><span style="color:orange; '
                        'font-family:monospace;">'
                        f'<b>[{sys_tag}]</b> {message}'
                        '</span>'
                    )

        except Exception as exc:
            print(
                f"FEHLER beim Umschalten des Matrix-Modus: {exc}",
                flush=True
            )
            traceback.print_exc()
            return

        # 8. Uhr aktualisieren
        analog_clock = getattr(self, "analog_clock", None)

        if analog_clock is not None:
            try:
                analog_clock.update()
            except Exception as exc:
                print(
                    f"DEBUG: Uhr-Aktualisierung fehlgeschlagen: {exc}",
                    flush=True
                )

        # 9. LEDs synchronisieren
        force_leds = getattr(
            self,
            "force_leds_static",
            None
        )

        if callable(force_leds):
            try:
                force_leds()
            except Exception as exc:
                print(
                    f"DEBUG: LED-Aktualisierung fehlgeschlagen: {exc}",
                    flush=True
                )

        # 10. Button-Höhen aktualisieren
        for button in getattr(self, "all_buttons", []):
            try:
                button.setFixedHeight(
                    getattr(self, "BUTTON_HEIGHT", 50)
                )
            except (AttributeError, RuntimeError):
                continue

        # 11. Konfiguration speichern
        if not is_loading:
            save_config_func = globals().get("save_config")

            if callable(save_config_func):
                try:
                    save_config_func(
                        cfg,
                        gui_instance=self,
                        silent=True
                    )
                except Exception as exc:
                    print(
                        "WARNUNG: Konfiguration konnte nicht gespeichert "
                        f"werden: {exc}",
                        flush=True
                    )

        # 12. Endzustand protokollieren
        print(
            "DEBUG: toggle_matrix abgeschlossen:",
            "theme_mode =", repr(cfg.get("theme_mode")),
            "| button_text =", repr(btn.text()),
            flush=True
        )



    def animate_everything(self):
        """Steuert das Blinken von Text und LEDs."""
        self._blink_state = not self._blink_state
        # 2. LEDs blinken lassen (Matrix-Check inklusive)
        is_matrix = "EXIT" in self.btn_matrix.text()

        for led in self.status_leds:
            if self._blink_state:
                # AN-Phase
                color = led.base_color if not is_matrix else "#00FF41"
                border = "#FFF" if not is_matrix else "#00FF41"
            else:
                # AUS-Phase (Abgedunkelt)
                color = "#1a532d" if led.tool_exists else "#5a1a14"
                border = "#333"

            led.setStyleSheet(
                f"background-color: {color}; border-radius: 7px; border: 2px solid {border};"
            )

    def get_status_indicator(self, tool_name):
        """Erzeugt ein Widget mit LED-Punkt und Label für den Systemcheck."""
        container = QWidget()
        layout = QHBoxLayout(container)
        layout.setContentsMargins(5, 0, 5, 0)
        layout.setSpacing(6)

        # Die LED (ein Kreis)
        led = QLabel()
        led.setFixedSize(10, 10)

        # Prüfung ob Tool (git, patch, etc.) im Systempfad ist
        exists = shutil.which(tool_name) is not None
        color = "#27ae60" if exists else "#c0392b"  # Grün wenn da, Rot wenn fehlt

        led.setStyleSheet(
            f"""
            background-color: {color}; 
            border-radius: 5px; 
            border: 1px solid rgba(255,255,255,0.3);
        """
        )

        # Das Text-Label daneben
        label = QLabel(tool_name.lower())
        label.setFont(QFont("Monospace", 9))

        layout.addWidget(led)
        layout.addWidget(label)
        return container

    def toggle_theme(self):
        """
        Schaltet zwischen Light, Dark und Matrix Mode um, aktualisiert die UI
        und speichert die Auswahl dauerhaft in der Config-Datei.
        """

        # --- Final Label ausblenden, falls sichtbar ---
        if hasattr(self, "hide_final_label"):
            self.hide_final_label()
        elif hasattr(self, "final_label") and self.final_label:
            self.final_label.hide()
        import json

        # Aktuellen Modus aus den globalen Variablen holen (geladen durch load_config)
        current_mode = globals().get("THEME_MODE", "standard")
        lang = getattr(self, "LANG", "de").lower()

        # 1. Logik für den Wechsel (Rotation: Standard -> Dark -> Matrix -> zurück)
        if current_mode == "standard":
            new_mode = "dark"
        elif current_mode == "dark":
            new_mode = "matrix"
        else:
            new_mode = "standard"

        # 2. Globale Variable sofort aktualisieren
        globals()["THEME_MODE"] = new_mode

        # 3. THEMEN ANWENDEN
        if new_mode == "standard":
            # --- LIGHT MODE ---
            self.setStyleSheet("background-color: #F5F5F5; color: #222;")
            self.info_text.setStyleSheet(
                "background-color: #FFFFFF; color: #222; border: 1px solid #CCC; border-radius: 5px;"
            )
            if hasattr(self, "header_widget"):
                self.header_widget.setStyleSheet(
                    "background-color: #E0E0E0; border: 1px solid #BBB; border-radius: 10px;"
                )
            self.btn_theme.setStyleSheet(
                "background-color: #DDD; color: #222; border: 1px solid #BBB; border-radius: 4px; font-size: 10px;"
            )
            self.btn_theme.setText("☀️ Light Mode")

            pbar = getattr(self, "progress_bar", None)
            if pbar:
                pbar.setStyleSheet(
                    "QProgressBar { text-align: center; border: 1px solid #BBB; border-radius: 5px; background-color: #EEE; color: #222; }"
                )
                pbar.setFormat("Helles Design" if lang == "de" else "Light Mode")

        elif new_mode == "dark":
            # --- DARK MODE ---
            self.setStyleSheet("background-color: #2F2F2F; color: #EEE;")
            self.info_text.setStyleSheet(
                "background-color: #000000; color: #FFFFFF; border: 1px solid #444; border-radius: 5px;"
            )
            if hasattr(self, "header_widget"):
                self.header_widget.setStyleSheet(
                    "background-color: #2F2F2F; border: 1px solid #444; border-radius: 10px;"
                )
            self.btn_theme.setStyleSheet(
                "background-color: #3D3D3D; color: #EEE; border: 1px solid #555; border-radius: 4px; font-size: 10px;"
            )
            self.btn_theme.setText("🌓 Dark Mode")

            pbar = getattr(self, "progress_bar", None)
            if pbar:
                rainbow = (
                    "qlineargradient(x1:0, y1:0, x2:1, y2:0, "
                    "stop:0.0 #FF0000, stop:0.2 #FF7F00, stop:0.4 #FFFF00, "
                    "stop:0.6 #00FF00, stop:0.8 #0000FF, stop:1.0 #8B00FF)"
                )
                pbar.setStyleSheet(
                    f"QProgressBar {{ text-align: center; border: 2px solid #222; border-radius: 6px; background-color: #111; color: black; font-weight: bold; }}"
                    f"QProgressBar::chunk {{ background: {rainbow}; border-radius: 4px; }}"
                )
                pbar.setFormat("Dunkles Design" if lang == "de" else "Dark Mode")

        elif new_mode == "matrix":
            # --- MATRIX MODE ---
            # Klassisches Hacker-Grün auf Schwarz
            self.setStyleSheet("background-color: #000000; color: #00FF00;")
            self.info_text.setStyleSheet(
                "background-color: #000000; color: #00FF00; border: 1px solid #00FF00; border-radius: 5px; font-family: 'Courier New';"
            )
            if hasattr(self, "header_widget"):
                self.header_widget.setStyleSheet(
                    "background-color: #000000; border: 1px solid #00FF00; border-radius: 10px;"
                )
            self.btn_theme.setStyleSheet(
                "background-color: #000000; color: #00FF00; border: 1px solid #00FF00; border-radius: 4px; font-size: 10px;"
            )
            self.btn_theme.setText("📟 Matrix Mode")

            pbar = getattr(self, "progress_bar", None)
            if pbar:
                pbar.setStyleSheet(
                    "QProgressBar { text-align: center; border: 1px solid #00FF00; border-radius: 5px; background-color: #000; color: #00FF00; }"
                    "QProgressBar::chunk { background-color: #008800; }"
                )
                pbar.setFormat("SYSTEM MATRIX..." if lang == "de" else "MATRIX ACTIVE")

        # 4. SPEICHERN IN DATEI (Persistenz)
        try:
            # Nutzt die CONFIG_FILE Konstante aus dem globalen Scope
            config_path = globals().get("CONFIG_FILE", "config.json")
            if os.path.exists(config_path):
                with open(config_path, "r", encoding="utf-8") as f:
                    cfg = json.load(f)

                cfg["theme_mode"] = new_mode

                with open(config_path, "w", encoding="utf-8") as f:
                    json.dump(cfg, f, indent=4, ensure_ascii=False)
        except Exception as e:
            print(f"⚠️ Fehler beim Speichern des Themes: {e}")

    import os
    import re
    import requests
    from datetime import datetime

    # --- Hilfsfunktionen innerhalb der Klasse ---

    def get_local_revision(self):
        """
        Liefert die aktuelle OSCam-Revision.

        Priorität:

        1. Aktueller Streamboard-Git-Stand aus TEMP_REPO
        2. oscam_rev.txt neben dem Patch Manager
        3. "?" wenn keine Revision ermittelt werden kann

        WICHTIG:

            TEMP_REPO
                = OSCam / Streamboard

            PATCH_EMU_GIT_DIR
                = OSCam-Emu

        Deshalb darf für die aktuelle OSCam-Revision
        NICHT PATCH_EMU_GIT_DIR verwendet werden.
        """

        import os
        import re

        # ============================================================
        # 1. AKTUELLEN STREAMBOARD-GIT-STAND VERWENDEN
        # ============================================================

        try:

            repo_dir = TEMP_REPO

            git_dir = os.path.join(
                repo_dir,
                ".git"
            )

            if os.path.isdir(git_dir):

                # ----------------------------------------------------
                # HEAD des Streamboard-Repositories
                # ----------------------------------------------------

                commit = git_commit_id(
                    repo_dir,
                    "HEAD"
                )

                # ----------------------------------------------------
                # OSCam-Version aus globals.h
                # ----------------------------------------------------

                version = get_oscam_version(
                    repo_dir,
                    commit
                )

                if version:

                    version_text = str(
                        version
                    ).strip()

                    # ------------------------------------------------
                    # Beispiel:
                    #
                    # 2.26.09-11968
                    #
                    # Ergebnis:
                    #
                    # 11968
                    # ------------------------------------------------

                    match = re.search(
                        r"-(\d+)$",
                        version_text
                    )

                    if match:

                        revision = match.group(1)

                        if revision.isdigit():

                            # ----------------------------------------
                            # Aktuelle Revision auch lokal speichern
                            # ----------------------------------------

                            try:
                                self.save_local_revision(
                                    revision
                                )
                            except Exception:
                                pass

                            return revision

        except Exception:
            pass

        # ============================================================
        # 2. oscam_rev.txt ALS FALLBACK
        # ============================================================

        try:

            script_dir = os.path.dirname(
                os.path.realpath(__file__)
            )

            rev_file = os.path.join(
                script_dir,
                "oscam_rev.txt"
            )

            if os.path.isfile(rev_file):

                with open(
                    rev_file,
                    "r",
                    encoding="utf-8"
                ) as f:

                    content = f.read().strip()

                # ----------------------------------------------------
                # Nur reine Zahlen akzeptieren
                # ----------------------------------------------------

                if content.isdigit():

                    return content

        except Exception:
            pass

        # ============================================================
        # 3. KEIN WERT VERFÜGBAR
        # ============================================================

        return "?"

    def get_latest_remote_revision(self):
        """
        Ermittelt die aktuellste OSCam-Revision aus dem
        bereits geladenen Streamboard-Git.

        Bevorzugt wird das lokale TEMP_REPO verwendet.

        Falls TEMP_REPO nicht verfügbar ist, wird versucht,
        das Streamboard-Git direkt zu aktualisieren.
        """

        import os
        import re

        # ============================================================
        # 1. TEMP_REPO verwenden
        # ============================================================

        try:

            repo_dir = TEMP_REPO

            git_dir = os.path.join(
                repo_dir,
                ".git"
            )

            if os.path.isdir(git_dir):

                # ----------------------------------------------------
                # Remote aktualisieren
                # ----------------------------------------------------

                try:

                    git_command(
                        [
                            "fetch",
                            "--no-tags",
                            "--prune",
                            "origin"
                        ],
                        cwd=repo_dir,
                        timeout=1200
                    )

                except Exception:
                    pass

                # ----------------------------------------------------
                # Remote HEAD bestimmen
                # ----------------------------------------------------

                ref = "origin/master"

                try:

                    result = git_command(
                        [
                            "symbolic-ref",
                            "--short",
                            "refs/remotes/origin/HEAD"
                        ],
                        cwd=repo_dir,
                        allowed_returncodes=(0, 1)
                    )

                    remote_head = (
                        result.stdout
                        or ""
                    ).strip()

                    if remote_head:

                        ref = remote_head

                except Exception:
                    pass

                # ----------------------------------------------------
                # Commit bestimmen
                # ----------------------------------------------------

                try:

                    commit = git_commit_id(
                        repo_dir,
                        ref
                    )

                except Exception:

                    commit = git_commit_id(
                        repo_dir,
                        "HEAD"
                    )

                # ----------------------------------------------------
                # Version auslesen
                # ----------------------------------------------------

                version = get_oscam_version(
                    repo_dir,
                    commit
                )

                if version:

                    match = re.search(
                        r"-(\d+)$",
                        str(version).strip()
                    )

                    if match:

                        revision = match.group(1)

                        if revision.isdigit():

                            return revision

        except Exception:
            pass

        # ============================================================
        # 2. FALLBACK: HTTP
        # ============================================================

        try:

            import requests

            url = (
                "https://git.streamboard.tv/"
            )

            headers = {
                "User-Agent":
                    "OSCam-Patch-Manager"
            }

            response = requests.get(
                url,
                headers=headers,
                timeout=15
            )

            if response.status_code == 200:

                matches = re.findall(
                    r"r(\d{5,7})",
                    response.text
                )

                if matches:

                    # ------------------------------------------------
                    # Nicht einfach den ersten Treffer blind verwenden.
                    # Höchste gefundene Revision verwenden.
                    # ------------------------------------------------

                    revisions = [
                        int(value)
                        for value in matches
                        if value.isdigit()
                    ]

                    if revisions:

                        return str(
                            max(revisions)
                        )

        except Exception:
            pass

        return None

    def save_local_revision(self, revision):
        """
        Speichert die aktuelle OSCam-Revision in:

            <Plugin-Verzeichnis>/oscam_rev.txt

        Nicht os.getcwd() verwenden, da das Arbeitsverzeichnis
        beim Start der Anwendung ein anderes Verzeichnis sein kann.
        """

        import os

        try:

            # ========================================================
            # Revision validieren
            # ========================================================

            revision = str(
                revision
            ).strip()

            if not revision.isdigit():

                return False

            # ========================================================
            # Immer Plugin-Verzeichnis verwenden
            # ========================================================

            script_dir = os.path.dirname(
                os.path.realpath(__file__)
            )

            rev_file = os.path.join(
                script_dir,
                "oscam_rev.txt"
            )

            # ========================================================
            # Speichern
            # ========================================================

            with open(
                rev_file,
                "w",
                encoding="utf-8",
                newline="\n"
            ) as f:

                f.write(
                    revision
                )

            return True

        except Exception as e:

            print(
                f"Fehler beim Speichern der Revision: {e}"
            )

    def start_oscam_update_check(self):
        """Wird aufgerufen, um den Vergleich zu starten."""
        # 1. Lokale Rev holen (aus Datei oder Fallback)
        self.current_rev = self.get_local_revision()

        # 2. Remote Rev von Streamboard holen
        remote_rev = self.get_latest_remote_revision()

        if remote_rev:
            try:
                # Prüfen ob die Online-Nummer größer als die lokale ist
                update_available = int(remote_rev) > int(self.current_rev)
                self.on_update_check_finished(update_available, remote_rev)
            except ValueError:
                self.on_update_check_finished(False, self.current_rev)
        else:
            # Fehlerfall: Nur lokalen Stand zeigen
            self.on_update_check_finished(False, self.current_rev)

    def on_update_check_finished(self, update_available=False, new_rev=None):
        """
        Aktualisiert das UI:
        - Status Label (Update / aktuell)
        - Neongrün-Blinken der Revision (3 Mal)
        - ProgressBar bleibt unverändert
        - Am Ende: Label "Was bauen wir heute?" in Kasten
        """

        from datetime import datetime
        from PyQt6.QtCore import QTimer, Qt
        from PyQt6.QtWidgets import QLabel

        # Hide final label if exists
        if hasattr(self, "hide_final_label"):
            self.hide_final_label()
        elif hasattr(self, "final_label") and self.final_label:
            self.final_label.hide()

        txt = getattr(self, "TEXT", {})
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.last_timestamp = timestamp  # Für Sprachwechsel speichern!

        # 1. Die alte Revision für die Anzeige zwischenspeichern
        old_rev = getattr(self, "current_rev", "11943")

        # --- Revisions-Sound nur beim roten Aufblinken ---
        sound_manager = getattr(self, "sound_manager", None)

        def blink_revision(color):
            set_status_ui(color)

            if color == C_BLINK and sound_manager and sound_manager.enabled:
                
                sound_manager.play_revision()

        # 2. Die neue Revision für die Zukunft speichern
        if update_available and new_rev:
            self.current_rev = new_rev

        self.last_remote_rev = new_rev

        # --- Zentrale Farben & Schriftarten ---
        C_TIME = "#FF0000"
        F_TIME = "Arial, Segoe UI, sans-serif"
        F_TIME_SIZE = "24px"
        F_TIME_WEIGHT = "bold"

        C_REV = "#00FF00"
        F_REV = "Arial, Segoe UI, sans-serif"
        F_REV_SIZE = "24px"
        F_REV_WEIGHT = "bold"

        C_BLINK = "#F70606"
        C_FINAL_COLOR = "cyan"
        BG_FINAL_LABEL = "black"
        F_FINAL_LABEL = "Arial, Segoe UI, sans-serif"
        F_FINAL_LABEL_SIZE = "24px"
        F_FINAL_LABEL_WEIGHT = "bold"
        FINAL_LABEL_BORDER_RADIUS = 6

        # Nachrichten
        MSG_UPTODATE_COLOR = "#00FF00"
        MSG_UPTODATE_FONT = "Arial, Segoe UI, sans-serif"
        MSG_UPTODATE_SIZE = "24px"
        MSG_UPTODATE_WEIGHT = "bold"

        MSG_UPDATE_COLOR = "#FFAA00"
        MSG_UPDATE_FONT = "Arial, Segoe UI, sans-serif"
        MSG_UPDATE_SIZE = "24px"
        MSG_UPDATE_WEIGHT = "bold"

        # --- System Sound Helper ---
        def play_system_sound(sound_type="info"):
            safe_play = globals().get("safe_play")
            if safe_play:
                sounds = {
                    "update": "dialog-information.oga",
                    "uptodate": "complete.oga",
                    "beep": "bell.oga",
                }
                safe_play(sounds.get(sound_type, "complete.oga"))

        # --- Status Label ---
        status_label = getattr(self, "status_label", None)
        msg = txt.get("oscam_uptodate", "OSCam ist aktuell.")

        def set_status_ui(rev_color=C_REV, opacity=1.0):
            """Aktualisiert Status Label mit Farben, Fonts und optional Blink"""
            if status_label:
                html = (
                    f"✅ <span style='font-size:{F_TIME_SIZE}; font-family:{F_TIME}; color:{C_TIME}; font-weight:{F_TIME_WEIGHT};'>[{timestamp}]</span> "
                    f"<span style='font-size:{MSG_UPTODATE_SIZE}; font-family:{MSG_UPTODATE_FONT}; font-weight:{MSG_UPTODATE_WEIGHT}; color:{MSG_UPTODATE_COLOR};'>{msg}</span> "
                    f"<span style='color:{rev_color}; opacity:{opacity}; font-size:{F_REV_SIZE}; font-family:{F_REV}; font-weight:{F_REV_WEIGHT};'>(r{old_rev})</span>"
                )
                status_label.setText(html)
                status_label.show()

        if update_available:
            title = txt.get("oscam_update_found", "UPDATE VERFÜGBAR")
            
            # Update-Meldung zusätzlich im großen Infoscreen anzeigen
            self.append_info(
                self.info_text,
                f"🚀 [{timestamp}] {title} "
                f"(r{old_rev} → r{new_rev})",
                "warning",
            )

            if status_label:
                html = (
                    f"🚀 <span style='font-size:{F_TIME_SIZE}; font-family:{F_TIME}; color:{C_TIME}; font-weight:{F_TIME_WEIGHT};'>[{timestamp}]</span> "
                    f"<span style='font-size:{MSG_UPDATE_SIZE}; font-family:{MSG_UPDATE_FONT}; font-weight:{MSG_UPDATE_WEIGHT}; color:{MSG_UPDATE_COLOR};'><b>{title}</b></span> "
                    f"<span style='color:{C_REV}; font-size:{F_REV_SIZE}; font-family:{F_REV}; font-weight:{F_REV_WEIGHT};'>(r{old_rev} ➔ r{new_rev})</span>"
                )
                status_label.setText(html)
            play_system_sound("update")

            # --- Blink-Sequenz 3x ---
            delays = [300, 600, 900, 1200, 1500, 1800]
            blink_colors = [
                "transparent",
                C_BLINK,
                "transparent",
                C_BLINK,
                "transparent",
                C_BLINK,
            ]
            for i, delay in enumerate(delays):
                QTimer.singleShot(
                    delay,
                    lambda c=blink_colors[i]: blink_revision(c)
                )

        
        else:
            # --- Up-to-date ---
            set_status_ui()

            # Status zusätzlich im großen Infoscreen anzeigen
            self.append_info(
                self.info_text,
                f"✅ [{timestamp}] {msg} (r{old_rev})",
                "success",
            )

            play_system_sound("uptodate" if new_rev else "error")

            # --- Blink-Sequenz für Revision ---
            delays = [300, 600, 900, 1200, 1500, 1800]
            blink_colors = [
                "transparent",
                C_BLINK,
                "transparent",
                C_BLINK,
                "transparent",
                C_BLINK,
            ]
            for i, delay in enumerate(delays):
                QTimer.singleShot(
                    delay,
                    lambda c=blink_colors[i]: blink_revision(c)
                )


    def trigger_alert_animation(self, widget):
        """Lässt ein Widget dezent rot pulsieren."""
        effect = QGraphicsColorizeEffect(widget)
        widget.setGraphicsEffect(effect)

        self.pulse_anim = QPropertyAnimation(effect, b"color")
        self.pulse_anim.setDuration(1200)
        self.pulse_anim.setStartValue(QColor(Qt.GlobalColor.transparent))
        self.pulse_anim.setEndValue(QColor(255, 50, 50))  # Softes Rot
        self.pulse_anim.setEasingCurve(QEasingCurve.Type.InOutQuad)
        self.pulse_anim.setLoopCount(-1)  # Unendlich
        self.pulse_anim.start()

    def upload_progress_with_speed(self, current, total):
        """Aktualisiert Progressbar und zeigt Live-Speed im Log an."""
        import time

        # Initialisierung beim ersten Aufruf
        if not hasattr(self, "_upload_start_time") or current <= 0:
            self._upload_start_time = time.time()
            self._last_printed_second = 0

        # Progressbar füllen
        if total > 0:
            percent = int((current / total) * 100)
            self.progress_bar.setValue(percent)

        # Geschwindigkeit berechnen (alle 0.5 Sekunden aktualisieren)
        elapsed = time.time() - self._upload_start_time
        if elapsed > 0.5:
            speed_kb = (current / 1024) / elapsed
            speed_text = (
                f"🚀 Upload: {speed_kb:.2f} KB/s ({current/1024:.1f} KB übertragen)"
            )

            # Schreibt die Info in das Log-Fenster (überschreibt die letzte Zeile nicht,
            # aber wir können eine Status-Zeile im GUI-Label nutzen oder im Log-Anhängen)
            if int(elapsed) > self._last_printed_second:
                self.append_info(self.info_text, speed_text, "info")
                self._last_printed_second = int(elapsed)

    def change_colors(self):
        """
        Aktualisiert das komplette Farbschema der GUI.

        Funktionen:
        - Liest die aktuell gewählte Farbe sicher aus der Config/ComboBox.
        - Aktualisiert current_diff_colors zentral.
        - Repaintet die normalen Buttons.
        - Schützt S3/S4/NCam-Installationsbuttons vor Theme-Überschreibung.
        - Aktualisiert Checkbox, Header, Badges und Status.
        - Setzt den LED-Zustand nach dem Repaint erneut.
        - Synchronisiert theme_mode mit dem gewählten Theme.
        - Speichert die Änderung zentral.
        """

        from PyQt6.QtWidgets import QPushButton

        global current_diff_colors, current_color_name

        # ============================================================
        # 0. FINAL LABEL AUSBLENDEN
        # ============================================================
        try:
            if hasattr(self, "hide_final_label"):
                self.hide_final_label()
            elif getattr(self, "final_label", None):
                self.final_label.hide()
        except RuntimeError:
            pass

        # ============================================================
        # 1. CONFIG SICHER ERMITTELN
        # ============================================================
        config_obj = getattr(
            self,
            "cfg",
            getattr(self, "current_config", {})
        )

        if not isinstance(config_obj, dict):
            config_obj = {}

        saved_color = config_obj.get("color", "Classics")

        # ============================================================
        # 2. AKTUELLE FARBE ERMITTELN
        # ============================================================
        color_from_combo = ""

        try:
            if hasattr(self, "color_box") and self.color_box:
                color_from_combo = self.color_box.currentText().strip()
        except RuntimeError:
            color_from_combo = ""

        # Während des Startvorgangs niemals eine leere/noch nicht
        # initialisierte ComboBox verwenden.
        if getattr(self, "is_loading", False):
            current_color_name = saved_color
        elif color_from_combo:
            current_color_name = color_from_combo
        else:
            current_color_name = saved_color

        # ============================================================
        # 3. THEME SUCHEN
        # ============================================================
        base_colors = DIFF_COLORS.get(current_color_name)

        # Fallback, falls Theme-Key nicht existiert
        if not base_colors:
            base_colors = DIFF_COLORS.get(
                saved_color,
                DIFF_COLORS.get(
                    "Classics",
                    {
                        "bg": "#2F2F2F",
                        "fg": "#FFFFFF",
                        "hover": "#444444",
                        "active": "#222222",
                    },
                ),
            )

        # ============================================================
        # 4. FARBEN ZENTRAL AUFBAUEN
        # ============================================================
        bg = base_colors.get("bg", "#2F2F2F")
        fg = base_colors.get(
            "fg",
            base_colors.get("text", "#FFFFFF")
        )

        hover = base_colors.get("hover")

        if not hover:
            if hasattr(self, "adjust_color"):
                hover = self.adjust_color(bg, 1.20)
            else:
                hover = bg

        active = base_colors.get("active")

        if not active:
            if hasattr(self, "adjust_color"):
                active = self.adjust_color(bg, 0.80)
            else:
                active = bg

        current_diff_colors = {
            **base_colors,
            "bg": bg,
            "fg": fg,
            "text": base_colors.get("text", fg),
            "hover": hover,
            "active": active,
        }

        # ============================================================
        # 5. SOUND
        # ============================================================
        try:
            safe_play_func = globals().get("safe_play")
            if safe_play_func:
                safe_play_func("dialog-information.oga")
        except Exception:
            pass

        # ============================================================
        # 6. ALLGEMEINES REPAINT
        # ============================================================
        try:
            if hasattr(self, "repaint_ui_colors"):
                self.repaint_ui_colors()
        except Exception:
            pass


        # ============================================================
        # 7/8. KEINE Sonderfarbe für aktive Grid-Buttons:
        # Alle Buttons bleiben im ausgewählten Farbschema.
        # ============================================================

        # ============================================================
        # 9. INSTALLATIONSBUTTONS EXPLIZIT SCHÜTZEN
        #
        # Ihre individuelle Farbe bleibt erhalten.
        # ============================================================
        for attr_name in ("btn_s3", "btn_s4", "btn_ncam"):

            btn = getattr(self, attr_name, None)

            if not btn:
                continue

            try:
                # Vorhandenes individuelles Styling NICHT verändern.
                # Falls der Button noch keinen Style besitzt,
                # bekommt er einen neutralen Fallback.
                if not btn.styleSheet().strip():

                    btn.setStyleSheet(
                        f"""
                        QPushButton {{
                            text-align: left;
                            padding-left: 8px;
                            font-weight: bold;
                            background-color: {bg};
                            color: {fg};
                            border: 2px solid {fg};
                            border-radius: 8px;
                        }}
                        """
                    )


            except RuntimeError:
                pass

        # ============================================================
        # 10. TELEMETRIE CHECKBOX
        # ============================================================
        telemetry_cb = getattr(self, "telemetry_cb", None)

        if telemetry_cb:

            try:
                telemetry_cb.setStyleSheet(
                    f"""
                    QCheckBox {{
                        background-color: {bg};
                        color: {fg};
                        border: 2px solid {fg};
                        border-radius: 4px;
                        padding: 5px 10px;
                        font-weight: 700;
                    }}

                    QCheckBox:hover {{
                        background-color: {hover};
                    }}

                    QCheckBox::indicator {{
                        width: 16px;
                        height: 16px;
                        border: 2px solid {fg};
                        border-radius: 3px;
                        background-color: transparent;
                    }}

                    QCheckBox::indicator:hover {{
                        border: 1px solid white;
                    }}

                    QCheckBox::indicator:checked {{
                        background-color: {fg};
                        border: 1px solid white;
                    }}
                    """
                )

            except RuntimeError:
                pass

        # ============================================================
        # 11. HEADER CONTAINER
        # ============================================================
        header_container = getattr(self, "header_container", None)

        if header_container:

            try:
                header_container.setStyleSheet(
                    f"""
                    QFrame {{
                        background-color: {bg};
                        border: 1px solid #444444;
                        border-radius: 8px;
                    }}
                    """
                )
            except RuntimeError:
                pass

        # ============================================================
        # 12. BADGES
        # ============================================================
        badge_style = f"""
            QFrame {{
                background-color: {bg};
                border: 1px solid #444444;
                border-radius: 6px;
            }}

            QFrame:hover {{
                border: 2px solid {fg};
            }}
        """

        left_badge = getattr(self, "left_badge", None)

        if left_badge:
            try:
                left_badge.setStyleSheet(badge_style)
            except RuntimeError:
                pass

        right_badge = getattr(self, "right_badge", None)

        if right_badge:
            try:
                right_badge.setStyleSheet(
                    f"""
                    QFrame {{
                        background-color: transparent;
                        border: 1px solid #444444;
                        border-radius: 6px;
                    }}

                    QFrame:hover {{
                        border: 2px solid {fg};
                    }}
                    """
                )
            except RuntimeError:
                pass

        # ============================================================
        # 13. HEADER LABEL
        # ============================================================
        header_label = getattr(self, "header_label", None)

        if header_label:

            try:
                header_label.setStyleSheet(
                    f"""
                    QLabel {{
                        color: {fg};
                        font-weight: bold;
                        font-size: 15px;
                        background: transparent;
                        border: none;
                    }}
                    """
                )
            except RuntimeError:
                pass

        # ============================================================
        # 14. STATUS LABEL
        # ============================================================
        status_label = getattr(self, "status_label", None)

        if status_label:

            try:
                status_label.setStyleSheet(
                    f"""
                    QLabel {{
                        color: {fg};
                        font-weight: bold;
                        font-size: 18px;
                        background: transparent;
                        border: none;
                    }}
                    """
                )
            except RuntimeError:
                pass

        # ============================================================
        # 15. LED-STATUS ERZWINGEN
        #
        # MUSS NACH DEM REPAINT KOMMEN.
        # ============================================================
        is_led_enabled = config_obj.get("led_enabled", True)

        blink_timer = getattr(self, "blink_timer", None)

        if blink_timer:

            try:
                blink_timer.stop()
            except RuntimeError:
                pass

        # LEDs zuerst in definierten statischen Zustand bringen
        try:
            if hasattr(self, "force_user_leds_static"):
                self.force_user_leds_static()
        except Exception:
            pass

        # Danach Blinkbetrieb wieder aktivieren
        if is_led_enabled:

            try:
                slider = getattr(self, "slider_speed", None)

                speed_val = slider.value() if slider else 500

                if 10 <= speed_val < 950 and blink_timer:
                    blink_timer.start(speed_val)

            except (RuntimeError, ValueError, TypeError):
                pass

        # ============================================================
        # 16. CONFIG AKTUALISIEREN
        # ============================================================
        old_color = config_obj.get("theme_color", config_obj.get("color"))

        config_changed = old_color != current_color_name

        if config_changed:
            # Keep both keys synchronized: older code reads "color", while
            # the settings selector reads "theme_color".
            config_obj["color"] = current_color_name
            config_obj["theme_color"] = current_color_name

            # Nur bei tatsächlicher Farbänderung Theme-Modus neu setzen
            if "matrix" in str(current_color_name).lower():
                config_obj["theme_mode"] = "matrix"
            else:
                config_obj["theme_mode"] = "standard"


        # ============================================================
        # 17. INSTANZEN SYNCHRONISIEREN
        # ============================================================
        try:
            if hasattr(self, "cfg"):
                self.cfg = config_obj

            if hasattr(self, "current_config"):
                self.current_config = config_obj

        except Exception:
            pass

        # ============================================================
        # 18. SPEICHERN
        #
        # Während des Startvorgangs nicht unnötig speichern.
        # ============================================================
        if not getattr(self, "is_loading", False):

            try:
                save_config_func = globals().get("save_config")

                if save_config_func:
                    save_config_func(
                        config_obj,
                        gui_instance=self,
                        silent=True
                    )

            except Exception as e:
                print(f"[change_colors] Config konnte nicht gespeichert werden: {e}")

        # ============================================================
        # 18b. FARBMELDUNG IM GROSSEN INFOSCREEN
        #
        # Nur bei einer tatsächlichen Änderung melden.
        # Die Meldung wird ANGEHÄNGT und überschreibt keine
        # vorhandenen Infoscreen-Meldungen.
        # ============================================================
        if config_changed and not getattr(self, "is_loading", False):

            try:
                info_widget = getattr(self, "info_text", None)

                if info_widget is not None and hasattr(self, "append_info"):

                    lang = str(getattr(self, "LANG", "de")).lower()

                    if lang.startswith("de"):
                        color_msg = (
                            f"🎨 Farbe eingestellt: <b>{current_color_name}</b>"
                        )
                    else:
                        color_msg = (
                            f"🎨 Color selected: <b>{current_color_name}</b>"
                        )

                    # WICHTIG:
                    # Den bereits aufgebauten großen Infoscreen niemals neu setzen
                    # oder leeren. Der komplette System-Check-Block (Copyright,
                    # LIVE/System Monitor, Autor/Version, Hauptmerkmale,
                    # Systemdaten, Tools, Netzwerk, Repositories, Statistik
                    # und "System-Check OK. Bereit.") bleibt erhalten.
                    #
                    # Vor dem Anhängen merken wir uns den aktuellen HTML-Inhalt.
                    # append_info fügt anschließend ausschließlich am Ende ein.
                    existing_html = info_widget.toHtml()

                    self.append_info(
                        info_widget,
                        color_msg,
                        "info"
                    )

                    # Sicherheitsprüfung: Falls eine unerwartete UI-Aktion
                    # den Inhalt verändert hätte, den vollständigen bisherigen
                    # Infoscreen wiederherstellen und die Farbm­eldung danach
                    # erneut anhängen.
                    if existing_html and not info_widget.toHtml().startswith(
                        existing_html[:80]
                    ):
                        info_widget.setHtml(existing_html)
                        self.append_info(
                            info_widget,
                            color_msg,
                            "info"
                        )

            except Exception as e:
                print(f"[change_colors] Farbmeldung im Infoscreen fehlgeschlagen: {e}")

        # ============================================================
        # 19. BUTTON-TEXTE NACH THEME/STYLE SICHER ANPASSEN
        # ============================================================
        try:
            from PyQt6.QtCore import QTimer

            if hasattr(self, "_fit_all_button_texts"):

                # Nach dem ersten Theme-Repaint
                QTimer.singleShot(
                    100,
                    self._fit_all_button_texts
                )

                # Nach Sprach-/Layout-Updates
                QTimer.singleShot(
                    500,
                    self._fit_all_button_texts
                )

                # Nach vollständiger GUI-Initialisierung
                QTimer.singleShot(
                    1000,
                    self._fit_all_button_texts
                )

        except Exception as e:
            print(f"[change_colors] Button-Text-Anpassung fehlgeschlagen: {e}")

        # Letzter Schritt: sämtliche Buttons (einschließlich S3/S4/NCam und
        # Stats) erhalten das ausgewählte Theme, dessen Hover-Farbe und Tooltips.
        try:
            self.apply_global_button_style()
        except Exception as exc:
            print(f"[change_colors] Button-Theme/Tooltips konnten nicht aktualisiert werden: {exc}")

    def log_message(self, message):
        """Zentrale Funktion: Zeit in ROT, Inhalt in CYAN - sauber untereinander."""
        from datetime import datetime

        now = datetime.now().strftime("%H:%M:%S")

        # 1. Sicherstellen, dass wir eine neue Zeile haben
        self.info_text.append("")

        # 2. HTML-Zeile bauen (Zeit ROT, Nachricht CYAN/Inhalt)
        full_html = f"<span style='color:red;'>[{now}]</span> {message}"

        # 3. Cursor ans Ende und HTML einfügen
        self.info_text.moveCursor(QTextCursor.MoveOperation.End)
        self.info_text.insertHtml(full_html)

        # 4. Scrollen
        self.info_text.moveCursor(QTextCursor.MoveOperation.End)

    def scroll_to_bottom_smooth(self):
        """Scrollt das Info-Fenster sanft nach unten."""
        from PyQt6.QtCore import QPropertyAnimation, QEasingCurve

        if not hasattr(self, "info_text") or not self.info_text:
            return

        scrollbar = self.info_text.verticalScrollBar()

        # Animation für die Eigenschaft 'value' der Scrollbar
        self.scroll_anim = QPropertyAnimation(scrollbar, b"value")
        self.scroll_anim.setDuration(300)  # Dauer in ms
        self.scroll_anim.setStartValue(scrollbar.value())
        self.scroll_anim.setEndValue(scrollbar.maximum())

        # Eine sanfte Kurve (beschleunigt und bremst ab)
        self.scroll_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.scroll_anim.start()

    
    def show_info(self):
        """Zeigt das Info-Fenster mit sprachabhängigen Texten und Theme-Farben."""
        from PyQt6.QtWidgets import (
            QApplication,
            QDialog,
            QVBoxLayout,
            QHBoxLayout,
            QFormLayout,
            QLabel,
            QGroupBox,
            QPushButton,
            QStyle,
        )
        from PyQt6.QtGui import QFont
        from PyQt6.QtCore import Qt

        lang = str(getattr(self, "LANG", "de")).lower()[:2]
        is_de = lang == "de"
        t = TEXTS.get(lang, TEXTS.get("en", {}))

        # --------------------------------------------------
        # Info-Button: Tooltip sicherstellen
        # --------------------------------------------------
        info_btn = getattr(self, "btn_info", None)

        if info_btn is not None:
            info_btn.setToolTip(
                "Zeigt Informationen über das OSCam Toolkit an."
                if is_de
                else "Displays information about the OSCam Toolkit."
            )
            info_btn.setToolTipDuration(10000)

            # Sicherstellen, dass der Button die Methode öffnet.
            try:
                info_btn.clicked.disconnect(self.show_info)
            except (TypeError, RuntimeError):
                pass

            info_btn.clicked.connect(self.show_info)

        # --------------------------------------------------
        # Dialog erstellen
        # --------------------------------------------------
        info_dialog = QDialog(self)
        info_dialog.setWindowTitle(
            t.get("info_title", "About OSCam Emu Toolkit")
        )
        info_dialog.resize(450, 410)
        info_dialog.setMinimumHeight(390)
        info_dialog.setMaximumHeight(450)

        # --------------------------------------------------
        # Farben aus dem aktuellen Theme laden
        # --------------------------------------------------
        colors = globals().get("current_diff_colors", {})

        bg_color = colors.get("bg", "#FFFFFF")
        text_color = colors.get("text", "#000000")
        border_col = colors.get("border", "#888888")
        active_color = colors.get("active", "#e0e0e0")

        info_dialog.setStyleSheet(
            f"""
            QDialog {{
                background-color: {bg_color};
            }}
            """
        )

        layout = QVBoxLayout(info_dialog)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(15)

        # --------------------------------------------------
        # Header
        # --------------------------------------------------
        header_layout = QHBoxLayout()

        icon_label = QLabel()
        icon = QApplication.style().standardIcon(
            QStyle.StandardPixmap.SP_MessageBoxInformation
        )
        icon_label.setPixmap(icon.pixmap(64, 64))

        title_vbox = QVBoxLayout()

        title_label = QLabel("OSCam Emu Patch Manager")
        title_label.setFont(QFont("Arial", 14, QFont.Weight.Bold))
        title_label.setStyleSheet("color: #0000FF;")

        version_label = QLabel(f"Version {APP_VERSION}")
        version_label.setStyleSheet("color: #666666;")

        title_vbox.addWidget(title_label)
        title_vbox.addWidget(version_label)

        header_layout.addWidget(icon_label)
        header_layout.addLayout(title_vbox)
        header_layout.addStretch()

        layout.addLayout(header_layout)

        # --------------------------------------------------
        # Credits / Authors
        # --------------------------------------------------
        credits_title = t.get("credits_label", "Credits / Authors")
        credits_group = QGroupBox(credits_title)

        credits_group.setStyleSheet(
            f"""
            QGroupBox {{
                color: {text_color};
                border: 1px solid {border_col};
                margin-top: 10px;
                font-weight: bold;
            }}
            """
        )

        credits_layout = QFormLayout(credits_group)

        author_label = QLabel(
            "Hauptautor:" if is_de else "Main Author:"
        )
        author_val = QLabel("<b>speedy005</b>")
        author_val.setStyleSheet("color: #008000;")

        emu_label = QLabel(
            "Emu-Patch:" if is_de else "Emu Patch:"
        )
        emu_val = QLabel("OSCam-Emu Team")
        emu_val.setStyleSheet("color: #0000FF;")

        license_label = QLabel("Lizenz:" if is_de else "License:")
        lic_val = QLabel("MIT License")
        lic_val.setStyleSheet("color: #FF0000;")

        for label in (author_label, emu_label, license_label):
            label.setStyleSheet(f"color: {text_color};")

        credits_layout.addRow(author_label, author_val)
        credits_layout.addRow(emu_label, emu_val)
        credits_layout.addRow(license_label, lic_val)

        layout.addWidget(credits_group)

        # --------------------------------------------------
        # OSCam-Quellrepository
        # --------------------------------------------------
        source_title = (
            "OSCam-Quellrepository"
            if is_de
            else "OSCam source repository"
        )

        source_group = QGroupBox(source_title)
        source_group.setStyleSheet(
            f"""
            QGroupBox {{
                color: {text_color};
                border: 1px solid {border_col};
                margin-top: 10px;
                font-weight: bold;
            }}
            """
        )

        source_layout = QVBoxLayout(source_group)

        try:
            active_source = get_selected_stream_repo_name()
        except Exception:
            active_source = "Unbekannt" if is_de else "Unknown"

        try:
            active_url = get_selected_stream_repo_url()
        except Exception:
            active_url = ""

        source_label = QLabel(f"{active_source}\n{active_url}")
        source_label.setWordWrap(True)
        source_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        source_label.setStyleSheet(f"color: {text_color};")

        source_layout.addWidget(source_label)
        layout.addWidget(source_group)

        # --------------------------------------------------
        # Copyright
        # --------------------------------------------------
        copy_label = QLabel(
            "© 2026 speedy005 - Alle Rechte vorbehalten."
            if is_de
            else "© 2026 speedy005 - All rights reserved."
        )
        copy_label.setFont(QFont("Arial", 8))
        copy_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        copy_label.setStyleSheet(f"color: {text_color};")

        layout.addWidget(copy_label)

        # --------------------------------------------------
        # Schließen-Button
        # --------------------------------------------------
        close_btn = QPushButton(t.get("close", "Schließen" if is_de else "Close"))
        close_btn.setMinimumHeight(50)

        button_radius = getattr(self, "BUTTON_RADIUS", 8)

        close_btn.setStyleSheet(
            f"""
            QPushButton {{
                border-radius: {button_radius}px;
                background-color: {active_color};
                color: #0000FF;
                font-weight: bold;
                border: 1px solid {border_col};
                padding: 6px;
            }}
            QPushButton:hover {{
                border: 1px solid #0000FF;
            }}
            """
        )

        close_btn.clicked.connect(info_dialog.accept)
        layout.addWidget(close_btn)

        # Dialog modal anzeigen
        info_dialog.exec()


    def get_t(self, key, default=None):
        """Zentrale Methode zur Textübersetzung."""
        lang = str(getattr(self, "LANG", "de")).lower()
        # Dictionary für die aktuelle Sprache holen, Fallback auf Englisch
        lang_dict = TEXTS.get(lang, TEXTS.get("en", {}))

        if key in lang_dict:
            return lang_dict[key]

        # Falls Key fehlt, technischen Namen schön machen
        if default:
            return default

        return str(key).replace("_", " ").title()

    def get_gui_settings(self):
        """Sammelt alle aktuellen Einstellungen aus der UI für die Config."""
        return {
            # Falls du ein Eingabefeld/Spinbox für Commits hast:
            "commit_count": (
                self.commit_spin.value()
                if hasattr(self, "commit_spin")
                else getattr(self, "commit_count", 5)
            ),
            # Falls du eine Color-Box (QComboBox) hast:
            "color": (
                self.color_box.currentText()
                if hasattr(self, "color_box")
                else getattr(self, "current_color", "")
            ),
            # Sprache (Wichtig: "language" muss klein geschrieben sein wie in load_config!)
            "language": str(getattr(self, "LANG", "DE")).upper(),
            # Pfad
            "s3_patch_path": getattr(self, "OLD_PATCH_DIR", OLD_PATCH_DIR),
        }

    def show_update_dialog(self, latest_version, current_version):
        """Öffnet das Pop-up Fenster für das verfügbare Update."""

        # --- Final-Label ausblenden ---
        if hasattr(self, "hide_final_label"):
            self.hide_final_label()
        elif hasattr(self, "final_label") and self.final_label:
            self.final_label.hide()
        from PyQt6.QtWidgets import QMessageBox

        txt = getattr(self, "TEXT", {})
        lang = getattr(self, "LANG", "de").lower()

        msg_box = QMessageBox(self)
        msg_box.setIcon(QMessageBox.Icon.Information)
        msg_box.setWindowTitle(txt.get("update_title", "Update"))

        # Text-Inhalt
        msg_text = (
            f"{txt.get('update_msg', 'Update verfügbar')}\n\n"
            f"{txt.get('new_version_label', 'Neu')}: v{latest_version}\n"
            f"{txt.get('old_version_label', 'Installiert')}: v{current_version}"
        )
        msg_box.setText(msg_text)

        # Buttons übersetzen
        msg_box.setStandardButtons(
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        btn_yes = msg_box.button(QMessageBox.StandardButton.Yes)
        if btn_yes:
            btn_yes.setText(txt.get("yes", "Ja"))
        btn_no = msg_box.button(QMessageBox.StandardButton.No)
        if btn_no:
            btn_no.setText(txt.get("no", "Nein"))

        # Sound beim Öffnen des Update-Fensters
        sound_manager = getattr(self, "sound_manager", None)
        if sound_manager and sound_manager.enabled:
            sound_manager.success_sound.play()

        if msg_box.exec() == QMessageBox.StandardButton.Yes:
            if hasattr(self, "plugin_update_action"):
                self.plugin_update_action(latest_version=latest_version)

    def change_emu_repo(self):
        """Wählt das OSCam-Emu-Git-Repository für die nächste Patch-Erstellung."""
        from PyQt6.QtWidgets import QInputDialog, QApplication, QMessageBox
        from PyQt6.QtCore import QTimer
        import re

        presets=[
            "https://github.com/oscam-mirror/oscam-emu.git",
            "https://github.com/speedy005/Oscam-emu.git",
        ]
        current=str(getattr(self,"EMUREPO",None) or globals().get("EMUREPO",presets[0])).strip()
        if current and current not in presets:
            presets.insert(0,current)

        is_de=str(getattr(self,"LANG","de")).lower()[:2]=="de"
        dialog=QInputDialog(self)
        dialog.setWindowTitle("OSCam-Emu Git auswählen" if is_de else "Select OSCam-Emu Git")
        dialog.setLabelText("Repository-URL auswählen oder eigene Git-URL eingeben:" if is_de else "Select a repository URL or enter your own Git URL:")
        dialog.setComboBoxItems(presets)
        dialog.setComboBoxEditable(True)
        dialog.setTextValue(current or presets[0])
        dialog.setOkButtonText("Übernehmen" if is_de else "Apply")
        dialog.setCancelButtonText("Abbrechen" if is_de else "Cancel")

        if not dialog.exec():
            return False
        new_url=(dialog.textValue() or "").strip()
        valid=bool(re.match(r"^(https?://|ssh://|git@|file://|/|[A-Za-z]:[\\/])",new_url))
        if not valid:
            QMessageBox.warning(self,"OSCam-Emu Git",
                "Bitte eine gültige Git-Repository-URL eingeben." if is_de else
                "Please enter a valid Git repository URL.")
            return False

        # Die Auswahl sofort als aktive Patch-Quelle merken.
        # create_patch() darf hier NICHT wieder auf eine alte
        # emu_repo_url aus der geladenen Config zurückfallen.
        self.EMUREPO = new_url
        self.selected_emu_repo_url = new_url
        self.selected_emu_repo_branch = str(
            getattr(self, "selected_emu_repo_branch", "master") or "master"
        ).strip() or "master"

        # Eine OSCam-Emu-Revision gehört immer zu genau EINEM Repository.
        # Wird über "Repo URL" das Repository gewechselt, darf eine zuvor
        # ausgewählte SHA aus dem alten Repository nicht weiterverwendet werden.
        self.selected_emu_commit = None
        self.selected_emu_commit_repo_url = ""
        self.display_emu_commit = None
        globals()["SELECTED_EMU_COMMIT"] = ""
        globals()["SELECTED_EMU_COMMIT_REPO_URL"] = ""

        globals()["EMUREPO"] = new_url
        globals()["SELECTED_EMU_REPO_URL"] = new_url
        globals()["SELECTED_EMU_REPO_BRANCH"] = self.selected_emu_repo_branch
        if hasattr(self,"cfg"):
            self.cfg["EMUREPO"]=new_url
            self.cfg["emu_repo_url"]=new_url
            saver=globals().get("save_config")
            if callable(saver):
                saver(self.cfg,gui_instance=self)

        pbar=getattr(self,"progress_bar",None)
        if pbar:
            pbar.setValue(100)
            pbar.setFormat(("✅ OSCam-Emu Git gesetzt: %p%" if is_de else "✅ OSCam-Emu Git selected: %p%"))
            pbar.show()
            QTimer.singleShot(2500,getattr(self,"pbar_idle",lambda:pbar.setValue(0)))
        # Nach einem Repo-Wechsel die Revision-Anzeige sofort auf den
        # aktuellen Stand des neu gewählten OSCam-Emu-Repositories setzen.
        try:
            self.update_revision_button_labels()
        except Exception:
            pass

        if hasattr(self,"info_text"):
            self.info_text.append(f"<b>✅ OSCam-Emu Git:</b> {new_url}")
            self.info_text.append(
                "<b>ℹ️ OSCam-Emu Revision:</b> Auswahl zurückgesetzt – "
                "beim nächsten Create Patch wird der aktuelle Branch-Stand "
                "dieses Repositories verwendet."
            )
            self.info_text.ensureCursorVisible()
        return True
    def change_modifier_name(self):
        """Öffnet Autor-Dialog mit Regenbogen-Progress, Sound und DE/EN Support."""

        # --- Final-Label ausblenden ---
        if hasattr(self, "hide_final_label"):
            self.hide_final_label()
        elif hasattr(self, "final_label") and self.final_label:
            self.final_label.hide()
        from PyQt6.QtWidgets import QInputDialog, QLineEdit, QApplication
        from PyQt6.QtCore import QTimer

        # 1. SETUP & SPRACHE
        current_author = getattr(self, "patch_modifier", "speedy005")
        lang = str(getattr(self, "LANG", "de")).lower()[:2]
        is_de = lang == "de"
        pbar = getattr(self, "progress_bar", None)

        # Lokalisierte Texte
        T_TITLE = "Patch Autor" if is_de else "Patch Author"
        T_LABEL = "Name des Autors:" if is_de else "Author Name:"
        T_SAVING = "Speichere Autor..." if is_de else "Saving Author..."
        T_DONE = "Autor geändert!" if is_de else "Author changed!"
        T_CANCEL = "Abgebrochen" if is_de else "Cancelled"

        # RESET-FUNKTION (Zurück zu Orange/Gold)
        def restore_style():
            if pbar:
                pbar.setValue(0)
                pbar.setFormat("%p%")
                pbar.setStyleSheet(
                    """
                    QProgressBar { border: 1px solid #444; border-radius: 8px; background-color: #1A1A1A; 
                    color: black; text-align: center; font-weight: bold; }
                    QProgressBar::chunk { background-color: qlineargradient(x1:0, y1:0, x2:1, y2:0, 
                    stop:0 #F37804, stop:1 #FFD700); border-radius: 8px; }
                """
                )

        # --- SOUND BEIM ÖFFNEN ---
        if "safe_play" in globals():
            safe_play("dialog-information.oga")

        # 2. REGENBOGEN-PROGRESS STARTEN
        if pbar:
            rainbow = (
                "qlineargradient(x1:0, y1:0, x2:1, y2:0, "
                "stop:0.0 #FF0000, stop:0.2 #FF7F00, stop:0.4 #FFFF00, "
                "stop:0.6 #00FF00, stop:0.8 #0000FF, stop:1.0 #8B00FF);"
            )
            pbar.setStyleSheet(
                f"""
                QProgressBar {{
                    text-align: center; font-weight: 900; border: 2px solid #222;
                    border-radius: 6px; background-color: #111; color: black; font-size: 15pt;
                }}
                QProgressBar::chunk {{ background-color: {rainbow}; border-radius: 4px; }}
            """
            )
            pbar.setFormat(f"👤 {T_SAVING} %p%")
            pbar.setValue(20)
            pbar.show()
            QApplication.processEvents()

        # 3. DIALOG KONFIGURIEREN
        dialog = QInputDialog(self)
        dialog.setWindowTitle(T_TITLE)
        dialog.setLabelText(T_LABEL)
        dialog.setTextValue(current_author)
        dialog.setOkButtonText("OK")
        dialog.setCancelButtonText("Abbrechen" if is_de else "Cancel")

        if dialog.exec():
            new_name = dialog.textValue().strip()
            if new_name:
                if pbar:
                    pbar.setValue(60)
                self.patch_modifier = new_name

                if hasattr(self, "cfg"):
                    self.cfg["patch_modifier"] = self.patch_modifier
                    try:
                        if "save_config" in globals():
                            globals()["save_config"](self.cfg, gui_instance=self)

                        if hasattr(self, "update_language"):
                            self.update_language()

                        # --- ERFOLG ---
                        if "safe_play" in globals():
                            safe_play("complete.oga")
                        if pbar:
                            pbar.setValue(100)
                            pbar.setFormat(f"✅ {T_DONE}")
                            QApplication.processEvents()

                    except Exception as e:
                        if "safe_play" in globals():
                            safe_play("dialog-error.oga")
                        if pbar:
                            pbar.setStyleSheet(
                                "QProgressBar { color: red; font-weight: 900; }"
                            )

                # Auto-Reset nach Erfolg
                QTimer.singleShot(3000, restore_style)
        else:
            # --- 4. SPEZIELLER ABBRUCH-ABLAUF ---
            if "safe_play" in globals():
                safe_play("dialog-warning.oga")

            if pbar:
                # Erst auf 100% füllen (Regenbogen)
                pbar.setValue(100)
                pbar.setFormat("100%")
                pbar.repaint()
                QApplication.processEvents()

                # Nach 400ms: Auf 0 setzen und rote Meldung zeigen
                def show_cancel_msg():
                    pbar.setValue(0)
                    pbar.setFormat(f"❌ {T_CANCEL}")
                    # Rotes Highlight für den Fehler/Abbruch
                    pbar.setStyleSheet(
                        f"""
                        QProgressBar {{ 
                            text-align: center; color: red; font-weight: 900; 
                            font-size: 15pt; border: 2px solid red; background: #111; 
                        }}
                    """
                    )
                    pbar.repaint()
                    QApplication.processEvents()
                    # Nach weiteren 2 Sekunden endgültiger Reset zu Orange/Gold
                    QTimer.singleShot(2000, restore_style)

                QTimer.singleShot(400, show_cancel_msg)

    def collect_and_save(self):
        """Speichert leise und aktualisiert die Farben sofort."""
        try:
            theme_name = self.color_box.currentText()

            # Globale Farbe aktualisieren, damit repaint_ui_colors sie findet
            global current_diff_colors
            if theme_name in DIFF_COLORS:
                current_diff_colors = DIFF_COLORS[theme_name]

            new_cfg = {
                # "color" ist der kanonische Schlüssel in load_config().
                "color": theme_name,
                "commit_count": int(self.commit_spin.value()),
                "language": str(self.language_box.currentText()).lower()[:2],
                "work_dir": getattr(self, "WORK_DIR", self.base_dir),
                "s3_patch_path": getattr(
                    self, "OLD_PATCH_DIR", self.current_config.get("s3_patch_path", self.base_dir)
                ),
            }

            # Niemals die komplette Config mit einem Teil-Dictionary überschreiben.
            # save_config merged die Änderungen mit den vorhandenen Einstellungen.
            save_config(new_cfg, gui_instance=self, silent=True)
            self.current_config.update(new_cfg)
            # UI sofort neu färben
            self.repaint_ui_colors()

            # Log nur in der GUI (kein print!)
            self.info_text.append(f"💾 Konfiguration gespeichert: {theme_name}")

        except Exception as e:
            self.info_text.append(f"❌ Fehler beim Speichern: {e}")

    def manual_tool_check(self):
        """System-Check mit Regenbogen-Progress, Sound und DE/EN Support."""

        # --- Final-Label ausblenden ---
        if hasattr(self, "hide_final_label"):
            self.hide_final_label()
        elif hasattr(self, "final_label") and self.final_label:
            self.final_label.hide()
        import time, shutil, os, platform
        from datetime import datetime
        from PyQt6.QtWidgets import QApplication
        from PyQt6.QtCore import QTimer

        # 1. Doppelklick-Sperre & Setup
        now_ts = time.time()
        if hasattr(self, "_last_check_run") and (now_ts - self._last_check_run) < 0.5:
            return
        self._last_check_run = now_ts

        lang = str(getattr(self, "LANG", "de")).lower()[:2]
        is_de = lang == "de"
        pbar = getattr(self, "progress_bar", None)
        timestamp = datetime.now().strftime("%H:%M:%S")

        # Lokalisierte Texte
        T_START = "System-Check..." if is_de else "System Check..."
        T_DONE = "Check bereit!" if is_de else "Check ready!"
        T_ERR = "Fehler!" if is_de else "Error!"

        # --- SOUND BEIM ÖFFNEN (Start) ---
        if "safe_play" in globals():
            safe_play("service-login.oga")

        # 2. REGENBOGEN-PROGRESS AKTIVIEREN
        if pbar:
            rainbow = (
                "qlineargradient(x1:0, y1:0, x2:1, y2:0, "
                "stop:0.0 #FF0000, stop:0.2 #FF7F00, stop:0.4 #FFFF00, "
                "stop:0.6 #00FF00, stop:0.8 #0000FF, stop:1.0 #8B00FF);"
            )
            pbar.setStyleSheet(
                f"""
                QProgressBar {{
                    text-align: center; font-weight: 700; border: 2px solid #222;
                    border-radius: 6px; background-color: #111; color: black; font-size: 15pt;
                }}
                QProgressBar::chunk {{ background-color: {rainbow}; border-radius: 4px; }}
            """
            )
            pbar.setFormat(f"🛠️ {T_START} %p%")
            pbar.setValue(20)
            pbar.show()
            QApplication.processEvents()

        try:
            self.info_text.clear()
            msg_init = (
                f"Starte System-Check... [{timestamp}]"
                if is_de
                else f"Starting system check... [{timestamp}]"
            )
            self.append_info(msg_init, "info")

            if pbar:
                pbar.setValue(50)

            # 3. Tools prüfen
            required_tools = ["git", "python3"]
            if platform.system() != "Windows":
                required_tools.extend(["patch", "zip"])

            missing = [t for t in required_tools if shutil.which(t) is None]

            if not missing:
                self.append_info(
                    (
                        "✅ Alle benötigten System-Tools sind bereit."
                        if is_de
                        else "✅ All required tools are ready."
                    ),
                    "success",
                )

                # --- SOUND BEI ERFOLG ---
                if "safe_play" in globals():
                    safe_play("complete.oga")

                if pbar:
                    pbar.setValue(100)
                    pbar.setFormat(f"✅ {T_DONE} 100%")

                # Folgeschritte ausführen
                if hasattr(self, "check_for_plugin_update"):
                    self.check_for_plugin_update()
                if hasattr(self, "show_start_config"):
                    self.show_start_config()
            else:
                # --- SOUND BEI WARNUNG ---
                if "safe_play" in globals():
                    safe_play("dialog-error.oga")
                missing_str = ", ".join(missing)
                self.append_info(
                    (
                        f"⚠️ Fehlende Tools: {missing_str}"
                        if is_de
                        else f"⚠️ Missing tools: {missing_str}"
                    ),
                    "warning",
                )
                if pbar:
                    pbar.setValue(0)

        except Exception as e:
            if "safe_play" in globals():
                safe_play("dialog-error.oga")
            self.append_info(f"❌ Check-Fehler: {str(e)}", "error")
            if pbar:
                pbar.setStyleSheet("QProgressBar { color: red; font-weight: 700; }")

        # 4. AUTO-RESET (3 Sekunden) -> Zurück zu Orange/Gold Style
        if pbar:

            def restore_style():
                pbar.setValue(0)
                pbar.setFormat("%p%")
                pbar.setStyleSheet(
                    """
                    QProgressBar { border: 1px solid #444; border-radius: 8px; background-color: #1A1A1A; 
                    color: black; text-align: center; font-weight: bold; }
                    QProgressBar::chunk { background-color: qlineargradient(x1:0, y1:0, x2:1, y2:0, 
                    stop:0 #F37804, stop:1 #FFD700); border-radius: 8px; }
                """
                )

            QTimer.singleShot(3000, restore_style)

    def show_start_config(self):
        """Entfernt alle Textanzeigen und setzt nur noch den ProgressBar-Abschluss."""
        from PyQt6.QtWidgets import QApplication
        from PyQt6.QtCore import QTimer

        # --- 1. PROGRESSBAR ABSCHLUSS-TEXT ---
        pbar = getattr(self, "progress_bar", None)
        if pbar:
            lang = getattr(self, "LANG", "de").lower()
            is_de = lang == "de"
            ready_txt = "✅ System bereit" if is_de else "✅ System ready"

            pbar.setValue(100)
            pbar.setFormat(f"{ready_txt}")

            # CSS für gute Lesbarkeit auf dem Regenbogen-Balken
            pbar.setStyleSheet(
                pbar.styleSheet() + " QProgressBar { color: black; font-weight: bold; }"
            )

        QApplication.processEvents()

        # --- 2. SOUND & TIMER ---
        if "safe_play" in globals():
            safe_play("complete.oga")

        # Nach 3 Sek. wird der Balken durch pbar_idle geleert/versteckt
        if hasattr(self, "pbar_idle"):
            QTimer.singleShot(500, self.pbar_idle)

    
    def resizeEvent(self, event):
        """
        Zentrale Steuerung für die UI-Skalierung bei Auflösungsänderung.
        Passt Header, Logo, Schriftgrößen und Overlay an.
        """
        super().resizeEvent(event)

        # Responsive Breiten für Status- und Einstellungsleiste:
        # feste Pixelbreiten waren auf schmalen Fenstern unnötig platzintensiv.
        try:
            compact = self.width() < 1150
            if hasattr(self, "status_info_label"):
                self.status_info_label.setFixedWidth(145 if compact else 220)
                self.status_info_label.setStyleSheet(
                    "color: #FF0000; font-size: %dpx; font-weight: bold; border: none; background: transparent;"
                    % (16 if compact else 22)
                )
            if hasattr(self, "btn_matrix"):
                self.btn_matrix.setFixedWidth(180 if compact else 300)
            if hasattr(self, "btn_theme"):
                self.btn_theme.setFixedWidth(130 if compact else 200)
            if hasattr(self, "slider_speed"):
                self.slider_speed.setFixedWidth(75 if compact else 110)
            if hasattr(self, "lbl_speed_val"):
                self.lbl_speed_val.setFixedWidth(65 if compact else 85)
            if hasattr(self, "language_box"):
                self.language_box.setFixedWidth(65 if compact else 80)
            if hasattr(self, "color_label"):
                self.color_label.setFixedWidth(85 if compact else 120)
            if hasattr(self, "color_box"):
                self.color_box.setFixedWidth(95 if compact else 120)
            if hasattr(self, "commit_label"):
                self.commit_label.setFixedWidth(100 if compact else 140)
            if hasattr(self, "commit_spin"):
                self.commit_spin.setFixedWidth(65 if compact else 75)
            if hasattr(self, "btn_check_tools"):
                self.btn_check_tools.setFixedWidth(165 if compact else 220)
        except RuntimeError:
            # Widgets können während des Schließens bereits zerstört sein.
            pass

        # Buttonhöhen und Beschriftungen bei jeder Fenstergrößenänderung neu berechnen.
        QTimer.singleShot(0, self._fit_all_button_texts)

        # 1. Header-Steuerelemente responsiv anpassen
        if hasattr(self, "header_widget"):
            QTimer.singleShot(0, self._fit_header_controls)

        # 2. Dynamische Schriftgrößen berechnen
        dynamic_size = max(10, int(self.width() / 90))

        button_font = QFont(
            "Arial", dynamic_size, QFont.Weight.Bold
        )
        label_font = QFont(
            "Arial", max(9, dynamic_size - 1)
        )
        clock_font = QFont(
            "Arial",
            max(12, int(dynamic_size * 0.9)),
            QFont.Weight.Bold,
        )

        # 3. Alle Buttons aktualisieren
        for btn in getattr(self, "all_buttons", []):
            btn.setFont(button_font)

            if not btn.icon().isNull():
                icon_dim = int(dynamic_size * 2.0)
                btn.setIconSize(QSize(icon_dim, icon_dim))

        # 4. Aktions-Grid aktualisieren
        for btn in getattr(self, "buttons", {}).values():
            btn.setFont(button_font)

        # 5. Uhr aktualisieren
        
        # Uhr und Datum immer gleich groß und fett
        clock_date_font = QFont(
            "Segoe UI",
            max(12, int(dynamic_size * 0.9)),
            QFont.Weight.Bold,
        )

        if hasattr(self, "digital_clock"):
            self.digital_clock.setFont(clock_date_font)

        if hasattr(self, "date_label"):
            self.date_label.setFont(clock_date_font)


        # 6. Header-Labels aktualisieren
        if hasattr(self, "lang_label"):
            self.lang_label.setFont(label_font)

        if hasattr(self, "color_label"):
            self.color_label.setFont(label_font)

        if hasattr(self, "commit_label"):
            self.commit_label.setFont(label_font)

        # 7. Logo einmal nach der Layout-Aktualisierung skalieren
        if (
            hasattr(self, "logo_label")
            and getattr(self, "original_pixmap", None) is not None
        ):
            QTimer.singleShot(0, self._fit_header_logo)

        # 8. Info-Text aktualisieren
        if hasattr(self, "info_text"):
            text_font = QFont(
                "Courier", max(10, dynamic_size)
            )
            self.info_text.setFont(text_font)

        # 9. Lade-Overlay auf Fenstergröße halten
        if getattr(self, "loading_overlay", None):
            self.loading_overlay.setGeometry(self.rect())


    def open_terminal(self, **kwargs):
        """Öffnet Terminal (S3, S4 oder NCam) mit Sudo-Support, Regenbogen-Progress und Sound."""
        if hasattr(self, "hide_final_label"):
            self.hide_final_label()
        elif hasattr(self, "final_label") and self.final_label:
            self.final_label.hide()

        import subprocess, platform, shutil, os
        from PyQt6.QtWidgets import QApplication
        from PyQt6.QtCore import QTimer

        # 1. PARAMETER RAUSHOLEN & SOFORT RADIKAL BEREINIGEN
        s3_path_raw = kwargs.get("s3_path")
        use_sudo = kwargs.get("use_sudo", False)
        lang = str(getattr(self, "LANG", "de")).lower()[:2]
        is_de = lang == "de"
        pbar = getattr(self, "progress_bar", None)

        # FIX: Alle eventuell mitgelieferten Anführungszeichen sofort entfernen,
        # bevor irgendeine Pfadfunktion (dirname/basename) darauf zugreift!
        s3_path = None
        if s3_path_raw:
            s3_path = str(s3_path_raw).replace('"', '').strip()

        # Erweiterte Erkennung für S3, S4 und NCam
        s3_path_lower = s3_path.lower() if s3_path else ""
        if "ncam" in s3_path_lower:
            proj_name = "NCam"
        elif "s4" in s3_path_lower or "simplebuild" in s3_path_lower:
            proj_name = "S4"
        else:
            proj_name = "S3"

        T_LOAD = (
            (
                f"{proj_name} Menü wird gestartet..."
                if s3_path
                else "Terminal wird geöffnet..."
            )
            if is_de
            else (f"Starting {proj_name} menu..." if s3_path else "Opening terminal...")
        )
        # Das Öffnen des Menüs ist kein abgeschlossener Build. Die Anzeige
        # darf daher nicht fälschlich einen erfolgreichen Build suggerieren.
        T_DONE = "Terminal geöffnet – Build läuft extern" if is_de else "Terminal opened – build runs externally"

        # Vorabprüfung: bekannte Startdateien und Arbeitsverzeichnis validieren,
        # bevor ein externes Terminal geöffnet wird. Unter Windows wird der Pfad
        # später durch MSYS2/WSL verarbeitet; dort keine Linux-Rechteprüfung.
        if s3_path and platform.system() == "Linux":
            start_dir = os.path.dirname(s3_path) or os.getcwd()
            if not os.path.isdir(start_dir):
                raise_msg = (
                    f"Arbeitsverzeichnis nicht gefunden: {start_dir}"
                    if is_de else f"Working directory not found: {start_dir}"
                )
                if hasattr(self, "append_info") and getattr(self, "info_text", None):
                    self.append_info(self.info_text, f"❌ {raise_msg}", "error")
                if pbar:
                    pbar.setValue(0)
                    pbar.setFormat("❌ " + ("Pfadfehler" if is_de else "Path error"))
                return
            if not os.path.isfile(s3_path):
                raise_msg = (
                    f"Startdatei nicht gefunden: {s3_path}"
                    if is_de else f"Startup file not found: {s3_path}"
                )
                if hasattr(self, "append_info") and getattr(self, "info_text", None):
                    self.append_info(self.info_text, f"❌ {raise_msg}", "error")
                if pbar:
                    pbar.setValue(0)
                    pbar.setFormat("❌ " + ("Datei fehlt" if is_de else "File missing"))
                return
            if not os.access(s3_path, os.X_OK):
                raise_msg = (
                    f"Startdatei ist nicht ausführbar: {s3_path} (Tipp: chmod +x {shlex.quote(s3_path)})"
                    if is_de else f"Startup file is not executable: {s3_path} (tip: chmod +x {shlex.quote(s3_path)})"
                )
                if hasattr(self, "append_info") and getattr(self, "info_text", None):
                    self.append_info(self.info_text, f"❌ {raise_msg}", "error")
                if pbar:
                    pbar.setValue(0)
                    pbar.setFormat("❌ " + ("Keine Ausführungsrechte" if is_de else "Not executable"))
                return

        if "safe_play" in globals():
            safe_play("service-login.oga")

        # 2. PROGRESSBAR STYLE (REGENBOGEN)
        if pbar:
            rainbow = "qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0.0 #FF0000, stop:0.2 #FF7F00, stop:0.4 #FFFF00, stop:0.6 #00FF00, stop:0.8 #0000FF, stop:1.0 #8B00FF);"
            pbar.setStyleSheet(
                f"QProgressBar {{ text-align: center; font-weight: 700; border: 2px solid #222; border-radius: 6px; background-color: #111; color: black; font-size: 15pt; }} QProgressBar::chunk {{ background-color: {rainbow}; border-radius: 4px; }}"
            )
            pbar.setFormat(f"💻 {T_LOAD} %p%")
            pbar.setValue(20)
            pbar.show()
            QApplication.processEvents()

        
        build_log_path = None
        try:
            # 3. BEFEHL ZUSAMMENBAUEN
            system = platform.system()
            terminal_opened = False
            exec_cmd = ""
            is_s4_script = proj_name == "S4"

            if s3_path:
                s3_dir = os.path.dirname(s3_path)
                exe_name = os.path.basename(s3_path)

                s3_cmd = f"./{exe_name} menu"
                if is_s4_script:
                    s3_cmd = f"./{exe_name}"
                if use_sudo and system == "Linux":
                    s3_cmd = f"sudo {s3_cmd}"

                if system == "Windows":
                    from windows_msys2_setup import bash_environment

                    msys2_root = os.environ.get("MSYS2_ROOT", r"C:\msys64")
                    bash_candidates = [
                        os.environ.get("MSYS2_BASH"),
                        os.path.join(msys2_root, "usr", "bin", "bash.exe"),
                        r"C:\msys64\usr\bin\bash.exe",
                        r"C:\tools\msys64\usr\bin\bash.exe",
                    ]
                    msys2_bash = next(
                        (
                            os.path.normpath(candidate)
                            for candidate in bash_candidates
                            if candidate and os.path.isfile(candidate)
                        ),
                        None,
                    )

                    if not msys2_bash:
                        raise FileNotFoundError(
                            "MSYS2 Bash nicht gefunden. Bitte MSYS2_ROOT "
                            "oder MSYS2_BASH prüfen."
                        )

                    s3_dir_bash = os.path.normpath(s3_dir).replace("\\", "/")
                    s3_dir_bash_q = "'" + s3_dir_bash.replace("'", "'\\''") + "'"
                    exe_name_q = "'" + exe_name.replace("'", "'\\''") + "'"

                    # Auch beim Start unter Windows/MSYS2 Logs im Startordner
                    # des Patch Managers anlegen. Zuvor wurde nur der Linux-
                    # Terminalzweig protokolliert, weshalb hier kein Ordner entstand.
                    log_dir = getattr(self, "build_log_dir", None) or os.path.join(os.path.dirname(os.path.abspath(__file__)), "build_logs")
                    os.makedirs(log_dir, exist_ok=True)
                    log_stamp = QDateTime.currentDateTime().toString("yyyyMMdd_HHmmss")
                    build_log_path = os.path.abspath(os.path.join(
                        log_dir, f"{proj_name.lower()}_{log_stamp}.log"
                    ))
                    # Datei sofort anlegen, damit auch dann ein sichtbares Log
                    # existiert, wenn MSYS2 kein `script`-Programm mitbringt.
                    with open(build_log_path, "w", encoding="utf-8", errors="replace") as log_file:
                        log_file.write(
                            f"{proj_name} Build-Log\n"
                            f"Start: {QDateTime.currentDateTime().toString('yyyy-MM-dd HH:mm:ss')}\n"
                            f"Arbeitsverzeichnis: {s3_dir}\n"
                            "=" * 72 + "\n"
                        )
                    log_path_bash = build_log_path.replace("\\", "/")
                    log_path_bash_q = "'" + log_path_bash.replace("'", "'\\''") + "'"
                    quoted_s3_cmd = "'" + s3_cmd.replace("'", "'\\''") + "'"
                    run_cmd = f"cd {s3_dir_bash_q} && {s3_cmd}"
                    quoted_run_cmd = "'" + run_cmd.replace("'", "'\\''") + "'"
                    bash_cmd = (
                        "export MSYSTEM=UCRT64; "
                        "export PATH=/ucrt64/bin:/usr/bin:/bin; "
                        f"cd {s3_dir_bash_q} || exit 2; "
                        "if command -v script >/dev/null 2>&1; then "
                        f"script -q -e -c {quoted_run_cmd} {log_path_bash_q}; result=$?; "
                        "else "
                        f"echo 'Hinweis: util-linux script nicht gefunden; kein Log-Mitschnitt.'; "
                        f"echo 'FEHLER: MSYS2-Programm script fehlt; Build-Ausgabe wird nicht mitgeschnitten.' >> {log_path_bash_q}; "
                        f"{s3_cmd}; result=$?; "
                        "fi; "
                        'echo; echo "Simplebuild beendet (Exit-Code: $result)"; '
                        f'echo "Protokoll: {log_path_bash}"; '
                        "exec bash -i"
                    )

                    subprocess.Popen(
                        [msys2_bash, "--login", "-i", "-c", bash_cmd],
                        env=bash_environment(),
                        creationflags=subprocess.CREATE_NEW_CONSOLE,
                    )
                    terminal_opened = True

                elif system == "Linux":
                    # Shell-Befehl als Argumente aufbauen, nicht als
                    # zusätzlich verschachtelten, gequoteten Befehl.
                    exec_cmd = (
                        f"cd {shlex.quote(s3_dir)} && "
                        f"{s3_cmd}"
                    )

            elif system == "Windows":
                subprocess.Popen(
                    ["cmd"],
                    creationflags=subprocess.CREATE_NEW_CONSOLE,
                )
                terminal_opened = True

            if system == "Linux" and not terminal_opened:
                terminals = [
                    ("x-terminal-emulator", "debian"),
                    ("gnome-terminal", "gnome"),
                    ("konsole", "kde"),
                    ("xfce4-terminal", "xfce"),
                    ("mate-terminal", "mate"),
                    ("lxterminal", "lx"),
                    ("xterm", "xterm"),
                    ("kgx", "gnome"),
                    ("ptyxis", "gnome"),
                    ("tilix", "tilix"),
                    ("kitty", "kitty"),
                    ("alacritty", "alacritty"),
                    ("wezterm", "wezterm"),
                    ("foot", "foot"),
                ]

                terminal_errors = []

                for term, kind in terminals:
                    terminal_path = shutil.which(term)
                    if not terminal_path:
                        continue

                    # Persistentes Terminalprotokoll: `script` erhält ein echtes
                    # Pseudo-TTY, damit die interaktiven SimpleBuild-Menüs weiterhin
                    # Eingaben annehmen können. Falls `script` fehlt, bleibt das
                    # bisherige Startverhalten unverändert.
                    # Protokolle immer im Verzeichnis ablegen, aus dem der
                    # Patch Manager gestartet wurde (nicht im Home-Cache).
                    log_dir = getattr(self, "build_log_dir", None) or os.path.join(os.path.dirname(os.path.abspath(__file__)), "build_logs")
                    os.makedirs(log_dir, exist_ok=True)
                    log_stamp = QDateTime.currentDateTime().toString("yyyyMMdd_HHmmss")
                    build_log_path = os.path.abspath(os.path.join(
                        log_dir, f"{proj_name.lower()}_{log_stamp}.log"
                    ))
                    with open(build_log_path, "w", encoding="utf-8", errors="replace") as log_file:
                        log_file.write(
                            f"{proj_name} Build-Log\n"
                            f"Start: {QDateTime.currentDateTime().toString('yyyy-MM-dd HH:mm:ss')}\n"
                            f"Arbeitsverzeichnis: {s3_dir}\n"
                            "=" * 72 + "\n"
                        )
                    quoted_log_path = shlex.quote(build_log_path)
                    quoted_exec_cmd = shlex.quote(exec_cmd)
                    shell_command = (
                        f"cd {shlex.quote(s3_dir)} && "
                        "if command -v script >/dev/null 2>&1; then "
                        f"script -q -e -c {quoted_exec_cmd} {quoted_log_path}; result=$?; "
                        "else "
                        f"{exec_cmd}; result=$?; "
                        "echo 'Hinweis: util-linux script nicht gefunden; kein Log-Mitschnitt.'; "
                        f"echo 'FEHLER: util-linux script fehlt; Build-Ausgabe wird nicht mitgeschnitten.' >> {quoted_log_path}; "
                        "fi; "
                        f"echo; echo \"Simplebuild beendet (Exit-Code: $result)\"; "
                        f"echo \"Protokoll: {build_log_path}\"; "
                        "exec bash"
                    )

                    if kind == "debian":
                        args = [
                            terminal_path, "-e", "bash", "-lc",
                            shell_command,
                        ]
                    elif kind in ("gnome", "mate", "kde"):
                        args = [
                            terminal_path, "--", "bash", "-lc",
                            shell_command,
                        ]
                    elif kind == "xfce":
                        args = [
                            terminal_path, "--command",
                            f"bash -lc {shlex.quote(shell_command)}",
                        ]
                    elif kind == "lx":
                        args = [
                            terminal_path, "-e",
                            f"bash -lc {shlex.quote(shell_command)}",
                        ]
                    elif kind == "xterm":
                        args = [
                            terminal_path, "-e", "bash", "-lc",
                            shell_command,
                        ]
                    elif kind == "tilix":
                        args = [
                            terminal_path, "-e",
                            f"bash -lc {shlex.quote(shell_command)}",
                        ]
                    elif kind == "kitty":
                        args = [terminal_path, "bash", "-lc", shell_command]
                    elif kind == "alacritty":
                        args = [
                            terminal_path, "-e", "bash", "-lc",
                            shell_command,
                        ]
                    elif kind == "wezterm":
                        args = [
                            terminal_path, "start", "--",
                            "bash", "-lc", shell_command,
                        ]
                    elif kind == "foot":
                        args = [terminal_path, "bash", "-lc", shell_command]
                    else:
                        args = [
                            terminal_path, "-e", "bash", "-lc",
                            shell_command,
                        ]

                    try:
                        subprocess.Popen(args, start_new_session=True)
                        terminal_opened = True
                        break
                    except OSError as exc:
                        terminal_errors.append(f"{term}: {exc}")

                if not terminal_opened:
                    details = "\n".join(terminal_errors)
                    raise RuntimeError(
                        "Kein Terminal-Emulator gefunden oder gestartet."
                        + (f"\n{details}" if details else "")
                    )

            if "safe_play" in globals():
                safe_play("complete.oga")
            if pbar:
                # 100 % bedeutet hier nur: Terminalstart abgeschlossen.
                # Der eigentliche Build läuft im externen Terminal.
                pbar.setValue(100)
                pbar.setFormat(f"🖥️ {T_DONE}")
            if hasattr(self, "append_info") and hasattr(self, "info_text"):
                self.append_info(
                    self.info_text,
                    (
                        f"🖥️ [{QTime.currentTime().toString('HH:mm:ss')}] "
                        f"{proj_name}: Terminal geöffnet. Arbeitsverzeichnis: "
                        f"{os.path.dirname(s3_path) if s3_path else os.getcwd()}. "
                        f"Build-Ausgabe und Exit-Code erscheinen im externen Terminal. "
                        + (f"Protokoll: {build_log_path}. " if build_log_path else "")
                        + "Dieser Status bestätigt nur den Terminalstart."
                    )
                    if is_de else
                    (
                        f"🖥️ [{QTime.currentTime().toString('HH:mm:ss')}] "
                        f"{proj_name}: terminal opened. Working directory: "
                        f"{os.path.dirname(s3_path) if s3_path else os.getcwd()}. "
                        f"Build output and exit code appear in the external terminal. "
                        + (f"Log: {build_log_path}. " if build_log_path else "")
                        + "This status confirms only that the terminal started."
                    ),
                    "info",
                )

        except Exception as e:
            if "safe_play" in globals():
                safe_play("dialog-error.oga")
            if pbar:
                pbar.setFormat("❌ Error!")
            if hasattr(self, "append_info"):
                self.append_info(
                    self.info_text,
                    f"❌ Fehler: {str(e)}",
                    "error",
                )


            # 5. RESET NACH 3 SEKUNDEN
            if pbar:
                QTimer.singleShot(
                    3000,
                    (
                        self.pbar_idle
                        if hasattr(self, "pbar_idle")
                        else lambda: pbar.setValue(0)
                    ),
                )


    def analyze_latest_build_log(self):
        """Crash-safe entry point for the BUILD-LOG analysis button."""
        try:
            return self._analyze_latest_build_log_impl()
        except Exception as exc:
            # Exceptions escaping a PyQt button slot can terminate the whole GUI.
            # Keep this diagnostic action isolated and report the failure in the console.
            import traceback
            is_de = str(getattr(self, "LANG", "de")).lower().startswith("de")
            message = (
                f"BUILD-LOG-Analyse abgebrochen; die GUI bleibt geöffnet. Details: {exc}"
                if is_de else
                f"BUILD LOG analysis stopped; the GUI will remain open. Details: {exc}"
            )
            try:
                info = getattr(self, "info_text", None)
                if info is not None and hasattr(self, "append_info"):
                    self.append_info(info, message, "error")
                else:
                    print(message)
                print(traceback.format_exc())
            except Exception:
                print(message)
                traceback.print_exc()
            return None

    def _analyze_latest_build_log_impl(self):
        """Analyze recent build logs with project, completion and error indicators."""
        import os
        import re
        from datetime import datetime
        from PyQt6.QtCore import QDateTime
        from PyQt6.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QLabel, QTextEdit, QPushButton, QMessageBox

        is_de = str(getattr(self, "LANG", "de")).lower().startswith("de")

        def show_analysis_window(window_title, body, severity="info"):
            """Display analysis results in a dedicated, read-only window."""
            result_dialog = QDialog(self)
            result_dialog.setWindowTitle(window_title)
            result_dialog.resize(900, 650)
            result_layout = QVBoxLayout(result_dialog)
            heading = QLabel(window_title, result_dialog)
            heading.setWordWrap(True)
            result_layout.addWidget(heading)
            result_text = QTextEdit(result_dialog)
            result_text.setReadOnly(True)
            result_text.setLineWrapMode(QTextEdit.LineWrapMode.NoWrap)
            result_text.setPlainText(str(body))
            result_layout.addWidget(result_text, 1)
            button_row = QHBoxLayout()
            copy_button = QPushButton("Text kopieren" if is_de else "Copy text", result_dialog)
            copy_button.clicked.connect(lambda: QApplication.clipboard().setText(result_text.toPlainText()))
            close_button = QPushButton("Schließen" if is_de else "Close", result_dialog)
            close_button.clicked.connect(result_dialog.accept)
            button_row.addWidget(copy_button)
            button_row.addStretch(1)
            button_row.addWidget(close_button)
            result_layout.addLayout(button_row)
            result_dialog.setModal(True)
            result_dialog.exec()

        log_dir = getattr(self, "build_log_dir", None) or os.path.join(os.path.dirname(os.path.abspath(__file__)), "build_logs")
        try:
            candidates = [
                os.path.join(log_dir, name)
                for name in os.listdir(log_dir)
                if name.lower().endswith(".log") and os.path.isfile(os.path.join(log_dir, name))
            ]
        except OSError as exc:
            message = (f"Build-Log-Ordner kann nicht gelesen werden: {exc}" if is_de else f"Cannot read build-log directory: {exc}")
            show_analysis_window("BUILD-LOG-ANALYSE" if is_de else "BUILD LOG ANALYSIS", message, "error")
            return

        if not candidates:
            message = (f"Keine Build-Logs gefunden. Erwarteter Ordner: {log_dir}" if is_de
                       else f"No build logs found. Expected folder: {log_dir}")
            show_analysis_window("BUILD-LOG-ANALYSE" if is_de else "BUILD LOG ANALYSIS", message, "warning")
            return

        candidates.sort(key=os.path.getmtime, reverse=True)
        latest_path = candidates[0]
        try:
            with open(latest_path, "r", encoding="utf-8", errors="replace") as handle:
                content = handle.read()
            lines = content.splitlines()
        except OSError as exc:
            message = (f"Log konnte nicht gelesen werden: {exc}" if is_de else f"Could not read log: {exc}")
            show_analysis_window("BUILD-LOG-ANALYSE" if is_de else "BUILD LOG ANALYSIS", message, "error")
            return

        error_pattern = re.compile(
            r"\b(error:|fatal error:|undefined reference|no such file or directory|"
            r"command not found|cannot find|failed|failure|error\s*\[|make\[[0-9]+\]: \*\*\*)",
            re.IGNORECASE,
        )
        warning_pattern = re.compile(r"\bwarning:|\bdeprecated\b", re.IGNORECASE)
        errors = [(i + 1, line.strip()) for i, line in enumerate(lines) if error_pattern.search(line)]
        warnings = [(i + 1, line.strip()) for i, line in enumerate(lines) if warning_pattern.search(line)]

        exit_matches = re.findall(r"(?:Exit-Code|exit code):\s*(-?\d+)", content, re.IGNORECASE)
        exit_code = exit_matches[-1] if exit_matches else None
        finished_match = re.search(r"SIMPLEBUILD_FINISHED:exit=(-?\d+)", content, re.IGNORECASE)
        started_marker = "SIMPLEBUILD_STARTED" in content
        finished_marker = finished_match is not None
        if finished_marker:
            status_code = finished_match.group(1)
            status = (("ERFOLGREICH" if status_code == "0" else "FEHLGESCHLAGEN") if is_de
                      else ("SUCCESSFUL" if status_code == "0" else "FAILED"))
        elif started_marker:
            status = "GESTARTET – Abschlussmarker fehlt" if is_de else "STARTED – completion marker missing"
        elif exit_code is not None:
            status = (("ERFOLGREICH (Exit-Code 0)" if exit_code == "0" else f"FEHLGESCHLAGEN (Exit-Code {exit_code})") if is_de
                      else ("SUCCESSFUL (exit code 0)" if exit_code == "0" else f"FAILED (exit code {exit_code})"))
        elif errors:
            status = "FEHLERHINWEISE GEFUNDEN – Ergebnis nicht bestätigt" if is_de else "ERROR INDICATORS FOUND – outcome not confirmed"
        else:
            status = "UNBEKANNT – kein eindeutiger Abschlussmarker" if is_de else "UNKNOWN – no definitive completion marker"

        # Extract a likely project label from the log header or filename; never infer success from the name.
        project_match = re.search(r"^\s*(.+?)\s+Build-Log\s*$", content, re.IGNORECASE | re.MULTILINE)
        project = project_match.group(1).strip() if project_match else os.path.basename(latest_path).rsplit("_", 1)[0]
        start_match = re.search(r"^\s*Start:\s*(.+)$", content, re.IGNORECASE | re.MULTILINE)
        finish_match = re.search(r"^.*(?:Simplebuild beendet|Ergebnis):.*$", content, re.IGNORECASE | re.MULTILINE)

        summary = [
            "=" * 54,
            (f"BUILD-LOG-Analyse: {os.path.basename(latest_path)}" if is_de else f"BUILD LOG ANALYSIS: {os.path.basename(latest_path)}"),
            (f"Projekt: {project}" if is_de else f"Project: {project}"),
            (f"Status: {status}" if is_de else f"Status: {status}"),
            (f"Analysiert am: {QDateTime.currentDateTime().toString('yyyy-MM-dd HH:mm:ss')}" if is_de else f"Analyzed at: {QDateTime.currentDateTime().toString('yyyy-MM-dd HH:mm:ss')}"),
            (f"Log geändert: {datetime.fromtimestamp(os.path.getmtime(latest_path)).astimezone().isoformat(timespec='seconds')}" if is_de else f"Log modified: {datetime.fromtimestamp(os.path.getmtime(latest_path)).astimezone().isoformat(timespec='seconds')}"),
        ]
        if start_match:
            summary.append((f"Startzeit: {start_match.group(1).strip()}" if is_de else f"Start time: {start_match.group(1).strip()}"))
        if finish_match:
            summary.append((f"Abschlusszeile: {finish_match.group(0).strip()}" if is_de else f"Completion line: {finish_match.group(0).strip()}"))
        summary.append((f"Fehler-Treffer: {len(errors)} | Warnungen: {len(warnings)}" if is_de else f"Error matches: {len(errors)} | Warnings: {len(warnings)}"))
        if exit_code is not None:
            summary.append(f"Exit-Code: {exit_code}")
        if errors:
            summary.append("--- Fehler ---" if is_de else "--- Errors ---")
            summary.extend(f"Zeile {number}: {line}" for number, line in errors[:25])
        if warnings:
            summary.append("--- Warnungen (max. 15) ---" if is_de else "--- Warnings (max. 15) ---")
            summary.extend(f"Zeile {number}: {line}" for number, line in warnings[:15])
        if not errors and not warnings:
            summary.append("Keine typischen Fehler-/Warnungsmuster gefunden; das ist kein sicherer Erfolgsnachweis." if is_de
                           else "No common error/warning patterns found; this does not guarantee success.")
        summary.append((f"Vollständiges Log: {latest_path}" if is_de else f"Full log: {latest_path}"))

        # Compact overview of the next four newest logs helps compare projects without opening files manually.
        if len(candidates) > 1:
            summary.append("--- Weitere aktuelle Logs ---" if is_de else "--- Other recent logs ---")
            for path in candidates[1:5]:
                try:
                    with open(path, "r", encoding="utf-8", errors="replace") as handle:
                        sample = handle.read()
                    marker = re.search(r"SIMPLEBUILD_FINISHED:exit=(-?\d+)", sample, re.IGNORECASE)
                    exit_found = re.findall(r"(?:Exit-Code|exit code):\s*(-?\d+)", sample, re.IGNORECASE)
                    if marker:
                        state = (("Erfolg" if marker.group(1) == "0" else "Fehler") if is_de else ("Success" if marker.group(1) == "0" else "Failed"))
                    elif "SIMPLEBUILD_STARTED" in sample:
                        state = "Abschluss fehlt" if is_de else "Completion missing"
                    elif exit_found:
                        state = (("Erfolg" if exit_found[-1] == "0" else "Fehler") if is_de else ("Success" if exit_found[-1] == "0" else "Failed"))
                    else:
                        state = "Unbekannt" if is_de else "Unknown"
                    summary.append(f"{os.path.basename(path)} — {state}")
                except OSError:
                    summary.append(f"{os.path.basename(path)} — " + ("nicht lesbar" if is_de else "unreadable"))
        report_text = "\n".join(summary)
        show_analysis_window(
            (f"BUILD-LOG-ANALYSE – {os.path.basename(latest_path)}" if is_de else f"BUILD LOG ANALYSIS – {os.path.basename(latest_path)}"),
            report_text,
            "error" if errors or (exit_code is not None and exit_code != "0") else ("warning" if "UNKNOWN" in status.upper() or "UNBEKANNT" in status.upper() or warnings else "info"),
        )

    def select_patch_path(self):
        """Öffnet Verzeichnis-Dialog mit Regenbogen-Progress, Sound und Auto-Reset zu Idle."""

        # --- Final-Label ausblenden ---
        if hasattr(self, "hide_final_label"):
            self.hide_final_label()
        elif hasattr(self, "final_label") and self.final_label:
            self.final_label.hide()
        import os
        from PyQt6.QtWidgets import QFileDialog, QApplication
        from PyQt6.QtCore import QTimer

        # 1. SETUP & SPRACHE
        lang = str(getattr(self, "LANG", "de")).lower()[:2]
        is_de = lang == "de"
        pbar = getattr(self, "progress_bar", None)

        # Lokalisierte Texte
        T_TITLE = "Patch-Ordner auswählen" if is_de else "Select Patch Folder"
        T_LOAD = "Ordner wird gewählt..." if is_de else "Selecting folder..."
        T_DONE = "Pfad gespeichert!" if is_de else "Path saved!"

        # --- SOUND START ---
        if "safe_play" in globals():
            safe_play("service-login.oga")

        # 2. REGENBOGEN-PROGRESS AKTIVIEREN
        if pbar:
            rainbow = (
                "qlineargradient(x1:0, y1:0, x2:1, y2:0, "
                "stop:0.0 #FF0000, stop:0.2 #FF7F00, stop:0.4 #FFFF00, "
                "stop:0.6 #00FF00, stop:0.8 #0000FF, stop:1.0 #8B00FF);"
            )
            pbar.setStyleSheet(
                f"""
                QProgressBar {{
                    text-align: center; font-weight: 900; border: 2px solid #222;
                    border-radius: 8px; background-color: #111; color: black; font-size: 14pt;
                }}
                QProgressBar::chunk {{ background: {rainbow}; border-radius: 6px; }}
            """
            )
            pbar.setFormat(f"📁 {T_LOAD}")
            pbar.setValue(20)
            pbar.show()
            QApplication.processEvents()

        # 3. DIALOG ÖFFNEN
        start_path = getattr(self, "OLD_PATCH_DIR", os.getcwd())
        directory = QFileDialog.getExistingDirectory(self, T_TITLE, start_path)

        if directory:
            directory = os.path.normpath(directory)

            # --- INTERNE WERTE UPDATEN ---
            # Wir nutzen save_config, um die Datei sicher zu schreiben
            new_cfg_data = {"s3_patch_path": directory, "old_patch_dir": directory}

            # Falls save_config existiert, nutzen wir sie (triggert auch Sound/Log)
            if "save_config" in globals():
                save_config(new_cfg_data, gui_instance=self)
            else:
                # Fallback falls save_config nicht global erreichbar
                self.cfg.update(new_cfg_data)
                self.OLD_PATCH_DIR = directory

            # Pfade für die App-Logik setzen
            self.OLD_PATCH_FILE = os.path.join(directory, "oscam-emu.patch")
            self.ALT_PATCH_FILE = os.path.join(directory, "oscam-emu.altpatch")

            if pbar:
                pbar.setValue(100)
                pbar.setFormat(f"✅ {T_DONE}")

            if "safe_play" in globals():
                safe_play("complete.oga")

            if hasattr(self, "log_message"):
                msg = (
                    f"📁 Neuer Patch-Pfad: {directory}"
                    if is_de
                    else f"📁 New Patch Path: {directory}"
                )
                self.log_message(
                    f"<span style='color:#FFD700; font-weight:bold;'>{msg}</span>"
                )
        else:
            # Abgebrochen -> Kurzes Feedback
            if pbar:
                pbar.setValue(0)
                pbar.setFormat("❌" if is_de else "❌")

        # 4. AUTO-RESET ZU DEINEM NEUEN IDLE (mit den 5 Blinks)
        # Wir warten 2 Sekunden, damit der User das "Pfad gespeichert" lesen kann
        if hasattr(self, "pbar_idle"):
            QTimer.singleShot(2500, self.pbar_idle)

    
    
    def get_backup_excluded_dirs(self, project_dir):
        """Ermittelt geschützte Projektordner aus config.json."""
        import json
        from pathlib import Path

        project_dir = Path(project_dir).resolve()

        excluded_dirs = {
            "backup",
            "oscam-emu-git",
            "streamboard-git",
            "__pycache__",
        }

        config_file = project_dir / "config.json"

        try:
            with config_file.open("r", encoding="utf-8") as file:
                config = json.load(file)

            for key in (
                "s3_custom_path",
                "ncam_custom_path",
                "s4_custom_path",
            ):
                value = config.get(key)

                if not isinstance(value, str) or not value.strip():
                    continue

                custom_path = Path(value).resolve()

                # Nur Ordner ausschließen, die innerhalb des
                # Projektordners liegen, nicht externe Verzeichnisse.
                try:
                    relative = custom_path.relative_to(project_dir)
                except ValueError:
                    continue

                if relative.parts:
                    excluded_dirs.add(relative.parts[0])

        except (OSError, ValueError, TypeError) as exc:
            print(f"[BACKUP] config.json konnte nicht gelesen werden: {exc}")

        return excluded_dirs

    
  
    def create_gi_backup(self):
        """Erstellt ein geprüftes Backup mit Linux-Dateirechten.

        Schließt geschützte Ordner und Symlinks aus.
        Das bestehende Backup bleibt bis zum erfolgreichen Abschluss erhalten.
        """

        import os
        import json
        import stat
        import shutil
        import tempfile
        import zipfile

        from pathlib import Path
        from PyQt6.QtWidgets import QMessageBox

        project_dir = Path(__file__).resolve().parent.resolve()
        backup_dir = project_dir / "backup"
        backup_zip = backup_dir / "backup_git.zip"
        config_file = project_dir / "config.json"

        excluded_dirs = {
            "backup",
            ".git",
            "oscam-emu-git",
            "streamboard-git",
            "__pycache__",
            ".venv",
            "venv",
        }

        excluded_names = {name.casefold() for name in excluded_dirs}
        def is_excluded_dir(dirname):
            name = dirname.casefold()

            return (
                name in excluded_names
                or "s3" in name
                or "s4" in name
            )
        def norm(path):
            return os.path.normcase(os.path.abspath(str(path)))

        def is_inside(path, parent):
            try:
                return os.path.commonpath(
                    [norm(path), norm(parent)]
                ) == norm(parent)
            except (ValueError, OSError):
                return False

        def add_to_archive(archive, source, arcname):
            """Schreibt Dateien mit Unix-Modus und ohne Symlink-Verfolgung."""
            source = Path(source)

            if source.is_symlink():
                return False

            file_stat = source.stat(follow_symlinks=False)

            if not (stat.S_ISREG(file_stat.st_mode)
                    or stat.S_ISDIR(file_stat.st_mode)):
                return False

            if stat.S_ISDIR(file_stat.st_mode):
                arcname = arcname.rstrip("/") + "/"

            info = zipfile.ZipInfo.from_file(
                source,
                arcname=arcname,
            )
            info.create_system = 3  # Unix
            info.external_attr = (file_stat.st_mode & 0xFFFF) << 16

            if stat.S_ISDIR(file_stat.st_mode):
                info.external_attr |= 0x10
                archive.writestr(info, b"")
            else:
                info.compress_type = zipfile.ZIP_DEFLATED
                with source.open("rb") as src:
                    with archive.open(info, "w") as dst:
                        shutil.copyfileobj(src, dst)

            return True

        temp_zip = None

        try:
            # --------------------------------------------------
            # 1. Konfiguration prüfen
            # --------------------------------------------------
            with config_file.open("r", encoding="utf-8") as file:
                config = json.load(file)

            if not isinstance(config, dict):
                raise ValueError(
                    "config.json enthält kein JSON-Objekt."
                )

            custom_paths = []

            for key in (
                "s3_custom_path",
                "ncam_custom_path",
                "s4_custom_path",
            ):
                value = config.get(key)

                if not isinstance(value, str) or not value.strip():
                    raise ValueError(
                        f"Der Konfigurationswert '{key}' fehlt "
                        "oder ist ungültig."
                    )

                custom_path = Path(
                    value.strip()
                ).expanduser()

                if not custom_path.is_absolute():
                    custom_path = project_dir / custom_path

                custom_path = custom_path.resolve()

                if not is_inside(custom_path, project_dir):
                    raise ValueError(
                        "Der konfigurierte Pfad liegt außerhalb "
                        f"des Projekts: {custom_path}"
                    )

                if norm(custom_path) == norm(project_dir):
                    raise ValueError(
                        f"'{key}' verweist auf das gesamte "
                        "Projektverzeichnis."
                    )

                if norm(custom_path) not in {
                    norm(path) for path in custom_paths
                }:
                    custom_paths.append(custom_path)

            if len(custom_paths) != 3:
                raise ValueError(
                    "Die drei konfigurierten Ordner müssen "
                    "eindeutig sein."
                )

            # --------------------------------------------------
            # 2. Eindeutiges temporäres Archiv erstellen
            # --------------------------------------------------
            backup_dir.mkdir(parents=True, exist_ok=True)

            fd, temp_name = tempfile.mkstemp(
                prefix="backup_git.",
                suffix=".tmp.zip",
                dir=backup_dir,
            )
            os.close(fd)
            temp_zip = Path(temp_name)

            file_count = 0
            dir_count = 0

            # --------------------------------------------------
            # 3. Projektdateien archivieren
            # --------------------------------------------------
            with zipfile.ZipFile(
                temp_zip,
                mode="w",
                compression=zipfile.ZIP_DEFLATED,
            ) as archive:

                for root, dirs, files in os.walk(
                    project_dir,
                    topdown=True,
                    followlinks=False,
                ):
                    root_path = Path(root)
                    filtered_dirs = []

                    for dirname in dirs:
                        candidate = root_path / dirname

                        # s3-/s4-Ordner und geschützte Standardordner ausschließen.
                        if is_excluded_dir(dirname):
                            continue

                        if any(
                            norm(candidate) == norm(custom_path)
                            or is_inside(candidate, custom_path)
                            for custom_path in custom_paths
                        ):
                            continue

                        if candidate.is_symlink():
                            continue

                        filtered_dirs.append(dirname)

                    # Verzeichnisliste vor dem Abstieg einschränken.
                    dirs[:] = filtered_dirs

                    # Verzeichnismetadaten einschließlich Modus sichern.
                    for dirname in filtered_dirs:
                        candidate = root_path / dirname

                        relative_path = candidate.relative_to(
                            project_dir
                        ).as_posix()

                        if add_to_archive(
                            archive, candidate, relative_path
                        ):
                            dir_count += 1

                    for filename in files:
                        source_file = root_path / filename

                        # Symlinks und spezielle Dateitypen überspringen.
                        if source_file.is_symlink():
                            continue

                        if not is_inside(source_file, project_dir):
                            continue

                        if any(
                            is_inside(source_file, custom_path)
                            for custom_path in custom_paths
                        ):
                            continue

                        relative_path = source_file.relative_to(
                            project_dir
                        )
                        relative_parts = relative_path.parts

                        if any(
                            part.casefold() in excluded_names
                            for part in relative_parts
                        ):
                            continue

                        # Backup-Dateien niemals selbst archivieren.
                        if (
                            norm(source_file) == norm(backup_zip)
                            or source_file == temp_zip
                        ):
                            continue

                        if add_to_archive(
                            archive,
                            source_file,
                            relative_path.as_posix(),
                        ):
                            file_count += 1

            # --------------------------------------------------
            # 4. Archiv und Dateirechte überprüfen
            # --------------------------------------------------
            with zipfile.ZipFile(temp_zip, "r") as archive:
                bad_file = archive.testzip()

                if bad_file is not None:
                    raise RuntimeError(
                        f"ZIP-Prüfung fehlgeschlagen: {bad_file}"
                    )

                for info in archive.infolist():
                    # Alle Einträge müssen Unix-Modi enthalten.
                    if info.create_system != 3:
                        raise RuntimeError(
                            "Unix-Dateimetadaten fehlen: "
                            f"{info.filename}"
                        )

                    mode = (info.external_attr >> 16) & 0xFFFF

                    if not mode:
                        raise RuntimeError(
                            "Dateirechte fehlen: "
                            f"{info.filename}"
                        )

            # --------------------------------------------------
            # 5. Erst jetzt das bisherige Backup ersetzen
            # --------------------------------------------------
            os.replace(temp_zip, backup_zip)
            temp_zip = None

            QMessageBox.information(
                self,
                "Backup erfolgreich",
                f"Das Backup wurde erstellt und geprüft:\n"
                f"{backup_zip}\n\n"
                f"Dateien: {file_count}\n"
                f"Verzeichnisse: {dir_count}\n"
                "Linux-Dateirechte: gespeichert\n"
                "Symlinks: übersprungen",
            )

            if hasattr(self, "log_message"):
                self.log_message(
                    f"Backup erstellt: {backup_zip} "
                    f"({file_count} Dateien, {dir_count} Verzeichnisse)"
                )

            return True

        except Exception as exc:
            # Nur das neue temporäre Archiv entfernen.
            # Das bisherige Backup wird nicht absichtlich verändert.
            if temp_zip is not None:
                try:
                    temp_zip.unlink(missing_ok=True)
                except OSError:
                    pass

            QMessageBox.critical(
                self,
                "Backup fehlgeschlagen",
                "Das Backup konnte nicht sicher erstellt werden:\n"
                f"{exc}\n\n"
                "Das bisherige Backup wurde nicht absichtlich "
                "ersetzt.",
            )

            if hasattr(self, "log_message"):
                self.log_message(f"Backup-Fehler: {exc}")

            return False



    
    def _has_project_write_access(self, project_dir):
        """Prüft, ob der aktuelle Prozess im Projektordner schreiben darf."""
        import os
        import tempfile
        from pathlib import Path

        project_dir = Path(project_dir)

        try:
            with tempfile.NamedTemporaryFile(
                prefix=".write_test_",
                dir=str(project_dir),
                delete=True,
            ):
                pass
            return True
        except (OSError, PermissionError):
            return False

    
    
    def plugin_update_action(self, latest_version=None, progress_callback=None):
        """Aktualisiert das Projekt aus GitHub und schützt lokale Daten."""

        import json
        import os
        import sys
        import time
        import shutil
        import tempfile
        import zipfile
        import subprocess

        from pathlib import Path, PurePosixPath
        from io import BytesIO

        import requests

        from PyQt6.QtCore import QTime
        from PyQt6.QtWidgets import QMessageBox, QApplication

        project_dir = Path(__file__).resolve().parent.resolve()

        # --------------------------------------------------
        # 1. Grundeinstellungen und geschützte Ordner
        # --------------------------------------------------
        protected_dirs = {
            "backup",
            ".git",
            "oscam-emu-git",
            "streamboard-git",
            "__pycache__",
            ".venv",
            "venv",
        }

        protected_files = {
            "Noto_Color_Emoji.zip",
            "oscam_emu_toolkit2.png",
            "oscam-patch-generator.png",
            "speedy_oscam_managert.png",
            "final-oscam-emu-patch-maker.png",
            "final-englisch.png",
            "speedy_banner.png",
            "README.md",
            "version.txt",
            "config.json",
            "settings.json",
            "github_config.json",
            "install_counter.txt",
            "oscam_rev.txt",
        }

        config_file = project_dir / "config.json"
        custom_keys = (
            "s3_custom_path",
            "ncam_custom_path",
            "s4_custom_path",
        )

        # Einheitliche Pfadbehandlung, insbesondere unter Windows.
        def normalized_path(path):
            return os.path.normcase(
                os.path.abspath(os.fspath(path))
            )

        project_norm = normalized_path(project_dir)

        def is_inside(path, parent):
            """True, wenn path gleich parent ist oder darunter liegt."""
            try:
                return os.path.commonpath(
                    [normalized_path(path), normalized_path(parent)]
                ) == normalized_path(parent)
            except (ValueError, OSError):
                return False

        # --------------------------------------------------
        # 2. Konfigurierte Ordner sicher ermitteln
        # --------------------------------------------------
        protected_custom_paths = []

        try:
            with config_file.open("r", encoding="utf-8") as file:
                config = json.load(file)

            if not isinstance(config, dict):
                raise ValueError(
                    "config.json enthält kein gültiges JSON-Objekt."
                )

            for key in custom_keys:
                value = config.get(key)

                if not isinstance(value, str) or not value.strip():
                    raise ValueError(
                        f"Der Konfigurationswert '{key}' fehlt."
                    )

                custom_path = Path(value.strip()).expanduser()

                if not custom_path.is_absolute():
                    custom_path = project_dir / custom_path

                custom_path = custom_path.resolve()

                # Externe Pfade nicht stillschweigend akzeptieren.
                if not is_inside(custom_path, project_dir):
                    raise ValueError(
                        f"Der Ordner '{key}' liegt außerhalb des "
                        f"Projektverzeichnisses: {custom_path}"
                    )

                if normalized_path(custom_path) == project_norm:
                    raise ValueError(
                        f"Der Ordner '{key}' verweist auf das gesamte Projekt."
                    )

                if normalized_path(custom_path) not in {
                    normalized_path(p) for p in protected_custom_paths
                }:
                    protected_custom_paths.append(custom_path)

            if len(protected_custom_paths) != 3:
                raise ValueError(
                    "Die drei konfigurierten Ordner müssen eindeutig sein."
                )

        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            QMessageBox.critical(
                self,
                "Konfigurationsfehler",
                "Das Update wurde aus Sicherheitsgründen nicht gestartet.\n\n"
                "Die drei Ordner aus config.json konnten nicht sicher "
                "ermittelt werden.\n\n"
                f"Details: {exc}",
            )
            return False

        # --------------------------------------------------
        # 3. Zentrale Schutzprüfung für Backup und Update
        # --------------------------------------------------
        def is_protected(path):
            path = Path(path)

            try:
                relative = path.relative_to(project_dir)
            except ValueError:
                return True

            # Geschützte Standardordner überall im relativen Pfad.
            # Geschützte Standardordner und alle Ordner mit s3 oder s4
            # im Namen schützen – sowohl beim Backup als auch beim Update.
            protected_dir_names = {
                name.casefold() for name in protected_dirs
            }

            if any(
                part.casefold() in protected_dir_names
                or "s3" in part.casefold()
                or "s4" in part.casefold()
                for part in relative.parts[:-1]
            ):
                return True

            # Die drei konfigurierten Ordner samt Inhalt schützen.
            for custom_path in protected_custom_paths:
                if is_inside(path, custom_path):
                    return True

            # Bestimmte Dateien nicht sichern oder überschreiben.
            if path.name.casefold() in {
                name.casefold() for name in protected_files
            }:
                return True

            # INI-Dateien grundsätzlich erhalten.
            if path.suffix.casefold() == ".ini":
                return True

            return False

        def update_progress(value, message):
            """Aktualisiert Fortschritt und optionale Statusanzeige."""
            if progress_callback:
                progress_callback(value, message)

            pbar = getattr(self, "progress_bar", None)
            if pbar is not None:
                pbar.setValue(value)
                pbar.setFormat(message)

            info = getattr(self, "info_text", None)
            if info is not None:
                timestamp = QTime.currentTime().toString("HH:mm:ss")
                self.append_info(
                    info,
                    f"🔄 [{timestamp}] Update {value}% – {message}",
                    "info",
                )

            QApplication.processEvents()

        # --------------------------------------------------
        # 4. Schreibrechte prüfen
        # --------------------------------------------------
        if not self._has_project_write_access(project_dir):
            lang = str(getattr(self, "LANG", "de")).lower()[:2]
            is_de = lang == "de"

            msg_box = QMessageBox(self)
            msg_box.setWindowTitle(
                "Administratorrechte erforderlich"
                if is_de else "Administrator privileges required"
            )
            msg_box.setText(
                (
                    "Für das Update fehlen Schreibrechte. "
                    "Soll das Programm mit Administratorrechten neu starten "
                    "und das Update automatisch fortsetzen?"
                )
                if is_de else
                (
                    "The update requires write access. Restart the application "
                    "with administrator privileges and continue automatically?"
                )
            )

            yes_button = msg_box.addButton(
                "Ja" if is_de else "Yes",
                QMessageBox.ButtonRole.YesRole,
            )
            no_button = msg_box.addButton(
                "Nein" if is_de else "No",
                QMessageBox.ButtonRole.NoRole,
            )

            msg_box.setDefaultButton(yes_button)
            msg_box.setEscapeButton(no_button)
            msg_box.exec()

            if msg_box.clickedButton() != yes_button:
                return False

            restart_result = self.restart_application_elevated(
                perform_update=True
            )

            if restart_result != "already_elevated":
                return False

        # --------------------------------------------------
        # 5. Dauerhaftes Backup sicher erstellen
        # --------------------------------------------------
        persistent_backup_dir = project_dir / "backup"
        persistent_backup_zip = persistent_backup_dir / "backup.zip"
        backup_temp_zip = persistent_backup_dir / "backup.tmp.zip"

        try:
            persistent_backup_dir.mkdir(parents=True, exist_ok=True)

            if backup_temp_zip.exists():
                backup_temp_zip.unlink()

            # os.walk mit topdown=True ist entscheidend:
            # Geschützte Verzeichnisse werden aus dirs entfernt,
            # bevor os.walk diese Verzeichnisse betreten kann.
            with zipfile.ZipFile(
                backup_temp_zip,
                mode="w",
                compression=zipfile.ZIP_DEFLATED,
            ) as backup_archive:

                for root, dirs, files in os.walk(
                    project_dir,
                    topdown=True,
                    followlinks=False,
                ):
                    root_path = Path(root)

                    filtered_dirs = []

                    for dirname in dirs:
                        candidate = root_path / dirname
                        # Alle Ordner ausschließen, deren Name s3 oder s4 enthält.
                        dirname_lower = dirname.casefold()

                        if "s3" in dirname_lower or "s4" in dirname_lower:
                            continue
                        # Standardordner ausschließen.
                        if dirname.casefold() in {
                            name.casefold() for name in protected_dirs
                        }:
                            continue

                        # Konfigurierte Ordner vor dem Betreten ausschließen.
                        if any(
                            normalized_path(candidate)
                            == normalized_path(custom_path)
                            for custom_path in protected_custom_paths
                        ):
                            continue

                        # Keine Verzeichnis-Symlinks verfolgen.
                        if candidate.is_symlink():
                            continue

                        filtered_dirs.append(dirname)

                    dirs[:] = filtered_dirs

                    for filename in files:
                        source_file = root_path / filename

                        # Keine Datei-Symlinks oder externen Ziele archivieren.
                        if source_file.is_symlink():
                            continue

                        if not is_inside(source_file, project_dir):
                            continue

                        # Zusätzliche Schutzprüfung, bevor die Datei gelesen wird.
                        if is_protected(source_file):
                            continue

                        relative_path = source_file.relative_to(project_dir)

                        backup_archive.write(
                            source_file,
                            arcname=relative_path.as_posix(),
                        )

            # Integrität des ZIP-Archivs prüfen.
            with zipfile.ZipFile(backup_temp_zip, "r") as backup_archive:
                bad_file = backup_archive.testzip()

                if bad_file is not None:
                    raise RuntimeError(
                        f"Backup-Prüfung fehlgeschlagen: {bad_file}"
                    )

            # Erst jetzt das bisherige Backup ersetzen.
            os.replace(backup_temp_zip, persistent_backup_zip)

        except Exception as backup_error:
            try:
                if backup_temp_zip.exists():
                    backup_temp_zip.unlink()
            except OSError:
                pass

            QMessageBox.critical(
                self,
                "Backup-Fehler",
                "Das Backup konnte nicht sicher erstellt werden:\n"
                f"{backup_error}\n\n"
                "Das Update wurde nicht gestartet. "
                "Ein eventuell vorhandenes backup.zip wurde nicht absichtlich "
                "durch ein unvollständiges Archiv ersetzt.",
            )
            return False

        # --------------------------------------------------
        # 6. Temporäres Rollback-Backup
        # --------------------------------------------------
        backup_dir = Path(tempfile.mkdtemp(prefix="oscam_backup_"))

        download_url = (
            "https://github.com/speedy005/oscam_patch_manager_new/"
            "archive/refs/heads/master.zip"
        )

        backup_files = []
        new_files = []
        rollback_errors = []

        try:
            # --------------------------------------------------
            # 7. GitHub-Archiv herunterladen
            # --------------------------------------------------
            update_progress(10, "GitHub wird heruntergeladen...")

            response = requests.get(
                download_url,
                timeout=(15, 120),
                headers={"Accept": "application/vnd.github+json"},
            )
            response.raise_for_status()

            update_progress(20, "Download abgeschlossen. ZIP wird geprüft...")

            # --------------------------------------------------
            # 8. Archiv prüfen und Update-Dateien ermitteln
            # --------------------------------------------------
            with zipfile.ZipFile(BytesIO(response.content), "r") as archive:
                members = archive.namelist()

                file_members = [
                    member for member in members
                    if not member.endswith("/")
                ]

                if not file_members:
                    raise RuntimeError("Das GitHub-Archiv ist leer.")

                # GitHub-Archive müssen genau einen gemeinsamen Stammordner haben.
                first_parts = PurePosixPath(
                    file_members[0].replace("\\", "/")
                ).parts

                if not first_parts:
                    raise RuntimeError("Ungültige Archivstruktur.")

                root_name = first_parts[0]
                updates = []

                for member in file_members:
                    normalized_member = member.replace("\\", "/")
                    member_path = PurePosixPath(normalized_member)
                    parts = member_path.parts

                    if (
                        not parts
                        or parts[0] != root_name
                        or member_path.is_absolute()
                        or ".." in parts
                        or any(":" in part for part in parts)
                    ):
                        raise RuntimeError(
                            f"Unsicherer Dateipfad im Update: {member}"
                        )

                    relative_parts = parts[1:]

                    if not relative_parts:
                        continue

                    relative_name = PurePosixPath(*relative_parts)

                    if any(part in ("", ".", "..") for part in relative_parts):
                        raise RuntimeError(
                            f"Ungültiger Dateipfad im Update: {member}"
                        )

                    target = (
                        project_dir / Path(*relative_parts)
                    ).resolve()

                    if not is_inside(target, project_dir):
                        raise RuntimeError(
                            f"Datei liegt außerhalb des Projektordners: {target}"
                        )

                    # Geschützte Verzeichnisse und Dateien nie aktualisieren.
                    if is_protected(target):
                        continue

                    if target.exists() and not target.is_file():
                        raise RuntimeError(
                            f"Zielpfad ist keine Datei: {target}"
                        )

                    updates.append((member, target))

                if not updates:
                    raise RuntimeError(
                        "Keine aktualisierbaren Projektdateien gefunden."
                    )

                update_progress(
                    23,
                    f"{len(updates)} Update-Dateien gefunden.",
                )

                # --------------------------------------------------
                # 9. Vorhandene Dateien für Rollback sichern
                # --------------------------------------------------
                update_progress(25, "Rollback-Backup wird erstellt...")

                for _, target in updates:
                    if target.is_file():
                        backup_file = backup_dir / target.relative_to(project_dir)
                        backup_file.parent.mkdir(parents=True, exist_ok=True)

                        shutil.copy2(target, backup_file)
                        backup_files.append((target, backup_file))
                    else:
                        new_files.append(target)

                update_progress(35, "Rollback-Backup abgeschlossen.")

                # --------------------------------------------------
                # 10. Update installieren
                # --------------------------------------------------
                update_progress(40, "Projektdateien werden ersetzt...")

                for index, (member, target) in enumerate(updates):
                    relative_target = target.relative_to(project_dir)

                    # Vorhandene WAV-Dateien im sounds-Ordner erhalten.
                    if (
                        target.is_file()
                        and target.suffix.lower() == ".wav"
                        and "sounds" in relative_target.parts
                    ):
                        continue

                    target.parent.mkdir(parents=True, exist_ok=True)

                    temp_target = target.with_name(
                        target.name + ".update_tmp"
                    )

                    try:
                        with archive.open(member, "r") as source:
                            with open(temp_target, "wb") as destination:
                                shutil.copyfileobj(source, destination)

                        replace_error = None

                        for attempt in range(4):
                            try:
                                os.replace(temp_target, target)
                                replace_error = None
                                break

                            except PermissionError as exc:
                                replace_error = exc

                                print(
                                    f"[UPDATE DEBUG] Versuch {attempt + 1}/4: "
                                    f"{exc!r}; Ziel={target}"
                                )

                                if attempt < 3:
                                    time.sleep(0.5 * (attempt + 1))

                        if replace_error is not None:
                            raise replace_error

                    finally:
                        try:
                            if temp_target.exists():
                                temp_target.unlink()
                        except OSError:
                            pass

                    if not target.is_file():
                        raise RuntimeError(
                            f"Datei konnte nicht installiert werden: {target}"
                        )

                    progress = 40 + int(
                        50 * (index + 1) / len(updates)
                    )

                    update_progress(
                        progress,
                        f"Installiere Datei {index + 1}/{len(updates)}",
                    )

            # --------------------------------------------------
            # 11. Update erfolgreich
            # --------------------------------------------------
            lang = str(getattr(self, "LANG", "de")).lower()[:2]
            is_de = lang == "de"

            update_progress(
                100,
                "Update erfolgreich" if is_de else "Update successful",
            )

            timestamp = QTime.currentTime().toString("HH:mm:ss")

            info = getattr(self, "info_text", None)
            if info is not None:
                self.append_info(
                    info,
                    f"✅ [{timestamp}] "
                    + (
                        "Update erfolgreich abgeschlossen."
                        if is_de
                        else "Update completed successfully."
                    ),
                    "success",
                )

            msg_box = QMessageBox(self)
            msg_box.setIcon(QMessageBox.Icon.Question)
            msg_box.setWindowTitle(
                "Update erfolgreich" if is_de else "Update successful"
            )
            msg_box.setText(
                (
                    "Das Projekt wurde aktualisiert.\n\n"
                    "Lokale Konfigurationen und geschützte Ordner wurden erhalten.\n\n"
                    "Programm jetzt neu starten?"
                )
                if is_de else
                (
                    "The project has been updated.\n\n"
                    "Local configurations and protected folders were preserved.\n\n"
                    "Restart the application now?"
                )
            )

            yes_btn = msg_box.addButton(
                "Ja" if is_de else "Yes",
                QMessageBox.ButtonRole.YesRole,
            )
            msg_box.addButton(
                "Nein" if is_de else "No",
                QMessageBox.ButtonRole.NoRole,
            )

            msg_box.setDefaultButton(yes_btn)
            msg_box.exec()

            should_restart = msg_box.clickedButton() == yes_btn

            # Erfolgreiches Update: Rollback-Dateien nicht mehr erforderlich.
            shutil.rmtree(backup_dir, ignore_errors=True)

            if should_restart:
                if getattr(sys, "frozen", False):
                    command = [sys.executable, *sys.argv[1:]]
                    app_path = sys.executable
                else:
                    script_path = os.path.abspath(sys.argv[0])
                    command = [
                        sys.executable,
                        script_path,
                        *sys.argv[1:],
                    ]
                    app_path = script_path

                popen_kwargs = {
                    "cwd": os.path.dirname(app_path) or None,
                    "close_fds": True,
                }

                if os.name == "nt":
                    popen_kwargs["creationflags"] = getattr(
                        subprocess,
                        "CREATE_NEW_PROCESS_GROUP",
                        0,
                    )
                else:
                    popen_kwargs["start_new_session"] = True

                try:
                    process = subprocess.Popen(command, **popen_kwargs)

                    time.sleep(1)

                    if process.poll() is not None:
                        raise RuntimeError(
                            "Der neue Prozess wurde sofort beendet "
                            f"(Exit-Code {process.returncode})."
                        )

                except Exception as restart_error:
                    QMessageBox.critical(
                        self,
                        "Neustart fehlgeschlagen" if is_de else "Restart failed",
                        (
                            "Die aktualisierte Anwendung konnte nicht neu "
                            f"gestartet werden:\n{restart_error}"
                        )
                        if is_de else
                        (
                            "The updated application could not be restarted:\n"
                            f"{restart_error}"
                        ),
                    )
                    return True

                QApplication.quit()

            return True

        except Exception as error:
            # --------------------------------------------------
            # 12. Fehler: Rollback versuchen
            # --------------------------------------------------
            timestamp = QTime.currentTime().toString("HH:mm:ss")

            info = getattr(self, "info_text", None)
            if info is not None:
                self.append_info(
                    info,
                    f"❌ [{timestamp}] Update fehlgeschlagen: {error}",
                    "error",
                )
                self.append_info(
                    info,
                    f"↩️ [{timestamp}] Rollback wird ausgeführt...",
                    "warning",
                )

            # Neu angelegte Dateien entfernen.
            for target in reversed(new_files):
                try:
                    if target.exists() and target.is_file():
                        target.unlink()
                except Exception as rollback_error:
                    rollback_errors.append(
                        f"{target}: {rollback_error}"
                    )

            # Vorhandene Dateien wiederherstellen.
            for target, backup_file in reversed(backup_files):
                try:
                    if backup_file.exists():
                        target.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(backup_file, target)
                except Exception as rollback_error:
                    rollback_errors.append(
                        f"{target}: {rollback_error}"
                    )

            details = f"Update fehlgeschlagen:\n{error}"

            if rollback_errors:
                details += (
                    "\n\nRollback nicht vollständig!\n"
                    f"Rollback-Backup:\n{backup_dir}\n\n"
                    + "\n".join(rollback_errors)
                )

                if info is not None:
                    self.append_info(
                        info,
                        f"⚠️ [{timestamp}] Rollback unvollständig. "
                        f"Backup prüfen: {backup_dir}",
                        "error",
                    )
            else:
                details += (
                    "\n\nDie geänderten Dateien wurden wiederhergestellt."
                )

                shutil.rmtree(backup_dir, ignore_errors=True)

                if info is not None:
                    self.append_info(
                        info,
                        f"✅ [{timestamp}] Rollback abgeschlossen.",
                        "success",
                    )

            QMessageBox.critical(
                self,
                "Update-Fehler",
                details,
            )

            return False

    def ask_for_update(self, latest_version):
        """Fragt nach Update mit Sound und bereitet die Regenbogen-Bar vor."""
        from PyQt6.QtWidgets import QMessageBox, QApplication
        from PyQt6.QtCore import QTimer

        lang = str(getattr(self, "LANG", "de")).lower()[:2]
        is_de = lang == "de"
        pbar = getattr(self, "progress_bar", None)

        # SOUND abspielen, bevor die Box blockiert
        if "safe_play" in globals():
            safe_play("dialog-information.oga")

        msg_box = QMessageBox(self)
        msg_box.setIcon(QMessageBox.Icon.Question)
        msg_box.setWindowTitle("Update" if is_de else "Update Available")
        message = (
            f"Update {latest_version} verfügbar. Jetzt installieren?"
            if is_de
            else f"Update {latest_version} available. Install now?"
        )
        msg_box.setText(message)

        yes_btn = msg_box.addButton(
            "Ja" if is_de else "Yes", QMessageBox.ButtonRole.YesRole
        )
        no_btn = msg_box.addButton(
            "Nein" if is_de else "No", QMessageBox.ButtonRole.NoRole
        )
        msg_box.setDefaultButton(yes_btn)

        if msg_box.exec() == 0 or msg_box.clickedButton() == yes_btn:
            # Regenbogen-Style sofort anwenden
            if pbar:
                rainbow = (
                    "qlineargradient(x1:0, y1:0, x2:1, y2:0, "
                    "stop:0.0 #FF0000, stop:0.2 #FF7F00, stop:0.4 #FFFF00, "
                    "stop:0.6 #00FF00, stop:0.8 #0000FF, stop:1.0 #8B00FF);"
                )
                pbar.setStyleSheet(
                    f"QProgressBar {{ text-align: center; font-weight: 900; color: black; background: #111; border: 2px solid #222; }} "
                    f"QProgressBar::chunk {{ background: {rainbow}; }}"
                )
                pbar.setFormat("🚀 Starte..." if is_de else "🚀 Starting...")
                pbar.setValue(5)
                pbar.show()
                QApplication.processEvents()

            QTimer.singleShot(200, lambda: self.plugin_update_action(latest_version))

    # ======= HIER EINSETZEN =======
    def create_action_button(
        self,
        parent,
        text,
        color,
        callback,
        all_buttons_list,
        fg="white",
        factor_hover=1.15,
        factor_pressed=0.85,
        min_height=50,
        radius=8,
        icon_name=None,
    ):
        # --- Final Label verstecken ---
        if hasattr(self, "hide_final_label"):
            self.hide_final_label()

        from PyQt6.QtGui import QIcon
        from PyQt6.QtCore import QSize, Qt
        from PyQt6.QtWidgets import QStyle, QSizePolicy, QPushButton

        # Text-Formatierung: Bei mehr als 12 Zeichen Umbruch für Icons
        display_text = text
        btn = QPushButton(display_text, parent)
        btn.setProperty("action_button", True)
        # --- ICON LOGIK (WINDOWS & LINUX SAFE) ---
        if icon_name:
            icon = QIcon()
            if str(icon_name).startswith("SP_"):
                p_enum = getattr(QStyle.StandardPixmap, icon_name, None)
                if p_enum:
                    icon = self.style().standardIcon(p_enum)
            elif os.path.exists(str(icon_name)):
                icon = QIcon(icon_name)

            if not icon.isNull():
                btn.setIcon(icon)
                btn.setIconSize(QSize(22, 22))

        # Farben für Effekte berechnen
        hover_color = (
            self.adjust_color(color, factor_hover)
            if hasattr(self, "adjust_color")
            else color
        )
        pressed_color = (
            self.adjust_color(color, factor_pressed)
            if hasattr(self, "adjust_color")
            else color
        )

        # --- STYLESHEET MIT GLOW-EFFEKT ---
        btn.setStyleSheet(
            f"""
            QPushButton {{
                background-color: {color}; color: {fg}; border-radius: {radius}px;
                border: 1px solid rgba(255,255,255,0.1); padding: 5px;
                font-weight: bold; min-height: {min_height}px;
            }}
            QPushButton:hover {{
                background-color: {hover_color};
                border: 1px solid white;
                color: #FFFFFF;
                /* Glow-Effekt Simulation durch Border */
            }} 
            QPushButton:pressed {{
                background-color: {pressed_color};
                border: 2px solid white;
            }} 
        """
        )

        btn.setCursor(Qt.CursorShape.PointingHandCursor)

        # --- DER AUTOMATISCHE REGENBOGEN-TRIGGER ---
        def enhanced_callback():
            # 1. Regenbogen-Progress sofort starten
            pbar = getattr(self, "progress_bar", None)
            if pbar:
                rainbow = (
                    "qlineargradient(x1:0, y1:0, x2:1, y2:0, "
                    "stop:0.0 #FF0000, stop:0.2 #FF7F00, stop:0.4 #FFFF00, "
                    "stop:0.6 #00FF00, stop:0.8 #0000FF, stop:1.0 #8B00FF);"
                )
                pbar.setStyleSheet(
                    f"QProgressBar {{ text-align: center; font-weight: 700; border: 2px solid #222; "
                    f"border-radius: 6px; background: #111; color: black; font-size: 15pt; }} "
                    f"QProgressBar::chunk {{ background: {rainbow}; }}"
                )
                pbar.setValue(15)
                pbar.show()

            # 2. Original-Funktion ausführen
            if callback:
                callback()

        if callback:
            btn.clicked.connect(enhanced_callback)

        all_buttons_list.append(btn)
        return btn

    def generate_buttons(
        self, parent, button_definitions, all_buttons_list, info_widget=None
    ):
        """Erzeugt Buttons basierend auf Dictionary-Listen (DE/EN tauglich)."""
        buttons = {}
        def_height = getattr(self, "BUTTON_HEIGHT", 50)
        def_radius = getattr(self, "BUTTON_RADIUS", 8)

        for bd in button_definitions:
            # Falls Name "work", "temp" oder "emu" -> Nutze open_custom_folder
            key = bd.get("key", "unknown")
            callback = bd.get("callback", lambda: None)

            # Icons übersetzen (SP_ Pfade)
            icon = bd.get("icon")
            if not icon and "open" in key:
                icon = "SP_DirIcon"

            btn = self.create_action_button(
                parent=parent,
                text=bd.get("text", "Button"),
                color=bd.get("color", "#444444"),
                callback=callback,
                all_buttons_list=all_buttons_list,
                fg=bd.get("fg", "white"),
                icon_name=icon,
                min_height=bd.get("min_height", def_height),
                radius=bd.get("radius", def_radius),
            )
            buttons[key] = btn
        return buttons

    
    def _fit_header_controls(self):
        """Passt die Kopfzeilen-Buttons an die verfügbare Fensterbreite an."""
        if not hasattr(self, "header_widget"):
            return

        width = max(480, self.width())
        # Schrift wird bei schmalen Fenstern kleiner, aber nicht unlesbar.
        font_size = max(7, min(10, int(width / 150)))
        compact = width < 1450
        gap = 4 if compact else 8

        for layout_name in ("header_layout", "left_header_layout", "right_header_layout"):
            layout = getattr(self, layout_name, None)
            if layout is not None:
                layout.setSpacing(gap)

        names = (
            "info_button", "btn_s3", "btn_s4", "btn_ncam",
            "header_btn_s3_ncam", "header_btn_s4",
            "header_btn_patch_ncam", "header_btn_patch_s4",
            "log_button", "telemetry_cb", "oscam_source_button",
        )
        for name in names:
            button = getattr(self, name, None)
            if button is None:
                continue
            button.setFont(QFont("Segoe UI", font_size, QFont.Weight.Bold))
            if name != "info_button":
                button.setMinimumHeight(50 if compact else 50)
                button.setMaximumWidth(260 if name in ("btn_s3", "btn_s4", "btn_ncam") else 340)
                button.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)

    
   
    def _fit_header_logo(self):
        """Skaliert das Banner proportional bis zur maximalen Größe."""
        if (
            not hasattr(self, "logo_label")
            or self.original_pixmap is None
        ):
            return

        rect = self.logo_label.contentsRect()
        if rect.width() <= 0 or rect.height() <= 0:
            return

        # Die verfügbare Fläche des Labels bestimmt die Logo-Größe.
        # Keine festen Pixel-Maxima: Das Banner wächst/schrumpft mit der
        # Fenster- und Bildschirmgröße und behält sein Seitenverhältnis.
        self.update_logo(
            width=rect.width(),
            height=rect.height(),
            fill_gap=False,
        )


    def update_logo(self, width=None, height=None, fill_gap=False):
        """Skaliert das Banner auf die vorgegebene Fläche."""
        if (
            not hasattr(self, "logo_label")
            or self.original_pixmap is None
        ):
            return

        if width is None or height is None:
            rect = self.logo_label.contentsRect()
            width = rect.width()
            height = rect.height()

        if width <= 0 or height <= 0:
            return

        aspect_mode = (
            Qt.AspectRatioMode.IgnoreAspectRatio
            if fill_gap
            else Qt.AspectRatioMode.KeepAspectRatio
        )

        scaled_pixmap = self.original_pixmap.scaled(
            width,
            height,
            aspect_mode,
            Qt.TransformationMode.SmoothTransformation,
        )

        self.logo_label.setPixmap(scaled_pixmap)





    # 🔹 HIER EINSETZEN 🔹
    def adjust_color(self, hex_color, factor):
        """
        Passt die Helligkeit einer Farbe an.
        factor > 1.0 = heller (Hover), factor < 1.0 = dunkler (Pressed).
        """
        hex_color = hex_color.lstrip("#")

        # Support für 3-stellige Hex-Codes (z.B. #F00 -> #FF0000)
        if len(hex_color) == 3:
            hex_color = "".join([c * 2 for c in hex_color])

        try:
            r = int(hex_color[0:2], 16)
            g = int(hex_color[2:4], 16)
            b = int(hex_color[4:6], 16)
        except ValueError:
            # Fallback falls der Hex-Code ungültig ist
            return "#1E90FF"

        # Farbberechnung mit Clamp (0-255)
        r = max(0, min(255, int(r * factor)))
        g = max(0, min(255, int(g * factor)))
        b = max(0, min(255, int(b * factor)))

        return f"#{r:02X}{g:02X}{b:02X}"

    def update_revision_button_labels(self):
        """
        Aktualisiert die beiden Revisions-Buttons mit der Versionsnummer aus
        globals.h des aktuell verwendeten Git-Commits.

        Beispiel:
            Streamboard Revision (11968)
            OSCam-Emu Revision (11968)

        Die ermittelte Revisionsnummer wird zusätzlich im GUI-Objekt
        gespeichert. Dadurch bleibt die Anzeige auch nach einem
        Sprachwechsel erhalten, selbst wenn globals.h kurzfristig nicht
        erneut gelesen werden kann.
        """
        import os
        import re

        def revision_for(repo_dir, selected_commit=None, cache_attr=None):
            repo_dir = os.path.abspath(str(repo_dir or "").strip())
            commit = str(selected_commit or "").strip()

            if not commit and repo_dir and os.path.isdir(os.path.join(repo_dir, ".git")):
                try:
                    result = git_command(["rev-parse", "HEAD"], cwd=repo_dir)
                    commit = (result.stdout or "").strip()
                except Exception:
                    commit = ""

            revision = ""

            # Die Version wird immer aus globals.h des exakt verwendeten
            # Commits gelesen, nicht aus einer eventuell alten Arbeitsdatei.
            if repo_dir and commit and os.path.isdir(os.path.join(repo_dir, ".git")):
                try:
                    version = str(get_oscam_version(repo_dir, commit) or "").strip()
                except Exception:
                    version = ""

                if version:
                    # Üblicher OSCam-Wert: 2.xx.xx-11968
                    match = re.search(r"(?:^|[-_.])([0-9]+)$", version)
                    if match:
                        revision = match.group(1)
                    else:
                        # Falls globals.h nur eine reine Revisionsnummer enthält.
                        match = re.fullmatch(r"[0-9]+", version)
                        if match:
                            revision = version

            # Letzten erfolgreichen Wert behalten, damit ein Sprachwechsel
            # oder ein kurzzeitig nicht lesbares Repository die Anzeige nicht
            # verschwinden lässt.
            if revision:
                if cache_attr:
                    setattr(self, cache_attr, revision)
            elif cache_attr:
                revision = str(getattr(self, cache_attr, "") or "").strip()

            return revision

        stream_revision = revision_for(
            globals().get("STREAMREPO", ""),
            (
                getattr(self, "selected_streamboard_commit", None)
                or getattr(self, "display_streamboard_commit", None)
            ),
            "streamboard_revision_number",
        )

        emu_dir = globals().get("PATCH_EMU_GIT_DIR", "")
        if not emu_dir:
            emu_dir = getattr(self, "PATCH_EMU_GIT_DIR", "")

        emu_revision = revision_for(
            emu_dir,
            (
                getattr(self, "selected_emu_commit", None)
                or getattr(self, "display_emu_commit", None)
            ),
            "emu_revision_number",
        )

        def set_revision(key, base_text, revision):
            entry = getattr(self, "option_buttons", {}).get(key)
            if not entry:
                return

            btn = entry[0] if isinstance(entry, tuple) else entry
            text = base_text
            if revision:
                text = f"{base_text} ({revision})"

            btn.setText(text)
            btn.setProperty("revision_number", revision or "")
            btn.setProperty("base_text", base_text)

        # Die Bezeichnungen sind sprachunabhängig identisch. Wichtig ist,
        # dass nur der Text vor der Klammer übersetzt würde; die Revision
        # selbst bleibt unverändert erhalten.
        set_revision(
            "streamboard_checkout",
            "Streamboard Revision",
            stream_revision,
        )
        set_revision(
            "oscam_emu_revision",
            "OSCam-Emu Revision",
            emu_revision,
        )

    def setup_option_buttons(self, parent_layout):
        """Erstellt die mittleren Buttons mit HTML-Tooltips, Regenbogen-Progress und Sound."""
        from PyQt6.QtWidgets import (
            QGridLayout,
            QWidget,
            QSizePolicy,
            QApplication,
            QPushButton,
        )
        from PyQt6.QtGui import QFont
        from PyQt6.QtCore import QTimer

        # Sprache abrufen
        lang = str(getattr(self, "LANG", "de")).lower()[:2]
        is_de = lang == "de"

        button_defs = [
            (
                "git_status",
                "git_status",
                "#1E90FF",
                self.show_commits,
                "white",
                "SP_FileDialogContentsView",
                "🔍 <b>Git Status:</b> Zeigt die neuesten Commits vom Server an.",
                "🔍 <b>Git Status:</b> Shows the latest commits from the server.",
            ),
            (
                "plugin_update",
                "plugin_update",
                "#FF8C00",
                self.plugin_update_button_clicked,
                "white",
                "SP_ArrowDown",
                "🔄 <b>Update:</b> Sucht nach neuen Versionen für Plugins und Tools.",
                "🔄 <b>Update:</b> Checks for new versions of plugins and tools.",
            ),
            (
                "restart_tool",
                "restart_tool",
                "#FF4500",
                self.restart_application_with_info,
                "white",
                "SP_BrowserReload",
                "♻️ <b>Neustart:</b> Startet den Patch Manager komplett neu.",
                "♻️ <b>Restart:</b> Restarts the patch manager completely.",
            ),
                        (
                "restart_tool_admin",
                "restart_tool_admin",
                "#B22222",
                self.restart_application_elevated,
                "white",
                "SP_DialogApplyButton",
                "🔐 <b>Admin-Neustart:</b> Startet das Tool mit Administratorrechten.",
                "🔐 <b>Admin restart:</b> Restarts the tool with elevated privileges.",
            ),
            (
                "edit_patch_header",
                "edit_patch_header",
                "#32CD32",
                self.edit_patch_header,
                "white",
                "SP_FileDialogDetailedView",
                "📝 <b>Header:</b> Bearbeite die Beschreibungs-Daten des Patches.",
                "📝 <b>Header:</b> Edit the patch description metadata.",
            ),
            (
                "github_emu_config",
                "github_emu_config_button",
                "#FFA500",
                self.edit_emu_github_config,
                "black",
                "SP_ComputerIcon",
                "⚙️ <b>Config:</b> GitHub Token und Emu-Einstellungen konfigurieren.",
                "⚙️ <b>Config:</b> Configure GitHub tokens and Emu settings.",
            ),
            (
                "github_upload_patch",
                "github_upload_patch",
                "#1E90FF",
                github_upload_patch_file,
                "white",
                "SP_ArrowUp",
                "⬆️ <b>Upload:</b> Lädt die aktuelle Patch-Datei in dein GitHub Repo.",
                "⬆️ <b>Upload:</b> Uploads the current patch file to your GitHub repo.",
            ),
            (
                "github_upload_emu",
                "github_upload_emu",
                "#1E90FF",
                github_upload_oscam_emu_folder,
                "white",
                "SP_ArrowUp",
                "📂 <b>Upload:</b> Lädt den gesamten Emu-Ordner auf GitHub hoch.",
                "📂 <b>Upload:</b> Uploads the entire Emu folder to GitHub.",
            ),
            (
                "oscam_emu_git_patch",
                "oscam_emu_git_patch",
                "#32CD32",
                patch_oscam_emu_git,
                "white",
                "SP_DialogApplyButton",
                "🛠️ <b>Patch Emu:</b> Wendet Änderungen auf das Emu-Git Repository an.",
                "🛠️ <b>Patch Emu:</b> Applies changes to the Emu-Git repository.",
            ),
            (
                "oscam_emu_git_clear",
                "oscam_emu_git_clear",
                "#FF4500",
                self.oscam_emu_git_clear,
                "white",
                "SP_TrashIcon",
                "🧹 <b>Clear:</b> Bereinigt das lokale Emu-Verzeichnis (Reset).",
                "🧹 <b>Clear:</b> Cleans the local Emu directory (reset).",
            ),
            (
                "s3_menu",
                "s3_simplebuild",
                "#EAFF00",
                self.start_s3_menu,
                "black",
                "SP_FileDialogDetailedView",
                "🚀 <b>S3 Menü:</b> Öffnet das Standard s3_simplebuild Terminal.",
                "🚀 <b>S3 Menu:</b> Opens the standard s3_simplebuild terminal.",
            ),
            (
                "s4_menu",
                "s4_simplebuild",
                "#00E5FF",
                self.start_s4_menu,
                "black",
                "SP_FileDialogDetailedView",
                "🚀 <b>S4 Menü:</b> Öffnet das S4 Simplebuild Terminal.",
                "🚀 <b>S4 Menu:</b> Opens the S4 Simplebuild terminal.",
            ),
            (
                "ncam_menu",
                "NCam Bonecrew",
                "#FF8C00",
                self.start_ncam_menu,
                "black",
                "SP_ComputerIcon",
                "🏴‍☠️ <b>NCam:</b> Startet das spezialisierte NCam Bonecrew Menü.",
                "🏴‍☠️ <b>NCam:</b> Launches the specialized NCam Bonecrew menu.",
            ),
            (
                "fix_perms",
                "Rechte reparieren" if is_de else "Fix Permissions",
                "#D3D3D3",
                self.fix_all_tool_permissions,
                "black",
                "SP_DialogNoButton",
                "🔓 <b>Rechte:</b> Fixiert Schreibrechte für alle Ordner/Dateien.",
                "🔓 <b>Rights:</b> Fixes write permissions for all folders/files.",
            ),
            (
                "streamboard_checkout",
                "Streamboard Revision" if is_de else "Streamboard Revision",
                "#00BFFF",
                self.checkout_streamboard_revision,
                "white",
                "SP_DialogOpenButton",
                "📌 <b>Streamboard Revision:</b> Beliebigen Streamboard-Commit als Patch-Basis auswählen.",
                "📌 <b>Streamboard Revision:</b> Select any Streamboard commit as the patch base.",
            ),
            (
                "oscam_emu_revision",
                "OSCam-Emu Revision",
                "#00BFFF",
                self.checkout_emu_revision,
                "white",
                "SP_DialogOpenButton",
                "📌 <b>OSCam-Emu Revision:</b> Beliebigen OSCam-Emu-Commit als Patch-Basis auswählen.",
                "📌 <b>OSCam-Emu Revision:</b> Select any OSCam-Emu commit as the patch base.",
            ),
        ]

        container = QGroupBox("INSTALLATION & TOOLS")
        container.setObjectName("installationToolsPanel")
        options_grid = QGridLayout(container)
        options_grid.setSpacing(6)
        options_grid.setContentsMargins(10, 12, 10, 10)

        if not hasattr(self, "all_buttons"):
            self.all_buttons = []

        self.option_buttons = getattr(self, "option_buttons", {})

        # Responsive Startaufteilung; wird bei jeder Größenänderung angepasst.
        cols_per_row = 4

        # Einheitliche Höhe
        FLACH_HEIGHT = 50

        # ---------------------------------------------------------
        # BUTTONS ERSTELLEN
        # ---------------------------------------------------------
        for idx, (
            key,
            text_key,
            color,
            callback,
            fg,
            icon,
            tt_de,
            tt_en,
        ) in enumerate(button_defs):

            raw_text = (
                self.get_t(text_key, text_key)
                if hasattr(self, "get_t")
                else text_key
            )

            btn = QPushButton(raw_text)

            if key == "fix_perms":
                self.btn_fix_perms = btn

            # -----------------------------------------------------
            # CALLBACK
            # -----------------------------------------------------
            def create_cb(c, k=key):
                def wrapper():

                    if "safe_play" in globals():
                        safe_play("service-login.oga")

                    pbar = getattr(self, "progress_bar", None)

                    if pbar:
                        rainbow = (
                            "qlineargradient("
                            "x1:0, y1:0, x2:1, y2:0, "
                            "stop:0.0 #FF0000, "
                            "stop:0.2 #FF7F00, "
                            "stop:0.4 #FFFF00, "
                            "stop:0.6 #00FF00, "
                            "stop:0.8 #0000FF, "
                            "stop:1.0 #8B00FF);"
                        )

                        pbar.setStyleSheet(
                            f"""
                            QProgressBar {{
                                text-align: center;
                                font-weight: 700;
                                border: 2px solid #222;
                                border-radius: 6px;
                                background-color: #111;
                                color: black;
                                font-size: 15pt;
                            }}

                            QProgressBar::chunk {{
                                background-color: {rainbow};
                                border-radius: 4px;
                            }}
                            """
                        )

                        msg = (
                            "Verarbeite..."
                            if is_de
                            else "Processing..."
                        )

                        pbar.setFormat(f"⚙️ {msg} %p%")
                        pbar.setValue(15)
                        pbar.show()

                        QApplication.processEvents()

                    try:

                        if hasattr(c, "__self__") or k == "online_patch_dl":
                            c()

                        else:
                            callback_func = getattr(
                                self,
                                "upload_progress_with_speed",
                                pbar.setValue if pbar else None,
                            )

                            c(
                                gui_instance=self,
                                info_widget=self.info_text,
                                progress_callback=callback_func,
                            )

                        if "safe_play" in globals():
                            safe_play("complete.oga")

                        if pbar:
                            pbar.setValue(100)
                            pbar.setFormat(
                                "✅ OK!"
                                if is_de
                                else "✅ Done!"
                            )

                    except Exception as e:

                        if "safe_play" in globals():
                            safe_play("dialog-error.oga")

                        if pbar:
                            pbar.setStyleSheet(
                                """
                                QProgressBar {
                                    color: red;
                                    font-weight: 700;
                                }
                                """
                            )

                        print(f"Fehler bei {k}: {e}")

                    if pbar:
                        QTimer.singleShot(
                            3000,
                            self.pbar_idle
                            if hasattr(self, "pbar_idle")
                            else lambda: pbar.setValue(0),
                        )

                return wrapper

            # -----------------------------------------------------
            # BUTTON ERSTELLEN
            # -----------------------------------------------------
            btn = self.create_action_button(
                parent=self,
                text=raw_text,
                color=color,
                callback=create_cb(callback),
                all_buttons_list=self.all_buttons,
                fg=fg,
                icon_name=icon,
                min_height=FLACH_HEIGHT,
                radius=getattr(self, "BUTTON_RADIUS", 10),
            )

            # -----------------------------------------------------
            # TOOLTIP
            # -----------------------------------------------------
            btn.setToolTip(
                tt_de if is_de else tt_en
            )

            btn.setStyleSheet(
                btn.styleSheet()
                + f"""
                QToolTip {{
                    background-color: #2b2b2b;
                    color: {color};
                    border: 1px solid {color};
                    border-radius: 5px;
                    padding: 8px;
                    font-size: 10pt;
                }}
                """
            )

            # -----------------------------------------------------
            # WICHTIG:
            # TEXT NICHT UMBRECHEN
            # -----------------------------------------------------
            

            # -----------------------------------------------------
            # BUTTON-GRÖSSE
            # -----------------------------------------------------
            btn.setSizePolicy(
                QSizePolicy.Policy.Expanding,
                QSizePolicy.Policy.Fixed,
            )

            btn.setMinimumWidth(220)
            btn.setMinimumHeight(50)
            btn.setMaximumHeight(50)

            # ---------------------------------------------------------
            # EINZEILIGER BUTTON-TEXT
            # Schrift wird bei langen Namen automatisch kleiner
            # ---------------------------------------------------------
            font = QFont("Segoe UI", 9, QFont.Weight.Bold)
            btn.setFont(font)
            btn.setToolTip(btn.toolTip() or raw_text)


            # -----------------------------------------------------
            # GRID
            # -----------------------------------------------------
            row, col = divmod(
                idx,
                cols_per_row,
            )

            options_grid.addWidget(
                btn,
                row,
                col,
            )

            # Button speichern
            self.option_buttons[key] = (
                btn,
                text_key,
            )

        # ---------------------------------------------------------
        # Die konfigurierten Spalten gleichmäßig verteilen.
        # ---------------------------------------------------------
        for i in range(cols_per_row):
            options_grid.setColumnStretch(i, 1)

        # Responsive Reflow: Buttons bleiben verbunden und werden nur neu angeordnet.
        # Die Reflow-Schwelle an die tatsächliche Mindestbreite der Buttons
        # anlehnen. So nutzt das Grid breite Fenster besser aus, ohne die
        # Buttons unter ihre lesbare Mindestbreite zu drücken.
        self._options_grid_reflow = ResponsiveGridReflow(
            # Drei bis vier ausreichend breite Spalten statt vieler schmaler
            # Spalten; lange Beschriftungen bleiben dadurch lesbar.
            container, options_grid, min_item_width=400, parent=self
        )

        # ---------------------------------------------------------
        # CONTAINER
        # ---------------------------------------------------------
        parent_layout.addWidget(container)

        # Revisions direkt im Button anzeigen, z. B. "Streamboard Revision (11968)".
        self.update_revision_button_labels()

    def update_all_texts(self):
        # ---------------------------------------------------------
        # LABELS
        # ---------------------------------------------------------
        self.lang_label.setText(
            TEXTS[self.LANG]["ui.language"]
        )

        self.color_label.setText(
            TEXTS[self.LANG]["color_label"]
        )

        self.commit_label.setText(
            TEXTS[self.LANG]["commit_count_label"]
        )

        self.info_button.setToolTip(
            TEXTS[self.LANG]["ui.info"]
        )

        # ---------------------------------------------------------
        # OPTION BUTTONS
        # ---------------------------------------------------------
        for key, (btn, text_key) in self.option_buttons.items():

            new_text = self.get_t(text_key, text_key)

            btn.setText(new_text)
            btn.setProperty("text_key", text_key)

        # Revisions-Suffix nach dem Sprachwechsel wieder ergänzen.
        self.update_revision_button_labels()

        # ---------------------------------------------------------
        # GRID BUTTONS / PATCH AKTIONEN
        # ---------------------------------------------------------
        for key, btn in getattr(self, "buttons", {}).items():

            new_text = self.get_t(key, key)

            # Vollständigen Text setzen
            btn.setText(new_text)

            # Übersetzungsschlüssel speichern
            btn.setProperty("text_key", key)

            # Vollständigen Text zusätzlich speichern
            btn.setProperty("full_text", new_text)

        # ---------------------------------------------------------
        # INFO TEXT
        # ---------------------------------------------------------
        if (
            hasattr(self, "info_text")
            and "info_text" in TEXTS[self.LANG]
        ):
            self.info_text.setPlainText(
                TEXTS[self.LANG]["info_text"]
            )

    # ---------------------
    @staticmethod
    def start_update(self):
        print("Update starten …")
        # hier später:
        # - git pull
        # - zip download + entpacken
        # - oder installer starten

    def append_info(self, *args, **kwargs):
        from PyQt6.QtGui import QTextCursor
        from PyQt6.QtWidgets import QApplication, QTextEdit
        from PyQt6.QtCore import QPropertyAnimation, QEasingCurve, QAbstractAnimation

        # 1. Argumente sicher entpacken
        try:
            if len(args) == 3:
                widget, text, level = args[0], args[1], args[2]
            elif len(args) == 2:
                widget = getattr(self, "info_text", None)
                text, level = args[0], args[1]
            else:
                return
        except:
            return

        if not isinstance(widget, QTextEdit) or widget is None:
            return

        # Vor dem Einfügen merken, ob der Benutzer bereits am Ende liest.
        # Nur dann später automatisch nach unten scrollen.
        scrollbar = widget.verticalScrollBar()
        was_at_bottom = (
            scrollbar is None
            or scrollbar.value() >= scrollbar.maximum() - 2
        )

        # 2. Farben & Variablen
        colors = {
            "success": "#00FF00",
            "warning": "orange",
            "error": "red",
            "info": "yellow",
            "white": "white",
            "raw": "transparent",
        }
        lvl_str = str(level).lower()
        current_color = colors.get(lvl_str, "white")
        do_newline = kwargs.get("newline", True)

        # Persist only noteworthy events; ordinary build output stays in the live
        # console so the event history remains compact. Logging failures must
        # never interfere with the GUI or the original operation.
        if lvl_str in ("error", "warning", "success"):
            try:
                import json
                import os
                from datetime import datetime
                from pathlib import Path

                log_root = Path(os.path.dirname(os.path.abspath(__file__))) / "event_history"
                log_root.mkdir(parents=True, exist_ok=True)
                event_file = log_root / "events.jsonl"
                max_bytes = 2 * 1024 * 1024
                if event_file.exists() and event_file.stat().st_size >= max_bytes:
                    backup = log_root / "events.previous.jsonl"
                    try:
                        if backup.exists():
                            backup.unlink()
                        event_file.replace(backup)
                    except OSError:
                        # Continue without rotation if the file is locked.
                        pass
                event = {
                    "timestamp": datetime.now().astimezone().isoformat(timespec="seconds"),
                    "level": lvl_str,
                    "message": str(text)[:4000],
                }
                with event_file.open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(event, ensure_ascii=False) + "\n")
            except Exception:
                pass

        # 3. HTML vorbereiten
        # Meldungen als Text behandeln, nicht als HTML. So können Build-Ausgaben
        # mit spitzen Klammern/Markup weder das Layout beschädigen noch HTML injizieren.
        from html import escape as _html_escape
        safe_text = _html_escape(str(text)).replace("\n", "<br>").replace("\r", "")
        html_output = ""
        if do_newline and widget.toPlainText().strip() != "":
            html_output += "<br>"

        if lvl_str == "raw":
            html_output += f'<div style="display:inline;">{safe_text}</div>'
        else:
            html_output += (
                f'<div style="color:{current_color}; display:inline;">{safe_text}</div>'
            )

        # 4. Am Ende einfügen
        # NIEMALS setHtml()/clear()/setPlainText() verwenden:
        # append_info ist bewusst nicht-destruktiv, damit der komplette
        # große Infoscreen mit allen bisherigen Meldungen sichtbar bleibt.
        cursor = widget.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        widget.setTextCursor(cursor)
        cursor.insertHtml(html_output)

        # 5. Scroll-Verhalten: nur automatisch folgen, wenn der Benutzer
        # vor der neuen Meldung bereits am unteren Ende der Konsole war.
        if was_at_bottom and scrollbar is not None:
            scrollbar.setValue(scrollbar.maximum())
            widget.ensureCursorVisible()

    def run_background_task(self, fn, *args, **kwargs):
        """Startet eine Funktion im Hintergrund und steuert die Progressbar"""
        self.progress_bar.setValue(0)
        self.progress_bar.show()

        self.worker = TaskWorker(fn, *args, **kwargs)
        self.worker.progress.connect(self.progress_bar.setValue)
        self.worker.info.connect(
            lambda txt, lvl: self.append_info(self.info_text, txt, lvl)
        )
        self.worker.start()

    def restart_application_with_info(
        self, checked=False, progress_callback=None, info_widget=None
    ):
        """
        Sichert alle Daten und startet das Tool plattformübergreifend neu.
        Final-Label wird vorher ausgeblendet.
        """

        # --- Final Label ausblenden ---
        if hasattr(self, "hide_final_label"):
            self.hide_final_label()
        elif hasattr(self, "final_label") and self.final_label:
            self.final_label.hide()

        from PyQt6.QtWidgets import QMessageBox, QApplication
        from PyQt6.QtCore import QTimer  # FIX: QTimer Import hinzugefügt
        import sys
        import os
        import subprocess

        # 1. Sprache & Texte sicherstellen
        lang_key = getattr(self, "LANG", "de").lower()
        lang_pack = (
            globals()
            .get("TEXTS", {})
            .get(lang_key, globals().get("TEXTS", {}).get("en", {}))
        )

        # 2. Dialog aufbauen
        msg = QMessageBox(self)
        msg.setIcon(QMessageBox.Icon.Question)
        msg.setWindowTitle(lang_pack.get("restart_tool", "Neustart"))
        msg.setText(
            lang_pack.get(
                "restart_tool_question", "Möchten Sie das Tool jetzt neu starten?"
            )
        )

        yes_btn = msg.addButton(
            lang_pack.get("yes", "Ja"), QMessageBox.ButtonRole.YesRole
        )
        no_btn = msg.addButton(
            lang_pack.get("no", "Nein"), QMessageBox.ButtonRole.NoRole
        )

        msg.exec()

        if msg.clickedButton() == yes_btn:
            # Sound abspielen
            # Neustart-Sound
            sound_manager = getattr(self, "sound_manager", None)
            if sound_manager and sound_manager.enabled:
                sound_manager.exit_sound.play()
                QApplication.processEvents()

            # --- KRITISCHES SPEICHERN VOR NEUSTART ---
            try:
                # Falls self.cfg existiert, stellen wir sicher, dass die aktuellen UI-Werte drin sind
                if hasattr(self, "cfg"):
                    # Manuelle Synchronisation der wichtigsten Felder (Sicherheitsnetz)
                    if hasattr(self, "patch_modifier"):
                        self.cfg["patch_modifier"] = self.patch_modifier
                    if hasattr(self, "EMUREPO"):
                        self.cfg["EMUREPO"] = self.EMUREPO
                    if hasattr(self, "LANG"):
                        self.cfg["language"] = self.LANG.upper()

                    # Speichern (Silent=True, da wir eh beenden)
                    if "save_config" in globals():
                        globals()["save_config"](
                            self.cfg, gui_instance=self, silent=True
                        )

                # WICHTIG: Gib dem OS kurz Zeit, die Datei physisch zu schreiben
                QApplication.processEvents()

            except Exception as e:
                print(f"⚠️ Save error before restart: {e}")

            # --- PLATTFORMÜBERGREIFENDER NEUSTART ---
            try:
                python_exe = sys.executable
                # FIX: Variable einheitlich auf 'script' benannt
                script = os.path.realpath(sys.argv[0])
                cmd_args = sys.argv[1:]

                # Starte neuen Prozess
                if os.name == "nt":
                    # FIX: 'script_path' zu 'script' korrigiert
                    subprocess.Popen(
                        [python_exe, script] + cmd_args,
                        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
                        close_fds=True,
                        shell=False,
                    )
                else:
                    # FIX: 'script_path' zu 'script' korrigiert
                    subprocess.Popen(
                        [python_exe, script] + cmd_args,
                        cwd=os.path.dirname(script),
                        start_new_session=True,
                        close_fds=True
                    )

                # Beende aktuelle Instanz sauber
                QApplication.processEvents()
                QTimer.singleShot(800, QApplication.quit)
                
            except Exception as e:
                print(f"❌ Kritischer Fehler beim Neustart: {e}")
                QApplication.quit()
                sys.exit(0)
        else:
            # Bei "Nein" Fortschrittsbalken (falls vorhanden) auf 100 setzen
            if progress_callback:
                try:
                    progress_callback(100)
                except:
                    pass
    
    def restart_application_elevated(self, *args, **kwargs):
        """Startet das Tool mit erhöhten Rechten neu."""
        import os
        import sys
        import shutil
        import platform
        import subprocess

        from PyQt6.QtWidgets import QApplication, QMessageBox
        from PyQt6.QtCore import QTimer

        lang = str(getattr(self, "LANG", "de")).lower()[:2]
        is_de = lang == "de"

        perform_update = bool(kwargs.get("perform_update", False))

        extra_args = (
            ["--perform-update"]
            if perform_update and "--perform-update" not in sys.argv
            else []
        )

        # Prüfen, ob der aktuelle Prozess bereits erhöhte Rechte besitzt.
        already_elevated = False

        if os.name == "nt":
            try:
                import ctypes
                already_elevated = bool(
                    ctypes.windll.shell32.IsUserAnAdmin()
                )
            except Exception:
                already_elevated = False

        elif hasattr(os, "geteuid"):
            already_elevated = os.geteuid() == 0

        
        if already_elevated:
            if perform_update:
                # Der laufende Prozess hat bereits erhöhte Rechte.
                return "already_elevated"

            QMessageBox.information(

                self,
                "Hinweis" if is_de else "Information",
                (
                    "Das Tool läuft bereits mit Administratorrechten."
                    if is_de
                    else "The application is already running with administrator privileges."
                ),
            )
            return True

        # Bei einem manuellen Admin-Neustart erst bestätigen lassen.
        if not perform_update:
            msg_box = QMessageBox(self)
            msg_box.setWindowTitle(
                "Administratorrechte" if is_de else "Administrator privileges"
            )
            msg_box.setText(
                "Das Tool mit erhöhten Rechten neu starten?"
                if is_de
                else "Restart the application with elevated privileges?"
            )

            yes_button = msg_box.addButton(
                "Ja" if is_de else "Yes",
                QMessageBox.ButtonRole.YesRole,
            )
            no_button = msg_box.addButton(
                "Nein" if is_de else "No",
                QMessageBox.ButtonRole.NoRole,
            )

            msg_box.setDefaultButton(no_button)
            msg_box.setEscapeButton(no_button)
            msg_box.exec()

            if msg_box.clickedButton() != yes_button:
                return False

        script = os.path.realpath(sys.argv[0])
        cwd = os.path.dirname(script) or os.getcwd()

        try:
            if os.name == "nt":
                import ctypes

                # Windows: Neustart über UAC.
                if getattr(sys, "frozen", False):
                    executable = sys.executable
                    params = subprocess.list2cmdline(
                        sys.argv[1:] + extra_args
                    )
                else:
                    executable = sys.executable
                    params = subprocess.list2cmdline(
                        [script] + sys.argv[1:] + extra_args
                    )

                result = ctypes.windll.shell32.ShellExecuteW(
                    None,
                    "runas",
                    executable,
                    params,
                    cwd,
                    1,
                )

                if result <= 32:
                    raise RuntimeError(
                        "ShellExecuteW Fehlercode: "
                        f"{result}\n"
                        f"Python: {sys.executable}\n"
                        f"Skript: {script}\n"
                        f"Argumente: {params}\n"
                        f"Arbeitsverzeichnis: {cwd}"
                    )

            elif platform.system() == "Linux":
                # Linux: Rechteerhöhung über PolicyKit.
                pkexec = shutil.which("pkexec")

                if not pkexec:
                    QMessageBox.warning(
                        self,
                        "Nicht verfügbar" if is_de else "Unavailable",
                        (
                            "pkexec ist nicht installiert. Bitte installiere "
                            "PolicyKit oder starte das Tool über deine "
                            "Systemverwaltung mit Root-Rechten."
                            if is_de
                            else
                            "pkexec is unavailable. Install PolicyKit or "
                            "start the application with root privileges."
                        ),
                    )
                    return False

                # Für die grafische Sitzung benötigte Variablen übernehmen.
                env_args = []

                for key in (
                    "DISPLAY",
                    "XAUTHORITY",
                    "WAYLAND_DISPLAY",
                    "XDG_RUNTIME_DIR",
                    "QT_QPA_PLATFORM",
                    "DBUS_SESSION_BUS_ADDRESS",
                ):
                    value = os.environ.get(key)
                    if value:
                        env_args.append(f"{key}={value}")

                if getattr(sys, "frozen", False):
                    command = (
                        [sys.executable] + sys.argv[1:] + extra_args
                    )
                else:
                    command = (
                        [sys.executable, script]
                        + sys.argv[1:]
                        + extra_args
                    )

                subprocess.Popen(
                    [pkexec, "env"] + env_args + command,
                    cwd=cwd,
                )

            else:
                QMessageBox.warning(
                    self,
                    "Nicht unterstützt" if is_de else "Unsupported",
                    (
                        "Diese Plattform wird nicht unterstützt."
                        if is_de
                        else "This platform is not supported."
                    ),
                )
                return False

            # Die bisherige GUI erst nach erfolgreichem Anstoßen beenden.
            QTimer.singleShot(500, QApplication.quit)
            return True

        except Exception as exc:
            QMessageBox.critical(
                self,
                "Neustart fehlgeschlagen" if is_de else "Restart failed",
                str(exc),
            )
            return False



    def restart_application(self, *args, **kwargs):
        """Startet die Anwendung neu und zeigt kurz den Neon-Status an."""

        # --- Final Label ausblenden ---
        if hasattr(self, "hide_final_label"):
            self.hide_final_label()
        elif hasattr(self, "final_label") and self.final_label:
            self.final_label.hide()

        import subprocess
        import sys
        import os
        from PyQt6.QtWidgets import QApplication

        # --- Progress-Bar Feedback (Neon-Design) ---
        pbar = getattr(self, "progress_bar", None)
        lang = str(getattr(self, "LANG", "de")).lower()[:2]
        reboot_txt = "🔄 Neustart..." if lang == "de" else "🔄 Restarting..."

        if pbar:
            # Dein spezifisches Stylesheet (Dunkel, 20px, Neon-Gradient)
            rainbow = (
                "qlineargradient(spread:pad, x1:0, y1:0, x2:1, y2:0, "
                "stop:0 #FF00FF, stop:0.5 #00FFFF, stop:1 #39FF14)"
            )
            pbar.setStyleSheet(f"""
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
                    background-color: {rainbow};
                    border-radius: 6px;
                }}
            """)
            pbar.setValue(100)
            pbar.setFormat(reboot_txt)
            pbar.show()
            QApplication.processEvents()

        # Speichern vor dem Exit
        try:
            if hasattr(self, "cfg"):
                self.cfg["language"] = getattr(self, "LANG", "DE").upper()
                if "save_config" in globals():
                    # Zugriff auf die globale save_config Funktion
                    globals()["save_config"](self.cfg)
        except:
            pass

        # Sound abspielen
        # Neustart-Sound
        sound_manager = getattr(self, "sound_manager", None)
        if sound_manager and sound_manager.enabled:
            sound_manager.exit_sound.play()
            QApplication.processEvents()

        # PFAD-FIX für Windows (Leerzeichen in 'Program Files' etc.)
        python = sys.executable
        script = os.path.realpath(sys.argv[0]) # Nutzt sys.argv[0] für den korrekten Skript-Einstiegspunkt
        
        # --- FIX FÜR WINDOWS NEUSTART (Entkopplung des Prozesses) ---
        creation_flags = 0
        if platform.system() == "Windows":
            # Verhindert, dass der neue Prozess an der sterbenden alten CMD-Instanz hängen bleibt
            creation_flags = subprocess.CREATE_NEW_PROCESS_GROUP

        try:
            subprocess.Popen(
                [python, script] + sys.argv[1:],
                cwd=os.path.dirname(script),
                creationflags=creation_flags
            )
        except Exception:
            # Fallback falls die Argumentenliste beschädigt ist
            subprocess.Popen([python, script], cwd=os.path.dirname(script), creationflags=creation_flags)

        # --- DER ENTSCHEIDENDE FIX: Altes GUI-Fenster hart schließen ---
        # Schließt die PyQt6-Schleife
        from PyQt6.QtCore import QEventLoop, QTimer

        if sound_manager and sound_manager.enabled:
            loop = QEventLoop()
            QTimer.singleShot(800, loop.quit)
            loop.exec()

        QApplication.quit()
        sys.exit(0)


    # ===================== ZIP PATCH =====================
    def zip_patch(self, info_widget=None, progress_callback=None):
        """Erstellt ein ZIP des Patches mit Neon-Regenbogen-Progress und Auto-Reset."""

        # --- Final Label ausblenden ---
        if hasattr(self, "hide_final_label"):
            self.hide_final_label()
        elif hasattr(self, "final_label") and self.final_label:
            self.final_label.hide()

        from PyQt6.QtWidgets import QTextEdit, QApplication
        from PyQt6.QtCore import QTimer
        import zipfile
        import os

        # 1. Widget & ProgressBar Setup
        widget = (
            info_widget
            if isinstance(info_widget, QTextEdit)
            else getattr(self, "info_text", None)
        )
        lang = getattr(self, "LANG", "de").lower()[:2]
        is_de = lang == "de"
        pbar = getattr(self, "progress_bar", None)

        # --- Neon-Styles Definition ---
        STYLE_NEON = """
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

        if pbar:
            pbar.setStyleSheet(STYLE_NEON.format(text_color="black", chunk_color=RAINBOW_GRADIENT))
            msg_zip = "Erstelle ZIP..." if is_de else "Creating ZIP..."
            pbar.setFormat(f"📦 {msg_zip} %p%")
            pbar.setValue(10)
            pbar.show()
            QApplication.processEvents()

        # Hilfsfunktionen
        def get_msg(key, default, **kwargs):
            # Annahme: TEXTS ist global definiert
            template = TEXTS.get(lang, {}).get(key, default)
            try: return template.format(**kwargs)
            except: return template

        def play_zip_sound(success=True):
            if "safe_play" in globals():
                safe_play("complete.oga" if success else "dialog-error.oga")

        # --- Eigentliche ZIP-Logik ---
        try:
            patch_file = globals().get("PATCH_FILE", "oscam-emu.patch")
            zip_file_path = globals().get("ZIP_FILE", "oscam-emu.patch.zip")

            if pbar: pbar.setValue(30)

            if not os.path.exists(patch_file):
                msg = get_msg("patch_file_missing", "Datei nicht gefunden: {path}", path=patch_file)
                if hasattr(self, "append_info"): self.append_info(widget, msg, "error")
                play_zip_sound(False)
                if pbar:
                    pbar.setStyleSheet(STYLE_NEON.format(text_color="#FF0000", chunk_color="#800"))
                    pbar.setValue(100)
                return

            if pbar: pbar.setValue(60)

            # Zippen
            with zipfile.ZipFile(zip_file_path, "w", compression=zipfile.ZIP_DEFLATED) as zipf:
                zipf.write(patch_file, os.path.basename(patch_file))

            if pbar: pbar.setValue(90)

            # Erfolgsmeldung
            msg = get_msg("zip_success", "✅ Patch erfolgreich gepackt: {zip_file}", zip_file=zip_file_path)
            if hasattr(self, "append_info"): self.append_info(widget, msg, "success")
            play_zip_sound(True)

            if pbar:
                done_msg = "ZIP erstellt!" if is_de else "ZIP created!"
                pbar.setFormat(f"✅ {done_msg}")

        except Exception as e:
            if pbar:
                pbar.setStyleSheet(STYLE_NEON.format(text_color="#FF0000", chunk_color="#800"))
            msg = get_msg("zip_failed", "❌ Fehler beim Zippen: {error}", error=str(e))
            if hasattr(self, "append_info"): self.append_info(widget, msg, "error")
            play_zip_sound(False)

        # --- ABSCHLUSS & AUTO-RESET ---
        if pbar:
            pbar.setValue(100)
        
            def restore_pbar():
                pbar.setValue(0)
                pbar.setFormat("%p%")
                pbar.setStyleSheet("""
                    QProgressBar { 
                        border: 1px solid #444; border-radius: 8px; background-color: #1A1A1A; 
                        color: black; text-align: center; font-weight: bold; 
                    }
                    QProgressBar::chunk { 
                        background-color: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #F37804, stop:1 #FFD700); 
                        border-radius: 8px; 
                    }
                """)

            QTimer.singleShot(3000, restore_pbar)

        if progress_callback:
            try: progress_callback(100)
            except: pass

        QApplication.processEvents()

    def run_command(self, cmd, cwd=None):
        """Führt Befehle aus und gibt das Ergebnis als String zurück."""
        import subprocess

        try:
            if isinstance(cmd, str):
                import shlex
                command = shlex.split(cmd, posix=(os.name != "nt"))
            else:
                command = list(cmd)

            result = subprocess.run(
                command,
                cwd=cwd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                shell=False,
                check=False,
            )

            stdout = (result.stdout or "").strip()
            stderr = (result.stderr or "").strip()
            if result.returncode != 0:
                # Diagnose enthält Exit-Code und bevorzugt stderr; einige Tools
                # schreiben ihre Fehlermeldung allerdings nach stdout.
                detail = stderr or stdout
                if not detail:
                    detail = (
                        "Der Befehl hat keine weiteren Fehlerdetails ausgegeben."
                        if getattr(self, "current_language", "DE") == "DE"
                        else "The command did not provide additional error details."
                    )
                command_text = " ".join(str(part) for part in command)
                is_de = getattr(self, "current_language", "DE") == "DE"
                if is_de:
                    message = (
                        f"❌ Befehlsfehler (Exit-Code {result.returncode}): "
                        f"{command_text}\n{detail}"
                    )
                    if cwd:
                        message += f"\nArbeitsverzeichnis: {cwd}"
                else:
                    message = (
                        f"❌ Command failed (exit code {result.returncode}): "
                        f"{command_text}\n{detail}"
                    )
                    if cwd:
                        message += f"\nWorking directory: {cwd}"
                self.log(message, "error")
                return ""

            return stdout
        except FileNotFoundError as e:
            is_de = getattr(self, "current_language", "DE") == "DE"
            message = (
                f"❌ Programm/Befehl nicht gefunden: {e}"
                if is_de
                else f"❌ Program/command not found: {e}"
            )
            self.log(message, "error")
            return ""
        except Exception as e:
            is_de = getattr(self, "current_language", "DE") == "DE"
            message = (
                f"❌ Systemfehler beim Ausführen des Befehls: {e}"
                if is_de
                else f"❌ System error while running command: {e}"
            )
            self.log(message, "error")
            return ""

    def log(self, msg, level="info"):
        """
        Gibt Nachrichten im Infoscreen aus.
        Nutzt die vorhandene append_info Logik, um den THEME-Fehler zu vermeiden.
        """
        from PyQt6.QtGui import QTextCursor
        from PyQt6.QtWidgets import QApplication

        # Wir nutzen deine funktionierende append_info statt dem kaputten THEME-Dictionary
        if hasattr(self, "append_info") and hasattr(self, "info_text"):
            self.append_info(self.info_text, msg, level)
        else:
            # Notfall-Fallback falls append_info nicht erreichbar ist
            self.info_text.append(msg)

        # Sicherstellen, dass das Fenster zum Ende scrollt
        self.info_text.moveCursor(QTextCursor.MoveOperation.End)

        # GUI aktualisieren, damit der Text sofort erscheint (wichtig bei Prozessen)
        QApplication.processEvents()

    def commit_value_changed(self, value):
        """Speichert die Commit-Anzahl über den zentralen Config-Manager."""
        try:
            value = max(1, min(int(value), 1000))
            saved = save_config(
                {"commit_count": value},
                gui_instance=self,
                silent=True,
            )
            if not saved:
                raise RuntimeError("Die Konfiguration konnte nicht gespeichert werden.")

            self.append_info(
                self.info_text,
                f"Commit-Anzahl auf {value} gesetzt (gespeichert)",
                "success",
            )
        except Exception as exc:
            self.append_info(
                self.info_text,
                f"❌ Fehler beim Speichern der Commit-Anzahl: {exc}",
                "error",
            )

    def change_old_patch_dir(self, info_widget=None, progress_callback=None):
        """
        Ändert den Speicherort des alten Patch-Ordners mit Neon-Regenbogen-Progress.
        Text bleibt am Ende 3 Sekunden stehen, bevor der Balken zurückgesetzt wird.
        """

        # --- Final Label ausblenden ---
        if hasattr(self, "hide_final_label"):
            self.hide_final_label()
        elif hasattr(self, "final_label") and self.final_label:
            self.final_label.hide()
        
        from PyQt6.QtWidgets import QFileDialog, QApplication
        from PyQt6.QtCore import QTimer
        import json, os

        widget = info_widget or getattr(self, "info_text", None)
        lang = str(getattr(self, "LANG", "de")).lower()[:2]
        is_de = lang == "de"
        pbar = getattr(self, "progress_bar", None)

        # --- Neon-Styles Definition ---
        STYLE_NEON = """
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

        # -------------------------------
        # Helper-Funktionen
        # -------------------------------
        def log(text, level="info"):
            if widget and hasattr(self, "append_info"):
                self.append_info(widget, text, level)

        def set_progress(val, text=None):
            if not pbar: return
            pbar.setStyleSheet(STYLE_NEON.format(text_color="black", chunk_color=RAINBOW_GRADIENT))
            pbar.show()
            pbar.setValue(val)
            if text: pbar.setFormat(text)
            QApplication.processEvents()
            if progress_callback:
                try: progress_callback(val)
                except: pass

        def finalize_pbar(text, visible_seconds=3):
            if not pbar: return
            pbar.setStyleSheet(STYLE_NEON.format(text_color="black", chunk_color=RAINBOW_GRADIENT))
            pbar.setValue(100)
            pbar.setFormat(text)
        
            # Nach 3 Sekunden Chunk transparent, Value auf 0
            QTimer.singleShot(visible_seconds * 1000, lambda: pbar.setStyleSheet(
                STYLE_NEON.format(text_color="black", chunk_color="transparent")
            ))
            QTimer.singleShot(visible_seconds * 1000, lambda: pbar.setValue(0))
            if progress_callback:
                QTimer.singleShot(visible_seconds * 1000, lambda: progress_callback(0))

        # -------------------------------
        # START
        # -------------------------------
        if pbar:
            set_progress(10, "📂 " + ("Ordner auswählen..." if is_de else "Select folder..."))

        # Nutzt Standard-Pfad falls OLD_PATCH_DIR nicht gesetzt
        start_dir = getattr(self, "OLD_PATCH_DIR", os.getcwd())
        new_dir = QFileDialog.getExistingDirectory(self, "Select S3 Patch Folder", start_dir)

        if new_dir:
            if pbar:
                set_progress(50, "🔄 " + ("Pfade aktualisieren..." if is_de else "Updating paths..."))
        
            # Pfade aktualisieren
            self.OLD_PATCH_DIR = new_dir
            self.OLD_PATCH_FILE = os.path.join(new_dir, "oscam-emu.patch")
            self.ALT_PATCH_FILE = os.path.join(new_dir, "oscam-emu.altpatch")
            self.PATCH_MANAGER_OLD = os.path.join(new_dir, "oscam_patch_manager_old.py")
            self.CONFIG_OLD = os.path.join(new_dir, "config_old.json")
            self.GITHUB_CONFIG_OLD = os.path.join(new_dir, "github_upload_config_old.json")

            # Config speichern
            if hasattr(self, "cfg"):
                self.cfg["s3_patch_path"] = new_dir
                try:
                    with open("config.json", "w", encoding="utf-8") as f:
                        json.dump(self.cfg, f, indent=2)

                    success_msg = f"✅ Ordner geändert: {new_dir}" if is_de else f"✅ Folder changed: {new_dir}"
                    log(success_msg, "success")
                    if pbar:
                        finalize_pbar("✅ " + ("Gespeichert!" if is_de else "Saved!"))
                    if "safe_play" in globals():
                        safe_play("complete.oga")

                except Exception as e:
                    log(f"❌ Fehler: {e}", "error")
                    if pbar:
                        finalize_pbar("❌ " + str(e))
                    if "safe_play" in globals():
                        safe_play("dialog-error.oga")
        else:
            log("ℹ️ Abgebrochen" if is_de else "ℹ️ Cancelled", "info")
            if pbar:
                finalize_pbar("ℹ️ " + ("Abgebrochen" if is_de else "Cancelled"))

    
    
    def update_ui_texts(self):
        """Aktualisiert Texte, Icons und Tooltips zentral, ohne unnötig Styles anzuhängen."""
        import json
        import os
        import platform

        from PyQt6.QtCore import QSize, QTimer
        from PyQt6.QtWidgets import QApplication, QStyle

        # --------------------------------------------------
        # 1. Abschlussmeldung ausblenden und Sprache bestimmen
        # --------------------------------------------------
        try:
            hide_final = getattr(self, "hide_final_label", None)
            if callable(hide_final):
                hide_final()
            else:
                final_label = getattr(self, "final_label", None)
                if final_label is not None:
                    final_label.hide()
        except Exception as exc:
            print(f"[update_ui_texts] Abschlusslabel konnte nicht ausgeblendet werden: {exc}")

        lang = str(getattr(self, "LANG", "de")).lower()[:2]
        is_de = lang == "de"
        pbar = getattr(self, "progress_bar", None)

        def tr(de_text, en_text):
            return de_text if is_de else en_text

        def apply_button_style(btn, color="#00FFFF", tooltip_color=None):
            """Setzt einen vollständigen, wiederholbar anwendbaren Button-Style."""
            if btn is None:
                return
            tip_color = tooltip_color or color
            btn.setStyleSheet(
                f"""
                QPushButton {{
                    text-align: left;
                    padding-left: 8px;
                    font-weight: bold;
                    color: {color};
                    background-color: #3d3d3d;
                    border: 1px solid {color};
                    border-radius: 8px;
                }}
                QPushButton:hover {{
                    background-color: {color};
                    color: black;
                }}

                QToolTip {{
                    background-color: #202833;
                    color: #EAF6FF;
                    border: 1px solid #00B8D4;
                    border-radius: 5px;
                    padding: 5px;
                    font-size: 10pt;
                }}
                """
            )

        def apply_final_style(btn, text, standard_pixmap):
            if btn is None:
                return
            clean_text = str(text).strip()
            if len(clean_text) > 22 and " " in clean_text:
                clean_text = clean_text.replace(" ", "\n", 1)

            btn.setText(clean_text)
            btn.setIcon(QApplication.style().standardIcon(standard_pixmap))
            btn.setIconSize(QSize(20, 20))
            btn.setMinimumSize(200, 50)
            apply_button_style(btn, "#00FFFF")

        # --------------------------------------------------
        # 2. ProgressBar starten
        # --------------------------------------------------
        if pbar is not None:
            pbar.setValue(20)
            pbar.show()

        # --------------------------------------------------
        # 3. Labels aktualisieren
        # --------------------------------------------------
        labels = [
            ("commit_label", "Anzahl Commits:", "Commit Count:"),
            ("color_label", "Farbe:", "Color:"),
            ("lang_label", "🌐 Sprache:", "🌐 Language:"),
        ]
        for attr, de_text, en_text in labels:
            label = getattr(self, attr, None)
            if label is not None:
                label.setText(tr(de_text, en_text))

        # --------------------------------------------------
        # 4. Konfiguration laden und Pfade übernehmen
        # --------------------------------------------------
        is_win = platform.system() == "Windows"
        config_file = globals().get("CONFIG_FILE", "config.json")
        file_cfg = {}

        if os.path.isfile(config_file):
            try:
                with open(config_file, "r", encoding="utf-8") as handle:
                    loaded_cfg = json.load(handle)
                if isinstance(loaded_cfg, dict):
                    file_cfg = loaded_cfg
                else:
                    print("[update_ui_texts] config.json enthält kein JSON-Objekt.")
            except (OSError, json.JSONDecodeError) as exc:
                print(f"[update_ui_texts] Konfiguration konnte nicht geladen werden: {exc}")

        s3_path = file_cfg.get(
            "s3_custom_path",
            getattr(self, "S3_PATH", r"C:\s3" if is_win else "/opt/s3"),
        )
        s4_path = file_cfg.get(
            "s4_custom_path",
            getattr(self, "S4_PATH", r"C:\opt\simplebuild4" if is_win else "/opt/simplebuild4"),
        )
        ncam_path = file_cfg.get(
            "ncam_custom_path",
            getattr(self, "NCAM_PATH", r"C:\opt\ncam" if is_win else "/opt/s3_ncam_bonecrew"),
        )

        self.S3_PATH = os.path.normpath(os.path.expanduser(str(s3_path)))
        self.S4_PATH = os.path.normpath(os.path.expanduser(str(s4_path)))
        self.NCAM_PATH = os.path.normpath(os.path.expanduser(str(ncam_path)))
        self.current_config = file_cfg
        self.cfg = file_cfg

        # --------------------------------------------------
        # 5. S3 / S4 / NCam: Installation erkennen und Tooltips setzen
        # --------------------------------------------------
        def apply_tool_button(btn, current_path, name):
            if btn is None:
                return

            sub_map = {
                "S3": "s3",
                "NCam": "s3_ncam_bonecrew",
                "S4": "simplebuild4",
            }
            sub_folder = sub_map[name]

            if name == "S4":
                possible_names = (
                    "simplebuild", "simplebuild.exe", "s4", "s4.exe",
                    "simplebuild4", "simplebuild4.exe",
                )
            elif name == "NCam":
                possible_names = (
                    "ncam", "ncam.exe", "oscam", "oscam.exe", "s3", "s3.exe",
                )
            else:
                possible_names = ("s3", "s3.exe")

            base = os.path.normpath(os.path.expanduser(str(current_path)))
            exists = False
            for filename in possible_names:
                direct_path = os.path.join(base, filename)
                nested_path = ""
                if os.path.basename(base).lower() != sub_folder.lower():
                    nested_path = os.path.join(base, sub_folder, filename)
                if os.path.isfile(direct_path) or (
                    nested_path and os.path.isfile(nested_path)
                ):
                    exists = True
                    break

            if exists:
                label = f"🚀 {name} OK"
                color = "#2ecc71"
                tip = tr(
                    f"<b>Linksklick:</b> {name}-Menü öffnen<br><b>Rechtsklick:</b> Pfad ändern",
                    f"<b>Left-click:</b> Open {name} menu<br><b>Right-click:</b> Change path",
                )
            else:
                label = f"🚀 {name} {tr('Installieren', 'Install')}"
                color = "#f39c12"
                tip = tr(
                    f"<b>Linksklick:</b> {name}-Installation starten<br><b>Rechtsklick:</b> Ordner wählen",
                    f"<b>Left-click:</b> Start {name} setup<br><b>Right-click:</b> Select folder",
                )

            btn.setText(label)
            btn.setToolTip(tip)
            btn.setToolTipDuration(10000)
            btn.setMinimumSize(320, 50)
            btn._original_text = label
            apply_button_style(btn, color)

        apply_tool_button(getattr(self, "btn_s3", None), self.S3_PATH, "S3")
        apply_tool_button(getattr(self, "btn_s4", None), self.S4_PATH, "S4")
        apply_tool_button(getattr(self, "btn_ncam", None), self.NCAM_PATH, "NCam")

        # --------------------------------------------------
        # 6. Standard-Buttons und Tooltips
        # --------------------------------------------------
        mapping = [
            # 1. Arbeitsordner und lokale Repositories
            (getattr(self, "btn_open_work", None), "Arbeitsordner", "Work Folder",
             QStyle.StandardPixmap.SP_DirIcon,
             "Öffnet den lokalen Patch-Ordner",
             "Opens the local patch folder"),

            (getattr(self, "btn_open_temp", None), "Temp Git Streamboard", "Temp Git Streamboard",
             QStyle.StandardPixmap.SP_DirIcon,
             "Zeigt den lokalen Git-Clone",
             "Shows the local Git clone"),

            (getattr(self, "btn_open_emu", None), "Emu Git Temp", "Emu Git Temp",
             QStyle.StandardPixmap.SP_DirIcon,
             "Öffnet das Repository im Browser",
             "Opens the repository in the browser"),

            # 2. Umgebung prüfen
            (getattr(self, "btn_check_tools", None), "Tools prüfen", "Check Tools",
             QStyle.StandardPixmap.SP_ComputerIcon,
             "Prüft Compiler und Abhängigkeiten",
             "Checks compilers and dependencies"),

            # 3. Repository konfigurieren
            (getattr(self, "btn_repo_url", None), "Oscam Emu URL", "Oscam Emu URL",
             QStyle.StandardPixmap.SP_DriveNetIcon,
             "Ändert die Git-Repository-URL",
             "Changes the Git repository URL"),

            (getattr(self, "btn_modifier", None), "Patch Autor", "Patch Author",
             QStyle.StandardPixmap.SP_FileDialogDetailedView,
             "Ändert den Namen des Patch-Autors",
             "Changes the patch author's name"),

            # 4. Änderungen und Updates prüfen
            (getattr(self, "btn_check_commit", None), "Commit-Prüfung", "Commit Check",
             QStyle.StandardPixmap.SP_BrowserReload,
             "Prüft online auf Updates",
             "Checks online for updates"),

            
            # 5. Dateiberechtigungen reparieren
            (
            getattr(self, "btn_fix_perms", None),
            "Rechte reparieren" if self.LANG == "de"
            else "Fix Permissions",
            "Fix Permissions",
            QStyle.StandardPixmap.SP_DialogNoButton,
            "🔐 <b>Rechte:</b> Setzt Schreibrechte für Tool-Dateien und Ordner neu.",
            "🔐 <b>Permissions:</b> Resets write permissions for tool files and folders.",
        ),
        ]
        for btn, de_text, en_text, icon, de_tip, en_tip in mapping:
            if btn is not None:
                apply_final_style(btn, tr(de_text, en_text), icon)
                btn.setToolTip(tr(de_tip, en_tip))
                btn.setToolTipDuration(10000)


        
        # --------------------------------------------------
        # 7. Grid-Buttons
        # --------------------------------------------------
        if isinstance(getattr(self, "buttons", None), dict):
            # Logische Reihenfolge der Patch-Funktionen
            grid_icons = {
                # 1. Patch erstellen und bearbeiten
                "patch_create": QStyle.StandardPixmap.SP_FileIcon,
                "patch_renew": QStyle.StandardPixmap.SP_BrowserReload,

                # 2. Patch prüfen und anwenden
                "patch_check": QStyle.StandardPixmap.SP_DialogApplyButton,
                "patch_apply": QStyle.StandardPixmap.SP_MediaPlay,

                # 3. Patch exportieren und sichern
                "patch_zip": QStyle.StandardPixmap.SP_DriveFDIcon,
                "backup_old": QStyle.StandardPixmap.SP_DialogSaveButton,

                # 4. Verzeichnisse verwalten
                "change_old_dir": QStyle.StandardPixmap.SP_DirOpenIcon,
                "clean_folder": QStyle.StandardPixmap.SP_TrashIcon,

                # 5. Programm beenden
                "exit": QStyle.StandardPixmap.SP_DialogCloseButton,
            }

            get_t = getattr(self, "get_t", None)

            for key, btn in self.buttons.items():
                if btn is None or key not in grid_icons:
                    continue

                try:
                    text = get_t(key, key) if callable(get_t) else key
                except Exception as exc:
                    print(
                        f"[update_ui_texts] Übersetzung für {key} fehlgeschlagen: {exc}"
                    )
                    text = key

                apply_final_style(btn, text, grid_icons[key])


        # --------------------------------------------------
        # 8. Matrix-Button
        # --------------------------------------------------
        matrix_btn = getattr(self, "btn_matrix", None)
        if matrix_btn is not None:
            matrix_btn.setText(tr("🔙 MATRIX Beenden", "🔙 EXIT MATRIX"))
            matrix_btn.setMinimumSize(220, 50)
            matrix_btn.setToolTip(
                tr("Verlässt den Matrix-Modus.", "Exits Matrix mode.")
            )
            matrix_btn.setToolTipDuration(10000)
            apply_button_style(matrix_btn, "#00FFFF")

        # --------------------------------------------------
        # 9. Theme auswählen und anwenden
        # --------------------------------------------------
        theme_mode = str(file_cfg.get("theme_mode", "standard")).lower()
        theme_color = str(file_cfg.get("color", "Classics")).lower()
        is_matrix = "matrix" in theme_mode or "matrix" in theme_color

        theme_method = getattr(
            self,
            "enable_matrix_theme" if is_matrix else "enable_standard_theme",
            None,
        )
        if callable(theme_method):
            try:
                theme_method()
            except Exception as exc:
                print(f"[update_ui_texts] Theme konnte nicht angewendet werden: {exc}")

        # --------------------------------------------------
        # 10. Info-Button-Tooltip mit Theme-Farben
        # --------------------------------------------------
        info_btn = getattr(self, "info_button", None) or getattr(self, "btn_info", None)
        if info_btn is not None:
            info_btn.setToolTip(
                tr(
                    "Zeigt Informationen über das OSCam Toolkit an.",
                    "Displays information about the OSCam Toolkit.",
                )
            )
            info_btn.setToolTipDuration(10000)

            if is_matrix:
                tooltip_bg, tooltip_fg, tooltip_border = "#101010", "#00FF00", "#00FF00"
            elif "blue" in theme_color or "blau" in theme_color:
                tooltip_bg, tooltip_fg, tooltip_border = "#202A44", "#FFFFFF", "#4488FF"
            elif "red" in theme_color or "rot" in theme_color:
                tooltip_bg, tooltip_fg, tooltip_border = "#351818", "#FFFFFF", "#FF4444"
            elif "purple" in theme_color or "lila" in theme_color:
                tooltip_bg, tooltip_fg, tooltip_border = "#291A35", "#FFFFFF", "#BB77FF"
            elif "green" in theme_color or "grün" in theme_color:
                tooltip_bg, tooltip_fg, tooltip_border = "#18301F", "#FFFFFF", "#44CC66"
            else:
                tooltip_bg, tooltip_fg, tooltip_border = "#303030", "#FFFFFF", "#777777"

            # Nur den Tooltip-Stil ergänzen; nicht bei jedem Aufruf erneut anhängen.
            info_btn.setStyleSheet(
                f"""
                QToolTip {{
                    background-color: {tooltip_bg};
                    color: {tooltip_fg};
                    border: 1px solid {tooltip_border};
                    border-radius: 5px;
                    padding: 6px;
                    font-size: 10pt;
                }}
                """
            )

        # --------------------------------------------------
        # 11. Button-Text-Fit und ProgressBar abschließen
        # --------------------------------------------------
        fit_buttons = getattr(self, "_fit_all_button_texts", None)
        if callable(fit_buttons):
            QTimer.singleShot(0, fit_buttons)
            QTimer.singleShot(200, fit_buttons)

        if pbar is not None:
            pbar.setValue(100)
            idle_callback = getattr(self, "pbar_idle", None)
            if callable(idle_callback):
                QTimer.singleShot(2000, idle_callback)
            else:
                QTimer.singleShot(2000, pbar.hide)

        QApplication.processEvents()




    def edit_patch_header(self, info_widget=None, progress_callback=None):
        """Öffnet den Header-Editor mit Neon-Regenbogen-Progress, Sound und Sprach-Support."""

        # --- Final Label ausblenden ---
        if hasattr(self, "hide_final_label"):
            self.hide_final_label()
        elif hasattr(self, "final_label") and self.final_label:
            self.final_label.hide()

        import os
        from PyQt6.QtWidgets import (
            QDialog,
            QVBoxLayout,
            QTextEdit,
            QDialogButtonBox,
            QApplication,
        )
        from PyQt6.QtCore import QTimer

        # 1. Sprache & Progress Setup
        lang = str(getattr(self, "LANG", "de")).lower()[:2]
        is_de = lang == "de"
        pbar = getattr(self, "progress_bar", None)

        # Lokalisierte Texte
        T_LOADING = "Editor wird geladen..." if is_de else "Loading editor..."
        T_SAVED = "Patch gespeichert!" if is_de else "Patch saved!"
        T_CANCEL = "Abgebrochen" if is_de else "Cancelled"
        T_TITLE = "Patch-Header Editor"

        # --- Neon-Styles Definition ---
        STYLE_NEON = """
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

        # --- SOUND BEIM ÖFFNEN ---
        if "safe_play" in globals():
            safe_play("service-login.oga")

        if pbar:
            pbar.setStyleSheet(STYLE_NEON.format(text_color="black", chunk_color=RAINBOW_GRADIENT))
            pbar.setFormat(f"📝 {T_LOADING}")
            pbar.setValue(20)
            pbar.show()
            QApplication.processEvents()

        # Pfad zur Datei sicherstellen
        base = getattr(self, "base_dir", os.getcwd())
        p_file = os.path.join(base, "oscam-emu.patch")

        if not os.path.exists(p_file):
            if pbar:
                pbar.setStyleSheet(STYLE_NEON.format(text_color="#FF0000", chunk_color="#800"))
                pbar.setFormat("❌ Datei fehlt!" if is_de else "❌ File missing!")
                pbar.setValue(100)
            if "safe_play" in globals():
                safe_play("dialog-error.oga")
            return

        # 2. Dialog erstellen
        dlg = QDialog(self)
        dlg.setWindowTitle(T_TITLE)
        dlg.resize(900, 700)
        ly = QVBoxLayout(dlg)

        edit = QTextEdit()
        # Matrix-Style: Schwarz mit grüner Schrift
        edit.setStyleSheet(
            "background-color: #000; color: #39FF14; font-family: monospace; font-size: 11pt; border: 1px solid #333;"
        )

        try:
            with open(p_file, "r", encoding="utf-8", errors="ignore") as f:
                edit.setPlainText(f.read())
            if pbar:
                pbar.setValue(60)
        except Exception:
            if pbar: pbar.setValue(0)
            return

        ly.addWidget(edit)

        # --- BUTTONS ---
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        btn_save = bb.button(QDialogButtonBox.StandardButton.Save)
        if btn_save: btn_save.setText("Speichern" if is_de else "Save")
        btn_cancel = bb.button(QDialogButtonBox.StandardButton.Cancel)
        if btn_cancel: btn_cancel.setText("Abbrechen" if is_de else "Cancel")

        bb.accepted.connect(dlg.accept)
        bb.rejected.connect(dlg.reject)
        ly.addWidget(bb)

        if pbar: pbar.setValue(90)

        # 3. Dialog ausführen
        result = dlg.exec()

        if result == QDialog.DialogCode.Accepted:
            try:
                if "safe_play" in globals(): safe_play("complete.oga")

                content = edit.toPlainText()
                with open(p_file, "w", encoding="utf-8") as f:
                    f.write(content)

                if pbar:
                    pbar.setValue(100)
                    pbar.setFormat(f"✅ {T_SAVED}")
            except Exception:
                if "safe_play" in globals(): safe_play("dialog-error.oga")
                if pbar:
                    pbar.setStyleSheet(STYLE_NEON.format(text_color="#FF0000", chunk_color="#800"))
        else:
            if "safe_play" in globals(): safe_play("dialog-error.oga")
            if pbar:
                pbar.setValue(0)
                pbar.setFormat(f"↩️ {T_CANCEL}")

        # 4. AUTO-RESET (nach 3 Sekunden zurück zum Standard Orange/Gold)
        def restore_pbar():
            if pbar:
                pbar.setValue(0)
                pbar.setFormat("%p%")
                pbar.setStyleSheet(
                    """
                    QProgressBar { 
                        border: 1px solid #444; border-radius: 8px; background-color: #1A1A1A; 
                        color: black; text-align: center; font-weight: bold; 
                    }
                    QProgressBar::chunk { 
                        background-color: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #F37804, stop:1 #FFD700); 
                        border-radius: 8px; 
                    }
                """
                )

        QTimer.singleShot(3000, restore_pbar)
        if progress_callback: progress_callback(100)

        # ---------------------
        # PLUGIN UPDATE
        # ---------------------

    def update_plugin_button_state(self):
        """Aktualisiert den Text und die Farbe des Update-Buttons."""
        btn_data = self.option_buttons.get("plugin_update")
        if not btn_data:
            return
        btn = btn_data[0]

        lang = str(getattr(self, "LANG", "de")).lower()
        t = TEXTS.get(lang, TEXTS.get("de", {}))

        cv = APP_VERSION.lstrip("v")
        lv = getattr(self, "latest_version", cv)

        if Version(lv) > Version(cv):
            txt = t.get("state_plugin_update_available", "Update: {current} → {latest}")
            btn.setText(txt.format(current=cv, latest=lv))
            btn.setStyleSheet(
                "background-color: #d35400; color: white; font-weight: bold;"
            )
        else:
            txt = t.get("state_plugin_uptodate", "v{version} aktuell")
            btn.setText(txt.format(version=cv))
            btn.setStyleSheet(
                "background-color: #1E90FF; color: white; font-weight: bold;"
            )

    def fetch_latest_version(self):
        """Vergleicht lokale APP_VERSION mit version.txt auf GitHub"""
        import requests
        from packaging.version import Version

        widget = getattr(self, "info_text", None)

        def log(text_key, level="info", **kwargs):
            colors = {
                "success": "green",
                "warning": "orange",
                "error": "red",
                "info": "gray",
            }
            color = colors.get(level, "gray")
            text_template = TEXTS[LANG].get(text_key, text_key)
            text = text_template.format(**kwargs)

            if isinstance(widget, QTextEdit):
                widget.append(f'<span style="color:{color}">{text}</span>')
                widget.moveCursor(QTextCursor.MoveOperation.End)
                QApplication.processEvents()
            else:
                print(f"[{level.upper()}] {text}")

        try:
            url = "https://raw.githubusercontent.com/speedy005/oscam_patch_manager_new/master/version.txt"
            resp = requests.get(url, timeout=10)
            resp.raise_for_status()

            latest = resp.text.strip()
            self.latest_version = latest

            if Version(latest) > Version(APP_VERSION):
                log("github_version_available", "info", version=latest)
                self.ask_for_update(latest)  # 👈 wichtig
            else:
                log("update_current_version", "success", version=APP_VERSION)

        except Exception as e:
            log("github_version_fetch_failed", "warning", error=str(e))

    def fetch_latest_github_version(self):
        """
        Holt die neueste Version von GitHub (version.txt) und aktualisiert self.latest_version.
        """
        try:
            import requests

            url = "https://raw.githubusercontent.com/speedy005/oscam_patch_manager_new/master/version.txt"
            resp = requests.get(url, timeout=10)
            resp.raise_for_status()
            version = resp.text.strip().lstrip("v")  # "v1.4.7" → "1.4.7"
            self.latest_version = version

            self.append_info(
                self.info_text, f"Verfügbare GitHub-Version: v{version}", "info"
            )

            # Button-Zustand aktualisieren
            self.update_plugin_button_state()
            return version

        except Exception as e:
            self.append_info(
                self.info_text,
                f"❌ Fehler beim Abrufen der GitHub-Version: {e}",
                "error",
            )
            self.latest_version = None
            self.update_plugin_button_state()
            return None

    def plugin_update_button_clicked(
        self, checked=False, info_widget=None, progress_callback=None
    ):
        """
        Geprüfter Update-Callback mit Regenbogen-Progress und schwarzem Text (DE/EN).
        Zentrierte Log-Ausgabe und korrekte URL-Verarbeitung.
        Erzwingt Ja/Nein Buttons passend zur gewählten Sprache.
        """

        # --- Final Label sofort ausblenden ---
        if hasattr(self, "hide_final_label"):
            self.hide_final_label()
        elif hasattr(self, "final_label") and self.final_label:
            self.final_label.hide()
        from PyQt6.QtWidgets import QTextEdit, QApplication, QMessageBox
        from PyQt6.QtGui import QTextCursor
        import requests, time, re

        # --- 1. SPRACHE & PROGRESSBAR SETUP ---
        lang_key = getattr(self, "LANG", "de").lower()
        is_de = lang_key == "de"
        widget = info_widget or getattr(self, "info_text", None)
        pbar = getattr(self, "progress_bar", None)

        if pbar:
            rainbow = (
                "qlineargradient(x1:0, y1:0, x2:1, y2:0, "
                "stop:0.0 #FF0000, stop:0.2 #FF7F00, stop:0.4 #FFFF00, "
                "stop:0.6 #00FF00, stop:0.8 #0000FF, stop:1.0 #8B00FF);"
            )
            pbar.setStyleSheet(
                f"""
                QProgressBar {{
                    text-align: center; font-weight: bold; border: 2px solid #222;
                    border-radius: 6px; background-color: #111; color: black; font-size: 11pt;
                }}
                QProgressBar::chunk {{ background-color: {rainbow}; border-radius: 4px; }}
                """
            )
            msg_check = "Prüfe auf Updates..." if is_de else "Checking for updates..."
            pbar.setFormat(f"{msg_check} %p%")
            pbar.setValue(0)
            pbar.show()
            QApplication.processEvents()

        # Version-Klasse (Fallback)
        try:
            from packaging.version import Version
        except ImportError:

            class Version:
                def __init__(self, v):
                    self.v = [int(x) for x in re.findall(r"\d+", str(v))]

                def __gt__(self, other):
                    return self.v > other.v

                def __lt__(self, other):
                    return self.v < other.v

                def __eq__(self, other):
                    return self.v == other.v

        def log(text_key, level="info", **kwargs):
            colors = {
                "success": "#00FF00",
                "warning": "orange",
                "error": "red",
                "info": "#00ADFF",
            }
            color = colors.get(level, "gray")
            lang_dict = (
                globals()
                .get("TEXTS", {})
                .get(lang_key, globals().get("TEXTS", {}).get("en", {}))
            )
            text_template = lang_dict.get(text_key, text_key)

            try:
                safe_kwargs = {
                    "current": globals().get("APP_VERSION", "3.3.7"),
                    "latest": getattr(self, "latest_version", "???"),
                }
                safe_kwargs.update(kwargs)
                text = text_template.format(**safe_kwargs)
            except:
                text = text_template

            if isinstance(widget, QTextEdit):
                widget.append(
                    f'<div style="text-align:center;"><span style="color:{color}"><b>{text}</b></span></div>'
                )
                widget.moveCursor(QTextCursor.MoveOperation.End)
                QApplication.processEvents()

        log("update_check_start", "info")
        if pbar:
            pbar.setValue(20)

        # --- 2. URL & CHECK ---
        version_url_base = "https://raw.githubusercontent.com/speedy005/oscam_patch_manager_new/refs/heads/master/version.txt"

        try:
            v_url = f"{version_url_base}?nocache={int(time.time())}"
            headers = {"User-Agent": "Mozilla/5.0"}
            resp = requests.get(v_url, headers=headers, timeout=10)
            resp.raise_for_status()

            latest_str = resp.text.strip().lower().replace("v", "").strip()
            current_str = (
                globals()
                .get("APP_VERSION", "3.3.7")
                .strip()
                .lower()
                .replace("v", "")
                .strip()
            )

            self.latest_version = latest_str
            v_latest = Version(latest_str)
            v_current = Version(current_str)

            if pbar:
                pbar.setValue(60)

            # --- 3. VERGLEICH & MESSAGEBOX ---
            if v_latest > v_current:
                log("update_found", "warning", latest=latest_str, current=current_str)

                if pbar:
                    pbar.setValue(80)
                    msg_up = "Update verfügbar!" if is_de else "Update available!"
                    pbar.setFormat(f"✨ {msg_up} (v{latest_str})")

                lang_dict = globals().get("TEXTS", {}).get(lang_key, {})
                msg_title = lang_dict.get("update_box_title", "Update")
                msg_body = lang_dict.get("update_box_msg", "Update?").format(
                    latest=latest_str, current=current_str
                )

                msg_box = QMessageBox(self)
                msg_box.setWindowTitle(msg_title)
                msg_box.setText(msg_body)

                # Buttons manuell setzen um "Yes/No" (Systemsprache) zu umgehen
                yes_text, no_text = ("Ja", "Nein") if is_de else ("Yes", "No")
                yes_button = msg_box.addButton(yes_text, QMessageBox.ButtonRole.YesRole)
                no_button = msg_box.addButton(no_text, QMessageBox.ButtonRole.NoRole)

                msg_box.setDefaultButton(yes_button)
                msg_box.setEscapeButton(no_button)

                msg_box.exec()

                if msg_box.clickedButton() == yes_button:
                    if hasattr(self, "plugin_update_action"):
                        self.plugin_update_action(latest_version=latest_str)
                else:
                    log("update_declined", "info")
                    if pbar:
                        pbar.setValue(100)
            else:
                # Aktuell
                if pbar:
                    pbar.setFormat(
                        f"✅ v{current_str} aktuell 100%"
                        if is_de
                        else f"✅ v{current_str} up-to-date 100%"
                    )
                    pbar.setValue(100)
                log("update_no_update", "success")

        except Exception as e:
            error_label = "Fehler" if is_de else "Error"
            log("update_fail", "error", error=str(e))
            if pbar:
                pbar.setStyleSheet("QProgressBar { color: red; font-weight: bold; }")
                pbar.setFormat(f"❌ {error_label}: {str(e)[:50]}")
                pbar.setValue(100)

        # Timer zum Aufräumen
        from PyQt6.QtCore import QTimer

        QTimer.singleShot(
            4000,
            lambda: (
                self.pbar_idle()
                if hasattr(self, "pbar_idle")
                else (pbar.hide() if pbar else None)
            ),
        )

        # UI Color Fix für Hover-Effekte
        if hasattr(self, "repaint_ui_colors"):
            self.repaint_ui_colors()

    # ---------------------
    # UPDATE CHECK
    # ---------------------
    def check_for_update_on_start(self):
        """
        Vervollständigt den Log voll übersetzbar.
        Finalisiert den System-Check mit der korrekten Konfiguration aus der JSON.
        """
        if getattr(self, "_update_dialog_active", False):
            return
        self._update_dialog_active = True

        import requests
        import time

        # Nutzen der im Skript definierten Version-Klasse als Fallback
        try:
            from packaging.version import Version
        except ImportError:
            Version = globals().get("Version")

        txt = getattr(self, "TEXT", {})

        # --- ZENTRALE FORMATIERUNG ---
        SZ_BIG = "35px"  # Orange Titel
        SZ_NORM = "35px"  # Standard-Inhalte
        F_FAMILY = "'Arial', sans-serif"

        C_ORANGE = "#F37804"
        C_GREEN = "#00FF00"
        C_BLUE = "#00ADFF"
        C_LINE = "#808080"

        v_url = "https://raw.githubusercontent.com/speedy005/oscam_patch_manager_new/master/version.txt"

        try:
            # Cache-Busting durch Timestamp
            resp = requests.get(f"{v_url}?t={int(time.time())}", timeout=5)
            resp.raise_for_status()

            latest_v = resp.text.strip().lstrip("v")
            current_v = APP_VERSION.strip().lstrip("v")

            output = []

            # --- LOGIK: VERSION VERGLEICHEN ---
            is_new = False
            try:
                is_new = Version(latest_v) > Version(current_v)
            except Exception:
                is_new = latest_v != current_v

            if is_new:
                # --- FALL: UPDATE VERFÜGBAR ---
                label = txt.get("new_version_available", "Update verfügbar")
                output.append(
                    f'<span style="font-family:{F_FAMILY}; font-size:{SZ_BIG}; color:{C_ORANGE}"><b>➔ {label}: v{latest_v}</b></span>'
                )

                if hasattr(self, "btn_update"):
                    btn_tpl = txt.get("upd_btn_new", "Update verfügbar: v{v}")
                    btn_txt = btn_tpl.format(v=latest_v, latest=latest_v)
                    self.btn_update.setText(btn_txt)
                    # WICHTIG: Farbe setzen, aber Größe stabil halten (Fixed-Policy vorausgesetzt)
                    self.btn_update.setStyleSheet(
                        f"color: {C_ORANGE}; font-weight: bold; border: 1px solid {C_ORANGE}; border-radius: 5px;"
                    )
            else:
                # --- FALL: KEIN UPDATE ---
                output.append(
                    f'<span style="font-family:{F_FAMILY}; font-size:{SZ_NORM}; color:{C_GREEN}"><b>{txt.get("no_update_found", "Kein Update vorhanden....")}</b></span>'
                )

                upd_ok_msg = txt.get("upd_ok", "Plugin ist aktuell")
                output.append(
                    f'<span style="font-family:{F_FAMILY}; font-size:{SZ_NORM}; color:{C_BLUE}"><b>✅ {upd_ok_msg} (Version: {current_v})</b></span>'
                )

                if hasattr(self, "btn_update"):
                    btn_curr_tpl = txt.get("upd_btn_current", "v{v} (Aktuell)")
                    # FIX: Benutze current_v statt der undefinierten current_version
                    btn_curr_txt = btn_curr_tpl.format(v=current_v, current=current_v)
                    self.btn_update.setText(btn_curr_txt)
                    self.btn_update.setStyleSheet(
                        "color: #00FF00; font-weight: bold; border: 1px solid #00FF00; border-radius: 5px;"
                    )

            # --- KONFIGURATIONS-BLOCK ---
            output.append(f'<span style="color:{C_LINE}">{"-" * 45}</span>')
            output.append(
                f'<span style="font-family:{F_FAMILY}; font-size:{SZ_BIG}; color:{C_ORANGE}"><b>{txt.get("active_conf", "🛠️ Aktive Konfiguration:")}</b></span>'
            )

            # Sicherer Zugriff auf Config
            cfg_obj = getattr(self, "cfg", {})
            author = cfg_obj.get(
                "patch_modifier", getattr(self, "patch_modifier", "speedy005")
            )
            repo_url = cfg_obj.get(
                "EMUREPO",
                getattr(self, "EMUREPO", "https://github.com/speedy005/Oscam-emu.git"),
            )

            output.append(
                f'<span style="font-family:{F_FAMILY}; font-size:{SZ_NORM}; color:{C_GREEN}"><b>  👤 {txt.get("patch_author", "Patch Autor")}: {author}</b></span>'
            )
            output.append(
                f'<span style="font-family:{F_FAMILY}; font-size:{SZ_NORM}; color:{C_BLUE}"><b>  🌐 {txt.get("repository", "Repository")}: {repo_url}</b></span>'
            )
            output.append(f'<span style="color:{C_LINE}">{"-" * 45}</span>')

            # Log ausgeben
            if hasattr(self, "append_info") and hasattr(self, "info_text"):
                self.append_info(self.info_text, "\n".join(output), "raw")

            # Abschluss-Sound
            if "safe_play" in globals():
                safe_play("complete.oga")

            if is_new:
                self.show_update_dialog(latest_v, current_v)

        except Exception as e:
            if "safe_play" in globals():
                safe_play("dialog-error.oga")
            err_lbl = txt.get("update_error", "Update-Check fehlgeschlagen")
            if hasattr(self, "append_info"):
                self.append_info(
                    self.info_text,
                    f'<span style="font-family:{F_FAMILY}; font-size:{SZ_NORM}; color:red"><b>➔ {err_lbl}: {str(e)}</b></span>',
                    "raw",
                )
        finally:
            from PyQt6.QtCore import QTimer

            QTimer.singleShot(
                1000, lambda: setattr(self, "_update_dialog_active", False)
            )

        # =====================
        # INIT UI
        # =====================

    def init_ui(self):
        from PyQt6.QtWidgets import (
            QVBoxLayout,
            QHBoxLayout,
            QGridLayout,
            QLabel,
            QPushButton,
            QWidget,
            QTextEdit,
            QComboBox,
            QSpinBox,
            QProgressBar,
            QApplication,
            QFrame,
            QFileDialog,
        )
        from PyQt6.QtWidgets import QStyle, QSizePolicy
        from PyQt6.QtGui import QPixmap, QFont
        from PyQt6.QtCore import Qt, QSize, QTimer, QDateTime

        import subprocess
        import os
        import requests

        # ---------------------------------------------------------
        # 1. Grundwerte & Hauptlayout
        # ---------------------------------------------------------
        self.UI_BUTTON_H = 50
        self.UI_BADGE_H = 50
        self.UI_PROGRESS_H = 25
        self.UI_STATUS_H = 50
        self.UI_RADIUS = 10

        B_HEIGHT = self.UI_BUTTON_H
        B_WIDTH = 300
        CONTROL_HEIGHT = self.UI_BUTTON_H
        self.BUTTON_HEIGHT = self.UI_BUTTON_H
        main_layout = QVBoxLayout(self)
        main_layout.setSpacing(12)
        main_layout.setContentsMargins(12, 10, 12, 14)
        self.setStyleSheet("""
            QWidget { background-color: #10141d; color: #e8f1ff; }
            QGroupBox {
                background-color: #171d29;
                border: 1px solid #29384b;
                border-radius: 12px;
                margin-top: 13px;
                padding: 10px 8px 8px 8px;
                font-weight: 800;
                color: #35e6ff;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 14px;
                padding: 0 8px;
                color: #35e6ff;
                letter-spacing: 1px;
            }
            QToolTip {
                background: #111827; color: #f2f7ff;
                border: 1px solid #35e6ff; padding: 7px; border-radius: 6px;
            }
        """)
        self.create_buttons()
        
        # =========================================================
        # HEADER-BEREICH (STEAM / DISCORD CLEAN)
        # =========================================================

        header_widget = QFrame()
        self.header_widget = header_widget
        header_widget.setMinimumHeight(200)
        header_widget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        header_widget.setStyleSheet(
            """
            QFrame {
                background-color: #2F2F2F;
                border: 1px solid #444;
                border-radius: 10px;
            }
            QLabel {
                background-color: transparent;
                border: none;
                font-weight: bold;
            }
            QPushButton {
                color: #EAFF00 !important;
                background-color: #3d3d3d;
                border: 1px solid #555;
                border-radius: 8px;
                padding: 5px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #4d4d4d;
                border: 1px solid #EAFF00;
                color: white !important;
            }
        """
        )

        header_layout = QHBoxLayout(header_widget)
        self.header_layout = header_layout
        header_layout.setContentsMargins(8, 6, 0, 6)
        header_layout.setSpacing(6)
        header_layout.setAlignment(Qt.AlignmentFlag.AlignVCenter)


        # =========================================================
        # LEFT SECTION
        # =========================================================

        left_header_container = QWidget()
        left_header_layout = QHBoxLayout(left_header_container)
        self.left_header_layout = left_header_layout
        left_header_layout.setContentsMargins(4, 0, 0, 0)
        left_header_layout.setSpacing(8)
        left_header_layout.setAlignment(Qt.AlignmentFlag.AlignVCenter)


        # --- INFO BUTTON ---
        self.info_button = QPushButton()
        self.info_button.setMinimumSize(36, 36)
        self.info_button.setSizePolicy(
            QSizePolicy.Policy.Minimum,
            QSizePolicy.Policy.Fixed
        )

        icon_info = self.style().standardIcon(
            QStyle.StandardPixmap.SP_MessageBoxInformation
        )
        self.info_button.setIcon(icon_info)
        self.info_button.setIconSize(QSize(28, 28))
        self.info_button.setCursor(Qt.CursorShape.PointingHandCursor)

        # Tooltip entsprechend der aktuellen Sprache
        is_de = str(getattr(self, "LANG", "de")).lower().startswith("de")

        self.info_button.setToolTip(
            "Zeigt Informationen über das OSCam Toolkit an."
            if is_de
            else "Displays information about the OSCam Toolkit."
        )
        self.info_button.setToolTipDuration(10000)

        # Klick öffnet das Info-Fenster
        self.info_button.clicked.connect(self.show_info)

        left_header_layout.addWidget(self.info_button)


        # =========================================================
        # CLOCK + DATE (WICHTIG: VBOX = UNTEREINANDER)
        # =========================================================

        time_date_container = QWidget()
        time_date_layout = QVBoxLayout(time_date_container)
        time_date_layout.setContentsMargins(0, 0, 0, 0)
        time_date_layout.setSpacing(1)
        time_date_layout.setAlignment(Qt.AlignmentFlag.AlignVCenter)

        bold_font_time = QFont("Segoe UI", 8, QFont.Weight.Bold)
        bold_font_date = QFont("Segoe UI", 8, QFont.Weight.Bold)

        # --- CLOCK ---
        self.digital_clock = QLabel("--:--:--")
        self.digital_clock.setFont(bold_font_time)
        self.digital_clock.setStyleSheet("color: red;")
        time_date_layout.addWidget(self.digital_clock, alignment=Qt.AlignmentFlag.AlignLeft)

        # --- DATE ---
        self.date_label = QLabel("--.--.----")
        self.date_label.setFont(bold_font_date)
        self.date_label.setStyleSheet("color: orange; background: transparent;")
        time_date_layout.addWidget(self.date_label, alignment=Qt.AlignmentFlag.AlignLeft)


        
        # =========================================================
        # BUTTON CARD + S3 REPOSITORY-AUSWAHL
        # =========================================================

        btn_card = QWidget()
        btn_card_layout = QVBoxLayout(btn_card)
        btn_card_layout.setContentsMargins(0, 0, 0, 0)
        btn_card_layout.setSpacing(6)
        btn_card_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        s3_s4_h_layout = QHBoxLayout()
        s3_s4_h_layout.setSpacing(8)
        s3_s4_h_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        for _install_btn in (self.btn_s3, self.btn_s4, self.btn_ncam):
            _install_btn.setMinimumSize(105, 50)
            _install_btn.setMaximumWidth(260)
            _install_btn.setSizePolicy(
                QSizePolicy.Policy.Preferred,
                QSizePolicy.Policy.Fixed,
            )

        s3_s4_h_layout.addWidget(self.btn_s3)
        s3_s4_h_layout.addWidget(self.btn_s4)
        btn_card_layout.addLayout(s3_s4_h_layout)

        btn_card_layout.addWidget(
            self.btn_ncam,
            alignment=Qt.AlignmentFlag.AlignCenter,
        )

        # ---------------------------------------------------------
        # S3 GIT-REPOSITORY AUSWÄHLEN
        # ---------------------------------------------------------
        self.s3_repository_options = {
            "S3 Simplebuild (Original)": "https://github.com/gorgone/s3_releases.git",
            "S3 Simplebuild speeddy005": "https://github.com/speedy005/speedy005-S3-Simplebuild.git",
        }

        # Konfiguration laden, sofern noch nicht vorhanden.
        if not isinstance(getattr(self, "current_config", None), dict):
            self.current_config = {}

        self.cfg = self.current_config

        saved_repo_name = self.current_config.get(
            "s3_repository_name",
            "S3 Simplebuild (Original)",
        )

        if saved_repo_name == "S3 Simplebuild":
            saved_repo_name = "S3 Simplebuild speeddy005"
        if saved_repo_name not in self.s3_repository_options:
            saved_repo_name = "S3 Simplebuild (Original)"

        self.selected_s3_repository = saved_repo_name

        self.s3_repo_label = QLabel("S3 Git-Quelle:")
        self.s3_repo_label.setAlignment(
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft
        )
        self.s3_repo_label.setStyleSheet(
            "color: #EAFF00; background: transparent; "
            "font-weight: bold; border: none;"
        )

        self.s3_repo_combo = QComboBox()
        self.s3_repo_combo.setObjectName("s3RepositoryCombo")
        self.s3_repo_combo.addItems(
            list(self.s3_repository_options.keys())
        )
        self.s3_repo_combo.setCurrentText(saved_repo_name)
        self.s3_repo_combo.setMinimumWidth(230)
        self.s3_repo_combo.setMinimumHeight(34)
        self.s3_repo_combo.setToolTip(
            "Repository auswählen, das bei der S3-Installation verwendet wird."
        )
        self.s3_repo_combo.setStyleSheet("""
            QComboBox {
                color: #EAFF00;
                background-color: #252525;
                border: 1px solid #666;
                border-radius: 6px;
                padding: 5px 8px;
                font-weight: bold;
            }
            QComboBox:hover {
                border: 1px solid #EAFF00;
            }
            QComboBox QAbstractItemView {
                color: #EAFF00;
                background-color: #252525;
                selection-background-color: #444;
                selection-color: white;
            }
        """)

        s3_repo_row = QHBoxLayout()
        s3_repo_row.setContentsMargins(0, 0, 0, 0)
        s3_repo_row.setSpacing(6)
        s3_repo_row.addWidget(self.s3_repo_label)
        s3_repo_row.addWidget(self.s3_repo_combo, 1)

        # Die Git-Quelle wird erst nach Auswahl des Installationspfads abgefragt.
        # Deshalb wird die ComboBox nicht unter den drei Hauptbuttons angezeigt.

        # Nur verbinden, nicht zusätzlich den S3-Button verdrahten.
        self.s3_repo_combo.currentTextChanged.connect(
            self.on_s3_repository_changed
        )

        # =========================================================
        # LEFT BUILD (ORDER WICHTIG!)
        # =========================================================

        left_header_layout.addWidget(time_date_container)
        left_header_layout.addWidget(btn_card)



        # =========================================================
        #         FINAL HEADER ASSEMBLY
        # =========================================================

        # LEFT-HEADER wird erst beim finalen Layout einmalig eingefügt.

        # =========================================================
        # SIGNALS (UNCHANGED)
        # =========================================================

        self.btn_s3.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.btn_s3.customContextMenuRequested.connect(self.select_s3_path_manually)
        self.btn_s3.clicked.connect(self.start_s3_install)

        self.btn_s4.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.btn_s4.customContextMenuRequested.connect(self.select_s4_path_manually)
        self.btn_s4.clicked.connect(self.start_s4_install)

        self.btn_ncam.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.btn_ncam.customContextMenuRequested.connect(self.select_ncam_path_manually)
        self.btn_ncam.clicked.connect(self.start_ncam_install)


        # =========================================================
        # FINAL HEADER ATTACH
        # =========================================================

        header_layout.addWidget(left_header_container, 0)
        #header_layout.addStretch(1)
        # --- MIDDLE: Logo (responsive) ---
        self.logo_label = QLabel()
        self.logo_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.logo_label.setMinimumSize(0, 0)
        self.logo_label.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Expanding,
        )

        header_layout.addWidget(self.logo_label, 1)


        self.original_pixmap = None

        try:
            url = (
                "https://raw.githubusercontent.com/"
                "speedy005/oscam_patch_manager_new/master/"
                "speedy_banner.png"
            )

            resp = requests.get(url, timeout=5)
            resp.raise_for_status()

            pixmap = QPixmap()
            if pixmap.loadFromData(resp.content):
                self.original_pixmap = pixmap
                QTimer.singleShot(0, self._fit_header_logo)
            else:
                raise ValueError("Logo konnte nicht geladen werden")

        except Exception as exc:
            self.logo_label.setText("OSCAM TOOLKIT")
            self.logo_label.setStyleSheet(
                "color: white; font-size: 18px;"
            )
            self.original_pixmap = None
            print(f"Logo konnte nicht geladen werden: {exc}")



        # --- RIGHT: Log-Button + Version & Autor (OPTIMIERT) ---
        right_header_container = QWidget()
        right_header_layout = QHBoxLayout(right_header_container)
        self.right_header_layout = right_header_layout
        right_header_layout.setContentsMargins(0, 0, 0, 0)
        right_header_layout.setSpacing(8)
        right_header_layout.setAlignment(
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight
        )

        # --- INSTALLATION / PATCH-BEREICH ZWISCHEN LABEL/LOG ---
        lang = getattr(self, "LANG", "de").lower()
        # Zwei Spalten: links die Patch-Ordner, rechts die Patch-Kopieraktionen.
        install_path_container = QWidget()
        install_path_layout = QHBoxLayout(install_path_container)
        install_path_layout.setContentsMargins(0, 0, 0, 0)
        install_path_layout.setSpacing(8)
        install_path_layout.setAlignment(
            Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter
        )

        path_button_column = QWidget()
        path_button_layout = QVBoxLayout(path_button_column)
        path_button_layout.setContentsMargins(0, 0, 0, 0)
        path_button_layout.setSpacing(3)
        path_button_layout.setAlignment(Qt.AlignmentFlag.AlignHCenter)

        patch_button_column = QWidget()
        patch_button_layout = QVBoxLayout(patch_button_column)
        patch_button_layout.setContentsMargins(0, 0, 0, 0)
        patch_button_layout.setSpacing(3)
        patch_button_layout.setAlignment(Qt.AlignmentFlag.AlignHCenter)

        self.header_btn_s3_ncam = QPushButton(self.TEXT.get("patch_folder_s3_ncam", "S3-NCam-Bonecrew-Pfad"))
        self.header_btn_s3_ncam.setMinimumSize(200, 50)
        self.header_btn_s3_ncam.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        self.header_btn_s3_ncam.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
        self.header_btn_s3_ncam.setIcon(self.style().standardIcon(QApplication.style().StandardPixmap.SP_DirOpenIcon))
        self.header_btn_s3_ncam.setIconSize(QSize(20, 20))
        self.header_btn_s3_ncam.setToolTip(
            self.TEXT.get("patch_folder_s3_ncam_tooltip", "S3-NCam-Bonecrew-Pfad wählen")
        )
        self.header_btn_s3_ncam.clicked.connect(self.select_ncam_patch_path_manually)
        path_button_layout.addWidget(self.header_btn_s3_ncam)

        self.header_btn_s4 = QPushButton(self.TEXT.get("patch_folder_s4", "S4-Pfad"))
        self.header_btn_s4.setMinimumSize(200, 50)
        self.header_btn_s4.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        self.header_btn_s4.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
        self.header_btn_s4.setIcon(self.style().standardIcon(QApplication.style().StandardPixmap.SP_DirOpenIcon))
        self.header_btn_s4.setIconSize(QSize(20, 20))
        self.header_btn_s4.setToolTip(
            self.TEXT.get("patch_folder_s4_tooltip", "S4-Pfad wählen")
        )
        self.header_btn_s4.clicked.connect(self.select_s4_patch_path_manually)
        path_button_layout.addWidget(self.header_btn_s4)

        # Die beiden Patch-Kopieraktionen ebenfalls untereinander.
        self.header_btn_patch_ncam = QPushButton(self.TEXT.get("patch_copy_s3_ncam", "Patch → S3-NCam"))
        self.header_btn_patch_ncam.setMinimumSize(200, 50)
        self.header_btn_patch_ncam.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        self.header_btn_patch_ncam.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
        self.header_btn_patch_ncam.setIcon(self.style().standardIcon(QApplication.style().StandardPixmap.SP_FileIcon))
        self.header_btn_patch_ncam.setIconSize(QSize(20, 20))
        self.header_btn_patch_ncam.setToolTip(
            "Kopiert oscam-emu.patch in den konfigurierten S3-NCam-Bonecrew-Patch-Ordner."
            if str(getattr(self, "LANG", "de")).lower().startswith("de")
            else "Copies oscam-emu.patch to the configured S3-NCam-Bonecrew patch folder."
        )
        self.header_btn_patch_ncam.clicked.connect(
            lambda checked=False: self.copy_patch_to_target("ncam")
        )
        patch_button_layout.addWidget(self.header_btn_patch_ncam)

        self.header_btn_patch_s4 = QPushButton(self.TEXT.get("patch_copy_s4", "Patch → S4"))
        self.header_btn_patch_s4.setMinimumSize(200, 50)
        self.header_btn_patch_s4.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        self.header_btn_patch_s4.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
        self.header_btn_patch_s4.setIcon(self.style().standardIcon(QApplication.style().StandardPixmap.SP_FileIcon))
        self.header_btn_patch_s4.setIconSize(QSize(20, 20))
        self.header_btn_patch_s4.setToolTip(
            "Kopiert oscam-emu.patch in den konfigurierten S4-Patch-Ordner."
            if str(getattr(self, "LANG", "de")).lower().startswith("de")
            else "Copies oscam-emu.patch to the configured S4 patch folder."
        )
        self.header_btn_patch_s4.clicked.connect(
            lambda checked=False: self.copy_patch_to_target("s4")
        )
        patch_button_layout.addWidget(self.header_btn_patch_s4)

        install_path_layout.addWidget(path_button_column)
        install_path_layout.addWidget(patch_button_column)

        
        # ---------------------------------------------------------
        # HEADER-AKTIONEN
        # Log speichern | Stats
        # Oscam Git URL leicht nach links versetzt darunter
        # ---------------------------------------------------------

        header_actions_container = QWidget()
        header_actions_layout = QGridLayout(header_actions_container)
        header_actions_layout.setContentsMargins(0, 0, 0, 0)
        header_actions_layout.setHorizontalSpacing(3)
        header_actions_layout.setVerticalSpacing(3)
        header_actions_layout.setAlignment(
            Qt.AlignmentFlag.AlignHCenter |
            Qt.AlignmentFlag.AlignVCenter
        )

        # Einheitlicher Stil für ALLE Aktionsbuttons
        action_button_style = """
            QPushButton {
                color: #EAFF00;
                background-color: #3d3d3d;
                border: 1px solid #555;
                border-radius: 7px;
                padding: 2px 8px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #4d4d4d;
                border: 1px solid #EAFF00;
                color: white;
            }
            QPushButton:pressed {
                background-color: #222;
                border: 1px solid white;
            }
        """

        # --- LOG SPEICHERN ---
        log_text = "Log speichern" if lang == "de" else "Save Log"

        self.log_button = QPushButton(log_text)
        self.log_button.setMinimumSize(120, 32)
        self.log_button.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        self.log_button.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
        self.log_button.setIcon(
            self.style().standardIcon(
                QApplication.style().StandardPixmap.SP_DriveHDIcon
            )
        )
        self.log_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.log_button.setToolTip(
            "Speichert den aktuellen Log-/Protokollinhalt in einer Datei."
            if str(getattr(self, "LANG", "de")).lower().startswith("de")
            else "Saves the current log output to a file."
        )
        self.log_button.setStyleSheet(action_button_style)

        if hasattr(self, "export_log"):
            self.log_button.clicked.connect(self.export_log)

        # --- STATS ---
        self.telemetry_cb = QPushButton("Stats")
        self.telemetry_cb.setCheckable(True)
        self.telemetry_cb.setMinimumSize(58, 32)
        self.telemetry_cb.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        self.telemetry_cb.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
        self.telemetry_cb.setChecked(
            bool(get_setting("allow_telemetry", True))
        )
        self.telemetry_cb.setCursor(Qt.CursorShape.PointingHandCursor)
        self.telemetry_cb.setToolTip(
            "Anonyme Nutzungsstatistik (Hit-Counter) erlauben/verbieten."
            if lang == "de" else
            "Allow/disallow anonymous usage statistics (Hit-Counter)."
        )
        self.telemetry_cb.setAccessibleName(
            "Stats aktivieren oder deaktivieren"
        )
        self.telemetry_cb.setStyleSheet(action_button_style)
        self.telemetry_cb.toggled.connect(self.on_telemetry_changed)

        # --- OSCAM-GIT-URL ---
        self.oscam_source_button = QPushButton("Oscam Git URL")
        self.oscam_source_button.setMinimumSize(300, 50)
        self.oscam_source_button.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        self.oscam_source_button.setFont(
            QFont("Segoe UI", 8, QFont.Weight.Bold)
        )
        self.oscam_source_button.setIcon(self.style().standardIcon(QApplication.style().StandardPixmap.SP_DirLinkIcon))
        self.oscam_source_button.setIconSize(QSize(20, 20))
        self.oscam_source_button.setCursor(
            Qt.CursorShape.PointingHandCursor
        )
        self.oscam_source_button.setToolTip(
            "Quelle für Create Patch und OSCam-Emu-Git Patch auswählen"
            if lang == "de" else
            "Choose source for Create Patch and OSCam-Emu-Git Patch"
        )
        self.oscam_source_button.setStyleSheet(action_button_style)
        self.oscam_source_button.clicked.connect(self.choose_oscam_source)

        # --- OBERE REIHE ---
        header_actions_layout.addWidget(
            self.log_button, 0, 0, 1, 2,
            alignment=Qt.AlignmentFlag.AlignCenter
        ) 

        # --- UNTERE REIHE ---
        # Eigener Container, damit nur Oscam-git-url nach links
        # versetzt wird, ohne die obere Reihe zu verschieben.
        oscam_row_container = QWidget()
        oscam_row_layout = QHBoxLayout(oscam_row_container)
        oscam_row_layout.setContentsMargins(0, 0, 0, 0)
        oscam_row_layout.setSpacing(0)
        oscam_row_layout.addWidget(
            self.oscam_source_button,
            alignment=Qt.AlignmentFlag.AlignCenter
        )

        header_actions_layout.addWidget(
            oscam_row_container, 1, 0, 1, 2,
            alignment=Qt.AlignmentFlag.AlignCenter
        )

        # Aktionsblock rechts neben den Patch-Kopierbuttons einsetzen.
        install_path_layout.addWidget(header_actions_container)


        # Kompakte, zum bestehenden Header passende Optik.
        for _btn in (
            self.header_btn_s3_ncam,
            self.header_btn_s4,
            self.header_btn_patch_ncam,
            self.header_btn_patch_s4,
        ):
            _btn.setMinimumWidth(0)
            _btn.setCursor(Qt.CursorShape.PointingHandCursor)
            _btn.setStyleSheet("""
                QPushButton {
                    color: #EAFF00;
                    background-color: #3d3d3d;
                    border: 1px solid #555;
                    border-radius: 7px;
                    padding: 2px 8px;
                    font-weight: bold;
                }
                QPushButton:hover {
                    background-color: #4d4d4d;
                    border: 1px solid #EAFF00;
                    color: white;
                }
                QPushButton:pressed {
                    background-color: #222;
                    border: 1px solid white;
                }
            """)

        # Die Installations-/Patch-Aktionen sind Teil des rechten Header-Bereichs;
        # das Logo bleibt dadurch mittig zwischen linker und rechter Seite.

        # Fonts definieren, BEVOR sie benutzt werden (löst NameError)
        bold_font_header = QFont("Segoe UI", 25, QFont.Weight.Bold)
        bold_font_version = QFont("Segoe UI", 25, QFont.Weight.Bold)

        # Container für die Labels rechts oben
        version_text_container = QWidget()
        version_text_layout = QVBoxLayout(version_text_container)
        version_text_layout.setContentsMargins(8, 0, 8, 0)
        version_text_layout.setSpacing(2)
        version_text_layout.setAlignment(
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight
        )

        # Farben und Version holen
        current_diff_colors = getattr(self, "current_diff_colors", {"fg": "#FF0000"})
        text_color = current_diff_colors.get("fg", "#EAFF00")
        app_v = getattr(self, "APP_VERSION", globals().get("APP_VERSION", "4.0.0"))

        # Das Haupt-Versionslabel (Das farbige)
        # WICHTIG: Wir weisen es self.version_label zu, damit es später aktualisierbar ist
        self.version_label = QLabel(f"v{app_v}")
        self.version_label.setFont(bold_font_version)
        self.version_label.setStyleSheet(
            f"color: {text_color}; background: transparent; margin-bottom: 5px;"
        )

        # Autor Label (Blau im Screenshot)
        self.by_label = QLabel("by speedy005")
        self.by_label.setFont(QFont("Segoe UI", 20, QFont.Weight.Bold))
        self.by_label.setStyleSheet("color: #0055ff; background: transparent;")

        # In den kleinen Container packen
        version_text_layout.addWidget(self.version_label)
        version_text_layout.addWidget(self.by_label)

        # Installations-/Patch-Buttons rechts neben dem mittigen Banner.
        # Version, Stats und "by speedy005" bleiben ganz rechts.
        right_header_layout.addWidget(install_path_container)
        right_header_layout.setSpacing(4)

        # Versionsanzeige etwas nach rechts verschieben
        right_header_layout.addSpacing(40)

        right_header_layout.addWidget(version_text_container)

        self.telemetry_cb.setSizePolicy(
            QSizePolicy.Policy.Fixed,
            QSizePolicy.Policy.Fixed
        )
        right_header_layout.addWidget(
            self.telemetry_cb,
            0,
            Qt.AlignmentFlag.AlignTop
        )

        # Zum Haupt-Header hinzufügen
        header_layout.addWidget(right_header_container, 0)

        main_layout.addWidget(header_widget)
        QTimer.singleShot(0, self._fit_header_controls)
        # Theme-Farben und Tooltips werden zentral anhand der aktuellen Sprache
        # und des ausgewählten Farbschemas gesetzt.
        self.apply_global_button_style()

        # ---------------------------------------------------------
        # INFO + PROGRESS
        # ---------------------------------------------------------
        # Große Live-Konsole als visueller Mittelpunkt der Oberfläche
        console_panel = QGroupBox("LIVE SYSTEM CONSOLE  /  STATUS & LOGS")
        self.console_panel = console_panel
        console_layout = QVBoxLayout(console_panel)
        console_layout.setContentsMargins(10, 12, 10, 10)
        console_layout.setSpacing(8)
        self.info_text = QTextEdit()
        self.info_text.setObjectName("liveConsole")
        self.info_text.setReadOnly(True)
        # Begrenzung der sichtbaren Live-Konsole verhindert, dass sehr lange
        # Sitzungen unbegrenzt Speicher belegen. Build-Log-Dateien bleiben unberührt.
        try:
            self.info_text.document().setMaximumBlockCount(10000)
        except Exception as exc:
            print(f"[console] Begrenzung der Konsolenhistorie nicht verfügbar: {exc}")
        self.info_text.setMinimumHeight(220)
        self.info_text.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.info_text.setFont(QFont("Consolas", 11))
        self.info_text.setStyleSheet("""
            QTextEdit#liveConsole {
                background-color: #080c12; color: #b9f7ff;
                border: 1px solid #24465a; border-radius: 9px;
                padding: 10px; selection-background-color: #176b80;
            }
        """)
        # Kleine Werkzeugleiste für die Live-Konsole; ändert keine gespeicherten Logs.
        console_tools = QHBoxLayout()
        console_tools.setContentsMargins(0, 0, 0, 0)
        console_tools.setSpacing(6)

        is_de = str(getattr(self, "LANG", LANG)).lower() == "de"
        copy_log_button = QPushButton("LOG KOPIEREN" if is_de else "COPY LOG")
        copy_log_button.setToolTip(
            "Gesamte sichtbare Konsolenausgabe in die Zwischenablage kopieren"
            if is_de else "Copy all visible console output to the clipboard"
        )
        def copy_visible_console():
            """Kopiert ausschließlich den aktuell sichtbaren Konsoleninhalt."""
            try:
                QApplication.clipboard().setText(self.info_text.toPlainText())
            except Exception as exc:
                self.append_info(self.info_text, f"Kopieren fehlgeschlagen: {exc}", "error")

        copy_log_button.clicked.connect(copy_visible_console)
        self.copy_log_button = copy_log_button

        analyze_build_logs_button = QPushButton("BUILD-LOG ANALYSIEREN" if is_de else "ANALYZE BUILD LOG")
        analyze_build_logs_button.setToolTip(
            "Das neueste Build-Protokoll auf typische Fehler und Warnungen prüfen"
            if is_de else "Check the newest build log for common errors and warnings"
        )
        analyze_build_logs_button.clicked.connect(self.analyze_latest_build_log)
        self.analyze_build_logs_button = analyze_build_logs_button

        diagnostics_button = QPushButton("SYSTEM-DIAGNOSE" if is_de else "SYSTEM DIAGNOSTICS")
        diagnostics_button.setToolTip(
            "System, Tool-Verzeichnisse und vorhandene Build-Logs prüfen; startet keinen Build"
            if is_de else "Check system, tool directories and existing build logs; does not start a build"
        )
        diagnostics_button.clicked.connect(self.open_diagnostics_dialog)
        self.diagnostics_button = diagnostics_button

        log_history_button = QPushButton("BUILD-LOG-VERLAUF" if is_de else "BUILD LOG HISTORY")
        log_history_button.setToolTip(
            "Alle Build-Logs nach Status, Zeit und Fehlerhinweisen vergleichen"
            if is_de else "Compare all build logs by status, time and error indicators"
        )
        log_history_button.clicked.connect(self.open_build_log_history)
        self.log_history_button = log_history_button

        clear_console_button = QPushButton("KONSOLE LEEREN" if is_de else "CLEAR CONSOLE")
        clear_console_button.setToolTip(
            "Nur die sichtbare Konsolenausgabe leeren; Logdateien bleiben unverändert"
            if is_de else "Clear only the visible console output; log files remain unchanged"
        )
        clear_console_button.clicked.connect(self.info_text.clear)
        self.clear_console_button = clear_console_button

        for tool_button in (copy_log_button, analyze_build_logs_button, diagnostics_button, log_history_button, clear_console_button):
            tool_button.setMinimumHeight(30)
            tool_button.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
            console_tools.addWidget(tool_button)

        console_tools.addStretch(1)
        console_layout.addLayout(console_tools)
        console_layout.addWidget(self.info_text, 1)
        main_layout.addWidget(console_panel, 10)

        # Auto-Scroll wird beim Hinzufügen neuer Meldungen gesteuert.
        # Kein periodischer Timer: so bleibt die Konsole ruhig, wenn der
        # Benutzer ältere Einträge liest.
        self.scroll_timer = None

        main_layout.addSpacing(12)

        self.progress_bar = QProgressBar()
        self.progress_bar.setMinimumHeight(self.UI_PROGRESS_H)
        self.progress_bar.setMaximumHeight(40)
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(True)
        self.progress_bar.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.progress_bar.setStyleSheet(
            """
        QProgressBar {
            border: 1px solid #444;
            border-radius: 8px;
            background-color: #1A1A1A;
            color: white;
            text-align: center;
            font-weight: bold;
            font-size: 14px;
        }
        QProgressBar::chunk {
            background-color: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                                              stop:0 #F37804, stop:1 #FFD700);
            border-radius: 8px;
        }
        """
        )
        main_layout.addWidget(self.progress_bar)
        # self.pbar_idle()
        # ---------------------------------------------------------
        # STATUS-BAR (MAXIMALER AUSBAU: GROSSE ANZEIGEN & ZIP)
        # ---------------------------------------------------------
        status_bar_container = QFrame()
        status_bar_container.setFixedHeight(self.UI_STATUS_H)
        status_bar_container.setStyleSheet(
            "QFrame { background-color: rgba(0,0,0,0.4); border: 1px solid #444; border-radius: 10px; }"
        )

        status_bar_layout = QHBoxLayout(status_bar_container)
        status_bar_layout.setContentsMargins(15, 0, 15, 0)
        status_bar_layout.setSpacing(12)

        # FIX 1: Feste Breite und statische Attribute verhindern das "Zucken" des Textes
        self.status_info_label = QLabel("SYSTEM STATUS:")
        self.status_info_label.setFixedWidth(220)  # Reserviert festen Platz
        self.status_info_label.setAttribute(Qt.WidgetAttribute.WA_StaticContents)
        self.status_info_label.setStyleSheet(
            "color: #FF0000; font-size: 22px; font-weight: bold; border: none; background: transparent;"
        )
        status_bar_layout.addWidget(self.status_info_label)

        # Listen-Trennung für Stabilität
        self.status_leds = []  # Bleiben immer statisch (Grün/Rot)
        self.user_leds = []  # Nur für Slider-Blinken

        tools_to_check = [
            "py",
            "qt6",
            "snd",
            "git",
            "patch",
            "zip",
            "gcc",
            "make",
            "ssl",
            "usb",
        ]
        label_colors = {
            "py": "#3776AB",
            "qt6": "#41CD52",
            "snd": "#FF0000",
            "git": "#2ecc71",
            "patch": "#3498db",
            "zip": "#E24A39",
            "gcc": "#f1c40f",
            "make": "#e67e22",
            "ssl": "#9b59b6",
            "usb": "#1abc9c",
        }

        for tool in tools_to_check:
            exists = False
            display_name = tool.upper()

            # --- SYSTEM CHECK ---
            if tool == "py":
                exists = True
                display_name = f"PY {sys.version_info.major}.{sys.version_info.minor}"
            elif tool == "qt6":
                exists = importlib.util.find_spec("PyQt6") is not None
            elif tool == "snd":
                exists = (
                    any(shutil.which(c) for c in ["paplay", "pw-play", "aplay"])
                    or platform.system() == "Windows"
                )
                display_name = "SOUND"
            elif tool in ["ssl", "usb"]:
                if platform.system() == "Linux" and shutil.which("dpkg"):
                    pkg = "libssl-dev" if tool == "ssl" else "libusb-1.0-0-dev"
                    exists = (
                        subprocess.run(
                            ["dpkg-query", "-W", "-f='${Status}'", pkg],
                            capture_output=True,
                            text=True,
                        ).returncode
                        == 0
                    )
                else:
                    exists = True
            else:
                exists = shutil.which(tool) is not None

            # Tool Container
            t_widget = QWidget()
            # FIX 2: Feste Breite für Container verhindert Layout-Verschiebungen in der Zeile
            t_widget.setMinimumWidth(80)
            t_lay = QHBoxLayout(t_widget)
            t_lay.setContentsMargins(0, 0, 0, 0)
            t_lay.setSpacing(6)

            # SYSTEM LED (Wird nur EINMAL gefärbt)
            led = QLabel()
            led.setFixedSize(16, 16)
            led.tool_exists = exists
            led.base_color = "#27ae60" if exists else "#c0392b"
            led.setStyleSheet(
                f"background-color: {led.base_color}; border-radius: 8px; border: 2px solid #555;"
            )
            led.setCursor(Qt.CursorShape.PointingHandCursor)
            led.mousePressEvent = lambda e, t=tool: self.show_tool_help(t)

            self.status_leds.append(led)

            # Text
            c = label_colors.get(tool, "#FFF")
            lbl = QLabel(display_name)
            lbl.setStyleSheet(
                f"color: {c}; font-size: 20px; font-weight: bold; font-family: 'Segoe UI';"
            )

            t_lay.addWidget(led)
            t_lay.addWidget(lbl)
            status_bar_layout.addWidget(t_widget)

        status_bar_layout.addStretch()

        # ---------------------------------------------------------
        # USER-LED entfernt (Punkt gelöscht)
        self.user_leds = []

        # ---------------------------------------------------------
        # SPEED REGLER
        # ---------------------------------------------------------
        speed_icon = QLabel("⚡")
        speed_icon.setStyleSheet(
            "font-size: 20px; border: none; background: transparent;"
        )
        status_bar_layout.addWidget(speed_icon)

        self.slider_speed = QSlider(Qt.Orientation.Horizontal)
        self.slider_speed.setFixedWidth(110)
        self.slider_speed.setRange(50, 1000)
        self.slider_speed.setInvertedAppearance(True)

        current_speed = self.current_config.get("blink_speed", 500)
        self.slider_speed.setValue(current_speed)
        self.slider_speed.setCursor(Qt.CursorShape.PointingHandCursor)
        self.slider_speed.setStyleSheet(
            "QSlider::groove:horizontal { border: 1px solid #666; height: 6px; background: #222; border-radius: 3px; }"
            "QSlider::handle:horizontal { background: #F37804; border: 1px solid #444; width: 14px; height: 14px; margin: -5px 0; border-radius: 7px; }"
        )

        if hasattr(self, "update_blink_speed"):
            self.slider_speed.valueChanged.connect(self.update_blink_speed)

        status_bar_layout.addWidget(self.slider_speed)

        self.lbl_speed_val = QLabel("")
        self.lbl_speed_val.setStyleSheet(
            "color: #aaa; font-size: 12px; font-weight: bold; border: none;"
        )
        self.lbl_speed_val.setFixedWidth(85)
        status_bar_layout.addWidget(self.lbl_speed_val)

        # ---------------------------------------------------------
        # BUTTONS
        # ---------------------------------------------------------
        self.btn_matrix = QPushButton("📟 MATRIX MODE")
        self.btn_matrix.setFixedSize(300, self.UI_BUTTON_H)
        self.btn_matrix.setStyleSheet(
            "QPushButton { background-color: #000; color: #00FF41; border: 1px solid #008F11; border-radius: 6px; font-size: 14px; font-weight: bold; } QPushButton:hover { background-color: #008F11; color: black; }"
        )
        if hasattr(self, "toggle_matrix"):
            self.btn_matrix.clicked.connect(lambda checked=False: self.toggle_matrix())
        status_bar_layout.addWidget(self.btn_matrix)

        self.btn_theme = QPushButton("🌓 THEME")
        self.btn_theme.setFixedSize(200, self.UI_BUTTON_H)
        self.btn_theme.setStyleSheet(
            "QPushButton { background-color: #444; color: red; border: 1px solid #666; border-radius: 6px; font-size: 18px; font-weight: bold; } QPushButton:hover { background-color: #555; border: 1px solid #F37804; }"
        )
        self.btn_theme.clicked.connect(self.toggle_theme)
        status_bar_layout.addWidget(self.btn_theme)

        main_layout.addWidget(status_bar_container)

        # --- ANIMATIONS-TIMER STARTEN ---
        self._blink_state = True
        self.master_timer = QTimer(self)
        self.master_timer.timeout.connect(self.animate_everything)
        self.master_timer.start(500)  # 500ms Takt für das Blinken
        # ---------------------------------------------------------
        # KONTROLL-BLOCK MIT HEADER
        # ---------------------------------------------------------

        controls_group = QFrame()
        # FIX: Wir setzen eine feste Mindesthöhe für den gesamten Block,
        # damit er beim Start nicht zusammenfällt.
        controls_group.setMinimumHeight(100)

        controls_group.setStyleSheet(
            f"""
        QFrame {{
            border: 1px solid #bbb;
            border-radius: 10px;
            background-color: #2F2F2F;
        }}
        /* FIX: Wir erzwingen hier im Stylesheet, dass alle Buttons 
           im Kasten sofort ihre Zielhöhe einnehmen */
        QPushButton {{
            min-height: {self.UI_BUTTON_H}px;
            max-height: {self.UI_BUTTON_H}px;
        }}
        """
        )
        controls_group_layout = QVBoxLayout(controls_group)

        # OPTIMIERUNG: Mehr Abstand nach oben (20) und unten (20) macht den Kasten höher
        controls_group_layout.setContentsMargins(0, 20, 15, 20)
        # Mehr Abstand zwischen Header und den Reglern
        controls_group_layout.setSpacing(0)

        translated_text = self.get_t("settings_header", "Einstellungen")
        self.controls_header = QLabel(translated_text)

        self.controls_header.setMinimumWidth(280)
        # OPTIMIERUNG: Header-Höhe von 28 auf 45 erhöht für mehr Präsenz
        self.controls_header.setMinimumHeight(50)
        self.controls_header.setAlignment(Qt.AlignmentFlag.AlignCenter)

        bg = current_diff_colors.get("bg", "#2F2F2F")  # Dunkelgrauer Hintergrund
        fg = current_diff_colors.get("fg", "#EAFF00")  # NEONGELBE SCHRIFT

        self.controls_header.setStyleSheet(
            f"""
            QLabel {{
                background-color: {bg};
                color: {fg};
                font-size: 20px;          /* Schrift im Header vergrößert */
                font-weight: bold;
                padding-left: 15px;
                padding-right: 15px;
                border-radius: 8px;
            }}
            """
        )

        controls_group_layout.addWidget(
            self.controls_header, alignment=Qt.AlignmentFlag.AlignLeft
        )

        controls_row = QWidget()
        controls_layout = QHBoxLayout(controls_row)
        # OPTIMIERUNG: Auch hier mehr Innenabstand für die Zeile
        controls_layout.setContentsMargins(0, 0, 5, 5)
        controls_layout.setSpacing(0)

        # Nutze hier deine neue B_HEIGHT (z.B. 60), falls definiert, sonst BUTTON_HEIGHT
        CONTROL_HEIGHT = getattr(self, "B_HEIGHT", 50)
        controls_layout.addStretch(0)
        control_style = f"""
        QComboBox, QSpinBox {{
            font-size: 18px;
            border-radius: {self.BUTTON_RADIUS}px;
            border: 2px solid #555;
            padding: 4px 8px;
            color: white;
            background-color: #444;
            height: {CONTROL_HEIGHT}px;
            margin-left: -2px;   /* Schiebt die Box näher an den Doppelpunkt des Labels */
        }}
        """

        def make_label(text):
            lbl = QLabel(text)
            lbl.setMinimumHeight(CONTROL_HEIGHT)

            # FIX: Von 85 auf 65 verringert, damit die Boxen näher am Wort kleben
            lbl.setFixedWidth(50)

            lbl.setAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
            lbl.setStyleSheet(
                f"""
                color: {fg}; 
                font-weight: bold; 
                font-size: 18px;
                background: transparent;
                padding-left: 0px;
            """
            )
            return lbl

        # --- 1. Sprache-Dropdown (Kompakt) ---
        self.lang_label = make_label(self.get_t("language_label", "Sprache:"))
       

        self.language_box = QComboBox()
        self.language_box.addItems(["EN", "DE"])
        self.language_box.setFixedSize(80, B_HEIGHT)
        self.language_box.setStyleSheet(
            f"""
            QComboBox {{
                background-color: #444444; color: white; font-weight: bold; font-size: 20px;
                border: 2px solid #555555; border-radius: 6px; padding-left: 5px;
            }}
            QComboBox::drop-down {{ border: none; width: 0px; }}
            QComboBox::down-arrow {{
                image: none; border-left: 5px solid transparent; border-right: 5px solid transparent;
                border-top: 5px solid #FF0000;
            }}
        """
        )

        # Sprach-Logik (Einmalig)
        saved_lang = self.cfg.get("language", "de").lower()
        (
            self.language_box.setCurrentIndex(0)
            if saved_lang == "en"
            else self.language_box.setCurrentIndex(1)
        )
        self.language_box.currentIndexChanged.connect(self.change_language)
        self.language_box.setToolTip(
            "Sprache der Oberfläche auswählen"
            if str(self.cfg.get("language", "de")).lower() == "de"
            else "Choose the interface language"
        )
        self.language_box.setToolTipDuration(10000)

        # --- 2. Farbe/Style (Kompakt) ---
        self.color_label = make_label(self.get_t("color_label", "Farbe:"))
        self.color_label.setFixedWidth(120)
        self.color_label.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )

        self.color_box = QComboBox()
        self.color_box.addItems(list(DIFF_COLORS.keys()))
        # Older versions stored the theme as "color", newer UI code may use
        # "theme_color". Prefer the newer key but retain backward compatibility.
        saved_color = self.cfg.get("theme_color", self.cfg.get("color", "Classics"))
        index = self.color_box.findText(saved_color)
        self.color_box.setCurrentIndex(index if index >= 0 else 0)
        self.color_box.setFixedSize(120, CONTROL_HEIGHT)
        self.color_box.setStyleSheet(control_style)
        self.color_box.currentIndexChanged.connect(self.change_colors)

        # --- 3. Commits (Kompakt) ---
        self.commit_label = make_label(self.get_t("commit_count_label", "Commits:"))

        # Breite festlegen (sizeHint + Puffer)
        
        

        # HÖHE festlegen (z.B. 35 Pixel, passend zu deinen CONTROL_HEIGHT Werten)
        self.commit_label.setFixedSize(140, self.UI_BUTTON_H)

        self.commit_label.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )

        self.commit_spin = QSpinBox()
        self.commit_spin.setRange(1, 20)
        self.commit_spin.setFixedSize(75, CONTROL_HEIGHT)
        self.commit_spin.setValue(self.cfg.get("commit_count", 10))
        self.commit_spin.valueChanged.connect(self.commit_value_changed)
        self.commit_spin.setToolTip(
            "Anzahl der Commits für die Anzeige bzw. Auswertung festlegen"
            if str(getattr(self, "LANG", self.cfg.get("language", "de"))).lower() in ("de", "0")
            else "Set the number of commits to display or analyze"
        )
        self.commit_spin.setToolTipDuration(10000)

        self.commit_spin.setStyleSheet(
            """
            QSpinBox {
                background-color: #222222; color: #FFFFFF; font-size: 20px; font-weight: bold;
                border: 2px solid #555555; border-radius: 6px;
            }
            QSpinBox::up-button, QSpinBox::down-button {
                background-color: #333333; width: 25px; border-left: 1px solid #555555;
            }
            QSpinBox::up-arrow { border-left: 6px solid transparent; border-right: 6px solid transparent; border-bottom: 6px solid #FF0000; }
            QSpinBox::down-arrow { border-left: 6px solid transparent; border-right: 6px solid transparent; border-top: 6px solid #FF0000; }
        """
        )

        # Signal wurde bereits einmalig oben verbunden.
        saved_commits = self.cfg.get("commit_count", 10)
        if int(self.commit_spin.value()) != int(saved_commits):
            self.commit_spin.setValue(saved_commits)

        self.btn_check_tools = QPushButton(
            self.get_t("check_tools_button", "🛠️ Tools prüfen")
        )
        self.btn_check_tools.setFixedSize(220, self.UI_BUTTON_H)

        # --- 0. Container Erstellung (Korrektur: QGroupBox statt QFrame) ---
        controls_group = QGroupBox("")
        controls_group_layout = QVBoxLayout(controls_group)

        # Stylesheet: Macht den Kasten extrem flach und rückt den Titel bündig
        controls_group.setStyleSheet(
            """
            QGroupBox {
                font-weight: bold;
                border: 1px solid #555555;
                border-radius: 8px;
                margin-top: 10px; 
                background-color: transparent;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                subcontrol-position: top left;
                left: 10px;
                padding: 0 3px;
                color: #00FF00;
            }
        """
        )
        # Erzwingt eine geringe Höhe für den gesamten Block
        # controls_group.setMaximumHeight(140)

        button_style = """
            QPushButton {
                background-color: #444444;
                color: white;
                font-weight: bold; 
                font-size: 24px;          /* Ein kleiner Schritt zurück auf 28px wirkt oft Wunder */
                font-family: sans-serif;
                
                border-radius: 6px;
                border: 3px solid #555555;

                /* ZENTRIERUNG ERZWINGEN */
                text-align: center;
                padding: 0px;
                margin: 0px;
            }
            QPushButton:hover { 
                background-color: #333333; 
                color: #FFD700; 
                border: 3px solid #FFD700;
            }
        """
        # --- Deine neuen Maße (Jetzt auch im Code genutzt!) ---
        B_WIDTH = 440
        B_HEIGHT = self.UI_BUTTON_H
        I_SIZE = 25

        # --- 2. Ordner-Buttons (Mit korrekter Höhe und Icon-Größe) ---
        f_icon = self.style().standardIcon(self.style().StandardPixmap.SP_DirIcon)

        # --- 2. Ordner-Buttons (Oben) ---
        f_icon = self.style().standardIcon(self.style().StandardPixmap.SP_DirIcon)

        self.btn_open_work = QPushButton(" Arbeitsordner")
        self.btn_open_work.setIcon(f_icon)
        self.btn_open_work.setIconSize(QSize(I_SIZE, I_SIZE))
        self.btn_open_work.setFixedSize(B_WIDTH, B_HEIGHT)
        self.btn_open_work.setStyleSheet(button_style)
        self.btn_open_work.clicked.connect(
            lambda checked=False: self.open_custom_folder(
                getattr(self, "WORK_DIR", self.base_dir)
            )
        )

        self.btn_open_temp = QPushButton(" Temp-Repo")
        self.btn_open_temp.setIcon(f_icon)
        self.btn_open_temp.setIconSize(QSize(I_SIZE, I_SIZE))
        self.btn_open_temp.setFixedSize(B_WIDTH, B_HEIGHT)
        self.btn_open_temp.setStyleSheet(button_style)
        self.btn_open_temp.clicked.connect(
            lambda checked=False: self.open_custom_folder(
                getattr(self, "TEMP_REPO", ".")
            )
        )

        self.btn_open_emu = QPushButton(" Emu-Git")
        self.btn_open_emu.setIcon(f_icon)
        self.btn_open_emu.setIconSize(QSize(I_SIZE, I_SIZE))
        self.btn_open_emu.setFixedSize(B_WIDTH, B_HEIGHT)
        self.btn_open_emu.setStyleSheet(button_style)
        self.btn_open_emu.clicked.connect(
            lambda checked=False: self.open_custom_folder(
                getattr(self, "PATCH_EMU_GIT_DIR", ".")
            )
        )

        # --- 3. Funktions-Buttons (Unten) ---
        self.btn_check_tools = QPushButton("🛠️ Tools prüfen")
        self.btn_check_tools.setIconSize(QSize(I_SIZE, I_SIZE))  # Icon-Größe für Emojis
        self.btn_check_tools.setFixedSize(B_WIDTH, B_HEIGHT)
        self.btn_check_tools.setStyleSheet(button_style)
        self.btn_check_tools.clicked.connect(self.run_full_system_check)

        self.btn_modifier = QPushButton("👤 Patch Autor")
        self.btn_modifier.setIconSize(QSize(I_SIZE, I_SIZE))
        self.btn_modifier.setFixedSize(B_WIDTH, B_HEIGHT)
        self.btn_modifier.setStyleSheet(button_style)
        self.btn_modifier.clicked.connect(self.change_modifier_name)

        self.btn_repo_url = QPushButton("🌐 Repo URL")
        self.btn_repo_url.setIconSize(QSize(I_SIZE, I_SIZE))
        self.btn_repo_url.setFixedSize(B_WIDTH, B_HEIGHT)
        self.btn_repo_url.setStyleSheet(button_style)
        self.btn_repo_url.clicked.connect(self.change_emu_repo)

        # --- Konfiguration (kannst du zentral anpassen) ---
        self.font_size_buttons = 12  # Hier die gewünschte Schriftgröße in Pixeln ändern
        current_lang = getattr(self, "lang", "de")  # Erkennt deine gewählte Sprache

        # 1. FARBSCHEMA ERMITTELN (Passend zu deinem toggle_theme)
        is_dark = "#2F2F2F" in self.styleSheet() or not self.styleSheet()

        if is_dark:
            # MATRIX / DARK COLORS (Orange/Gold)
            c1, c2, b_color = "#F37804", "#8B4513", "#444"
        else:
            # LIGHT / SYSTEM COLORS (Blau)
            c1, c2, b_color = "#3498db", "#2980b9", "#1c5980"

        # 2. TEXTE FÜR MEHRSPRACHIGKEIT
        lang = getattr(self, "LANG", "de").lower()
        text_patch_online = "🌐 Patch Online " if lang == "de" else "🌐 Load Patch "

        # 3. BUTTON ERSTELLEN
        self.btn_patch_online = QPushButton(text_patch_online)
        self.btn_patch_online.setFixedSize(B_WIDTH, B_HEIGHT)

        # 4. DYNAMISCHES STYLESHEET (Mit Variablen und Font-Fix 700)
        self.btn_patch_online.setStyleSheet(
            f"""
            QPushButton {{
                background-color: qlineargradient(x1:0, y1:0, x2:0, y2:1, 
                                                  stop:0 {c1}, stop:1 {c2});
                color: white; 
                font-weight: 700; 
                font-size: {self.font_size_buttons}px;
                border: 1px solid {b_color};
                border-radius: 10px;
            }}
            QPushButton:hover {{
                background-color: qlineargradient(x1:0, y1:0, x2:0, y2:1, 
                                                  stop:0 {c2}, stop:1 {c1});
                border: 1px solid #ffffff;
            }}
            QPushButton:pressed {{
                background-color: {b_color};
                padding-top: 2px;
                border-radius: 10px;
            }}
        """
        )

        # 5. VERKNÜPFUNG
        if hasattr(self, "handle_online_patch_button"):
            self.btn_patch_online.clicked.connect(self.handle_online_patch_button)

        # --- Bestehender Button: Commit Check ---
        self.btn_check_commit = QPushButton("🔄 Commit Check")
        self.btn_check_commit.setIconSize(QSize(I_SIZE, I_SIZE))
        self.btn_check_commit.setFixedSize(B_WIDTH, B_HEIGHT)
        self.btn_check_commit.setStyleSheet(button_style)
        self.btn_check_commit.clicked.connect(self.check_for_new_commit)

        # --- Manuelles Projekt-Backup ---
        self.btn_backup_gui = QPushButton("💾 Backup erstellen")
        self.btn_backup_gui.setIconSize(QSize(I_SIZE, I_SIZE))
        self.btn_backup_gui.setFixedSize(300, B_HEIGHT)
        self.btn_backup_gui.setStyleSheet(button_style)
        self.btn_backup_gui.setToolTip(
            "Erstellt backup/backup_gi.zip mit dem aktuellen Projektstand"
        )
        self.btn_backup_gui.clicked.connect(self.create_gi_backup)

        # --- 4. GRID-LAYOUT (Header-Fix: Badge 220px, Lücke 5px, Badge 280px, Weißer Text) ---
        from PyQt6.QtWidgets import QFrame

        grid_layout = QGridLayout()
        grid_layout.setContentsMargins(10, 0, 10, 10)
        grid_layout.setVerticalSpacing(5)
        grid_layout.setHorizontalSpacing(8)

        # Farben: Basis transparent (Neon weg), Hover nutzt System-Style
        base_bg = "transparent"
        hover_bg = current_diff_colors.get("btn_hover", "#2a4a6a")

        # Haupt-Container (Gesamtbreite: 220 + 5 + 280 = 505px)
        self.header_container = QWidget()
        self.header_container.setFixedSize(950, self.UI_BADGE_H)
        self.header_container.mousePressEvent = self.on_header_clicked

        h_layout = QHBoxLayout(self.header_container)
        h_layout.setContentsMargins(0, 0, 0, 0)
        h_layout.setSpacing(10)  # EXAKT 5 Pixel Lücke zwischen den Badges

        # --- A) LINKER BADGE (Einstellungen - Länge 220) ---
        self.left_badge = QFrame()
        self.left_badge.setFixedSize(180, self.UI_BADGE_H)
        self.left_badge.setStyleSheet(
            f"""
            QFrame {{
                background-color: {base_bg};
                border: 1px solid #444444;
                border-radius: 6px;
            }}
            QFrame:hover {{
                background-color: {hover_bg};
                border: 1px solid #777777;
            }}
        """
        )

        left_layout = QHBoxLayout(self.left_badge)
        left_layout.setContentsMargins(10, 0, 10, 0)

        # Icon links
        icon_label = QLabel()
        icon = self.style().standardIcon(self.style().StandardPixmap.SP_ComputerIcon)
        icon_label.setPixmap(icon.pixmap(20, 20))
        left_layout.addWidget(icon_label)

        # --- TITEL (Dynamisch ans Theme angepasst) ---
        # Aktuelle Akzentfarbe aus dem Theme laden (z.B. Neongelb)
        t_fg = current_diff_colors.get("fg", "#EAFF00")

        title_text = " Einstellungen" if self.LANG == "de" else " Settings"
        self.header_label = QLabel(title_text)

        # 'white' durch die Theme-Variable {t_fg} ersetzt
        self.header_label.setStyleSheet(
            f"""
            QLabel {{
                color: {t_fg}; 
                font-weight: bold; 
                font-size: 15px; 
                background: transparent; 
                border: none;
            }}
        """
        )

        left_layout.addWidget(self.header_label)
        left_layout.addStretch()
        h_layout.addWidget(self.left_badge)

        # --- B) RECHTER BADGE (OSCam Status - Länge 280) ---
        self.right_badge = QFrame()
        self.right_badge.setFixedSize(600, self.UI_BADGE_H)
        self.right_badge.setStyleSheet(
            f"""
            QFrame {{
                background-color: transparent;
                border: 1px solid #444444;
                border-radius: 6px;
            }}
            QFrame:hover {{
                background-color: transparent;
                border: 1px solid #777777;
            }}
        """
        )

        right_layout = QHBoxLayout(self.right_badge)
        right_layout.setContentsMargins(
            10, 0, 10, 0
        )  # Margins setzen, um Platz für Text zu schaffen

        # Status-Label (Zwingend WEISSE Schrift)
        self.status_label = QLabel("")
        self.status_label.setStyleSheet(
            "color: white; font-weight: bold; font-size: 18px; background: transparent; border: none;"
        )

        # Füge das Label ohne Stretch hin
        right_layout.addWidget(self.status_label)

        # Keine weiteren Strecken mehr erforderlich
        h_layout.addWidget(self.right_badge)

        # --- Integration ins Grid (Zeile 0) ---
        grid_layout.addWidget(self.header_container, 0, 0, 1, 4)
        grid_layout.addWidget(self.btn_open_work, 1, 9)

        grid_layout.addWidget(self.btn_open_emu, 0, 6)
        grid_layout.addWidget(self.btn_repo_url, 0, 7)

        grid_layout.addWidget(self.btn_open_temp, 0, 8)
        grid_layout.addWidget(self.btn_backup_gui, 0, 9)

        # --- EINSTELLUNGEN: kompakt, linksbündig, eine Reihe ---
        settings_row = QHBoxLayout()
        settings_row.setContentsMargins(0, 0, 0, 0)
        settings_row.setSpacing(6)

        # Sprache
        self.lang_label.setFixedWidth(120)
        self.lang_label.setAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        )
        self.language_box.setFixedWidth(90)

        settings_row.addWidget(self.lang_label)
        settings_row.addWidget(self.language_box)

        # Abstand zwischen den Gruppen
        settings_row.addSpacing(8)

        # Farbe
        self.color_label.setFixedWidth(80)
        self.color_label.setAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        )
        self.color_box.setFixedWidth(200)

        settings_row.addWidget(self.color_label)
        settings_row.addWidget(self.color_box)

        settings_row.addSpacing(8)

        # Commits
        self.commit_label.setFixedWidth(140)
        self.commit_label.setAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        )
        self.commit_spin.setFixedWidth(80)

        settings_row.addWidget(self.commit_label)
        settings_row.addWidget(self.commit_spin)

        # Backup-Button direkt hinter der Commit-Anzahl-Auswahl
        #settings_row.addWidget(self.btn_backup_gui)

        # Freier Platz bleibt rechts von den Einstellungen
        settings_row.addStretch(1)

        # Einstellungen in einer einzigen Reihe links anordnen
        grid_layout.addLayout(settings_row, 1, 0, 1, 6)

        # Die vier Git-/Revisions-Buttons direkt unter den Ordner-Buttons anordnen.
        # Die Widgets werden aus ihren bisherigen Layout-Positionen verschoben.
        grid_layout.addWidget(self.oscam_source_button, 1, 6)

        # Repo URL ist bereits oben neben Emu-Git platziert.

        # Keine Grid-Spalte künstlich verbreitern
        for i in range(grid_layout.columnCount()):
            grid_layout.setColumnStretch(i, 0)

        # Kleine Abstände zwischen den Grid-Elementen
        grid_layout.setHorizontalSpacing(6)
        grid_layout.setVerticalSpacing(6)


        # Layout zum Kasten hinzufügen
        controls_group_layout.addLayout(grid_layout)
        main_layout.addWidget(controls_group)

        # --- UNTERE SEKTION: klare Priorität für Patch-Aktionen ---
        self.grid_container = QGroupBox("PATCH & BUILD")
        self.grid_container.setObjectName("patchBuildPanel")
        self.grid_container.setStyleSheet("""
            QGroupBox#patchBuildPanel {
                margin-top: 4px;
            }

            QGroupBox#patchBuildPanel::title {
                subcontrol-origin: margin;
                subcontrol-position: top left;
                padding: 0px 5px;
                top: -10px;
            }
        """)
        self.layout_grid_buttons = QGridLayout(self.grid_container)
        # Kompakte, gleichmäßige Abstände; das Grid passt die Spaltenzahl
        # anhand der tatsächlich verfügbaren Breite dynamisch an.
        self.layout_grid_buttons.setHorizontalSpacing(6)
        self.layout_grid_buttons.setVerticalSpacing(6)
        self.layout_grid_buttons.setContentsMargins(8, 8, 8, 8)

        # Container zuerst einbetten, dann die vorhandenen Aktionsbuttons erzeugen.
        main_layout.addWidget(self.grid_container)
        self.setup_grid_buttons()

        # Die sechs bisher oben rechts platzierten Aktionsbuttons werden
        # in den PATCH & BUILD-Bereich integriert. Die Reihenfolge folgt
        # dem typischen Arbeitsablauf: Patch holen/erstellen, prüfen und
        # anwenden, sichern, anschließend Repository-/Systemwerkzeuge.
        
        patch_build_order = [
            getattr(self, "btn_patch_online", None),
            getattr(self, "buttons", {}).get("patch_create"),
            getattr(self, "buttons", {}).get("patch_renew"),
            getattr(self, "buttons", {}).get("patch_check"),
            getattr(self, "buttons", {}).get("patch_apply"),
            getattr(self, "buttons", {}).get("patch_zip"),
            getattr(self, "buttons", {}).get("backup_old"),
            getattr(self, "buttons", {}).get("clean_folder"),
            getattr(self, "buttons", {}).get("change_old_dir"),
            getattr(self, "btn_check_commit", None),
            getattr(self, "btn_modifier", None),
            getattr(self, "btn_check_tools", None),

            # Beenden
            getattr(self, "buttons", {}).get("exit"),

            # Direkt danach: Rechte fixen
            getattr(self, "btn_fix_perms", None),
        ]


        # Alte Grid-Einträge leeren und in der gewünschten Reihenfolge neu
        # einsetzen. Signale/Callbacks bleiben dabei unverändert verbunden.
        patch_layout = self.layout_grid_buttons
        while patch_layout.count():
            item = patch_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(self.grid_container)

        from PyQt6.QtWidgets import QSizePolicy
        seen_patch_buttons = set()
        ordered_patch_buttons = []
        for button in patch_build_order:
            if button is None or id(button) in seen_patch_buttons:
                continue
            seen_patch_buttons.add(id(button))
            ordered_patch_buttons.append(button)
            button.setParent(self.grid_container)
            # Die sechs verschobenen Buttons sollen sich genauso flexibel
            # an die Spaltenbreite anpassen wie die nativen Patch-Buttons.
            if button in (
                getattr(self, "btn_patch_online", None),
                #getattr(self, "btn_backup_gui", None),
                getattr(self, "btn_repo_url", None),
                getattr(self, "btn_check_commit", None),
                getattr(self, "btn_modifier", None),
                getattr(self, "btn_check_tools", None),
            ):
                button.setMinimumSize(250, 50)
                # Keine künstliche Maximalbreite: sonst entstehen im rechten
                # Bereich große Lücken, obwohl das Grid seine Spalten streckt.
                button.setMaximumHeight(50)
                button.setSizePolicy(
                    QSizePolicy.Policy.Expanding,
                    QSizePolicy.Policy.Fixed,
                )

        # Erst kompakt einsetzen; ResponsiveGridReflow ordnet danach anhand
        # der realen Containerbreite in passende Spalten um.
        for index, button in enumerate(ordered_patch_buttons):
            patch_layout.addWidget(button, index, 0)

        # ResponsiveGridReflow liest dieselbe Layout-Reihenfolge aus und
        # passt die Spaltenzahl beim Fenster-Resize automatisch an.
        reflow = getattr(self, "_patch_grid_reflow", None)
        if reflow is not None:
            reflow.reflow()

        # Weitere Werkzeuge in einer separaten, responsiven Sektion.

        self.setup_option_buttons(main_layout)

        # Revisions-Buttons auf dieselbe Zeile wie „Oscam Git URL“ setzen.
        # Repo URL bleibt oben neben Emu-Git und wird nicht erneut verschoben.
        revision_button_min_width = 220
        revision_button_height = max(
            self.oscam_source_button.sizeHint().height(),
            self.oscam_source_button.height(),
            50,
        )
        for col, key in ((7, "streamboard_checkout"), (8, "oscam_emu_revision")):
            entry = self.option_buttons.get(key)
            if entry:
                button = entry[0] if isinstance(entry, (tuple, list)) else entry
                # Keine feste Breite erzwingen: Das Grid muss die Buttons an
                # die tatsächlich verfügbare Fensterbreite anpassen können.
                button.setMinimumWidth(revision_button_min_width)
                button.setMaximumWidth(16777215)
                button.setFixedHeight(revision_button_height)
                button.setSizePolicy(
                    QSizePolicy.Policy.Expanding,
                    QSizePolicy.Policy.Fixed,
                )
                button.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
                button.setStyleSheet(
                    button.styleSheet()
                    + "\nQPushButton { padding-left: 6px; padding-right: 6px; margin: 0px; }"
                )
                button.setToolTip(button.text())
                grid_layout.addWidget(
                    button, 1, col,
                    alignment=Qt.AlignmentFlag.AlignVCenter
                )


        
        
        # ---------------------------------------------------------
        # TIMER & DIGITAL CLOCK
        # ---------------------------------------------------------
        def update_digital_clock():
            now = QDateTime.currentDateTime()
            # Aktiviert die digitale Uhrzeit-Aktualisierung:
            self.digital_clock.setText(now.toString("HH:mm:ss"))
            # Aktualisiert das Datum:
            self.date_label.setText(now.toString("dd.MM.yyyy"))

        self.clock_timer = QTimer(self)
        self.clock_timer.timeout.connect(update_digital_clock)
        self.clock_timer.start(1000)
        update_digital_clock()


        # ---------------------------------------------------------
        # FINAL SETTINGS
        # ---------------------------------------------------------
        self.change_colors()
        self.update_language()

        # Einheitliches Farbschema nach der vollständigen UI-Initialisierung
        # nochmals anwenden. Einige Widgets/Statusprüfungen werden verzögert
        # erstellt oder aktualisiert; diese Aufrufe stellen sicher, dass deren
        # individuelle Styles nicht dauerhaft das Theme überschreiben.
        # Stats bleibt eine QCheckBox und wird vom Button-Styling nicht verändert.
        for _style_delay in (0, 250, 1000):
            QTimer.singleShot(
                _style_delay,
                lambda: self.apply_global_button_style()
                if hasattr(self, "apply_global_button_style") else None,
            )

        # 2. Fenster-Eigenschaften bildschirmabhängig festlegen
        # Mindestgröße an die tatsächlich verfügbare Bildschirmfläche anpassen.
        # So verhindert die GUI auf kleineren Displays keine passende Fenstergröße.
        screen = QApplication.primaryScreen()
        if screen is not None:
            available = screen.availableGeometry()  # ohne Taskleiste/Dock
            min_width = min(1200, max(640, available.width() - 50))
            min_height = min(850, max(480, available.height() - 80))
        else:
            min_width, min_height = 800, 600
        self.setMinimumSize(min_width, min_height)
        self.setWindowTitle(
            f"OSCam Toolkit v{APP_VERSION} | {getattr(self, 'patch_modifier', 'speedy005')}"
        )

        # 3. Maximiert starten, damit die GUI die verfügbare Bildschirmfläche nutzt.
        # Die Mindestgröße ist oben bereits an die Auflösung angepasst.
        self.showMaximized()
        QApplication.processEvents()  # WICHTIG: Berechnet die 60px Höhe vor dem ersten Klick
        self.updateGeometry()  # Stabilisiert das FlowLayout

        # 4. Signale sind bereits an den jeweiligen Handlern verbunden:
        #    Farbe -> change_colors(), Commits -> commit_value_changed().
        #    Keine zweite Verbindung hier, damit kein doppeltes Speichern/Feedback entsteht.

        # 5. System-Check mit kleiner Verzögerung starten
        # Wir definieren das Design zentral für ALLE Buttons der App
        neon_style = """
            QPushButton {
                background-color: #3d3d3d !important;
                color: #EAFF00 !important;
                border: 1px solid #555 !important;
                border-radius: 8px !important;
                padding: 5px !important;
                font-weight: bold !important;
                font-size: 12pt !important;
            }
            QPushButton:hover {
                background-color: #4d4d4d !important;
                border: 1px solid #EAFF00 !important;
                color: white !important;
            }
        """

        # --- THEME BEIM START LADEN (Matrix-Check) ---
        # 1. Sicherstellen, dass wir die aktuellsten Daten haben
        # Falls self.current_config noch leer ist, laden wir sie hier kurz manuell
        if not hasattr(self, "current_config") or not self.current_config:
            if os.path.exists(CONFIG_FILE):
                try:
                    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                        self.current_config = json.load(f)
                    self.cfg = self.current_config
                except Exception as e:
                    print(f"Fehler beim Laden der Konfiguration: {e}")
                    self.current_config = {}
                    self.cfg = self.current_config
            else:
                self.current_config = {}
                self.cfg = self.current_config

        # S3-Git-Repositories
        self.s3_repository_options = {
            "S3 Simplebuild (Original)": "https://github.com/gorgone/s3_releases.git",
            "S3 Simplebuild speeddy005": "https://github.com/speedy005/speedy005-S3-Simplebuild.git",
        }

        # Gespeicherte Auswahl wiederherstellen
        self.selected_s3_repository = self.current_config.get(
            "s3_repository_name",
            "S3 Simplebuild (Original)"
        )

        if self.selected_s3_repository not in self.s3_repository_options:
            self.selected_s3_repository = "S3 Simplebuild (Original)"

        self.s3_repo_combo = None
        self.s3_install_worker = None

        cfg = getattr(self, "current_config", {})
        saved_theme = cfg.get("theme_mode", "standard")

        # 2. Matrix erzwingen
        if saved_theme == "matrix":
            # Wir setzen is_loading auf True, um Sounds/Logs beim Start zu blockieren
            was_loading = getattr(self, "is_loading", False)
            self.is_loading = True

            # WICHTIG: Erst das normale Stylesheet löschen, dann Matrix setzen
            self.setStyleSheet("")
            self.toggle_matrix(force_state="matrix")

            # Uhr-Update
            if hasattr(self, "analog_clock"):
                self.analog_clock.update()

            self.is_loading = was_loading
        else:
            # Falls nicht Matrix, Standard-Neon setzen
            self.setStyleSheet(self.styleSheet() + neon_style)

    # =====================
    # BUTTON & COLOR HANDLING
    # =====================
    def update_buttons_language(self):
        self.github_upload_patch_button.setText(TEXTS[LANG]["github_upload_patch"])
        self.github_upload_emu_button.setText(TEXTS[LANG]["github_upload_emu"])
    
    def checkout_streamboard_revision(self):
        """
        Wählt eine beliebige Streamboard-Git-Revision aus und checkt sie
        im lokalen STREAMREPO als detached HEAD aus.

        Wichtig:
        - Es werden keine untracked Dateien gelöscht.
        - Bei einem nicht sauberen Working Tree wird der Vorgang abgebrochen.
        - Ein Commit kann aus der Liste gewählt oder als SHA manuell eingegeben werden.
        - Die Auswahl wird in self.selected_streamboard_commit gespeichert und
          anschließend von create_patch() als Patch-Basis verwendet.
        """
        import os
        import re
        import subprocess

        from PyQt6.QtWidgets import (
            QApplication,
            QComboBox,
            QDialog,
            QDialogButtonBox,
            QLabel,
            QLineEdit,
            QListWidget,
            QListWidgetItem,
            QMessageBox,
            QPushButton,
            QVBoxLayout,
        )

        lang = str(getattr(self, "LANG", "de")).lower()[:2]
        is_de = lang == "de"

        texts = getattr(self, "TEXT", {}) or {}

        def tr(key, de_fallback, en_fallback):
            return texts.get(key) or (
                de_fallback if is_de else en_fallback
            )

        stream_dir = os.path.abspath(
            globals().get("STREAMREPO", "")
        )
        stream_choices = dict(STREAM_REPO_CHOICES)
        stream_url = str(get_selected_stream_repo_url()).strip()

        
        if stream_url not in stream_choices.values():
            stream_choices[tr(
                "current_source",
                "Aktuelle Quelle",
                "Current Source"
            )] = stream_url


        if not stream_dir:
            QMessageBox.critical(
                self,
                "Streamboard",
                "STREAMREPO ist nicht definiert."
                if is_de else
                "STREAMREPO is not defined."
            )
            return False

        try:
            git = find_git_executable()
        except Exception:
            git = None

        if not git:
            QMessageBox.critical(
                self,
                "Git",
                "Git wurde nicht gefunden."
                if is_de else
                "Git was not found."
            )
            return False

        def git_run(args, cwd=stream_dir, check=True, timeout=1200):
            result = subprocess.run(
                [git] + list(args),
                cwd=cwd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
            )
            out = (result.stdout or "").strip()
            err = (result.stderr or "").strip()
            if check and result.returncode != 0:
                raise RuntimeError(err or out or "Git command failed")
            return out

        try:
            # Repository bei Bedarf anlegen/klonen.
            if not os.path.isdir(stream_dir) or not os.path.isdir(
                os.path.join(stream_dir, ".git")
            ):
                ensure_git_repository(
                    stream_dir,
                    stream_url,
                    description="Streamboard",
                    clone_timeout=1200,
                )

            # Sicherheitsprüfung: niemals uncommitted Änderungen überschreiben.
            status = git_run(["status", "--porcelain"], timeout=60)
            if status:
                QMessageBox.warning(
                    self,
                    "Streamboard",
                    (
                        "Das Streamboard-Arbeitsverzeichnis enthält lokale Änderungen.\n\n"
                        "Bitte zuerst committen/stashen oder den Ordner bereinigen.\n"
                        "Es werden von diesem Dialog keine Dateien gelöscht."
                    ) if is_de else (
                        "The Streamboard working tree contains local changes.\n\n"
                        "Please commit/stash them or clean the repository first.\n"
                        "This dialog will not delete any files."
                    )
                )
                return False

            # Commit-Liste aus dem aktuell ausgewählten Repository laden.
            git_run(
                [
                    "fetch",
                    stream_url,
                    "refs/heads/master:refs/remotes/revision-picker/master",
                ],
                timeout=1200,
            )

            # Aktuellen Remote-Master direkt vom Server bestimmen.
            remote_head = ""
            try:
                remote_head = git_run(
                    ["ls-remote", "--exit-code", stream_url, "refs/heads/master"],
                    cwd=stream_dir,
                    timeout=120,
                ).split()[0]
            except Exception:
                remote_head = ""

            # Letzte 100 Commits aus dem ausgewählten Repository.
            log_output = git_run(
                [
                    "log",
                    "revision-picker/master",
                    "-100",
                    "--date=format:%Y-%m-%d %H:%M",
                    "--format=%H%x09%ad%x09%s",
                ],
                timeout=120,
            )

            commits = []
            seen = set()
            for line in log_output.splitlines():
                parts = line.split("\t", 2)
                if len(parts) != 3:
                    continue
                sha, date_text, subject = parts
                sha = sha.strip()
                if not re.fullmatch(r"[0-9a-fA-F]{40}", sha):
                    continue
                if sha in seen:
                    continue
                seen.add(sha)
                commits.append((sha, date_text, subject))

            if remote_head and remote_head not in seen:
                try:
                    git_run(["fetch", stream_url, remote_head], timeout=1200)
                    subject = git_run(
                        ["show", "-s", "--format=%s", remote_head],
                        timeout=60,
                    )
                    date_text = git_run(
                        ["show", "-s", "--date=format:%Y-%m-%d %H:%M", "--format=%ad", remote_head],
                        timeout=60,
                    )
                    commits.insert(0, (remote_head, date_text, subject))
                    seen.add(remote_head)
                except Exception:
                    pass

            dialog = QDialog(self)
            dialog.setModal(True)
            dialog.resize(900, 620)
            
            dialog.setWindowTitle(
                tr(
                    "select_revision",
                    "Streamboard Git-Revision auswählen",
                    "Select Streamboard Git Revision"
                )
            )


            layout = QVBoxLayout(dialog)

            info = QLabel(
                (
                    "Wähle einen Commit aus oder gib einen vollständigen Git-SHA ein.\n"
                    "Der ausgewählte Commit wird detached ausgecheckt und für die Patch-Erstellung verwendet."
                ) if is_de else (
                    "Select a commit or enter a full Git SHA.\n"
                    "The selected commit will be checked out detached and used as the patch base."
                )
            )
            info.setWordWrap(True)
            layout.addWidget(info)
            repo_label = QLabel(
                "Repository für Commit-Liste:" if is_de else
                "Repository for commit list:"
            )
            layout.addWidget(repo_label)

            repo_combo = QComboBox(dialog)
            repo_names = list(stream_choices.keys())
            selected_repo_name = next(
                (
                    name for name, url in stream_choices.items()
                    if url == stream_url
                ),
                repo_names[0] if repo_names else "",
            )
            repo_combo.addItems(repo_names)
            if selected_repo_name:
                repo_combo.setCurrentText(selected_repo_name)
            layout.addWidget(repo_combo)
            current = getattr(self, "selected_streamboard_commit", "")
            if current:
                current_label = QLabel(
                    f"Aktuell ausgewählt: {current}" if is_de else
                    f"Currently selected: {current}"
                )
                layout.addWidget(current_label)

            commit_list = QListWidget(dialog)
            layout.addWidget(commit_list)

            for sha, date_text, subject in commits:
                item = QListWidgetItem(
                    f"{date_text}  {sha[:12]}  {subject}"
                )
                item.setData(0x0100, sha)
                commit_list.addItem(item)

            sha_edit = QLineEdit(dialog)
            sha_edit.setPlaceholderText(
                "Vollständigen 40-stelligen Commit-SHA eingeben"
                if is_de else
                "Enter full 40-character commit SHA"
            )
            layout.addWidget(sha_edit)

            refresh_button = QPushButton(
                "Commit-Liste aktualisieren" if is_de else "Refresh commit list"
            )
            layout.addWidget(refresh_button)

            
            buttons = QDialogButtonBox(
                QDialogButtonBox.StandardButton.Ok
                | QDialogButtonBox.StandardButton.Cancel,
                parent=dialog,
            )

            buttons.button(
                QDialogButtonBox.StandardButton.Ok
            ).setText("OK" if is_de else "OK")

            buttons.button(
                QDialogButtonBox.StandardButton.Cancel
            ).setText("Abbrechen" if is_de else "Cancel")

            layout.addWidget(buttons)

            layout.addWidget(buttons)

            def use_list_selection():
                item = commit_list.currentItem()
                if item is not None:
                    sha_edit.setText(str(item.data(0x0100)))

            commit_list.itemDoubleClicked.connect(
                lambda _item: use_list_selection()
            )
            commit_list.currentItemChanged.connect(
                lambda _current, _previous: use_list_selection()
            )

            # Refresh schließt den Dialog nicht. Die Liste wird neu aufgebaut.
            
            def refresh_list():
                try:
                    if git_run(["status", "--porcelain"], timeout=60):
                        
                        QMessageBox.warning(
                            dialog,
                            "Streamboard",
                            tr(
                                "working_tree_not_clean",
                                "Arbeitsverzeichnis ist nicht sauber.",
                                "Working tree is not clean."
                            )
                        )

                        return

                    selected_name = repo_combo.currentText()
                    selected_url = str(
                        stream_choices.get(selected_name, "")
                    ).strip()

                    
                    if not selected_url:
                        raise RuntimeError(
                            tr(
                                "no_repository_url",
                                "Für die ausgewählte Quelle ist keine URL definiert.",
                                "No URL is defined for the selected source."
                            )
                        )


                    remote_ref = "refs/remotes/revision-picker/master"

                    
                    git_run(
                        [
                            "fetch",
                            "--force",
                            selected_url,
                            f"+refs/heads/master:{remote_ref}",
                        ],
                        timeout=1200,
                    )


                    fresh = git_run(
                        [
                            "log",
                            remote_ref,
                            "-100",
                            "--date=format:%Y-%m-%d %H:%M",
                            "--format=%H%x09%ad%x09%s",
                        ],
                        timeout=120,
                    )

                    commit_list.clear()
                    sha_edit.clear()
                    local_seen = set()

                    for line in fresh.splitlines():
                        parts = line.split("\t", 2)
                        if len(parts) != 3:
                            continue

                        sha, date_text, subject = parts
                        sha = sha.lower()

                        if not re.fullmatch(r"[0-9a-f]{40}", sha):
                            continue
                        if sha in local_seen:
                            continue

                        local_seen.add(sha)
                        refreshed_item = QListWidgetItem(
                            f"{date_text}  {sha[:12]}  {subject}"
                        )
                        refreshed_item.setData(0x0100, sha)
                        commit_list.addItem(refreshed_item)

                except Exception as exc:
                    QMessageBox.critical(
                        dialog,
                        "Git",
                        str(exc),
                    )

            refresh_button.clicked.connect(refresh_list)
            repo_combo.currentTextChanged.connect(
                lambda _name: refresh_list()
            )
            buttons.accepted.connect(dialog.accept)
            buttons.rejected.connect(dialog.reject)

            if dialog.exec() != QDialog.DialogCode.Accepted:
                return False
            stream_url = str(
                stream_choices.get(repo_combo.currentText(), "")
            ).strip()

            
            if not stream_url:
                QMessageBox.warning(
                    self,
                    "Streamboard",
                    tr(
                        "no_repository_url",
                        "Für das ausgewählte Repository fehlt die URL.",
                        "The selected repository has no URL."
                    )
                )
                return False

                
            selected = sha_edit.text().strip()
            if not selected:
                QMessageBox.warning(
                    self,
                    "Streamboard",
                    "Bitte einen Commit auswählen oder eine SHA eingeben."
                    if is_de else
                    "Please select a commit or enter a SHA."
                )
                return False

            if not re.fullmatch(r"[0-9a-fA-F]{40}", selected):
                QMessageBox.warning(
                    self,
                    "Streamboard",
                    "Es muss eine vollständige 40-stellige Git-SHA eingegeben werden."
                    if is_de else
                    "A complete 40-character Git SHA is required."
                )
                return False

            selected = selected.lower()

            # Commit bei manueller Eingabe explizit vom Streamboard-Server holen.
            local_selected = git_run(
                ["rev-parse", "--verify", selected + "^{commit}"],
                check=False,
                timeout=60,
            )
            if local_selected != selected:
                git_run(
                    ["fetch", stream_url, selected],
                    timeout=1200,
                )
                local_selected = git_run(
                    ["rev-parse", "--verify", selected + "^{commit}"],
                    check=False,
                    timeout=60,
                )

            if local_selected != selected:
                raise RuntimeError(
                    "Der gewünschte Commit konnte nicht aus Streamboard geladen werden."
                    if is_de else
                    "The requested commit could not be fetched from Streamboard."
                )

            # Exakt diesen Commit auschecken. Kein Reset --hard und kein git clean.
            git_run(
                ["checkout", "--detach", selected],
                timeout=1200,
            )

            checked_out = git_run(
                ["rev-parse", "HEAD"],
                timeout=60,
            )
            if checked_out != selected:
                raise RuntimeError(
                    "Der ausgewählte Commit konnte nicht exakt ausgecheckt werden."
                    if is_de else
                    "The selected commit could not be checked out exactly."
                )

            self.selected_streamboard_commit = selected
            self.selected_streamboard_commit_repo_url = stream_url
            self.display_streamboard_commit = selected
            self.update_revision_button_labels()
            # Commit und Herkunft für bestehende Helfer verfügbar machen.
            globals()["SELECTED_STREAMBOARD_COMMIT"] = selected
            globals()["SELECTED_STREAMBOARD_COMMIT_REPO_URL"] = stream_url

            # Button/Status optisch aktualisieren, falls vorhanden.
            status_text = (
                f"Streamboard Basis: {selected[:12]}"
                if is_de else
                f"Streamboard base: {selected[:12]}"
            )
            try:
                if hasattr(self, "info_text"):
                    self.info_text.append(
                        f"<b>✅ {status_text}</b>"
                    )
                    self.info_text.ensureCursorVisible()
            except Exception:
                pass

            try:
                QApplication.processEvents()
            except Exception:
                pass

            QMessageBox.information(
                self,
                "Streamboard",
                (
                    f"Commit erfolgreich ausgewählt:\n\n{selected}\n\n"
                    "Dieser Commit wird bei der nächsten Patch-Erstellung als Basis verwendet."
                ) if is_de else (
                    f"Commit selected successfully:\n\n{selected}\n\n"
                    "This commit will be used as the base for the next patch."
                )
            )
            return True

        except Exception as exc:
            print("[STREAMBOARD CHECKOUT] Fehler:", repr(exc), flush=True)
            try:
                QMessageBox.critical(
                    self,
                    "Streamboard Git",
                    f"Fehler:\n{exc}"
                )
            except Exception:
                pass
            return False

    def checkout_emu_revision(self):
        """
        Wählt eine beliebige OSCam-Emu-Git-Revision aus und checkt sie
        im lokalen PATCH_EMU_GIT_DIR als detached HEAD aus.

        Das Verhalten entspricht dem Streamboard-Revisionsdialog:
        - letzte 100 Commits anzeigen
        - Commit per Liste oder vollständiger SHA auswählen
        - Commit-Liste aktualisieren
        - Auswahl in self.selected_emu_commit speichern
        - Auswahl wird beim nächsten Patchlauf als exakte OSCam-Emu-Basis verwendet
        """
        import os
        import re
        import subprocess

        from PyQt6.QtWidgets import (
            QApplication,
            QComboBox,
            QDialog,
            QDialogButtonBox,
            QLabel,
            QLineEdit,
            QListWidget,
            QListWidgetItem,
            QMessageBox,
            QPushButton,
            QVBoxLayout,
        )

        lang = str(getattr(self, "LANG", "de")).lower()[:2]
        is_de = lang == "de"

        texts = getattr(self, "TEXT", {}) or {}

        def tr(key, de_fallback, en_fallback):
            return texts.get(key) or (
                de_fallback if is_de else en_fallback
            )

        emu_dir = os.path.abspath(
            globals().get("PATCH_EMU_GIT_DIR", "")
        )

        # Die aktive OSCam-Emu-URL kommt aus der bestehenden GUI-Konfiguration.
        try:
            emu_url, emu_branch = get_emu_repository_config()
        except Exception:
            emu_url = globals().get(
                "EMUREPO_URL",
                "https://github.com/oscam-mirror/oscam-emu.git"
            )
            emu_branch = "master"

        emu_url = str(emu_url or "").strip()
        emu_branch = str(emu_branch or "master").strip() or "master"
        emu_repo_choices = {
            "OSCam-EMU Mirror": "https://github.com/oscam-mirror/oscam-emu.git",
            "speedy005 OSCam-EMU": "https://github.com/speedy005/Oscam-emu.git",
        }

        
        if emu_url not in emu_repo_choices.values():
            emu_repo_choices[tr(
                "current_source",
                "Aktuelle Quelle",
                "Current Source"
            )] = emu_url

        if not emu_dir:
            QMessageBox.critical(
                self,
                "OSCam-Emu",
                "PATCH_EMU_GIT_DIR ist nicht definiert."
                if is_de else
                "PATCH_EMU_GIT_DIR is not defined."
            )
            return False

        try:
            git = find_git_executable()
        except Exception:
            git = None

        if not git:
            QMessageBox.critical(
                self,
                "Git",
                "Git wurde nicht gefunden."
                if is_de else
                "Git was not found."
            )
            return False

        def git_run(args, cwd=emu_dir, check=True, timeout=1200):
            result = subprocess.run(
                [git] + list(args),
                cwd=cwd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
            )
            out = (result.stdout or "").strip()
            err = (result.stderr or "").strip()
            if check and result.returncode != 0:
                raise RuntimeError(err or out or "Git command failed")
            return out

        try:
            # Repository bei Bedarf mit der aktuell konfigurierten URL anlegen.
            if not os.path.isdir(emu_dir) or not os.path.isdir(
                os.path.join(emu_dir, ".git")
            ):
                ensure_git_repository(
                    emu_dir,
                    emu_url,
                    description="OSCam-Emu",
                    clone_timeout=1200,
                )

            # Das lokale OSCam-Emu-Repository immer an die aktuell über
            # "Repo URL" ausgewählte Quelle anbinden.
            #
            # WICHTIG: sync_emu_origin() ist ein lokaler Helfer innerhalb
            # von create_patch() und steht in diesem GUI-Dialog nicht zur
            # Verfügung. Deshalb wird die Origin-URL hier direkt
            # synchronisiert. So entsteht kein "name ... is not defined".
            current_origin = git_run(
                ["remote", "get-url", "origin"],
                check=False,
                timeout=60,
            ).strip()

            if not current_origin:
                git_run(
                    ["remote", "add", "origin", emu_url],
                    timeout=60,
                )
            elif current_origin.rstrip("/") != emu_url.rstrip("/"):
                git_run(
                    ["remote", "set-url", "origin", emu_url],
                    timeout=60,
                )

            # Keine lokalen Änderungen überschreiben.
            status = git_run(["status", "--porcelain"], timeout=60)
            if status:
                QMessageBox.warning(
                    self,
                    "OSCam-Emu",
                    (
                        "Das OSCam-Emu-Arbeitsverzeichnis enthält lokale Änderungen.\n\n"
                        "Bitte zuerst committen/stashen oder den Ordner bereinigen.\n"
                        "Es werden von diesem Dialog keine Dateien gelöscht."
                    ) if is_de else (
                        "The OSCam-Emu working tree contains local changes.\n\n"
                        "Please commit/stash them or clean the repository first.\n"
                        "This dialog will not delete any files."
                    )
                )
                return False

            # Den konfigurierten Branch aktualisieren und als Remote-Referenz bereitstellen.
            git_run(["fetch", "origin", emu_branch], timeout=1200)

            remote_ref = f"origin/{emu_branch}"
            if not git_run(
                ["rev-parse", "--verify", remote_ref],
                check=False,
                timeout=60,
            ):
                raise RuntimeError(
                    f"OSCam-Emu Branch konnte nicht gefunden werden: {emu_branch}"
                )

            commits_raw = git_run(
                [
                    "log",
                    remote_ref,
                    "-100",
                    "--date=format:%Y-%m-%d %H:%M",
                    "--format=%H%x09%ad%x09%s",
                ],
                timeout=120,
            )

            commits = []
            for line in commits_raw.splitlines():
                parts = line.split("\t", 2)
                if len(parts) != 3:
                    continue
                sha, date_text, subject = parts
                if not re.fullmatch(r"[0-9a-fA-F]{40}", sha):
                    continue
                commits.append((sha.lower(), date_text, subject))

            
            dialog = QDialog(self)
            dialog.setWindowTitle(
                tr(
                    "select_revision",
                    "OSCam-Emu Revision auswählen",
                    "Select OSCam-Emu Revision"
                )
            )
            dialog.resize(850, 600)

            layout = QVBoxLayout(dialog)
            repo_label = QLabel(
                tr(
                    "select_repository",
                    "Git-Repository für die Commit-Liste:",
                    "Git repository for the commit list:"
                )
            )
            layout.addWidget(repo_label)


            repo_combo = QComboBox(dialog)

            for repo_name, repo_url in emu_repo_choices.items():
                repo_combo.addItem(repo_name, repo_url)

            current_index = repo_combo.findData(emu_url)
            if current_index >= 0:
                repo_combo.setCurrentIndex(current_index)

            layout.addWidget(repo_combo)
            info = QLabel(
                (
                    "Wähle einen OSCam-Emu-Commit aus oder gib einen vollständigen "
                    "40-stelligen Git-SHA ein."
                ) if is_de else (
                    "Select an OSCam-Emu commit or enter a full 40-character Git SHA."
                )
            )
            info.setWordWrap(True)
            layout.addWidget(info)

            current = getattr(self, "selected_emu_commit", "")
            if current:
                current_label = QLabel(
                    f"Aktuell ausgewählt: {current}"
                    if is_de else
                    f"Currently selected: {current}"
                )
                layout.addWidget(current_label)

            commit_list = QListWidget(dialog)
            layout.addWidget(commit_list)

            for sha, date_text, subject in commits:
                item = QListWidgetItem(
                    f"{date_text}  {sha[:12]}  {subject}"
                )
                item.setData(0x0100, sha)
                commit_list.addItem(item)

            sha_edit = QLineEdit(dialog)
            sha_edit.setPlaceholderText(
                "Vollständigen 40-stelligen Commit-SHA eingeben"
                if is_de else
                "Enter full 40-character commit SHA"
            )
            layout.addWidget(sha_edit)

            refresh_button = QPushButton(
                "Commit-Liste aktualisieren"
                if is_de else
                "Refresh commit list"
            )
            layout.addWidget(refresh_button)

            
            buttons = QDialogButtonBox(
                QDialogButtonBox.StandardButton.Ok
                | QDialogButtonBox.StandardButton.Cancel,
                parent=dialog,
            )

            buttons.button(
                QDialogButtonBox.StandardButton.Ok
            ).setText("OK" if is_de else "OK")

            buttons.button(
                QDialogButtonBox.StandardButton.Cancel
            ).setText("Abbrechen" if is_de else "Cancel")

            layout.addWidget(buttons)


            def use_list_selection():
                item = commit_list.currentItem()
                if item is not None:
                    sha_edit.setText(str(item.data(0x0100)))

            commit_list.itemDoubleClicked.connect(
                lambda _item: use_list_selection()
            )
            commit_list.currentItemChanged.connect(
                lambda _current, _previous: use_list_selection()
            )

            
            def refresh_list():
                try:
                    if git_run(["status", "--porcelain"], timeout=60):
                        
                        QMessageBox.warning(
                            dialog,
                            "OSCam-Emu",
                            tr(
                                "working_tree_not_clean",
                                "Arbeitsverzeichnis ist nicht sauber.",
                                "Working tree is not clean."
                            )
                        )

                        return

                    selected_url = str(
                        repo_combo.currentData() or ""
                    ).strip()

                    
                    if not selected_url:
                        raise RuntimeError(
                            tr(
                                "no_repository_url",
                                "Keine Repository-URL ausgewählt.",
                                "No repository URL selected."
                            )
                        )


                    remote_ref = "refs/remotes/revision-picker/emu"

                    
                    git_run(
                        [
                            "fetch",
                            "--force",
                            selected_url,
                            f"+refs/heads/{emu_branch}:{remote_ref}",
                        ],
                        timeout=1200,
                    )


                    fresh = git_run(
                        [
                            "log",
                            remote_ref,
                            "-100",
                            "--date=format:%Y-%m-%d %H:%M",
                            "--format=%H%x09%ad%x09%s",
                        ],
                        timeout=120,
                    )

                    commit_list.clear()
                    sha_edit.clear()
                    local_seen = set()

                    for line in fresh.splitlines():
                        parts = line.split("\t", 2)
                        if len(parts) != 3:
                            continue

                        sha, date_text, subject = parts
                        if not re.fullmatch(r"[0-9a-fA-F]{40}", sha):
                            continue

                        sha = sha.lower()
                        if sha in local_seen:
                            continue
                        local_seen.add(sha)

                        item = QListWidgetItem(
                            f"{date_text}  {sha[:12]}  {subject}"
                        )
                        item.setData(0x0100, sha)
                        commit_list.addItem(item)

                except Exception as exc:
                    QMessageBox.critical(
                        dialog,
                        "Git",
                        str(exc),
                    )

            refresh_button.clicked.connect(refresh_list)
            repo_combo.currentIndexChanged.connect(
                lambda _index: refresh_list()
            )

            buttons.accepted.connect(dialog.accept)
            buttons.rejected.connect(dialog.reject)

            if dialog.exec() != QDialog.DialogCode.Accepted:
                return False

            selected = sha_edit.text().strip().lower()

            selected_emu_url = str(
                repo_combo.currentData() or ""
            ).strip()

                
            if not selected_emu_url:
                QMessageBox.warning(
                    self,
                    "OSCam-Emu",
                    tr(
                        "select_repository_prompt",
                        "Bitte ein Repository auswählen.",
                        "Please select a repository."
                    )
                )
                return False

                

            if not selected:
                QMessageBox.warning(
                    self,
                    "OSCam-Emu",
                    "Bitte einen Commit auswählen oder eine SHA eingeben."
                    if is_de else
                    "Please select a commit or enter a SHA."
                )
                return False

            if not re.fullmatch(r"[0-9a-f]{40}", selected):
                QMessageBox.warning(
                    self,
                    "OSCam-Emu",
                    "Es muss eine vollständige 40-stellige Git-SHA eingegeben werden."
                    if is_de else
                    "A complete 40-character Git SHA is required."
                )
                return False

            # Manuell eingegebene SHA bei Bedarf direkt vom aktiven OSCam-Emu-Remote holen.
            local_selected = git_run(
                ["rev-parse", "--verify", selected + "^{commit}"],
                check=False,
                timeout=60,
            )

            
            if local_selected != selected:
                git_run(
                    ["fetch", selected_emu_url, selected],
                    timeout=1200,
                )


            if local_selected != selected:
                raise RuntimeError(
                    "Der gewünschte OSCam-Emu-Commit konnte nicht aus dem Repository geladen werden."
                    if is_de else
                    "The requested OSCam-Emu commit could not be fetched from the repository."
                )

            # Exakt diesen Commit auschecken.
            git_run(
                ["checkout", "--detach", selected],
                timeout=1200,
            )

            checked_out = git_run(
                ["rev-parse", "HEAD"],
                check=False,
                timeout=60,
            )

            if checked_out != selected:
                raise RuntimeError(
                    "Der ausgewählte OSCam-Emu-Commit konnte nicht exakt ausgecheckt werden."
                    if is_de else
                    "The selected OSCam-Emu commit could not be checked out exactly."
                )

            # Commit und das dazugehörige Repository gemeinsam merken.
            # Eine Revision darf später niemals versehentlich mit einer
            # anderen OSCam-Emu-Repo-URL verwendet werden.
            self.selected_emu_commit = selected
            self.selected_emu_commit_repo_url = selected_emu_url
            self.display_emu_commit = selected
            globals()["SELECTED_EMU_COMMIT"] = selected
            globals()["SELECTED_EMU_COMMIT_REPO_URL"] = selected_emu_url
            self.update_revision_button_labels()

            status_text = (
                f"OSCam-Emu Basis: {selected[:12]}"
                if is_de else
                f"OSCam-Emu base: {selected[:12]}"
            )

            try:
                if hasattr(self, "info_text"):
                    self.info_text.append(f"<b>✅ {status_text}</b>")
                    self.info_text.ensureCursorVisible()
                QApplication.processEvents()
            except Exception:
                pass

            QMessageBox.information(
                self,
                "OSCam-Emu",
                (
                    f"Commit erfolgreich ausgewählt:\n\n{selected}\n\n"
                    "Dieser Commit wird bei der nächsten Patch-Erstellung als OSCam-Emu-Basis verwendet."
                ) if is_de else (
                    f"Commit selected successfully:\n\n{selected}\n\n"
                    "This commit will be used as the OSCam-Emu base for the next patch."
                )
            )
            return True

        except Exception as exc:
            print("[OSCAM-EMU CHECKOUT] Fehler:", repr(exc), flush=True)
            try:
                QMessageBox.critical(
                    self,
                    "OSCam-Emu Git",
                    f"Fehler:\n{exc}"
                )
            except Exception:
                pass
            return False

    def setup_grid_buttons(self):
            """
            Erstellt die Aktions-Buttons für die Patch-Verwaltung.

            Verwendet ausschließlich das bereits in init_ui()
            erzeugte self.grid_container und self.layout_grid_buttons.

            Der Patch-Check besitzt einen direkten Qt-Signal-Handler,
            damit sicher festgestellt werden kann, ob der Button-Klick
            überhaupt ankommt.
            """

            from PyQt6.QtWidgets import (
                QSizePolicy,
                QApplication,
                QStyle,
            )
            from PyQt6.QtGui import QIcon
            from PyQt6.QtCore import QSize, QTimer

            print(
                "[GRID SETUP] setup_grid_buttons() START",
                flush=True
            )

            # =========================================================
            # SPRACHE
            # =========================================================

            is_de = (
                getattr(
                    self,
                    "LANG",
                    "de"
                ).lower() == "de"
            )

            # =========================================================
            # GRID CONTAINER
            # =========================================================

            grid_container = getattr(
                self,
                "grid_container",
                None
            )

            if grid_container is None:
                raise RuntimeError(
                    "self.grid_container wurde vor "
                    "setup_grid_buttons() nicht erstellt."
                )

            grid_layout = getattr(
                self,
                "layout_grid_buttons",
                None
            )

            if grid_layout is None:
                raise RuntimeError(
                    "self.layout_grid_buttons wurde vor "
                    "setup_grid_buttons() nicht erstellt."
                )

            print(
                f"[GRID SETUP] Container vorhanden: "
                f"{grid_container}",
                flush=True
            )

            print(
                f"[GRID SETUP] Layout vorhanden: "
                f"{grid_layout}",
                flush=True
            )

            # =========================================================
            # ALTE BUTTONS ENTFERNEN
            # =========================================================

            print(
                "[GRID SETUP] Entferne alte Buttons...",
                flush=True
            )

            while grid_layout.count():

                item = grid_layout.takeAt(0)

                widget = item.widget()

                if widget is not None:

                    try:
                        widget.deleteLater()
                    except Exception:
                        pass

            grid_layout.setHorizontalSpacing(6)
            grid_layout.setVerticalSpacing(6)
            grid_layout.setContentsMargins(8, 8, 8, 8)

            # =========================================================
            # ICONS
            # =========================================================

            def get_system_icon(
                theme_name: str,
                fallback: QStyle.StandardPixmap
            ):

                icon = QIcon.fromTheme(
                    theme_name
                )

                if not icon.isNull():
                    return icon

                return QApplication.style().standardIcon(
                    fallback
                )

            ICON_MAP = {

                "patch_create": (
                    "document-new",
                    QStyle.StandardPixmap.SP_FileIcon
                ),

                "patch_renew": (
                    "view-refresh",
                    QStyle.StandardPixmap.SP_BrowserReload
                ),

                "patch_check": (
                    "dialog-ok",
                    QStyle.StandardPixmap.SP_DialogApplyButton
                ),

                "patch_apply": (
                    "system-run",
                    QStyle.StandardPixmap.SP_MediaPlay
                ),

                "patch_zip": (
                    "package-x-generic",
                    QStyle.StandardPixmap.SP_DriveFDIcon
                ),

                "backup_old": (
                    "document-save",
                    QStyle.StandardPixmap.SP_DialogSaveButton
                ),

                "clean_folder": (
                    "edit-clear",
                    QStyle.StandardPixmap.SP_TrashIcon
                ),

                "change_old_dir": (
                    "folder-open",
                    QStyle.StandardPixmap.SP_DirOpenIcon
                ),

                "exit": (
                    "application-exit",
                    QStyle.StandardPixmap.SP_DialogCloseButton
                ),
            }

            # =========================================================
            # BUTTON CALLBACKS
            # =========================================================

            def callback_patch_create():

                print(
                    "[GRID CALLBACK] patch_create START",
                    flush=True
                )

                return create_patch(
                    self,
                    self.info_text,
                    self.progress_bar.setValue
                )

            def callback_patch_renew():

                print(
                    "[GRID CALLBACK] patch_renew START",
                    flush=True
                )

                return create_patch(
                    self,
                    self.info_text,
                    self.progress_bar.setValue
                )

            def callback_patch_check():

                print(
                    "",
                    flush=True
                )

                print(
                    "==================================================",
                    flush=True
                )

                print(
                    "[PATCH CHECK BUTTON] CLICK EMPFANGEN",
                    flush=True
                )

                print(
                    "[PATCH CHECK BUTTON] self.check_patch =",
                    getattr(
                        self,
                        "check_patch",
                        None
                    ),
                    flush=True
                )

                print(
                    "[PATCH CHECK BUTTON] info_text =",
                    getattr(
                        self,
                        "info_text",
                        None
                    ),
                    flush=True
                )

                print(
                    "[PATCH CHECK BUTTON] progress_bar =",
                    getattr(
                        self,
                        "progress_bar",
                        None
                    ),
                    flush=True
                )

                try:

                    if hasattr(
                        self,
                        "append_info"
                    ):

                        self.append_info(
                            self.info_text,
                            "🚨 PATCH CHECK BUTTON WURDE GEDRÜCKT!",
                            "info"
                        )

                except Exception as exc:

                    print(
                        "[PATCH CHECK BUTTON] "
                        "append_info Fehler:",
                        repr(exc),
                        flush=True
                    )

                # -----------------------------------------------------
                # check_patch existiert?
                # -----------------------------------------------------

                check_function = getattr(
                    self,
                    "check_patch",
                    None
                )

                if not callable(
                    check_function
                ):

                    print(
                        "[PATCH CHECK BUTTON] "
                        "FEHLER: self.check_patch ist "
                        "nicht aufrufbar!",
                        flush=True
                    )

                    try:

                        self.append_info(
                            self.info_text,
                            "❌ self.check_patch ist nicht aufrufbar!",
                            "error"
                        )

                    except Exception:
                        pass

                    return False

                # -----------------------------------------------------
                # Progress Callback
                # -----------------------------------------------------

                progress_callback = None

                try:

                    if getattr(
                        self,
                        "progress_bar",
                        None
                    ) is not None:

                        progress_callback = (
                            self.progress_bar.setValue
                        )

                except Exception:
                    progress_callback = None

                # -----------------------------------------------------
                # check_patch aufrufen
                # -----------------------------------------------------

                print(
                    "[PATCH CHECK BUTTON] "
                    "Rufe self.check_patch() auf...",
                    flush=True
                )

                try:

                    result = check_function(
                        self.info_text,
                        progress_callback
                    )

                    print(
                        "[PATCH CHECK BUTTON] "
                        f"check_patch() Ergebnis: {result!r}",
                        flush=True
                    )

                    print(
                        "==================================================",
                        flush=True
                    )

                    return result

                except Exception as exc:

                    print(
                        "[PATCH CHECK BUTTON] "
                        "check_patch() EXCEPTION:",
                        repr(exc),
                        flush=True
                    )

                    import traceback

                    traceback.print_exc()

                    try:

                        self.append_info(
                            self.info_text,
                            (
                                "❌ Fehler beim Starten "
                                "des Patch-Checks:\n"
                                f"{exc}"
                            ),
                            "error"
                        )

                    except Exception:
                        pass

                    return False

            def callback_patch_apply():

                print(
                    "[GRID CALLBACK] patch_apply START",
                    flush=True
                )

                is_de = str(getattr(self, "LANG", "de")).lower().startswith("de")
                confirm = QMessageBox(self)
                confirm.setIcon(QMessageBox.Icon.Warning)
                confirm.setWindowTitle("Patch anwenden?" if is_de else "Apply patch?")
                confirm.setText(
                    "Der Patch kann Dateien im Zielprojekt verändern.\n\n"
                    "Stelle sicher, dass wichtige Änderungen gesichert sind.\n"
                    "Möchtest du fortfahren?"
                    if is_de else
                    "The patch may modify files in the target project.\n\n"
                    "Make sure important changes are backed up.\n"
                    "Do you want to continue?"
                )
                confirm.setStandardButtons(
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
                )
                confirm.setDefaultButton(QMessageBox.StandardButton.No)
                confirm.button(QMessageBox.StandardButton.Yes).setText("Ja" if is_de else "Yes")
                confirm.button(QMessageBox.StandardButton.No).setText("Nein" if is_de else "No")
                answer = confirm.exec()
                if answer != QMessageBox.StandardButton.Yes:
                    try:
                        self.append_info(
                            self.info_text,
                            "Patch-Anwendung vom Benutzer abgebrochen.",
                            "warning",
                        )
                    except Exception:
                        pass
                    return False

                try:
                    if self.progress_bar:
                        self.progress_bar.setValue(0)
                except Exception:
                    pass

                try:
                    return self.apply_patch(
                        self.info_text,
                        self.progress_bar.setValue
                    )
                except Exception as exc:
                    try:
                        self.append_info(
                            self.info_text,
                            f"Patch konnte nicht angewendet werden: {exc}",
                            "error",
                        )
                    except Exception:
                        pass
                    QMessageBox.critical(
                        self,
                        "Patch-Fehler",
                        f"Der Patch konnte nicht angewendet werden.\n\n{exc}",
                    )
                    return False

            def callback_patch_zip():

                print(
                    "[GRID CALLBACK] patch_zip START",
                    flush=True
                )

                return self.zip_patch(
                    self.info_text,
                    self.progress_bar.setValue
                )

            def callback_backup_old():

                print(
                    "[GRID CALLBACK] backup_old START",
                    flush=True
                )

                return backup_old_patch(
                    self,
                    make_backup=True,
                    info_widget=self.info_text,
                    progress_callback=self.progress_bar.setValue
                )

            def callback_clean_folder():

                print(
                    "[GRID CALLBACK] clean_folder START",
                    flush=True
                )

                is_de = str(getattr(self, "LANG", "de")).lower().startswith("de")
                confirm = QMessageBox(self)
                confirm.setIcon(QMessageBox.Icon.Warning)
                confirm.setWindowTitle(
                    "Patch-Ordner leeren?" if is_de else "Clear patch folder?"
                )
                confirm.setText(
                    "Diese Aktion kann Dateien im Patch-Ordner löschen.\n\n"
                    "Bitte prüfe, ob du benötigte Dateien gesichert hast.\n"
                    "Möchtest du wirklich fortfahren?"
                    if is_de else
                    "This action may delete files in the patch folder.\n\n"
                    "Make sure you have backed up any files you need.\n"
                    "Do you really want to continue?"
                )
                confirm.setStandardButtons(
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
                )
                confirm.setDefaultButton(QMessageBox.StandardButton.No)
                confirm.button(QMessageBox.StandardButton.Yes).setText("Ja" if is_de else "Yes")
                confirm.button(QMessageBox.StandardButton.No).setText("Nein" if is_de else "No")
                answer = confirm.exec()
                if answer != QMessageBox.StandardButton.Yes:
                    try:
                        self.append_info(
                            self.info_text,
                            "Bereinigung des Patch-Ordners abgebrochen.",
                            "warning",
                        )
                    except Exception:
                        pass
                    return False

                try:
                    return clean_patch_folder(
                        self,
                        self.info_text,
                        self.progress_bar.setValue
                    )
                except Exception as exc:
                    try:
                        self.append_info(
                            self.info_text,
                            f"Patch-Ordner konnte nicht bereinigt werden: {exc}",
                            "error",
                        )
                    except Exception:
                        pass
                    QMessageBox.critical(
                        self,
                        "Bereinigungsfehler",
                        f"Der Patch-Ordner konnte nicht bereinigt werden.\n\n{exc}",
                    )
                    return False

            def callback_change_old_dir():

                print(
                    "[GRID CALLBACK] change_old_dir START",
                    flush=True
                )

                return self.change_old_patch_dir(
                    self.info_text,
                    self.progress_bar.setValue
                )

            def callback_emu_revision():
                print(
                    "[GRID CALLBACK] oscam_emu_revision START",
                    flush=True
                )
                return self.checkout_emu_revision()

            def callback_exit():

                print(
                    "[GRID CALLBACK] exit START",
                    flush=True
                )

                return self.close_with_confirm()

            # =========================================================
            # ACTION-LISTE
            # =========================================================

            grid_actions = [

                (
                    "patch_create",
                    callback_patch_create,
                    "Erstellt einen neuen Patch aus den aktuellen Änderungen",
                    "Creates a new patch from current changes",
                ),

                (
                    "patch_renew",
                    callback_patch_renew,
                    "Erneuert den vorhandenen Patch im Arbeitsverzeichnis",
                    "Renews the existing patch in work directory",
                ),

                (
                    "patch_check",
                    callback_patch_check,
                    "Prüft, ob der Patch sauber auf den Quellcode anwendbar ist",
                    "Checks if the patch can be applied cleanly",
                ),

                (
                    "patch_apply",
                    callback_patch_apply,
                    "Wendet den Patch permanent auf die OSCam-Sourcen an",
                    "Applies the patch permanently to OSCam sources",
                ),

                (
                    "patch_zip",
                    callback_patch_zip,
                    "Packt alle Patch-Dateien in ein ZIP-Archiv",
                    "Compresses all patch files into a ZIP archive",
                ),

                (
                    "backup_old",
                    callback_backup_old,
                    "Sichert den aktuellen Stand in den Backup-Ordner",
                    "Backs up the current state to backup folder",
                ),

                (
                    "clean_folder",
                    callback_clean_folder,
                    "Löscht temporäre Dateien im Patch-Verzeichnis",
                    "Deletes temporary files in patch directory",
                ),

                (
                    "change_old_dir",
                    callback_change_old_dir,
                    "Wählt ein anderes Verzeichnis für alte Patches aus",
                    "Selects a different directory for old patches",
                ),

                (
                    "exit",
                    callback_exit,
                    "Beendet das Programm sicher",
                    "Exit the program safely",
                ),
            ]

            # =========================================================
            # BUTTON-SPEICHER
            # =========================================================

            self.buttons = {}

            if not hasattr(
                self,
                "all_buttons"
            ):
                self.all_buttons = []

            cols = 3

            btn_color = "#1E90FF"

            # =========================================================
            # BUTTONS ERZEUGEN
            # =========================================================

            for idx, (
                key,
                func,
                tt_de,
                tt_en
            ) in enumerate(
                grid_actions
            ):

                button_text = self.get_t(
                    key,
                    key
                )

                # -----------------------------------------------------
                # Sonderfall clean_folder
                # -----------------------------------------------------

                if key == "clean_folder":

                    if (
                        button_text == "clean_folder"
                        or
                        button_text.strip()
                        == "Patch-Ordner"
                    ):

                        button_text = (
                            "Patch-Ordner leeren"
                            if is_de
                            else
                            "Clear Patch Folder"
                        )

                print(
                    f"[GRID] Erzeuge {key}: "
                    f"{button_text!r}",
                    flush=True
                )

                # -----------------------------------------------------
                # CALLBACK-WRAPPER
                #
                # Wichtig:
                # Kein Lambda.
                # Kein verschachteltes [-1].
                # -----------------------------------------------------

                def grid_callback(
                    checked=False,
                    f=func,
                    k=key
                ):

                    print(
                        "",
                        flush=True
                    )

                    print(
                        "--------------------------------------------------",
                        flush=True
                    )

                    print(
                        f"[GRID SIGNAL] clicked: {k}",
                        flush=True
                    )

                    print(
                        f"[GRID SIGNAL] checked: {checked}",
                        flush=True
                    )

                    print(
                        f"[GRID SIGNAL] callback: {f}",
                        flush=True
                    )

                    try:

                        result = self._run_grid_action(
                            k,
                            f
                        )

                        print(
                            f"[GRID SIGNAL] "
                            f"{k} Ergebnis: {result!r}",
                            flush=True
                        )

                        return result

                    except Exception as exc:

                        print(
                            f"[GRID SIGNAL] "
                            f"{k} EXCEPTION:",
                            repr(exc),
                            flush=True
                        )

                        import traceback

                        traceback.print_exc()

                        return False

                # -----------------------------------------------------
                # BUTTON ERZEUGEN
                # -----------------------------------------------------

                print(
                    f"[GRID] create_action_button("
                    f"{key})...",
                    flush=True
                )

                btn = self.create_action_button(
                    parent=grid_container,
                    text=button_text,
                    color=btn_color,
                    fg="white",
                    callback=grid_callback,
                    all_buttons_list=self.all_buttons,
                    min_height=46,
                    radius=self.BUTTON_RADIUS,
                )

                # -----------------------------------------------------
                # BUTTON VALIDIEREN
                # -----------------------------------------------------

                if btn is None:

                    print(
                        f"[GRID ERROR] "
                        f"create_action_button({key}) "
                        f"gab None zurück!",
                        flush=True
                    )

                    continue

                print(
                    f"[GRID] Button Objekt: "
                    f"{btn!r}",
                    flush=True
                )

                print(
                    f"[GRID] enabled={btn.isEnabled()} "
                    f"visible={btn.isVisible()}",
                    flush=True
                )

                # =====================================================
                # SPEZIALBEHANDLUNG PATCH CHECK
                # =====================================================

                if key == "patch_check":

                    print(
                        "[PATCH CHECK] QPushButton wurde erzeugt",
                        flush=True
                    )

                    print(
                        "[PATCH CHECK] objectName:",
                        btn.objectName(),
                        flush=True
                    )

                    print(
                        "[PATCH CHECK] enabled:",
                        btn.isEnabled(),
                        flush=True
                    )

                    print(
                        "[PATCH CHECK] visible:",
                        btn.isVisible(),
                        flush=True
                    )

                    print(
                        "[PATCH CHECK] signalsBlocked:",
                        btn.signalsBlocked(),
                        flush=True
                    )

                    # -------------------------------------------------
                    # DIREKTER SIGNAL-TEST
                    # -------------------------------------------------

                    def patch_check_direct_signal(
                        checked=False
                    ):

                        print(
                            "",
                            flush=True
                        )

                        print(
                            "##################################################",
                            flush=True
                        )

                        print(
                            "[PATCH CHECK DIRECT] "
                            "Qt clicked SIGNAL EMPFANGEN!",
                            flush=True
                        )

                        print(
                            f"[PATCH CHECK DIRECT] checked={checked}",
                            flush=True
                        )

                        print(
                            "##################################################",
                            flush=True
                        )

                    btn.clicked.connect(
                        patch_check_direct_signal
                    )

                    print(
                        "[PATCH CHECK] "
                        "Direktes clicked-Signal verbunden.",
                        flush=True
                    )

                # =====================================================
                # BUTTON-EIGENSCHAFTEN
                # =====================================================

                btn.setProperty(
                    "text_key",
                    key
                )

                btn.setProperty(
                    "full_text",
                    button_text
                )

                btn.setText(
                    button_text
                )

                btn.setSizePolicy(
                    QSizePolicy.Policy.Expanding,
                    QSizePolicy.Policy.Fixed
                )

                btn.setMinimumWidth(
                    230
                )

                btn.setMinimumHeight(
                    50
                )

                btn.setMaximumHeight(
                    50
                )

                # =====================================================
                # ICON
                # =====================================================

                theme_name, fallback = ICON_MAP.get(
                    key,
                    (
                        "application-x-executable",
                        QStyle.StandardPixmap.SP_FileIcon
                    )
                )

                btn.setIcon(
                    get_system_icon(
                        theme_name,
                        fallback
                    )
                )

                btn.setIconSize(
                    QSize(
                        20,
                        20
                    )
                )

                # =====================================================
                # TOOLTIP
                # =====================================================

                btn.setToolTip(
                    tt_de
                    if is_de
                    else
                    tt_en
                )

                # =====================================================
                # STYLE
                # =====================================================

                btn.setStyleSheet(
                    btn.styleSheet()
                    +
                    f"""
                    QPushButton {{
                        padding: 8px 10px;
                        text-align: center;
                        border: 1px solid #2d7185;
                        border-radius: 9px;
                        font-weight: 700;
                        min-height: 42px;
                    }}
                    QPushButton:hover {{
                        border: 1px solid #35e6ff;
                        color: #ffffff;
                    }}
                    QPushButton:pressed {{
                        padding-top: 10px;
                        background-color: #123b49;
                    }}

                    QToolTip {{
                    background-color: #202833;
                    color: #EAF6FF;
                    border: 1px solid #00B8D4;
                    border-radius: 4px;
                    padding: 5px;
                    font-size: 10pt;
                    font-weight: bold;
                }}
                                    """
                )

                # =====================================================
                # GRID POSITION
                # =====================================================

                row, col = divmod(
                    idx,
                    cols
                )

                grid_layout.addWidget(
                    btn,
                    row,
                    col
                )

                # =====================================================
                # BUTTON SPEICHERN
                # =====================================================

                self.buttons[
                    key
                ] = btn

                print(
                    f"[GRID] Button fertig: "
                    f"{key} -> {btn.text()!r}",
                    flush=True
                )

            # =========================================================
            # SPALTEN-STRETCH
            # =========================================================

            for i in range(cols):

                grid_layout.setColumnStretch(
                    i,
                    1
                )

            # Bei schmalen Fenstern weniger Spalten, bei breiten mehr.
            self._patch_grid_reflow = ResponsiveGridReflow(
                grid_container, grid_layout, min_item_width=320, parent=self
            )

            # =========================================================
            # TEXT ANPASSEN
            # =========================================================

            QTimer.singleShot(
                100,
                self._fit_all_button_texts
            )

            # =========================================================
            # ABSCHLUSS
            # =========================================================

            print(
                "[GRID SETUP] "
                f"{len(self.buttons)} Buttons erstellt.",
                flush=True
            )

            print(
                "[GRID SETUP] "
                "Patch-Check Button:",
                self.buttons.get(
                    "patch_check"
                ),
                flush=True
            )

            print(
                "[GRID SETUP] setup_grid_buttons() ENDE",
                flush=True
            )
    
    def check_patch(self, info_widget=None, progress_callback=None):
        """
        Prüft den erzeugten OSCam-Emu-Patch gegen den aktuellen
        Streamboard-OSCam-Quellcode.

        Der erzeugte Patch besitzt einen eigenen 3-zeiligen Header.
        Dieser Header wird für git apply temporär entfernt.

        Es werden KEINE Änderungen am Repository vorgenommen.
        Es wird ausschließlich ausgeführt:

            git apply --check --binary

        Dadurch wird nur geprüft, ob der Patch sauber anwendbar ist.
        """

        import os
        import tempfile

        from PyQt6.QtWidgets import QApplication

        print(
            "[PATCH CHECK] check_patch() START",
            flush=True
        )

        # ---------------------------------------------------------
        # Info-Widget bestimmen
        # ---------------------------------------------------------

        if info_widget is None:
            info_widget = getattr(
                self,
                "info_text",
                None
            )

        lang = getattr(
            self,
            "LANG",
            "de"
        ).lower()

        is_de = lang == "de"

        # ---------------------------------------------------------
        # Final-Label ausblenden
        # ---------------------------------------------------------

        if hasattr(self, "hide_final_label"):
            try:
                self.hide_final_label()
            except Exception:
                pass

        # ---------------------------------------------------------
        # Logging
        # ---------------------------------------------------------

        def write_log(text, level="info"):

            print(
                f"[PATCH CHECK] {text}",
                flush=True
            )

            try:
                self.append_info(
                    info_widget,
                    str(text),
                    level
                )

            except Exception as exc:

                print(
                    "[PATCH CHECK] append_info Fehler:",
                    exc,
                    flush=True
                )

                try:
                    if info_widget is not None:
                        info_widget.append(
                            str(text)
                        )
                except Exception:
                    pass

        # ---------------------------------------------------------
        # ProgressBar
        # ---------------------------------------------------------

        pbar = getattr(
            self,
            "progress_bar",
            None
        )

        def update_p(
            value,
            error=False,
            text=None
        ):

            try:

                if pbar:

                    pbar.setValue(
                        int(value)
                    )

                    if error:

                        pbar.setFormat(
                            "❌ Patch Fehler"
                        )

                    elif text:

                        pbar.setFormat(
                            str(text)
                        )

                    else:

                        pbar.setFormat(
                            "%p%"
                        )

                    pbar.show()

            except Exception:
                pass

            if progress_callback:

                try:

                    progress_callback(
                        int(value)
                    )

                except Exception:
                    pass

            try:
                QApplication.processEvents()
            except Exception:
                pass

        # ---------------------------------------------------------
        # Sound
        # ---------------------------------------------------------

        def play_check_sound(success=True):

            try:

                if "safe_play" in globals():

                    globals()["safe_play"](
                        "complete.oga"
                        if success
                        else
                        "dialog-error.oga"
                    )

            except Exception:
                pass

        # ---------------------------------------------------------
        # Einheitliche Fehlerbehandlung
        # ---------------------------------------------------------

        def check_failed(message):

            write_log(
                "❌ " + str(message),
                "error"
            )

            update_p(
                100,
                error=True,
                text=(
                    "❌ Patch Fehler"
                    if is_de
                    else
                    "❌ Patch Error"
                )
            )

            play_check_sound(
                False
            )

            return False

        # ---------------------------------------------------------
        # START
        # ---------------------------------------------------------

        write_log(
            "🔍 PATCH PRÜFEN wurde gestartet...",
            "info"
        )

        update_p(
            5,
            text=(
                "Patch-Prüfung startet..."
                if is_de
                else
                "Starting patch check..."
            )
        )

        # =========================================================
        # PATCH_FILE
        # =========================================================

        
        patch_file = (
            getattr(self, "PATCH_FILE", None)
            or globals().get("PATCH_FILE")
        )

        if not patch_file:
            try:
                from paths import PATCH_FILE as CONFIG_PATCH_FILE
                patch_file = CONFIG_PATCH_FILE
            except (ImportError, AttributeError):
                patch_file = None

        if not patch_file:
            return check_failed(
                "PATCH_FILE ist nicht konfiguriert. "
                "Der Pfad konnte auch nicht aus paths.py gelesen werden."
            )


        if not patch_file:

            return check_failed(
                "PATCH_FILE ist nicht konfiguriert."
            )

        patch_file = os.path.abspath(
            os.path.expanduser(
                os.path.expandvars(
                    str(patch_file)
                )
            )
        )

        write_log(
            f"📄 Patch-Datei:\n{patch_file}",
            "info"
        )

        # ---------------------------------------------------------
        # Patch vorhanden?
        # ---------------------------------------------------------

        if not os.path.isfile(
            patch_file
        ):

            return check_failed(
                "Patch-Datei wurde nicht gefunden:\n"
                f"{patch_file}"
            )

        # ---------------------------------------------------------
        # Patch-Größe
        # ---------------------------------------------------------

        try:

            patch_size = os.path.getsize(
                patch_file
            )

        except Exception as exc:

            return check_failed(
                "Patch-Datei kann nicht gelesen werden:\n"
                f"{exc}"
            )

        if patch_size <= 0:

            return check_failed(
                "Patch-Datei ist leer."
            )

        write_log(
            "✓ Patch-Datei gefunden.",
            "success"
        )

        write_log(
            "✓ Patch-Größe: "
            f"{patch_size:,} Bytes".replace(",", "."),
            "success"
        )

        update_p(
            20,
            text=(
                "Patch gefunden..."
                if is_de
                else
                "Patch found..."
            )
        )

       
        # =========================================================
        # STREAMREPO
        # =========================================================

        streamboard_repo = (
            globals().get("STREAMREPO")
            or getattr(self, "STREAMREPO", None)
        )

        if not streamboard_repo:
            try:
                from paths import STREAMREPO as CONFIG_STREAMREPO
                streamboard_repo = CONFIG_STREAMREPO
            except (ImportError, AttributeError):
                streamboard_repo = None

        if not streamboard_repo:
            try:
                from paths import TEMP_REPO as CONFIG_TEMP_REPO
                streamboard_repo = CONFIG_TEMP_REPO
            except (ImportError, AttributeError):
                streamboard_repo = None

        if not streamboard_repo:
            return check_failed(
                "STREAMREPO ist nicht konfiguriert. "
                "Der Pfad konnte nicht aus paths.py gelesen werden."
            )

        streamboard_repo = os.path.abspath(
            os.path.expanduser(
                os.path.expandvars(
                    str(streamboard_repo)
                )
            )
        )

        write_log(
            "📁 Prüfe gegen aktuelles "
            "Streamboard-Repository:",
            "info"
        )

        write_log(
            f"   {streamboard_repo}",
            "info"
        )

        if not os.path.isdir(
            os.path.join(streamboard_repo, ".git")
        ):
            return check_failed(
                "STREAMREPO ist kein gültiges Git-Repository:\n"
                f"{streamboard_repo}"
            )


        # ---------------------------------------------------------
        # Repository vorhanden?
        # ---------------------------------------------------------

        if not os.path.isdir(
            streamboard_repo
        ):

            return check_failed(
                "Streamboard-Repository existiert nicht:\n"
                f"{streamboard_repo}"
            )

        update_p(
            30,
            text=(
                "Streamboard-Repository prüfen..."
                if is_de
                else
                "Checking Streamboard repository..."
            )
        )

        # =========================================================
        # GIT REPOSITORY PRÜFEN
        # =========================================================

        try:

            write_log(
                "🔎 Prüfe Streamboard-Git-Repository...",
                "info"
            )

            result = git_command(
                [
                    "rev-parse",
                    "--is-inside-work-tree"
                ],
                cwd=streamboard_repo,
                allowed_returncodes=(0,),
                timeout=60
            )

            if (
                (result.stdout or "")
                .strip()
                .lower()
                != "true"
            ):

                raise RuntimeError(
                    "Das Streamboard-Verzeichnis ist "
                    "kein Git-Repository."
                )

            write_log(
                "✓ Streamboard-Git-Repository erkannt.",
                "success"
            )

        except Exception as exc:

            return check_failed(
                "Streamboard-Git-Repository konnte "
                "nicht geprüft werden:\n"
                f"{exc}"
            )

        # =========================================================
        # AKTUELLEN STREAMBOARD-HEAD ERMITTELN
        # =========================================================

        try:

            result = git_command(
                [
                    "rev-parse",
                    "HEAD"
                ],
                cwd=streamboard_repo,
                allowed_returncodes=(0,),
                timeout=60
            )

            streamboard_commit = (
                result.stdout or ""
            ).strip()

            if not streamboard_commit:

                raise RuntimeError(
                    "Streamboard-HEAD konnte "
                    "nicht ermittelt werden."
                )

            write_log(
                "✓ Aktueller Streamboard-HEAD:",
                "success"
            )

            write_log(
                f"   {streamboard_commit}",
                "info"
            )

        except Exception as exc:

            return check_failed(
                "Streamboard-HEAD konnte "
                "nicht ermittelt werden:\n"
                f"{exc}"
            )

        update_p(
            40,
            text=(
                "Streamboard-Stand ermittelt..."
                if is_de
                else
                "Streamboard revision detected..."
            )
        )

        # =========================================================
        # PATCH EINLESEN
        # =========================================================

        temporary_patch = None

        try:

            write_log(
                "📖 Lese Patch-Datei...",
                "info"
            )

            with open(
                patch_file,
                "r",
                encoding="utf-8",
                errors="replace"
            ) as handle:

                content = handle.read()

        except Exception as exc:

            return check_failed(
                "Patch-Datei konnte nicht gelesen werden:\n"
                f"{exc}"
            )

        update_p(
            50,
            text=(
                "Patch vorbereiten..."
                if is_de
                else
                "Preparing patch..."
            )
        )

        # =========================================================
        # HEADER ENTFERNEN
        # =========================================================

        marker = "diff --git "

        position = content.find(
            marker
        )

        if position < 0:

            return check_failed(
                "Im Patch wurde kein "
                "'diff --git'-Block gefunden."
            )

        header = content[
            :position
        ].strip()

        git_patch = content[
            position:
        ]

        if not git_patch.strip():

            return check_failed(
                "Der eigentliche Git-Patch ist leer."
            )

        if header:

            write_log(
                "✓ Patch-Header erkannt und entfernt.",
                "success"
            )

            write_log(
                "   Git-Diff beginnt bei Zeichen "
                f"{position}.",
                "info"
            )

        else:

            write_log(
                "ℹ️ Kein zusätzlicher Patch-Header "
                "gefunden.",
                "info"
            )

        # =========================================================
        # TEMPORÄRE PATCH-DATEI
        # =========================================================

        try:

            temp_handle = tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                newline="",
                suffix=".patch",
                prefix="oscam_patch_check_",
                delete=False
            )

            temporary_patch = temp_handle.name

            try:

                temp_handle.write(
                    git_patch
                )

            finally:

                temp_handle.close()

            temporary_size = os.path.getsize(
                temporary_patch
            )

            if temporary_size <= 0:

                raise RuntimeError(
                    "Temporäre Git-Patch-Datei ist leer."
                )

            write_log(
                "✓ Temporärer Git-Patch wurde erzeugt.",
                "success"
            )

            write_log(
                f"   {temporary_patch}",
                "info"
            )

            write_log(
                "   Größe: "
                f"{temporary_size:,} Bytes"
                .replace(",", "."),
                "info"
            )

        except Exception as exc:

            return check_failed(
                "Temporärer Git-Patch konnte "
                "nicht erzeugt werden:\n"
                f"{exc}"
            )

        update_p(
            65,
            text=(
                "Git-Patch vorbereitet..."
                if is_de
                else
                "Git patch prepared..."
            )
        )

        # =========================================================
        # GIT APPLY --CHECK
        # =========================================================

        try:

            write_log(
                "",
                "info"
            )

            write_log(
                "🔍 Prüfe Patch mit:",
                "info"
            )

            write_log(
                "   git apply --check --binary",
                "info"
            )

            write_log(
                "   Repository:",
                "info"
            )

            write_log(
                f"   {streamboard_repo}",
                "info"
            )

            write_log(
                "   Keine Änderungen werden angewendet.",
                "info"
            )

            result = git_command(
                [
                    "apply",
                    "--check",
                    "--binary",
                    temporary_patch
                ],
                cwd=streamboard_repo,
                allowed_returncodes=(0, 1),
                timeout=600
            )

            stdout = (
                result.stdout or ""
            ).strip()

            stderr = (
                result.stderr or ""
            ).strip()

            update_p(
                85,
                text=(
                    "Patch wird geprüft..."
                    if is_de
                    else
                    "Checking patch..."
                )
            )

            # =====================================================
            # ERFOLG
            # =====================================================

            if result.returncode == 0:

                write_log(
                    "",
                    "info"
                )

                write_log(
                    "════════════════════════════════════════════",
                    "info"
                )

                write_log(
                    "✅ PATCH-PRÜFUNG ERFOLGREICH",
                    "success"
                )

                write_log(
                    "✓ Der Patch kann auf den aktuellen "
                    "Streamboard-Stand angewendet werden.",
                    "success"
                )

                write_log(
                    f"✓ Streamboard-HEAD: "
                    f"{streamboard_commit}",
                    "info"
                )

                write_log(
                    "✓ git apply --check war erfolgreich.",
                    "success"
                )

                if stdout:

                    write_log(
                        stdout,
                        "info"
                    )

                write_log(
                    "════════════════════════════════════════════",
                    "info"
                )

                update_p(
                    100,
                    text="✅ Patch OK"
                )

                if pbar:

                    try:

                        pbar.setFormat(
                            "✅ Patch OK"
                        )

                    except Exception:
                        pass

                play_check_sound(
                    True
                )

                print(
                    "[PATCH CHECK] ERFOLGREICH",
                    flush=True
                )

                return True

            # =====================================================
            # FEHLER
            # =====================================================

            error_text = (
                stderr
                or stdout
                or
                f"git apply beendet mit "
                f"Returncode {result.returncode}"
            )

            write_log(
                "",
                "info"
            )

            write_log(
                "════════════════════════════════════════════",
                "info"
            )

            write_log(
                "❌ PATCH-PRÜFUNG FEHLGESCHLAGEN",
                "error"
            )

            write_log(
                "Der Patch kann nicht sauber auf den "
                "aktuellen Streamboard-Stand angewendet werden.",
                "error"
            )

            write_log(
                f"Streamboard-HEAD: "
                f"{streamboard_commit}",
                "info"
            )

            write_log(
                "",
                "info"
            )

            write_log(
                "Git-Fehler:",
                "error"
            )

            write_log(
                error_text,
                "error"
            )

            write_log(
                "════════════════════════════════════════════",
                "info"
            )

            update_p(
                100,
                error=True,
                text="❌ Patch Fehler"
            )

            if pbar:

                try:

                    pbar.setFormat(
                        "❌ Patch Fehler"
                    )

                except Exception:
                    pass

            play_check_sound(
                False
            )

            print(
                "[PATCH CHECK] FEHLGESCHLAGEN",
                flush=True
            )

            return False

        except Exception as exc:

            write_log(
                "❌ Fehler beim Ausführen von "
                "git apply --check:",
                "error"
            )

            write_log(
                str(exc),
                "error"
            )

            update_p(
                100,
                error=True,
                text="❌ Patch Fehler"
            )

            if pbar:

                try:

                    pbar.setFormat(
                        "❌ Patch Fehler"
                    )

                except Exception:
                    pass

            play_check_sound(
                False
            )

            print(
                "[PATCH CHECK] EXCEPTION:",
                exc,
                flush=True
            )

            return False

        finally:

            # -----------------------------------------------------
            # Temporäre Datei löschen
            # -----------------------------------------------------

            if temporary_patch:

                try:

                    if os.path.isfile(
                        temporary_patch
                    ):

                        os.remove(
                            temporary_patch
                        )

                        print(
                            "[PATCH CHECK] "
                            "Temporäre Patch-Datei gelöscht.",
                            flush=True
                        )

                except Exception as exc:

                    print(
                        "[PATCH CHECK] "
                        "Temporäre Patch-Datei konnte "
                        f"nicht gelöscht werden: {exc}",
                        flush=True
                    )

            print(
                "[PATCH CHECK] check_patch() ENDE",
                flush=True
            )
    
    # =====================
    # BUTTON & COLOR HANDLING
    # =====================
    def create_option_button(
        self, key, text, color="#FFFFFF", fg="black", callback=None
    ):
        """
        Erstellt einen stylischen Option-Button für die GUI.
        - key: eindeutiger Button-Key
        - text: Anzeige-Text
        - color: Hintergrundfarbe (Hex)
        - fg: Textfarbe (Hex)
        - callback: Funktion beim Klick, bekommt info_widget + progress_callback übergeben
        """
        btn = QPushButton(text)
        btn.setFont(QFont("Arial", 14, QFont.Weight.Bold))
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setMinimumHeight(self.BUTTON_HEIGHT)
        btn.setStyleSheet(
            f"""
            QPushButton {{
                background-color: {color};
                color: {fg};
                border-radius: {self.BUTTON_RADIUS}px;
            }}
            QPushButton:hover {{
                background-color: {self.adjust_color(color, 1.2)};
            }}
            QPushButton:pressed {{
                background-color: {self.adjust_color(color, 0.85)};
            }}
        """
        )

        # Click Event mit info_widget & progress_callback
        if callback:
            btn.clicked.connect(
                lambda checked=False: callback(
                    info_widget=self.info_text, progress_callback=None
                )
            )

        return btn

    def on_header_clicked(self, event):
        """Wird aufgerufen, wenn man auf den blinkenden Balken klickt."""
        if (
            hasattr(self, "pulse_anim")
            and self.pulse_anim.state() == QPropertyAnimation.State.Running
        ):

            # Sofortiges Feedback im Log (Nutzt deine append_info)
            self.append_info("Update-Prozess gestartet...", "info")

            self.pulse_anim.stop()
            if self.header_container.graphicsEffect():
                self.header_container.setGraphicsEffect(None)

            self.header_label.setText(" Aktualisierung läuft...")

            # Start des Pulls
            self.run_git_pull()

    def run_git_pull(self):
        """Führt das Git-Update aus und nutzt die vorhandene append_info Logik."""
        try:
            # Git Pull ausführen und Output abfangen
            process = subprocess.run(
                ["git", "-C", TEMP_REPO, "pull"],
                capture_output=True,
                text=True,
                check=True,
            )

            self.header_label.setText(" System aktuell")

            # Erfolgsmeldung in den Infoscreen (Level 'success' für Grün)
            self.append_info("OSCam Repo wurde erfolgreich aktualisiert!", "success")

            # Falls Git Änderungen meldet, diese als 'white' oder 'raw' anzeigen
            if process.stdout and "Already up to date" not in process.stdout:
                self.append_info(process.stdout.strip(), "white")

        except subprocess.CalledProcessError as e:
            self.header_label.setText(" Update Fehler!")
            # Fehlermeldung in den Infoscreen (Level 'error' für Rot)
            self.append_info(f"Git Fehler: {e.stderr.strip()}", "error")

        except Exception as e:
            self.header_label.setText(" Fehler!")
            self.append_info(f"Unerwarteter Fehler: {str(e)}", "error")

    def log_to_info(self, message, color="white"):
        """Hilfsmethode zum Schreiben in den Infoscreen (QTextEdit)."""
        # Falls dein QTextEdit 'infoscreen' heißt:
        timestamp = QDateTime.currentDateTime().toString("hh:mm:ss")
        html_text = f'<span style="color:gray;">[{timestamp}]</span> <span style="color:{color};">{message}</span>'
        self.infoscreen.append(html_text)
        # Automatisch nach unten scrollen
        self.infoscreen.moveCursor(QTextCursor.MoveOperation.End)

    def check_for_new_commit(self):
        """
        Prüft den aktuellsten Commit von Streamboard.

        Der Netzwerk/Git-Check läuft in einem QThread,
        damit die PyQt-Oberfläche nicht einfriert.

        Repository:
            https://git.streamboard.tv/common/oscam.git
        Branch:
            master
        """

        from PyQt6.QtCore import QThread, pyqtSignal
        from PyQt6.QtWidgets import QMessageBox, QApplication
        import subprocess

        # ---------------------------------------------------------
        # Final Label verstecken
        # ---------------------------------------------------------
        if hasattr(self, "hide_final_label"):
            self.hide_final_label()

        # ---------------------------------------------------------
        # Sprache
        # ---------------------------------------------------------
        lang = getattr(self, "LANG", "de").lower()
        self.TEXT = globals().get("TEXTS", {}).get(lang, {})
        is_de = lang.startswith("de")

        # ---------------------------------------------------------
        # ProgressBar
        # ---------------------------------------------------------
        pbar = getattr(self, "progress_bar", None)

        rainbow = (
            "qlineargradient("
            "x1:0, y1:0, x2:1, y2:0, "
            "stop:0 #FF0000, "
            "stop:0.2 #FF7F00, "
            "stop:0.4 #FFFF00, "
            "stop:0.6 #00FF00, "
            "stop:0.8 #0000FF, "
            "stop:1 #8B00FF)"
        )

        def set_progress(value, text=None):
            if pbar is None:
                return

            pbar.setStyleSheet(
                f"""
                QProgressBar {{
                    text-align: center;
                    font-weight: bold;
                    border: 2px solid #222;
                    border-radius: 6px;
                    background-color: #111;
                    color: black;
                    font-size: 15pt;
                }}

                QProgressBar::chunk {{
                    background-color: {rainbow};
                    border-radius: 4px;
                }}
                """
            )

            pbar.setValue(value)

            if text is not None:
                pbar.setFormat(text)

            pbar.show()
            QApplication.processEvents()

        def finalize_pbar(text, visible_seconds=3):
            if pbar is None:
                return

            pbar.setStyleSheet(
                f"""
                QProgressBar {{
                    text-align: center;
                    font-weight: bold;
                    border: 2px solid #222;
                    border-radius: 6px;
                    background-color: #111;
                    color: black;
                    font-size: 15pt;
                }}

                QProgressBar::chunk {{
                    background-color: {rainbow};
                    border-radius: 4px;
                }}
                """
            )

            pbar.setValue(100)
            pbar.setFormat(text)

            from PyQt6.QtCore import QTimer

            def clear_bar():
                if pbar is None:
                    return

                pbar.setStyleSheet(
                    """
                    QProgressBar {
                        text-align: center;
                        font-weight: bold;
                        border: 2px solid #222;
                        border-radius: 6px;
                        background-color: #111;
                        color: black;
                        font-size: 15pt;
                    }

                    QProgressBar::chunk {
                        background-color: transparent;
                    }
                    """
                )

                pbar.setValue(0)
                pbar.setFormat("")

            QTimer.singleShot(
                visible_seconds * 1000,
                clear_bar
            )

        # ---------------------------------------------------------
        # Git Worker
        # ---------------------------------------------------------
        class CommitWorker(QThread):

            success = pyqtSignal(str)
            error = pyqtSignal(str)

            def run(self):
                try:
                    repo_url = (
                        "https://git.streamboard.tv/"
                        "common/oscam.git"
                    )

                    result = subprocess.run(
                        [
                            "git",
                            "ls-remote",
                            repo_url,
                            "refs/heads/master"
                        ],
                        capture_output=True,
                        text=True,
                        encoding="utf-8",
                        errors="replace",
                        timeout=20
                    )

                    if result.returncode != 0:
                        error_text = (
                            result.stderr.strip()
                            or "git ls-remote fehlgeschlagen."
                        )

                        self.error.emit(error_text)
                        return

                    output = result.stdout.strip()

                    if not output:
                        self.error.emit(
                            "Keine Antwort von Streamboard."
                        )
                        return

                    # Erwartet:
                    # HASH<TAB>refs/heads/master
                    parts = output.split()

                    if not parts:
                        self.error.emit(
                            "Kein Commit-Hash erhalten."
                        )
                        return

                    commit_hash = parts[0].strip().lower()

                    # SHA-1 muss 40 Zeichen haben
                    if len(commit_hash) != 40:
                        self.error.emit(
                            f"Ungültiger Commit-Hash: {commit_hash}"
                        )
                        return

                    self.success.emit(commit_hash)

                except subprocess.TimeoutExpired:
                    self.error.emit(
                        "Zeitüberschreitung bei der Verbindung."
                    )

                except FileNotFoundError:
                    self.error.emit(
                        "Git wurde nicht gefunden. "
                        "Bitte prüfen, ob Git installiert ist."
                    )

                except Exception as e:
                    self.error.emit(str(e))

        # ---------------------------------------------------------
        # Progress starten
        # ---------------------------------------------------------
        if pbar:
            set_progress(
                10,
                "🔍 Prüfe Streamboard..."
                if is_de
                else
                "Checking Streamboard..."
            )

            set_progress(
                30,
                "🌐 Verbinde mit Streamboard..."
                if is_de
                else
                "🌐 Connecting to Streamboard..."
            )

        # ---------------------------------------------------------
        # Worker erzeugen
        # ---------------------------------------------------------
        worker = CommitWorker(self)

        # Referenz behalten, damit Python den Thread nicht
        # vorzeitig freigibt.
        self._commit_check_worker = worker

        # ---------------------------------------------------------
        # Erfolgreicher Check
        # ---------------------------------------------------------
        
      
        def commit_received(newest_hash):

            try:
                if pbar:
                    set_progress(75)

                # -------------------------------------------------
                # NEUER COMMIT VON STREAMBOARD
                # -------------------------------------------------
                new_hash = str(
                    newest_hash
                ).strip().lower()

                # -------------------------------------------------
                # BISHERIGER AKTUELLER COMMIT AUS CONFIG
                # -------------------------------------------------
                old_hash = str(
                    self.cfg.get(
                        "last_stream_commit",
                        ""
                    )
                ).strip().lower()

                # -------------------------------------------------
                # ANZEIGE
                # -------------------------------------------------
                new_display = (
                    new_hash[:8]
                    if new_hash
                    else "---"
                )

                old_display = (
                    old_hash[:8]
                    if old_hash
                    else "---"
                )

                # -------------------------------------------------
                # TEXTE
                # -------------------------------------------------
                msg_new = self.TEXT.get(
                    "check_commit_new_found",
                    "Neuer Commit gefunden!"
                )

                msg_up = self.TEXT.get(
                    "check_commit_up_to_date",
                    "Kein neuer Commit vorhanden."
                )

                lbl_new = self.TEXT.get(
                    "check_commit_new_hash",
                    "Neu:"
                )

                lbl_old = self.TEXT.get(
                    "check_commit_old_hash",
                    "Alt:"
                )

                # -------------------------------------------------
                # PRÜFEN
                # -------------------------------------------------
                is_new_commit = (
                    new_hash != old_hash
                )

                print(
                    "[COMMIT] Streamboard:",
                    new_hash,
                    flush=True
                )

                print(
                    "[COMMIT] bisher last_stream_commit:",
                    old_hash,
                    flush=True
                )

                print(
                    "[COMMIT] neuer Commit:",
                    is_new_commit,
                    flush=True
                )

                # =================================================
                # NEUER COMMIT
                # =================================================
                if is_new_commit:

                    safe_play(
                        "complete.oga"
                    )

                    self.log_message(
                        "<span style='color:cyan;'>"
                        f"🆕 {msg_new} "
                        f"({new_display})"
                        "</span>"
                    )

                    info_text = (
                        f"{msg_new}\n\n"
                        f"{lbl_new} {new_display}\n"
                        f"{lbl_old} {old_display}"
                    )

                # =================================================
                # KEIN NEUER COMMIT
                # =================================================
                else:

                    safe_play(
                        "dialog-information.oga"
                    )

                    self.log_message(
                        "<span style='color:cyan;'>"
                        f"ℹ️ {msg_up} "
                        f"({new_display})"
                        "</span>"
                    )

                    info_text = (
                        f"{msg_up}\n\n"
                        f"{lbl_new} {new_display}\n"
                        f"{lbl_old} {old_display}"
                    )

                # -------------------------------------------------
                # DIALOG
                # -------------------------------------------------
                QMessageBox.information(
                    self,
                    self.TEXT.get(
                        "check_commit_title",
                        "OSCam Commit-Check"
                    ),
                    info_text
                )

                
                # =================================================
                # COMMIT-HISTORIE AKTUALISIEREN
                # NUR wenn tatsächlich ein neuer Commit vorhanden ist
                # =================================================

                if is_new_commit:

                    # Bisheriger aktueller Commit wird zum vorherigen
                    self.cfg[
                        "previous_stream_commit"
                    ] = old_hash

                    # Neuer Streamboard-Commit wird aktuell
                    self.cfg[
                        "last_stream_commit"
                    ] = new_hash

                    print(
                        "[COMMIT] Neuer Commit erkannt",
                        flush=True
                    )

                    print(
                        "[COMMIT] previous_stream_commit:",
                        self.cfg[
                            "previous_stream_commit"
                        ],
                        flush=True
                    )

                    print(
                        "[COMMIT] last_stream_commit:",
                        self.cfg[
                            "last_stream_commit"
                        ],
                        flush=True
                    )

                    # -------------------------------------------------
                    # CONFIG NUR BEI NEUEM COMMIT SPEICHERN
                    # -------------------------------------------------
                    save_config_func = globals().get(
                        "save_config"
                    )

                    if not save_config_func:
                        raise RuntimeError(
                            "save_config wurde nicht gefunden."
                        )

                    result = save_config_func(
                        self.cfg,
                        gui_instance=self,
                        silent=True
                    )

                    print(
                        "[COMMIT] save_config:",
                        result,
                        flush=True
                    )

                else:

                    print(
                        "[COMMIT] Kein neuer Commit - "
                        "Historie bleibt unverändert.",
                        flush=True
                    )


                # -------------------------------------------------
                # CONFIG SPEICHERN
                # -------------------------------------------------
                save_config_func = globals().get(
                    "save_config"
                )

                if not save_config_func:
                    raise RuntimeError(
                        "save_config wurde nicht gefunden."
                    )

                result = save_config_func(
                    self.cfg,
                    gui_instance=self,
                    silent=True
                )

                print(
                    "[COMMIT] save_config:",
                    result,
                    flush=True
                )

                # -------------------------------------------------
                # PROGRESS
                # -------------------------------------------------
                if pbar:

                    if is_new_commit:

                        finalize_pbar(
                            f"🆕 {msg_new}",
                            3
                        )

                    else:

                        finalize_pbar(
                            f"✅ {msg_up}",
                            3
                        )

            except Exception as e:

                safe_play(
                    "dialog-error.oga"
                )

                error_text = str(e)

                self.log_message(
                    "<span style='color:red;'>"
                    f"❌ {error_text}"
                    "</span>"
                )

                QMessageBox.critical(
                    self,
                    self.TEXT.get(
                        "check_commit_title",
                        "OSCam Commit-Check"
                    ),
                    error_text
                )

                if pbar:
                    finalize_pbar(
                        "❌ Fehler"
                    )






        # ---------------------------------------------------------
        # Fehler vom Worker
        # ---------------------------------------------------------
        def commit_error(error_text):

            safe_play(
                "dialog-error.oga"
            )

            err_lbl = self.TEXT.get(
                "check_commit_error",
                "Fehler beim Check:"
            )

            self.log_message(
                "<span style='color:red;'>"
                f"❌ {err_lbl} {error_text}"
                "</span>"
            )

            QMessageBox.critical(
                self,
                self.TEXT.get(
                    "check_commit_title",
                    "OSCam Commit-Check"
                ),
                f"{err_lbl}\n{error_text}"
            )

            if pbar:
                finalize_pbar(
                    "❌ Fehler"
                )

        # ---------------------------------------------------------
        # Signale verbinden
        # ---------------------------------------------------------
        worker.success.connect(
            commit_received
        )

        worker.error.connect(
            commit_error
        )

        # ---------------------------------------------------------
        # Thread nach Ende aufräumen
        # ---------------------------------------------------------
        def worker_finished():
            self._commit_check_worker = None

        worker.finished.connect(
            worker_finished
        )

        # ---------------------------------------------------------
        # Start
        # ---------------------------------------------------------
        worker.start()

    def on_clean_emu_clicked(self):
        """Sorgt dafür, dass das Log vor der Bereinigung englisch wird."""
        # 1. Sprache/UI synchronisieren (löscht alte deutsche Texte im Log)
        self.update_language()
        # 2. Bereinigung starten (schreibt jetzt ins leere, englische Log)
        from oscam_patch_manager import (
            clean_oscam_emu_git,
        )  # oder wo die Funktion liegt

        clean_oscam_emu_git(gui_instance=self)

    def on_button_clicked(self, key, func):
        self.set_active_button(key)
        self.run_action(func)

    def set_active_button(self, active_key):
        self.active_button_key = active_key

        # 1. Sicherstellen, dass wir die richtige Textfarbe aus dem aktuellen Theme haben
        # Wir probieren 'fg', falls das fehlt 'text', sonst weiß als Notlösung.
        text_color = current_diff_colors.get(
            "fg", current_diff_colors.get("text", "#FFFFFF")
        )

        # 2. Die Hover-Farbe für den inaktiven Zustand holen
        hover_color = current_diff_colors.get("hover", current_diff_colors["bg"])

        for key, btn in self.buttons.items():
            if key == active_key:
                # AKTIVER BUTTON: Giftgrün (wie in deinem Original)
                # Tipp: Du könntest hier auch current_diff_colors['active'] nutzen!
                btn.setStyleSheet(
                    f"""
                    QPushButton {{
                        background-color: #00FF00; 
                        color: #000000; 
                        border-radius: {self.BUTTON_RADIUS}px; 
                        min-height: {self.BUTTON_HEIGHT}px;
                        font-weight: bold;
                        border: 2px solid white;
                    }}
                """
                )
            else:
                # INAKTIVE BUTTONS: Nutzen das aktuelle Farbschema (Theme)
                # Wir bauen hier auch den Hover-Effekt wieder ein, damit er nicht verloren geht!
                btn.setStyleSheet(
                    f"""
                    QPushButton {{
                        background-color: {current_diff_colors['bg']}; 
                        color: {text_color}; 
                        border-radius: {self.BUTTON_RADIUS}px; 
                        min-height: {self.BUTTON_HEIGHT}px;
                    }}
                    QPushButton:hover {{
                        background-color: {hover_color};
                    }}
                """
                )

    
    
    def repaint_ui_colors(self):
        """
        Wendet das aktuell gewählte Farbschema auf die normalen GUI-Elemente an.

        Individuelle Buttons wie:
        - Grid-Buttons
        - S3
        - S4
        - NCam

        werden nicht überschrieben.

        Zusätzlich wird die Farbauswahl-ComboBox (color_box)
        vollständig an das aktuell gewählte Farbschema angepasst.
        """

        from PyQt6.QtWidgets import (
            QPushButton,
            QCheckBox,
            QComboBox,
            QListView,
        )
        from PyQt6.QtGui import QColor

        global current_diff_colors

        # ============================================================
        # 1. AKTUELLE THEME-FARBEN
        # ============================================================

        text_color = current_diff_colors.get(
            "fg",
            current_diff_colors.get(
                "text",
                "#FFFFFF"
            )
        )

        bg_color = current_diff_colors.get(
            "bg",
            "#2F2F2F"
        )

        hover_color = current_diff_colors.get(
            "hover",
            "#444444"
        )

        active_color = current_diff_colors.get(
            "active",
            "#666666"
        )

        # ============================================================
        # 2. INDIVIDUELLE BUTTONS SCHÜTZEN
        # ============================================================

        protected_buttons = {
            "btn_s3",
            "btn_s4",
            "btn_ncam",
        }

        # ============================================================
        # 3. GRID-BUTTONS SCHÜTZEN
        # ============================================================

        grid_buttons = set()

        if hasattr(self, "buttons") and isinstance(
            self.buttons,
            dict
        ):
            try:
                grid_buttons = {
                    btn
                    for btn in self.buttons.values()
                    if btn is not None
                }

            except Exception:
                grid_buttons = set()

        # ============================================================
        # 4. NORMALE BUTTONS
        # ============================================================

        for btn in self.findChildren(QPushButton):

            try:

                # Alle Buttons erhalten dasselbe aktive Farbschema.
                # Der Stats-Button wird weiter unten mit seinem Toggle-Zustand gestylt.
                btn.setGraphicsEffect(None)
                if not btn.toolTip().strip():
                    label = btn.text().replace("&", "").strip() or btn.objectName() or "Button"
                    btn.setToolTip(label)
                btn.setCursor(Qt.CursorShape.PointingHandCursor)

                btn.setStyleSheet(
                    f"""
                    QPushButton {{
                        background-color: {bg_color};
                        color: {text_color};
                        border-radius: 10px;
                        font-weight: 700;
                        padding: 2px 6px;
                        border: 1px solid {text_color};
                        min-height: 35px;
                    }}

                    QPushButton:hover {{
                        background-color: {hover_color};
                        color: {text_color};
                        border: 1px solid {text_color};
                    }}

                    QPushButton:pressed {{
                        background-color: {active_color};
                        color: {text_color};
                        border: 2px solid {text_color};
                    }}

                    QPushButton:disabled {{
                        background-color: {active_color};
                        color: #777777;
                        border: 1px solid #555555;
                    }}
                    """
                )

            except RuntimeError:
                continue

        # ============================================================
        # 5. STATS-TOGGLE: aktiviert = rote Schrift, deaktiviert = Theme
        # ============================================================
        cb = getattr(self, "telemetry_cb", None)
        if cb:
            try:
                if not cb.toolTip().strip():
                    cb.setToolTip("Anonyme Nutzungsstatistik aktivieren oder deaktivieren.")
                cb.setStyleSheet(f"""
                    QPushButton {{ background-color: {bg_color}; color: {text_color};
                                  border-radius: 10px; font-weight: 700; padding: 2px 12px;
                                  border: 1px solid {text_color}; min-height: 35px; }}
                    QPushButton:hover {{ background-color: {hover_color}; color: {text_color};
                                         border: 2px solid {text_color}; }}
                    QPushButton:checked {{ color: #FF0000; background-color: {bg_color};
                                           border: 2px solid #FF0000; }}
                    QPushButton:checked:hover {{ color: #FF0000; background-color: {hover_color};
                                                 border: 2px solid #FF0000; }}
                    QPushButton:pressed {{ background-color: {active_color}; }}
                """)
            except RuntimeError:
                pass

        # ============================================================
        # 6. FARB-AUSWAHLBOX / COMBOBOX
        # ============================================================

        color_box = getattr(
            self,
            "color_box",
            None
        )

        if color_box:

            try:

                # ----------------------------------------------------
                # Haupt-ComboBox
                # ----------------------------------------------------

                color_box.setStyleSheet(
                    f"""
                    QComboBox {{
                        background-color: {bg_color};
                        color: {text_color};
                        border: 1px solid {text_color};
                        border-radius: 8px;
                        padding: 5px 10px;
                        min-height: 35px;
                        font-weight: 700;
                    }}

                    QComboBox:hover {{
                        background-color: {hover_color};
                        color: {text_color};
                        border: 1px solid {text_color};
                    }}

                    QComboBox:focus {{
                        background-color: {bg_color};
                        color: {text_color};
                        border: 1px solid {text_color};
                    }}

                    QComboBox:on {{
                        background-color: {bg_color};
                        color: {text_color};
                    }}

                    QComboBox:disabled {{
                        background-color: {active_color};
                        color: #777777;
                        border: 1px solid #555555;
                    }}

                    QComboBox::drop-down {{
                        background-color: {bg_color};
                        border: none;
                        width: 30px;
                        subcontrol-origin: padding;
                        subcontrol-position: top right;
                    }}

                    QComboBox::drop-down:hover {{
                        background-color: {hover_color};
                    }}

                    QComboBox::down-arrow {{
                        width: 10px;
                        height: 10px;
                    }}

                    QComboBox QAbstractItemView {{
                        background-color: {bg_color};
                        color: {text_color};
                        border: 1px solid {text_color};
                        selection-background-color: {hover_color};
                        selection-color: {text_color};
                        outline: none;
                        padding: 2px;
                    }}

                    QComboBox QAbstractItemView::item {{
                        background-color: {bg_color};
                        color: {text_color};
                        padding: 7px 10px;
                        min-height: 25px;
                    }}

                    QComboBox QAbstractItemView::item:hover {{
                        background-color: {hover_color};
                        color: {text_color};
                    }}

                    QComboBox QAbstractItemView::item:selected {{
                        background-color: {hover_color};
                        color: {text_color};
                    }}

                    QComboBox QAbstractItemView::item:selected:hover {{
                        background-color: {active_color};
                        color: {text_color};
                    }}
                    """
                )

                # ----------------------------------------------------
                # Wichtig:
                #
                # Bei bestimmten Qt-Styles wird das Popup als separates
                # View-Widget behandelt. Deshalb setzen wir zusätzlich
                # den View direkt.
                # ----------------------------------------------------

                try:

                    view = color_box.view()

                    if view:

                        view.setStyleSheet(
                            f"""
                            QListView {{
                                background-color: {bg_color};
                                color: {text_color};
                                border: 1px solid {text_color};
                                outline: none;
                                padding: 2px;
                            }}

                            QListView::item {{
                                background-color: {bg_color};
                                color: {text_color};
                                padding: 7px 10px;
                                min-height: 25px;
                            }}

                            QListView::item:hover {{
                                background-color: {hover_color};
                                color: {text_color};
                            }}

                            QListView::item:selected {{
                                background-color: {hover_color};
                                color: {text_color};
                            }}

                            QListView::item:selected:hover {{
                                background-color: {active_color};
                                color: {text_color};
                            }}
                            """
                        )

                except (
                    RuntimeError,
                    AttributeError
                ):
                    pass

            except RuntimeError:
                pass

        # ============================================================
        # 7. PROGRESS BAR
        # ============================================================

        pb = getattr(
            self,
            "progress_bar",
            None
        )

        if pb:

            try:

                if pb.value() == 0:

                    if hasattr(
                        self,
                        "pbar_idle"
                    ):
                        self.pbar_idle()

                else:

                    if hasattr(
                        self,
                        "_idle_anim"
                    ):
                        self._idle_anim.stop()

                    pb.setStyleSheet(
                        f"""
                        QProgressBar {{
                            border: 1px solid {text_color};
                            border-radius: 7px;
                            background-color: {bg_color};
                            text-align: center;
                            color: {text_color};
                            font-weight: 700;
                        }}

                        QProgressBar::chunk {{
                            background-color: {active_color};
                            border-radius: 6px;
                        }}
                        """
                    )

            except RuntimeError:
                pass

        # ============================================================
        # 8. LABELS & HEADER
        # ============================================================

        labels = [
            "lang_label",
            "color_label",
            "commit_label",
            "controls_header",
            "github_header",
            "header_label",
            "status_label",
        ]

        for lbl_name in labels:

            lbl = getattr(
                self,
                lbl_name,
                None
            )

            if not lbl:
                continue

            try:

                is_header = (
                    "header"
                    in lbl_name
                )

                label_bg = (
                    bg_color
                    if is_header
                    else "transparent"
                )

                lbl.setStyleSheet(
                    f"""
                    QLabel {{
                        color: {text_color};
                        font-weight: 700;
                        font-size: 18px;
                        background-color: {label_bg};
                        border: none;
                    }}
                    """
                )

            except RuntimeError:
                pass

        # ============================================================
        # 9. HAUPTFENSTER-HINTERGRUND
        # ============================================================

        try:

            self.setAutoFillBackground(
                True
            )

            palette = self.palette()

            palette.setColor(
                self.backgroundRole(),
                QColor(bg_color)
            )

            self.setPalette(
                palette
            )

        except (
            RuntimeError,
            AttributeError,
            TypeError
        ):
            pass

        # ============================================================
        # 10. PROGRESSBAR IDLE NACH THEME-ÄNDERUNG
        # ============================================================

        try:

            if (
                pb
                and pb.value() == 0
                and hasattr(
                    self,
                    "pbar_idle"
                )
            ):
                self.pbar_idle()

        except RuntimeError:
            pass

        # ============================================================
        # 11. GUI NEU ZEICHNEN
        # ============================================================

        try:

            self.update()
            self.repaint()

        except RuntimeError:
            pass


    def close_with_confirm(self):
        """
        Zeigt eine sprachabhängige Bestätigungsabfrage beim Beenden.
        Speichert die aktuelle Konfiguration und beendet anschließend die Anwendung.
        """

        from PyQt6.QtWidgets import QApplication, QMessageBox

        # ------------------------------------------------------------
        # Sprache ermitteln
        # ------------------------------------------------------------
        lang = str(
            getattr(self, "LANG", "de")
        ).lower().strip()

        # Nur die Sprache vor dem Länder-/Regionscode verwenden:
        # z.B. "de_DE" -> "de"
        lang = lang.split("_")[0].split("-")[0]

        # ------------------------------------------------------------
        # Übersetzung sicher laden
        # ------------------------------------------------------------
        texts = globals().get("TEXTS", {})

        if not isinstance(texts, dict):
            texts = {}

        t = texts.get(lang)

        if not isinstance(t, dict):
            t = texts.get("en", {})

        if not isinstance(t, dict):
            t = {}

        # ------------------------------------------------------------
        # Texte mit Fallbacks
        # ------------------------------------------------------------
        title = t.get(
            "exit",
            "Programm beenden" if lang == "de" else "Exit"
        )

        question = t.get(
            "exit_question",
            (
                "Möchtest du den OSCam Patch Manager wirklich beenden?"
                if lang == "de"
                else "Do you really want to exit?"
            )
        )

        yes_text = t.get(
            "yes",
            "Ja" if lang == "de" else "Yes"
        )

        no_text = t.get(
            "no",
            "Nein" if lang == "de" else "No"
        )

        # ------------------------------------------------------------
        # Bestätigungsdialog
        # ------------------------------------------------------------
        msg = QMessageBox(self)
        msg.setIcon(QMessageBox.Icon.Question)
        msg.setWindowTitle(str(title))
        msg.setText(str(question))

        yes_button = msg.addButton(
            str(yes_text),
            QMessageBox.ButtonRole.YesRole
        )

        no_button = msg.addButton(
            str(no_text),
            QMessageBox.ButtonRole.NoRole
        )

        msg.setDefaultButton(no_button)

        msg.exec()

        # ------------------------------------------------------------
        # Nur bei "Ja" wirklich beenden
        # ------------------------------------------------------------
        if msg.clickedButton() != yes_button:
            return

        # ------------------------------------------------------------
        # Aktuelle GUI-Werte speichern
        # ------------------------------------------------------------
        exit_updates = {
            "s3_custom_path": getattr(
                self,
                "S3_PATH",
                r"C:\s3"
            ),

            "ncam_custom_path": getattr(
                self,
                "NCAM_PATH",
                r"C:\opt\ncam"
            ),

            "s4_custom_path": getattr(
                self,
                "S4_PATH",
                r"C:\opt\simplebuild4"
            ),

            "last_session_exit": "success",
        }

        # ------------------------------------------------------------
        # Bereits vorhandene Konfiguration übernehmen
        # ------------------------------------------------------------
        old_cfg = getattr(
            self,
            "cfg",
            getattr(
                self,
                "current_config",
                {}
            )
        )

        if isinstance(old_cfg, dict):
            for key in (
                "color",
                "language",
                "commit_count",
                "theme_mode",
                "blink_speed",
                "led_enabled",
                "EMUREPO",
                "patch_modifier",
                "s3_patch_path",
            ):
                if key in old_cfg:
                    exit_updates[key] = old_cfg[key]

        # ------------------------------------------------------------
        # Konfiguration speichern
        # ------------------------------------------------------------
        try:
            save_config_func = globals().get("save_config")

            if callable(save_config_func):
                self.is_closing = True

                save_config_func(
                    exit_updates,
                    gui_instance=self,
                    silent=True
                )

        except Exception as e:
            print(
                f"[WARN] Config save failed: {e}"
            )

        
        # ------------------------------------------------------------
        # Beenden-Sound
        # ------------------------------------------------------------
        try:
            sound_manager = getattr(self, "sound_manager", None)

            if sound_manager and sound_manager.enabled:
                sound_manager.exit_sound.play()

                # Sound kurz abspielen lassen, bevor die Anwendung schließt
                QTimer.singleShot(800, QApplication.quit)
            else:
                QApplication.quit()

        except Exception as e:
            print(f"[WARN] Exit sound failed: {e}")
            QApplication.quit()

    
    def update_language(self):
        """
        Übersetzt alle Buttons, Labels und Header zentral.
        Verhindert 'RuntimeError: wrapped C/C++ object has been deleted'.
        """
        from PyQt6.QtWidgets import QApplication

        # 1. Aktuelle Sprache ermitteln
        lang = str(getattr(self, "LANG", "de")).lower()[:2]
        lang_dict = TEXTS.get(lang, TEXTS.get("en", {}))
        self.TEXT = lang_dict

        # Helferfunktion für Übersetzungen
        def get_t(key, default=None):
            if key in lang_dict:
                return lang_dict[key]
            return (
                default if default is not None else str(key).replace("_", " ").title()
            )

        # Sicherheits-Check Funktion
        def safe_ui(attr_name, func_name, *args, **kwargs):
            widget = getattr(self, attr_name, None)
            if widget is not None:
                try:
                    # Prüfen ob das Widget noch "lebt"
                    method = getattr(widget, func_name)
                    method(*args, **kwargs)
                except (RuntimeError, AttributeError):
                    pass

        # --- A) GRID & OPTION BUTTONS ---
        if hasattr(self, "buttons") and self.buttons:
            for key, btn in self.buttons.items():
                try:
                    btn.setText(get_t(key))
                except RuntimeError:
                    pass

        if hasattr(self, "option_buttons") and self.option_buttons:
            for key, val in self.option_buttons.items():
                if isinstance(val, (list, tuple)) and len(val) >= 2:
                    try:
                        prefix = "💻 " if "terminal" in str(key).lower() else ""
                        val[0].setText(f"{prefix}{get_t(val[1])}")
                    except RuntimeError:
                        pass
        # Backup-Button übersetzen
        if hasattr(self, "btn_backup_gui"):
            self.btn_backup_gui.setText(
                self.TEXT.get(
                    "backup_button_text",
                    "💾 Backup erstellen" if self.LANG == "de"
                    else "💾 Create Backup"
                )
            )
        # --- B) HEADER (Die Haupt-Fehlerquelle) ---
        safe_ui("controls_header", "setText", get_t("settings_header", "Einstellungen" if lang == "de" else "Settings"))
        safe_ui(
            "github_header",
            "setText",
            get_t("github_config_header", "GitHub-Konfiguration" if lang == "de" else "GitHub Configuration"),
        )

        # --- C) DIE 3 NEUEN ORDNER-BUTTONS & INSTALLATIONS-BUTTONS ---
        safe_ui(
            "btn_open_work",
            "setText",
            "📁 Arbeitsordner" if lang == "de" else "📁 Working Folder",
        )
        safe_ui(
            "btn_open_temp", "setText", "📁 Temp-Repo" if lang == "de" else "📁 Temp Repo"
        )
        safe_ui("btn_open_emu", "setText", "📁 Emu-Git" if lang == "de" else "📁 Emu Git")

        # HIER ERGÄNZT: Die Übersetzungen für S3, S4 und NCam
        safe_ui(
            "btn_s3", 
            "setText", 
            get_t("install_s3", "🚀 S3 Installieren" if lang == "de" else "🚀 Install S3")
        )
        safe_ui(
            "btn_s4", 
            "setText", 
            get_t("install_s4", "🚀 S4 Installieren" if lang == "de" else "🚀 Install S4")
        )
        safe_ui(
            "btn_ncam", 
            "setText", 
            get_t("install_ncam", "🚀 NCam Installieren" if lang == "de" else "🚀 Install NCam-speedy")
        )

        # --- D) FUNKTIONS-BUTTONS ---
        safe_ui(
            "btn_check_tools", "setText",
            get_t("check_tools_button", "🛠️ Tools prüfen" if lang == "de" else "🛠️ Check Tools")
        )
        safe_ui(
            "btn_check_commit", "setText",
            get_t("check_commit_button", "🔄 Commit prüfen" if lang == "de" else "🔄 Check Commit")
        )
        safe_ui(
            "btn_patch_online", "setText",
            "🌐 " + str(get_t(
                "patch_online_download",
                "Patches aus Git laden" if lang == "de" else "Load Patches from Git"
            ))
        )

        # Patch Autor
        if hasattr(self, "btn_modifier"):
            auth_text = "👤 Patch Autor" if lang == "de" else "👤 Patch Author"
            current_name = getattr(self, "patch_modifier", "speedy005")
            tip = (
                f"Autor: {current_name}" if lang == "de" else f"Author: {current_name}"
            )
            safe_ui("btn_modifier", "setText", auth_text)
            safe_ui("btn_modifier", "setToolTip", tip)

        # Repo URL
        if hasattr(self, "btn_repo_url"):
            safe_ui("btn_repo_url", "setText", get_t("repo_url_button", "🌐 Repo URL"))
            repo_tip = "Emu-Repo URL ändern" if lang == "de" else "Change Emu-Repo URL"
            safe_ui("btn_repo_url", "setToolTip", repo_tip)

        # --- PATCH-ORDNER / PATCH-KOPIERBUTTONS ---
        safe_ui("header_btn_s3_ncam", "setText", get_t("patch_folder_s3_ncam", "S3-NCam-Bonecrew-Pfad" if lang == "de" else "S3-NCam-Bonecrew path"))
        safe_ui("header_btn_s4", "setText", get_t("patch_folder_s4", "S4-Pfad" if lang == "de" else "S4 path"))
        safe_ui("header_btn_patch_ncam", "setText", get_t("patch_copy_s3_ncam", "Patch → S3-NCam"))
        safe_ui("header_btn_patch_s4", "setText", get_t("patch_copy_s4", "Patch → S4"))
        safe_ui("header_btn_s3_ncam", "setToolTip", get_t("patch_folder_s3_ncam_tooltip", "S3-NCam-Bonecrew-Pfad wählen" if lang == "de" else "Select the S3-NCam-Bonecrew path"))
        safe_ui("header_btn_s4", "setToolTip", get_t("patch_folder_s4_tooltip", "S4-Pfad wählen" if lang == "de" else "Select the S4 path"))
        safe_ui(
            "header_btn_patch_ncam",
            "setToolTip",
            "Kopiert oscam-emu.patch in den konfigurierten S3-NCam-Bonecrew-Patch-Ordner."
            if lang == "de"
            else "Copies oscam-emu.patch to the configured S3-NCam-Bonecrew patch folder.",
        )
        safe_ui(
            "header_btn_patch_s4",
            "setToolTip",
            "Kopiert oscam-emu.patch in den konfigurierten S4-Patch-Ordner."
            if lang == "de"
            else "Copies oscam-emu.patch to the configured S4 patch folder.",
        )
        safe_ui(
            "log_button",
            "setToolTip",
            "Speichert den aktuellen Log-/Protokollinhalt in einer Datei."
            if lang == "de"
            else "Saves the current log output to a file.",
        )

        # --- E) LABELS & SYSTEM ---
        safe_ui("lang_label", "setText", get_t("language_label", "Sprache:"))
        safe_ui("color_label", "setText", get_t("color_label", "Farbe:"))
        safe_ui("commit_label", "setText", get_t("commit_count_label", "Anzahl Commits:"))

        safe_ui(
            "patch_emu_git_button",
            "setText",
            get_t("patch_emu_git_button", "Patch OSCam Emu"),
        )
        safe_ui(
            "github_upload_patch_button",
            "setText",
            get_t("github_upload_button", "GitHub Upload"),
        )
        safe_ui("clean_emu_button", "setText", get_t("clean_emu_button", "Bereinigen"))
        safe_ui("copy_log_button", "setText", get_t("copy_log_button", "LOG KOPIEREN" if lang == "de" else "COPY LOG"))
        safe_ui("analyze_build_logs_button", "setText", get_t("analyze_build_logs_button", "BUILD-LOG ANALYSIEREN" if lang == "de" else "ANALYZE BUILD LOG"))
        safe_ui("diagnostics_button", "setText", get_t("system_diagnostics_button", "SYSTEM-DIAGNOSE" if lang == "de" else "SYSTEM DIAGNOSTICS"))
        safe_ui("log_history_button", "setText", get_t("build_log_history_button", "BUILD-LOG-VERLAUF" if lang == "de" else "BUILD LOG HISTORY"))
        safe_ui("clear_console_button", "setText", get_t("clear_console_button", "KONSOLE LEEREN" if lang == "de" else "CLEAR CONSOLE"))
        safe_ui("copy_log_button", "setToolTip", get_t("copy_log_tooltip", "Gesamte sichtbare Konsolenausgabe in die Zwischenablage kopieren" if lang == "de" else "Copy all visible console output to the clipboard"))
        safe_ui("clear_console_button", "setToolTip", get_t("clear_console_tooltip", "Nur die sichtbare Konsolenausgabe leeren; Logdateien bleiben unverändert" if lang == "de" else "Clear only the visible console output; log files remain unchanged"))
        safe_ui("analyze_build_logs_button", "setToolTip", get_t("analyze_build_logs_tooltip", "Das neueste Build-Protokoll auf typische Fehler und Warnungen prüfen" if lang == "de" else "Check the newest build log for common errors and warnings"))
        safe_ui("diagnostics_button", "setToolTip", get_t("system_diagnostics_tooltip", "System, Tool-Verzeichnisse und vorhandene Build-Logs prüfen; startet keinen Build" if lang == "de" else "Check system, tool directories and existing build logs; does not start a build"))
        safe_ui("log_history_button", "setToolTip", get_t("build_log_history_tooltip", "Alle Build-Logs nach Status, Zeit und Fehlerhinweisen vergleichen" if lang == "de" else "Compare all build logs by status, time and error indicators"))

        

        # Tooltips und Hover-Farben nach allen Textänderungen synchronisieren.
        try:
            self.apply_global_button_style()
        except Exception as exc:
            print(f"[update_language] Button-Theme/Tooltips konnten nicht aktualisiert werden: {exc}")

        QApplication.processEvents()

    # =====================
    # GITHUB EMU CREDENTIALS
    # =====================

    def check_emu_credentials(self):
        cfg = load_github_config()
        if not all([cfg.get("emu_repo_url"), cfg.get("username"), cfg.get("token")]):
            lang = getattr(self, "LANG", LANG)
            self.append_info(
                self.info_text,
                TEXTS[lang].get(
                    "github_emu_credentials_missing", "GitHub-Emu-Zugangsdaten fehlen!"
                ),
                "warning",
            )

    def edit_emu_github_config(self, info_widget=None, progress_callback=None):
        """
        Öffnet den GitHub-Konfigurationsdialog mit Regenbogen-ProgressBar,
        schwarzer Schrift, Sound und zweisprachigem UI.
        """
        from PyQt6.QtWidgets import QFormLayout, QLabel, QDialogButtonBox, QApplication
        from PyQt6.QtCore import QTimer

        # --- Final Label verstecken ---
        if hasattr(self, "hide_final_label"):
            self.hide_final_label()

        widget = info_widget or getattr(self, "info_text", None)
        pbar = getattr(self, "progress_bar", None)
        current_lang = str(getattr(self, "LANG", "de")).lower()[:2]
        is_de = current_lang == "de"

        # --- Styles ---
        rainbow = (
            "qlineargradient(x1:0, y1:0, x2:1, y2:0, "
            "stop:0.0 #FF0000, stop:0.2 #FF7F00, stop:0.4 #FFFF00, "
            "stop:0.6 #00FF00, stop:0.8 #0000FF, stop:1.0 #8B00FF);"
        )
        style_rainbow = f"""
            QProgressBar {{
                text-align: center;
                font-weight: bold;
                border: 2px solid #222;
                border-radius: 6px;
                background-color: #111;
                color: black;
                font-size: 11pt;
            }}
            QProgressBar::chunk {{
                background-color: {rainbow};
                border-radius: 4px;
            }}
        """
        style_fade = """
            QProgressBar {
                text-align: center;
                font-weight: bold;
                border: 2px solid #222;
                border-radius: 6px;
                background-color: #111;
                color: black;
                font-size: 11pt;
            }
            QProgressBar::chunk { background-color: transparent; }
        """

        # --- Helper-Funktionen ---
        def set_progress(val, txt=None):
            if pbar:
                pbar.setStyleSheet(style_rainbow)
                pbar.setValue(val)
                pbar.setFormat(txt if txt else "%p%")
                pbar.show()
            if progress_callback:
                try:
                    progress_callback(val)
                except:
                    pass
            QApplication.processEvents()

        def finalize_pbar(text, visible_seconds=3):
            if pbar:
                pbar.setValue(100)
                pbar.setFormat(text)
                QTimer.singleShot(
                    visible_seconds * 1000, lambda: pbar.setStyleSheet(style_fade)
                )
                QTimer.singleShot(visible_seconds * 1000, lambda: pbar.setValue(0))
                if progress_callback:
                    QTimer.singleShot(
                        visible_seconds * 1000, lambda: progress_callback(0)
                    )

        def play_sound(action="open"):
            if "safe_play" in globals():
                sound_map = {"open": "dialog-information.oga", "save": "complete.oga"}
                safe_play(sound_map.get(action, "complete.oga"))

        def get_txt(key, default=""):
            lang_pkg = TEXTS.get(current_lang, TEXTS.get("de", {}))
            return lang_pkg.get(key, default)

        # --- Start Progress ---
        set_progress(
            20,
            "⏳ "
            + (
                get_txt("loading_config", "Konfiguration wird geladen...")
                if is_de
                else "Loading configuration..."
            ),
        )
        play_sound("open")

        dialog = GithubConfigDialog(self)
        set_progress(50)

        # UI Texte anpassen
        layout = dialog.layout()
        if isinstance(layout, QFormLayout):
            mapping = [
                (dialog.patch_repo, "patch_repo_label", "Patch Repo:"),
                (dialog.patch_branch, "patch_branch_label", "Patch Branch:"),
                (dialog.emu_repo, "emu_repo_label", "EMU Repo:"),
                (dialog.emu_branch, "emu_branch_label", "EMU Branch:"),
                (dialog.username, "github_username_label", "GitHub User:"),
                (dialog.token, "github_token_label", "Token:"),
                (dialog.user_name, "github_user_name_label", "Git Name:"),
                (dialog.user_email, "github_user_email_label", "Git Email:"),
            ]
            for field, key, default in mapping:
                label = layout.labelForField(field)
                if label and isinstance(label, QLabel):
                    label.setText(get_txt(key, default))

        # Buttons anpassen
        button_box = dialog.findChild(QDialogButtonBox)
        if button_box:
            save_btn = button_box.button(QDialogButtonBox.StandardButton.Save)
            cancel_btn = button_box.button(QDialogButtonBox.StandardButton.Cancel)
            if save_btn:
                save_btn.setText(get_txt("save", "Speichern"))
            if cancel_btn:
                cancel_btn.setText(get_txt("cancel", "Abbrechen"))

        set_progress(
            80, "🔧 " + (get_txt("config_ready", "Bereit") if is_de else "Ready")
        )

        # --- Dialog ausführen ---
        if dialog.exec():
            msg = get_txt("github_config_saved", "GitHub Konfiguration gespeichert.")
            self.append_info(widget, msg, "success")
            play_sound("save")
            finalize_pbar(
                "✅ " + (get_txt("saved", "Gespeichert!") if is_de else "Saved!")
            )
        else:
            finalize_pbar("%p%")

    # =====================
    # RUN ACTION WRAPPER
    # =====================
    def run_action(self, action_func):
        """
        Führt eine Aktion aus, zeigt Fortschritt in der ProgressBar und Meldungen im Info-Widget.
        action_func: Funktion, die (info_widget, progress_callback) als Parameter akzeptiert
        """
        lang = getattr(self, "LANG", LANG)  # aktuelle GUI-Sprache

        try:
            # Progress starten
            self.progress.setValue(0)
            self.progress.setStyleSheet(
                f"QProgressBar::chunk {{background-color:{current_diff_colors['bg']};}}"
            )

            # Aktion ausführen
            action_func(
                info_widget=self.info_text, progress_callback=self.progress.setValue
            )

            # Fortschritt abschließen
            self.progress.setValue(100)

        except Exception as e:
            # Fehler im Info-Widget ausgeben, sprachabhängig
            error_text = (
                TEXTS[lang]
                .get("generic_error", "❌ Fehler: {error}")
                .format(error=str(e))
            )
            self.append_info(self.info_text, error_text, "error")
            self.progress.setValue(0)

    # =====================
    # BUTTON CALLBACKS
    # =====================

    
    def apply_patch(self, info_widget=None, progress_callback=None):
        """
        Wendet den OSCam-Emu-Patch auf das konfigurierte
        Streamboard-OSCam-Quellverzeichnis an.

        Das Zielverzeichnis wird ausschließlich über STREAMREPO
        bestimmt.

        Erwartetes Verzeichnis:
            <PLUGIN_DIR>\\streamboard-git
        """

        if hasattr(self, "hide_final_label"):
            try:
                self.hide_final_label()
            except Exception:
                pass

        import os
        from PyQt6.QtWidgets import QApplication

        # ---------------------------------------------------------
        # Info-Widget
        # ---------------------------------------------------------

        info_widget = (
            info_widget
            if info_widget is not None
            else getattr(
                self,
                "info_text",
                None
            )
        )

        # ---------------------------------------------------------
        # Sprache
        # ---------------------------------------------------------

        lang = getattr(
            self,
            "LANG",
            "de"
        ).lower()

        # ---------------------------------------------------------
        # ProgressBar
        # ---------------------------------------------------------

        pbar = getattr(
            self,
            "progress_bar",
            None
        )

        rainbow = (
            "qlineargradient("
            "x1:0, y1:0, x2:1, y2:0, "
            "stop:0.0 #FF0000, "
            "stop:0.2 #FF7F00, "
            "stop:0.4 #FFFF00, "
            "stop:0.6 #00FF00, "
            "stop:0.8 #0000FF, "
            "stop:1.0 #8B00FF"
            ");"
        )

        style_rainbow = f"""
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

        style_error = """
            QProgressBar {
                text-align: center;
                font-weight: 900;
                border: 2px solid #500;
                border-radius: 6px;
                background-color: #111;
                color: #FF0000;
                font-size: 14pt;
            }

            QProgressBar::chunk {
                background: #800;
                border-radius: 4px;
            }
        """

        def update_p(value, is_err=False):
            """Aktualisiert ProgressBar und optionalen Callback."""

            if pbar:
                try:
                    pbar.setFormat("%p%")
                    pbar.setStyleSheet(
                        style_error
                        if is_err
                        else style_rainbow
                    )
                    pbar.setValue(value)
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

        def play_apply_sound(success=True):
            """Spielt Abschluss- bzw. Fehlerton."""

            try:
                safe_play = globals().get(
                    "safe_play"
                )

                if safe_play:
                    safe_play(
                        "complete.oga"
                        if success
                        else "dialog-error.oga"
                    )
            except Exception:
                pass

        # =========================================================
        # KONFIGURATION
        # =========================================================

        patch_file = globals().get(
            "PATCH_FILE",
            getattr(
                self,
                "PATCH_FILE",
                ""
            )
        )

        # ---------------------------------------------------------
        # WICHTIG:
        # Das OSCam-Quellverzeichnis kommt ausschließlich
        # aus STREAMREPO.
        # ---------------------------------------------------------

        source_dir = globals().get(
            "STREAMREPO",
            ""
        )

        if not source_dir:
            source_dir = getattr(
                self,
                "STREAMREPO",
                ""
            )

        # ---------------------------------------------------------
        # PATCH_FILE prüfen
        # ---------------------------------------------------------

        if not patch_file:
            self.append_info(
                info_widget,
                "❌ PATCH_FILE ist nicht konfiguriert!",
                "error"
            )

            update_p(
                100,
                True
            )

            play_apply_sound(
                False
            )

            return False

        # ---------------------------------------------------------
        # STREAMREPO prüfen
        # ---------------------------------------------------------

        if not source_dir:
            self.append_info(
                info_widget,
                (
                    "❌ STREAMREPO ist nicht konfiguriert!\n\n"
                    "Das OSCam-Quellverzeichnis muss über "
                    "STREAMREPO definiert werden."
                ),
                "error"
            )

            update_p(
                100,
                True
            )

            play_apply_sound(
                False
            )

            return False

        # ---------------------------------------------------------
        # Pfade normalisieren
        # ---------------------------------------------------------

        patch_file = os.path.abspath(
            os.path.expanduser(
                os.path.expandvars(
                    str(patch_file)
                )
            )
        )

        source_dir = os.path.abspath(
            os.path.expanduser(
                os.path.expandvars(
                    str(source_dir)
                )
            )
        )

        # ---------------------------------------------------------
        # Start
        # ---------------------------------------------------------

        update_p(10)

        self.append_info(
            info_widget,
            f"📁 OSCam-Quellverzeichnis:\n{source_dir}",
            "info"
        )

        # ---------------------------------------------------------
        # Patch-Datei prüfen
        # ---------------------------------------------------------

        if not os.path.isfile(
            patch_file
        ):
            msg = self.get_t(
                "patch_file_missing",
                "❌ Patch-Datei fehlt!"
            ).format(
                path=patch_file
            )

            self.append_info(
                info_widget,
                msg,
                "error"
            )

            update_p(
                100,
                True
            )

            play_apply_sound(
                False
            )

            if pbar:
                try:
                    pbar.setFormat(
                        "❌ Datei fehlt"
                    )
                except Exception:
                    pass

            return False

        # ---------------------------------------------------------
        # OSCam-Verzeichnis prüfen
        # ---------------------------------------------------------

        if not os.path.isdir(
            source_dir
        ):
            self.append_info(
                info_widget,
                (
                    "❌ OSCam-Quellverzeichnis nicht gefunden:\n"
                    f"{source_dir}"
                ),
                "error"
            )

            update_p(
                100,
                True
            )

            play_apply_sound(
                False
            )

            return False

        # =========================================================
        # OSCAM QUELLCODE PRÜFEN
        # =========================================================

        required_files = (
            "Makefile",
            "oscam.c",
            "globals.h"
        )

        missing_files = [
            filename
            for filename in required_files
            if not os.path.isfile(
                os.path.join(
                    source_dir,
                    filename
                )
            )
        ]

        if missing_files:
            self.append_info(
                info_widget,
                (
                    "❌ Das Zielverzeichnis enthält keinen "
                    "vollständigen OSCam-Quellcode.\n\n"
                    f"Verzeichnis:\n{source_dir}\n\n"
                    "Fehlende Dateien:\n"
                    + "\n".join(
                        f"• {filename}"
                        for filename in missing_files
                    )
                ),
                "error"
            )

            update_p(
                100,
                True
            )

            play_apply_sound(
                False
            )

            return False

        # ---------------------------------------------------------
        # Logger
        # ---------------------------------------------------------

        def logger(
            text,
            level="info"
        ):
            try:
                self.append_info(
                    info_widget,
                    text,
                    level
                )
            except Exception:
                pass

        # =========================================================
        # STARTMELDUNG
        # =========================================================

        start_msg = self.get_t(
            "executing_git_apply",
            "🚀 Wende Patch an..."
        ).format(
            patch=os.path.basename(
                patch_file
            )
        )

        self.append_info(
            info_widget,
            start_msg,
            "warning"
        )

        self.append_info(
            info_widget,
            f"📁 Ziel: {source_dir}",
            "info"
        )

        self.append_info(
            info_widget,
            f"📄 Patch: {patch_file}",
            "info"
        )

        update_p(40)

        # =========================================================
        # PATCH ANWENDEN
        # =========================================================

        try:

            self.append_info(
                info_widget,
                "🔧 Führe git apply aus...",
                "info"
            )

            code = run_bash(
                f'git apply {patch_file}',
                cwd=source_dir,
                logger=logger,
                lang=lang
            )

            # -----------------------------------------------------
            # ERFOLG
            # -----------------------------------------------------

            if code == 0:

                self.append_info(
                    info_widget,
                    self.get_t(
                        "patch_emu_git_done",
                        "✅ Patch erfolgreich angewendet"
                    ),
                    "success"
                )

                self.append_info(
                    info_widget,
                    f"📁 Gepatchtes Repository:\n{source_dir}",
                    "success"
                )

                update_p(100)

                play_apply_sound(
                    True
                )

                if pbar:
                    try:
                        pbar.setFormat(
                            "✅ Patch angewendet"
                        )
                    except Exception:
                        pass

                return True

            # -----------------------------------------------------
            # GIT APPLY FEHLER
            # -----------------------------------------------------

            self.append_info(
                info_widget,
                self.get_t(
                    "patch_emu_git_apply_failed",
                    "❌ Patch konnte nicht angewendet werden"
                ),
                "error"
            )

            self.append_info(
                info_widget,
                (
                    f"📁 Zielverzeichnis:\n"
                    f"{source_dir}"
                ),
                "error"
            )

            self.append_info(
                info_widget,
                (
                    f"📄 Patch-Datei:\n"
                    f"{patch_file}"
                ),
                "error"
            )

            update_p(
                100,
                True
            )

            play_apply_sound(
                False
            )

            if pbar:
                try:
                    pbar.setFormat(
                        "❌ Fehler beim Patchen"
                    )
                except Exception:
                    pass

            return False

        # =========================================================
        # SCHWERER FEHLER
        # =========================================================

        except Exception as exc:

            self.append_info(
                info_widget,
                (
                    "❌ Schwerer Fehler beim Patchen:\n"
                    f"{exc}"
                ),
                "error"
            )

            update_p(
                100,
                True
            )

            play_apply_sound(
                False
            )

            if pbar:
                try:
                    pbar.setFormat(
                        "❌ Fehler beim Patchen"
                    )
                except Exception:
                    pass

            return False

        # =========================================================
        # ABSCHLUSS
        # =========================================================

        finally:

            try:
                QApplication.processEvents()
            except Exception:
                pass


    def change_old_(self, info_widget=None, progress_callback=None):
        # --- Final Label verstecken ---
        if hasattr(self, "hide_final_label"):
            self.hide_final_label()
        global OLD_, OLD_PATCH_FILE, ALT_PATCH_FILE

        info_widget = info_widget or self.info_text
        lang = getattr(self, "LANG", LANG)  # aktuelle GUI-Sprache

        new_dir = QFileDialog.getExistingDirectory(
            self, TEXTS[lang]["change_old_dir"], OLD_
        )

        if new_dir:
            OLD_ = new_dir
            OLD_PATCH_FILE = os.path.join(OLD_, "oscam-emu.patch")
            ALT_PATCH_FILE = os.path.join(OLD_, "oscam-emu.altpatch")

            self.OLD_PATCH_DIR = OLD_
            self.OLD_PATCH_FILE = OLD_PATCH_FILE
            self.ALT_PATCH_FILE = ALT_PATCH_FILE
            saved = save_config(
                {
                    "s3_patch_path": OLD_,
                    "old_patch_dir": OLD_,
                },
                gui_instance=self,
                silent=True,
            )
            if not saved:
                self.append_info(
                    info_widget,
                    "❌ Fehler beim Speichern des Patch-Pfads.",
                    "error",
                )
                return False

            self.append_info(
                info_widget,
                TEXTS[lang]["old_patch_path_changed"].format(OLD_=OLD_),
                "success",
            )
        else:
            self.append_info(
                info_widget, TEXTS[lang]["old_patch_path_cancelled"], "info"
            )

        if progress_callback:
            progress_callback(100)

    def select_ncam_patch_path_manually(self):
        """Wählt ausschließlich den Patch-Ordner für S3-NCam-Bonecrew."""
        from PyQt6.QtWidgets import QMessageBox

        is_de = str(getattr(self, "LANG", "de")).lower()[:2] == "de"
        current = getattr(self, "NCAM_PATCH_PATH", "") or getattr(self, "OLD_PATCH_DIR", os.getcwd())
        title = "S3-NCam-Bonecrew Patch-Ordner auswählen" if is_de else "Select S3-NCam-Bonecrew patch folder"
        directory = QFileDialog.getExistingDirectory(self, title, current)
        if not directory:
            return
        directory = os.path.normpath(directory)
        if not isinstance(getattr(self, "cfg", None), dict):
            self.cfg = {}
        saved = save_config({"ncam_patch_path": directory}, gui_instance=self, silent=True)
        if saved is False:
            QMessageBox.warning(
                self, "Fehler" if is_de else "Error",
                ("Der S3-NCam-Patch-Pfad konnte nicht gespeichert werden:\n" if is_de else "Could not save the S3-NCam patch path:\n") + directory,
            )
            return
        self.NCAM_PATCH_PATH = directory
        self.cfg["ncam_patch_path"] = directory
        QMessageBox.information(
            self, "Pfad gespeichert" if is_de else "Path saved",
            ("S3-NCam-Patch-Pfad wurde gespeichert:\n" if is_de else "S3-NCam patch path was saved:\n") + directory,
        )

    def select_s4_patch_path_manually(self):
        """Wählt ausschließlich den Patch-Ordner für S4."""
        from PyQt6.QtWidgets import QMessageBox

        is_de = str(getattr(self, "LANG", "de")).lower()[:2] == "de"
        current = getattr(self, "S4_PATCH_PATH", "") or getattr(self, "OLD_PATCH_DIR", os.getcwd())
        title = "S4 Patch-Ordner auswählen" if is_de else "Select S4 patch folder"
        directory = QFileDialog.getExistingDirectory(self, title, current)
        if not directory:
            return
        directory = os.path.normpath(directory)
        if not isinstance(getattr(self, "cfg", None), dict):
            self.cfg = {}
        saved = save_config({"s4_patch_path": directory}, gui_instance=self, silent=True)
        if saved is False:
            QMessageBox.warning(
                self, "Fehler" if is_de else "Error",
                ("Der S4-Patch-Pfad konnte nicht gespeichert werden:\n" if is_de else "Could not save the S4 patch path:\n") + directory,
            )
            return
        self.S4_PATCH_PATH = directory
        self.cfg["s4_patch_path"] = directory
        QMessageBox.information(
            self, "Pfad gespeichert" if is_de else "Path saved",
            ("S4-Patch-Pfad wurde gespeichert:\n" if is_de else "S4 patch path was saved:\n") + directory,
        )

    
    def copy_patch_to_target(self, target):
        """Kopiert oscam-emu.patch in den gewählten S3-NCam- oder S4-Ordner."""
        import os
        import shutil
        from PyQt6.QtWidgets import QMessageBox
        from paths import PATCH_FILE as CONFIG_PATCH_FILE

        is_de = str(getattr(self, "LANG", "de")).lower()[:2] == "de"

        # Patch-Quelldatei ermitteln
        source = (
            getattr(self, "PATCH_FILE", None)
            or globals().get("PATCH_FILE")
            or CONFIG_PATCH_FILE
        )
        source = os.path.abspath(
            os.path.expanduser(
                os.path.expandvars(str(source))
            )
        )

        # Gespeicherte Konfiguration lesen
        cfg = getattr(self, "cfg", {})
        if not isinstance(cfg, dict):
            cfg = {}

        # Zielordner ermitteln
        if target == "ncam":
            destination_dir = (
                getattr(self, "NCAM_PATCH_PATH", None)
                or globals().get("NCAM_PATCH_PATH")
                or cfg.get("ncam_patch_path", "")
            )
            label = "S3-NCam-Bonecrew"

        elif target == "s4":
            destination_dir = (
                getattr(self, "S4_PATCH_PATH", None)
                or globals().get("S4_PATCH_PATH")
                or cfg.get("s4_patch_path", "")
            )
            label = "S4"

        else:
            return

        destination_dir = (
            os.path.abspath(
                os.path.expanduser(
                    os.path.expandvars(str(destination_dir))
                )
            )
            if destination_dir else ""
        )

        # Quelldatei prüfen
        if not os.path.isfile(source):
            QMessageBox.warning(
                self,
                "Patch fehlt" if is_de else "Patch missing",
                f"Die Quelldatei wurde nicht gefunden:\n{source}"
                if is_de else
                f"The patch file was not found:\n{source}",
            )
            return

        # Zielordner prüfen
        if not destination_dir:
            QMessageBox.warning(
                self,
                "Pfad fehlt" if is_de else "Path missing",
                f"Bitte zuerst den Zielordner für {label} auswählen."
                if is_de else
                f"Please select the destination folder for {label} first.",
            )
            return

        if not os.path.isdir(destination_dir):
            QMessageBox.warning(
                self,
                "Ordner fehlt" if is_de else "Folder missing",
                f"Der Zielordner existiert nicht:\n{destination_dir}"
                if is_de else
                f"The destination folder does not exist:\n{destination_dir}",
            )
            return

        try:
            destination = os.path.join(
                destination_dir, "oscam-emu.patch"
            )

            # Backup der vorhandenen Datei anlegen
            if os.path.isfile(destination):
                shutil.copy2(destination, destination + ".bak")

            shutil.copy2(source, destination)

            message = (
                f"Patch erfolgreich nach {label} kopiert:\n{destination}"
            )
            self.append_info(
                getattr(self, "info_text", None),
                f"✅ {message}",
                "success",
            )

            QMessageBox.information(
                self,
                "Patch kopiert" if is_de else "Patch copied",
                message if is_de else
                f"Patch successfully copied to {label}:\n{destination}",
            )

        except Exception as exc:
            self.append_info(
                getattr(self, "info_text", None),
                f"❌ Patch-Kopie fehlgeschlagen: {exc}",
                "error",
            )
            QMessageBox.critical(
                self,
                "Fehler" if is_de else "Error",
                f"Patch konnte nicht kopiert werden:\n{exc}"
                if is_de else
                f"Could not copy patch:\n{exc}",
            )


    

    def open_build_log_history(self):
        """Crash-safe entry point: history errors must never escape a Qt slot."""
        try:
            return self._open_build_log_history_impl()
        except Exception as exc:
            import traceback
            is_de = str(getattr(self, "LANG", LANG)).lower().startswith("de")
            title = "Fehler im Build-Log-Verlauf" if is_de else "Build log history error"
            message = (("Der Build-Log-Verlauf konnte nicht geöffnet werden. Die Haupt-GUI bleibt geöffnet.\n\n" if is_de
                        else "Build log history could not be opened. The main GUI will remain open.\n\n")
                       + f"{type(exc).__name__}: {exc}")
            try:
                from PyQt6.QtWidgets import QMessageBox
                QMessageBox.critical(self, title, message)
            except Exception:
                print(message)
            traceback.print_exc()
            return None

    def _open_build_log_history_impl(self):
        """Show a read-only, bilingual overview of existing build logs."""
        import os
        import re
        import csv
        from datetime import datetime
        from PyQt6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
                                     QTableWidget, QTableWidgetItem, QFileDialog, QMessageBox,
                                     QHeaderView, QLineEdit, QComboBox, QTextEdit)
        from PyQt6.QtCore import QTimer

        is_de = str(getattr(self, "LANG", LANG)).lower().startswith("de")
        title = "BUILD-LOG-VERLAUF" if is_de else "BUILD LOG HISTORY"
        dialog = QDialog(self)
        dialog.setWindowTitle(title)
        dialog.resize(1080, 650)
        # Avoid forcing a very wide window on smaller displays; action buttons
        # are already arranged in separate rows below.
        dialog.setMinimumSize(720, 500)
        layout = QVBoxLayout(dialog)
        summary_label = QLabel()
        layout.addWidget(summary_label)
        filter_row = QHBoxLayout()
        search_label = QLabel("Suche:" if is_de else "Search:")
        search_input = QLineEdit()
        search_input.setPlaceholderText("Dateiname oder Status…" if is_de else "Filename or status…")
        status_filter = QComboBox()
        status_filter.addItems((["Alle Status", "Erfolgreich", "Fehlgeschlagen", "Unbekannt", "Gestartet", "Lesefehler"] if is_de
                                else ["All statuses", "Successful", "Failed", "Unknown", "Started", "Read error"]))
        issue_filter = QComboBox()
        issue_filter.addItems((['Alle Logs', 'Mit Fehlern', 'Mit Fehlern oder Warnungen'] if is_de
                               else ['All logs', 'With errors', 'With errors or warnings']))
        issue_filter.setToolTip('Nach Fehlern und Warnungen filtern' if is_de else 'Filter by errors and warnings')
        filter_row.addWidget(search_label)
        filter_row.addWidget(search_input, 1)
        filter_row.addWidget(status_filter)
        filter_row.addWidget(issue_filter)
        layout.addLayout(filter_row)
        headers = (["Projekt/Datei", "Status", "Geändert", "Größe", "Fehler", "Warnungen"] if is_de
                   else ["Project/File", "Status", "Modified", "Size", "Errors", "Warnings"])
        table = QTableWidget(0, len(headers), dialog)
        table.setHorizontalHeaderLabels(headers)
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        table.setSelectionMode(QTableWidget.SelectionMode.ExtendedSelection)
        table.setAlternatingRowColors(True)
        table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for col in range(1, len(headers)):
            table.horizontalHeader().setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)
        layout.addWidget(table, 1)

        def scan_logs():
            log_dir = getattr(self, "build_log_dir", None) or os.path.join(os.path.dirname(os.path.abspath(__file__)), "build_logs")
            rows = []
            try:
                # Collect file metadata once (rather than calling stat repeatedly during sorting).
                # Limit the overview to the newest 500 logs to keep refreshes predictable.
                candidates = []
                for name in os.listdir(log_dir):
                    if not name.lower().endswith(".log"):
                        continue
                    path = os.path.join(log_dir, name)
                    try:
                        stat = os.stat(path)
                        if os.path.isfile(path):
                            candidates.append((stat.st_mtime, name, stat.st_size))
                    except OSError:
                        # Keep inaccessible entries visible if their metadata can no longer be read.
                        candidates.append((0, name, None))
                candidates.sort(key=lambda entry: entry[0], reverse=True)
                max_log_files = 500
                names = [(name, size) for _, name, size in candidates[:max_log_files]]
                omitted_count = max(0, len(candidates) - len(names))
            except OSError as exc:
                return [], f"{('Log-Ordner nicht lesbar' if is_de else 'Log directory unreadable')}: {exc}"
            error_re = re.compile(r"\b(error:|fatal error:|undefined reference|command not found|failed|failure|make\[[0-9]+\]: \*\*\*)", re.I)
            warning_re = re.compile(r"\bwarning:|\bdeprecated\b", re.I)
            # A per-file cap alone could still read several gigabytes when hundreds
            # of logs exist. Bound the complete refresh as well as each individual log.
            total_scan_budget = 32 * 1024 * 1024
            scanned_bytes = 0
            for name, known_size in names:
                path = os.path.join(log_dir, name)
                try:
                    max_scan_bytes = 8 * 1024 * 1024
                    remaining_budget = max(0, total_scan_budget - scanned_bytes)
                    if remaining_budget == 0:
                        rows.append({"file": name, "path": path,
                                     "status": "Unbekannt (Scan-Limit)" if is_de else "Unknown (scan limit)",
                                     "modified": datetime.fromtimestamp(os.path.getmtime(path)).astimezone().strftime("%Y-%m-%d %H:%M:%S"),
                                     "size": known_size if isinstance(known_size, int) else "",
                                     "errors": "", "warnings": "", "scan_truncated": True,
                                     "scan_limited": True})
                        continue
                    # Counts/status are based on the sampled prefix for oversized files.
                    read_limit = min(max_scan_bytes, remaining_budget)
                    with open(path, "rb") as fh:
                        raw_content = fh.read(read_limit)
                    scanned_bytes += len(raw_content)
                    scan_truncated = (isinstance(known_size, int) and known_size > len(raw_content)) or len(raw_content) == read_limit
                    content = raw_content.decode("utf-8", errors="replace")
                    finished = re.findall(r"SIMPLEBUILD_FINISHED:exit=(-?\d+)", content, re.I)
                    exit_codes = re.findall(r"(?:Exit-Code|exit code):\s*(-?\d+)", content, re.I)
                    if finished:
                        code = finished[-1]
                        status = (("Erfolgreich" if code == "0" else f"Fehlgeschlagen ({code})") if is_de else ("Successful" if code == "0" else f"Failed ({code})"))
                    elif "SIMPLEBUILD_STARTED" in content:
                        status = "Gestartet – Abschluss fehlt" if is_de else "Started – completion missing"
                    elif exit_codes:
                        code = exit_codes[-1]
                        status = (("Erfolgreich" if code == "0" else f"Fehlgeschlagen ({code})") if is_de else ("Successful" if code == "0" else f"Failed ({code})"))
                    else:
                        status = "Unbekannt" if is_de else "Unknown"
                    errors = len(error_re.findall(content))
                    warnings = len(warning_re.findall(content))
                    modified = datetime.fromtimestamp(os.path.getmtime(path)).astimezone().strftime("%Y-%m-%d %H:%M:%S")
                    file_size = known_size if isinstance(known_size, int) else os.path.getsize(path)
                    rows.append({"file": name, "path": path, "status": status, "modified": modified,
                                 "size": file_size, "errors": errors, "warnings": warnings,
                                 "scan_truncated": scan_truncated, "scan_limited": False})
                except OSError:
                    rows.append({"file": name, "path": path, "status": "Lesefehler" if is_de else "Read error",
                                 "modified": "", "size": "", "errors": "", "warnings": ""})
            if omitted_count:
                limit_note = (f"Anzeige auf die {max_log_files} neuesten Logs begrenzt; {omitted_count} ältere Logs ausgelassen."
                              if is_de else
                              f"Showing the {max_log_files} newest logs; omitted {omitted_count} older logs.")
            else:
                limit_note = None
            return rows, limit_note

        current_rows = []
        all_rows = []
        current_problem = None

        def safe_callback(callback, *args):
            """Keep exceptions in Qt signal callbacks from taking down the GUI."""
            try:
                return callback(*args)
            except Exception as exc:
                try:
                    if 'live_timer' in locals():
                        live_timer.stop()
                except Exception:
                    pass
                try:
                    QMessageBox.warning(
                        dialog,
                        "Fehler im Build-Log-Verlauf" if is_de else "Build log history error",
                        ("Der Build-Log-Verlauf konnte diesen Schritt nicht ausführen. Die GUI bleibt geöffnet.\n\n" if is_de
                         else "Build log history could not complete this action. The GUI will remain open.\n\n")
                        + f"{type(exc).__name__}: {exc}",
                    )
                except Exception:
                    pass
                return None

        def render_rows():
            nonlocal current_rows
            # Preserve selected log filenames when filters or a refresh rebuild the table.
            previously_selected = set()
            try:
                for selected_index in table.selectionModel().selectedRows():
                    old_row = selected_index.row()
                    if 0 <= old_row < len(current_rows):
                        previously_selected.add(str(current_rows[old_row].get("file", "")))
            except Exception:
                previously_selected = set()
            query = search_input.text().strip().casefold()
            selected_status = status_filter.currentText().casefold()
            all_statuses = {"alle status", "all statuses"}
            visible = []
            for item in all_rows:
                haystack = (str(item["file"]) + " " + str(item["status"]) + " " + str(item["path"])).casefold()
                status_text = str(item["status"]).casefold()
                if query and query not in haystack:
                    continue
                selected_issue = issue_filter.currentText().casefold()
                issue_errors = selected_issue in ("mit fehlern", "with errors")
                issue_errors_warnings = selected_issue in ("mit fehlern oder warnungen", "with errors or warnings")
                try:
                    error_count = int(item.get("errors", 0) or 0)
                    warning_count = int(item.get("warnings", 0) or 0)
                except (TypeError, ValueError):
                    error_count, warning_count = 0, 0
                if issue_errors and error_count <= 0:
                    continue
                if issue_errors_warnings and error_count <= 0 and warning_count <= 0:
                    continue
                if selected_status not in all_statuses:
                    if selected_status in ("fehlgeschlagen", "failed"):
                        if not ("fehlgeschlagen" in status_text or "failed" in status_text): continue
                    elif selected_status in ("erfolgreich", "successful"):
                        if not ("erfolgreich" in status_text or "successful" in status_text): continue
                    elif selected_status in ("unbekannt", "unknown"):
                        if not ("unbekannt" in status_text or "unknown" in status_text): continue
                    elif selected_status in ("gestartet", "started"):
                        if not ("gestartet" in status_text or "started" in status_text): continue
                    elif selected_status in ("lesefehler", "read error"):
                        if not ("lesefehler" in status_text or "read error" in status_text): continue
                visible.append(item)
            current_rows = visible
            # Batch table painting to reduce flicker and UI overhead when many logs are shown.
            table.setUpdatesEnabled(False)
            try:
                table.setRowCount(len(current_rows))
                for r, item in enumerate(current_rows):
                    values = [item["file"], item["status"], item["modified"],
                              (f'{item["size"]:,} B' if isinstance(item["size"], int) else str(item["size"])),
                              str(item["errors"]), str(item["warnings"])]
                    for c, value in enumerate(values):
                        cell = QTableWidgetItem(value)
                        if c == 0:
                            tip = item["path"]
                            if item.get("scan_limited"):
                                tip += ("\nInhaltsanalyse wegen des Gesamtlimits von 32 MiB übersprungen; Status und Trefferzahlen sind unbekannt."
                                        if is_de else
                                        "\nContent scan skipped due to the 32 MiB total limit; status and counts are unknown.")
                            elif item.get("scan_truncated"):
                                tip += ("\nInhaltsanalyse begrenzt; Fehler-/Warnungszahlen können unvollständig sein."
                                        if is_de else
                                        "\nContent scan was limited; error/warning counts may be incomplete.")
                            cell.setToolTip(tip)
                        table.setItem(r, c, cell)
                    if str(item.get("file", "")) in previously_selected:
                        table.selectRow(r)
            finally:
                table.setUpdatesEnabled(True)
            failed = sum("fehl" in str(x["status"]).lower() or "fail" in str(x["status"]).lower() for x in current_rows)
            unknown = sum("unbekannt" in str(x["status"]).lower() or "unknown" in str(x["status"]).lower() for x in current_rows)
            summary = (f"{len(current_rows)} von {len(all_rows)} Logs angezeigt · {failed} fehlgeschlagen · {unknown} unbekannt" if is_de
                       else f"Showing {len(current_rows)} of {len(all_rows)} logs · {failed} failed · {unknown} unknown")
            if current_problem:
                summary += " — " + current_problem
            summary_label.setText(summary)

        def refresh():
            nonlocal all_rows, current_problem
            all_rows, current_problem = scan_logs()
            render_rows()

        def preview_log(row, column=0):
            if row < 0 or row >= len(current_rows):
                return
            item = current_rows[row]
            preview = QDialog(dialog)
            preview.setWindowTitle(("Log ansehen: " if is_de else "View log: ") + item["file"])
            preview.resize(900, 600)
            preview_layout = QVBoxLayout(preview)
            path_label = QLabel(item["path"])
            path_label.setWordWrap(True)
            preview_layout.addWidget(path_label)
            text_view = QTextEdit(preview)
            text_view.setReadOnly(True)
            try:
                preview_limit = 8 * 1024 * 1024
                with open(item["path"], "rb") as log_file:
                    raw_preview = log_file.read(preview_limit + 1)
                preview_truncated = len(raw_preview) > preview_limit
                preview_text = raw_preview[:preview_limit].decode("utf-8", errors="replace")
                if preview_truncated:
                    preview_text += ("\n\n[Ansicht gekürzt: nur die ersten 8 MiB werden angezeigt.]"
                                     if is_de else
                                     "\n\n[Preview truncated: showing only the first 8 MiB.] ")
                text_view.setPlainText(preview_text)
            except OSError as exc:
                text_view.setPlainText(("Log konnte nicht gelesen werden: " if is_de else "Could not read log: ") + str(exc))
            preview_layout.addWidget(text_view, 1)
            close_preview = QPushButton("Schließen" if is_de else "Close")
            close_preview.clicked.connect(preview.accept)
            preview_layout.addWidget(close_preview)
            preview.exec()

        def compare_selected_logs():
            """Compare exactly two selected logs in a separate, read-only diff window."""
            selected_indexes = sorted({index.row() for index in table.selectionModel().selectedRows()})
            if len(selected_indexes) != 2:
                QMessageBox.information(
                    dialog, "Hinweis" if is_de else "Notice",
                    "Bitte genau zwei Logs auswählen (Strg-Klick für Mehrfachauswahl)." if is_de else
                    "Please select exactly two logs (Ctrl-click to select multiple rows)."
                )
                return
            first, second = (current_rows[i] for i in selected_indexes)
            diff_dialog = QDialog(dialog)
            diff_dialog.setWindowTitle(("Build-Logs vergleichen: " if is_de else "Compare build logs: ") + first["file"] + " ↔ " + second["file"])
            diff_dialog.resize(1000, 680)
            diff_layout = QVBoxLayout(diff_dialog)
            details = QLabel(
                (f"ALT: {first['file']}  |  Status: {first['status']}\nNEU: {second['file']}  |  Status: {second['status']}" if is_de else
                 f"FIRST: {first['file']}  |  Status: {first['status']}\nSECOND: {second['file']}  |  Status: {second['status']}")
            )
            details.setWordWrap(True)
            diff_layout.addWidget(details)
            diff_view = QTextEdit(diff_dialog)
            diff_view.setReadOnly(True)
            diff_layout.addWidget(diff_view, 1)
            try:
                import difflib
                max_bytes = 2 * 1024 * 1024
                def read_limited(path):
                    with open(path, "rb") as source:
                        data = source.read(max_bytes + 1)
                    truncated = len(data) > max_bytes
                    data = data[:max_bytes]
                    return data.decode("utf-8", errors="replace").splitlines(), truncated
                first_lines, first_truncated = read_limited(first["path"])
                second_lines, second_truncated = read_limited(second["path"])
                diff_lines = list(difflib.unified_diff(
                    first_lines, second_lines,
                    fromfile=first["file"], tofile=second["file"], lineterm=""
                ))
                if first_truncated or second_truncated:
                    diff_lines.append("")
                    diff_lines.append("[" + ("Datei gekürzt: maximal 2 MiB je Log." if is_de else "File truncated: maximum 2 MiB per log.") + "]")
                if diff_lines:
                    diff_view.setPlainText("\n".join(diff_lines))
                else:
                    diff_view.setPlainText("Die Inhalte sind identisch." if is_de else "The contents are identical.")
            except OSError as exc:
                diff_view.setPlainText(("Vergleich fehlgeschlagen: " if is_de else "Comparison failed: ") + str(exc))
            close_diff = QPushButton("Schließen" if is_de else "Close")
            close_diff.clicked.connect(diff_dialog.accept)
            diff_layout.addWidget(close_diff)
            diff_dialog.exec()

        def export_csv():
            if not current_rows:
                QMessageBox.information(dialog, "Hinweis" if is_de else "Notice",
                                        "Keine Logs zum Exportieren vorhanden." if is_de else "No logs available to export.")
                return
            path, _ = QFileDialog.getSaveFileName(dialog, "Log-Verlauf als CSV speichern" if is_de else "Save log history as CSV",
                                                  "build_log_history.csv", "CSV (*.csv)")
            if not path:
                return
            try:
                with open(path, "x", newline="", encoding="utf-8-sig") as fh:
                    writer = csv.writer(fh, delimiter=";")
                    writer.writerow(headers + (["Pfad"] if is_de else ["Path"]))
                    for item in current_rows:
                        writer.writerow([item["file"], item["status"], item["modified"], item["size"], item["errors"], item["warnings"], item["path"]])
                QMessageBox.information(dialog, "Export erfolgreich" if is_de else "Export successful", path)
            except FileExistsError:
                QMessageBox.warning(dialog, "Datei existiert" if is_de else "File exists",
                                    "Datei existiert bereits; bitte anderen Namen wählen." if is_de else "File already exists; choose another name.")
            except OSError as exc:
                QMessageBox.critical(dialog, "Exportfehler" if is_de else "Export error", str(exc))

        def export_json():
            """Export the currently filtered build-log rows as UTF-8 JSON, without overwriting files."""
            import json
            if not current_rows:
                QMessageBox.information(
                    dialog, "Hinweis" if is_de else "Notice",
                    "Keine Logs zum Exportieren vorhanden." if is_de else "No logs available to export."
                )
                return
            default_name = "build_log_history.json"
            target, _ = QFileDialog.getSaveFileName(
                dialog,
                "Log-Verlauf als JSON speichern" if is_de else "Save log history as JSON",
                default_name,
                ("JSON-Datei (*.json)" if is_de else "JSON file (*.json)")
            )
            if not target:
                return
            payload = {
                "format_version": 1,
                "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                "language": "de" if is_de else "en",
                "filters": {
                    "search": search_input.text().strip(),
                    "status": status_filter.currentText(),
                    "issues": issue_filter.currentText(),
                },
                "count": len(current_rows),
                "logs": [
                    {
                        "file": item.get("file", ""),
                        "path": item.get("path", ""),
                        "status": item.get("status", ""),
                        "modified": item.get("modified", ""),
                        "size_bytes": item.get("size", ""),
                        "errors": item.get("errors", ""),
                        "warnings": item.get("warnings", ""),
                    }
                    for item in current_rows
                ],
            }
            try:
                # Exclusive creation prevents accidental overwrite of an existing export.
                with open(target, "x", encoding="utf-8", newline="") as output:
                    json.dump(payload, output, ensure_ascii=False, indent=2)
                    output.write("\n")
                QMessageBox.information(
                    dialog,
                    "Export erfolgreich" if is_de else "Export successful",
                    ("JSON-Export gespeichert unter:\n" if is_de else "JSON export saved to:\n") + target,
                )
            except FileExistsError:
                QMessageBox.warning(
                    dialog, "Datei existiert" if is_de else "File exists",
                    "Die Datei existiert bereits. Bitte einen anderen Namen wählen." if is_de else
                    "The file already exists. Please choose another name."
                )
            except OSError as exc:
                QMessageBox.critical(
                    dialog, "Exportfehler" if is_de else "Export error",
                    ("JSON-Export fehlgeschlagen:\n" if is_de else "JSON export failed:\n") + str(exc)
                )

        def find_duplicate_logs():
            """Find byte-identical logs by SHA-256 without changing any files."""
            import hashlib
            # Group by size first to avoid hashing files that cannot be identical.
            size_groups = {}
            unreadable = []
            for item in all_rows:
                size = item.get("size")
                if not isinstance(size, int):
                    continue
                size_groups.setdefault(size, []).append(item)
            candidates = [group for group in size_groups.values() if len(group) > 1]
            digest_groups = {}
            for group in candidates:
                for item in group:
                    try:
                        digest = hashlib.sha256()
                        with open(item["path"], "rb") as source:
                            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                                digest.update(chunk)
                        digest_groups.setdefault((item["size"], digest.hexdigest()), []).append(item)
                    except OSError as exc:
                        unreadable.append(f"{item['file']}: {exc}")
            duplicates = [group for group in digest_groups.values() if len(group) > 1]
            result_dialog = QDialog(dialog)
            result_dialog.setWindowTitle("Identische Build-Logs" if is_de else "Identical build logs")
            result_dialog.resize(800, 500)
            result_layout = QVBoxLayout(result_dialog)
            result_text = QTextEdit(result_dialog)
            result_text.setReadOnly(True)
            lines = []
            if duplicates:
                for number, group in enumerate(duplicates, 1):
                    lines.append((f"Gruppe {number} – identischer Inhalt ({group[0]['size']} Bytes):" if is_de else
                                  f"Group {number} – identical content ({group[0]['size']} bytes):"))
                    lines.extend("  • " + entry["path"] for entry in group)
                    lines.append("")
            else:
                lines.append("Keine byte-identischen Logs gefunden." if is_de else "No byte-identical logs found.")
            if unreadable:
                lines.extend(["", "Nicht lesbar:" if is_de else "Unreadable:", *unreadable])
            result_text.setPlainText("\n".join(lines))
            result_layout.addWidget(result_text, 1)
            close_result = QPushButton("Schließen" if is_de else "Close")
            close_result.clicked.connect(result_dialog.accept)
            result_layout.addWidget(close_result)
            result_dialog.exec()

        def show_build_summary():
            """Show a compact read-only health summary for the currently scanned logs."""
            summary_dialog = QDialog(dialog)
            summary_dialog.setWindowTitle("Build-Übersicht" if is_de else "Build summary")
            summary_dialog.resize(760, 520)
            summary_layout = QVBoxLayout(summary_dialog)
            summary_text = QTextEdit(summary_dialog)
            summary_text.setReadOnly(True)
            summary_layout.addWidget(summary_text, 1)

            rows = list(all_rows)
            total = len(rows)
            successful = sum("erfolgreich" in str(x.get("status", "")).casefold() or "successful" in str(x.get("status", "")).casefold() for x in rows)
            failed = sum("fehlgeschlagen" in str(x.get("status", "")).casefold() or "failed" in str(x.get("status", "")).casefold() for x in rows)
            started = sum("gestartet" in str(x.get("status", "")).casefold() or "started" in str(x.get("status", "")).casefold() for x in rows)
            unknown = sum("unbekannt" in str(x.get("status", "")).casefold() or "unknown" in str(x.get("status", "")).casefold() for x in rows)
            unreadable = sum("lesefehler" in str(x.get("status", "")).casefold() or "read error" in str(x.get("status", "")).casefold() for x in rows)
            try:
                errors = sum(int(x.get("errors", 0) or 0) for x in rows)
                warnings = sum(int(x.get("warnings", 0) or 0) for x in rows)
            except (TypeError, ValueError):
                errors = warnings = 0
            resolved = successful + failed
            success_rate = (successful / resolved * 100.0) if resolved else None
            lines = [
                "BUILD-ÜBERSICHT" if is_de else "BUILD SUMMARY",
                "=" * 54,
                (f"Logs gesamt: {total}" if is_de else f"Total logs: {total}"),
                (f"Erfolgreich: {successful}" if is_de else f"Successful: {successful}"),
                (f"Fehlgeschlagen: {failed}" if is_de else f"Failed: {failed}"),
                (f"Gestartet / ohne Abschlussmarker: {started}" if is_de else f"Started / missing completion marker: {started}"),
                (f"Unbekannter Status: {unknown}" if is_de else f"Unknown status: {unknown}"),
                (f"Lesefehler: {unreadable}" if is_de else f"Read errors: {unreadable}"),
                (f"Erkannte Fehler-Treffer insgesamt: {errors}" if is_de else f"Detected error matches: {errors}"),
                (f"Erkannte Warnungs-Treffer insgesamt: {warnings}" if is_de else f"Detected warning matches: {warnings}"),
                (f"Erfolgsquote bei abgeschlossenen Builds: {success_rate:.1f}%" if is_de else f"Success rate for completed builds: {success_rate:.1f}%") if success_rate is not None else ("Erfolgsquote: nicht berechenbar (keine abgeschlossenen Builds)" if is_de else "Success rate: unavailable (no completed builds)"),
                "",
                ("Neueste Logs:" if is_de else "Most recent logs:"),
            ]
            for item in rows[:10]:
                lines.append(f"• {item.get('modified', '')} | {item.get('status', '')} | {item.get('file', '')}")
            if not rows:
                lines.append("Keine Logs gefunden." if is_de else "No logs found.")
            lines.extend(["", ("Hinweis: Fehler- und Warnungszahlen beruhen auf Textmustern; sie sind keine vollständige Compilerdiagnose." if is_de else "Note: error and warning counts are based on text patterns; they are not a complete compiler diagnosis.")])
            summary_text.setPlainText("\n".join(lines))
            close_summary = QPushButton("Schließen" if is_de else "Close")
            close_summary.clicked.connect(summary_dialog.accept)
            summary_layout.addWidget(close_summary)
            summary_dialog.exec()

        def check_log_integrity():
            """Perform read-only sanity checks on available log files."""
            result_dialog = QDialog(dialog)
            result_dialog.setWindowTitle("Log-Prüfung" if is_de else "Log integrity check")
            result_dialog.resize(900, 560)
            result_layout = QVBoxLayout(result_dialog)
            result_text = QTextEdit(result_dialog)
            result_text.setReadOnly(True)
            result_layout.addWidget(result_text, 1)
            findings = []
            checked = 0
            max_files = 500
            max_bytes = 8 * 1024 * 1024
            for item in all_rows[:max_files]:
                path = item.get("path", "")
                name = item.get("file", os.path.basename(path))
                issues = []
                try:
                    size = os.path.getsize(path)
                    if size == 0:
                        issues.append("Leere Datei" if is_de else "Empty file")
                    if size > max_bytes:
                        issues.append((f"Große Datei ({size} Bytes; Inhalt nur teilweise geprüft)" if is_de else
                                       f"Large file ({size} bytes; content only partially checked)"))
                    with open(path, "rb") as fh:
                        data = fh.read(max_bytes + 1)
                    truncated = len(data) > max_bytes
                    data = data[:max_bytes]
                    if data:
                        if not data.endswith((b"\n", b"\r")) and not truncated:
                            issues.append("Letzte Zeile ohne Zeilenumbruch" if is_de else "Last line has no line ending")
                        decoded = data.decode("utf-8", errors="replace")
                        if "\ufffd" in decoded:
                            issues.append("Ungültige UTF-8-Sequenzen möglich (kann auch andere Kodierung sein)" if is_de else
                                           "Possible invalid UTF-8 sequences (file may use another encoding)")
                        if "SIMPLEBUILD_STARTED" in decoded and not re.search(r"SIMPLEBUILD_FINISHED:exit=-?\d+", decoded, re.I):
                            issues.append("Startmarker ohne Abschlussmarker im geprüften Inhalt" if is_de else
                                           "Start marker without completion marker in checked content")
                        if truncated:
                            issues.append("Prüfung auf 8 MiB begrenzt" if is_de else "Check limited to 8 MiB")
                    checked += 1
                except OSError as exc:
                    issues.append(("Nicht lesbar: " if is_de else "Unreadable: ") + str(exc))
                if issues:
                    findings.append(name + "\n  " + "\n  ".join(issues) + "\n  " + path)
            if len(all_rows) > max_files:
                findings.append((f"Hinweis: nur die ersten {max_files} Logs geprüft." if is_de else
                                 f"Note: only the first {max_files} logs were checked."))
            header = (f"Geprüfte Logs: {checked}\nAuffällige Dateien: {len(findings)}\n\n" if is_de else
                      f"Logs checked: {checked}\nFiles with findings: {len(findings)}\n\n")
            if findings:
                result_text.setPlainText(header + "\n\n".join(findings))
            else:
                result_text.setPlainText(header + ("Keine Auffälligkeiten nach den einfachen Plausibilitätsprüfungen gefunden. Das bestätigt nicht die fachliche Richtigkeit des Logs." if is_de else
                                                    "No issues found by these basic sanity checks. This does not certify the log's correctness."))
            close_result = QPushButton("Schließen" if is_de else "Close")
            close_result.clicked.connect(result_dialog.accept)
            result_layout.addWidget(close_result)
            result_dialog.exec()

        # Arrange actions in compact, grouped rows instead of one very wide toolbar.
        action_area = QVBoxLayout()
        action_row1 = QHBoxLayout()
        action_row2 = QHBoxLayout()
        action_row3 = QHBoxLayout()
        refresh_button = QPushButton("Aktualisieren" if is_de else "Refresh")
        refresh_button.setToolTip("Log-Liste neu einlesen (F5)." if is_de else "Reload the log list (F5).")
        refresh_button.clicked.connect(lambda: safe_callback(refresh))
        search_input.textChanged.connect(lambda _text: safe_callback(render_rows))
        status_filter.currentTextChanged.connect(lambda _text: safe_callback(render_rows))
        issue_filter.currentTextChanged.connect(lambda _text: safe_callback(render_rows))
        table.cellDoubleClicked.connect(lambda row, column: safe_callback(preview_log, row, column))
        open_selected_button = QPushButton("Ausgewähltes Log öffnen" if is_de else "Open selected log")
        open_selected_button.setToolTip("Vorschau des ausgewählten Logs öffnen (Enter)." if is_de else "Open a preview of the selected log (Enter).")
        def open_selected_log():
            row = table.currentRow()
            if row >= 0:
                preview_log(row, 0)
            else:
                QMessageBox.information(dialog, "Hinweis" if is_de else "Notice",
                                        "Bitte zuerst ein Log auswählen." if is_de else "Please select a log first.")
        open_selected_button.clicked.connect(lambda: safe_callback(open_selected_log))

        def show_log_findings():
            """Show numbered error/warning lines from the selected log without modifying it."""
            row = table.currentRow()
            if row < 0 or row >= len(current_rows):
                QMessageBox.information(
                    dialog, "Hinweis" if is_de else "Notice",
                    "Bitte zuerst ein Log auswählen." if is_de else "Please select a log first."
                )
                return
            item = current_rows[row]
            findings_dialog = QDialog(dialog)
            findings_dialog.setWindowTitle(("Fehler- und Warnungsdetails: " if is_de else "Error and warning details: ") + item["file"])
            findings_dialog.resize(900, 560)
            findings_layout = QVBoxLayout(findings_dialog)
            findings_path = QLabel(item["path"])
            findings_path.setWordWrap(True)
            findings_layout.addWidget(findings_path)
            findings_text = QTextEdit(findings_dialog)
            findings_text.setReadOnly(True)
            findings_layout.addWidget(findings_text, 1)
            try:
                finding_re = re.compile(
                    r"error:|fatal error:|undefined reference|command not found|\bfailed\b|\bfailure\b|make\[[0-9]+\]: \*\*\*|\bwarning:|\bdeprecated\b",
                    re.I,
                )
                matches = []
                with open(item["path"], "r", encoding="utf-8", errors="replace") as log_file:
                    for line_number, line in enumerate(log_file, 1):
                        if finding_re.search(line):
                            matches.append(f"{line_number}: {line.rstrip()}" )
                            if len(matches) >= 500:
                                matches.append("… weitere Treffer ausgelassen …" if is_de else "… additional matches omitted …")
                                break
                if matches:
                    findings_text.setPlainText("\n".join(matches))
                else:
                    findings_text.setPlainText("Keine passenden Fehler- oder Warnungszeilen gefunden." if is_de else "No matching error or warning lines found.")
            except OSError as exc:
                findings_text.setPlainText(("Log konnte nicht gelesen werden: " if is_de else "Could not read log: ") + str(exc))
            close_findings = QPushButton("Schließen" if is_de else "Close")
            close_findings.clicked.connect(findings_dialog.accept)
            findings_layout.addWidget(close_findings)
            findings_dialog.exec()

        def save_log_analysis_report():
            """Export a self-contained, read-only analysis report for the selected log."""
            row = table.currentRow()
            if row < 0 or row >= len(current_rows):
                QMessageBox.information(
                    dialog, "Hinweis" if is_de else "Notice",
                    "Bitte zuerst ein Log auswählen." if is_de else "Please select a log first."
                )
                return
            item = current_rows[row]
            default_name = os.path.splitext(os.path.basename(item["file"]))[0] + "_analysis.txt"
            target, _ = QFileDialog.getSaveFileName(
                dialog,
                "Analysebericht speichern" if is_de else "Save analysis report",
                default_name,
                ("Textdatei (*.txt)" if is_de else "Text file (*.txt)")
            )
            if not target:
                return
            finding_re = re.compile(
                r"error:|fatal error:|undefined reference|command not found|\bfailed\b|\bfailure\b|make\[[0-9]+\]: \*\*\*|\bwarning:|\bdeprecated\b",
                re.I,
            )
            matches = []
            try:
                with open(item["path"], "r", encoding="utf-8", errors="replace") as log_file:
                    for line_number, line in enumerate(log_file, 1):
                        if finding_re.search(line):
                            matches.append(f"{line_number}: {line.rstrip()}")
                            if len(matches) >= 500:
                                matches.append("… weitere Treffer ausgelassen …" if is_de else "… additional matches omitted …")
                                break
                report_lines = [
                    "BUILD-LOG-ANALYSEBERICHT" if is_de else "BUILD LOG ANALYSIS REPORT",
                    "=" * 72,
                    ("Datei: " if is_de else "File: ") + str(item["file"]),
                    ("Pfad: " if is_de else "Path: ") + str(item["path"]),
                    ("Status: " if is_de else "Status: ") + str(item["status"]),
                    ("Geändert: " if is_de else "Modified: ") + str(item["modified"]),
                    ("Größe: " if is_de else "Size: ") + str(item["size"]),
                    ("Fehler-Treffer: " if is_de else "Error matches: ") + str(item["errors"]),
                    ("Warnungs-Treffer: " if is_de else "Warning matches: ") + str(item["warnings"]),
                    ("Erstellt: " if is_de else "Created: ") + datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %Z"),
                    "",
                    ("Fehler- und Warnungszeilen (max. 500):" if is_de else "Error and warning lines (max. 500):"),
                    "-" * 72,
                    *(matches or (["Keine passenden Zeilen gefunden."] if is_de else ["No matching lines found."])),
                ]
                # Exclusive creation prevents accidentally overwriting an existing report.
                with open(target, "x", encoding="utf-8-sig", newline="") as report_file:
                    report_file.write("\n".join(report_lines) + "\n")
                QMessageBox.information(
                    dialog, "Bericht gespeichert" if is_de else "Report saved",
                    ("Analysebericht gespeichert unter:\n" if is_de else "Analysis report saved to:\n") + target
                )
            except FileExistsError:
                QMessageBox.warning(
                    dialog, "Datei existiert" if is_de else "File exists",
                    "Die Datei existiert bereits. Bitte einen anderen Namen wählen." if is_de else
                    "The file already exists. Please choose another name."
                )
            except OSError as exc:
                QMessageBox.critical(
                    dialog, "Exportfehler" if is_de else "Export error",
                    ("Der Analysebericht konnte nicht gespeichert werden:\n" if is_de else "Could not save analysis report:\n") + str(exc)
                )

        def export_filtered_error_digest():
            """Export a compact read-only digest for the currently filtered logs."""
            rows = list(current_rows[:100])
            if not rows:
                QMessageBox.information(
                    dialog, "Keine Logs" if is_de else "No logs",
                    "Die aktuelle Auswahl enthält keine Logs." if is_de else "The current selection contains no logs."
                )
                return
            target, _ = QFileDialog.getSaveFileName(
                dialog,
                "Fehlerübersicht speichern" if is_de else "Save error digest",
                "build_error_digest.txt",
                ("Textdatei (*.txt)" if is_de else "Text file (*.txt)")
            )
            if not target:
                return
            finding_re = re.compile(
                r"error:|fatal error:|undefined reference|command not found|\bfailed\b|\bfailure\b|make\[[0-9]+\]: \*\*\*|\bwarning:|\bdeprecated\b",
                re.I,
            )
            report = [
                "BUILD-LOG-FEHLERÜBERSICHT" if is_de else "BUILD LOG ERROR DIGEST",
                "=" * 72,
                ("Logs in aktueller Filterauswahl: " if is_de else "Logs in current filtered selection: ") + str(len(rows)),
                ("Erstellt: " if is_de else "Generated: ") + datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %Z"),
                ("Hinweis: Musterbasierte Erkennung; Treffer sind keine vollständige Compilerdiagnose." if is_de else "Note: pattern-based detection; matches are not a complete compiler diagnosis."),
                "",
            ]
            for index, item in enumerate(rows, 1):
                report.extend([
                    f"[{index}] {item.get('file', '')}",
                    ("Pfad: " if is_de else "Path: ") + str(item.get("path", "")),
                    ("Status: " if is_de else "Status: ") + str(item.get("status", "")),
                    ("Fehler: " if is_de else "Errors: ") + str(item.get("errors", "")) + " | " + ("Warnungen: " if is_de else "Warnings: ") + str(item.get("warnings", "")),
                ])
                matched = []
                try:
                    with open(item["path"], "r", encoding="utf-8", errors="replace") as log_file:
                        for line_number, line in enumerate(log_file, 1):
                            if finding_re.search(line):
                                matched.append(f"  {line_number}: {line.rstrip()}")
                                if len(matched) >= 50:
                                    matched.append("  … weitere Treffer ausgelassen …" if is_de else "  … additional matches omitted …")
                                    break
                except (OSError, KeyError) as exc:
                    matched.append(("  Log nicht lesbar: " if is_de else "  Log unreadable: ") + str(exc))
                report.extend(matched or (["  Keine passenden Fehler-/Warnungszeilen gefunden."] if is_de else ["  No matching error/warning lines found."]))
                report.append("")
            try:
                # Exclusive creation avoids overwriting an existing report.
                with open(target, "x", encoding="utf-8-sig", newline="") as output:
                    output.write("\n".join(report) + "\n")
                QMessageBox.information(
                    dialog, "Export abgeschlossen" if is_de else "Export complete",
                    ("Fehlerübersicht gespeichert:\n" if is_de else "Error digest saved:\n") + target
                )
            except FileExistsError:
                QMessageBox.warning(
                    dialog, "Datei existiert" if is_de else "File exists",
                    "Die Datei existiert bereits. Bitte einen anderen Namen wählen." if is_de else "The file already exists. Please choose another name."
                )
            except OSError as exc:
                QMessageBox.critical(
                    dialog, "Exportfehler" if is_de else "Export error",
                    ("Fehlerübersicht konnte nicht gespeichert werden:\n" if is_de else "Could not save error digest:\n") + str(exc)
                )

        summary_button = QPushButton("Build-Übersicht" if is_de else "Build summary")
        summary_button.setToolTip("Zusammenfassung aller gefundenen Logs anzeigen." if is_de else "Show a summary of all discovered logs.")
        summary_button.clicked.connect(lambda: safe_callback(show_build_summary))
        integrity_button = QPushButton("Logs prüfen" if is_de else "Check logs")
        integrity_button.setToolTip("Prüft einfache Auffälligkeiten, ohne Dateien zu verändern." if is_de else "Checks basic log anomalies without modifying files.")
        integrity_button.clicked.connect(lambda: safe_callback(check_log_integrity))
        duplicates_button = QPushButton("Duplikate prüfen" if is_de else "Find duplicates")
        duplicates_button.setToolTip("Byte-identische Logs anhand SHA-256 finden; Dateien bleiben unverändert." if is_de else "Find byte-identical logs using SHA-256; files remain unchanged.")
        duplicates_button.clicked.connect(lambda: safe_callback(find_duplicate_logs))
        compare_button = QPushButton("Logs vergleichen" if is_de else "Compare logs")
        compare_button.setToolTip("Genau zwei Logs auswählen und Unterschiede anzeigen." if is_de else "Select exactly two logs to view their differences.")
        compare_button.clicked.connect(lambda: safe_callback(compare_selected_logs))
        findings_button = QPushButton("Fehler/Warnungen" if is_de else "Errors/Warnings")
        findings_button.setToolTip("Erkannte Fehler- und Warnungszeilen des ausgewählten Logs anzeigen." if is_de else "Show detected error and warning lines from the selected log.")
        findings_button.clicked.connect(lambda: safe_callback(show_log_findings))
        report_button = QPushButton("Analysebericht speichern…" if is_de else "Save analysis report…")
        report_button.setToolTip("Einen Textbericht zum ausgewählten Log speichern." if is_de else "Save a text report for the selected log.")
        report_button.clicked.connect(lambda: safe_callback(save_log_analysis_report))
        digest_button = QPushButton("Fehlerübersicht exportieren…" if is_de else "Export error digest…")
        digest_button.setToolTip("Fehler-/Warnungstreffer der aktuell gefilterten Logs gesammelt exportieren." if is_de else "Export error/warning matches for the currently filtered logs in one report.")
        digest_button.clicked.connect(lambda: safe_callback(export_filtered_error_digest))
        live_button = QPushButton("Live-Aktualisierung: AUS" if is_de else "Live refresh: OFF")
        live_button.setToolTip("Automatische Aktualisierung alle 10 Sekunden ein-/ausschalten." if is_de else "Toggle automatic refresh every 10 seconds.")
        live_timer = QTimer(dialog)
        live_timer.setInterval(10000)
        live_timer.timeout.connect(lambda: safe_callback(refresh))
        live_enabled = {"value": False}
        def toggle_live_refresh():
            live_enabled["value"] = not live_enabled["value"]
            if live_enabled["value"]:
                live_timer.start()
            else:
                live_timer.stop()
            live_button.setText(("Live-Aktualisierung: AN" if live_enabled["value"] else "Live-Aktualisierung: AUS") if is_de
                                else ("Live refresh: ON" if live_enabled["value"] else "Live refresh: OFF"))
        live_button.clicked.connect(lambda: safe_callback(toggle_live_refresh))
        export_button = QPushButton("CSV exportieren…" if is_de else "Export CSV…")
        export_button.setToolTip("Aktuell gefilterte Logs als CSV speichern." if is_de else "Save the currently filtered logs as CSV.")
        export_button.clicked.connect(lambda: safe_callback(export_csv))
        json_export_button = QPushButton("JSON exportieren…" if is_de else "Export JSON…")
        json_export_button.setToolTip(
            "Aktuell gefilterte Logs als JSON exportieren, ohne vorhandene Dateien zu überschreiben."
            if is_de else
            "Export currently filtered logs as JSON without overwriting existing files."
        )
        json_export_button.clicked.connect(lambda: safe_callback(export_json))
        close_button = QPushButton("Schließen" if is_de else "Close")
        close_button.setToolTip("Build-Log-Verlauf schließen." if is_de else "Close build log history.")
        close_button.clicked.connect(dialog.accept)
        dialog.finished.connect(live_timer.stop)
        # Keyboard shortcuts speed up navigation without adding more toolbar buttons.
        try:
            from PyQt6.QtGui import QKeySequence, QShortcut
            refresh_shortcut = QShortcut(QKeySequence("F5"), dialog)
            refresh_shortcut.activated.connect(lambda: safe_callback(refresh))
            find_shortcut = QShortcut(QKeySequence("Ctrl+F"), dialog)
            find_shortcut.activated.connect(search_input.setFocus)
            open_shortcut = QShortcut(QKeySequence("Return"), table)
            open_shortcut.activated.connect(lambda: safe_callback(open_selected_log))
            copy_path_shortcut = QShortcut(QKeySequence("Ctrl+C"), table)
            def copy_selected_log_paths():
                selected = sorted({index.row() for index in table.selectionModel().selectedRows()})
                if not selected:
                    row = table.currentRow()
                    selected = [row] if 0 <= row < len(current_rows) else []
                paths = [str(current_rows[index].get("path", "")) for index in selected
                         if 0 <= index < len(current_rows)]
                if paths:
                    QApplication.clipboard().setText("\n".join(paths))
            copy_path_shortcut.activated.connect(lambda: safe_callback(copy_selected_log_paths))
            table.setToolTip(("F5: Aktualisieren · Strg+F: Suche · Enter: Log öffnen · Strg+C: Pfad kopieren"
                              if is_de else "F5: Refresh · Ctrl+F: Search · Enter: Open log · Ctrl+C: Copy path"))
        except Exception:
            pass
        for button in (refresh_button, open_selected_button, compare_button, summary_button):
            button.setMinimumWidth(105)
            button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            action_row1.addWidget(button, 1)
        for button in (integrity_button, duplicates_button, findings_button, report_button):
            button.setMinimumWidth(105)
            button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            action_row2.addWidget(button, 1)
        for button in (digest_button, live_button, export_button, json_export_button, close_button):
            button.setMinimumWidth(105)
            button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            action_row3.addWidget(button, 1)
        action_area.addLayout(action_row1)
        action_area.addLayout(action_row2)
        action_area.addLayout(action_row3)
        layout.addLayout(action_area)
        safe_callback(refresh)
        try:
            dialog.exec()
        except Exception as exc:
            QMessageBox.critical(self, "Fehler" if is_de else "Error",
                                 ("Build-Log-Verlauf wurde sicher beendet; die Haupt-GUI bleibt geöffnet.\n" if is_de
                                  else "Build log history was stopped safely; the main GUI remains open.\n") + f"{type(exc).__name__}: {exc}")
        finally:
            live_timer.stop()

    def open_event_history_viewer(self):
        """Zeigt gespeicherte Warnungen, Fehler und Erfolgsmeldungen an."""
        import json
        from pathlib import Path
        from PyQt6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel,
                                     QLineEdit, QComboBox, QPlainTextEdit,
                                     QPushButton, QFileDialog, QMessageBox)
        is_de = str(getattr(self, "LANG", "de")).lower().startswith("de")
        dialog = QDialog(self)
        dialog.setWindowTitle("Ereignis- und Fehlerhistorie" if is_de else "Event and error history")
        dialog.resize(850, 560)
        dialog.setMinimumSize(600, 380)
        layout = QVBoxLayout(dialog)
        filters = QHBoxLayout()
        filters.addWidget(QLabel("Filter:" if is_de else "Level:"))
        level = QComboBox()
        level.addItem("Alle" if is_de else "All", "all")
        level.addItem("Fehler" if is_de else "Errors", "error")
        level.addItem("Warnungen" if is_de else "Warnings", "warning")
        level.addItem("Erfolge" if is_de else "Successes", "success")
        filters.addWidget(level)
        search = QLineEdit()
        search.setPlaceholderText("Text suchen…" if is_de else "Search text…")
        filters.addWidget(search, 1)
        layout.addLayout(filters)
        output = QPlainTextEdit()
        output.setReadOnly(True)
        output.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        layout.addWidget(output, 1)
        status = QLabel()
        layout.addWidget(status)
        event_dir = Path(__file__).resolve().parent / "event_history"
        files = [event_dir / "events.previous.jsonl", event_dir / "events.jsonl"]
        records = []
        for file_path in files:
            try:
                with file_path.open("r", encoding="utf-8") as handle:
                    for line in handle:
                        try:
                            item = json.loads(line)
                            if isinstance(item, dict):
                                records.append(item)
                        except (json.JSONDecodeError, TypeError):
                            continue
            except OSError:
                continue
        records.sort(key=lambda item: str(item.get("timestamp", "")), reverse=True)
        visible_text = []
        def refresh_events(*_):
            selected = level.currentData()
            needle = search.text().strip().casefold()
            visible_text.clear()
            for item in records:
                item_level = str(item.get("level", "info")).lower()
                message = str(item.get("message", ""))
                timestamp = str(item.get("timestamp", ""))
                if selected != "all" and item_level != selected:
                    continue
                if needle and needle not in (timestamp + " " + message).casefold():
                    continue
                visible_text.append(f"[{timestamp}] [{item_level.upper()}] {message}")
            output.setPlainText("\n".join(visible_text))
            status.setText((f"{len(visible_text)} Einträge angezeigt · {len(records)} insgesamt" if is_de else
                            f"Showing {len(visible_text)} entries · {len(records)} total"))
        level.currentIndexChanged.connect(refresh_events)
        search.textChanged.connect(refresh_events)
        buttons = QHBoxLayout()
        export_button = QPushButton("Gefiltert exportieren…" if is_de else "Export filtered…")
        close_button = QPushButton("Schließen" if is_de else "Close")
        def export_events():
            path, _ = QFileDialog.getSaveFileName(dialog,
                "Ereignisse exportieren" if is_de else "Export events",
                "ereignisse.txt" if is_de else "events.txt", "Text (*.txt)")
            if not path:
                return
            try:
                with open(path, "x", encoding="utf-8") as handle:
                    handle.write("\n".join(visible_text))
            except FileExistsError:
                QMessageBox.warning(dialog, "Datei existiert" if is_de else "File exists",
                                    "Die Datei existiert bereits." if is_de else "That file already exists.")
            except OSError as exc:
                QMessageBox.warning(dialog, "Exportfehler" if is_de else "Export error", str(exc))
        export_button.clicked.connect(export_events)
        close_button.clicked.connect(dialog.accept)
        buttons.addWidget(export_button)
        buttons.addStretch(1)
        buttons.addWidget(close_button)
        layout.addLayout(buttons)
        refresh_events()
        dialog.exec()

    def open_diagnostics_dialog(self):
        """Read-only system/tool/build-log diagnostics; never starts or cancels builds."""
        import json
        import os
        import platform
        import shutil
        from datetime import datetime
        from PyQt6.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QTextEdit, QFileDialog, QMessageBox

        is_de = str(getattr(self, "LANG", LANG)).lower().startswith("de")
        dialog = QDialog(self)
        dialog.setWindowTitle("Systemdiagnose & Build-Logs" if is_de else "System diagnostics & build logs")
        dialog.resize(900, 620)
        layout = QVBoxLayout(dialog)
        intro = QLabel(
            "Nur lesende Prüfung. Es wird kein Build gestartet/abgebrochen und es werden keine Logs gelöscht."
            if is_de else
            "Read-only checks. No build is started/cancelled and no logs are deleted."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)
        summary = QLabel("Diagnose wird vorbereitet …" if is_de else "Preparing diagnostics …")
        summary.setWordWrap(True)
        summary.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        summary.setStyleSheet("QLabel { padding: 8px; border: 1px solid palette(mid); border-radius: 4px; }")
        layout.addWidget(summary)
        output = QTextEdit()
        output.setReadOnly(True)
        output.setFont(QFont("Consolas", 10))
        layout.addWidget(output, 1)

        def collect_report():
            report = {"created_at": datetime.now().astimezone().isoformat(), "system": {}, "tools": {}, "build_logs": [], "process_monitor": {}}
            tool_paths = {
                "git": shutil.which("git"),
                "bash": shutil.which("bash"),
                "wsl": shutil.which("wsl") or shutil.which("wsl.exe"),
                "make": shutil.which("make") or shutil.which("mingw32-make"),
                "gcc": shutil.which("gcc"),
                "g++": shutil.which("g++"),
                "clang": shutil.which("clang"),
                "python": shutil.which("python") or shutil.which("python3"),
            }
            report["system"] = {
                "platform": platform.platform(),
                "system": platform.system(),
                "release": platform.release(),
                "python": sys.version,
                "executable": sys.executable,
                "cwd": os.getcwd(),
                "tools_on_path": tool_paths,
                "available_tool_count": sum(bool(value) for value in tool_paths.values()),
                "missing_common_tools": [name for name, value in tool_paths.items() if value is None],
            }
            # Read-only process snapshot. A compiler process alone is only an indicator,
            # not proof that it belongs to this application or one of its builds.
            process_candidates = []
            process_errors = []
            process_keywords = ("make", "gcc", "g++", "cc1", "clang", "cmake", "ninja", "ld", "simplebuild", "build.sh", "arm-linux")
            try:
                import psutil  # Optional dependency; never install it automatically.
                for proc in psutil.process_iter(["pid", "name", "exe"]):
                    try:
                        name = str(proc.info.get("name") or "")
                        exe = str(proc.info.get("exe") or "")
                        haystack = (name + " " + exe).lower()
                        if any(keyword in haystack for keyword in process_keywords):
                            process_candidates.append({"pid": proc.info.get("pid"), "name": name, "executable": exe})
                    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                        continue
            except ImportError:
                # Standard-library fallback, keeping only matching process rows.
                try:
                    if platform.system().lower() == "windows":
                        result = subprocess.run(["tasklist", "/FO", "CSV", "/NH"], capture_output=True, text=True, timeout=5, check=False)
                        import csv
                        import io
                        for row in csv.reader(io.StringIO(result.stdout or "")):
                            if len(row) >= 2 and any(k in row[0].lower() for k in process_keywords):
                                process_candidates.append({"pid": row[1], "name": row[0], "executable": ""})
                        if result.returncode and not result.stdout:
                            process_errors.append((result.stderr or "tasklist failed")[:300])
                    else:
                        result = subprocess.run(["ps", "-eo", "pid=,comm="], capture_output=True, text=True, timeout=5, check=False)
                        for line in (result.stdout or "").splitlines():
                            parts = line.strip().split(None, 1)
                            if len(parts) == 2 and any(k in parts[1].lower() for k in process_keywords):
                                process_candidates.append({"pid": parts[0], "name": parts[1], "executable": ""})
                        if result.returncode and not result.stdout:
                            process_errors.append((result.stderr or "ps failed")[:300])
                except (OSError, subprocess.SubprocessError) as exc:
                    process_errors.append(str(exc)[:300])
            # Track workers owned by this GUI separately from system-wide process matches.
            tracked_workers = []
            worker_attributes = (
                ("s3_install_worker", "S3 installation" if not is_de else "S3-Installation"),
                ("s3_worker", "S3 build" if not is_de else "S3-Build"),
                ("ncam_worker", "NCam installation" if not is_de else "NCam-Installation"),
                ("ncam_update_worker", "NCam update" if not is_de else "NCam-Update"),
            )
            for attr_name, display_name in worker_attributes:
                worker = getattr(self, attr_name, None)
                if worker is None:
                    continue
                try:
                    running = bool(worker.isRunning()) if callable(getattr(worker, "isRunning", None)) else False
                    finished = bool(worker.isFinished()) if callable(getattr(worker, "isFinished", None)) else not running
                    tracked_workers.append({
                        "name": display_name,
                        "state": "RUNNING" if running else "FINISHED" if finished else "NOT RUNNING / UNKNOWN",
                    })
                except RuntimeError as exc:
                    tracked_workers.append({"name": display_name, "state": "STATUS UNKNOWN", "detail": str(exc)[:200]})

            if any(item.get("state") == "RUNNING" for item in tracked_workers):
                overall_status = "GUI BUILD WORKER RUNNING" if not is_de else "GUI-BUILD-WORKER LÄUFT"
            elif process_errors and not process_candidates:
                overall_status = "STATUS UNKNOWN" if not is_de else "STATUS UNBEKANNT"
            elif process_candidates:
                overall_status = "SYSTEM PROCESS CANDIDATES FOUND; NOT ATTRIBUTED TO GUI" if not is_de else "SYSTEM-PROZESS-KANDIDATEN GEFUNDEN; GUI-ZUORDNUNG UNBEKANNT"
            else:
                overall_status = "NO TRACKED GUI BUILD RUNNING" if not is_de else "KEIN VERFOLGTER GUI-BUILD AKTIV"

            report["process_monitor"] = {
                "status": overall_status,
                "tracked_gui_workers": tracked_workers,
                "confidence": "GUI worker state is application-specific; system process matches are indicators only, not proof." if not is_de else "GUI-Workerstatus ist anwendungsspezifisch; gefundene Systemprozesse sind nur Hinweise, kein Beweis.",
                "system_process_candidates": process_candidates[:100],
                "truncated": len(process_candidates) > 100,
                "errors": process_errors,
            }

            # Inspect only directories already present. Do not clone, fetch, checkout or modify Git.
            candidate_roots = []
            for value in (os.environ.get("SIMPLEBUILD_DIR"), r"C:\Simplebuild", os.path.dirname(os.path.abspath(__file__))):
                if value and value not in candidate_roots:
                    candidate_roots.append(value)
            for tool in ("s3_gorgone", "s3_speedy005", "s4", "ncam"):
                matches = []
                for root in candidate_roots:
                    for candidate in (os.path.join(root, tool), root if os.path.basename(root).lower() == tool.lower() else None):
                        if candidate and os.path.isdir(candidate):
                            matches.append({"path": candidate, "git_dir": os.path.exists(os.path.join(candidate, ".git"))})
                report["tools"][tool] = {"found": bool(matches), "locations": matches}
            log_dir = getattr(self, "build_log_dir", None) or os.path.join(os.path.dirname(os.path.abspath(__file__)), "build_logs")
            report["log_directory"] = log_dir
            # Actionable hints are advisory only; diagnostics never modify the system.
            recommendations = []
            missing_tools = [name for name, value in tool_paths.items() if value is None]
            if "git" in missing_tools:
                recommendations.append({
                    "severity": "warning",
                    "code": "GIT_NOT_FOUND",
                    "message": "Git wurde nicht im PATH gefunden. Installation und PATH prüfen." if is_de else "Git was not found on PATH. Check the installation and PATH.",
                })
            if not any(os.path.isdir(os.path.join(root, name)) for root in candidate_roots for name in ("s3_gorgone", "s3_speedy005", "s4", "ncam")):
                recommendations.append({
                    "severity": "warning",
                    "code": "PROJECT_DIRS_NOT_FOUND",
                    "message": "Keiner der erwarteten Projektordner wurde an den geprüften Orten gefunden. Projektpfad/SIMPLEBUILD_DIR prüfen." if is_de else "No expected project folders were found in checked locations. Check the project path/SIMPLEBUILD_DIR.",
                })
            if not os.path.isdir(log_dir):
                recommendations.append({
                    "severity": "info",
                    "code": "LOG_DIR_MISSING",
                    "message": "Der Build-Log-Ordner existiert noch nicht oder liegt an einem anderen Ort." if is_de else "The build-log directory does not exist yet or is located elsewhere.",
                })
            failed_logs = [item.get("name", "") for item in report.get("build_logs", []) if "FAILED" in str(item.get("status", "")).upper() or "FEHLGESCHLAGEN" in str(item.get("status", "")).upper() or "ERROR" in str(item.get("status", "")).upper() or "FEHLER" in str(item.get("status", "")).upper()]
            if failed_logs:
                recommendations.append({
                    "severity": "warning",
                    "code": "BUILD_LOG_ERRORS",
                    "message": ("Fehlerindikatoren in Build-Logs prüfen: " if is_de else "Review error indicators in build logs: ") + ", ".join(failed_logs[:8]),
                })
            report["recommendations"] = recommendations
            try:
                for name in sorted(os.listdir(log_dir), key=lambda n: os.path.getmtime(os.path.join(log_dir, n)) if os.path.isfile(os.path.join(log_dir, n)) else 0, reverse=True):
                    path = os.path.join(log_dir, name)
                    if not os.path.isfile(path) or not name.lower().endswith(".log"):
                        continue
                    status = "UNKNOWN" if not is_de else "UNBEKANNT"
                    try:
                        # Read only the tail of large logs: diagnostics should stay
                        # responsive even when a build log is several hundred MB.
                        max_scan_bytes = 1024 * 1024
                        with open(path, "rb") as f:
                            f.seek(0, os.SEEK_END)
                            file_size = f.tell()
                            scan_size = min(file_size, max_scan_bytes)
                            f.seek(-scan_size, os.SEEK_END)
                            content = f.read(scan_size).decode("utf-8", errors="replace")
                        log_truncated_for_scan = file_size > max_scan_bytes
                        if "SIMPLEBUILD_FINISHED:exit=0" in content:
                            status = "SUCCESS (marker)" if not is_de else "ERFOLGREICH (Marker)"
                        elif "SIMPLEBUILD_FINISHED:exit=" in content:
                            status = "FAILED (marker)" if not is_de else "FEHLGESCHLAGEN (Marker)"
                        elif "SIMPLEBUILD_STARTED" in content:
                            status = "STARTED, completion marker missing" if not is_de else "GESTARTET, Abschlussmarker fehlt"
                        elif any(x in content.lower() for x in ("fatal error:", "undefined reference", "command not found", "make: ***")):
                            status = "ERROR INDICATORS FOUND" if not is_de else "FEHLERHINWEISE gefunden"
                        report["build_logs"].append({"name": name, "path": path, "status": status, "bytes": file_size, "scan_bytes": scan_size, "scan_truncated": log_truncated_for_scan, "modified": datetime.fromtimestamp(os.path.getmtime(path)).astimezone().isoformat()})
                    except OSError as exc:
                        report["build_logs"].append({"name": name, "path": path, "status": "READ ERROR" if not is_de else "LESEFEHLER", "error": str(exc)})
            except OSError as exc:
                report["log_scan_error"] = str(exc)
            return report

        def refresh():
            # A failed optional probe must not close the diagnostics window.
            try:
                report = collect_report()
            except Exception as exc:
                error_text = f"{type(exc).__name__}: {exc}"
                summary.setText(
                    ("Diagnose konnte nicht vollständig erstellt werden. Details stehen unten." if is_de else
                     "Diagnostics could not be completed. Details are shown below.")
                )
                output.setPlainText(error_text)
                return {"created_at": datetime.now().astimezone().isoformat(),
                        "diagnostic_error": error_text, "recommendations": []}
            system = report.get("system", {})
            missing = system.get("missing_common_tools", [])
            repos = report.get("tools", {})
            found_repos = [name for name, data in repos.items() if data.get("found")]
            logs = report.get("build_logs", [])
            running = [item.get("name", "worker") for item in report.get("process_monitor", {}).get("tracked_gui_workers", []) if item.get("state") == "RUNNING"]
            if is_de:
                summary_lines = [
                    f"Werkzeuge: {system.get('available_tool_count', 0)}/{len(system.get('tools_on_path', {}))} gefunden",
                    "Fehlende Werkzeuge: " + (", ".join(missing) if missing else "keine der geprüften Standardwerkzeuge"),
                    "Repository-/Projektordner gefunden: " + (", ".join(found_repos) if found_repos else "keine der geprüften Ordner"),
                    f"Build-Logs: {len(logs)} gefunden",
                    "Aktive GUI-Worker: " + (", ".join(running) if running else "keine erkannt"),
                    "Empfehlungen: " + (str(len(report.get("recommendations", []))) if report.get("recommendations") else "keine zusätzlichen Hinweise"),
                ]
            else:
                summary_lines = [
                    f"Tools: {system.get('available_tool_count', 0)}/{len(system.get('tools_on_path', {}))} found",
                    "Missing tools: " + (", ".join(missing) if missing else "none of the checked common tools"),
                    "Project/repository folders found: " + (", ".join(found_repos) if found_repos else "none of the checked folders"),
                    f"Build logs: {len(logs)} found",
                    "Active GUI workers: " + (", ".join(running) if running else "none detected"),
                    "Recommendations: " + (str(len(report.get("recommendations", []))) if report.get("recommendations") else "no additional hints"),
                ]
            summary.setText("\n".join(summary_lines))
            output.setPlainText(json.dumps(report, ensure_ascii=False, indent=2))
            return report

        def save_report():
            try:
                report = collect_report()
            except Exception as exc:
                QMessageBox.warning(
                    dialog, "Diagnosefehler" if is_de else "Diagnostic error",
                    ("Der Bericht konnte nicht erstellt werden: " if is_de else
                     "The report could not be generated: ") + f"{type(exc).__name__}: {exc}"
                )
                return
            suggested = f"simplebuild_diagnose_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
            path, _ = QFileDialog.getSaveFileName(dialog, "Diagnosebericht speichern" if is_de else "Save diagnostic report", suggested, "JSON (*.json)")
            if not path:
                return
            try:
                # Exclusive create avoids overwriting an existing report.
                with open(path, "x", encoding="utf-8") as handle:
                    json.dump(report, handle, ensure_ascii=False, indent=2)
                QMessageBox.information(dialog, "Gespeichert" if is_de else "Saved", path)
            except FileExistsError:
                QMessageBox.warning(dialog, "Datei existiert" if is_de else "File exists", "Die Datei existiert bereits; bitte einen anderen Namen wählen." if is_de else "That file already exists; choose another name.")
            except OSError as exc:
                QMessageBox.critical(dialog, "Fehler" if is_de else "Error", str(exc))

        buttons = QHBoxLayout()
        refresh_button = QPushButton("Jetzt aktualisieren" if is_de else "Refresh now")
        refresh_button.setToolTip("System, Werkzeuge und Build-Logs erneut lesend prüfen." if is_de else "Re-check system, tools and build logs without modifying them.")
        refresh_button.clicked.connect(refresh)
        save_button = QPushButton("Bericht speichern…" if is_de else "Save report…")
        save_button.setToolTip("Aktuelle Diagnose als JSON-Datei exportieren." if is_de else "Export the current diagnostics as a JSON file.")
        save_button.clicked.connect(save_report)
        auto_refresh_button = QPushButton("Live-Aktualisierung: AUS" if is_de else "Live refresh: OFF")
        auto_refresh_button.setCheckable(True)
        auto_refresh_button.setToolTip(
            "Aktualisiert Diagnose und Log-Status alle 5 Sekunden. Startet oder beendet keine Builds."
            if is_de else
            "Refreshes diagnostics and log status every 5 seconds. Does not start or stop builds."
        )
        refresh_timer = QTimer(dialog)
        refresh_timer.setInterval(5000)
        refresh_timer.timeout.connect(refresh)

        def toggle_auto_refresh(enabled):
            if enabled:
                refresh_timer.start()
                auto_refresh_button.setText("Live-Aktualisierung: EIN" if is_de else "Live refresh: ON")
                refresh()
            else:
                refresh_timer.stop()
                auto_refresh_button.setText("Live-Aktualisierung: AUS" if is_de else "Live refresh: OFF")

        auto_refresh_button.toggled.connect(toggle_auto_refresh)
        close_button = QPushButton("Schließen" if is_de else "Close")
        close_button.setToolTip("Build-Log-Verlauf schließen." if is_de else "Close build log history.")
        close_button.clicked.connect(dialog.accept)
        event_history_button = QPushButton("Ereignishistorie…" if is_de else "Event history…")
        event_history_button.setToolTip("Gespeicherte Fehler, Warnungen und Erfolge filtern und exportieren." if is_de else "Filter and export saved errors, warnings and successes.")
        event_history_button.clicked.connect(self.open_event_history_viewer)
        buttons.addWidget(event_history_button)
        buttons.addWidget(refresh_button)
        buttons.addWidget(auto_refresh_button)
        buttons.addWidget(save_button)
        buttons.addStretch(1)
        buttons.addWidget(close_button)
        layout.addLayout(buttons)
        refresh()
        dialog.finished.connect(refresh_timer.stop)
        dialog.exec()


def auto_install_emoji_font():
    """Installiert Noto Color Emoji bei Bedarf unter Windows oder Linux."""

    def _run_install():
        os_type = platform.system()

        try:
            print("[SYSTEM] Prüfe Emoji-Schriftart...")

            if os_type == "Linux":
                font_dir = os.path.expanduser("~/.local/share/fonts")
                target_path = os.path.join(font_dir, "NotoColorEmoji.ttf")

                # Ist die Schriftart bereits installiert, nichts tun.
                if os.path.isfile(target_path) and os.path.getsize(target_path) > 0:
                    print("[SYSTEM] Emoji-Schriftart bereits vorhanden.")
                    return

            elif os_type == "Windows":
                font_dir = os.path.join(
                    os.environ.get("LOCALAPPDATA", ""),
                    "Microsoft", "Windows", "Fonts"
                )
                target_path = os.path.join(font_dir, "NotoColorEmoji.ttf")

                if os.path.isfile(target_path) and os.path.getsize(target_path) > 0:
                    print("[SYSTEM] Emoji-Schriftart bereits vorhanden.")
                    return
            else:
                print("[SYSTEM] Automatische Emoji-Installation nicht unterstützt.")
                return

            import requests
            import zipfile
            import io

            url = (
                "https://github.com/speedy005/"
                "oscam_patch_manager_new/raw/master/Noto_Color_Emoji.zip"
            )

            response = requests.get(url, timeout=20)
            response.raise_for_status()

            with zipfile.ZipFile(io.BytesIO(response.content)) as z:
                ttf_name = next(
                    (f for f in z.namelist() if f.lower().endswith(".ttf")),
                    None
                )

                if not ttf_name:
                    print("[!] Keine TTF-Datei im Emoji-Archiv gefunden.")
                    return

                font_data = z.read(ttf_name)

            os.makedirs(font_dir, exist_ok=True)

            with open(target_path, "wb") as f:
                f.write(font_data)

            if os_type == "Linux":
                subprocess.run(
                    ["fc-cache", "-f", font_dir],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=False
                )
                print("[✓] Emoji-Schriftart installiert. Neustart empfohlen.")

            elif os_type == "Windows":
                ctypes.windll.gdi32.AddFontResourceW(target_path)
                ctypes.windll.user32.SendMessageW(0xFFFF, 0x001D, 0, 0)
                print("[✓] Emoji-Schriftart installiert.")

        except Exception as e:
            print(f"[!] Emoji-Schriftart: {e}")

    threading.Thread(target=_run_install, daemon=True).start()





# =============================================================================
# EXECUTION BOOTLOADER: Oscam Emu Patch Manager by speedy005
# =============================================================================
