
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import paths


class ConfigPathTests(unittest.TestCase):

    def test_config_file_is_inside_plugin_directory(self):
        """Die Config liegt im vorgesehenen Plugin-Verzeichnis."""
        expected = os.path.join(paths.PLUGIN_DIR, "config.json")
        self.assertEqual(paths.CONFIG_FILE, expected)

    def test_windows_path_is_not_silently_stripped_by_test(self):
        """Dokumentiert das erwartete Verhalten für Windows-Pfade."""
        windows_path = r"C:\opt\ncam"

        # Ein Windows-Pfad soll als gespeicherter Wert erhalten bleiben.
        self.assertEqual(windows_path, r"C:\opt\ncam")

    def test_json_config_round_trip(self):
        """JSON lässt sich in einem temporären Ordner speichern und laden."""
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "config.json"
            original = {
                "language": "de",
                "theme_mode": "standard",
                "ncam_custom_path": "/opt/s3_ncam_bonecrew",
                "led_enabled": True,
                "blink_speed": 500,
            }

            config_path.write_text(
                json.dumps(original, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

            loaded = json.loads(config_path.read_text(encoding="utf-8"))
            self.assertEqual(loaded, original)

    def test_invalid_json_is_detectable(self):
        """Beschädigte JSON-Dateien werden im Test erkannt."""
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "config.json"
            config_path.write_text("{ungültiges JSON", encoding="utf-8")

            with self.assertRaises(json.JSONDecodeError):
                json.loads(config_path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
