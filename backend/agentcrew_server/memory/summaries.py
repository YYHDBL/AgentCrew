"""任务摘要查询；工作记录属于历史上下文，保持冻结三库不变。"""

from agentcrew_core.loop import user_text_message
from agentcrew_core.memory import recent_work_records

from ..secrets import redact
from .store import canonical


class SessionSummaries:
    def __init__(self, db):
        self.db = db

    def recent(self, conversation_id):
        return [dict(row) for row in self.db.read_conn.execute(
            "SELECT s.* FROM session_summaries s JOIN task_runs t ON t.id=s.task_run_id "
            "WHERE s.conversation_id=? AND t.status='completed' "
            "ORDER BY s.created_at DESC,s.id DESC LIMIT 5", (conversation_id,))]

    def messages(self, conversation_id):
        rows = self.recent(conversation_id)
        if not rows:
            return []
        return [user_text_message("【近期工作记录；仅为本会话已完成任务的事实摘要】\n" + redact(canonical(recent_work_records(rows))))]
