"""M3-14：桌面行为偏好（关窗即退出、阻止休眠）持久化。"""

V21_STATEMENTS = (
    """CREATE TABLE desktop_preferences (
        id INTEGER PRIMARY KEY CHECK(id=1),
        close_to_quit INTEGER NOT NULL CHECK(close_to_quit IN (0,1)),
        prevent_sleep INTEGER NOT NULL CHECK(prevent_sleep IN (0,1)),
        updated_at TEXT NOT NULL)""",
    "INSERT INTO desktop_preferences(id,close_to_quit,prevent_sleep,updated_at) VALUES(1,0,1,'1970-01-01T00:00:00+00:00')",
)
