"""OSCam Patch Manager module fragment: Application entry point."""

# Explicit module imports retained for compatibility with legacy callbacks.
import ctypes
import os
import platform
import shlex
import shutil
import subprocess
import sys
import traceback

from PyQt6.QtCore import Qt, QTimer, QLibraryInfo
from PyQt6.QtWidgets import QApplication, QMessageBox

from bootstrap import auto_install_emoji_font
from dialogs_splash import CinematicMatrixSplash
from gui import PatchManagerGUI


def _is_elevated():
    """Return whether this process already has administrator/root privileges."""
    if platform.system() == "Windows":
        try:
            return bool(ctypes.windll.shell32.IsUserAnAdmin())
        except Exception:
            return False

    if platform.system() == "Linux":
        return hasattr(os, "geteuid") and os.geteuid() == 0

    return False


def _restart_elevated():
    """
    Restart this application with elevated privileges.

    Uses absolute paths so a sudo/pkexec process cannot accidentally resolve
    main.py relative to /root. On Linux, only the display-session variables
    needed by Qt are forwarded.
    """
    system = platform.system()
    script_path = os.path.abspath(sys.argv[0])
    python_cmd = [sys.executable, script_path, *sys.argv[1:], "--elevated"]

    if system == "Windows":
        if not ctypes.windll.shell32.IsUserAnAdmin():
            params = subprocess.list2cmdline(python_cmd[1:])
            result = ctypes.windll.shell32.ShellExecuteW(
                None,
                "runas",
                sys.executable,
                params,
                os.path.dirname(script_path),
                1,
            )
            if result <= 32:
                raise RuntimeError(
                    f"Windows could not elevate the process (error {result})."
                )
            return True
        return False

    if system == "Linux":
        if os.geteuid() == 0:
            return False

        launcher = shutil.which("pkexec") or shutil.which("sudo")
        if not launcher:
            raise RuntimeError(
                "Neither pkexec nor sudo was found; cannot request root privileges."
            )

        env_values = {}
        for key in (
            "DISPLAY",
            "XAUTHORITY",
            "XDG_RUNTIME_DIR",
            "WAYLAND_DISPLAY",
            "DBUS_SESSION_BUS_ADDRESS",
        ):
            value = os.environ.get(key)
            if value:
                env_values[key] = value

        # Prefer sudo when available: pass argv directly, without shell quoting.
        sudo = shutil.which("sudo")
        if sudo:
            command = [sudo, "env"]
            command.extend(f"{key}={value}" for key, value in env_values.items())
            command.extend(python_cmd)
            os.execv(sudo, command)

        # pkexec accepts an executable and arguments. Use /usr/bin/env to pass
        # environment values without building an unsafe shell command.
        env_exe = shutil.which("env") or "/usr/bin/env"
        command = [launcher, env_exe]
        command.extend(f"{key}={value}" for key, value in env_values.items())
        command.extend(python_cmd)
        os.execv(launcher, command)

    return False


def main():
    """Configure Qt and start the OSCam Patch Manager GUI."""
    # Avoid accidental recursive elevation.
    

    os.environ.setdefault("QT_AUTO_SCREEN_SCALE_FACTOR", "1")
    os.environ.setdefault("NO_AT_BRIDGE", "1")

    system = platform.system()
    if system == "Linux" and not os.environ.get("QT_QPA_PLATFORM"):
        if os.environ.get("WAYLAND_DISPLAY"):
            os.environ["QT_QPA_PLATFORM"] = "wayland"
        elif os.environ.get("DISPLAY"):
            os.environ["QT_QPA_PLATFORM"] = "xcb"
    elif system == "Windows":
        for stream in (sys.stdout, sys.stderr):
            if hasattr(stream, "reconfigure"):
                try:
                    stream.reconfigure(encoding="utf-8")
                except (OSError, ValueError):
                    pass

    try:
        policy = getattr(Qt.HighDpiScaleFactorRoundingPolicy, "PassThrough", None)
        if policy is not None:
            QApplication.setHighDpiScaleFactorRoundingPolicy(policy)
    except Exception:
        pass

    plugin_path = QLibraryInfo.path(QLibraryInfo.LibraryPath.PluginsPath)
    if plugin_path:
        os.environ.setdefault("QT_QPA_PLATFORM_PLUGIN_PATH", plugin_path)

    app = QApplication.instance() or QApplication(sys.argv)
    app.setStyle("Fusion")

    screen = app.primaryScreen()
    if screen is not None:
        geometry = screen.availableGeometry()
        width, height = geometry.width(), geometry.height()
    else:
        width, height = 0, 0

    try:
        import getpass
        current_user = getpass.getuser()
    except Exception:
        current_user = (
            os.environ.get("USERNAME")
            or os.environ.get("USER")
            or "unknown"
        )

   

    

    main_window = None

    def actually_start():
        nonlocal main_window
        try:
            main_window = PatchManagerGUI()
            main_window.setWindowTitle("OSCam Emu Patch Manager new - by speedy005")
            main_window.showMaximized()
            main_window.raise_()
            if "--perform-update" in sys.argv:
                sys.argv.remove("--perform-update")
                if hasattr(main_window, "plugin_update_action"):
                    QTimer.singleShot(
                        1500,
                        main_window.plugin_update_action,
                    )
                else:
                    print(
                        "[SYSTEM] Update konnte nicht automatisch "
                        "gestartet werden: Methode nicht gefunden."
                    )

            if hasattr(main_window, "update_ui_texts"):
                QTimer.singleShot(100, main_window.update_ui_texts)

            if system == "Windows":
                try:
                    import winsound
                    winsound.Beep(1500, 100)
                except Exception:
                    pass

        except Exception:
            details = traceback.format_exc()
            print(f"FATAL ERROR:\n{details}")
            message = QMessageBox()
            message.setIcon(QMessageBox.Icon.Critical)
            message.setWindowTitle("SYSTEM FAILURE")
            message.setText("Kritischer Fehler beim Laden des Kern-Moduls.")
            message.setDetailedText(details)
            message.setStyleSheet(
                "background-color: #050505; color: #00ff41; "
                "font-family: 'Consolas';"
            )
            message.exec()
            app.exit(1)

    def launch_main_gui():
        QTimer.singleShot(200, actually_start)

    try:
        splash = CinematicMatrixSplash(duration=5000)
        splash.finished.connect(launch_main_gui)
        splash.show()
    except Exception as exc:
        print(f"[SYSTEM] Splash-Screen übersprungen: {exc}")
        actually_start()

    return app.exec()
