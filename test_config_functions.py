
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import config_manager


class ConfigFunctionTests(unittest.TestCase):

    def test_save_and_load_use_temporary_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "config.json"

            # Nur innerhalb dieses Tests auf eine temporäre Datei zeigen.
            with patch.object(config_manager, "CONFIG_FILE", str(config_path)):
                result = config_manager.save_config(
                    {
                        "language": "de",
                        "theme_mode": "standard",
                        "blink_speed": 500,
                        "led_enabled": True,
                    },
                    gui_instance=None,
                    silent=True,
                )

                self.assertTrue(result, "save_config() sollte True liefern")
                self.assertTrue(config_path.exists())

                saved = json.loads(
                    config_path.read_text(encoding="utf-8")
                )
                self.assertEqual(saved["language"], "de")
                self.assertEqual(saved["theme_mode"], "standard")
                self.assertEqual(saved["blink_speed"], 500)
                self.assertIs(saved["led_enabled"], True)

                loaded = config_manager.load_config(gui_instance=None)
                self.assertEqual(loaded["language"], "de")
                self.assertEqual(loaded["theme_mode"], "standard")
                self.assertEqual(loaded["blink_speed"], 500)
                self.assertIs(loaded["led_enabled"], True)

    def test_load_config_preserves_work_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "config.json"
            work_dir = str(Path(tmp) / "workspace")

            config_path.write_text(
                json.dumps({
                    "work_dir": work_dir,
                    "language": "de",
                }),
                encoding="utf-8",
            )

            with patch.object(
                config_manager, "CONFIG_FILE", str(config_path)
            ):
                loaded = config_manager.load_config()

            self.assertEqual(loaded["work_dir"], work_dir)

    def test_load_config_recovers_from_invalid_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "config.json"
            config_path.write_text("{kaputtes JSON", encoding="utf-8")

            with patch.object(config_manager, "CONFIG_FILE", str(config_path)):
                loaded = config_manager.load_config(gui_instance=None)

            self.assertIsInstance(loaded, dict)
            self.assertIn("language", loaded)
            self.assertIn("theme_mode", loaded)

    def test_load_config_fills_missing_defaults(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "config.json"
            config_path.write_text(
                json.dumps({"language": "de"}),
                encoding="utf-8",
            )

            with patch.object(config_manager, "CONFIG_FILE", str(config_path)):
                loaded = config_manager.load_config(gui_instance=None)

            self.assertEqual(loaded["language"], "de")
            self.assertIn("theme_mode", loaded)
            self.assertIn("blink_speed", loaded)
            self.assertIn("led_enabled", loaded)


if __name__ == "__main__":
    unittest.main(verbosity=2)
