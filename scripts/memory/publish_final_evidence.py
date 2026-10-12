"""发布真实 M1 最终验收材料，并扫描实际配置凭据。"""

import hashlib
import json
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TARGET = ROOT / "docs/acceptance/assets/M1/13"


def sanitize(value):
    if isinstance(value, dict):
        return {key: sanitize(item) for key, item in value.items() if key not in {"api_key", "api_key_hint", "token", "authorization"}}
    if isinstance(value, list):
        return [sanitize(item) for item in value]
    return value


def publish():
    TARGET.mkdir(parents=True, exist_ok=True)
    sources = {
        "final-memory-http.json": "data/m1-intermediate/13-final-memory-03.json",
        "compound-recovery.json": "desktop/.artifacts/m1-compound-1791027542277/compound-output.json",
        "compound-replay.json": "desktop/.artifacts/m1-compound-1791027542277/replay-output.json",
        "m0-regression.json": "desktop/.artifacts/m1-m0-1791028432324/recovery-output.json",
        "m0-replay.json": "desktop/.artifacts/m1-m0-1791028432324/replay-output.json",
        "management-electron.json": "desktop/.artifacts/m1-memory-1791028433581/memory-output.json",
        "three-store-electron.json": "desktop/.artifacts/m1-final-view-1791029279428/view-output.json",
        "material-reset.json": "data/m1-intermediate/13-memory-reset.json",
        **{f"{name}-sql-audit.json": f"data/m1-intermediate/13-{name}-audit.json"
            for name in ("compound", "http", "m0", "management", "view")},
        "backend-tests.txt": "data/m1-intermediate/13-backend-02.txt",
        "typecheck.txt": "data/m1-intermediate/13-typecheck.txt",
        "build.txt": "data/m1-intermediate/13-build.txt",
        "uv-sync.txt": "data/m1-intermediate/13-uv-sync.txt",
        "openapi.txt": "data/m1-intermediate/13-openapi.txt",
        "desktop-tests.txt": "data/m1-intermediate/13-desktop-management-02.txt",
        "memory-http-tests.txt": "data/m1-intermediate/13-final-memory-03.txt",
        "three-store-tests.txt": "data/m1-intermediate/13-final-view-03.txt",
        "compound-interrupted.png": "desktop/.artifacts/m1-compound-1791027542277/memory-interrupted.png",
        "compound-completed.png": "desktop/.artifacts/m1-compound-1791027542277/compound-completed.png",
        "compound-replay.png": "desktop/.artifacts/m1-compound-1791027542277/replay-stable.png",
        "m0-recovery.png": "desktop/.artifacts/m1-m0-1791028432324/recovery-completed.png",
        "m0-queue.png": "desktop/.artifacts/m1-m0-1791028432324/queue-completed.png",
        "m0-materials.png": "desktop/.artifacts/m1-m0-1791028432324/materials-imported.png",
        "m0-replay.png": "desktop/.artifacts/m1-m0-1791028432324/replay-stable.png",
        "user.png": "desktop/.artifacts/m1-final-view-1791029279428/user.png",
        "workspace.png": "desktop/.artifacts/m1-final-view-1791029279428/workspace.png",
        "soul.png": "desktop/.artifacts/m1-final-view-1791029279428/soul.png",
        "chinese-search.png": "desktop/.artifacts/m1-final-view-1791029279428/chinese-search.png",
        "background-notification.png": "desktop/.artifacts/m1-memory-1791028433581/memory-background.png",
    }
    for name, source in sources.items():
        source = ROOT / source
        if name.endswith(".json"):
            value = sanitize(json.loads(source.read_text()))
            if "all_checks_passed" in value:
                assert value["all_checks_passed"]
            (TARGET / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
        else:
            if name.endswith(".txt"):
                (TARGET / name).write_text(source.read_text().rstrip() + "\n")
            else:
                shutil.copyfile(source, TARGET / name)
    credentials = set()
    for config in (ROOT / "backend/data/config.json", ROOT / "data/m1-final-memory-02/config.json"):
        def collect(value):
            if isinstance(value, dict):
                for key, item in value.items():
                    if key == "api_key" and isinstance(item, str) and item:
                        credentials.add(item)
                    collect(item)
            elif isinstance(value, list):
                for item in value:
                    collect(item)
        collect(json.loads(config.read_text()))
    checks = []
    for path in sorted(TARGET.iterdir()):
        if path.name == "publication-check.json":
            continue
        data = path.read_bytes()
        matches = sum(secret.encode() in data for secret in credentials)
        assert matches == 0, path.name
        checks.append({"path": str(path.relative_to(ROOT)), "bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest(), "credential_matches": matches})
    (TARGET / "publication-check.json").write_text(json.dumps({"configured_keys_checked": len(credentials),
        "credential_matches": 0, "files": checks}, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"published_files": len(checks), "credential_matches": 0}))


if __name__ == "__main__":
    publish()
