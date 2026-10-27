
"""OSCam Patch Manager: application paths, repository defaults, and path helpers."""

import json
import os
import platform
import shutil
import subprocess
from datetime import datetime


# ============================================================
# APP CONFIG
# ============================================================

APP_VERSION = "1.5.9"


# ============================================================
# PLUGIN / WORKING DIRECTORY
# ============================================================

PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))
WORK_DIR = PLUGIN_DIR


# ============================================================
# REPOSITORY DIRECTORIES
# ============================================================

# OSCam-Quellrepository (Streamboard oder speedy005)
TEMP_REPO = os.path.join(PLUGIN_DIR, "streamboard-git")
STREAMREPO = TEMP_REPO

# OSCam-Emu-Repository
PATCH_EMU_GIT_DIR = os.path.join(PLUGIN_DIR, "oscam-emu-git")
EMUREPO = PATCH_EMU_GIT_DIR


# ============================================================
# FILES
# ============================================================

PYC_FILE = os.path.join(PLUGIN_DIR, "oscam_patch_manager_new.pyc")
CACHE_DIR = os.path.join(PLUGIN_DIR, "__pycache__")
CONFIG_FILE = os.path.join(PLUGIN_DIR, "config.json")
GITHUB_CONF_FILE = os.path.join(
    PLUGIN_DIR, "github_upload_config.json"
)
PATCH_FILE = os.path.join(PLUGIN_DIR, "oscam-emu.patch")
ZIP_FILE = os.path.join(PLUGIN_DIR, "oscam-emu.zip")
ICON_DIR = os.path.join(PLUGIN_DIR, "icons")


# ============================================================
# BACKUP DIRECTORIES
# ============================================================

def get_initial_patch_dir():
    """Choose the backup directory for the current operating system."""
    folder_name = "backup"

    if platform.system() != "Windows":
        s3_path = "/opt/s3/support/patches"

        if os.path.isdir(s3_path) and os.access(s3_path, os.W_OK):
            path = s3_path
        else:
            path = os.path.join(PLUGIN_DIR, folder_name)
    else:
        path = os.path.join(PLUGIN_DIR, folder_name)

    os.makedirs(path, exist_ok=True)
    return path


def get_best_patch_dir():
    """Choose the best writable patch directory."""
    s3_path = "/opt/s3/support/patches"

    if os.path.isdir(s3_path) and os.access(s3_path, os.W_OK):
        return s3_path

    local_path = os.path.join(PLUGIN_DIR, "patches")
    os.makedirs(local_path, exist_ok=True)
    return local_path


OLD_PATCH_DIR = get_initial_patch_dir()
OLD_PATCH_DIR_PLUGIN_DEFAULT = OLD_PATCH_DIR

OLD_PATCH_FILE = os.path.join(
    OLD_PATCH_DIR, "oscam-emu.patch"
)
ALT_PATCH_FILE = os.path.join(
    OLD_PATCH_DIR, "oscam-emu.altpatch"
)
PATCH_MANAGER_OLD = os.path.join(
    OLD_PATCH_DIR, "oscam_patch_manager_old.py"
)
CONFIG_OLD = os.path.join(
    OLD_PATCH_DIR, "config_old.json"
)
GITHUB_CONFIG_OLD = os.path.join(
    OLD_PATCH_DIR, "github_upload_config_old.json"
)


# ============================================================
# TOOLS
# ============================================================

CHECK_TOOLS_SCRIPT = os.path.join(PLUGIN_DIR, "check_tools.sh")
PATCH_MODIFIER = "speedy005"


# ============================================================
# REPOSITORY URLS
# ============================================================

EMUREPO_URL = "https://github.com/oscam-mirror/oscam-emu.git"

STREAMBOARD_OSCAM_URL = (
    "https://git.streamboard.tv/common/oscam.git"
)
SPEEDY_OSCAM_URL = (
    "https://github.com/speedy005/oscam-git"
)

STREAMREPO_URL = STREAMBOARD_OSCAM_URL

STREAM_REPO_CHOICES = {
    "Streamboard Oscam": STREAMBOARD_OSCAM_URL,
    "speedy Oscan": SPEEDY_OSCAM_URL,
}


# ============================================================
# GIT HELPERS
# ============================================================

def find_git_executable():
    """Return the Git executable path, or None if Git is unavailable."""
    return shutil.which("git")


def ensure_git_repository(
    repo_dir,
    repo_url,
    description="Repository",
    clone_timeout=1200,
    depth=None,
    log=print,
):
    """
    Ensure that repo_dir is a valid Git working tree.

    - Keeps an existing valid repository.
    - Clones when the directory is missing or empty.
    - Never deletes a non-empty invalid directory.
    - Supports the arguments used by create_patch().
    """
    if not repo_dir:
        raise RuntimeError(
            f"{description}: Repository-Pfad fehlt."
        )

    if not repo_url:
        raise RuntimeError(
            f"{description}: Repository-URL fehlt."
        )

    repo_dir = os.path.abspath(
        os.path.expanduser(
            os.path.expandvars(str(repo_dir))
        )
    )

    git_executable = find_git_executable()

    if not git_executable:
        raise RuntimeError(
            "Git wurde nicht gefunden. Bitte Git installieren "
            "oder den Git-Pfad zur PATH-Umgebungsvariable hinzufügen."
        )

    def git_is_valid(path):
        if not os.path.isdir(path):
            return False

        try:
            result = subprocess.run(
                [
                    git_executable,
                    "-C",
                    path,
                    "rev-parse",
                    "--is-inside-work-tree",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                stdin=subprocess.DEVNULL,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=60,
                shell=False,
            )

            return (
                result.returncode == 0
                and result.stdout.strip().lower() == "true"
            )

        except (OSError, subprocess.SubprocessError):
            return False

    # Vorhandenes gültiges Repository weiterverwenden.
    if git_is_valid(repo_dir):
        log(
            f"[GIT] {description}-Repository bereits vorhanden:"
        )
        log(f"      {repo_dir}")
        return repo_dir

    # Vorhandener Ordner darf nur leer automatisch geklont werden.
    if os.path.exists(repo_dir):
        if not os.path.isdir(repo_dir):
            raise RuntimeError(
                f"{description}: Repository-Pfad ist kein Verzeichnis:\n"
                f"{repo_dir}"
            )

        try:
            with os.scandir(repo_dir) as entries:
                has_entries = next(entries, None) is not None
        except OSError as exc:
            raise RuntimeError(
                f"{description}: Repository-Ordner kann nicht "
                f"gelesen werden:\n{repo_dir}\n{exc}"
            ) from exc

        if has_entries:
            raise RuntimeError(
                f"{description}: Ordner existiert, ist aber kein "
                f"gültiges Git-Repository und nicht leer:\n{repo_dir}\n"
                "Bitte den Ordner prüfen oder manuell sichern und "
                "bereinigen. Er wurde nicht gelöscht."
            )

    parent_dir = os.path.dirname(repo_dir)

    if parent_dir:
        os.makedirs(parent_dir, exist_ok=True)

    command = [git_executable, "clone"]

    if depth is not None:
        try:
            depth_value = int(depth)
        except (TypeError, ValueError):
            depth_value = 0

        if depth_value > 0:
            command.extend(["--depth", str(depth_value)])

    command.extend([str(repo_url), repo_dir])

    log(f"[GIT] {description}: Repository wird geklont.")
    log(f"[GIT] URL: {repo_url}")
    log(f"[GIT] Ziel: {repo_dir}")

    try:
        result = subprocess.run(
            command,
            cwd=parent_dir or None,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=clone_timeout,
            shell=False,
        )

    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(
            f"{description}: Git-Clone nach "
            f"{clone_timeout} Sekunden abgebrochen.\n"
            f"URL: {repo_url}"
        ) from exc

    except OSError as exc:
        raise RuntimeError(
            f"{description}: Git konnte nicht gestartet werden.\n"
            f"{exc}"
        ) from exc

    if result.returncode != 0:
        details = (
            result.stderr or result.stdout or
            "Unbekannter Git-Fehler"
        ).strip()

        raise RuntimeError(
            f"{description}: Git-Clone fehlgeschlagen.\n"
            f"URL: {repo_url}\n"
            f"Ziel: {repo_dir}\n"
            f"{details}"
        )

    if not git_is_valid(repo_dir):
        raise RuntimeError(
            f"{description}: Nach dem Klonen ist das Ziel "
            f"kein gültiges Git-Repository:\n{repo_dir}"
        )

    log(
        f"[GIT] {description}-Repository erfolgreich geklont:"
    )
    log(f"      {repo_dir}")

    return repo_dir


# ============================================================
# SELECTED OSCAM SOURCE
# ============================================================

def get_selected_stream_repo_url():
    """Read the selected OSCam repository from config.json."""
    try:
        if os.path.isfile(CONFIG_FILE):
            with open(CONFIG_FILE, "r", encoding="utf-8") as handle:
                config = json.load(handle)

            if not isinstance(config, dict):
                return STREAMBOARD_OSCAM_URL

            selected = config.get(
                "oscam_source",
                "Streamboard Oscam",
            )

            return STREAM_REPO_CHOICES.get(
                selected,
                STREAMBOARD_OSCAM_URL,
            )

    except (OSError, json.JSONDecodeError, AttributeError):
        pass

    return STREAMBOARD_OSCAM_URL


def get_selected_stream_repo_name():
    """Return the name of the selected OSCam repository."""
    url = get_selected_stream_repo_url()

    return next(
        (
            name
            for name, repo_url in STREAM_REPO_CHOICES.items()
            if repo_url == url
        ),
        "Streamboard Oscam",
    )



# ============================================================
# ENSURE REQUIRED DIRECTORIES EXIST
# ============================================================

# Nur den Backup-Ordner automatisch erstellen.
# Git-Repository-Ordner werden erst bei Bedarf angelegt.

try:
    os.makedirs(OLD_PATCH_DIR, exist_ok=True)
except OSError as exc:
    print(
        f"[PATH] Backup-Verzeichnis konnte nicht erstellt werden: "
        f"{OLD_PATCH_DIR}: {exc}"
    )



# ============================================================
# DATE / TIME HELPER
# ============================================================

def now():
    """Return the current timestamp."""
    return datetime.now()
