"""M1-04：原始业务记录的 trigram 索引和显式使用去重。"""

V5_STATEMENTS = (
    "CREATE VIRTUAL TABLE messages_fts USING fts5(content, content='messages', tokenize='trigram')",
    """CREATE TRIGGER messages_fts_insert AFTER INSERT ON messages BEGIN
        INSERT INTO messages_fts(rowid,content) VALUES(new.rowid,new.content); END""",
    """CREATE TRIGGER messages_fts_delete AFTER DELETE ON messages BEGIN
        INSERT INTO messages_fts(messages_fts,rowid,content) VALUES('delete',old.rowid,old.content); END""",
    """CREATE TRIGGER messages_fts_update AFTER UPDATE OF content ON messages BEGIN
        INSERT INTO messages_fts(messages_fts,rowid,content) VALUES('delete',old.rowid,old.content);
        INSERT INTO messages_fts(rowid,content) VALUES(new.rowid,new.content); END""",
    "INSERT INTO messages_fts(messages_fts) VALUES('rebuild')",
    "CREATE VIRTUAL TABLE memory_fts USING fts5(text, content='memory_entries', tokenize='trigram')",
    """CREATE TRIGGER memory_fts_insert AFTER INSERT ON memory_entries BEGIN
        INSERT INTO memory_fts(rowid,text) VALUES(new.rowid,new.text); END""",
    """CREATE TRIGGER memory_fts_delete AFTER DELETE ON memory_entries BEGIN
        INSERT INTO memory_fts(memory_fts,rowid,text) VALUES('delete',old.rowid,old.text); END""",
    """CREATE TRIGGER memory_fts_update AFTER UPDATE OF text ON memory_entries BEGIN
        INSERT INTO memory_fts(memory_fts,rowid,text) VALUES('delete',old.rowid,old.text);
        INSERT INTO memory_fts(rowid,text) VALUES(new.rowid,new.text); END""",
    "INSERT INTO memory_fts(memory_fts) VALUES('rebuild')",
    """CREATE TABLE memory_usage_hits (
        use_id TEXT NOT NULL, entry_id TEXT NOT NULL, created_at TEXT NOT NULL,
        PRIMARY KEY(use_id,entry_id))""",
)
