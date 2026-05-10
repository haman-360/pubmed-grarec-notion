from __future__ import annotations

import json
import os
from urllib.error import HTTPError
import urllib.request
from typing import Any


NOTION_API_URL = "https://api.notion.com/v1/pages"
NOTION_DATABASE_API_URL = "https://api.notion.com/v1/databases"
NOTION_VERSION = "2022-06-28"


def create_notion_page(summary: dict[str, Any], database_id: str, token: str, graphic_url: str = "") -> dict[str, Any]:
    database = retrieve_database(database_id, token)
    payload = build_notion_payload(summary, database_id, database=database, graphic_url=graphic_url)
    request = urllib.request.Request(
        NOTION_API_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Notion-Version": NOTION_VERSION,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as error:
        body = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Notion API error {error.code}: {body}") from error


def update_notion_page_cover_and_graphic_url(page_id: str, database_id: str, token: str, graphic_url: str) -> dict[str, Any]:
    database = retrieve_database(database_id, token)
    schema = database.get("properties", {})
    properties: dict[str, Any] = {}
    _add_property(properties, schema, "Graphic URL", {"url": graphic_url or None})
    _add_property(
        properties,
        schema,
        "Graphic Image",
        {"files": [{"name": os.path.basename(graphic_url), "type": "external", "external": {"url": graphic_url}}]},
    )
    payload: dict[str, Any] = {
        "cover": {"type": "external", "external": {"url": graphic_url}},
        "properties": properties,
    }
    request = urllib.request.Request(
        f"{NOTION_API_URL}/{page_id}",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Notion-Version": NOTION_VERSION,
        },
        method="PATCH",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as error:
        body = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Notion API error {error.code}: {body}") from error


def retrieve_database(database_id: str, token: str) -> dict[str, Any]:
    request = urllib.request.Request(
        f"{NOTION_DATABASE_API_URL}/{database_id}",
        headers={
            "Authorization": f"Bearer {token}",
            "Notion-Version": NOTION_VERSION,
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as error:
        body = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Notion API error {error.code}: {body}") from error


def build_notion_payload(
    summary: dict[str, Any],
    database_id: str,
    database: dict[str, Any],
    graphic_url: str = "",
) -> dict[str, Any]:
    schema = database.get("properties", {})
    title_name = _title_property_name(schema)
    properties: dict[str, Any] = {
        title_name: {"title": [{"text": {"content": _truncate(summary.get("title", ""), 2000)}}]},
    }

    _add_property(properties, schema, "PMID", {"rich_text": [{"text": {"content": str(summary.get("pmid", ""))}}]})
    _add_property(properties, schema, "PubMed URL", {"url": summary.get("pubmed_url") or None})
    _add_property(properties, schema, "DOI", {"rich_text": [{"text": {"content": summary.get("doi", "")}}]})
    _add_property(properties, schema, "Journal", {"select": _select(summary.get("journal"))})
    _add_property(properties, schema, "Year", {"number": summary.get("year")})
    _add_property(properties, schema, "Topic", {"multi_select": [{"name": value} for value in summary.get("topic", [])]})
    _add_property(properties, schema, "Study Type", {"select": _select(summary.get("study_type"))})
    _add_property(properties, schema, "Read Status", {"status": {"name": summary.get("read_status") or "要確認"}})
    _add_property(properties, schema, "Impact", {"select": _select(summary.get("impact"))})
    _add_property(properties, schema, "Practice Change", {"select": _select(summary.get("practice_change"))})
    _add_property(properties, schema, "Why Important", {"rich_text": [{"text": {"content": _truncate(summary.get("why_important", ""), 2000)}}]})
    _add_property(properties, schema, "Clinical Impact", {"rich_text": [{"text": {"content": _truncate(summary.get("clinical_impact", ""), 2000)}}]})
    _add_property(properties, schema, "Summary JP", {"rich_text": [{"text": {"content": _truncate(summary.get("summary_jp", ""), 2000)}}]})
    _add_property(properties, schema, "Take Home Message", {"rich_text": [{"text": {"content": _truncate(summary.get("take_home_message", ""), 2000)}}]})
    _add_property(properties, schema, "Graphic URL", {"url": graphic_url or None})
    _add_property(properties, schema, "Created By AI", {"checkbox": True})
    _add_property(properties, schema, "Human Checked", {"checkbox": False})

    if summary.get("published_date"):
        _add_property(properties, schema, "Published Date", {"date": {"start": summary["published_date"]}})

    payload: dict[str, Any] = {
        "parent": {"database_id": database_id},
        "properties": properties,
        "children": page_children(summary),
    }

    if graphic_url:
        payload["cover"] = {"type": "external", "external": {"url": graphic_url}}

    return payload


def page_children(summary: dict[str, Any]) -> list[dict[str, Any]]:
    sections = [
        ("Take Home Message", summary.get("take_home_message", "")),
        ("日本語要約", summary.get("summary_jp", "")),
        ("なぜ重要か", summary.get("why_important", "")),
        ("臨床への影響", summary.get("clinical_impact", "")),
        ("原文確認メモ", "対象:\n介入/曝露:\n比較:\n主要アウトカム:\n注意点:"),
    ]
    blocks: list[dict[str, Any]] = []
    for heading, text in sections:
        blocks.append(
            {
                "object": "block",
                "type": "heading_2",
                "heading_2": {"rich_text": [{"type": "text", "text": {"content": heading}}]},
            }
        )
        blocks.append(
            {
                "object": "block",
                "type": "paragraph",
                "paragraph": {"rich_text": [{"type": "text", "text": {"content": _truncate(text, 1900)}}]},
            }
        )
    return blocks


def notion_credentials_from_env() -> tuple[str | None, str | None]:
    _load_dotenv()
    return os.getenv("NOTION_TOKEN"), os.getenv("NOTION_DATABASE_ID")


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


def _select(value: Any) -> dict[str, str] | None:
    if not value:
        return None
    return {"name": str(value)[:100]}


def _title_property_name(schema: dict[str, Any]) -> str:
    for name, definition in schema.items():
        if definition.get("type") == "title":
            return name
    raise RuntimeError("No title property found in Notion database.")


def _add_property(properties: dict[str, Any], schema: dict[str, Any], name: str, value: dict[str, Any]) -> None:
    if name in schema:
        properties[name] = value


def _truncate(value: Any, limit: int) -> str:
    text = str(value or "")
    return text if len(text) <= limit else text[: limit - 3] + "..."
