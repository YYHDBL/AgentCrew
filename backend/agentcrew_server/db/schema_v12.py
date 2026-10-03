"""M2-04：区分历史任务版本登记与已绑定的新任务。"""

V12_STATEMENTS = (
    "ALTER TABLE task_governance ADD COLUMN skill_binding_version INTEGER NOT NULL DEFAULT 0 CHECK(skill_binding_version IN (0,1))",
)
