"""File tools scoped to user-approved folders.

Reads (list/search/read) run automatically once at least one folder is
approved. Writes (create/move/rename/delete) are WRITE-risk and always go
through the confirmation card. Deletes go to the Trash, never rm.
"""

from __future__ import annotations

import asyncio
import hashlib
import shutil
import tempfile
from pathlib import Path

from pydantic import BaseModel, Field

from ...agent.tools import RiskLevel, StagedAction, ToolRegistry, ToolResult, ToolSpec
from ...assurance import StagedFileWrite, canonical_file
from .roots import ApprovedRoots, PathOutsideRootsError

# Directories that are never worth scanning: hidden trees plus dependency/
# build caches that can hold hundreds of thousands of files.
SKIP_DIRS = {"node_modules", "__pycache__", "build", "dist", "venv", "env"}


def iter_search_files(root):
    """Yield candidate files under root, pruning hidden and cache directories.

    Uses os.walk so pruned directories are never descended into (rglob would
    enumerate them and filter afterwards — pathologically slow on dev trees).
    """
    import os

    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if not d.startswith(".") and d not in SKIP_DIRS]
        for name in filenames:
            if not name.startswith("."):
                yield Path(dirpath) / name


TEXT_SUFFIXES = {
    ".txt",
    ".md",
    ".markdown",
    ".py",
    ".js",
    ".ts",
    ".json",
    ".yaml",
    ".yml",
    ".toml",
    ".csv",
    ".tsv",
    ".html",
    ".css",
    ".xml",
    ".sh",
    ".log",
    ".ini",
    ".rtf",
}


class ListParams(BaseModel):
    folder: str = Field(description="Folder path to list (inside an approved folder)")


class SearchParams(BaseModel):
    query: str = Field(min_length=1, description="Text to find in file names or contents")
    folder: str = Field(default="", description="Optional subfolder to search in")


class ReadParams(BaseModel):
    path: str = Field(description="Path of a text file to read")


class WriteParams(BaseModel):
    path: str = Field(description="Path of the file to create or overwrite")
    content: str = Field(description="Full text content to write")
    overwrite: bool = Field(default=False, description="Allow replacing an existing file")


class MoveParams(BaseModel):
    source: str = Field(description="Existing file or folder path")
    destination: str = Field(description="New path (rename or move)")


class DeleteParams(BaseModel):
    path: str = Field(description="File or folder to move to the Trash")


def register_file_tools(
    registry: ToolRegistry, roots: ApprovedRoots, max_read_bytes: int = 262_144
) -> None:
    def _guard(raw: str, for_write: bool = False) -> Path | ToolResult:
        try:
            return roots.resolve(raw, for_write=for_write)
        except PathOutsideRootsError as exc:
            return ToolResult(ok=False, error=str(exc))

    async def list_folder(p: ListParams) -> ToolResult:
        target = _guard(p.folder)
        if isinstance(target, ToolResult):
            return target

        def _list() -> ToolResult:
            if not target.is_dir():
                return ToolResult(ok=False, error=f"Not a folder: {p.folder}")
            entries = []
            for child in sorted(target.iterdir()):
                if child.name.startswith("."):
                    continue
                kind = "folder" if child.is_dir() else "file"
                size = child.stat().st_size if child.is_file() else None
                entries.append({"name": child.name, "kind": kind, "size": size})
            return ToolResult(ok=True, data={"folder": str(target), "entries": entries[:200]})

        return await asyncio.to_thread(_list)

    async def search(p: SearchParams) -> ToolResult:
        base = _guard(p.folder) if p.folder else None
        if isinstance(base, ToolResult):
            return base

        def _search() -> ToolResult:
            search_roots = [base] if base else roots.roots()
            needle = p.query.lower()
            hits: list[dict] = []
            for root in search_roots:
                if len(hits) >= 30:
                    break
                for path in iter_search_files(root):
                    if len(hits) >= 30:
                        break
                    if needle in path.name.lower():
                        hits.append({"path": str(path), "match": "name"})
                        continue
                    try:
                        is_small_text = (
                            path.suffix.lower() in TEXT_SUFFIXES and path.stat().st_size < 1_000_000
                        )
                        if is_small_text and needle in path.read_text(errors="ignore").lower():
                            hits.append({"path": str(path), "match": "content"})
                    except OSError:
                        continue
            return ToolResult(ok=True, data={"query": p.query, "results": hits})

        return await asyncio.to_thread(_search)

    async def read_file(p: ReadParams) -> ToolResult:
        target = _guard(p.path)
        if isinstance(target, ToolResult):
            return target

        def _read() -> ToolResult:
            if not target.is_file():
                return ToolResult(ok=False, error=f"Not a file: {p.path}")
            if target.suffix.lower() not in TEXT_SUFFIXES:
                return ToolResult(
                    ok=False,
                    error=f"Unsupported format '{target.suffix}'. Only text formats are readable.",
                )
            data = target.read_bytes()[:max_read_bytes]
            text = data.decode("utf-8", errors="replace")
            truncated = target.stat().st_size > max_read_bytes
            return ToolResult(
                ok=True,
                data={"path": str(target), "truncated": truncated, "content": text},
            )

        return await asyncio.to_thread(_read)

    async def write_file(p: WriteParams) -> ToolResult:
        target = _guard(p.path, for_write=True)
        if isinstance(target, ToolResult):
            return target

        def _write() -> ToolResult:
            if target.exists() and not p.overwrite:
                return ToolResult(
                    ok=False, error=f"File exists: {p.path}. Set overwrite to replace it."
                )
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(p.content, encoding="utf-8")
            return ToolResult(ok=True, data=f"Wrote {len(p.content)} characters to {target}")

        return await asyncio.to_thread(_write)

    async def stage_write(p: WriteParams) -> StagedAction | ToolResult:
        target = _guard(p.path, for_write=True)
        if isinstance(target, ToolResult):
            return target
        if target.exists() and not p.overwrite:
            return ToolResult(
                ok=False, error=f"File exists: {p.path}. Set overwrite to replace it."
            )
        staging_dir = Path(tempfile.mkdtemp(prefix="hearth-intentseal-stage-"))
        op = await asyncio.to_thread(
            lambda: StagedFileWrite(target, p.content, staging_dir).stage()
        )
        undo: dict = {}
        undo_record = []

        async def commit() -> ToolResult:
            record = await asyncio.to_thread(op.commit)
            undo_record.append(record)
            undo.update(
                {
                    "kind": record.kind,
                    "detail": record.detail,
                    "reversible": record.reversible,
                }
            )
            return ToolResult(ok=True, data=f"Wrote {len(p.content)} characters to {target}")

        async def discard() -> None:
            await asyncio.to_thread(op.discard)
            await asyncio.to_thread(shutil.rmtree, staging_dir, True)

        async def undo_commit() -> None:
            if undo_record:
                await asyncio.to_thread(op.undo, undo_record[-1])

        return StagedAction(
            semantic_diff=op.diff().render(),
            commit=commit,
            discard=discard,
            undo_metadata=lambda: dict(undo),
            undo=undo_commit,
        )

    def write_state(p: WriteParams) -> str:
        target = _guard(p.path, for_write=True)
        if isinstance(target, ToolResult):
            return ""
        return canonical_file(str(target)).attributes.get("content_hash", "")

    async def move(p: MoveParams) -> ToolResult:
        src = _guard(p.source)
        if isinstance(src, ToolResult):
            return src
        dst = _guard(p.destination, for_write=True)
        if isinstance(dst, ToolResult):
            return dst

        def _move() -> ToolResult:
            if not src.exists():
                return ToolResult(ok=False, error=f"Source does not exist: {p.source}")
            if dst.exists():
                return ToolResult(ok=False, error=f"Destination already exists: {p.destination}")
            dst.parent.mkdir(parents=True, exist_ok=True)
            src.rename(dst)
            return ToolResult(ok=True, data=f"Moved {src} -> {dst}")

        return await asyncio.to_thread(_move)

    async def delete(p: DeleteParams) -> ToolResult:
        target = _guard(p.path)
        if isinstance(target, ToolResult):
            return target

        def _delete() -> ToolResult:
            if not target.exists():
                return ToolResult(ok=False, error=f"Does not exist: {p.path}")
            from send2trash import send2trash

            send2trash(str(target))
            return ToolResult(ok=True, data=f"Moved to Trash: {target}")

        return await asyncio.to_thread(_delete)

    registry.register(
        ToolSpec(
            name="files_list",
            description="List the contents of a folder inside the user's approved folders.",
            params_model=ListParams,
            risk=RiskLevel.READ,
            permission="files",
            handler=list_folder,
        )
    )
    registry.register(
        ToolSpec(
            name="files_search",
            description="Search approved folders for files by name or text content.",
            params_model=SearchParams,
            risk=RiskLevel.READ,
            permission="files",
            handler=search,
            timeout_s=60,
        )
    )
    registry.register(
        ToolSpec(
            name="files_read",
            description="Read a text file (txt, md, code, csv, json...) from an approved folder.",
            params_model=ReadParams,
            risk=RiskLevel.READ,
            permission="files",
            handler=read_file,
        )
    )
    registry.register(
        ToolSpec(
            name="files_write",
            description="Create or overwrite a text file inside an approved folder.",
            params_model=WriteParams,
            risk=RiskLevel.WRITE,
            permission="files",
            handler=write_file,
            stager=stage_write,
            rollback_supported=True,
            postcondition_supported=True,
            state_probe=write_state,
            expected_post_state=lambda p: hashlib.sha256(p.content.encode("utf-8")).hexdigest(),
            preview=lambda p: (
                f"Write file: {p.path}\nOverwrite existing: {p.overwrite}\n"
                f"--- content ({len(p.content)} chars) ---\n{p.content[:1500]}"
            ),
        )
    )
    registry.register(
        ToolSpec(
            name="files_move",
            description="Move or rename a file/folder within the approved folders.",
            params_model=MoveParams,
            risk=RiskLevel.WRITE,
            permission="files",
            handler=move,
            preview=lambda p: f"Move/rename:\n  from: {p.source}\n  to:   {p.destination}",
        )
    )
    registry.register(
        ToolSpec(
            name="files_delete",
            description="Move a file or folder in an approved folder to the Trash.",
            params_model=DeleteParams,
            risk=RiskLevel.WRITE,
            permission="files",
            handler=delete,
            preview=lambda p: f"Move to Trash: {p.path}\n(Recoverable from the macOS Trash)",
        )
    )

    # ---- Capability 1: full-text content search with line-level snippets ----

    class ContentSearchParams(BaseModel):
        keyword: str = Field(
            min_length=1,
            max_length=200,
            description="Word or phrase to search for inside file contents",
        )
        folder: str = Field(
            default="",
            description=(
                "Optional subfolder to restrict the search (must be inside an approved folder)"
            ),
        )
        case_sensitive: bool = Field(
            default=False, description="Whether the search is case-sensitive"
        )

    async def search_content(p: ContentSearchParams) -> ToolResult:
        base = _guard(p.folder) if p.folder else None
        if isinstance(base, ToolResult):
            return base

        def _search_content() -> ToolResult:
            search_roots = [base] if base else roots.roots()
            needle = p.keyword if p.case_sensitive else p.keyword.lower()
            hits: list[dict] = []
            files_scanned = 0
            for root in search_roots:
                if len(hits) >= 50:
                    break
                for path in iter_search_files(root):
                    if len(hits) >= 50:
                        break
                    if path.suffix.lower() not in TEXT_SUFFIXES:
                        continue
                    try:
                        if path.stat().st_size > 2_000_000:
                            continue  # skip very large files
                        text = path.read_text(errors="ignore")
                    except OSError:
                        continue
                    files_scanned += 1
                    lines = text.splitlines()
                    for lineno, line in enumerate(lines, start=1):
                        haystack = line if p.case_sensitive else line.lower()
                        if needle in haystack:
                            hits.append(
                                {
                                    "file": str(path),
                                    "line": lineno,
                                    "snippet": line.strip()[:300],
                                }
                            )
                            if len(hits) >= 50:
                                break
            return ToolResult(
                ok=True,
                data={
                    "keyword": p.keyword,
                    "files_scanned": files_scanned,
                    "match_count": len(hits),
                    "truncated": len(hits) >= 50,
                    "matches": hits,
                },
            )

        return await asyncio.to_thread(_search_content)

    registry.register(
        ToolSpec(
            name="files_search_content",
            description=(
                "Search the contents of text files in approved folders for a keyword or phrase. "
                "Returns matching lines with file path, line number, and a snippet. "
                "Use this when you need to find where specific text appears inside files."
            ),
            params_model=ContentSearchParams,
            risk=RiskLevel.READ,
            permission="files",
            handler=search_content,
            timeout_s=90,
        )
    )

    # ---- Vision: let the model look at an image in an approved folder ----

    class ViewImageParams(BaseModel):
        path: str = Field(description="Path of an image file (png/jpg/webp/…) to look at")

    async def view_image(p: ViewImageParams) -> ToolResult:
        from ...images import ImageError, encode_image_file, is_image_path

        target = _guard(p.path)
        if isinstance(target, ToolResult):
            return target
        if not is_image_path(target):
            return ToolResult(ok=False, error=f"Not a supported image format: {p.path}")

        def _load() -> ToolResult:
            if not target.is_file():
                return ToolResult(ok=False, error=f"Not a file: {p.path}")
            try:
                encoded = encode_image_file(target)
            except ImageError as exc:
                return ToolResult(ok=False, error=str(exc))
            return ToolResult(
                ok=True,
                data=f"Image loaded: {target.name}. Describe or analyze what you see.",
                image_b64=encoded,
            )

        return await asyncio.to_thread(_load)

    registry.register(
        ToolSpec(
            name="files_view_image",
            description=(
                "Look at an image file inside an approved folder (photo, screenshot, "
                "diagram, scanned document) and analyze what it shows. The image is "
                "downscaled before it reaches the model."
            ),
            params_model=ViewImageParams,
            risk=RiskLevel.READ,
            permission="files",
            handler=view_image,
            timeout_s=30,
        )
    )
