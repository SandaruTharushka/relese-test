import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import runtime_paths


class RuntimePathsTests(unittest.TestCase):
    def test_portable_runtime_dir_requires_writable_directory(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            exe_dir = Path(temp_dir)
            exe_dir.joinpath('.env').write_text('DATABASE_FILE=supermart.db\n', encoding='utf-8')

            with patch('runtime_paths.os.access', return_value=False):
                self.assertIsNone(runtime_paths._portable_runtime_dir(exe_dir))

    def test_portable_runtime_dir_uses_writable_marker_directory(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            exe_dir = Path(temp_dir)
            exe_dir.joinpath('supermart.db').write_text('', encoding='utf-8')

            with patch('runtime_paths.os.access', return_value=True):
                self.assertEqual(runtime_paths._portable_runtime_dir(exe_dir), exe_dir)

    def test_migrate_legacy_runtime_artifacts_copies_database_and_env(self):
        with tempfile.TemporaryDirectory() as source_dir, tempfile.TemporaryDirectory() as target_dir:
            exe_dir = Path(source_dir)
            runtime_dir = Path(target_dir)
            exe_dir.joinpath('.env').write_text('DATABASE_FILE=legacy.db\n', encoding='utf-8')
            exe_dir.joinpath('supermart.db').write_text('legacy-db', encoding='utf-8')

            runtime_paths._migrate_legacy_runtime_artifacts(exe_dir, runtime_dir)

            self.assertEqual(
                runtime_dir.joinpath('.env').read_text(encoding='utf-8'),
                'DATABASE_FILE=legacy.db\n',
            )
            self.assertEqual(
                runtime_dir.joinpath('supermart.db').read_text(encoding='utf-8'),
                'legacy-db',
            )


if __name__ == '__main__':
    unittest.main()
