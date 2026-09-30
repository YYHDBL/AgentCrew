"""M0-C7 迁移（版本 2）：客户端幂等键列 + 会话域事件放开任务外键。

1) backend-service §8 客户端幂等——POST /conversations 与 POST instructions
   接受可选 client_request_id，服务端按其唯一去重。conversations 与
   task_runs 各加一列 + 部分唯一索引（NULL 不占唯一性）。排队指令的幂等键
   存 pending_queue 项内（JSON）；出队建 task_run 时带上，全链可查。

2) harness-session §2.1 run_events.task_run_id 未标 NOT NULL（设计原文
   `task_run_id FK`），C2 建表时误加了 NOT NULL。queue.*（F006）是**会话域**
   事实：入队/取消可发生在无任何运行中任务的时刻（queue_paused 后取消剩余
   项），把这类事件锚到某个无关任务会污染任务级 SSE 流与 attempts 重建。
   表重建放开为可空（global_seq 显式保序复制，游标不漂移）；UNIQUE(task_
   run_id, seq) 改为部分唯一索引，对非空任务保持原语义。
"""

V2_STATEMENTS: tuple[str, ...] = (
    "ALTER TABLE conversations ADD COLUMN client_request_id TEXT",
    "ALTER TABLE task_runs ADD COLUMN client_request_id TEXT",
    """CREATE UNIQUE INDEX idx_conversations_client_request_id
       ON conversations (client_request_id)
       WHERE client_request_id IS NOT NULL""",
    """CREATE UNIQUE INDEX idx_task_runs_conv_client_request
       ON task_runs (conversation_id, client_request_id)
       WHERE client_request_id IS NOT NULL""",
    """
    CREATE TABLE run_events_v2 (
        global_seq         INTEGER PRIMARY KEY AUTOINCREMENT,
        id                 TEXT NOT NULL UNIQUE,
        task_run_id        TEXT REFERENCES task_runs(id),
        seq                INTEGER,
        conversation_id    TEXT NOT NULL REFERENCES conversations(id),
        agent_run_id       TEXT,
        attempt_no         INTEGER,
        type               TEXT NOT NULL,
        payload            TEXT NOT NULL DEFAULT '{}',
        created_at         TEXT NOT NULL
    )
    """,
    """
    INSERT INTO run_events_v2 (global_seq, id, task_run_id, seq, conversation_id,
                               agent_run_id, attempt_no, type, payload, created_at)
    SELECT global_seq, id, task_run_id, seq, conversation_id,
           agent_run_id, attempt_no, type, payload, created_at
    FROM run_events ORDER BY global_seq
    """,
    "DROP TABLE run_events",
    "ALTER TABLE run_events_v2 RENAME TO run_events",
    """CREATE UNIQUE INDEX idx_run_events_task_seq
       ON run_events (task_run_id, seq) WHERE task_run_id IS NOT NULL""",
    "CREATE INDEX idx_run_events_conversation ON run_events (conversation_id, global_seq)",
    "CREATE INDEX idx_run_events_task ON run_events (task_run_id, seq)",
)
