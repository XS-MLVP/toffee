"""Wiki links resolve to generated pages or main-branch source files."""
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch


SPEC = importlib.util.spec_from_file_location(
    'docs', Path(__file__).resolve().parents[2] / 'scripts/docs.py'
)
docs = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(docs)


class WikiExport(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name).resolve()
        self.repo = 'https://github.com/XS-MLVP/toffee'
        config = {'site_name': 'Toffee', 'repo_url': self.repo,
                  'nav': [{'Home': 'index.md'}, {'Guide': 'guide.md'}]}
        self.enterContext(patch.multiple(docs, ROOT=self.root, DOCS=self.root / 'docs', CONFIG=config))
        self.enterContext(contextlib.redirect_stdout(io.StringIO()))
        (self.root / 'docs').mkdir()
        (self.root / 'LICENSE').write_text('License')
        (self.root / 'test image.png').write_bytes(b'image')
        (self.root / 'docs/index.md').write_text(
            '# Home\n\n[Guide](guide.md#install)\n\n[License](../LICENSE)\n\n'
            '![Image](../test%20image.png)\n\n```text\n[Literal](missing.md)\n```\n'
        )
        (self.root / 'docs/guide.md').write_text('# Guide\n\n## Install\n\n[Home](index.md)\n')

    def test_export_preserves_code_and_rewrites_links_images_and_navigation(self):
        output = self.root / 'export'
        docs.export_wiki(SimpleNamespace(output=output))
        home = (output / 'Home.md').read_text()
        self.assertIn(f'[Guide]({self.repo}/wiki/guide#install)', home)
        self.assertIn(f'[License]({self.repo}/blob/main/LICENSE)', home)
        self.assertIn(f'![Image]({self.repo}/raw/refs/heads/main/test%20image.png)', home)
        self.assertIn('```text\n[Literal](missing.md)\n```', home)
        self.assertIn(f'[Guide]({self.repo}/wiki/guide)', (output / '_Sidebar.md').read_text())
        self.assertEqual(len(json.loads((output / 'manifest.json').read_text())), 4)

    def test_missing_links_fail_before_export(self):
        (self.root / 'docs/guide.md').write_text('# Guide\n\n[Missing](missing.md)\n')
        with self.assertRaisesRegex(SystemExit, 'missing or external path'):
            docs.export_wiki(SimpleNamespace(output=self.root / 'export'))
        self.assertFalse((self.root / 'export').exists())

    def test_missing_anchor_is_rejected(self):
        (self.root / 'docs/guide.md').write_text('# Guide\n\n[Missing](index.md#missing)\n')
        with self.assertRaisesRegex(SystemExit, 'missing anchor'):
            docs.check()

    def test_unconverted_reference_link_fails_instead_of_publishing_broken_page(self):
        (self.root / 'docs/guide.md').write_text('# Guide\n\n## Install\n\n[Home][home]\n\n[home]: index.md\n')
        with self.assertRaisesRegex(SystemExit, 'Relative exported Wiki link'):
            docs.export_wiki(SimpleNamespace(output=self.root / 'export'))


if __name__ == '__main__':
    unittest.main()
