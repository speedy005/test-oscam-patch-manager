"""OSCam Patch Manager module fragment: Theme palettes and theme helpers."""

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

DIFF_COLORS = {
    "Acid": {
        "bg": "#1D1D1D",
        "fg": "#DFFF00",
        "hover": "#BFFF00",
        "active": "#000000",
        "window_bg": "#2F2F2F",
    },
    "Aero": {
        "bg": "#00B8D4",
        "fg": "#FFFFFF",
        "hover": "#00E5FF",
        "active": "#0091EA",
        "window_bg": "#2F2F2F",
    },
    "Afterglow": {
        "bg": "#2C2C2C",
        "fg": "#E57373",
        "hover": "#FF8A65",
        "active": "#D32F2F",
        "window_bg": "#2F2F2F",
    },
    "Alien": {
        "bg": "#00FF41",
        "fg": "#000000",
        "hover": "#008F11",
        "active": "#003B00",
        "window_bg": "#2F2F2F",
    },
    "Amethyst": {
        "bg": "#9C27B0",
        "fg": "#E1BEE7",
        "hover": "#BA68C8",
        "active": "#7B1FA2",
        "window_bg": "#2F2F2F",
    },
    "Anthrazit": {
        "bg": "#2F2F2F",
        "fg": "#FFFFFF",
        "hover": "#3D3D3D",
        "active": "#242424",
        "window_bg": "#2F2F2F",
    },
    "Arctic": {
        "bg": "#000000",
        "fg": "#00D2FF",
        "hover": "#0081FF",
        "active": "#00458B",
        "window_bg": "#2F2F2F",
    },
    "Asphalt": {
        "bg": "#263238",
        "fg": "#ECEFF1",
        "hover": "#37474F",
        "active": "#102027",
        "window_bg": "#2F2F2F",
    },
    "Atomic": {
        "bg": "#1A1A1A",
        "fg": "#7FFF00",
        "hover": "#32CD32",
        "active": "#006400",
        "window_bg": "#2F2F2F",
    },
    "Aurora": {
        "bg": "#004D40",
        "fg": "#80CBC4",
        "hover": "#00897B",
        "active": "#002420",
        "window_bg": "#2F2F2F",
    },
    "Biohazard": {
        "bg": "#003300",
        "fg": "#39FF14",
        "hover": "#00FF41",
        "active": "#001100",
        "window_bg": "#2F2F2F",
    },
    "Blackout": {
        "bg": "#000000",
        "fg": "#444444",
        "hover": "#222222",
        "active": "#111111",
        "window_bg": "#2F2F2F",
    },
    "Blaze": {
        "bg": "#E65100",
        "fg": "#FFCC80",
        "hover": "#EF6C00",
        "active": "#BF360C",
        "window_bg": "#2F2F2F",
    },
    "BloodMoon": {
        "bg": "#330000",
        "fg": "#FF0000",
        "hover": "#660000",
        "active": "#000000",
        "window_bg": "#2F2F2F",
    },
    "Bordeaux": {
        "bg": "#800000",
        "fg": "#FFFFFF",
        "hover": "#A52A2A",
        "active": "#5D0000",
        "window_bg": "#2F2F2F",
    },
    "Bubblegum": {
        "bg": "#F06292",
        "fg": "#F8BBD0",
        "hover": "#F48FB1",
        "active": "#C2185B",
        "window_bg": "#2F2F2F",
    },
    "Bumblebee": {
        "bg": "#FFCC00",
        "fg": "#000000",
        "hover": "#000000",
        "active": "#333300",
        "window_bg": "#2F2F2F",
    },
    "Candy": {
        "bg": "#FF80AB",
        "fg": "#FCE4EC",
        "hover": "#F06292",
        "active": "#C2185B",
        "window_bg": "#2F2F2F",
    },
    "Carbon": {
        "bg": "#232323",
        "fg": "#E0E0E0",
        "hover": "#111111",
        "active": "#000000",
        "window_bg": "#2F2F2F",
    },
    "Classics": {
        "bg": "#3a6ea5",
        "fg": "#FFFFFF",
        "hover": "#4a7eb5",
        "active": "#2a5e95",
        "window_bg": "#2F2F2F",
    },
    "Coffee": {
        "bg": "#4E342E",
        "fg": "#D7CCC8",
        "hover": "#5D4037",
        "active": "#3E2723",
        "window_bg": "#2F2F2F",
    },
    "Copper": {
        "bg": "#3E2723",
        "fg": "#D84315",
        "hover": "#BF360C",
        "active": "#260E04",
        "window_bg": "#2F2F2F",
    },
    "Cosmos": {
        "bg": "#130f40",
        "fg": "#f093fb",
        "hover": "#30336b",
        "active": "#130f40",
        "window_bg": "#2F2F2F",
    },
    "Crimson": {
        "bg": "#000000",
        "fg": "#DC143C",
        "hover": "#800000",
        "active": "#000000",
        "window_bg": "#2F2F2F",
    },
    "Cyberpunk": {
        "bg": "#000000",
        "fg": "#00FFFF",
        "hover": "#F305FF",
        "active": "#FF0055",
        "window_bg": "#2F2F2F",
    },
    "DeepBlack": {
        "bg": "#1A1A1A",
        "fg": "#FFD700",
        "hover": "#333333",
        "active": "#000000",
        "window_bg": "#2F2F2F",
    },
    "DeepSea": {
        "bg": "#001219",
        "fg": "#94D2BD",
        "hover": "#0A9396",
        "active": "#005F73",
        "window_bg": "#2F2F2F",
    },
    "DeepSpace": {
        "bg": "#0B0D17",
        "fg": "#00D4FF",
        "hover": "#005F73",
        "active": "#001219",
        "window_bg": "#2F2F2F",
    },
    "Dracula": {
        "bg": "#282A36",
        "fg": "#BD93F9",
        "hover": "#44475A",
        "active": "#191A21",
        "window_bg": "#2F2F2F",
    },
    "Electric": {
        "bg": "#0000FF",
        "fg": "#FFFF00",
        "hover": "#00FFFF",
        "active": "#00008B",
        "window_bg": "#2F2F2F",
    },
    "Emerald": {
        "bg": "#2E7D32",
        "fg": "#FFFFFF",
        "hover": "#388E3C",
        "active": "#1B5E20",
        "window_bg": "#2F2F2F",
    },
    "Forest": {
        "bg": "#1B5E20",
        "fg": "#E8F5E9",
        "hover": "#2E7D32",
        "active": "#0D5302",
        "window_bg": "#2F2F2F",
    },
    "Frost": {
        "bg": "#000000",
        "fg": "#A5F2F3",
        "hover": "#FFFFFF",
        "active": "#000000",
        "window_bg": "#2F2F2F",
    },
    "Fusion": {
        "bg": "#2A0845",
        "fg": "#FFCC00",
        "hover": "#6441A5",
        "active": "#1A052D",
        "window_bg": "#2F2F2F",
    },
    "Galaxy": {
        "bg": "#0D001A",
        "fg": "#9D50BB",
        "hover": "#6E48AA",
        "active": "#300055",
        "window_bg": "#2F2F2F",
    },
    "Ghost": {
        "bg": "#F5F5F5",
        "fg": "#212121",
        "hover": "#E0E0E0",
        "active": "#BDBDBD",
        "window_bg": "#2F2F2F",
    },
    "Glitch": {
        "bg": "#000000",
        "fg": "#FF00FF",
        "hover": "#00FFFF",
        "active": "#FFFFFF",
        "window_bg": "#2F2F2F",
    },
    "Gold": {
        "bg": "#FFD700",
        "fg": "#000000",
        "hover": "#FFEA70",
        "active": "#DAA520",
        "window_bg": "#2F2F2F",
    },
    "Graphite": {
        "bg": "#424242",
        "fg": "#B0BEC5",
        "hover": "#616161",
        "active": "#212121",
        "window_bg": "#2F2F2F",
    },
    "Hazard": {
        "bg": "#000000",
        "fg": "#FFFF00",
        "hover": "#444400",
        "active": "#000000",
        "window_bg": "#2F2F2F",
    },
    "Horizon": {
        "bg": "#1C1C1C",
        "fg": "#FF4E50",
        "hover": "#F9D423",
        "active": "#D33030",
        "window_bg": "#2F2F2F",
    },
    "HotPink": {
        "bg": "#FF69B4",
        "fg": "#FFFFFF",
        "hover": "#FF1493",
        "active": "#C71585",
        "window_bg": "#2F2F2F",
    },
    "HyperSpace": {
        "bg": "#000000",
        "fg": "#FFFFFF",
        "hover": "#1A1A1A",
        "active": "#FFFFFF",
        "window_bg": "#2F2F2F",
    },
    "Iceberg": {
        "bg": "#E1F5FE",
        "fg": "#01579B",
        "hover": "#FFFFFF",
        "active": "#B3E5FC",
        "window_bg": "#2F2F2F",
    },
    "Inferno": {
        "bg": "#212121",
        "fg": "#FF4500",
        "hover": "#FF8C00",
        "active": "#8B0000",
        "window_bg": "#2F2F2F",
    },
    "Iridium": {
        "bg": "#101010",
        "fg": "#E0E0E0",
        "hover": "#FF0055",
        "active": "#000000",
        "window_bg": "#2F2F2F",
    },
    "Jungle": {
        "bg": "#1B5E20",
        "fg": "#C8E6C9",
        "hover": "#2E7D32",
        "active": "#003300",
        "window_bg": "#2F2F2F",
    },
    "Kryptonite": {
        "bg": "#0A0F0A",
        "fg": "#9DFF00",
        "hover": "#4DFF00",
        "active": "#000000",
        "window_bg": "#2F2F2F",
    },
    "Laser": {
        "bg": "#000000",
        "fg": "#FF003C",
        "hover": "#9D00FF",
        "active": "#45001A",
        "window_bg": "#2F2F2F",
    },
    "Lava": {
        "bg": "#4E0000",
        "fg": "#FF3300",
        "hover": "#FF6600",
        "active": "#220000",
        "window_bg": "#2F2F2F",
    },
    "Magma": {
        "bg": "#000000",
        "fg": "#FF0000",
        "hover": "#660000",
        "active": "#000000",
        "window_bg": "#2F2F2F",
    },
    "Matrix_Pro": {
        "bg": "#000000",
        "fg": "#00FF41",
        "hover": "#003B00",
        "active": "#000000",
        "window_bg": "#2F2F2F",
    },
    "Midnight": {
        "bg": "#1A1A1A",
        "fg": "#F7F7F7",
        "hover": "#333333",
        "active": "#000000",
        "window_bg": "#2F2F2F",
    },
    "MidnightBlue": {
        "bg": "#1A237E",
        "fg": "#C5CAE9",
        "hover": "#283593",
        "active": "#0D1137",
        "window_bg": "#2F2F2F",
    },
    "Misty": {
        "bg": "#90A4AE",
        "fg": "#ECEFF1",
        "hover": "#B0BEC5",
        "active": "#546E7A",
        "window_bg": "#2F2F2F",
    },
    "Nebula": {
        "bg": "#1A0033",
        "fg": "#00FFD1",
        "hover": "#7000FF",
        "active": "#0D001A",
        "window_bg": "#2F2F2F",
    },
    "Neon": {
        "bg": "#000000",
        "fg": "#00FF00",
        "hover": "#003300",
        "active": "#000000",
        "window_bg": "#2F2F2F",
    },
    "Nordic": {
        "bg": "#2E3440",
        "fg": "#D8DEE9",
        "hover": "#3B4252",
        "active": "#242933",
        "window_bg": "#2F2F2F",
    },
    "Nuclear": {
        "bg": "#1A1A1A",
        "fg": "#CCFF00",
        "hover": "#333333",
        "active": "#000000",
        "window_bg": "#2F2F2F",
    },
    "Obsidian": {
        "bg": "#050505",
        "fg": "#555555",
        "hover": "#EAFF00",
        "active": "#222222",
        "window_bg": "#2F2F2F",
    },
    "Overdrive": {
        "bg": "#120000",
        "fg": "#FF8000",
        "hover": "#FF0000",
        "active": "#000000",
        "window_bg": "#2F2F2F",
    },
    "Plasma": {
        "bg": "#000022",
        "fg": "#3D5AFE",
        "hover": "#8C9EFF",
        "active": "#1A237E",
        "window_bg": "#2F2F2F",
    },
    "Riddler": {
        "bg": "#000000",
        "fg": "#19FF19",
        "hover": "#6B00B3",
        "active": "#002200",
        "window_bg": "#2F2F2F",
    },
    "Supernova": {
        "bg": "#000000",
        "fg": "#FFFFFF",
        "hover": "#FFEA00",
        "active": "#FF3D00",
        "window_bg": "#2F2F2F",
    },
    "Vaporwave": {
        "bg": "#2D004F",
        "fg": "#00FFA3",
        "hover": "#FF44CC",
        "active": "#120021",
        "window_bg": "#2F2F2F",
    },
}

current_diff_colors = DIFF_COLORS["Classics"]
current_color_name = "Classics"

def fill_missing_keys(texts):
    """
    Prüft, ob alle Keys aus 'en' auch in 'de' existieren.
    Fehlt ein Key, wird er automatisch aus 'en' übernommen.
    """
    en_keys = texts.get("en", {})
    de_keys = texts.get("de", {})

    for key, value in en_keys.items():
        if key not in de_keys:
            de_keys[key] = value  # Englische Version als Platzhalter

    texts["de"] = de_keys

# ===================== LANGUAGE =====================
