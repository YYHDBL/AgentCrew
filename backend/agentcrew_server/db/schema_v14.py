"""M2-08：连接器启动代码的规范化目标、内容校验与修订绑定。"""

V14_STATEMENTS = (
    """CREATE TABLE connector_startup_files(connector_id TEXT NOT NULL REFERENCES connectors(id),
        connector_revision INTEGER NOT NULL,source_path TEXT NOT NULL,canonical_path TEXT NOT NULL,
        sha256 TEXT NOT NULL,created_at TEXT NOT NULL,PRIMARY KEY(connector_id,connector_revision,source_path))""",
    """CREATE TRIGGER connector_startup_immutable_update BEFORE UPDATE ON connector_startup_files
        BEGIN SELECT RAISE(ABORT,'IMMUTABLE_STARTUP_BINDING'); END""",
    """CREATE TRIGGER connector_startup_immutable_delete BEFORE DELETE ON connector_startup_files
        BEGIN SELECT RAISE(ABORT,'IMMUTABLE_STARTUP_BINDING'); END""",
)
