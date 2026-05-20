from __future__ import annotations

import json
import os
import re
from urllib.error import HTTPError
import urllib.request
from typing import Any


NOTION_API_URL = "https://api.notion.com/v1/pages"
NOTION_DATABASE_API_URL = "https://api.notion.com/v1/databases"
NOTION_BLOCKS_API_URL = "https://api.notion.com/v1/blocks"
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


def upsert_chatgpt_summary_page(
    summary: dict[str, Any],
    database_id: str,
    token: str,
    append_children_to_existing: bool = True,
) -> dict[str, Any]:
    database = retrieve_database(database_id, token)
    pmid = str(summary.get("pmid", "")).strip()
    existing_page = find_notion_page_by_pmid(pmid, database_id, token, database=database) if pmid else None
    payload = build_chatgpt_summary_payload(summary, database_id, database=database)

    if existing_page:
        page_id = existing_page["id"]
        page = update_notion_page(page_id, payload, token)
        if append_children_to_existing:
            append_block_children(page_id, payload.get("children", []), token)
        page["import_action"] = "updated"
        return page

    page = send_create_page(payload, token)
    page["import_action"] = "created"
    return page


def find_notion_page_by_pmid(
    pmid: str,
    database_id: str,
    token: str,
    database: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    database = database or retrieve_database(database_id, token)
    schema = database.get("properties", {})
    pmid_property = schema.get("PMID")
    if not pmid_property:
        return None

    property_type = pmid_property.get("type", "rich_text")
    filter_type = "rich_text" if property_type in {"title", "rich_text"} else property_type
    payload = {
        "filter": {
            "property": "PMID",
            filter_type: {"equals": str(pmid)},
        },
        "page_size": 1,
    }
    request = urllib.request.Request(
        f"{NOTION_DATABASE_API_URL}/{database_id}/query",
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
            result = json.loads(response.read().decode("utf-8"))
    except HTTPError as error:
        body = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Notion API error {error.code}: {body}") from error

    results = result.get("results", [])
    return results[0] if results else None


def build_chatgpt_summary_payload(
    summary: dict[str, Any],
    database_id: str,
    database: dict[str, Any],
) -> dict[str, Any]:
    schema = database.get("properties", {})
    graphic_url = str(summary.get("graphic_url") or "").strip()
    title_name = _title_property_name(schema)
    properties: dict[str, Any] = {
        title_name: {"title": [{"text": {"content": _truncate(summary.get("title", ""), 2000)}}]},
    }

    pmid = str(summary.get("pmid", "")).strip()
    _add_property(properties, schema, "PMID", {"rich_text": [{"text": {"content": pmid}}]})
    _add_property(properties, schema, "PubMed URL", {"url": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/" if pmid else None})
    _add_property(properties, schema, "DOI", {"rich_text": [{"text": {"content": str(summary.get("doi", "") or "")}}]})
    _add_property(properties, schema, "Journal", {"select": _select(summary.get("journal"))})
    _add_property(properties, schema, "Year", {"number": _number(summary.get("year"))})
    _add_property(properties, schema, "Topic", {"multi_select": [{"name": value} for value in _list(summary.get("topic"))]})
    _add_property(properties, schema, "Practice Change", {"select": _select(summary.get("practice_change"))})
    _add_property(properties, schema, "Take Home Message", {"rich_text": [{"text": {"content": _truncate(summary.get("one_line_summary", ""), 2000)}}]})
    if graphic_url:
        _add_property(properties, schema, "Graphic URL", {"url": graphic_url})
        _add_property(
            properties,
            schema,
            "Graphic Image",
            {"files": [{"name": os.path.basename(graphic_url), "type": "external", "external": {"url": graphic_url}}]},
        )
    _add_property(properties, schema, "Created By AI", {"checkbox": True})
    _add_property(properties, schema, "Human Checked", {"checkbox": _checkbox(summary.get("human_checked"))})

    payload: dict[str, Any] = {
        "parent": {"database_id": database_id},
        "properties": properties,
        "children": chatgpt_summary_children(summary),
    }
    if graphic_url:
        payload["cover"] = {"type": "external", "external": {"url": graphic_url}}
    return payload


def send_create_page(payload: dict[str, Any], token: str) -> dict[str, Any]:
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


def update_notion_page(page_id: str, payload: dict[str, Any], token: str) -> dict[str, Any]:
    update_payload = {"properties": payload.get("properties", {})}
    if payload.get("cover"):
        update_payload["cover"] = payload["cover"]

    request = urllib.request.Request(
        f"{NOTION_API_URL}/{page_id}",
        data=json.dumps(update_payload).encode("utf-8"),
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


def append_block_children(page_id: str, children: list[dict[str, Any]], token: str) -> dict[str, Any] | None:
    if not children:
        return None
    request = urllib.request.Request(
        f"{NOTION_BLOCKS_API_URL}/{page_id}/children",
        data=json.dumps({"children": children}).encode("utf-8"),
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


def chatgpt_summary_children(summary: dict[str, Any]) -> list[dict[str, Any]]:
    sections = [
        ("PICO", summary.get("pico", "")),
        ("Figure/Table Summary", summary.get("figure_table_summary", "")),
        ("Main Results", summary.get("main_results", "")),
        ("Safety", summary.get("safety", "")),
        ("Limitations", summary.get("limitations", "")),
        ("Applicability to Japanese Pediatric Clinic", summary.get("applicability_to_japanese_pediatric_clinic", "")),
        ("Tomorrow Action", summary.get("tomorrow_action", "")),
    ]
    blocks: list[dict[str, Any]] = []
    for heading, value in sections:
        blocks.append(
            {
                "object": "block",
                "type": "heading_2",
                "heading_2": {"rich_text": [{"type": "text", "text": {"content": heading}}]},
            }
        )
        for paragraph in _paragraph_chunks(value):
            blocks.append(
                {
                    "object": "block",
                    "type": "paragraph",
                    "paragraph": {"rich_text": [{"type": "text", "text": {"content": paragraph}}]},
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


def _number(value: Any) -> int | float | None:
    if value in (None, ""):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return int(number) if number.is_integer() else number


def _checkbox(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value in (None, ""):
        return False
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "on", "checked", "済", "確認済み"}
    return bool(value)


def _list(value: Any) -> list[str]:
    if value in (None, ""):
        return []
    if isinstance(value, list):
        values = []
        for item in value:
            values.extend(_split_select_values(item))
        return values
    return _split_select_values(value)


def _split_select_values(value: Any) -> list[str]:
    text = str(value or "").strip()
    if not text:
        return []
    parts = [part.strip() for part in re.split(r"[,、;；]", text)]
    return [part[:100] for part in parts if part]


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


def _paragraph_chunks(value: Any, limit: int = 1900) -> list[str]:
    text = _stringify_body_value(value)
    if not text:
        return [""]
    chunks = []
    current = ""
    for line in text.splitlines() or [text]:
        candidate = f"{current}\n{line}" if current else line
        if len(candidate) <= limit:
            current = candidate
            continue
        if current:
            chunks.append(current)
        while len(line) > limit:
            chunks.append(line[:limit])
            line = line[limit:]
        current = line
    if current:
        chunks.append(current)
    return chunks


def _stringify_body_value(value: Any) -> str:
    if value in (None, ""):
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "\n".join(f"- {_stringify_body_value(item)}" for item in value)
    if isinstance(value, dict):
        lines = []
        for key, item in value.items():
            lines.append(f"{key}: {_stringify_body_value(item)}")
        return "\n".join(lines)
    return str(value)
