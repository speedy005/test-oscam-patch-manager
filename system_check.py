"""OSCam Patch Manager module fragment: System-check worker."""

import math
import os
import struct
import tempfile
import wave

from PyQt6.QtCore import QObject, QTimer, QUrl, Qt
from PyQt6.QtMultimedia import QSoundEffect

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
from paths import APP_VERSION

class SystemCheckWorker(QObject):
    """
    Führt alle blockierenden System-/Netzwerkprüfungen außerhalb
    des GUI-Threads aus.

    GUI-Objekte niemals direkt aus diesem Worker verändern!
    """

    progress = pyqtSignal(int, str)
    html = pyqtSignal(str)
    finished = pyqtSignal()
    error = pyqtSignal(str)

    def __init__(self, lang="de", repo_url=""):
        super().__init__()

        self.lang = lang.lower()
        self.is_de = self.lang == "de"

        self.repo_url = (repo_url or "").strip()

        # Realistische, aber kurze Timeouts
        self.SOCKET_TIMEOUT = 2.5
        self.HTTP_TIMEOUT = (3.5, 6.0)
        self.TOOL_TIMEOUT = 1.5

    def tr(self, de, en):
        return de if self.is_de else en

    def row(
        self,
        icon,
        label,
        status,
        label_col="#00FF00",
        status_col="#00ADFF",
        size="22pt",
    ):
        emoji_fonts = (
            "'Segoe UI Emoji', 'Noto Color Emoji', "
            "'Segoe UI Symbol', sans-serif"
        )

        label = str(label).replace(" ", "&nbsp;")
        status = str(status)

        return (
            '<div style="'
            'white-space:nowrap;'
            'margin:2px 0;'
            'padding:0;'
            'line-height:1.2;'
            'text-align:center;'
            'background:transparent;">'

            f'<span style="'
            f'font-family:{emoji_fonts};'
            f'font-size:22pt;'
            f'vertical-align:middle;'
            f'background:transparent;">'
            f'{icon} '
            f'</span>'

            f'<span style="'
            f"font-family:'Arial Black','Segoe UI',sans-serif;"
            f'font-size:{size};'
            f'color:{label_col};'
            f'vertical-align:middle;">'
            f'<b>{label} :&nbsp;</b>'
            f'</span>'

            f'<span style="'
            f"font-family:'Arial Black','Segoe UI',sans-serif;"
            f'font-size:{size};'
            f'color:{status_col};'
            f'vertical-align:middle;">'
            f'<b>{status}</b>'
            f'</span>'

            '</div>'
        )

    def run(self):
        import os
        import re
        import shutil
        import socket
        import subprocess
        import platform
        import importlib.util
        import time
        from datetime import timedelta

        try:
            import requests
            import psutil
        except Exception as e:
            self.error.emit(f"Import error: {e}")
            self.finished.emit()
            return

        try:
            # ============================================================
            # TEXTE
            # ============================================================

            T = {
                "lang_label": self.tr("Sprache", "Language"),
                "lang_name": self.tr("Deutsch", "English"),
                "kernel": self.tr("System Kernel", "System Kernel"),
                "free": self.tr("frei", "free"),
                "not_available": self.tr(
                    "nicht verfügbar",
                    "not available",
                ),
                "cpu_temp": self.tr(
                    "CPU-Temperatur",
                    "CPU Temperature",
                ),
                "network": self.tr("Netzwerk", "Network"),
                "ping": "Ping Test",
                "online": "Online",
                "offline": "Offline",
                "ok": "OK",
                "missing": self.tr("FEHLT", "MISSING"),
                "tool": self.tr("Prüfe Tool", "Checking tool"),
                "repo": self.tr(
                    "Prüfe Repository",
                    "Checking repository",
                ),
                "network_check": self.tr(
                    "Teste Netzwerk Verbindung",
                    "Testing network connection",
                ),
                "stats": self.tr(
                    "Lade GitHub Statistiken",
                    "Loading GitHub statistics",
                ),
                "finish": self.tr(
                    "Finalisiere System Check",
                    "Finalizing system check",
                ),
                "done": self.tr(
                    "System Check abgeschlossen ✔",
                    "System check completed ✔",
                ),
            }

            C_GREEN = "#00FF00"
            C_BLUE = "#00ADFF"
            C_RED = "#FF0000"
            C_ORANGE = "#F57A08"
            C_YELLOW = "#FFFF00"

            S_NORM = "22pt"
            S_EMOJI = "22pt"
            S_REPO = "20pt"

            # ============================================================
            # HEADER
            # ============================================================

            copyright_text = self.tr(
                "© 2026 speedy005 - Alle Rechte vorbehalten.",
                "© 2026 speedy005 - All rights reserved.",
            )

            greeting = self.tr(
                "Grüße, speedy005",
                "Regards, speedy005",
            )

            app_ver = APP_VERSION

            header = f"""
            <style>
                body {{
                    background: transparent;
                }}

                span {{
                    background: transparent;
                }}
            </style>

            <div style="
                text-align:center;
                width:100%;
                font-family:sans-serif;
            ">

                <div style="
                    line-height:1.2;
                    margin-bottom:16px;
                ">
                    <span style="
                        font-family:'Arial Black',sans-serif;
                        font-size:18pt;
                        font-weight:bold;
                        color:#39FF14;
                        text-shadow:
                            0 0 6px #39FF14,
                            0 0 12px #00FFFF;
                    ">
                        {copyright_text.split("-", 1)[0].strip()}
                    </span>

                    <span style="
                        color:#FFFFFF;
                        font-size:18pt;
                    ">
                        -
                    </span>

                    <span style="
                        font-family:'Arial Black',sans-serif;
                        font-size:18pt;
                        font-weight:bold;
                        color:#FF0039;
                        text-shadow:
                            0 0 6px #FF00FF,
                            0 0 12px #FF1493;
                    ">
                        {copyright_text.split("-", 1)[1].strip()}
                    </span>
                </div>

                <div style="margin-bottom:8px;">
                    <span style="
                        color:{C_GREEN};
                        font-size:32pt;
                    ">●</span>

                    <span style="
                        color:{C_RED};
                        font-family:'Arial Black',sans-serif;
                        font-size:28pt;
                        font-weight:900;
                    ">
                        LIVE
                    </span>

                    <span style="
                        color:#FFFFFF;
                        font-size:22pt;
                    ">
                        |
                    </span>

                    <span style="
                        color:{C_BLUE};
                        font-family:'Arial Black',sans-serif;
                        font-size:28pt;
                        font-weight:900;
                    ">
                        System Monitor
                    </span>
                </div>

                <div style="margin-bottom:12px;">
                    <span style="
                        font-family:'Noto Color Emoji',
                        'Segoe UI Emoji',
                        'Apple Color Emoji',
                        sans-serif;
                        font-size:32pt;
                    ">🚀</span>

                    <span style="
                        color:{C_ORANGE};
                        font-family:'Arial Black',sans-serif;
                        font-size:32pt;
                        font-weight:900;
                    ">
                        OSCam Emu Patch Generator
                    </span>
                </div>

                <div style="
                    font-family:Arial,sans-serif;
                    font-size:16pt;
                    font-weight:bold;
                    margin-top:6px;
                ">
                    <span style="color:#FF0033;">
                        {greeting.split(",", 1)[0]}
                    </span>

                    <span style="color:#00ADFF;">
                        ,{greeting.split(",", 1)[1]}
                    </span>
                </div>

            </div>

            <div style="
                text-align:center;
                font-family:'Arial Black',sans-serif;
                font-size:22pt;
                font-weight:bold;
                margin-top:8px;
            ">
                <span style="color:{C_RED};">
                    {self.tr("Autor", "Author")}:
                </span>

                <span style="color:{C_YELLOW};">
                    speedy005
                </span>

                <span style="color:#FFFFFF;">
                    |
                </span>

                <span style="color:{C_ORANGE};">
                    Version:
                </span>

                <span style="color:{C_BLUE};">
                    {app_ver}
                </span>
            </div>
            """

            self.html.emit(header)

            self.progress.emit(
                8,
                self.tr(
                    "📋 Lade Programm Header",
                    "📋 Loading program header",
                ),
            )

            # ============================================================
            # SYSTEM
            # ============================================================

            self.progress.emit(
                15,
                self.tr(
                    "⚙️ Initialisiere System Check",
                    "⚙️ Initializing system check",
                ),
            )

            os_type = platform.system()

            if os_type == "Linux":
                try:
                    os_name = platform.freedesktop_os_release().get(
                        "PRETTY_NAME",
                        "Linux",
                    )
                except Exception:
                    os_name = f"Linux {platform.release()}"
            elif os_type == "Windows":
                os_name = f"Windows {platform.release()}"
            else:
                os_name = f"{os_type} {platform.release()}"

            os_icon = "💻" if os_type == "Windows" else "🐧"

            system_html = ""

            system_html += self.row(
                "🇩🇪" if self.is_de else "🇬🇧",
                T["lang_label"],
                T["lang_name"],
                C_GREEN,
                C_BLUE,
            )

            system_html += self.row(
                "⚙️",
                T["kernel"],
                f"{platform.system()} {platform.release()}",
                C_GREEN,
                C_BLUE,
            )

            system_html += self.row(
                os_icon,
                "System",
                os_name,
                C_GREEN,
                C_BLUE,
            )

            cpu_arch = platform.machine()
            cpu_count = psutil.cpu_count(logical=True) or 0

            system_html += self.row(
                "📟",
                "CPU",
                f"{cpu_arch} ({cpu_count} Kerne)",
                C_GREEN,
                C_BLUE,
            )

            ram = psutil.virtual_memory()

            ram_total = round(ram.total / (1024 ** 3))
            ram_free = round(ram.available / (1024 ** 3))

            system_html += self.row(
                "💾",
                "RAM",
                f"{ram_total}GB / {ram_free}GB "
                f"{T['free']}",
                C_GREEN,
                C_BLUE,
            )

            # ============================================================
            # DISKS
            # ============================================================

            for part in psutil.disk_partitions(all=False):
                try:
                    usage = psutil.disk_usage(part.mountpoint)

                    total_gb = round(
                        usage.total / (1024 ** 3)
                    )

                    free_gb = round(
                        usage.free / (1024 ** 3)
                    )

                    device_name = (
                        part.device
                        if os_type != "Windows"
                        else part.mountpoint
                    )

                    system_html += self.row(
                        "💽",
                        device_name,
                        f"{total_gb}GB / "
                        f"{free_gb}GB "
                        f"{T['free']}",
                        C_GREEN,
                        C_BLUE,
                    )

                except (PermissionError, OSError):
                    system_html += self.row(
                        "💽",
                        part.mountpoint,
                        T["not_available"],
                        C_RED,
                        C_BLUE,
                    )

            # ============================================================
            # TEMPERATURE
            # ============================================================

            try:
                temps = psutil.sensors_temperatures()

                found_temp = False

                for name, entries in temps.items():
                    if entries:
                        found_temp = True

                        temp = entries[0].current

                        system_html += self.row(
                            "🌡️",
                            f"{T['cpu_temp']} ({name})",
                            f"{temp:.1f}°C",
                            C_GREEN,
                            C_BLUE,
                        )

                if not found_temp:
                    system_html += self.row(
                        "🌡️",
                        T["cpu_temp"],
                        T["not_available"],
                        C_RED,
                        C_BLUE,
                    )

            except Exception:
                system_html += self.row(
                    "🌡️",
                    T["cpu_temp"],
                    T["not_available"],
                    C_RED,
                    C_BLUE,
                )

            # ============================================================
            # UPTIME
            # ============================================================

            try:
                uptime_sec = int(
                    time.time() - psutil.boot_time()
                )

                uptime_str = str(
                    timedelta(seconds=uptime_sec)
                )

            except Exception:
                uptime_str = "N/A"

            system_html += self.row(
                "⏱️",
                "Uptime",
                uptime_str,
                C_GREEN,
                C_BLUE,
            )

            self.html.emit(system_html)

            self.progress.emit(
                30,
                f"💻 {self.tr('Systeminformationen gelesen', 'System information read')}",
            )

            # ============================================================
            # TOOLS
            # ============================================================

            tool_list = [
                "pacman",
                "yay",
                "git",
                "patch",
                "zip",
                "nmap",
                "hydra",
                "john",
                "python3",
                "pip",
                "ssh",
                "sqlmap",
                "wireshark",
                "nikto",
                "tcpdump",
                "aircrack-ng",
                "hashcat",
            ]

            tool_html = ""

            total_tools = len(tool_list)

            for i, tool_name in enumerate(tool_list):

                path = shutil.which(tool_name)

                if path:
                    status_icon = "✅"
                    info = (
                        "aktiv"
                        if self.is_de
                        else "on"
                    )

                    try:
                        if tool_name == "john":
                            command = [
                                tool_name,
                                "--list=build-info",
                            ]
                        else:
                            command = [
                                tool_name,
                                "--version",
                            ]

                        info_raw = subprocess.check_output(
                            command,
                            stderr=subprocess.STDOUT,
                            timeout=self.TOOL_TIMEOUT,
                            text=True,
                            errors="replace",
                        ).strip()

                        match = re.search(
                            r"\d+(?:\.\d+)+|\d+",
                            info_raw,
                        )

                        if match:
                            info = f"v{match.group(0)}"

                    except (
                        subprocess.TimeoutExpired,
                        subprocess.SubprocessError,
                        OSError,
                    ):
                        # Tool ist vorhanden, aber Version konnte
                        # nicht rechtzeitig ermittelt werden.
                        info = (
                            "aktiv"
                            if self.is_de
                            else "on"
                        )

                    tool_html += self.row(
                        status_icon,
                        tool_name.capitalize(),
                        info,
                        C_GREEN,
                        C_BLUE,
                    )

                else:
                    tool_html += self.row(
                        "❌",
                        tool_name.capitalize(),
                        (
                            "nicht verfügbar"
                            if self.is_de
                            else "off"
                        ),
                        C_RED,
                        C_RED,
                    )

                progress = 30 + int(
                    ((i + 1) / total_tools) * 20
                )

                self.progress.emit(
                    progress,
                    f"🔍 {T['tool']}: {tool_name}",
                )

            self.html.emit(tool_html)

            # ============================================================
            # PYTHON PACKAGES
            # ============================================================

            package_html = ""

            for pkg in ("PyQt6", "requests"):
                try:
                    available = (
                        importlib.util.find_spec(pkg)
                        is not None
                    )
                except Exception:
                    available = False

                package_html += self.row(
                    "📦",
                    pkg,
                    T["ok"] if available else T["missing"],
                    C_GREEN if available else C_RED,
                    C_BLUE if available else C_RED,
                )

            self.html.emit(package_html)

            # ============================================================
            # NETWORK
            # ============================================================

            self.progress.emit(
                65,
                f"🌐 {T['network_check']}",
            )

            network_html = ""

            local_ip = None
            ping_ms = None

            try:
                start = time.perf_counter()

                with socket.create_connection(
                    ("8.8.8.8", 53),
                    timeout=self.SOCKET_TIMEOUT,
                ) as conn:

                    local_ip = conn.getsockname()[0]

                ping_ms = int(
                    (time.perf_counter() - start) * 1000
                )

                network_html += self.row(
                    "📡",
                    T["network"],
                    f"{T['online']} ({local_ip})",
                    C_GREEN,
                    C_BLUE,
                )

                network_html += self.row(
                    "⚡",
                    T["ping"],
                    f"{ping_ms} ms",
                    C_GREEN,
                    C_BLUE,
                )

            except Exception:
                network_html += self.row(
                    "❌",
                    T["network"],
                    T["offline"],
                    C_RED,
                    C_RED,
                )

            self.html.emit(network_html)

            # ============================================================
            # REPOSITORIES
            # ============================================================

            repo_list = [
                (
                    "📡",
                    "Streamboard Oscam",
                    "git.streamboard.tv",
                ),
                (
                    "🐙",
                    "OSCam Emu Mirror",
                    "github.com",
                ),
                (
                    "⭐",
                    "speedy Oscam Emu",
                    "github.com",
                ),
                (
                    "🔗",
                    "GitHub API Server",
                    "api.github.com",
                ),
            ]

            repo_html = ""

            total_repos = len(repo_list)

            for i, (icon, r_name, host) in enumerate(
                repo_list
            ):

                online = False

                try:
                    # TCP-Test statt gethostbyname().
                    # Dadurch haben wir einen echten Timeout.
                    with socket.create_connection(
                        (host, 443),
                        timeout=self.SOCKET_TIMEOUT,
                    ):
                        online = True

                except (
                    OSError,
                    socket.timeout,
                ):
                    online = False

                repo_html += self.row(
                    icon,
                    r_name,
                    T["online"]
                    if online
                    else T["offline"],
                    (
                        C_GREEN
                        if online
                        else C_RED
                    ),
                    (
                        C_BLUE
                        if online
                        else C_RED
                    ),
                    S_REPO,
                )

                progress = 70 + int(
                    ((i + 1) / total_repos) * 15
                )

                self.progress.emit(
                    progress,
                    f"📡 {T['repo']}: {r_name}",
                )

            # ============================================================
            # EINGESTELLTE REPO-URL ANZEIGEN
            # ============================================================

            if self.repo_url:

                safe_repo_url = (
                    self.repo_url
                    .replace("&", "&amp;")
                    .replace("<", "&lt;")
                    .replace(">", "&gt;")
                    .replace('"', "&quot;")
                )

                repo_html += f"""
                <div style="
                    text-align:center;
                    margin-top:12px;
                    margin-bottom:8px;
                    font-family:'Arial Black',sans-serif;
                ">

                    <span style="
                        color:{C_ORANGE};
                        font-size:20pt;
                        font-weight:bold;
                    ">
                        🔗 Repository:
                    </span>

                    <br>

                    <span style="
                        color:{C_BLUE};
                        font-size:18pt;
                        font-weight:bold;
                    ">
                        {safe_repo_url}
                    </span>

                </div>
                """

            self.html.emit(repo_html)

            # ============================================================
            # GITHUB STATISTIK
            # ============================================================

            self.progress.emit(
                88,
                f"📊 {T['stats']}",
            )

            git_count = 0

            # Wenn eine GitHub-Repo-URL konfiguriert ist,
            # versuchen wir daraus owner/repository zu ermitteln.
            github_repo = "speedy005/Oscam-Emu-patch-Manager"

            if self.repo_url:
                match = re.search(
                    r"github\.com/([^/\s]+)/([^/\s#?]+)",
                    self.repo_url,
                    re.IGNORECASE,
                )

                if match:
                    owner = match.group(1)
                    repository = match.group(2)

                    repository = repository.rstrip(
                        "/"
                    ).removesuffix(".git")

                    github_repo = (
                        f"{owner}/{repository}"
                    )

            try:
                import urllib3

                urllib3.disable_warnings(
                    urllib3.exceptions.InsecureRequestWarning
                )

                response = requests.get(
                    (
                        "https://api.github.com/"
                        f"repos/{github_repo}/releases"
                    ),
                    headers={
                        "User-Agent":
                            "OSCam-Emu-Patch-Manager"
                    },
                    timeout=self.HTTP_TIMEOUT,
                )

                if response.status_code == 200:

                    releases = response.json()

                    for release in releases:
                        for asset in release.get(
                            "assets",
                            [],
                        ):
                            try:
                                git_count += int(
                                    asset.get(
                                        "download_count",
                                        0,
                                    )
                                )
                            except (
                                TypeError,
                                ValueError,
                            ):
                                pass

            except (
                requests.RequestException,
                ValueError,
                OSError,
            ):
                # Statistik darf niemals den gesamten
                # Systemcheck scheitern lassen.
                git_count = 0

            # ============================================================
            # LOCAL COUNTER
            # ============================================================

            try:
                tool_dir = os.path.dirname(
                    os.path.abspath(__file__)
                )
            except NameError:
                tool_dir = os.getcwd()

            counter_file = os.path.join(
                tool_dir,
                "install_counter.txt",
            )

            try:

                if (
                    not os.path.exists(counter_file)
                    or os.path.getsize(counter_file) == 0
                ):
                    with open(
                        counter_file,
                        "w",
                        encoding="utf-8",
                    ) as f:
                        f.write("0")

                with open(
                    counter_file,
                    "r",
                    encoding="utf-8",
                ) as f:
                    try:
                        install_count = int(
                            f.read().strip()
                        )
                    except ValueError:
                        install_count = 0

                install_count += 1

                with open(
                    counter_file,
                    "w",
                    encoding="utf-8",
                ) as f:
                    f.write(str(install_count))

            except OSError:
                install_count = 0

            total_stats = install_count

            # ============================================================
            # STATISTIK HTML
            # ============================================================

            stats_html = f"""
            <div style="
                text-align:center;
                font-family:'Arial Black',sans-serif;
                margin-top:20px;
            ">

                <div style="
                    font-size:32pt;
                    font-weight:bold;
                    margin-bottom:10px;
                ">
                    📊
                    <span style="color:{C_YELLOW};">
                        STATISTICS
                    </span>
                </div>

                <div style="
                    font-size:28pt;
                    font-weight:bold;
                    margin:12px 0;
                ">
                    🐙
                    <span style="color:{C_GREEN};">
                        GitHub:
                    </span>

                    <span style="color:{C_BLUE};">
                        {git_count}
                    </span>
                </div>

                <div style="
                    font-size:28pt;
                    font-weight:bold;
                    margin:12px 0;
                ">
                    💾
                    <span style="color:{C_ORANGE};">
                        Local:
                    </span>

                    <span style="color:{C_BLUE};">
                        {install_count}
                    </span>
                </div>

                <div style="
                    font-size:30pt;
                    font-weight:bold;
                    margin-top:15px;
                ">
                    📊
                    <span style="color:{C_YELLOW};">
                        Total:
                    </span>

                    <span style="color:{C_BLUE};">
                        {total_stats}
                    </span>
                </div>

            </div>
            """

            self.html.emit(stats_html)

            # ============================================================
            # FOOTER
            # ============================================================

            self.progress.emit(
                96,
                f"⚙️ {T['finish']}",
            )

            footer = f"""
            <div style="
                text-align:center;
                line-height:1.2;
                margin-top:14px;
                margin-bottom:10px;
            ">

                <span style="
                    font-family:'Segoe UI Emoji',
                    'Noto Color Emoji',
                    sans-serif;
                    font-size:{S_EMOJI};
                ">
                    ✅
                </span>

                <span style="
                    font-family:'Arial Black',sans-serif;
                    font-size:{S_NORM};
                    font-weight:bold;
                    color:{C_GREEN};
                ">
                    {self.tr(
                        "System-Check OK.",
                        "System Check OK."
                    )}
                </span>

                <span style="
                    font-family:'Arial Black',sans-serif;
                    font-size:{S_NORM};
                    font-weight:bold;
                    color:{C_BLUE};
                ">
                    &nbsp;
                    {self.tr(
                        "Bereit.",
                        "Ready."
                    )}
                </span>

            </div>
            """

            self.html.emit(footer)

            self.progress.emit(
                100,
                f"✅ {T['done']}",
            )

        except Exception as e:
            self.error.emit(
                f"{type(e).__name__}: {e}"
            )

        finally:
            self.finished.emit()

# PatchManagerGUI
