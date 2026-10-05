"""通过真实治理API生成M2验收资源与本地范围材料。"""

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))

from agentcrew_server.secrets import register_secret, redact


def seed(url, root, connector_url):
    token = os.environ["AGENTCREW_TOKEN"]
    register_secret(token)
    root.mkdir(parents=True, exist_ok=True)
    requests = []
    with httpx.Client(base_url=url.rstrip("/") + "/api", headers={"Authorization": "Bearer " + token}, timeout=60) as client:
        def request(method, path, body=None):
            response = client.request(method, path, json=body)
            requests.append({"method": method, "path": path, "request": body, "status": response.status_code, "response": response.json()})
            response.raise_for_status()
            return response.json()["data"]
        identity = request("GET", "/identity")
        if identity["role"] != "owner":
            raise ValueError("治理验收材料必须由真实owner身份创建")
        selected = []
        for workspace, employee, text in (("office", "xiaowen", "负责办公材料与真实工具结果核查，执行当前合法指令，遵守scope、Grant和审批。"),
                ("analytics", "xiaogang", "负责数据来源与真实结果核查，仅使用当前授权能力，遵守scope、Grant和审批。")):
            current = request("GET", f"/memory/stores/soul/{employee}?workspace_id={workspace}&agent_id={employee}")
            if not current["entries"]:
                request("POST", f"/memory/stores/soul/{employee}?workspace_id={workspace}&agent_id={employee}",
                    {"change_id": "m2-acceptance-soul-" + employee, "expected_revision": current["revision"], "basis": "所有者配置真实治理验收员工岗位", "text": text})
            selected.append(request("GET", f"/agents/{employee}"))
        connector = None
        if connector_url:
            target = httpx.URL(connector_url)
            connector = request("POST", "/connectors", {"change_id": "m2-acceptance-http", "expected_revision": 0, "workspace_id": "office",
                "name": "M2真实持久化验收服务", "type": "http", "config": {"url": connector_url, "allowed_hosts": [target.host],
                    "allowed_ports": [target.port], "allow_loopback": True}})
            request("POST", "/grants", {"change_id": "m2-acceptance-http-xiaowen", "resource_type": "connector", "resource_id": connector["id"],
                "grantee_type": "agent", "grantee_id": "xiaowen"})
    files = []
    for name, content in (("authorized-external/source.txt", "已授权外部目录真实材料。"), ("outside-scope/source.txt", "未授权范围真实材料。")):
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists() and path.read_text() != content:
            raise ValueError("验收材料已经存在其他内容，停止覆盖")
        if not path.exists():
            path.write_text(content)
        files.append({"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "mtime_ns": path.stat().st_mtime_ns})
    result = {"identity": identity, "employees": selected, "connector": connector, "files": files, "http": requests}
    (root / "seed-evidence.json").write_text(redact(json.dumps(result, ensure_ascii=False, indent=2)) + "\n")
    print(json.dumps({"employees": len(selected), "files": len(files), "actual_http_requests": len(requests)}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--connector-url")
    args = parser.parse_args()
    seed(args.url, args.data_dir.resolve(), args.connector_url)
