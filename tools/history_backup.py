"""Online SQLite backup, safe with a live WAL database. Never overwrites a file."""
import argparse
from datetime import datetime, timezone
from pathlib import Path
import sqlite3

parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('database',type=Path)
parser.add_argument('directory',type=Path)
args=parser.parse_args()
if not args.database.is_file():parser.error('Source database does not exist')
args.directory.mkdir(mode=0o700,parents=True,exist_ok=True)
destination=args.directory/('community-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')+'.sqlite3')
destination.touch(mode=0o600,exist_ok=False)
with sqlite3.connect(args.database.resolve().as_uri()+'?mode=ro',uri=True) as source:
    with sqlite3.connect(destination) as backup:
        source.backup(backup,pages=128,sleep=.01)
        assert backup.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
print(destination)
