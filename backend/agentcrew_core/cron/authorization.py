"""规范化预授权范围的交集与缩小判定。"""

from pathlib import Path

from ..tools.gate import rule_matches


def path_candidates(tool, allowed, roots):
    candidates = []
    parent = Path(allowed)
    for scope in map(Path, roots):
        if parent.is_relative_to(scope):
            candidates.append({"tool": tool, "pattern": str(parent)})
        elif scope.is_relative_to(parent):
            candidates.append({"tool": tool, "pattern": str(scope)})
    return candidates


def authorization_contains(candidate, choice):
    tool, pattern = choice["tool"], choice["pattern"]
    if candidate["tool"] != tool:
        return False
    if tool in {"write_file", "read_file"}:
        return Path(pattern).is_relative_to(Path(candidate["pattern"]))
    if tool == "http_request":
        if pattern.startswith("*."):
            return pattern == candidate["pattern"] or (candidate["pattern"].startswith("*.") and pattern[2:].endswith(candidate["pattern"][1:]))
        return rule_matches(tool, {"url": "https://" + pattern}, candidate["pattern"])
    return pattern == candidate["pattern"]
