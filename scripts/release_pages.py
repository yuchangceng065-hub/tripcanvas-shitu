#!/usr/bin/env python3
"""Build GitHub Pages from a committed tree and check the published release."""

import argparse
import hashlib
import io
import json
import os
import re
import subprocess
import tarfile
import tempfile
from pathlib import Path, PurePosixPath
from urllib.parse import quote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
SITE = 'https://yuchangceng065-hub.github.io/tripcanvas-demo/'
BASE = '/tripcanvas-demo/'
API = 'https://tripcanvas-api.onrender.com/api/v1'
BUILD_INPUTS = (
    'backend/places.json', 'index.html', 'package.json', 'package-lock.json', 'public', 'src',
    'tsconfig.json', 'tsconfig.node.json', 'vite.config.ts',
)


def commit_sha(ref):
    return subprocess.check_output(
        ['git', 'rev-parse', '--verify', '--end-of-options', f'{ref}^{{commit}}'],
        cwd=ROOT, text=True,
    ).strip()


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def version(command):
    return subprocess.check_output([command, '--version'], text=True).strip()


def public_https(value):
    parts = urlsplit(value)
    if (parts.scheme != 'https' or not parts.hostname or parts.username is not None
            or parts.query or parts.fragment):
        raise argparse.ArgumentTypeError('Use a public HTTPS URL without credentials, query or fragment')
    return value


def build(commit, output, api):
    output = output.resolve()
    if output.exists():
        raise ValueError('Choose a new output directory')
    output.parent.mkdir(parents=True, exist_ok=True)
    env = {key: value for key, value in os.environ.items() if not key.startswith('VITE_')}
    env.update(VITE_API_BASE=api, NODE_ENV='production')
    with tempfile.TemporaryDirectory(prefix='tripcanvas-pages-', dir=output.parent) as temporary:
        work = Path(temporary)
        archive = subprocess.check_output(
            ['git', 'archive', '--format=tar', commit, *BUILD_INPUTS], cwd=ROOT,
        )
        with tarfile.open(fileobj=io.BytesIO(archive)) as source:
            source.extractall(work, filter='data')
        subprocess.run(['npm', 'ci', '--include=dev'], cwd=work, env=env, check=True)
        subprocess.run(['npm', 'run', 'build', '--', '--base', BASE],
                       cwd=work, env=env, check=True)
        dist = work / 'dist'
        files = {path.relative_to(dist).as_posix(): sha256(path.read_bytes())
                 for path in sorted(dist.rglob('*')) if path.is_file()}
        manifest = {
            'sourceCommit': commit, 'basePath': BASE, 'apiBase': api,
            'node': version('node'), 'npm': version('npm'),
            'lockfileSha256': sha256((work / 'package-lock.json').read_bytes()),
            'files': files,
        }
        (dist / '.nojekyll').touch()
        (dist / 'release.json').write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
        dist.rename(output)
    print(f'Built {commit} into {output} ({len(files)} verified files)')


def fetch(url, *, origin=None):
    # Use the system TLS store and retry transient CDN and proxy failures.
    with tempfile.TemporaryDirectory(prefix='tripcanvas-release-check-') as temporary:
        response = Path(temporary) / 'response'
        command = [
            'curl', '--disable', '--fail', '--silent', '--show-error', '--proto', '=https',
            '--connect-timeout', '10', '--max-time', '25', '--retry', '2',
            '--retry-all-errors', '--retry-max-time', '30',
            '--header', 'Cache-Control: no-cache',
        ]
        if origin:
            command.extend([
                '--request', 'OPTIONS', '--dump-header', str(response), '--output', os.devnull,
                '--header', f'Origin: {origin}', '--header', 'Access-Control-Request-Method: POST',
                '--header', 'Access-Control-Request-Headers: authorization,content-type,last-event-id',
            ])
        else:
            command.extend(['--output', str(response)])
        subprocess.run([*command, url], check=True)
        return response.read_bytes()


def verify(commit, site, api):
    manifest = json.loads(fetch(site + 'release.json'))
    if manifest.get('sourceCommit') != commit or manifest.get('apiBase') != api:
        raise ValueError('Published Pages manifest does not match the expected source commit and API')
    if manifest.get('basePath') != urlsplit(site).path:
        raise ValueError('Published Pages base path does not match the public site URL')
    lockfile = subprocess.check_output(['git', 'show', f'{commit}:package-lock.json'], cwd=ROOT)
    if manifest.get('lockfileSha256') != sha256(lockfile):
        raise ValueError('Published manifest does not match the source lockfile')
    files = manifest['files']
    if 'index.html' not in files or not any(path.startswith('assets/') and path.endswith('.js') for path in files):
        raise ValueError('Published manifest is missing the HTML or JavaScript bundle')
    for path, digest in files.items():
        relative = PurePosixPath(path)
        if relative.is_absolute() or '..' in relative.parts:
            raise ValueError(f'Invalid asset path: {path}')
        asset = site if path == 'index.html' else site + quote(path)
        if sha256(fetch(asset)) != digest:
            raise ValueError(f'Published asset differs from the manifest: {path}')

    health = json.loads(fetch(api + '/health'))
    deployed = health.get('commit') or ''
    if not re.fullmatch(r'[0-9a-f]{7,40}', deployed) or not commit.startswith(deployed):
        raise ValueError(f'Render reports commit {deployed or "unknown"}; expected {commit}')
    if health.get('status') != 'ok' or health.get('storage') != 'postgresql':
        raise ValueError('Render health does not report a healthy PostgreSQL-backed API')
    origin = f'{urlsplit(site).scheme}://{urlsplit(site).netloc}'
    headers = fetch(api + '/planner/confirm', origin=origin).decode()
    if not any(
        name.lower() == 'access-control-allow-origin' and value.strip() == origin
        for name, _, value in (line.partition(':') for line in headers.splitlines())
    ):
        raise ValueError(f'API does not allow {origin}')
    print(f'Pages assets ({len(files)}) and Render commit verified: {commit}')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    builder = commands.add_parser('build')
    builder.add_argument('--output', type=Path, required=True)
    verifier = commands.add_parser('verify')
    verifier.add_argument('--site', default=SITE, type=public_https)
    for command in (builder, verifier):
        command.add_argument('--commit', default='HEAD')
        command.add_argument('--api-base', default=API, type=public_https)
    args = parser.parse_args()
    commit = commit_sha(args.commit)
    api = args.api_base.rstrip('/')
    try:
        if args.command == 'build':
            build(commit, args.output, api)
        else:
            verify(commit, args.site.rstrip('/') + '/', api)
    except ValueError as error:
        parser.exit(1, f'{error}\n')
    except subprocess.CalledProcessError as error:
        parser.exit(error.returncode, f'{error.cmd[0]} exited with status {error.returncode}\n')


if __name__ == '__main__':
    main()
