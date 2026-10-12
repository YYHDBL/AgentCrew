"""真实SQLite迁移期间停止进程，供SIGKILL原子性验收。"""

import os
import signal
import sys
from pathlib import Path

from agentcrew_server.db.database import Database
from agentcrew_server.db.migrations import MIGRATIONS, _apply_migration, _bootstrap_version_table


root = Path(sys.argv[1])
db = Database(root / "agentcrew.db")
_bootstrap_version_table(db.write_conn)
for migration in MIGRATIONS[:10]:
    _apply_migration(db.write_conn, migration)


def stop_at_boundary(statement):
    if statement.startswith("CREATE TABLE demo_identities"):
        print("migration-boundary", flush=True)
        os.kill(os.getpid(), signal.SIGSTOP)


db.write_conn.set_trace_callback(stop_at_boundary)
_apply_migration(db.write_conn, MIGRATIONS[10])
