"""User documentation links and self-contained examples stay usable."""
from pathlib import Path
import re
from urllib.parse import unquote, urlsplit

import pytest


ROOT = Path(__file__).resolve().parents[2]
PYTHON = re.compile(r"```python\n(.*?)\n```", re.DOTALL)
LINK = re.compile(r"(?<!!)\[[^\]\n]+\]\(([^\s)]+)\)")


def user_pages():
    return [ROOT / name for name in (
        'README.md', 'README.en.md', 'CONTRIBUTING.md', 'tests/README.md',
        'examples/README.md', 'benchmarks/README.md',
    )] + sorted((ROOT / 'docs').rglob('*.md'))


@pytest.mark.parametrize('name', (
    'coverage-classes.md', 'coverage-transactions.md', 'coverage-patterns.md',
))
@pytest.mark.parametrize('language', ('zh', 'en'))
def test_first_coverage_definition_uses_the_real_public_api(name, language):
    path = ROOT / 'docs' / language / 'guides' / name
    code = PYTHON.search(path.read_text())
    assert code is not None, path
    namespace = {'__name__': '__main__'}
    exec(compile(code[1], str(path), 'exec'), namespace)
    if 'PipelineCoverage' in namespace:
        coverage = namespace['coverage']
        assert coverage.state.count(namespace['StatePoint'].busy) == 1
        assert coverage.state.count(namespace['StatePoint'].path) == 1
    if 'ProtocolCoverage' in namespace:
        assert 'tag_one_fast' in namespace['ProtocolCoverage'].compile().explain()


def test_user_documentation_links_point_to_existing_files():
    for page in user_pages():
        for target in LINK.findall(page.read_text()):
            url = urlsplit(target.strip('<>'))
            if url.scheme or url.netloc:
                prefix = '/XS-MLVP/toffee/blob/main/'
                if url.netloc == 'github.com' and url.path.startswith(prefix):
                    path = ROOT / unquote(url.path.removeprefix(prefix))
                    assert path.exists(), (page, target)
                continue
            if url.path:
                assert (page.parent / unquote(url.path)).exists(), (page, target)


def test_user_documentation_has_no_abandoned_branch_or_machine_paths():
    for page in user_pages():
        text = page.read_text()
        for obsolete in ('/blob/draft/', '/tmp/toffee', '/home/xyl/'):
            assert obsolete not in text, (page, obsolete)
