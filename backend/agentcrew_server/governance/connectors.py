"""连接器配置、真实校验、凭据及按授权组装执行工具。"""

import asyncio
import hashlib
import json
import re
import uuid
from dataclasses import replace
from pathlib import Path

from jsonschema import Draft202012Validator

from agentcrew_core.connectors import connector_effect, connector_headers, connector_target, validate_schema_references
from agentcrew_core.memory import contains_credentials
from agentcrew_core.events import RunEventType as T
from agentcrew_core.tools import Tool, ToolInvocation, ToolMetadata, ToolRegistry, ToolResult
from agentcrew_core.tools.judgment import build_protected_paths, path_is_protected
from ..connectors.http import connector_client, request_http
from ..connectors.mcp import call, discover
from ..secrets import known_secrets, redact, register_secret
from .resources import GovernanceError, canonical, now


class Connectors:
    def __init__(self, resources, identities, grants):
        self.resources, self.identities, self.grants, self.db = resources, identities, grants, resources.db

    @staticmethod
    def stable_name(connector_id, name):
        suffix = name if len(name) <= 27 else hashlib.sha256(name.encode()).hexdigest()[:27]
        return "mcp_" + connector_id + "_" + suffix

    @staticmethod
    def description(definition):
        text = definition["description"] if definition.get("description") is not None else definition["name"]
        return "第三方MCP工具：" + text[:400]

    @staticmethod
    def validate_config(kind, config):
        if contains_credentials(canonical(config)):
            raise GovernanceError("VALIDATION_ERROR", "连接器配置不能包含内嵌凭据", 422)
        if kind == "mcp" and config.get("transport") not in {"stdio", "streamable_http"}:
            raise GovernanceError("VALIDATION_ERROR", "MCP传输类型无效", 422)
        if (kind == "http" or config.get("transport") == "streamable_http") and not config.get("url"):
            raise GovernanceError("VALIDATION_ERROR", "网络连接器缺少目标URL", 422)
        if config.get("credential_header", "Authorization").lower() not in {"authorization", "x-api-key"}:
            raise GovernanceError("VALIDATION_ERROR", "连接器认证头必须为Authorization或X-API-Key", 422)
        if kind == "http" or config["transport"] == "streamable_http":
            connector_target(config["url"], config)
        else:
            if not config.get("command"):
                raise GovernanceError("VALIDATION_ERROR", "stdio缺少可执行路径", 422)
            command = Path(config["command"])
            if not command.is_absolute() or not command.is_file():
                raise GovernanceError("VALIDATION_ERROR", "stdio需要已存在的绝对可执行路径", 422)
            if any(secret in canonical(config) for secret in known_secrets()):
                raise GovernanceError("VALIDATION_ERROR", "连接器启动参数不能包含凭据", 422)
            for filename in config.get("startup_files", []):
                path = Path(filename)
                if not path.is_absolute() or not path.is_file() or path.suffix.lower() not in {".py", ".js", ".mjs", ".cjs"}:
                    raise GovernanceError("VALIDATION_ERROR", "启动依赖必须明确登记已存在的绝对代码文件", 422)
        for policy in config.get("tool_policies", {}).values():
            if policy["read_only"] and (policy["destructive"] or policy["needs_approval"]):
                raise GovernanceError("VALIDATION_ERROR", "连接器只读工具的风险声明冲突", 422)
        for endpoint in config.get("idempotent_endpoints", []):
            if not endpoint["path"].startswith("/") or not endpoint["guarantee"].strip():
                raise GovernanceError("VALIDATION_ERROR", "外部幂等端点必须声明精确路径和真实去重承诺", 422)

    def credential(self, connector_id):
        row = self.db.read_conn.execute("SELECT credential FROM connectors WHERE id=?", (connector_id,)).fetchone()
        if row is None:
            raise GovernanceError("NOT_FOUND", "连接器不存在", 404)
        register_secret(row[0])
        return row[0]

    def authorize_bound(self, task_id, name, inputs, connector):
        view = self.grants.check_tool(task_id, name, inputs)
        selected = next((item for item in view["connectors"] if item["id"] == connector["id"]), None)
        if selected is None or selected["revision"] != connector["revision"]:
            raise GovernanceError("REVISION_CONFLICT", "调用绑定的连接器修订已经失效")
        return view

    def capture_startup(self, conn, connector):
        config = connector["config"]
        if connector["type"] != "mcp" or config["transport"] != "stdio":
            return
        for filename in dict.fromkeys([config["command"], *config.get("startup_files", [])]):
            target = Path(filename).resolve()
            if path_is_protected(target, build_protected_paths(self.resources.data_dir)):
                raise GovernanceError("OUT_OF_SCOPE", "保护文件不能登记为启动资源", 403)
            if filename != config["command"] and target.suffix.lower() not in {".py", ".js", ".mjs", ".cjs"}:
                raise GovernanceError("VALIDATION_ERROR", "启动依赖的实际目标必须属于代码文件", 422)
            content = target.read_bytes()
            if any(secret.encode() in content for secret in known_secrets()):
                raise GovernanceError("VALIDATION_ERROR", "启动代码不能包含已登记凭据", 422)
            conn.execute("INSERT INTO connector_startup_files VALUES(?,?,?,?,?,?)", (connector["id"], connector["revision"],
                filename, str(target), hashlib.sha256(content).hexdigest(), now()))

    def startup_resources(self, connector, conn=None):
        if connector["type"] != "mcp" or connector["config"]["transport"] != "stdio":
            return []
        connection = conn if conn is not None else self.db.read_conn
        rows = [dict(row) for row in connection.execute("SELECT * FROM connector_startup_files WHERE connector_id=? AND connector_revision=? ORDER BY source_path", (connector["id"], connector["revision"]))]
        expected = set([connector["config"]["command"], *connector["config"].get("startup_files", [])])
        if {row["source_path"] for row in rows} != expected:
            raise GovernanceError("REVISION_CONFLICT", "启动资源缺少修订绑定，请提交配置新修订并校验")
        for row in rows:
            source, target = Path(row["source_path"]), Path(row["canonical_path"])
            if source.resolve() != target or not target.is_file() or hashlib.sha256(target.read_bytes()).hexdigest() != row["sha256"]:
                raise GovernanceError("REVISION_CONFLICT", "启动资源目标或内容已经变化，请提交配置新修订")
        return rows

    async def create(self, identity, request):
        workspace = request["workspace_id"]
        self.identities.require(identity, "manage", workspace)
        register_secret(request.get("credential"))
        safe_request = {**request, "credential": hashlib.sha256((request.get("credential") or "").encode()).hexdigest()}
        connector_id = uuid.uuid5(uuid.NAMESPACE_URL, "agentcrew:connector:" + request["change_id"]).hex
        def operation(conn):
            self.validate_config(request["type"], request["config"])
            stamp = now()
            conn.execute("INSERT INTO connectors(id,workspace_id,name,type,config,credential,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
                (connector_id, workspace, request["name"], request["type"], canonical(request["config"]), request.get("credential"), stamp, stamp))
            value = self.resources.get("connector", connector_id, conn)
            self.capture_startup(conn, value)
            return value, self.resources.scope("connector", connector_id, conn), {"status": value["status"]}
        return await self.resources.mutate(change_id=request["change_id"], actor_id=identity.effective_user_id,
            credential_owner_id=identity.credential_owner_id, request=safe_request, action="governance.connector_created",
            kind="connector", resource_id=connector_id, operation=operation,
            authorize=lambda conn: self.identities.require(identity, "manage", workspace, conn))

    async def update(self, identity, connector_id, request):
        original = self.resources.get("connector", connector_id)
        self.identities.require(identity, "manage", original["workspace_id"])
        if any(field in request and request[field] is None for field in ("config", "name", "status")):
            raise GovernanceError("VALIDATION_ERROR", "连接器配置、名称和状态不能为null", 422)
        config = request.get("config", original["config"])
        if original["type"] == "mcp" and config.get("transport") == "stdio" and request.get("credential"):
            raise GovernanceError("VALIDATION_ERROR", "stdio禁止网络凭据", 422)
        register_secret(request.get("credential"))
        safe = {**request}
        if "credential" in safe:
            safe["credential"] = hashlib.sha256((safe["credential"] or "").encode()).hexdigest()
        def operation(conn):
            resource = self.resources.get("connector", connector_id, conn)
            if resource["revision"] != request["expected_revision"]:
                raise GovernanceError("REVISION_CONFLICT", "连接器修订已经变化")
            if "config" in request:
                self.validate_config(resource["type"], request["config"])
            for field in ("config", "name", "status", "credential"):
                if field in request:
                    conn.execute(f"UPDATE connectors SET {field}=? WHERE id=?", (canonical(request[field]) if field == "config" else request[field], connector_id))
            conn.execute("UPDATE connectors SET revision=revision+1,updated_at=? WHERE id=?", (now(), connector_id))
            conn.execute("DELETE FROM connector_tools WHERE connector_id=?", (connector_id,))
            value = self.resources.get("connector", connector_id, conn)
            if "config" in request:
                self.capture_startup(conn, value)
            else:
                conn.execute("INSERT INTO connector_startup_files SELECT connector_id,?,source_path,canonical_path,sha256,? FROM connector_startup_files WHERE connector_id=? AND connector_revision=?",
                    (value["revision"], now(), connector_id, resource["revision"]))
            return value, self.resources.scope("connector", connector_id, conn), {"status": value["status"], "configuration_changed": True}
        return await self.resources.mutate(change_id=request["change_id"], actor_id=identity.effective_user_id,
            credential_owner_id=identity.credential_owner_id, request=safe, action="governance.connector_changed",
            kind="connector", resource_id=connector_id, operation=operation,
            authorize=lambda conn: self.identities.require(identity, "manage", original["workspace_id"], conn))

    async def validate(self, identity, connector_id, context):
        resource = self.resources.get("connector", connector_id)
        self.identities.require(identity, "manage", resource["workspace_id"])
        if resource["status"] != "active":
            raise GovernanceError("OUT_OF_SCOPE", "连接器已禁用", 403)
        def authorize():
            self.identities.require(identity, "manage", resource["workspace_id"])
            current = self.resources.get("connector", connector_id)
            if current["revision"] != resource["revision"] or current["status"] != "active":
                raise GovernanceError("REVISION_CONFLICT", "连接校验期间配置已经改变")
        credential = self.credential(connector_id)
        resource["startup_resources"] = self.startup_resources(resource)
        if resource["type"] == "http":
            async with connector_client(resource, credential, authorize) as client:
                response = await client.get(resource["config"]["url"])
                response.raise_for_status()
            def record(conn):
                current = self.resources.get("connector", connector_id, conn)
                if current["revision"] != resource["revision"] or current["status"] != "active":
                    raise GovernanceError("REVISION_CONFLICT", "连接校验期间配置已经改变")
                return {"id": connector_id, "revision": resource["revision"], "connector_id": connector_id, "ok": True, "status_code": response.status_code, "tools": []}, \
                    self.resources.scope("connector", connector_id, conn), {"status": "active"}
            return await self.resources.mutate(change_id="http-validation-" + uuid.uuid4().hex, actor_id=identity.effective_user_id,
                credential_owner_id=identity.credential_owner_id, request={"revision": resource["revision"]}, action="governance.connector_validated",
                kind="connector", resource_id=connector_id, operation=record,
                authorize=lambda conn: self.identities.require(identity, "manage", resource["workspace_id"], conn))
        definitions = await discover(resource, context, credential, authorize)
        tools = []
        for definition in definitions:
            if any(secret in canonical(definition) for secret in known_secrets()):
                raise GovernanceError("VALIDATION_ERROR", "MCP工具目录包含凭据，禁止发布", 422)
            if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", definition["name"]):
                raise GovernanceError("VALIDATION_ERROR", "MCP工具名称无效", 422)
            Draft202012Validator.check_schema(definition["inputSchema"])
            validate_schema_references(definition["inputSchema"])
            if definition.get("outputSchema") is not None:
                Draft202012Validator.check_schema(definition["outputSchema"])
                validate_schema_references(definition["outputSchema"])
            tools.append({"name": self.stable_name(connector_id, definition["name"]), "parameters": definition["inputSchema"]})
        def operation(conn):
            current = self.resources.get("connector", connector_id, conn)
            if current["revision"] != resource["revision"]:
                raise GovernanceError("REVISION_CONFLICT", "工具发现期间连接器修订改变")
            conn.execute("DELETE FROM connector_tools WHERE connector_id=?", (connector_id,))
            for definition, tool in zip(definitions, tools):
                conn.execute("INSERT INTO connector_tools VALUES(?,?,?,?,?,?)", (connector_id, definition["name"], tool["name"],
                    redact(canonical(definition)), resource["revision"], now()))
            return {"id": connector_id, "revision": resource["revision"], "connector_id": connector_id, "ok": True, "tools": tools}, \
                self.resources.scope("connector", connector_id, conn), {"status": "active"}
        return await self.resources.mutate(change_id="connector-validation-" + uuid.uuid4().hex, actor_id=identity.effective_user_id,
            credential_owner_id=identity.credential_owner_id, request={"revision": resource["revision"]}, action="governance.connector_validated",
            kind="connector", resource_id=connector_id, operation=operation,
            authorize=lambda conn: self.identities.require(identity, "manage", resource["workspace_id"], conn))

    def authorized_mcp(self, task_id):
        result = []
        for connector in self.grants.view(task_id)["connectors"]:
            if connector["type"] != "mcp":
                continue
            connector["startup_resources"] = self.startup_resources(connector)
            rows = self.db.read_conn.execute("SELECT * FROM connector_tools WHERE connector_id=? AND connector_revision=?", (connector["id"], connector["revision"])).fetchall()
            if not rows:
                raise GovernanceError("CONNECTOR_NOT_VALIDATED", "MCP连接器尚未成功发现工具", 409)
            result.extend((connector, row, json.loads(row["definition"])) for row in rows)
        return result

    def registry(self, task_id, base):
        registry = ToolRegistry()
        for name in base.names():
            tool = base.get(name)
            if name == "http_request":
                async def execute(inv, ctx):
                    view = self.grants.check_tool(ctx.task_run_id, inv.name, inv.input)
                    selected = view["selected_connector"]
                    return await request_http(inv, ctx, selected, self.credential(selected["id"]),
                        lambda: self.authorize_bound(ctx.task_run_id, inv.name, inv.input, selected))
                tool = replace(tool, execute=execute)
            registry.register(tool)
        for connector, row, definition in self.authorized_mcp(task_id):
            policy = connector["config"].get("tool_policies", {}).get(row["tool_name"], {})
            metadata = ToolMetadata(name=row["stable_name"], description=self.description(definition),
                parameters=definition["inputSchema"], read_only=policy.get("read_only", False), destructive=policy.get("destructive", False),
                risk_level=policy.get("risk_level", "medium"), needs_approval=policy.get("needs_approval", True),
                concurrent_safe=policy.get("read_only", False), side_effect_class="outcome_unknown", timeout_ms=policy.get("timeout_ms", 60000))
            async def execute(inv, ctx, selected=connector, name=row["tool_name"], approved_definition=definition, timeout_ms=metadata.timeout_ms):
                authorize = lambda: self.authorize_bound(ctx.task_run_id, inv.name, inv.input, selected)
                authorize()
                result = await call(selected, ctx, self.credential(selected["id"]), authorize, name, inv.input, approved_definition, timeout_ms)
                return ToolResult(ok=not result["isError"], output=redact(canonical(result)),
                    error="MCP_TOOL_ERROR" if result["isError"] else None, details={"connector_id": selected["id"]})
            registry.register(Tool(metadata=metadata, execute=execute,
                prepared_extras=lambda _input, selected=connector: {"connector_id": selected["id"], "connector_revision": selected["revision"]}))
        return registry
