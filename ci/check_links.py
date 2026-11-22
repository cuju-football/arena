# Copyright 2026 The cuju Authors
# SPDX-License-Identifier: Apache-2.0
"""Checks the links of the page, the docs and the README, online when run by hand.

Run it as `python ci/check_links.py` to request every external link once, HEAD first and GET
when HEAD fails, with a timeout, a pause between requests and a generic User-Agent, and to check
each path of cuju's repository that tests/support/links.json lists against cuju's repository on
GitHub. The report names each link that fails or moved permanently, with the files and lines that
hold it. `python ci/check_links.py --offline` checks the repository without a network request.
The tests in `tests/render/test_links.py` check the local links, the links to cuju against
the list, the Markdown anchors and mentions of removed things. CI runs the offline check.

A removed thing is an entry of `removed` in tests/support/links.json or in a file of
tests/support/removed, with a `pattern`, a regular expression read without regard to case, and
the `reason` it left. An entry may carry `spare`, a list of expressions whose matches are words
of their own that the pattern would otherwise catch, and `files`, a list of globs that limits the
entry to some files, where a glob that starts with `!` leaves files out. The scan reads every
tracked file of text and the name of every tracked file. It leaves out the changelog and its
fragments, which tell what each release removed, the lists themselves and the code of others.
"""

import argparse
import json
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = Path('tests') / 'support' / 'links.json'
REMOVED = Path('tests') / 'support' / 'removed'
# The fragments of the changelog, which ci/fragments.py folds into it.
CHANGES = 'changes'
# Folders that hold no file of the repository, for a tree that git does not list.
UNTRACKED = {'.git', '__pycache__', '.pytest_cache', '.ruff_cache', 'node_modules', '.venv'}
UNTRACKED |= {'build', 'dist', 'wheelhouse'}
WEB = Path('src') / 'cuju' / 'arena' / 'web'
OWNER = 'cuju-football'
HERE = 'arena'
CUJU = 'engine'
BRANCH = 'main'
# A generic client name, which names no person.
USER_AGENT = 'link-check/1.0 (Python urllib)'
TIMEOUT_SECONDS = 20
PAUSE_SECONDS = 0.5
REDIRECTS = 5

FENCE = re.compile(r'^\s{0,3}(```|~~~)')
CODE_SPAN = re.compile(r'(`+).+?\1')
INLINE = re.compile(r'\]\(\s*<?([^)\s>]+)>?')
REFERENCE = re.compile(r'^\s{0,3}\[[^\]]+\]:\s*<?([^\s>]+)>?')
ATTRIBUTE = re.compile(r'\b(?:href|src)\s*=\s*["\']([^"\']+)["\']')
PROPERTY = re.compile(r'\b(?:href|src)\s*:\s*[\'"]([^\'"]+)[\'"]')
MANIFEST = re.compile(r'"(?:src|url|start_url|scope)"\s*:\s*"([^"]+)"')
BARE = re.compile(r'https?://[^\s<>"\'`)\]}|\\]+')
HEADING = re.compile(r'^\s{0,3}(#{1,6})\s+(.*?)(?:\s+#+)?\s*$')
NAMED = re.compile(r'<a\s+(?:id|name)="([^"]+)"')


@dataclass(frozen=True)
class Link:
    """A link as a file holds it, with the folder that a relative link starts from."""

    file: Path
    line: int
    url: str
    base: Path

    def where(self, root):
        return f'{self.file.relative_to(root).as_posix()}:{self.line}'


def load(root):
    return json.loads((root / DATA).read_text(encoding='utf-8'))


def markdown_files(root):
    """Returns the Markdown files at the top and those of docs, translations and .github."""
    files = sorted(root.glob('*.md'))
    for folder in ('docs', 'translations', '.github'):
        files += sorted((root / folder).rglob('*.md'))
    return files


def page_files(root):
    """Returns the files of the page that hold links, the vendored code of others left out."""
    web = root / WEB
    files = [web / 'index.html', web / 'manifest.webmanifest', web / 'lessons.json']
    files += sorted((web / 'languages').glob('*.json'))
    files += sorted((root / 'translations').rglob('*.json'))
    files += sorted(p for p in (web / 'js').rglob('*.js') if not p.name.startswith('vendor'))
    return [path for path in files if path.is_file()]


def other_files(root):
    """Returns the files of the package's metadata, which name the project's addresses."""
    names = ['pyproject.toml', 'CITATION.cff', '.github/ISSUE_TEMPLATE/config.yml']
    return [root / name for name in names if (root / name).is_file()]


def clean(url):
    """Drops the punctuation of the sentence that ends a bare address."""
    url = url.rstrip('.,;:!?*_')
    while url.endswith(')') and url.count('(') < url.count(')'):
        url = url[:-1]
    return url


def markdown_links(path):
    links = []
    fenced = False
    for number, line in enumerate(path.read_text(encoding='utf-8').splitlines(), 1):
        if FENCE.match(line):
            fenced = not fenced
            continue
        if fenced:
            continue
        text = CODE_SPAN.sub('', line)
        found = INLINE.findall(text) + REFERENCE.findall(text) + ATTRIBUTE.findall(text)
        found += [clean(url) for url in BARE.findall(text)]
        for url in dict.fromkeys(found):
            links.append(Link(path, number, url, path.parent))
    return links


def page_links(path, web):
    """Returns the links of a file of the page, whose relative links start at the page's folder."""
    links = []
    text = path.read_text(encoding='utf-8')
    for number, line in enumerate(text.splitlines(), 1):
        found = ATTRIBUTE.findall(line)
        if path.suffix == '.js':
            found += PROPERTY.findall(line)
        if path.suffix in ('.json', '.webmanifest'):
            found += INLINE.findall(line) + MANIFEST.findall(line)
        for match in BARE.finditer(line):
            # An address that a template string completes is no link of its own.
            if line[match.end() : match.end() + 2] != '${':
                found.append(clean(match.group()))
        for url in dict.fromkeys(found):
            links.append(Link(path, number, url, web))
    return links


def collect(root):
    """Returns every link of the page, the docs, the README and the metadata."""
    links = []
    for path in markdown_files(root):
        links += markdown_links(path)
    for path in page_files(root):
        links += page_links(path, root / WEB)
    for path in other_files(root):
        links += [
            Link(path, number, clean(url), path.parent)
            for number, line in enumerate(path.read_text(encoding='utf-8').splitlines(), 1)
            for url in dict.fromkeys(BARE.findall(line))
        ]
    return links


def slug(heading):
    """Returns the anchor that GitHub gives a heading of a Markdown file."""
    text = re.sub(r'!?\[([^\]]*)\]\([^)]*\)', r'\1', heading)
    text = re.sub(r'\[([^\]]*)\]\[[^\]]*\]', r'\1', text)
    text = re.sub(r'<[^>]+>', '', text).replace('`', '').replace('*', '')
    text = re.sub(r'[^\w\- ]', '', text.strip().lower())
    return text.replace(' ', '-')


def anchors_of(text):
    """Returns the anchors of a Markdown text, its headings counted as GitHub counts them."""
    names, counts = set(), {}
    fenced = False
    for line in text.splitlines():
        if FENCE.match(line):
            fenced = not fenced
            continue
        if fenced:
            continue
        heading = HEADING.match(line)
        if heading:
            name = slug(heading.group(2))
            seen = counts.get(name, 0)
            names.add(name if not seen else f'{name}-{seen}')
            counts[name] = seen + 1
        names.update(NAMED.findall(line))
    return names


class Anchors:
    """The anchors of the files of the repository, read once each."""

    def __init__(self):
        self.held = {}

    def of(self, path):
        if path not in self.held:
            self.held[path] = anchors_of(path.read_text(encoding='utf-8'))
        return self.held[path]

    def ids(self, path, root):
        """Returns the ids of the page, with the symbols that core/icons.js adds to its sprite."""
        key = (path, 'ids')
        if key not in self.held:
            text = path.read_text(encoding='utf-8')
            source = root / WEB / 'js' / 'core' / 'icons.js'
            icons = source.read_text(encoding='utf-8') if source.is_file() else ''
            table = icons.partition('export const SYMBOLS = {')[2].partition('\n};')[0]
            names = re.findall(r"'?([\w-]+)'?\s*:\s*'[\w-]+'", table)
            self.held[key] = set(re.findall(r'\bid="([^"]+)"', text)) | {f'i-{n}' for n in names}
        return self.held[key]


def repository_link(parsed):
    """Returns the repository and the rest of the path of a link into one of the two repositories,
    with a raw file written as a file on its branch, or None for any other link."""
    parts = [urllib.parse.unquote(part) for part in parsed.path.split('/') if part]
    if len(parts) < 2 or parts[0] != OWNER:
        return None
    repository = parts[1].split('@')[0].removesuffix('.git')
    rest = parts[2:]
    if parsed.hostname == 'raw.githubusercontent.com':
        if rest[:2] == ['refs', 'heads']:
            rest = rest[2:]
        rest = ['blob', *rest]
    elif parsed.hostname != 'github.com':
        return None
    return repository, rest


def cuju_key(rest, fragment):
    """Returns how links.json names a path of cuju's repository."""
    return '/'.join(rest) + (f'#{fragment}' if fragment else '')


def skipped(host, data):
    return host in {entry['host'] for entry in data['skip']}


def problem_of(link, root, data, anchors, used):
    """Returns what is wrong with a link, or None, and notes each path of cuju that a link uses."""
    url = link.url
    if url.startswith(('mailto:', 'data:', 'javascript:', 'blob:', 'tel:')):
        return None
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme in ('http', 'https'):
        host = parsed.hostname or ''
        if skipped(host, data):
            return None
        target = repository_link(parsed)
        if target is None:
            return None
        repository, rest = target
        if repository == CUJU:
            key = cuju_key(rest, parsed.fragment)
            used.add(key)
            if key not in data['cuju']:
                return f'links into cuju at {key!r}, which {DATA.as_posix()} does not list'
            return None
        if repository == 'render':
            return None
        if repository != HERE:
            return f'links into an unknown repository {repository!r}'
        if len(rest) < 2 or rest[0] not in ('blob', 'tree'):
            return None
        if rest[1] != BRANCH:
            return f'links to the branch {rest[1]!r}, where links into this repository name main'
        path = root.joinpath(*rest[2:])
        return local_problem(path, parsed.fragment, root, anchors)
    if parsed.scheme or url.startswith('//'):
        return None
    target, _, fragment = url.partition('#')
    target = target.partition('?')[0]
    if '{' in target or '$' in target:
        return None
    if not target:
        if link.file.suffix == '.md' and fragment:
            if fragment not in anchors.of(link.file):
                return f'names the anchor #{fragment}, which no heading of the file gives'
        elif link.file.suffix == '.html' and fragment and '=' not in fragment:
            if fragment not in anchors.ids(link.file, root):
                return f'names the id #{fragment}, which the page lacks'
        return None
    if target.startswith('/'):
        if link.base == root / WEB:
            # A path from the top of the server, such as an address of the API.
            return None
        path = root / urllib.parse.unquote(target.lstrip('/'))
    else:
        path = link.base / urllib.parse.unquote(target)
    return local_problem(path, fragment, root, anchors)


def local_problem(path, fragment, root, anchors):
    path = Path(str(path))
    if not path.exists():
        try:
            name = path.resolve().relative_to(root.resolve()).as_posix()
        except ValueError:
            name = str(path)
        return f'names {name}, which the repository lacks'
    if fragment and path.suffix == '.md' and fragment not in anchors.of(path):
        return f'names the anchor #{fragment}, which no heading of {path.name} gives'
    return None


def vendored(name):
    """Returns whether a path of the repository holds the code of others, kept as it came."""
    parts = name.split('/')
    return 'vendor' in parts[:-1] or parts[-1].startswith('vendor')


def tracked_files(root):
    """Returns the path of every tracked file from the root, with forward slashes, sorted.

    Git lists the files of a checkout, those it tracks and those that wait to be added. A tree
    without git, such as a copy or the made-up tree of a test, gives every file outside the
    folders that hold caches, builds and environments.
    """
    root = Path(root)
    if (root / '.git').exists():
        command = ['git', '-C', str(root), 'ls-files', '-z', '--cached', '--others']
        try:
            listed = subprocess.run(
                [*command, '--exclude-standard'], capture_output=True, check=False
            )
        except OSError:
            listed = None
        if listed is not None and listed.returncode == 0:
            names = [name for name in listed.stdout.decode('utf-8').split('\0') if name]
            return sorted(name for name in names if (root / name).is_file())
    names = []
    for path in root.rglob('*'):
        parts = path.relative_to(root).parts
        # The code of others keeps its own folders, such as the build of three.js.
        own = parts[: parts.index('vendor')] if 'vendor' in parts else parts
        if any(part in UNTRACKED or part.endswith('.egg-info') for part in own):
            continue
        if path.is_file():
            names.append('/'.join(parts))
    return sorted(names)


def text_of(path):
    """Returns the text of a file, or None for a file of bytes, such as a picture or a model."""
    try:
        data = Path(path).read_bytes()
    except OSError:
        return None
    if b'\0' in data[:8192]:
        return None
    try:
        return data.decode('utf-8').replace('\r\n', '\n')
    except UnicodeDecodeError:
        return None


def glob_expression(glob):
    """Returns the expression of a glob of paths, where ** crosses folders and * stays in one."""
    parts = re.split(r'(\*\*/|\*\*|\*|\?)', glob)
    words = {'**/': '(?:.*/)?', '**': '.*', '*': '[^/]*', '?': '[^/]'}
    return re.compile(''.join(words.get(part) or re.escape(part) for part in parts) + r'\Z')


def in_globs(name, globs):
    """Returns whether a path is among the globs, where a glob that starts with ! leaves it out."""
    wanted = [glob for glob in globs if not glob.startswith('!')]
    unwanted = [glob[1:] for glob in globs if glob.startswith('!')]
    if any(glob_expression(glob).match(name) for glob in unwanted):
        return False
    return not wanted or any(glob_expression(glob).match(name) for glob in wanted)


def removed_lists(root):
    """Returns the files that list removed things, tests/support/links.json first."""
    root = Path(root)
    return [root / DATA, *sorted((root / REMOVED).glob('*.json'))]


def removed_entries(root, data=None):
    """Returns every removed thing as the file that lists it and its entry."""
    root = Path(root)
    entries = []
    for path in removed_lists(root):
        if path == root / DATA:
            listed = (data if data is not None else load(root)).get('removed', [])
        else:
            listed = json.loads(path.read_text(encoding='utf-8')).get('removed', [])
        entries += [(path.relative_to(root).as_posix(), entry) for entry in listed]
    return entries


def history(name):
    """Returns whether a file tells what each release changed, the changelog or a fragment."""
    return name == 'CHANGELOG.md' or (name.startswith(f'{CHANGES}/') and name.endswith('.md'))


def removed_scope(root):
    """Returns the tracked files that the scan of removed things reads."""
    root = Path(root)
    lists = {path.relative_to(root).as_posix() for path in removed_lists(root)}
    return [
        name
        for name in tracked_files(root)
        if not history(name) and name not in lists and not vendored(name)
    ]


class Removed:
    """A removed thing, its pattern, the words it spares and the files it reads."""

    def __init__(self, source, entry):
        self.source = source
        self.entry = entry
        self.pattern = re.compile(entry['pattern'], re.IGNORECASE)
        self.spares = [re.compile(spare, re.IGNORECASE) for spare in entry.get('spare', [])]
        self.globs = entry.get('files', [])
        self.spared = set()

    def reads(self, name):
        return in_globs(name, self.globs)

    def found(self, text):
        """Returns whether a line or a name mentions the thing outside the words it spares."""
        if not self.pattern.search(text):
            return False
        for index, spare in enumerate(self.spares):
            text, count = spare.subn(' ', text)
            if count:
                self.spared.add(index)
        return bool(self.pattern.search(text))

    def idle(self):
        """Returns the spared words that no file holds, which are stale themselves."""
        spares = self.entry.get('spare', [])
        return [spare for index, spare in enumerate(spares) if index not in self.spared]


def removed_problems(root, data):
    """Returns each mention of a removed thing in a tracked file or in the name of one.

    The changelog and its fragments stay out, since they tell what each release changed, removals
    included, and so do the lists of removed things and the code of others. A spared word that no
    file holds any longer is a problem of its own, so a list never outlives what it spares.
    """
    root = Path(root)
    things = [Removed(source, entry) for source, entry in removed_entries(root, data)]
    problems = []
    for name in removed_scope(root):
        reading = [thing for thing in things if thing.reads(name)]
        for thing in reading:
            if thing.found(name):
                entry = thing.entry
                problems.append(f'{name}: is named after {entry["pattern"]}, {entry["reason"]}')
        text = text_of(root / name) if reading else None
        if text is None:
            continue
        reading = [thing for thing in reading if thing.pattern.search(text)]
        if not reading:
            continue
        for number, line in enumerate(text.splitlines(), 1):
            for thing in reading:
                if thing.found(line):
                    entry = thing.entry
                    problems.append(
                        f'{name}:{number}: mentions {entry["pattern"]}, {entry["reason"]}'
                    )
    for thing in things:
        for spare in thing.idle():
            problems.append(
                f'{thing.source}: spares {spare} for {thing.entry["pattern"]}, which no file needs'
            )
    return problems


def offline_problems(root=ROOT):
    """Returns every problem that the check finds without the network, with its file and line."""
    root = Path(root)
    data = load(root)
    anchors = Anchors()
    used = set()
    problems = []
    for link in collect(root):
        problem = problem_of(link, root, data, anchors, used)
        if problem:
            problems.append(f'{link.where(root)}: {link.url}, {problem}')
    for key in data['cuju']:
        if key not in used:
            problems.append(f'{DATA.as_posix()}: lists {key!r} of cuju, which no link uses')
    return problems + removed_problems(root, data)


class _Stay(urllib.request.HTTPRedirectHandler):
    """Hands every redirect back, so the check sees whether it is permanent."""

    def redirect_request(self, *args, **kwargs):
        return None


_OPENER = urllib.request.build_opener(_Stay)


def fetch(url, method):
    """Returns the status, the Location or the error, and up to a megabyte of the body."""
    request = urllib.request.Request(
        url, method=method, headers={'User-Agent': USER_AGENT, 'Accept': '*/*'}
    )
    try:
        with _OPENER.open(request, timeout=TIMEOUT_SECONDS) as response:
            body = response.read(1 << 20) if method == 'GET' else b''
            return response.status, None, body
    except urllib.error.HTTPError as error:
        return error.code, error.headers.get('Location'), b''
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        return None, str(getattr(error, 'reason', error)), b''


def check_online(url):
    """Returns what is wrong with an external address, or None, and the body of its answer."""
    current = url
    for _ in range(REDIRECTS + 1):
        status, detail, body = fetch(current, 'HEAD')
        if status is None or status >= 400:
            time.sleep(PAUSE_SECONDS)
            status, detail, body = fetch(current, 'GET')
        if status in (301, 308):
            return f'moved permanently to {urllib.parse.urljoin(current, detail or "")}', body
        if status in (302, 303, 307) and detail:
            current = urllib.parse.urljoin(current, detail)
            time.sleep(PAUSE_SECONDS)
            continue
        if status is None:
            return f'failed, {detail}', body
        if status >= 400:
            return f'answered {status}', body
        return None, body
    return f'redirected more than {REDIRECTS} times', b''


def external(links, data):
    """Returns the external addresses without their anchors, each with the links that hold it."""
    found = {}
    for link in links:
        parsed = urllib.parse.urlsplit(link.url)
        if parsed.scheme not in ('http', 'https') or skipped(parsed.hostname or '', data):
            continue
        if '{' in link.url:
            continue
        found.setdefault(urllib.parse.urldefrag(link.url).url, []).append(link)
    return found


def cuju_problems(data):
    """Checks each path of cuju's repository that the list names, its anchor included."""
    problems = []
    for key in data['cuju']:
        rest, _, fragment = key.partition('#')
        url = f'https://github.com/{OWNER}/{CUJU}' + (f'/{rest}' if rest else '')
        problem, _ = check_online(url)
        time.sleep(PAUSE_SECONDS)
        parts = rest.split('/')
        if problem is None and fragment and parts[:2] == ['blob', BRANCH]:
            raw = f'https://raw.githubusercontent.com/{OWNER}/{CUJU}/{BRANCH}/'
            problem, body = check_online(raw + '/'.join(parts[2:]))
            time.sleep(PAUSE_SECONDS)
            if problem is None and fragment not in anchors_of(body.decode('utf-8', 'replace')):
                problem = f'has no anchor #{fragment}'
        if problem:
            problems.append(f'{DATA.as_posix()}: cuju {key!r}, {url}, {problem}')
    return problems


def online_problems(root=ROOT, report=print):
    root = Path(root)
    data = load(root)
    problems = []
    found = external(collect(root), data)
    report(f'Requesting {len(found)} external addresses')
    for url, links in sorted(found.items()):
        problem, _ = check_online(url)
        time.sleep(PAUSE_SECONDS)
        if problem:
            places = ', '.join(link.where(root) for link in links)
            problems.append(f'{url}, {problem}, in {places}')
    report(f'Checking the {len(data["cuju"])} paths of cuju in {DATA.as_posix()}')
    return problems + cuju_problems(data)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--offline', action='store_true', help='make no request')
    options = parser.parse_args(argv)
    problems = offline_problems()
    if not options.offline:
        problems += online_problems()
    for problem in problems:
        print(problem)
    print(f'{len(problems)} problems' if problems else 'Every link holds')
    return 1 if problems else 0


if __name__ == '__main__':
    sys.exit(main())
