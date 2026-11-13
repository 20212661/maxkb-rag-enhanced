import contextlib
import io
from pathlib import Path
import tempfile
import unittest

import configure


class ConfigureTests(unittest.TestCase):
    def test_credentials_are_complete_and_preserved_on_retry(self):
        with tempfile.TemporaryDirectory() as directory, contextlib.redirect_stdout(io.StringIO()):
            target = Path(directory) / ".env"
            configure.configure(target, configure.ROOT / ".env.example")
            first = target.read_bytes()
            values = dict(line.split("=", 1) for line in first.decode().splitlines() if line and not line.startswith("#"))
            self.assertEqual(values["WEB_PORT"], "8088")
            self.assertEqual(len(values["SECRET_KEY"]), 64)
            self.assertEqual(len(values["DB_PASSWORD"]), 48)
            self.assertNotEqual(values["DB_PASSWORD"], values["REDIS_PASSWORD"])
            self.assertEqual(len(values["ADMIN_PASSWORD"]), 48)
            configure.configure(target, configure.ROOT / ".env.example")
            self.assertEqual(target.read_bytes(), first)

    def test_existing_values_comments_and_missing_settings(self):
        with tempfile.TemporaryDirectory() as directory, contextlib.redirect_stdout(io.StringIO()):
            target = Path(directory) / ".env"
            target.write_text("# user configuration\nDB_PASSWORD='keep-me'\nWEB_PORT=8099\nSECRET_KEY=\n", "utf-8")
            configure.configure(target, configure.ROOT / ".env.example")
            content = target.read_text("utf-8")
            self.assertIn("DB_PASSWORD='keep-me'\n", content)
            self.assertIn("WEB_PORT=8099\n", content)
            self.assertIn("# user configuration\n", content)
            self.assertNotIn("SECRET_KEY=\n", content)
            self.assertEqual(content.count("DB_PASSWORD="), 1)

    def test_quoted_and_unquoted_placeholders_are_generated(self):
        with tempfile.TemporaryDirectory() as directory, contextlib.redirect_stdout(io.StringIO()):
            target = Path(directory) / ".env"
            target.write_text("DB_PASSWORD=change-me\nADMIN_PASSWORD='change-me'\n"
                              'REDIS_PASSWORD="change-me"\nSECRET_KEY=  change-me  \n', "utf-8")
            configure.configure(target, configure.ROOT / ".env.example")
            content = target.read_text("utf-8")
            values = dict(line.split("=", 1) for line in content.splitlines() if line and not line.startswith("#"))
            for name, length in configure.SECRET_LENGTHS.items():
                self.assertEqual(len(values[name]), length * 2)
                self.assertNotEqual(values[name], "change-me")
            self.assertEqual(len({values[name] for name in configure.SECRET_LENGTHS}), len(configure.SECRET_LENGTHS))


if __name__ == "__main__":
    unittest.main()
