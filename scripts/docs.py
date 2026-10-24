#!/usr/bin/env python3
"""Validate documentation links and export the main-branch manual to GitHub Wiki."""
import argparse
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path
import re
from urllib.parse import quote, unquote, urlsplit

import markdown
import yaml

ROOT = Path(__file__).resolve().parents[1]
CONFIG = yaml.safe_load((ROOT / 'mkdocs.yml').read_text(encoding='utf-8'))
DOCS = ROOT / CONFIG.get('docs_dir', 'docs')
LINK = re.compile(r'(!?\[[^\]\n]*\]\()([^\s)]+)(\))')


class Links(HTMLParser):
    def __init__(self, text):
        super().__init__()
        self.targets, self.ids = [], set()
        self.feed(markdown.markdown(text, extensions=['toc', 'tables', 'fenced_code']))

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if 'id' in attrs:
            self.ids.add(attrs['id'])
        key = 'src' if tag == 'img' else 'href'
        if key in attrs:
            self.targets.append(attrs[key])


def nav_items(entries):
    for entry in entries:
        for title, value in entry.items():
            if isinstance(value, list):
                yield from nav_items(value)
            else:
                yield title, value


def wiki_slug(page):
    relative = page.relative_to(DOCS)
    return 'Home' if relative == Path('index.md') else relative.with_suffix('').as_posix().replace('/', '-')


def wiki_url(slug):
    return CONFIG['repo_url'].rstrip('/') + '/wiki/' + quote(slug)


def check():
    pages = sorted(DOCS.rglob('*.md'))
    navigation = [Path(path) for _, path in nav_items(CONFIG['nav'])]
    if len(navigation) != len(pages) or set(navigation) != {page.relative_to(DOCS) for page in pages}:
        raise SystemExit('Navigation must include every documentation page exactly once')
    parsed = {page.resolve(): Links(page.read_text(encoding='utf-8')) for page in pages}
    errors = []
    for page, links in parsed.items():
        for target in links.targets:
            url = urlsplit(target)
            if url.scheme or url.netloc:
                continue
            destination = (page.parent / unquote(url.path)).resolve() if url.path else page
            if not destination.is_relative_to(ROOT) or not destination.exists():
                errors.append(f'{page.relative_to(ROOT)}: missing or external path {target}')
            elif url.fragment and destination in parsed and unquote(url.fragment) not in parsed[destination].ids:
                errors.append(f'{page.relative_to(ROOT)}: missing anchor {target}')
    if errors:
        raise SystemExit('\n'.join(errors))
    print(f'Checked navigation and local links in {len(pages)} pages.')
    return pages


def export_wiki(args):
    pages = check()
    output = Path(args.output).resolve()
    if output.exists() and any(output.iterdir()):
        raise SystemExit('Choose an empty output directory; Wiki export does not overwrite existing work.')
    slugs = {page.resolve(): wiki_slug(page) for page in pages}
    names = [*slugs.values(), '_Sidebar', '_Footer']
    if len({name.casefold() for name in names}) != len(names):
        raise SystemExit('Wiki page names collide')
    output.mkdir(parents=True, exist_ok=True)
    repo = CONFIG['repo_url'].rstrip('/')
    for page in pages:
        def replace_link(match):
            url = urlsplit(match[2])
            if url.scheme or url.netloc or not url.path:
                return match[0]
            target = (page.parent / unquote(url.path)).resolve()
            if target in slugs:
                destination = wiki_url(slugs[target])
            else:
                route = '/raw/refs/heads/main/' if match[1].startswith('!') else '/blob/main/'
                destination = repo + route + quote(target.relative_to(ROOT).as_posix())
            if url.query:
                destination += '?' + url.query
            if url.fragment:
                destination += '#' + url.fragment
            return match[1] + destination + match[3]

        chunks = re.split(r'(^```.*?^```[^\n]*$)', page.read_text(encoding='utf-8'), flags=re.M | re.S)
        for index in range(0, len(chunks), 2):
            chunks[index] = LINK.sub(replace_link, chunks[index])
        source = repo + '/blob/main/' + quote(page.relative_to(ROOT).as_posix())
        content = f'[源文档 / Source]({source})\n\n' + ''.join(chunks)
        (output / (slugs[page.resolve()] + '.md')).write_text(content, encoding='utf-8')

    sidebar = []
    def add_navigation(entries, indent=''):
        for entry in entries:
            for title, value in entry.items():
                if isinstance(value, list):
                    sidebar.append(f'{indent}- **{title}**')
                    add_navigation(value, indent + '  ')
                else:
                    sidebar.append(f'{indent}- [{title}]({wiki_url(slugs[(DOCS / value).resolve()])})')
    add_navigation(CONFIG['nav'])
    (output / '_Sidebar.md').write_text('\n'.join(sidebar) + '\n', encoding='utf-8')
    (output / '_Footer.md').write_text(
        f'内容在 [{CONFIG["site_name"]} 主仓库]({repo}/tree/main/docs)维护，由 CI 自动同步。\n',
        encoding='utf-8',
    )
    for page in output.glob('*.md'):
        for target in Links(page.read_text(encoding='utf-8')).targets:
            url = urlsplit(target)
            if not (url.scheme or url.netloc) and url.path:
                raise SystemExit(f'Relative exported Wiki link: {page.name}: {target}')
            if target.startswith(wiki_url('')) and not (output / (unquote(url.path.rsplit('/', 1)[-1]) + '.md')).exists():
                raise SystemExit(f'Broken exported Wiki link: {page.name}: {target}')
    manifest = {page.name: hashlib.sha256(page.read_bytes()).hexdigest() for page in sorted(output.glob('*.md'))}
    (output / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
    print(f'Exported {len(manifest)} Wiki pages to {output}; no remote repository was modified.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_subparsers(dest='action', required=True)
    actions.add_parser('check')
    wiki = actions.add_parser('wiki')
    wiki.add_argument('--output', required=True)
    args = parser.parse_args()
    if args.action == 'check':
        check()
    else:
        export_wiki(args)


if __name__ == '__main__':
    main()
