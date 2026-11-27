#!/usr/bin/env python3
"""Require a higher VERSION whenever shipped code changes."""

import re
import subprocess
import sys


def git(*args):
    return subprocess.check_output(['git', *args], text=True)


def shipped(path):
    return (path.startswith(('static/', 'scripts/')) or
            path in ('requirements.txt', 'requirements.lock') or
            '/' not in path and path.endswith('.py'))


def version(ref):
    value = git('show', ref + ':VERSION').strip()
    if not re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+', value):
        raise ValueError(f'Некорректный VERSION: {value}')
    return tuple(map(int, value.split('.')))


if __name__ == '__main__':
    base, head = sys.argv[1:]
    if base and base != '0' * 40:
        paths = git('diff', '--name-only', '--no-renames', '-z', base, head).split('\0')
        paths = [path for path in paths if path != 'scripts/check-release-version.py' and shipped(path)]
        if paths and version(head) <= version(base):
            sys.exit('Изменился код релиза. Увеличьте VERSION перед слиянием.')
