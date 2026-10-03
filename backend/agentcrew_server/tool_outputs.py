"""工具完整输出：有界内存缓冲、原子工件与可校验上下文指针。"""

from __future__ import annotations

import asyncio
import codecs
import hashlib
import io
import json
import os
import tempfile
from pathlib import Path

from agentcrew_core.tools.metadata import ToolResult, WorkContext
from agentcrew_core.events import RunEventType

PREFIX_BYTES = 2048
BUFFER_BYTES = 32 * 1024


class ArtifactIntegrityError(ValueError):
    def __init__(self, reason: str, path: Path):
        self.reason, self.path = reason, path
        super().__init__(f"{reason}：{path}")


class ToolOutputStore:
    def capture(self, directory: Path):
        if directory is None:
            raise RuntimeError("EXTERNALIZATION_UNAVAILABLE：缺少工件目录")
        directory.mkdir(parents=True, exist_ok=True)
        return tempfile.SpooledTemporaryFile(max_size=BUFFER_BYTES, mode="w+b", dir=directory)

    @staticmethod
    def _read_page(path: Path, offset: int, limit: int, output, expected: str | None):
        digest = hashlib.sha256()
        selected = total = 0
        with path.open("rb") as source:
            for total, line in enumerate(source, start=1):
                digest.update(line)
                if offset <= total < offset + limit:
                    line.decode("utf-8")
                    output.write(line)
                    selected += 1
        if expected is not None and digest.hexdigest() != expected:
            raise ValueError(f"ARTIFACT_HASH_MISMATCH：{path}")
        return selected, total

    async def read_page(self, path: Path, offset: int, limit: int, output, *, expected=None):
        reading = asyncio.create_task(asyncio.to_thread(
            self._read_page, path, offset, limit, output, expected))
        try:
            return await asyncio.shield(reading)
        finally:
            await reading

    async def archive_result(self, *, directory: Path, call_id: str, content: str,
                             task_run_id: str, conversation_id: str,
                             source_global_seq: int, events):
        archiving = asyncio.create_task(self._archive_result(directory=directory, call_id=call_id,
            content=content, task_run_id=task_run_id, conversation_id=conversation_id,
            source_global_seq=source_global_seq, events=events))
        try:
            return await asyncio.shield(archiving)
        finally:
            await archiving

    async def _archive_result(self, *, directory: Path, call_id: str, content: str,
                             task_run_id: str, conversation_id: str,
                             source_global_seq: int, events):
        artifact_id = f"{call_id}-context-output"
        path = directory / f"{artifact_id}.out"
        raw = content.encode("utf-8")
        expected = hashlib.sha256(raw).hexdigest()

        def existing(conn):
            row = conn.execute("SELECT payload FROM run_events WHERE conversation_id=? "
                "AND type='tool.result_externalized' AND json_extract(payload,'$.artifact_path')=? "
                "ORDER BY global_seq DESC LIMIT 1", (conversation_id, str(path))).fetchone()
            return json.loads(row[0]) if row else None

        previous = await events.channel.execute(existing)
        if previous is not None:
            if previous["sha256"] != expected or previous["size_bytes"] != len(raw):
                raise ArtifactIntegrityError("ARTIFACT_IDENTITY_MISMATCH", path)
            await asyncio.to_thread(self.verify, path, expected, len(raw))
            return previous
        with io.BytesIO(raw) as source:
            data = await asyncio.to_thread(self._save, directory, path.name, source, 0)
        payload = {"call_id": call_id, **{key: data[key] for key in
            ("artifact_path", "sha256", "size_bytes", "prefix_bytes")},
            "source_global_seq": source_global_seq}

        def commit(conn):
            with conn:
                conn.execute("BEGIN IMMEDIATE")
                old = existing(conn)
                if old is not None:
                    if old["sha256"] != expected or old["size_bytes"] != len(raw):
                        raise ArtifactIntegrityError("ARTIFACT_IDENTITY_MISMATCH", path)
                    return
                records = []
                for kind, value in (
                    (RunEventType.ARTIFACT_CREATED, {"artifact_id": artifact_id,
                        "task_run_id": task_run_id, "tool_call_id": call_id,
                        "path": str(path), "name": path.name, "ext": ".out"}),
                    (RunEventType.ARTIFACT_READY, {"artifact_id": artifact_id, "size_bytes": len(raw)}),
                    (RunEventType.TOOL_RESULT_EXTERNALIZED, payload)):
                    records.append(events.append_in_tx(conn, task_run_id=task_run_id,
                        conversation_id=conversation_id, type=kind, payload=value))
            for event in records:
                events.publish(event)
        committing = asyncio.create_task(events.channel.execute(commit))
        try:
            await asyncio.shield(committing)
        finally:
            await committing
        return payload

    @staticmethod
    def _save(directory: Path, name: str, source, prefix_limit: int) -> dict:
        directory.mkdir(parents=True, exist_ok=True)
        source.seek(0)
        prefix = source.read(prefix_limit)
        # IncrementalDecoder 保留完整字符，未完成的尾部字节留在 decoder 中。
        text = codecs.getincrementaldecoder("utf-8")(errors="replace").decode(prefix, final=False)
        text = codecs.getincrementaldecoder("utf-8")().decode(text.encode("utf-8")[:PREFIX_BYTES], final=False)
        source.seek(0)
        destination = directory / name
        descriptor, pending_text = tempfile.mkstemp(prefix=f".{name}.", suffix=".pending",
                                                     dir=directory)
        pending = Path(pending_text)
        digest, size = hashlib.sha256(), 0
        try:
            with os.fdopen(descriptor, "wb") as target:
                while chunk := source.read(65536):
                    target.write(chunk)
                    digest.update(chunk)
                    size += len(chunk)
                target.flush()
                os.fsync(target.fileno())
            os.replace(pending, destination)
            fd = os.open(directory, os.O_RDONLY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        finally:
            pending.unlink(missing_ok=True)
        return {"artifact_path": str(destination), "sha256": digest.hexdigest(),
                "size_bytes": size, "prefix_bytes": len(text.encode("utf-8")), "prefix": text}

    async def _finish_source(self, ctx, call_id, source, limit, *, stderr=False,
                             force=False, prefix_limit=PREFIX_BYTES):
        source.seek(0, os.SEEK_END)
        size = source.tell()
        source.seek(0)
        if size == 0 or (size <= limit and not force):
            return source.read().decode("utf-8", errors="replace"), None
        if ctx.artifacts_dir is None:
            raise RuntimeError("EXTERNALIZATION_UNAVAILABLE：缺少工件目录")
        artifact_id = f"{call_id}-stderr" if stderr else call_id
        data = await asyncio.to_thread(self._save, ctx.artifacts_dir, f"{artifact_id}.out", source, prefix_limit)
        if ctx.emit is not None:
            await ctx.emit("artifact.created", {"artifact_id": artifact_id,
                "task_run_id": ctx.task_run_id, "tool_call_id": call_id,
                "path": data["artifact_path"], "name": Path(data["artifact_path"]).name, "ext": ".out"})
            await ctx.emit("artifact.ready", {"artifact_id": artifact_id, "size_bytes": size})
            await ctx.emit("tool.result_externalized", {"call_id": call_id,
                **{key: data[key] for key in ("artifact_path", "sha256", "size_bytes", "prefix_bytes")},
                "source_global_seq": ctx.source_global_seq})
        ctx.readable_artifacts[str(Path(data["artifact_path"]).resolve())] = data["sha256"]
        pointer = (f"\n[输出已外部化 artifact={data['artifact_path']} "
                   f"sha256={data['sha256']} size_bytes={size}]")
        return data["prefix"] + pointer, data

    @staticmethod
    def verify(path: Path, sha256: str, size_bytes: int) -> None:
        if not path.is_file():
            raise ArtifactIntegrityError("ARTIFACT_MISSING", path)
        with path.open("rb") as source:
            actual_size = os.fstat(source.fileno()).st_size
            actual = hashlib.file_digest(source, "sha256").hexdigest()
        if actual != sha256 or actual_size != size_bytes:
            raise ArtifactIntegrityError("ARTIFACT_HASH_MISMATCH", path)

    @classmethod
    def verify_record(cls, record: dict) -> None:
        details = record.get("details") or {}
        if record.get("artifact_path") and details.get("sha256") and "prefix_bytes" in details:
            cls.verify(Path(record["artifact_path"]), details["sha256"], details["size_bytes"])
        if stderr_artifact := details.get("stderr_artifact"):
            cls.verify(Path(stderr_artifact["artifact_path"]),
                       stderr_artifact["sha256"], stderr_artifact["size_bytes"])

    async def finish(self, ctx: WorkContext, call_id: str, result: ToolResult, limit: int):
        commit = asyncio.create_task(self._finish(ctx, call_id, result, limit))
        try:
            await asyncio.shield(commit)
        finally:
            await commit

    async def _finish(self, ctx: WorkContext, call_id: str, result: ToolResult, limit: int):
        source = result.output_source if result.output_source is not None else io.BytesIO(result.output.encode("utf-8"))
        stderr = result.stderr_source
        try:
            source.seek(0, os.SEEK_END)
            stdout_size = source.tell()
            if stderr is not None:
                stderr.seek(0, os.SEEK_END)
            combined_size = stdout_size + (stderr.tell() if stderr is not None else 0)
            force = combined_size > limit
            result.output, data = await self._finish_source(ctx, call_id, source, limit, force=force)
            if data is not None:
                result.artifact_path = data["artifact_path"]
                result.details.update({key: value for key, value in data.items() if key != "prefix"})
            if stderr is not None:
                text, stderr_data = await self._finish_source(ctx, call_id, stderr, limit,
                    stderr=True, force=force, prefix_limit=0 if stdout_size else PREFIX_BYTES)
                result.details["stderr"] = text
                if text:
                    result.output += f"\n[stderr]\n{text}"
                if stderr_data is not None:
                    result.details["stderr_artifact"] = {key: value for key, value in stderr_data.items() if key != "prefix"}
        finally:
            source.close()
            if stderr is not None:
                stderr.close()
            result.output_source = result.stderr_source = None
