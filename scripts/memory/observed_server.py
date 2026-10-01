"""真实 sidecar 验收入口，仅观察发出的模型 HTTP 请求。"""

import hashlib
import json
import os
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path

from agentcrew_server.cli import main
from agentcrew_server.secrets import redact

_observed_requests = set()


def observe(frame, event, arg):
    if event != "call" or frame.f_globals.get("__name__") != "httpx._client" or frame.f_code.co_name != "_send_single_request":
        return
    request = frame.f_locals["request"]
    if request.method != "POST" or not request.url.path.endswith(("/chat/completions", "/messages")):
        return
    if request in _observed_requests:
        return
    _observed_requests.add(request)
    body = json.loads(request.content)
    value = {"observed_at": datetime.now(timezone.utc).isoformat(), "url": str(request.url),
             "session_id": request.headers.get("x-opencode-session"), "body": body,
             "body_sha256": hashlib.sha256(request.content).hexdigest()}
    with Path(os.environ["MEMORY_REQUEST_EVIDENCE"]).open("a", encoding="utf-8") as output:
        output.write(redact(json.dumps(value, ensure_ascii=False)) + "\n")


if __name__ == "__main__":
    sys.setprofile(observe)
    threading.setprofile(observe)
    raise SystemExit(main())
