import json
import os
from pathlib import Path
from typing import Any
from urllib.parse import quote
from uuid import uuid4

import aiofiles
import httpx
from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from jinja2 import Environment, FileSystemLoader, select_autoescape
from pydantic import BaseModel, Field


AUDIO_ROOT = Path(
    os.environ.get(
        "AUDIO_ROOT",
        "/remote/Audio",
    )
).resolve()

CORE_URL = os.environ.get(
    "CORE_URL",
    "http://ai-gateway:8080",
).rstrip("/")

MAX_UPLOAD_MB = int(
    os.environ.get(
        "MAX_UPLOAD_MB",
        "2048",
    )
)

MAX_UPLOAD_BYTES = MAX_UPLOAD_MB * 1024 * 1024

SUPPORTED_EXTENSIONS = {
    ".aac",
    ".aiff",
    ".alac",
    ".flac",
    ".m4a",
    ".m4v",
    ".mkv",
    ".mov",
    ".mp3",
    ".mp4",
    ".mpeg",
    ".mpga",
    ".ogg",
    ".opus",
    ".wav",
    ".webm",
    ".wma",
}

FOLDERS = (
    "incoming",
    "processing",
    "ready",
    "failed",
    "archive",
)



class KnowledgeChatRequest(BaseModel):
    query: str = Field(min_length=1, max_length=4000)
    limit: int = Field(default=5, ge=1, le=10)
    temperature: float = Field(default=0.1, ge=0.0, le=2.0)


app = FastAPI(
    title="Audio Lab",
    version="0.2.0",
)


templates = Environment(
    loader=FileSystemLoader(
        "/app/app/templates"
    ),
    autoescape=select_autoescape(
        ["html", "xml"]
    ),
)


def ensure_directories() -> None:
    for folder in FOLDERS:
        directory = AUDIO_ROOT / folder
        directory.mkdir(
            parents=True,
            exist_ok=True,
        )


def safe_path(
    folder: str,
    filename: str,
) -> Path:
    if folder not in FOLDERS:
        raise HTTPException(
            status_code=404,
            detail="Unknown folder",
        )

    base = (
        AUDIO_ROOT / folder
    ).resolve()

    path = (
        base / Path(filename).name
    ).resolve()

    if path != base and base not in path.parents:
        raise HTTPException(
            status_code=400,
            detail="Invalid path",
        )

    return path


def unique_destination(
    path: Path,
) -> Path:
    if not path.exists():
        return path

    counter = 1

    while True:
        candidate = path.with_name(
            f"{path.stem}_{counter}{path.suffix}"
        )

        if not candidate.exists():
            return candidate

        counter += 1


def file_info(
    path: Path,
) -> dict[str, Any]:
    stat = path.stat()

    return {
        "name": path.name,
        "size": stat.st_size,
        "modified": int(stat.st_mtime),
        "suffix": path.suffix.lower(),
    }


def list_folder(
    folder: str,
) -> list[dict[str, Any]]:
    directory = AUDIO_ROOT / folder

    if not directory.exists():
        return []

    files = [
        file_info(path)
        for path in directory.iterdir()
        if path.is_file()
        and not path.name.startswith(".")
    ]

    return sorted(
        files,
        key=lambda item: item["modified"],
        reverse=True,
    )


async def core_status() -> dict[str, Any] | None:
    try:
        async with httpx.AsyncClient(
            timeout=5.0
        ) as client:
            response = await client.get(
                f"{CORE_URL}/dashboard"
            )

        if response.status_code != 200:
            return None

        payload = response.json()

        if not isinstance(payload, dict):
            return None

        return payload

    except (
        httpx.HTTPError,
        ValueError,
    ):
        return None


async def audio_tasks() -> dict[str, Any]:
    try:
        async with httpx.AsyncClient(
            timeout=10.0
        ) as client:
            response = await client.get(
                f"{CORE_URL}/audio-lab/tasks",
                params={
                    "limit": 100,
                },
            )

        if response.status_code != 200:
            return {
                "tasks": [],
                "total": 0,
            }

        payload = response.json()

        if not isinstance(payload, dict):
            return {
                "tasks": [],
                "total": 0,
            }

        tasks = payload.get("tasks")

        if not isinstance(tasks, list):
            payload["tasks"] = []

        return payload

    except (
        httpx.HTTPError,
        ValueError,
    ):
        return {
            "tasks": [],
            "total": 0,
        }


async def search_knowledge(
    query: str,
    *,
    limit: int = 5,
) -> dict[str, Any]:
    normalized_query = " ".join(query.split()).strip()

    if not normalized_query:
        return {
            "query": "",
            "count": 0,
            "results": [],
            "error": None,
        }

    try:
        async with httpx.AsyncClient(
            timeout=1800.0
        ) as client:
            response = await client.post(
                f"{CORE_URL}/knowledge/search",
                json={
                    "query": normalized_query,
                    "limit": limit,
                    "source_type": "transcription",
                    "project": "audio-lab",
                },
            )

        try:
            payload = response.json()
        except ValueError:
            payload = {}

        if response.status_code >= 400:
            detail = (
                payload.get("detail")
                if isinstance(payload, dict)
                else None
            )

            return {
                "query": normalized_query,
                "count": 0,
                "results": [],
                "error": (
                    str(detail)
                    if detail
                    else response.text[:1000]
                ),
            }

        if not isinstance(payload, dict):
            return {
                "query": normalized_query,
                "count": 0,
                "results": [],
                "error": "Некоректна відповідь пошуку",
            }

        results = payload.get("results", [])

        if not isinstance(results, list):
            results = []

        return {
            "query": normalized_query,
            "count": len(results),
            "results": [
                result
                for result in results
                if isinstance(result, dict)
            ],
            "error": None,
        }

    except httpx.HTTPError as exc:
        return {
            "query": normalized_query,
            "count": 0,
            "results": [],
            "error": f"Homelab Core недоступний: {exc}",
        }


async def call_core_action(
    *,
    method: str,
    endpoint: str,
) -> tuple[bool, str]:
    try:
        async with httpx.AsyncClient(
            timeout=1800.0
        ) as client:
            response = await client.request(
                method,
                f"{CORE_URL}{endpoint}",
            )

        try:
            payload = response.json()
        except ValueError:
            payload = {}

        if response.status_code >= 400:
            detail = (
                payload.get("detail")
                if isinstance(payload, dict)
                else None
            )

            message = (
                str(detail)
                if detail
                else response.text
            )

            return (
                False,
                message[:1000],
            )

        if isinstance(payload, dict):
            status = payload.get("status")
            chunk_count = payload.get(
                "chunk_count"
            )

            if status == "indexed":
                return (
                    True,
                    (
                        "Транскрипцію додано в Qdrant. "
                        f"Фрагментів: {chunk_count or 0}."
                    ),
                )

            if status == "not_indexed":
                return (
                    True,
                    "Транскрипцію видалено з Qdrant.",
                )

        return (
            True,
            "Операцію успішно виконано.",
        )

    except httpx.HTTPError as exc:
        return (
            False,
            f"Homelab Core недоступний: {exc}",
        )


@app.on_event("startup")
async def startup() -> None:
    ensure_directories()


@app.get(
    "/",
    response_class=HTMLResponse,
)
async def index(
    request: Request,
) -> HTMLResponse:
    status = await core_status()
    tasks_payload = await audio_tasks()

    search_query = request.query_params.get(
        "q",
        "",
    ).strip()

    search_payload = await search_knowledge(
        search_query,
        limit=8,
    )

    raw_tasks = tasks_payload.get(
        "tasks",
        [],
    )

    tasks = [
        task
        for task in raw_tasks
        if isinstance(task, dict)
    ]

    active_tasks = [
        task
        for task in tasks
        if task.get("status")
        in {
            "waiting",
            "running",
        }
    ]

    completed_tasks = [
        task
        for task in tasks
        if task.get("status")
        == "completed"
    ]

    failed_tasks = [
        task
        for task in tasks
        if task.get("status")
        == "failed"
    ]

    template = templates.get_template(
        "index.html"
    )

    return HTMLResponse(
        template.render(
            status=status,
            tasks=tasks,
            active_tasks=active_tasks,
            completed_tasks=completed_tasks,
            failed_tasks=failed_tasks,
            folders={
                folder: list_folder(folder)
                for folder in FOLDERS
            },
            max_upload_mb=MAX_UPLOAD_MB,
            success=request.query_params.get(
                "success"
            ),
            error=request.query_params.get(
                "error"
            ),
            search_query=search_payload["query"],
            search_results=search_payload["results"],
            search_count=search_payload["count"],
            search_error=search_payload["error"],
        )
    )


@app.post("/upload")
async def upload(
    file: UploadFile = File(...),
) -> RedirectResponse:
    original_name = Path(
        file.filename or ""
    ).name

    if not original_name:
        raise HTTPException(
            status_code=400,
            detail="Missing filename",
        )

    suffix = Path(
        original_name
    ).suffix.lower()

    if suffix not in SUPPORTED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=(
                "Unsupported file extension: "
                f"{suffix or '[none]'}"
            ),
        )

    incoming_directory = (
        AUDIO_ROOT / "incoming"
    )

    destination = unique_destination(
        incoming_directory / original_name
    )

    temporary = (
        incoming_directory
        / f".upload-{uuid4().hex}.part"
    )

    written = 0

    try:
        async with aiofiles.open(
            temporary,
            "wb",
        ) as output:
            while True:
                chunk = await file.read(
                    1024 * 1024
                )

                if not chunk:
                    break

                written += len(chunk)

                if written > MAX_UPLOAD_BYTES:
                    raise HTTPException(
                        status_code=413,
                        detail=(
                            "File exceeds the "
                            f"{MAX_UPLOAD_MB} MB limit"
                        ),
                    )

                await output.write(chunk)

        temporary.rename(destination)

    except Exception:
        temporary.unlink(
            missing_ok=True
        )
        raise

    finally:
        await file.close()

    return RedirectResponse(
        url=(
            "/?success="
            + quote(
                (
                    f"Файл «{destination.name}» "
                    "додано в чергу."
                )
            )
            + "#tasks"
        ),
        status_code=303,
    )


@app.get(
    "/file/{folder}/{filename}"
)
async def download_file(
    folder: str,
    filename: str,
) -> FileResponse:
    path = safe_path(
        folder,
        filename,
    )

    if (
        not path.exists()
        or not path.is_file()
    ):
        raise HTTPException(
            status_code=404,
            detail="File not found",
        )

    return FileResponse(
        path,
        filename=path.name,
    )


@app.get(
    "/view/{folder}/{filename}",
    response_class=HTMLResponse,
)
async def view_file(
    folder: str,
    filename: str,
) -> HTMLResponse:
    path = safe_path(
        folder,
        filename,
    )

    if (
        not path.exists()
        or not path.is_file()
    ):
        raise HTTPException(
            status_code=404,
            detail="File not found",
        )

    if path.suffix.lower() not in {
        ".txt",
        ".json",
    }:
        raise HTTPException(
            status_code=400,
            detail=(
                "Only TXT and JSON "
                "can be viewed"
            ),
        )

    content = path.read_text(
        encoding="utf-8",
        errors="replace",
    )

    if path.suffix.lower() == ".json":
        try:
            content = json.dumps(
                json.loads(content),
                ensure_ascii=False,
                indent=2,
            )
        except ValueError:
            pass

    template = templates.get_template(
        "view.html"
    )

    return HTMLResponse(
        template.render(
            filename=path.name,
            folder=folder,
            content=content,
        )
    )


@app.post(
    "/delete/{folder}/{filename}"
)
async def delete_file(
    folder: str,
    filename: str,
) -> RedirectResponse:
    if folder not in {
        "ready",
        "failed",
        "archive",
    }:
        raise HTTPException(
            status_code=400,
            detail=(
                "Files in this folder "
                "cannot be deleted"
            ),
        )

    path = safe_path(
        folder,
        filename,
    )

    if (
        path.exists()
        and path.is_file()
    ):
        path.unlink()

    return RedirectResponse(
        url=(
            "/?success="
            + quote(
                f"Файл «{filename}» видалено."
            )
        ),
        status_code=303,
    )


@app.post(
    "/tasks/{task_id}/index"
)
async def index_task(
    task_id: str,
) -> RedirectResponse:
    ok, message = await call_core_action(
        method="POST",
        endpoint=(
            f"/audio-lab/tasks/"
            f"{task_id}/index"
        ),
    )

    parameter = (
        "success"
        if ok
        else "error"
    )

    return RedirectResponse(
        url=(
            f"/?{parameter}="
            f"{quote(message)}#tasks"
        ),
        status_code=303,
    )


@app.post(
    "/tasks/{task_id}/reindex"
)
async def reindex_task(
    task_id: str,
) -> RedirectResponse:
    ok, message = await call_core_action(
        method="POST",
        endpoint=(
            f"/audio-lab/tasks/"
            f"{task_id}/reindex"
        ),
    )

    parameter = (
        "success"
        if ok
        else "error"
    )

    return RedirectResponse(
        url=(
            f"/?{parameter}="
            f"{quote(message)}#tasks"
        ),
        status_code=303,
    )


@app.post(
    "/tasks/{task_id}/unindex"
)
async def unindex_task(
    task_id: str,
) -> RedirectResponse:
    ok, message = await call_core_action(
        method="DELETE",
        endpoint=(
            f"/audio-lab/tasks/"
            f"{task_id}/index"
        ),
    )

    parameter = (
        "success"
        if ok
        else "error"
    )

    return RedirectResponse(
        url=(
            f"/?{parameter}="
            f"{quote(message)}#tasks"
        ),
        status_code=303,
    )


@app.post("/api/knowledge/chat")
async def knowledge_chat(
    request: KnowledgeChatRequest,
) -> dict[str, Any]:
    try:
        async with httpx.AsyncClient(
            timeout=1800.0
        ) as client:
            response = await client.post(
                f"{CORE_URL}/knowledge/chat",
                json={
                    "query": request.query,
                    "model": "qwen-general",
                    "limit": request.limit,
                    "source_type": "transcription",
                    "project": "audio-lab",
                    "temperature": request.temperature,
                },
            )

        try:
            payload = response.json()
        except ValueError as exc:
            raise HTTPException(
                status_code=502,
                detail="Homelab Core returned invalid JSON",
            ) from exc

        if response.status_code >= 400:
            detail = (
                payload.get("detail")
                if isinstance(payload, dict)
                else None
            )

            raise HTTPException(
                status_code=response.status_code,
                detail=detail or "RAG request failed",
            )

        if not isinstance(payload, dict):
            raise HTTPException(
                status_code=502,
                detail="Invalid RAG response",
            )

        return payload

    except HTTPException:
        raise

    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Homelab Core недоступний: {exc}",
        ) from exc


@app.get("/health")
async def health() -> dict[str, Any]:
    status = await core_status()

    return {
        "status": "ok",
        "version": "0.2.0",
        "audio_root": str(
            AUDIO_ROOT
        ),
        "core_available": (
            status is not None
        ),
        "folders": {
            folder: len(
                list_folder(folder)
            )
            for folder in FOLDERS
        },
    }
