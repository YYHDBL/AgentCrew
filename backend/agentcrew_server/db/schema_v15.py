"""M2-10：已建立的审计锚点登记。"""

V15_STATEMENTS = (
    """CREATE TABLE audit_anchor_state(id INTEGER PRIMARY KEY CHECK(id=1),
        seq INTEGER NOT NULL CHECK(seq>0),hash TEXT NOT NULL CHECK(length(hash)=64))""",
    """CREATE TABLE audit_anchor_intents(id INTEGER PRIMARY KEY CHECK(id=1),
        seq INTEGER NOT NULL CHECK(seq>0),hash TEXT NOT NULL CHECK(length(hash)=64))""",
)
