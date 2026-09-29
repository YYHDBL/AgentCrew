"""配置链纯函数参数化单测（ADR-006：自有纯函数正常测，不涉外部系统）。"""

import pytest

from agentcrew_server.config import (
    DEFAULTS,
    ConfigError,
    apply_env,
    merge_config,
)


def test_defaults_only():
    effective, env_fields = apply_env(DEFAULTS, {})
    assert effective["log_level"] == "info"
    assert effective["gates"]["max_steps"] == 40
    assert effective["limits"]["max_files"] == 20
    assert env_fields == []


def test_merge_does_not_mutate_defaults():
    merged = merge_config(DEFAULTS, {"log_level": "debug", "gates": {"max_steps": 5}})
    assert merged["log_level"] == "debug"
    assert merged["gates"]["max_steps"] == 5
    assert merged["gates"]["stall_seconds"] == 600  # 未提及的字段保留默认
    assert DEFAULTS["log_level"] == "info"
    assert DEFAULTS["gates"]["max_steps"] == 40


@pytest.mark.parametrize(
    "file_level,env,expected,expect_env_field",
    [
        ("debug", {}, "debug", False),  # file > 默认
        ("debug", {"AGENTCREW_LOG_LEVEL": "warning"}, "warning", True),  # env > file
        ("debug", {"LOG_LEVEL": "error"}, "error", True),  # 裸名同样覆盖 file
        ("debug", {"LOG_LEVEL": "error", "AGENTCREW_LOG_LEVEL": "warning"}, "warning", True),  # AGENTCREW_ 优先
        ("info", {}, "info", False),
    ],
)
def test_log_level_precedence(file_level, env, expected, expect_env_field):
    effective, env_fields = apply_env(merge_config(DEFAULTS, {"log_level": file_level}), env)
    assert effective["log_level"] == expected
    assert ("log_level" in env_fields) is expect_env_field


def test_env_int_and_model_slot_overrides():
    effective, env_fields = apply_env(
        DEFAULTS,
        {
            "AGENTCREW_GATES__MAX_STEPS": "10",
            "AGENTCREW_LIMITS__MAX_FILE_MB": "3",
            "AGENTCREW_MAIN__API_KEY": "sk-test-12345678",
            "AGENTCREW_AUX__MODEL": "glm-flash",
        },
    )
    assert effective["gates"]["max_steps"] == 10
    assert isinstance(effective["gates"]["max_steps"], int)
    assert effective["limits"]["max_file_mb"] == 3
    assert effective["models"]["main"]["api_key"] == "sk-test-12345678"
    assert effective["models"]["aux"]["model"] == "glm-flash"
    assert env_fields == [
        "gates.max_steps",
        "limits.max_file_mb",
        "models.aux.model",
        "models.main.api_key",
    ]


def test_env_invalid_int_fails_closed():
    with pytest.raises(ConfigError, match="需要整数"):
        apply_env(DEFAULTS, {"AGENTCREW_GATES__MAX_STEPS": "abc"})


def test_invalid_log_level_fails_closed():
    with pytest.raises(ConfigError, match="log_level 非法"):
        apply_env(merge_config(DEFAULTS, {"log_level": "verbose"}), {})


def test_apply_env_does_not_mutate_input():
    merged = merge_config(DEFAULTS, {"log_level": "debug"})
    apply_env(merged, {"AGENTCREW_LOG_LEVEL": "warning"})
    assert merged["log_level"] == "debug"
    assert DEFAULTS["gates"]["max_steps"] == 40


def test_empty_env_value_ignored():
    effective, env_fields = apply_env(DEFAULTS, {"AGENTCREW_LOG_LEVEL": ""})
    assert effective["log_level"] == "info"
    assert env_fields == []
