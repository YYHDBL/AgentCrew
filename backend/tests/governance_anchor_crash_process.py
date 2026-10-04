"""真实提交锚点意图后，在文件替换前发送SIGKILL。"""

import os
import signal
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agentcrew_server.db.audit import _prepare_anchor_intent
from agentcrew_server.db.database import Database


db = Database(Path(sys.argv[1]) / "agentcrew.db")
row = db.write_conn.execute("SELECT seq,hash FROM audit_log ORDER BY seq DESC LIMIT 1").fetchone()
_prepare_anchor_intent(db.write_conn, row)
os.kill(os.getpid(), signal.SIGKILL)
