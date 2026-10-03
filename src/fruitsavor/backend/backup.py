"""Create a consistent, private SQLite backup without stopping the API."""
import argparse
from contextlib import closing
import os
from pathlib import Path
import sqlite3
import tempfile

from .config import Settings


def backup_database(source: Path, destination: Path) -> None:
    source = source.resolve(strict=True)
    destination = destination.absolute()
    if destination.exists() or destination.is_symlink():
        raise FileExistsError('Backup destination already exists')
    temporary = None
    try:
        # SQLite's backup API includes committed WAL content; filesystem copies do not.
        with closing(sqlite3.connect(source.as_uri() + '?mode=ro', uri=True)) as original:
            version = original.execute('PRAGMA user_version').fetchone()[0]
            tables = {row[0] for row in original.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if version != 3 or not {'fruits', 'scans', 'observations', 'observation_revisions'} <= tables:
                raise ValueError('Expected a FruitSavor schema v3 database')
            fd, filename = tempfile.mkstemp(prefix='.fruitsavor-backup-', dir=destination.parent)
            os.close(fd)
            temporary = Path(filename)
            backup = sqlite3.connect(temporary)
            try:
                original.backup(backup)
                backup.execute('PRAGMA journal_mode = DELETE')
                if backup.execute('PRAGMA integrity_check').fetchall() != [('ok',)]:
                    raise ValueError('Backup integrity check failed')
                if backup.execute('PRAGMA foreign_key_check').fetchone() is not None:
                    raise ValueError('Backup contains broken record references')
            finally:
                backup.close()
        with temporary.open('rb') as handle:
            os.fsync(handle.fileno())
        # Publish only the completed file, atomically, without overwriting a backup.
        os.link(temporary, destination)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('destination', type=Path)
    parser.add_argument('--source', type=Path, default=None)
    args = parser.parse_args(argv)
    try:
        backup_database(args.source or Settings.from_env().database, args.destination)
    except (OSError, sqlite3.Error, ValueError) as error:
        parser.exit(1, f'Backup failed: {error}\n')
    print(f'Backup saved to {args.destination}')


if __name__ == '__main__':
    main()
