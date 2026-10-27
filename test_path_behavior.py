
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import config_manager


class WindowsPathBehaviorTests(unittest.TestCase):

    def test_windows_path_is_changed_on_linux(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "config.json"
            original_path = r"C:\opt\ncam"

            config_path.write_text(
                json.dumps({
                    "ncam_custom_path": original_path,
                    "language": "de",
                }),
                encoding="utf-8",
            )

            with (
                patch.object(config_manager, "CONFIG_FILE", str(config_path)),
                patch.object(config_manager.platform, "system", return_value="Linux"),
            ):
                loaded = config_manager.load_config()

            print("Original:", repr(original_path))
            print("Geladen: ", repr(loaded["ncam_custom_path"]))

            self.assertEqual(
                loaded["ncam_custom_path"],
                original_path,
                "Windows-Pfade dürfen unter Linux nicht verändert werden."
            )
    def test_windows_path_is_preserved_on_windows(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "config.json"
            original_path = r"C:\opt\ncam"

            config_path.write_text(
                json.dumps({
                    "ncam_custom_path": original_path,
                    "language": "de",
                }),
                encoding="utf-8",
            )

            with (
                patch.object(
                    config_manager, "CONFIG_FILE", str(config_path)
                ),
                patch.object(
                    config_manager.platform,
                    "system",
                    return_value="Windows",
                ),
            ):
                loaded = config_manager.load_config()

            self.assertEqual(
                loaded["ncam_custom_path"],
                original_path,
                "Windows-Pfade dürfen unter Windows nicht verändert werden.",
            )

if __name__ == "__main__":
    unittest.main(verbosity=2)
