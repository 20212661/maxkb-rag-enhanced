import contextlib
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import configure


class ConfigureTests(unittest.TestCase):
    def test_credentials_are_complete_and_preserved_on_retry(self):
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(configure, "__file__", str(Path(directory) / "configure.py")), \
                contextlib.redirect_stdout(io.StringIO()):
            configure.main()
            target = Path(directory) / ".env"
            first = target.read_bytes()
            values = dict(line.split("=", 1) for line in first.decode().splitlines())
            self.assertEqual(values["WEB_PORT"], "8088")
            self.assertEqual(len(values["SECRET_KEY"]), 64)
            self.assertEqual(len(values["DB_PASSWORD"]), 48)
            self.assertNotEqual(values["DB_PASSWORD"], values["REDIS_PASSWORD"])
            configure.main()
            self.assertEqual(target.read_bytes(), first)


if __name__ == "__main__":
    unittest.main()
