from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
import urllib.request
from urllib.parse import quote

from fetch_pubmed import PubMedArticle, fetch_pubmed_article
from import_chatgpt_summary import normalize_chatgpt_summary, remember_notion_page
from openai_batch import (
    api_key_from_env,
    create_batch,
    download_file,
    extract_response_text,
    load_dotenv,
    retrieve_batch,
    upload_file,
)
from upload_to_notion import (
    attach_local_graphic,
    find_notion_page_by_pmid,
    notion_credentials_from_env,
    update_notion_page_cover_and_graphic_url,
    upsert_chatgpt_summary_page,
)


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PMIDS = ROOT / "input" / "pmids.txt"
DEFAULT_PDFS = ROOT / "input" / "pdfs"
JOBS = ROOT / "output" / "jobs"
BATCHES = ROOT / "output" / "batches"
SOURCES = ROOT / "output" / "sources"
SUMMARIES = ROOT / "output" / "summaries"
PROMPT_VERSION = "paper-review-v1"
PMC_BIOC_URL = "https://www.ncbi.nlm.nih.gov/research/bionlp/RESTful/pmcoa.cgi/BioC_json/{pmid}/unicode"
DEFAULT_GITHUB_PAGES_BASE_URL = "https://haman-360.github.io/pubmed-grarec-notion"


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser(description="Process PubMed papers with the discounted OpenAI Batch API.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    prepare = subparsers.add_parser("prepare", help="Fetch sources, check Notion duplicates, and estimate cost without OpenAI usage.")
    _add_input_args(prepare)
    prepare.add_argument("--update-existing", action="store_true", help="Reprocess Notion pages that already have a graphic.")

    submit = subparsers.add_parser("submit", help="Submit prepared jobs to the discounted Batch API.")
    submit.add_argument("--max-papers", type=int, default=_env_int("OPENAI_MAX_BATCH_PAPERS", 5))
    submit.add_argument("--yes", action="store_true", help="Submit without an interactive confirmation.")

    subparsers.add_parser("status", help="Show current OpenAI Batch status.")

    resume = subparsers.add_parser("resume", help="Collect completed batches, render graphics, and optionally update Notion.")
    resume.add_argument("--notion", action="store_true", help="Create/update Notion pages and upload graphics.")
    resume.add_argument("--no-render", action="store_true", help="Collect JSON without rendering graphics.")

    auto = subparsers.add_parser(
        "auto",
        help="Prepare, submit, wait for Batch completion, render graphics, and update Notion.",
    )
    _add_input_args(auto)
    auto.add_argument("--update-existing", action="store_true", help="Reprocess Notion pages that already have a graphic.")
    auto.add_argument("--max-papers", type=int, default=_env_int("OPENAI_MAX_BATCH_PAPERS", 5))
    auto.add_argument("--yes", action="store_true", help="Submit without an interactive confirmation.")
    auto.add_argument("--interval-minutes", type=int, default=30, help="Minutes between completion checks (minimum 5).")
    auto.add_argument("--timeout-hours", type=int, default=48, help="Maximum time to keep waiting for Batch completion.")

    args = parser.parse_args()
    if args.command == "prepare":
        prepare_jobs(args)
    elif args.command == "submit":
        submit_jobs(args)
    elif args.command == "status":
        show_status()
    elif args.command == "resume":
        resume_batches(args)
    elif args.command == "auto":
        automatic_workflow(args)


def _add_input_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--pmid", action="append", help="PMID to prepare. May be repeated.")
    parser.add_argument("--pmids-file", default=str(DEFAULT_PMIDS), help="Text file with one PMID per line.")
    parser.add_argument("--pdf-dir", default=str(DEFAULT_PDFS), help="Directory containing PMID_<id>.pdf files.")
    parser.add_argument("--email", default=os.getenv("NCBI_EMAIL", ""), help="Email sent to NCBI Entrez.")


def prepare_jobs(args: argparse.Namespace) -> None:
    _ensure_dirs()
    pmids = _collect_pmids(args.pmid, Path(args.pmids_file), Path(args.pdf_dir))
    if not pmids:
        raise SystemExit("No PMID found. Add IDs to input/pmids.txt or PDFs named PMID_<id>.pdf to input/pdfs/.")

    notion_token, database_id = notion_credentials_from_env()
    total_estimate = 0.0
    ready_count = 0
    print("PMID       STATUS                 SOURCE          EST. BATCH COST")
    for pmid in pmids:
        try:
            article = fetch_pubmed_article(pmid, email=args.email or None)
        except (ValueError, HTTPError, URLError) as error:
            failed_job = _read_json(_job_path(pmid), default={"pmid": pmid})
            failed_job.update({"status": "prepare_error", "prepare_error": str(error), "updated_at": _now()})
            _write_json(_job_path(pmid), failed_job)
            print(f"{pmid:<10} NOT_FOUND              -               $0.000  ({error})")
            continue

        pdf_path = _find_pdf(Path(args.pdf_dir), pmid)
        try:
            source_type, source_path, source_hash, estimated_input = _resolve_source(article, pdf_path)
        except ValueError as error:
            failed_job = _read_json(_job_path(pmid), default={"pmid": pmid})
            failed_job.update({"status": "no_source", "prepare_error": str(error), "updated_at": _now()})
            _write_json(_job_path(pmid), failed_job)
            print(f"{pmid:<10} NO_SOURCE              -               $0.000  ({error})")
            continue
        existing_page = None
        if notion_token and database_id:
            existing_page = find_notion_page_by_pmid(pmid, database_id, notion_token)

        action = "create"
        status = "ready"
        if existing_page:
            action = "update"
            if _notion_has_graphic(existing_page) and not args.update_existing:
                status = "skip_exists"

        previous = _read_json(_job_path(pmid), default={})
        if (
            previous.get("source_hash") == source_hash
            and previous.get("status") in {"batch_submitted", "completed", "notion_updated"}
            and not args.update_existing
        ):
            status = "skip_cached"

        estimated_output = 8000
        estimate = estimate_batch_cost(estimated_input, estimated_output)
        job = {
            "pmid": pmid,
            "article": asdict(article),
            "source_type": source_type,
            "source_path": source_path,
            "source_hash": source_hash,
            "estimated_input_tokens": estimated_input,
            "estimated_output_tokens": estimated_output,
            "estimated_batch_cost_usd": round(estimate, 6),
            "notion_action": action,
            "notion_page_id": existing_page.get("id") if existing_page else None,
            "notion_url": existing_page.get("url") if existing_page else None,
            "status": status,
            "prompt_version": PROMPT_VERSION,
            "updated_at": _now(),
        }
        _write_json(_job_path(pmid), job)
        if status == "ready":
            ready_count += 1
            total_estimate += estimate
        label = status.upper()
        print(f"{pmid:<10} {label:<22} {source_type:<15} ${estimate:.3f}")

    print()
    print(f"Batch投入予定: {ready_count}論文")
    print(f"推定上限: ${total_estimate:.3f}")
    print("OpenAI APIはまだ呼び出していません。")


def submit_jobs(args: argparse.Namespace) -> bool:
    _ensure_dirs()
    api_key = api_key_from_env()
    if not api_key:
        raise SystemExit("OPENAI_API_KEY is required in .env for submit.")

    jobs = [job for job in _load_jobs() if job.get("status") == "ready"][: args.max_papers]
    if not jobs:
        raise SystemExit("No prepared jobs are ready. Run prepare first.")

    max_cost = _env_float("OPENAI_MAX_COST_PER_PAPER_USD", 0.50)
    allowed = []
    for job in jobs:
        if float(job.get("estimated_batch_cost_usd", 0)) > max_cost:
            print(f"Skip PMID {job['pmid']}: estimated cost exceeds ${max_cost:.2f}.")
            continue
        allowed.append(job)
    jobs = allowed
    if not jobs:
        raise SystemExit("All prepared jobs exceed the configured per-paper cost cap.")

    total = sum(float(job.get("estimated_batch_cost_usd", 0)) for job in jobs)
    print(f"Submit {len(jobs)} paper(s) to OpenAI Batch. Estimated maximum: ${total:.3f}")
    if not args.yes:
        answer = input("半額Batchへ投入しますか？ [y/N]: ").strip().lower()
        if answer not in {"y", "yes"}:
            print("中止しました。")
            return False

    model = os.getenv("OPENAI_MODEL", "gpt-5.6-terra")
    detail = os.getenv("OPENAI_PDF_DETAIL", "low")
    requests = []
    for job in jobs:
        if job["source_type"] == "user_pdf" and not job.get("openai_file_id"):
            uploaded = upload_file(ROOT / job["source_path"], api_key, purpose="user_data")
            job["openai_file_id"] = uploaded["id"]
            _write_json(_job_path(job["pmid"]), job)
        custom_id = _custom_id(job)
        requests.append(_batch_request(job, model=model, detail=detail, custom_id=custom_id))
        job["custom_id"] = custom_id

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    jsonl_path = BATCHES / f"batch_input_{stamp}.jsonl"
    jsonl_path.write_text("\n".join(json.dumps(item, ensure_ascii=False) for item in requests) + "\n", encoding="utf-8")
    input_file = upload_file(jsonl_path, api_key, purpose="batch")
    batch = create_batch(input_file["id"], api_key, endpoint="/v1/responses")

    manifest = {
        "batch_id": batch["id"],
        "status": batch.get("status"),
        "input_file_id": input_file["id"],
        "input_path": str(jsonl_path.relative_to(ROOT)),
        "pmids": [job["pmid"] for job in jobs],
        "created_at": _now(),
    }
    _write_json(BATCHES / f"{batch['id']}.json", manifest)
    for job in jobs:
        job["batch_id"] = batch["id"]
        job["status"] = "batch_submitted"
        job["updated_at"] = _now()
        _write_json(_job_path(job["pmid"]), job)
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return True


def automatic_workflow(args: argparse.Namespace) -> None:
    """Run the paid submission once, then wait locally until Notion is updated."""
    prepare_jobs(args)
    if not submit_jobs(args):
        return
    watch_batches(args)


def watch_batches(args: argparse.Namespace) -> None:
    interval_seconds = max(5, int(args.interval_minutes)) * 60
    timeout_seconds = max(1, int(args.timeout_hours)) * 60 * 60
    deadline = time.monotonic() + timeout_seconds
    print()
    print("Batch完了を待機します。このTerminalは閉じないでください。")
    print("Macがスリープしても、起動後にここから確認を再開します。")

    while _has_uncollected_batches():
        try:
            resume_batches(argparse.Namespace(notion=True, no_render=False))
        except (HTTPError, URLError, TimeoutError, OSError, RuntimeError) as error:
            print(f"一時的なエラー: {error}")
            print("次の確認時に再試行します。")

        if not _has_uncollected_batches():
            print("すべてのBatchを回収し、Notionへの反映を完了しました。")
            return
        if time.monotonic() >= deadline:
            raise SystemExit(
                "自動待機がタイムアウトしました。後で `python3 scripts/process_papers.py resume --notion` を実行してください。"
            )
        print(f"次回確認: 約{interval_seconds // 60}分後")
        time.sleep(interval_seconds)

    print("自動待機が必要なBatchはありません。")


def _has_uncollected_batches() -> bool:
    return any(not manifest.get("collected_at") for _, manifest in _batch_manifests())


def show_status() -> None:
    api_key = api_key_from_env()
    if not api_key:
        raise SystemExit("OPENAI_API_KEY is required in .env for status.")
    manifests = _batch_manifests()
    if not manifests:
        print("Batch履歴はありません。")
        return
    for path, manifest in manifests:
        batch = retrieve_batch(manifest["batch_id"], api_key)
        manifest.update({
            "status": batch.get("status"),
            "request_counts": batch.get("request_counts"),
            "output_file_id": batch.get("output_file_id"),
            "error_file_id": batch.get("error_file_id"),
            "checked_at": _now(),
        })
        _write_json(path, manifest)
        counts = batch.get("request_counts") or {}
        print(f"{batch['id']}  {batch.get('status')}  completed={counts.get('completed', 0)} failed={counts.get('failed', 0)}")


def resume_batches(args: argparse.Namespace) -> None:
    api_key = api_key_from_env()
    if not api_key:
        raise SystemExit("OPENAI_API_KEY is required in .env for resume.")
    completed_any = False
    for path, manifest in _batch_manifests():
        if manifest.get("collected_at"):
            continue
        batch = retrieve_batch(manifest["batch_id"], api_key)
        manifest["status"] = batch.get("status")
        if batch.get("status") not in {"completed", "expired", "failed", "cancelled"}:
            print(f"{batch['id']}: {batch.get('status')}（まだ処理中）")
            _write_json(path, manifest)
            continue
        if batch.get("output_file_id"):
            output = download_file(batch["output_file_id"], api_key).decode("utf-8")
            _collect_output(output, render=not args.no_render, notion=args.notion)
            completed_any = True
        if batch.get("error_file_id"):
            error_text = download_file(batch["error_file_id"], api_key).decode("utf-8")
            error_path = BATCHES / f"{batch['id']}_errors.jsonl"
            error_path.write_text(error_text, encoding="utf-8")
            _mark_batch_errors(error_text)
        manifest.update({
            "output_file_id": batch.get("output_file_id"),
            "error_file_id": batch.get("error_file_id"),
            "collected_at": _now(),
        })
        _write_json(path, manifest)

    # A user may first collect/render and add --notion on a later run.  Keep
    # that workflow resumable without redownloading the Batch output.
    if args.notion:
        for job in _load_jobs():
            if job.get("status") not in {"completed", "rendered"}:
                continue
            summary_path = ROOT / str(job.get("summary_path", ""))
            if not summary_path.is_file():
                continue
            image_path = ROOT / str(job["image_path"]) if job.get("image_path") else None
            if not args.no_render and (image_path is None or not image_path.is_file()):
                image_path = _render_summary(summary_path, str(job["pmid"]))
                job["image_path"] = str(image_path.relative_to(ROOT))
                job["status"] = "rendered"
                _write_json(_job_path(str(job["pmid"])), job)
            _update_notion(_read_json(summary_path), image_path, job)
            completed_any = True
    if not completed_any:
        print("新しく回収できる完了結果はありません。")


def _collect_output(text: str, render: bool, notion: bool) -> None:
    for line in text.splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        custom_id = str(record.get("custom_id") or "")
        pmid = _pmid_from_custom_id(custom_id)
        if not pmid:
            print(f"Unknown Batch result: {custom_id}")
            continue
        job = _read_json(_job_path(pmid), default={})
        response = record.get("response") or {}
        if response.get("status_code") != 200:
            job["status"] = "batch_error"
            job["batch_error"] = response
            _write_json(_job_path(pmid), job)
            continue
        body = response.get("body") or {}
        raw_text = extract_response_text(body)
        try:
            result = json.loads(raw_text)
        except json.JSONDecodeError as error:
            job["status"] = "invalid_result"
            job["batch_error"] = f"Invalid JSON: {error}"
            _write_json(_job_path(pmid), job)
            continue
        if str(result.get("pmid")) != str(pmid):
            job["status"] = "invalid_result"
            job["batch_error"] = f"PMID mismatch: {result.get('pmid')}"
            _write_json(_job_path(pmid), job)
            continue

        summary = _canonicalize_summary(result, job)
        summary_path = SUMMARIES / f"PMID_{pmid}.json"
        _write_json(summary_path, summary)
        usage = body.get("usage") or {}
        actual_cost = estimate_batch_cost(int(usage.get("input_tokens", 0)), int(usage.get("output_tokens", 0)))
        job.update({
            "status": "completed",
            "summary_path": str(summary_path.relative_to(ROOT)),
            "usage": usage,
            "actual_batch_cost_usd": round(actual_cost, 6),
            "completed_at": _now(),
        })

        image_path = None
        if render:
            image_path = _render_summary(summary_path, pmid)
            job["image_path"] = str(image_path.relative_to(ROOT))
            job["status"] = "rendered"
        _write_json(_job_path(pmid), job)

        if notion:
            _update_notion(summary, image_path, job)


def _update_notion(summary: dict[str, Any], image_path: Path | None, job: dict[str, Any]) -> None:
    token, database_id = notion_credentials_from_env()
    if not token or not database_id:
        raise SystemExit("NOTION_TOKEN and NOTION_DATABASE_ID are required in .env for --notion.")
    normalized = normalize_chatgpt_summary(summary)
    graphic_url = ""
    if image_path:
        graphic_url = publish_graphic(image_path, str(summary["pmid"]))
        normalized["graphic_url"] = graphic_url
    page = upsert_chatgpt_summary_page(
        normalized,
        database_id,
        token,
        append_children_to_existing=False,
        replace_generated_section=True,
    )
    if image_path:
        attach_local_graphic(page["id"], database_id, token, image_path)
    if graphic_url:
        update_notion_page_cover_and_graphic_url(page["id"], database_id, token, graphic_url)
    remember_notion_page(summary.get("pmid"), page)
    job["status"] = "notion_updated"
    job["notion_page_id"] = page.get("id")
    job["notion_url"] = page.get("url")
    job["graphic_url"] = graphic_url
    job["updated_at"] = _now()
    _write_json(_job_path(str(summary["pmid"])), job)
    print(f"PMID {summary['pmid']}: Notion {page.get('import_action')} {page.get('url')}")


def graphic_public_url(image_path: Path) -> str:
    relative_path = image_path.resolve().relative_to(ROOT.resolve()).as_posix()
    base_url = os.getenv("GITHUB_PAGES_BASE_URL", DEFAULT_GITHUB_PAGES_BASE_URL).rstrip("/")
    return f"{base_url}/{quote(relative_path)}"


def publish_graphic(image_path: Path, pmid: str) -> str:
    relative_path = image_path.resolve().relative_to(ROOT.resolve()).as_posix()
    subprocess.run(["git", "add", "--", relative_path], cwd=ROOT, check=True)
    changed = subprocess.run(
        ["git", "diff", "--cached", "--quiet", "--", relative_path],
        cwd=ROOT,
        check=False,
    ).returncode != 0
    if changed:
        subprocess.run(
            ["git", "commit", "--only", "-m", f"Add grarec image for PMID {pmid}", "--", relative_path],
            cwd=ROOT,
            check=True,
        )
        branch = subprocess.run(
            ["git", "branch", "--show-current"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip() or "main"
        subprocess.run(["git", "push", "origin", branch], cwd=ROOT, check=True)
    url = graphic_public_url(image_path)
    wait_for_public_graphic(url)
    print(f"PMID {pmid}: Graphic URL {url}")
    return url


def wait_for_public_graphic(url: str, attempts: int = 60, delay_seconds: int = 5) -> None:
    last_error = ""
    for _ in range(attempts):
        request = urllib.request.Request(url, method="HEAD", headers={"User-Agent": "pubmed-grarec-notion/1.0"})
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                if response.status == 200:
                    return
        except (HTTPError, URLError, TimeoutError, OSError) as error:
            last_error = str(error)
        time.sleep(delay_seconds)
    raise RuntimeError(f"GitHub Pagesで画像を確認できませんでした: {url} ({last_error})")


def _resolve_source(article: PubMedArticle, pdf_path: Path | None) -> tuple[str, str, str, int]:
    if pdf_path:
        relative = pdf_path.resolve().relative_to(ROOT).as_posix()
        page_count = _pdf_page_count(pdf_path)
        return "user_pdf", relative, _sha256(pdf_path.read_bytes()), max(10000, page_count * 3000)

    full_text = fetch_pmc_full_text(article.pmid)
    if full_text:
        path = SOURCES / f"PMID_{article.pmid}_pmc.txt"
        path.write_text(full_text, encoding="utf-8")
        return "pmc_full_text", path.relative_to(ROOT).as_posix(), _sha256(full_text.encode()), max(1000, len(full_text) // 4)

    abstract = article.abstract.strip()
    if not abstract:
        raise ValueError("PDF、PMC全文、PubMed abstractのいずれも取得できませんでした。")
    path = SOURCES / f"PMID_{article.pmid}_abstract.txt"
    path.write_text(abstract, encoding="utf-8")
    return "pubmed_abstract", path.relative_to(ROOT).as_posix(), _sha256(abstract.encode()), max(500, len(abstract) // 4)


def fetch_pmc_full_text(pmid: str) -> str:
    request = urllib.request.Request(PMC_BIOC_URL.format(pmid=pmid), headers={"User-Agent": "pubmed-grarec-notion/1.0"})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            data = json.loads(response.read().decode("utf-8"))
    except (HTTPError, URLError, json.JSONDecodeError):
        return ""
    passages: list[str] = []
    for document in data if isinstance(data, list) else data.get("documents", []):
        for passage in document.get("passages", []):
            text = str(passage.get("text") or "").strip()
            if text:
                passages.append(text)
    return "\n\n".join(passages)


def _batch_request(job: dict[str, Any], model: str, detail: str, custom_id: str) -> dict[str, Any]:
    article = job["article"]
    prompt = _review_prompt(article, job["source_type"])
    content: list[dict[str, Any]] = [{"type": "input_text", "text": prompt}]
    if job["source_type"] == "user_pdf":
        content.append({"type": "input_file", "file_id": job["openai_file_id"], "detail": detail})
    else:
        source_text = (ROOT / job["source_path"]).read_text(encoding="utf-8")
        content.append({"type": "input_text", "text": f"\n\n論文本文またはAbstract:\n{source_text}"})
    body = {
        "model": model,
        "input": [{"role": "user", "content": content}],
        "reasoning": {"effort": os.getenv("OPENAI_REASONING_EFFORT", "medium")},
        "max_output_tokens": 8000,
        "store": False,
        "text": {"format": {"type": "json_schema", "name": "paper_review", "strict": True, "schema": review_schema()}},
    }
    return {"custom_id": custom_id, "method": "POST", "url": "/v1/responses", "body": body}


def _review_prompt(article: dict[str, Any], source_type: str) -> str:
    return f"""あなたは医学論文を精読する編集者です。以下の論文を、日本の小児科・一般臨床で安全に確認できる形で構造化してください。

絶対条件:
- 与えられた本文またはAbstractだけを根拠にする。
- 数値、N数、効果量、信頼区間、p値を推測しない。
- Abstractのみの場合は限界を明記する。
- 結論を原文より強くしない。
- 日本語で簡潔に書く。
- evidence_notesには、根拠となるページ、節、表、図、またはAbstract内の位置を可能な範囲で記載する。
- PMIDは必ず {article['pmid']} とする。

メタデータ:
PMID: {article['pmid']}
Title: {article['title']}
Journal: {article['journal']}
Year: {article.get('year') or ''}
DOI: {article.get('doi') or ''}
Source level: {source_type}
"""


def review_schema() -> dict[str, Any]:
    properties: dict[str, Any] = {
        "pmid": {"type": "string"},
        "title": {"type": "string"},
        "journal": {"type": "string"},
        "year": {"type": "string"},
        "doi": {"type": "string"},
        "topic": {"type": "array", "items": {"type": "string"}},
        "study_type": {"type": "string"},
        "one_line_summary": {"type": "string"},
        "practice_change": {"type": "string", "enum": ["Yes", "No", "Unclear"]},
        "pico": {"type": "string"},
        "figure_table_summary": {"type": "string"},
        "main_results": {"type": "string"},
        "safety": {"type": "string"},
        "limitations": {"type": "string"},
        "applicability_to_japanese_pediatric_clinic": {"type": "string"},
        "tomorrow_action": {"type": "string"},
        "why_important": {"type": "string"},
        "clinical_impact": {"type": "string"},
        "summary_jp": {"type": "string"},
        "evidence_notes": {"type": "string"},
    }
    return {"type": "object", "properties": properties, "required": list(properties), "additionalProperties": False}


def _canonicalize_summary(result: dict[str, Any], job: dict[str, Any]) -> dict[str, Any]:
    article = job["article"]
    result.update({
        "pmid": str(article["pmid"]),
        "title": article["title"],
        "journal": article["journal"],
        "year": article.get("year") or result.get("year"),
        "doi": article.get("doi") or result.get("doi", ""),
        "pubmed_url": article.get("pubmed_url"),
        "published_date": article.get("published_date"),
        "source_level": job["source_type"],
        "source_hash": job["source_hash"],
        "model": os.getenv("OPENAI_MODEL", "gpt-5.6-terra"),
        "prompt_version": PROMPT_VERSION,
        "human_checked": False,
        "take_home_message": result.get("one_line_summary", ""),
    })
    return result


def estimate_batch_cost(input_tokens: int, output_tokens: int) -> float:
    input_rate = _env_float("OPENAI_BATCH_INPUT_USD_PER_MILLION", 1.0)
    output_rate = _env_float("OPENAI_BATCH_OUTPUT_USD_PER_MILLION", 6.0)
    return input_tokens / 1_000_000 * input_rate + output_tokens / 1_000_000 * output_rate


def _render_summary(summary_path: Path, pmid: str) -> Path:
    now = datetime.now()
    output = ROOT / "images" / f"{now.year:04d}" / f"{now.month:02d}" / f"PMID_{pmid}_grarec.png"
    output.parent.mkdir(parents=True, exist_ok=True)
    build_cache = ROOT / ".build" / "clang-module-cache"
    build_cache.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env["CLANG_MODULE_CACHE_PATH"] = str(build_cache)
    env["SWIFTPM_MODULECACHE_OVERRIDE"] = str(ROOT / ".build" / "swiftpm-module-cache")
    result = subprocess.run(
        ["swift", "scripts/render_grarec.swift", str(summary_path), str(output)],
        cwd=ROOT,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Graphic rendering failed:\n{result.stdout}\n{result.stderr}")
    print(f"PMID {pmid}: rendered {output.relative_to(ROOT)}")
    return output


def _collect_pmids(explicit: list[str] | None, file_path: Path, pdf_dir: Path) -> list[str]:
    values = list(explicit or [])
    if not explicit and file_path.exists():
        for line in file_path.read_text(encoding="utf-8").splitlines():
            value = line.strip()
            if value and not value.startswith("#"):
                values.append(value)
    if not explicit and pdf_dir.exists():
        for path in pdf_dir.glob("*.pdf"):
            match = re.search(r"(?:PMID[_ -]?)?(\d{6,9})", path.stem, re.IGNORECASE)
            if match:
                values.append(match.group(1))
    normalized = []
    for value in values:
        value = str(value).strip()
        if not value.isdigit():
            print(f"Skip invalid PMID: {value}")
            continue
        if value not in normalized:
            normalized.append(value)
    return normalized


def _find_pdf(directory: Path, pmid: str) -> Path | None:
    if not directory.exists():
        return None
    candidates = list(directory.glob(f"PMID_{pmid}*.pdf")) + list(directory.glob(f"{pmid}*.pdf"))
    return sorted(candidates)[0] if candidates else None


def _notion_has_graphic(page: dict[str, Any]) -> bool:
    properties = page.get("properties", {})
    url = properties.get("Graphic URL", {}).get("url")
    files = properties.get("Graphic Image", {}).get("files") or []
    return bool(url or files or page.get("cover"))


def _pdf_page_count(path: Path) -> int:
    data = path.read_bytes()
    count = len(re.findall(rb"/Type\s*/Page(?!s)\b", data))
    return max(1, count)


def _custom_id(job: dict[str, Any]) -> str:
    return f"pmid-{job['pmid']}-summary-v1-{job['source_hash'][:8]}"


def _pmid_from_custom_id(custom_id: str) -> str:
    match = re.match(r"pmid-(\d+)-", custom_id)
    return match.group(1) if match else ""


def _mark_batch_errors(text: str) -> None:
    for line in text.splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        pmid = _pmid_from_custom_id(str(record.get("custom_id") or ""))
        if not pmid:
            continue
        job = _read_json(_job_path(pmid), default={})
        job["status"] = "batch_error"
        job["batch_error"] = record.get("error")
        job["updated_at"] = _now()
        _write_json(_job_path(pmid), job)


def _batch_manifests() -> list[tuple[Path, dict[str, Any]]]:
    if not BATCHES.exists():
        return []
    paths = [path for path in BATCHES.glob("batch_*.json") if not path.name.startswith("batch_input_")]
    paths.extend(path for path in BATCHES.glob("batch-*.json"))
    unique = sorted(set(paths))
    return [(path, _read_json(path, default={})) for path in unique if _read_json(path, default={}).get("batch_id")]


def _load_jobs() -> list[dict[str, Any]]:
    if not JOBS.exists():
        return []
    return [_read_json(path, default={}) for path in sorted(JOBS.glob("PMID_*.json"))]


def _job_path(pmid: str) -> Path:
    return JOBS / f"PMID_{pmid}.json"


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _ensure_dirs() -> None:
    for path in [JOBS, BATCHES, SOURCES, SUMMARIES, DEFAULT_PDFS]:
        path.mkdir(parents=True, exist_ok=True)


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


if __name__ == "__main__":
    main()
