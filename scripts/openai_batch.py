from __future__ import annotations

import json
import mimetypes
import os
import uuid
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
import urllib.request


OPENAI_API_BASE = "https://api.openai.com/v1"


class OpenAIAPIError(RuntimeError):
    pass


def api_key_from_env() -> str | None:
    load_dotenv()
    return os.getenv("OPENAI_API_KEY")


def load_dotenv(path: str = ".env") -> None:
    """Load simple KEY=VALUE settings without adding a dependency."""
    _load_dotenv(path)


def upload_file(path: Path, api_key: str, purpose: str) -> dict[str, Any]:
    content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    body, boundary = _multipart_body(
        fields={"purpose": purpose},
        files={"file": (path.name, path.read_bytes(), content_type)},
    )
    return _request_json(
        "POST",
        f"{OPENAI_API_BASE}/files",
        api_key,
        data=body,
        content_type=f"multipart/form-data; boundary={boundary}",
        timeout=120,
    )


def create_batch(input_file_id: str, api_key: str, endpoint: str = "/v1/responses") -> dict[str, Any]:
    return _request_json(
        "POST",
        f"{OPENAI_API_BASE}/batches",
        api_key,
        payload={
            "input_file_id": input_file_id,
            "endpoint": endpoint,
            "completion_window": "24h",
            "metadata": {"workflow": "pubmed-grarec-notion"},
        },
    )


def retrieve_batch(batch_id: str, api_key: str) -> dict[str, Any]:
    return _request_json("GET", f"{OPENAI_API_BASE}/batches/{batch_id}", api_key)


def download_file(file_id: str, api_key: str) -> bytes:
    request = urllib.request.Request(
        f"{OPENAI_API_BASE}/files/{file_id}/content",
        headers={"Authorization": f"Bearer {api_key}"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            return response.read()
    except HTTPError as error:
        body = error.read().decode("utf-8", errors="replace")
        raise OpenAIAPIError(f"OpenAI API error {error.code}: {body}") from error


def extract_response_text(body: dict[str, Any]) -> str:
    for output in body.get("output", []):
        if output.get("type") != "message":
            continue
        for content in output.get("content", []):
            if content.get("type") == "output_text" and content.get("text"):
                return str(content["text"])
    return ""


def _request_json(
    method: str,
    url: str,
    api_key: str,
    payload: dict[str, Any] | None = None,
    data: bytes | None = None,
    content_type: str = "application/json",
    timeout: int = 60,
) -> dict[str, Any]:
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = {"Authorization": f"Bearer {api_key}"}
    if data is not None:
        headers["Content-Type"] = content_type
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as error:
        body = error.read().decode("utf-8", errors="replace")
        raise OpenAIAPIError(f"OpenAI API error {error.code}: {body}") from error


def _multipart_body(
    fields: dict[str, str],
    files: dict[str, tuple[str, bytes, str]],
) -> tuple[bytes, str]:
    boundary = f"----pubmed-grarec-{uuid.uuid4().hex}"
    chunks: list[bytes] = []
    for name, value in fields.items():
        chunks.extend(
            [
                f"--{boundary}\r\n".encode(),
                f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode(),
                value.encode(),
                b"\r\n",
            ]
        )
    for name, (filename, content, content_type) in files.items():
        safe_filename = filename.replace('"', "")
        chunks.extend(
            [
                f"--{boundary}\r\n".encode(),
                f'Content-Disposition: form-data; name="{name}"; filename="{safe_filename}"\r\n'.encode(),
                f"Content-Type: {content_type}\r\n\r\n".encode(),
                content,
                b"\r\n",
            ]
        )
    chunks.append(f"--{boundary}--\r\n".encode())
    return b"".join(chunks), boundary


def _load_dotenv(path: str = ".env") -> None:
    if not os.path.exists(path):
        return
    with open(path, "r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))
