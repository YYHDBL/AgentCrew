"""Skill 创建、整篇编辑及唯一文本替换。"""

from .skill_view import _skill_view

SKILL_PATCH_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "name": {"type": "string", "minLength": 1, "maxLength": 80},
        "action": {"type": "string", "enum": ["read", "create", "patch", "edit"]},
        "file": {"type": "string"}, "expected_revision": {"type": "integer", "minimum": 0},
        "basis": {"type": "string", "minLength": 1}, "description": {"type": "string", "minLength": 1, "maxLength": 60},
        "text": {"type": "string"}, "old_text": {"type": "string"}, "new_text": {"type": "string"},
        "files": {"type": "object", "additionalProperties": {"type": ["string", "null"]}},
    }, "required": ["name", "action"],
}

_skill_patch = _skill_view
