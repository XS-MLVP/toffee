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
FENCED = re.compile(r'(^```.*?^```[^\n]*$)', re.M | re.S)


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


def languages():
    for plugin in CONFIG['plugins']:
        if isinstance(plugin, dict) and 'i18n' in plugin:
            return plugin['i18n']['languages']
    raise SystemExit('Configure the documentation languages in mkdocs.yml')


def wiki_slug(page):
    relative = page.relative_to(DOCS)
    locale, *parts = relative.parts
    path = Path(*parts)
    suffix = 'Home' if path == Path('index.md') else path.with_suffix('').as_posix().replace('/', '-')
    return f'{locale}-{suffix}'


def wiki_url(slug):
    return CONFIG['repo_url'].rstrip('/') + '/wiki/' + quote(slug)


def check():
    pages = sorted(DOCS.rglob('*.md'))
    navigation = [Path(path) for _, path in nav_items(CONFIG['nav'])]
    locales = languages()
    expected = {Path(lang['locale']) / path for lang in locales for path in navigation}
    if len(navigation) != len(set(navigation)) or expected != {page.relative_to(DOCS) for page in pages}:
        raise SystemExit('Navigation and translations must include every page exactly once in each language')
    baseline = next(lang['locale'] for lang in locales if lang.get('default'))
    errors = []
    for path in navigation:
        original = FENCED.findall((DOCS / baseline / path).read_text(encoding='utf-8'))
        for lang in locales:
            translated = DOCS / lang['locale'] / path
            if FENCED.findall(translated.read_text(encoding='utf-8')) != original:
                errors.append(f'{translated.relative_to(ROOT)}: translated code examples differ from {baseline}')
    def check_titles(entries, translations, locale):
        for entry in entries:
            for title, value in entry.items():
                if title not in translations:
                    errors.append(f'{locale}: missing navigation translation for {title}')
                if isinstance(value, list):
                    check_titles(value, translations, locale)
    for lang in locales:
        if not lang.get('default'):
            check_titles(CONFIG['nav'], lang.get('nav_translations', {}), lang['locale'])
    parsed = {page.resolve(): Links(page.read_text(encoding='utf-8')) for page in pages}
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
    print(f'Checked translations, shared examples, navigation, and local links in {len(pages)} pages.')
    return pages


def export_wiki(args):
    pages = check()
    output = Path(args.output).resolve()
    if output.exists() and any(output.iterdir()):
        raise SystemExit('Choose an empty output directory; Wiki export does not overwrite existing work.')
    slugs = {page.resolve(): wiki_slug(page) for page in pages}
    names = [*slugs.values(), 'Home', '_Sidebar', '_Footer']
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

        chunks = FENCED.split(page.read_text(encoding='utf-8'))
        for index in range(0, len(chunks), 2):
            chunks[index] = LINK.sub(replace_link, chunks[index])
        source = repo + '/blob/main/' + quote(page.relative_to(ROOT).as_posix())
        relative = page.relative_to(DOCS)
        switches = [f'[源文档 / Source]({source})']
        for lang in languages():
            if lang['locale'] != relative.parts[0]:
                counterpart = DOCS / lang['locale'] / Path(*relative.parts[1:])
                switches.append(f'[{lang["name"]}]({wiki_url(slugs[counterpart.resolve()])})')
        content = ' · '.join(switches) + '\n\n' + ''.join(chunks)
        (output / (slugs[page.resolve()] + '.md')).write_text(content, encoding='utf-8')

    home = [f'# {CONFIG["site_name"]}', '', '选择语言 / Choose a language:', '']
    sidebar = [f'[首页 / Home]({wiki_url("Home")})', '']
    def add_navigation(entries, lang, indent=''):
        for entry in entries:
            for title, value in entry.items():
                title = lang.get('nav_translations', {}).get(title, title)
                if isinstance(value, list):
                    sidebar.append(f'{indent}- **{title}**')
                    add_navigation(value, lang, indent + '  ')
                else:
                    slug = slugs[(DOCS / lang['locale'] / value).resolve()]
                    sidebar.append(f'{indent}- [{title}]({wiki_url(slug)})')
    for lang in languages():
        home.append(f'- [{lang["name"]}]({wiki_url(wiki_slug(DOCS / lang["locale"] / "index.md"))})')
        sidebar.extend([f'## {lang["name"]}', ''])
        add_navigation(CONFIG['nav'], lang)
        sidebar.append('')
    (output / 'Home.md').write_text('\n'.join(home) + '\n', encoding='utf-8')
    (output / '_Sidebar.md').write_text('\n'.join(sidebar) + '\n', encoding='utf-8')
    (output / '_Footer.md').write_text(
        f'内容由 CI 自动同步；请编辑[主仓库文档]({repo}/tree/main/docs)。 '
        f'Automatically synced by CI; edit the [source documentation]({repo}/tree/main/docs).\n',
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
