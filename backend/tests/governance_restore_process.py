"""实际执行启动恢复，供父进程核查SIGKILL后的持久化计划。"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agentcrew_server.governance.backups import apply_pending_restore


print(json.dumps(apply_pending_restore(Path(sys.argv[1]))), flush=True)
