"""真实 sidecar 验收入口，仅观察发出的模型 HTTP 请求。"""

import hashlib
import json
import os
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from agentcrew_server.cli import main
from agentcrew_server.secrets import redact

_observed_requests = set()
_observed_searches = {}
_observed_streams = set()
_observed_summary_tasks = {}
_observed_main_streams = set()


def observe(frame, event, arg):
    if (os.environ.get("MEMORY_MAIN_STREAM_EVIDENCE") and event == "call"
            and frame.f_globals.get("__name__") == "agentcrew_server.run_manager"
            and frame.f_code.co_name == "<lambda>" and frame.f_back.f_code.co_name == "run_task"
            and getattr(frame.f_back.f_locals.get("ev"), "type", None) == "text_delta"):
        emitter = frame.f_back.f_locals["deps"].emit
        captured = dict(zip(emitter.__code__.co_freevars,
                            (cell.cell_contents for cell in emitter.__closure__)))
        task_id = captured["task_run_id"]
        if task_id not in _observed_main_streams:
            _observed_main_streams.add(task_id)
            value = {"task_run_id": task_id, "event": "actual_main_text_delta",
                     "monotonic": time.monotonic(),
                     "observed_at": datetime.now(timezone.utc).isoformat()}
            with Path(os.environ["MEMORY_MAIN_STREAM_EVIDENCE"]).open("a", encoding="utf-8") as output:
                output.write(json.dumps(value, ensure_ascii=False) + "\n")
    if (os.environ.get("MEMORY_JOB_STREAM_EVIDENCE") and event == "call"
            and frame.f_globals.get("__name__") == "agentcrew_server.memory.jobs"
            and frame.f_code.co_name == "<lambda>" and frame.f_back.f_code.co_name == "run_task"
            and getattr(frame.f_back.f_locals.get("ev"), "type", None) == "text_delta"):
        emitter = frame.f_back.f_locals["deps"].emit
        captured = dict(zip(emitter.__code__.co_freevars, (cell.cell_contents for cell in emitter.__closure__)))
        job_id = captured["job_id"]
        if job_id not in _observed_streams:
            _observed_streams.add(job_id)
            job = captured["self"].get(job_id)
            _observed_summary_tasks[job["task_run_id"]] = job_id
            value = {"job_id": job_id, "conversation_id": job["conversation_id"], "event": "actual_text_delta", "monotonic": time.monotonic(),
                     "observed_at": datetime.now(timezone.utc).isoformat()}
            with Path(os.environ["MEMORY_JOB_STREAM_EVIDENCE"]).open("a", encoding="utf-8") as output:
                output.write(json.dumps(value, ensure_ascii=False) + "\n")
    if (os.environ.get("MEMORY_JOB_STREAM_EVIDENCE") and event == "return"
            and frame.f_globals.get("__name__") == "httpx._models" and frame.f_code.co_name == "aclose"):
        response = frame.f_locals["self"]
        request = response.request
        if request.method != "POST" or not request.url.path.endswith("/chat/completions"):
            return
        body = json.loads(request.content)
        if not body["messages"][0]["content"].startswith("你为已经完成的真实任务编写工作记录"):
            return
        material = json.loads(body["messages"][1]["content"])
        job_id = _observed_summary_tasks.get(material["task_run_id"])
        if job_id in _observed_streams and response.is_closed:
            value = {"job_id": job_id, "conversation_id": request.headers.get("x-opencode-session"),
                     "event": "response_closed", "monotonic": time.monotonic(),
                     "observed_at": datetime.now(timezone.utc).isoformat()}
            with Path(os.environ["MEMORY_JOB_STREAM_EVIDENCE"]).open("a", encoding="utf-8") as output:
                output.write(json.dumps(value, ensure_ascii=False) + "\n")
    if (os.environ.get("MEMORY_SEARCH_EVIDENCE") and event in {"call", "return"}
            and frame.f_globals.get("__name__") == "agentcrew_server.memory.search" and frame.f_code.co_name == "_query"):
        store = frame.f_locals["self"]
        count = store.db.read_conn.execute("SELECT COUNT(*) FROM llm_calls").fetchone()[0]
        if event == "call":
            _observed_searches[frame] = {"query": frame.f_locals["query"], "before_llm_calls": count}
        elif event == "return":
            value = {**_observed_searches.pop(frame), "after_llm_calls": count,
                     "result": arg, "observed_at": datetime.now(timezone.utc).isoformat()}
            with Path(os.environ["MEMORY_SEARCH_EVIDENCE"]).open("a", encoding="utf-8") as output:
                output.write(redact(json.dumps(value, ensure_ascii=False)) + "\n")
        return
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
