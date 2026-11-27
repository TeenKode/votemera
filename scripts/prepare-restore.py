#!/usr/bin/env python3
"""Prepare verified, closed copies of a SQLite backup in a private directory."""

import argparse
import shutil
import sqlite3
import sys
from contextlib import closing
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backup_restore import migrated_schema, prepare_access, prepare_data
from storage_schema import DATA_MIGRATIONS


def copy_and_prepare(source, target, prepare):
    if not source.is_file():
        raise ValueError(f'В копии отсутствует {source.name}')
    with closing(sqlite3.connect(source.resolve().as_uri() + '?mode=ro', uri=True)) as src:
        src.execute('PRAGMA trusted_schema=OFF')
        with closing(sqlite3.connect(target)) as dst:
            src.backup(dst)
            dst.row_factory = sqlite3.Row
            dst.execute('PRAGMA trusted_schema=OFF')
            dst.execute('PRAGMA foreign_keys=ON')
            with dst:
                dst.execute('BEGIN IMMEDIATE')
                prepare(dst)


def prepare_backup(source, target):
    full = source.is_dir()
    data = source / 'votemera.db' if full else source
    expected = migrated_schema(DATA_MIGRATIONS)
    copy_and_prepare(data, target / 'votemera.db', lambda c: prepare_data(c, expected))
    if full:
        copy_and_prepare(source / 'votemera.db.access', target / 'votemera.db.access', prepare_access)
        config = source / 'server.env'
        values = dict(line.split('=', 1) for line in config.read_text().splitlines() if '=' in line and not line.startswith('#'))
        port = values.get('VOTEMERA_PORT', '')
        if (values.get('VOTEMERA_DB') != '/var/lib/votemera/votemera.db' or
                not values.get('VOTEMERA_HOST') or not port.isascii() or not port.isdecimal() or not 1 <= int(port) <= 65535):
            raise ValueError('Несовместимый server.env в полной копии')
        shutil.copyfile(config, target / 'server.env')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('target', type=Path)
    args = parser.parse_args()
    try:
        prepare_backup(args.source, args.target)
    except (OSError, ValueError, sqlite3.Error) as exc:
        sys.exit(f'Не удалось подготовить копию: {exc}')
