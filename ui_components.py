"""OSCam Patch Manager module fragment: Reusable Qt layout and stream components."""

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

class FlowLayout(QLayout):
    def __init__(self, parent=None, margin=0, spacing=5):
        super().__init__(parent)
        self.item_list = []
        self.setContentsMargins(margin, margin, margin, margin)
        self.setSpacing(spacing)

    def addItem(self, item):
        self.item_list.append(item)

    def addWidget(self, widget):
        self.addItem(QWidgetItem(widget))

    def count(self):
        return len(self.item_list)

    def itemAt(self, index):
        if 0 <= index < len(self.item_list):
            return self.item_list[index]
        return None

    def takeAt(self, index):
        if 0 <= index < len(self.item_list):
            return self.item_list.pop(index)
        return None

    def expandingDirections(self):
        return Qt.Orientation(0)

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        return self.do_layout(QRect(0, 0, width, 0), test_only=True)

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self.do_layout(rect, test_only=False)

    def sizeHint(self):
        return self.minimumSize()

    def minimumSize(self):
        size = QSize()
        for item in self.item_list:
            # Wir nehmen die echte MinimumSize des Widgets (deine 40px oder 60px)
            size = size.expandedTo(item.minimumSize())

        margins = self.contentsMargins()  # Korrigiert von getContentsMargins
        size += QSize(
            margins.left() + margins.right(), margins.top() + margins.bottom()
        )
        return size

    def do_layout(self, rect, test_only):
        x = rect.x()
        y = rect.y()
        line_height = 0
        space = self.spacing()

        for item in self.item_list:
            # FIX: Wir holen uns die Größe, aber lassen sie NICHT dynamisch ändern
            # Wenn der Button eine feste Höhe hat (setMinimumHeight), nutzen wir diese.
            widget_size = item.sizeHint()

            # Falls der Button im Klick-Zustand versucht zu wachsen,
            # nehmen wir stattdessen die Mindestgröße (die du im Code fixiert hast).
            if item.widget():
                w = item.widget()
                # Falls eine feste Breite/Höhe gesetzt wurde, nutzen wir diese strikt
                final_width = widget_size.width()
                final_height = widget_size.height()
                widget_size = QSize(final_width, final_height)

            next_x = x + widget_size.width() + space

            # Zeilenumbruch-Logik
            if next_x - space > rect.right() and line_height > 0:
                x = rect.x()
                y += line_height + space
                next_x = x + widget_size.width() + space
                line_height = 0

            if not test_only:
                # Hier wird die Geometrie SCHREIBGESCHÜTZT gesetzt.
                # Der Button hat keine Chance mehr, sich beim Klick auszudehnen.
                item.setGeometry(QRect(QPoint(x, y), widget_size))

            x = next_x
            line_height = max(line_height, widget_size.height())

        return y + line_height - rect.y()

def ensure_dir(directory):
    """
    Erstellt das Verzeichnis, falls es noch nicht existiert.
    Optimiert für Pfadsicherheit und Fehlerhandling.
    """
    if not directory:
        return

    if not os.path.exists(directory):
        try:
            # exist_ok=True verhindert Fehler, falls ein anderer Prozess
            # das Verzeichnis im selben Moment erstellt
            os.makedirs(directory, exist_ok=True)
            print(f"[INFO] Verzeichnis erstellt: {directory}")
        except OSError as e:
            # Spezielles Handling für Berechtigungsfehler (z.B. in /opt/s3)
            print(f"[ERROR] Zugriff verweigert oder Pfad ungültig: {directory} ({e})")
        except Exception as e:
            print(f"[ERROR] Unbekannter Fehler beim Erstellen von {directory}: {e}")

from PyQt6.QtCore import QObject, pyqtSignal

class StreamToGui(QObject):
    """Sichere Weiterleitung von stdout an die GUI mittels Signalen."""

    new_text = pyqtSignal(str)

    def __init__(self):
        super().__init__()

    def write(self, text):
        if text.strip():
            self.new_text.emit(str(text))

    def flush(self):
        pass

# ===================== NEVER_DELETE =====================
NEVER_DELETE = [
    "oscam_patch_manager.py",
    "clean_texts.py",
    "def.py",
    "extract_text_keys.py",
    "texts_cleaned.py",
    "oscam-emu-patch.sh",
    "github_upload_config.json",
    "oscam-patch.sh",
    "config.json",
    "check_tools.sh",
    "icons",
]

# ===================== COLORS =====================
