#!/usr/bin/env python3
"""Fetch pinned upstream styles and build the CartoCSS compiler image."""
import argparse
from pathlib import Path
import subprocess

SOURCES = {
    'opentopomap': ('https://github.com/der-stefan/OpenTopoMap.git', '60c50cb8329d67c8556cd9f25b4a8e50bfc19c91'),
    'humanitarian': ('https://github.com/hotosm/HDM-CartoCSS.git', 'ff2bb32dc96e5e450dab052be0df0dff40f1c139'),
}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--directory', type=Path, default=Path('build/map-products/upstream'))
    a = p.parse_args()
    a.directory.mkdir(parents=True, exist_ok=True)
    for name, (url, revision) in SOURCES.items():
        target = a.directory/name
        if not target.exists():
            subprocess.run(['git', 'clone', '--no-checkout', url, str(target)], check=True)
            subprocess.run(['git', '-C', str(target), 'checkout', '--detach', revision], check=True)
        actual = subprocess.check_output(['git', '-C', str(target), 'rev-parse', 'HEAD'], text=True).strip()
        if actual != revision:
            raise SystemExit(f'{target}: expected {revision}, found {actual}; use a fresh directory')
    dockerfile = 'FROM node:20-slim\nRUN npm install -g carto@1.2.0\n'
    subprocess.run(['docker', 'build', '-t', 'carto-cli:latest', '-'], input=dockerfile, text=True, check=True)


if __name__ == '__main__':
    main()
