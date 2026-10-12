"""M2-06：经管理员连接校验保存的MCP工具目录。"""

V13_STATEMENTS = (
    """CREATE TABLE connector_tools(connector_id TEXT NOT NULL REFERENCES connectors(id),
        tool_name TEXT NOT NULL,stable_name TEXT NOT NULL UNIQUE,definition TEXT NOT NULL CHECK(json_valid(definition)),
        connector_revision INTEGER NOT NULL,validated_at TEXT NOT NULL,PRIMARY KEY(connector_id,tool_name))""",
)
