"""OSCam Patch Manager: application entry point."""

import ctypes
import os
import platform
import shutil
import subprocess
import sys
import traceback

from PyQt6.QtCore import QLibraryInfo, QTimer, Qt
from PyQt6.QtWidgets import QApplication, QMessageBox

# Retained for compatibility with existing project/bootstrap behavior.
from bootstrap import auto_install_emoji_font  # noqa: F401
from dialogs_splash import CinematicMatrixSplash
from gui import PatchManagerGUI


APP_TITLE = "OSCam Emu Patch Manager new - by speedy005"
ELEVATION_FLAG = "--elevated"
UPDATE_FLAG = "--perform-update"


def _is_elevated():
    """Return whether this process has administrator/root privileges."""
    system = platform.system()

    if system == "Windows":
        try:
            return bool(ctypes.windll.shell32.IsUserAnAdmin())
        except Exception:
            return False

    if system == "Linux":
        return hasattr(os, "geteuid") and os.geteuid() == 0

    return False


def _restart_elevated():
    """
    Relaunch this script with elevated privileges on Windows or Linux.

    Returns True if a new elevated process was successfully requested.
    On Linux, execv replaces the current process when sudo/pkexec is used.
    """
    system = platform.system()
    script_path = os.path.abspath(sys.argv[0])
    args = [arg for arg in sys.argv[1:] if arg != ELEVATION_FLAG]
    args.append(ELEVATION_FLAG)

    if system == "Windows":
        if _is_elevated():
            return False

        params = subprocess.list2cmdline([script_path, *args])
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
                f"Windows konnte keine Administratorrechte anfordern "
                f"(ShellExecute-Fehler {result})."
            )
        return True

    if system == "Linux":
        if _is_elevated():
            return False

        sudo = shutil.which("sudo")
        pkexec = shutil.which("pkexec")

        # Prefer sudo when installed because argv can be passed directly.
        if sudo:
            env_values = {
                key: os.environ[key]
                for key in (
                    "DISPLAY",
                    "XAUTHORITY",
                    "XDG_RUNTIME_DIR",
                    "WAYLAND_DISPLAY",
                    "DBUS_SESSION_BUS_ADDRESS",
                )
                if os.environ.get(key)
            }
            command = [sudo, "env"]
            command.extend(f"{key}={value}" for key, value in env_values.items())
            command.extend([sys.executable, script_path, *args])
            os.execv(sudo, command)
            return True  # pragma: no cover - execv replaces this process

        if pkexec:
            env_exe = shutil.which("env") or "/usr/bin/env"
            env_values = {
                key: os.environ[key]
                for key in (
                    "DISPLAY",
                    "XAUTHORITY",
                    "XDG_RUNTIME_DIR",
                    "WAYLAND_DISPLAY",
                    "DBUS_SESSION_BUS_ADDRESS",
                )
                if os.environ.get(key)
            }
            command = [pkexec, env_exe]
            command.extend(f"{key}={value}" for key, value in env_values.items())
            command.extend([sys.executable, script_path, *args])
            os.execv(pkexec, command)
            return True  # pragma: no cover

        raise RuntimeError(
            "Weder sudo noch pkexec wurde gefunden. "
            "Administrator-/Root-Rechte können nicht angefordert werden."
        )

    # Do not attempt automatic elevation on other operating systems.
    return False


def _configure_qt_environment():
    """Set platform and high-DPI environment options before QApplication."""
    os.environ.setdefault("NO_AT_BRIDGE", "1")

    system = platform.system()

    if system == "Linux" and not os.environ.get("QT_QPA_PLATFORM"):
        if os.environ.get("WAYLAND_DISPLAY"):
            os.environ["QT_QPA_PLATFORM"] = "wayland"
        elif os.environ.get("DISPLAY"):
            os.environ["QT_QPA_PLATFORM"] = "xcb"

    if system == "Windows":
        for stream in (sys.stdout, sys.stderr):
            if hasattr(stream, "reconfigure"):
                try:
                    stream.reconfigure(encoding="utf-8")
                except (OSError, ValueError):
                    pass

    # Qt 6 handles high-DPI scaling natively. Set this only if the user
    # has not explicitly configured a scale factor.
    os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "1")

    try:
        policy = Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
        QApplication.setHighDpiScaleFactorRoundingPolicy(policy)
    except (AttributeError, RuntimeError):
        pass

    plugin_path = QLibraryInfo.path(QLibraryInfo.LibraryPath.PluginsPath)
    if plugin_path:
        os.environ.setdefault("QT_QPA_PLATFORM_PLUGIN_PATH", plugin_path)


def main():
    """Configure Qt and launch the Patch Manager."""
    # Avoid elevation loops when the elevated child starts.
    if ELEVATION_FLAG not in sys.argv and platform.system() in ("Windows", "Linux"):
        if not _is_elevated():
            try:
                if _restart_elevated():
                    # Windows starts a separate process; close the unelevated one.
                    return 0
            except Exception as exc:
                print(f"[SYSTEM] Rechteanhebung fehlgeschlagen: {exc}", file=sys.stderr)
                try:
                    app = QApplication.instance() or QApplication(sys.argv)
                    QMessageBox.critical(
                        None,
                        "Administratorrechte erforderlich",
                        "Die Anwendung konnte nicht mit erhöhten Rechten gestartet "
                        f"werden.\n\nDetails: {exc}",
                    )
                except Exception:
                    pass
                return 1

    _configure_qt_environment()

    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName("OSCam Patch Manager")
    app.setStyle("Fusion")

    main_window = None
    splash = None

    def actually_start():
        nonlocal main_window
        try:
            main_window = PatchManagerGUI()
            main_window.setWindowTitle(APP_TITLE)

            # True fullscreen: no title bar or taskbar. Qt layouts should adapt
            # to the available screen size; widget-specific fixed sizes in gui.py
            # may still need responsive-layout adjustments.
            main_window.showFullScreen()
            main_window.raise_()
            main_window.activateWindow()

            if UPDATE_FLAG in sys.argv:
                sys.argv.remove(UPDATE_FLAG)
                if hasattr(main_window, "plugin_update_action"):
                    QTimer.singleShot(1500, main_window.plugin_update_action)
                else:
                    print(
                        "[SYSTEM] Update konnte nicht automatisch gestartet "
                        "werden: Methode 'plugin_update_action' nicht gefunden."
                    )

            if hasattr(main_window, "update_ui_texts"):
                QTimer.singleShot(100, main_window.update_ui_texts)

            if platform.system() == "Windows":
                try:
                    import winsound
                    winsound.Beep(1500, 100)
                except Exception:
                    pass

        except Exception:
            details = traceback.format_exc()
            print(f"FATAL ERROR:\n{details}", file=sys.stderr)

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
        # Let Qt finish closing/painting the splash screen first.
        QTimer.singleShot(200, actually_start)

    try:
        splash = CinematicMatrixSplash(duration=5000)
        splash.finished.connect(launch_main_gui)
        splash.show()
    except Exception as exc:
        print(f"[SYSTEM] Splash-Screen übersprungen: {exc}", file=sys.stderr)
        actually_start()

    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
