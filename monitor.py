#!/usr/bin/env python3
"""Discover, deduplicate and classify marine CDR literature in Zotero."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parent
CONFIG = ROOT / "config"
ZOTERO_API = "https://api.zotero.org"
OPENALEX_API = "https://api.openalex.org/works"
CROSSREF_API = "https://api.crossref.org/works"
INBOX = "00 — New Publications"
REVIEW_QUEUE = "01 — Review Queue"
BOOKS = "29 — Books and Book Chapters"
TREATIES = "30 — Treaties and International Instruments"
CANADIAN_LEGISLATION = "31 — Canadian Legislation and Regulations"
EXCLUDED = "90 — Excluded or Tangential"

OPENALEX_CURRENT_TYPES = ["article", "report"]
OPENALEX_BOOK_TYPES = ["book", "book-chapter"]
CROSSREF_CURRENT_TYPES = ["journal-article", "report", "posted-content"]
CROSSREF_BOOK_TYPES = [
    "book", "monograph", "edited-book", "reference-book", "book-chapter", "book-section",
    "book-part", "reference-entry",
]


def normalize_text(value: str) -> str:
    value = unicodedata.normalize("NFKD", value or "")
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()


def contains(text: str, phrase: str) -> bool:
    return f" {normalize_text(phrase)} " in f" {text} "


def hits(text: str, phrases: list[str]) -> list[str]:
    return [phrase for phrase in phrases if contains(text, phrase)]


def normalize_doi(value: str | None) -> str:
    if not value:
        return ""
    value = re.sub(r"^https?://(dx\.)?doi\.org/", "", value.strip(), flags=re.I)
    return value.lower().strip()


def chunks(items: list[Any], size: int) -> Iterable[list[Any]]:
    for start in range(0, len(items), size):
        yield items[start : start + size]


def openalex_abstract(index: dict[str, list[int]] | None) -> str:
    if not index:
        return ""
    positioned = [(position, word) for word, positions in index.items() for position in positions]
    return " ".join(word for _, word in sorted(positioned))


class ApiError(RuntimeError):
    pass


class HttpClient:
    def __init__(self, contact_email: str):
        self.user_agent = f"Legal-CDR-Zotero-Monitor/2.0 (mailto:{contact_email})"

    def request(self, url: str, *, method: str = "GET", headers: dict[str, str] | None = None,
                payload: Any | None = None, attempts: int = 4) -> tuple[Any, dict[str, str]]:
        merged = {"Accept": "application/json", "User-Agent": self.user_agent}
        merged.update(headers or {})
        data = None
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            merged["Content-Type"] = "application/json"
        for attempt in range(attempts):
            try:
                req = urllib.request.Request(url, data=data, headers=merged, method=method)
                with urllib.request.urlopen(req, timeout=45) as response:
                    body = response.read()
                    return (json.loads(body) if body else {}), dict(response.headers.items())
            except urllib.error.HTTPError as exc:
                if exc.code in {429, 500, 502, 503, 504} and attempt < attempts - 1:
                    retry = exc.headers.get("Retry-After") or exc.headers.get("Backoff")
                    time.sleep(float(retry) if retry and retry.isdigit() else 2 ** attempt)
                    continue
                detail = exc.read().decode("utf-8", errors="replace")[:1000]
                raise ApiError(f"{method} {url} failed ({exc.code}): {detail}") from exc
            except urllib.error.URLError as exc:
                if attempt < attempts - 1:
                    time.sleep(2 ** attempt)
                    continue
                raise ApiError(f"{method} {url} failed: {exc.reason}") from exc
        raise ApiError(f"{method} {url} failed")


@dataclass
class Candidate:
    title: str
    authors: list[str]
    year: int
    doi: str
    url: str
    abstract: str
    publication: str
    item_type: str
    discovery_topic: str
    source: str
    access_status: str = "unknown"
    book_title: str = ""
    publisher: str = ""
    isbn: str = ""
    pages: str = ""

    @property
    def identity(self) -> str:
        return self.doi or normalize_text(self.title)

    def zotero_item(self, inbox_key: str) -> dict[str, Any]:
        item_type = self.item_type if self.item_type in {"journalArticle", "book", "bookSection", "report"} else "report"
        publisher_url = f"https://doi.org/{self.doi}" if self.doi else self.url
        item: dict[str, Any] = {
            "itemType": item_type,
            "title": self.title,
            "creators": [{"creatorType": "author", "name": name} for name in self.authors],
            "abstractNote": self.abstract[:10000],
            "date": str(self.year) if self.year else "",
            "url": publisher_url,
            "accessDate": date.today().isoformat(),
            "language": "",
            "rights": "",
            "extra": (
                f"Discovered by Legal CDR Monitor\n"
                f"Discovery source: {self.source}\n"
                f"Access status: {self.access_status}\n"
                f"Publisher landing page: {publisher_url}"
            ),
            "tags": [
                {"tag": "workflow:new"},
                {"tag": f"discovery:{self.discovery_topic}"},
                {"tag": f"source:{self.source.lower()}"},
                {"tag": f"access:{self.access_status}"},
                {"tag": "link:publisher"},
            ],
            "collections": [inbox_key],
        }
        if item_type == "journalArticle":
            item.update({"publicationTitle": self.publication, "DOI": self.doi})
        elif item_type == "book":
            item.update({
                "publisher": self.publisher or self.publication,
                "ISBN": self.isbn,
                "DOI": self.doi,
            })
        elif item_type == "bookSection":
            item.update({
                "bookTitle": self.book_title or self.publication,
                "publisher": self.publisher,
                "pages": self.pages,
                "ISBN": self.isbn,
                "DOI": self.doi,
            })
        else:
            item.update({"institution": self.publication, "reportType": "Research output"})
            if self.doi:
                item["extra"] += f"\nDOI: {self.doi}"
        return item


@dataclass
class Classification:
    relevant: bool
    collections: list[str]
    tags: list[str]
    reason: str


class Classifier:
    def __init__(self, config: dict[str, Any]):
        self.config = config

    def classify_text(self, text: str) -> Classification:
        normalized = normalize_text(text)
        relevance = self.config["relevance"]
        excluded_hits = hits(normalized, relevance["exclude_phrases"])
        strong_hits = hits(normalized, relevance["strong_phrases"])
        marine_hits = hits(normalized, relevance["marine_terms"])
        cdr_hits = hits(normalized, relevance["cdr_terms"])
        legal_route = (marine_hits and hits(normalized, relevance["legal_cdr_terms"])
                       and hits(normalized, relevance["climate_carbon_terms"]))
        relevant = not excluded_hits and bool(strong_hits or (marine_hits and cdr_hits) or legal_route)

        if not relevant:
            reason = "explicit exclusion" if excluded_hits else "no marine-CDR relevance threshold"
            return Classification(False, [EXCLUDED], ["workflow:excluded", "relevance:tangential"], reason)

        names = [REVIEW_QUEUE]
        tags_out = ["workflow:review", "relevance:relevant"]
        for rule in self.config["collections"]:
            if hits(normalized, rule["keywords"]):
                names.append(rule["name"])
                tags_out.append(f"topic:{rule['tag']}")
        return Classification(True, list(dict.fromkeys(names)), list(dict.fromkeys(tags_out)), "relevant")

    def classify_item(self, data: dict[str, Any]) -> Classification:
        fields = [
            data.get("title", ""), data.get("abstractNote", ""), data.get("publicationTitle", ""),
            data.get("institution", ""), data.get("reportType", ""), data.get("extra", ""),
            " ".join(tag.get("tag", "") for tag in data.get("tags", [])),
        ]
        return self.classify_text(" ".join(fields))


class ZoteroLibrary:
    def __init__(self, client: HttpClient, group_id: str, api_key: str):
        self.client = client
        self.group_id = group_id
        self.headers = {"Zotero-API-Key": api_key, "Zotero-API-Version": "3"}
        self.base = f"{ZOTERO_API}/groups/{group_id}"

    def verify(self) -> None:
        data, _ = self.client.request(f"{ZOTERO_API}/keys/current", headers=self.headers)
        groups = data.get("access", {}).get("groups", {})
        access = groups.get(self.group_id) or groups.get(str(self.group_id)) or groups.get("all")
        can_write = access.get("write", False) if isinstance(access, dict) else access in {"write", True}
        if not can_write:
            raise ApiError(f"The API key does not have write access to Zotero group {self.group_id}.")

    def _all(self, resource: str) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        start = 0
        while True:
            params = urllib.parse.urlencode({"limit": 100, "start": start, "format": "json"})
            page, headers = self.client.request(f"{self.base}/{resource}?{params}", headers=self.headers)
            results.extend(page)
            total = int(headers.get("Total-Results", len(results)))
            if len(results) >= total or not page:
                return results
            start += len(page)

    def collections(self) -> list[dict[str, Any]]:
        return self._all("collections")

    def items(self) -> list[dict[str, Any]]:
        return self._all("items/top")

    def ensure_collections(self, definitions: list[dict[str, str]]) -> dict[str, str]:
        existing = {row["data"]["name"]: row["key"] for row in self.collections()}
        missing = [{"name": row["name"], "parentCollection": ""} for row in definitions if row["name"] not in existing]
        for batch in chunks(missing, 50):
            response, _ = self.client.request(f"{self.base}/collections", method="POST",
                                              headers=self.headers, payload=batch)
            for result in response.get("successful", {}).values():
                created = result.get("data", result)
                existing[created["name"]] = result.get("key") or created.get("key")
        return existing

    def existing_identities(self, rows: list[dict[str, Any]] | None = None) -> set[str]:
        identities: set[str] = set()
        for row in rows if rows is not None else self.items():
            data = row.get("data", row)
            doi = normalize_doi(data.get("DOI"))
            if not doi:
                match = re.search(r"(?:DOI:\s*|doi\.org/)(10\.\S+)", data.get("extra", ""), flags=re.I)
                doi = normalize_doi(match.group(1)) if match else ""
            title = data.get("title") or data.get("nameOfAct") or ""
            identities.add(doi or normalize_text(title))
        return {identity for identity in identities if identity}

    def add_items(self, items: list[dict[str, Any]]) -> int:
        return self._batch_write(items, "create")

    def update_items(self, items: list[dict[str, Any]]) -> int:
        return self._batch_write(items, "update")

    def _batch_write(self, items: list[dict[str, Any]], operation: str) -> int:
        written = 0
        for batch in chunks(items, 50):
            response, _ = self.client.request(f"{self.base}/items", method="POST",
                                              headers=self.headers, payload=batch)
            written += len(response.get("successful", {}))
            failures = response.get("failed", {})
            if failures:
                print(f"Warning: Zotero {operation} rejected {len(failures)} item(s): {failures}", file=sys.stderr)
        return written


def reclassify_inbox(library: ZoteroLibrary, collection_keys: dict[str, str], classifier: Classifier,
                       dry_run: bool) -> tuple[int, int, Counter[str]]:
    inbox_key = collection_keys[INBOX]
    managed_tag_prefixes = ("workflow:", "relevance:", "topic:")
    updates: list[dict[str, Any]] = []
    assignments: Counter[str] = Counter()
    excluded = 0
    for row in library.items():
        data = dict(row.get("data", row))
        if inbox_key not in data.get("collections", []):
            continue
        result = classifier.classify_item(data)
        target_names = list(result.collections)
        if result.relevant and data.get("itemType") in {"book", "bookSection"}:
            target_names.append(BOOKS)
        target_names = list(dict.fromkeys(target_names))
        target_keys = [collection_keys[name] for name in target_names]
        manual_keys = [key for key in data.get("collections", []) if key != inbox_key]
        data["collections"] = list(dict.fromkeys(manual_keys + target_keys))
        manual_tags = [tag for tag in data.get("tags", [])
                       if not tag.get("tag", "").startswith(managed_tag_prefixes)]
        data["tags"] = manual_tags + [{"tag": tag} for tag in result.tags]
        updates.append(data)
        for name in target_names:
            assignments[name] += 1
        excluded += int(not result.relevant)
    written = 0 if dry_run else library.update_items(updates)
    return len(updates), excluded, assignments


def period_filter(parts: list[str]) -> str | None:
    return ",".join(part for part in parts if part) or None


def openalex_item_type(value: str) -> str | None:
    return {
        "article": "journalArticle",
        "review": "journalArticle",
        "book": "book",
        "book-chapter": "bookSection",
        "reference-entry": "bookSection",
        "report": "report",
    }.get(value)


def crossref_item_type(value: str) -> str | None:
    if value in {"journal-article", "posted-content"}:
        return "journalArticle"
    if value in {"book", "monograph", "edited-book", "reference-book"}:
        return "book"
    if value in {"book-chapter", "book-section", "book-part", "reference-entry"}:
        return "bookSection"
    if value == "report":
        return "report"
    return None


def discover_openalex(client: HttpClient, query: str, topic: str, from_date: str | None,
                      to_date: str | None, email: str, work_types: list[str],
                      page: int = 1) -> list[Candidate]:
    filters = period_filter([
        f"from_publication_date:{from_date}" if from_date else "",
        f"to_publication_date:{to_date}" if to_date else "",
        f"type:{'|'.join(work_types)}" if work_types else "",
    ])
    request_params: dict[str, Any] = {"search": query, "per-page": 100, "page": page,
                                     "mailto": email}
    if from_date or to_date:
        request_params["sort"] = "publication_date:desc"
    if filters:
        request_params["filter"] = filters
    params = urllib.parse.urlencode(request_params)
    data, _ = client.request(f"{OPENALEX_API}?{params}")
    output = []
    for work in data.get("results", []):
        title = work.get("title") or ""
        if not title:
            continue
        item_type = openalex_item_type(work.get("type") or "")
        if not item_type:
            continue
        primary = work.get("primary_location") or {}
        source = primary.get("source") or {}
        authors = [a.get("author", {}).get("display_name", "") for a in work.get("authorships", [])[:20]]
        open_access = work.get("open_access") or {}
        access_status = "open" if open_access.get("is_oa") is True else "closed" if open_access.get("is_oa") is False else "unknown"
        biblio = work.get("biblio") or {}
        first_page, last_page = biblio.get("first_page") or "", biblio.get("last_page") or ""
        pages = f"{first_page}-{last_page}" if first_page and last_page and first_page != last_page else first_page
        container = source.get("display_name") or ""
        publisher = source.get("host_organization_name") or ""
        output.append(Candidate(
            title=title,
            authors=[a for a in authors if a],
            year=work.get("publication_year") or 0,
            doi=normalize_doi(work.get("doi")),
            url=work.get("doi") or primary.get("landing_page_url") or work.get("id", ""),
            abstract=openalex_abstract(work.get("abstract_inverted_index")),
            publication=container or publisher or "OpenAlex",
            item_type=item_type,
            discovery_topic=topic,
            source="OpenAlex",
            access_status=access_status,
            book_title=container if item_type == "bookSection" else "",
            publisher=publisher,
            pages=pages,
        ))
    return output


def discover_crossref(client: HttpClient, query: str, topic: str, from_date: str | None,
                      to_date: str | None, email: str, work_types: list[str],
                      page: int = 1) -> list[Candidate]:
    filters = period_filter([
        f"from-pub-date:{from_date}" if from_date else "",
        f"until-pub-date:{to_date}" if to_date else "",
        *[f"type:{value}" for value in work_types],
    ])
    request_params: dict[str, Any] = {"query.bibliographic": query, "rows": 100,
                                     "offset": (page - 1) * 100, "mailto": email}
    if from_date or to_date:
        request_params.update({"sort": "published", "order": "desc"})
    if filters:
        request_params["filter"] = filters
    params = urllib.parse.urlencode(request_params)
    data, _ = client.request(f"{CROSSREF_API}?{params}")
    output = []
    for work in data.get("message", {}).get("items", []):
        title = (work.get("title") or [""])[0]
        if not title:
            continue
        item_type = crossref_item_type(work.get("type") or "")
        if not item_type:
            continue
        authors = [" ".join(filter(None, [a.get("given"), a.get("family")])) for a in work.get("author", [])[:20]]
        parts = (work.get("published") or {}).get("date-parts") or [[0]]
        container = (work.get("container-title") or [""])[0]
        publisher = work.get("publisher") or ""
        isbn_values = work.get("ISBN") or []
        isbn = isbn_values[0] if isbn_values and isinstance(isbn_values[0], str) else ""
        output.append(Candidate(
            title=title,
            authors=[a for a in authors if a],
            year=parts[0][0] if parts and parts[0] else 0,
            doi=normalize_doi(work.get("DOI")),
            url=work.get("URL") or "",
            abstract=re.sub(r"<[^>]+>", "", work.get("abstract", "")),
            publication=container or publisher or "Crossref",
            item_type=item_type,
            discovery_topic=topic,
            source="Crossref",
            book_title=container if item_type == "bookSection" else "",
            publisher=publisher,
            isbn=isbn,
            pages=work.get("page") or "",
        ))
    return output


def merge_candidate(current: Candidate, incoming: Candidate) -> Candidate:
    """Merge duplicate metadata without losing OpenAlex access information."""
    if len(incoming.abstract) > len(current.abstract):
        current.abstract = incoming.abstract
    if current.access_status == "unknown" and incoming.access_status != "unknown":
        current.access_status = incoming.access_status
    if current.publication in {"OpenAlex", "Crossref", ""} and incoming.publication:
        current.publication = incoming.publication
    if not current.doi and incoming.doi:
        current.doi = incoming.doi
    if not current.url and incoming.url:
        current.url = incoming.url
    if not current.book_title and incoming.book_title:
        current.book_title = incoming.book_title
    if not current.publisher and incoming.publisher:
        current.publisher = incoming.publisher
    if not current.isbn and incoming.isbn:
        current.isbn = incoming.isbn
    if not current.pages and incoming.pages:
        current.pages = incoming.pages
    if current.item_type == "report" and incoming.item_type in {"journalArticle", "book", "bookSection"}:
        current.item_type = incoming.item_type
    return current


def discover_period(client: HttpClient, queries: list[dict[str, str]], from_date: str | None,
                    to_date: str | None, email: str, *, current_pages: int = 1,
                    book_pages: int = 1, include_current: bool = True,
                    include_books: bool = True) -> list[Candidate]:
    discovered: list[Candidate] = []
    for rule in queries:
        if include_current:
            for page in range(1, current_pages + 1):
                discovered.extend(discover_openalex(
                    client, rule["query"], rule["topic"], from_date, to_date, email,
                    OPENALEX_CURRENT_TYPES, page,
                ))
                discovered.extend(discover_crossref(
                    client, rule["query"], rule["topic"], from_date, to_date, email,
                    CROSSREF_CURRENT_TYPES, page,
                ))
        if include_books:
            for page in range(1, book_pages + 1):
                discovered.extend(discover_openalex(
                    client, rule["query"], rule["topic"], from_date, to_date, email,
                    OPENALEX_BOOK_TYPES, page,
                ))
                discovered.extend(discover_crossref(
                    client, rule["query"], rule["topic"], from_date, to_date, email,
                    CROSSREF_BOOK_TYPES, page,
                ))
    return discovered


def load_json(name: str) -> Any:
    return json.loads((CONFIG / name).read_text(encoding="utf-8"))


def curated_identity(data: dict[str, Any]) -> str:
    return normalize_doi(data.get("DOI")) or normalize_text(data.get("title") or data.get("nameOfAct") or "")


def curated_zotero_item(definition: dict[str, Any], collection_keys: dict[str, str]) -> dict[str, Any]:
    item_type = definition["itemType"]
    if item_type == "document":
        allowed = {
            "itemType", "title", "abstractNote", "type", "date", "publisher", "place", "DOI",
            "url", "shortTitle", "language", "rights", "extra",
        }
    elif item_type == "statute":
        allowed = {
            "itemType", "nameOfAct", "abstractNote", "code", "codeNumber", "publicLawNumber",
            "dateEnacted", "pages", "section", "session", "history", "DOI", "url", "shortTitle",
            "language", "rights", "extra",
        }
    else:
        raise ValueError(f"Unsupported curated Zotero item type: {item_type}")

    item = {key: value for key, value in definition.items() if key in allowed}
    issuer = definition.get("issuer", "")
    item["creators"] = [{"creatorType": "author", "name": issuer}] if issuer and item_type == "document" else []
    item["accessDate"] = date.today().isoformat() if item.get("url") else ""
    item["tags"] = [{"tag": tag} for tag in definition.get("tags", [])]
    item["collections"] = [collection_keys[name] for name in definition.get("collections", [])]
    item["relations"] = {}
    return item


def seed_curated_sources(library: ZoteroLibrary, collection_keys: dict[str, str],
                         definitions: list[dict[str, Any]], existing_rows: list[dict[str, Any]],
                         dry_run: bool) -> tuple[int, int, int, int]:
    existing = library.existing_identities(existing_rows)
    missing = []
    known = 0
    for definition in definitions:
        identity = curated_identity(definition)
        if not identity or identity in existing:
            known += 1
            continue
        missing.append(curated_zotero_item(definition, collection_keys))
        existing.add(identity)
    created = 0 if dry_run else library.add_items(missing)
    return len(definitions), created, known, len(missing) - created


def write_summary(found: int, created: int, skipped: int, classified: int, excluded: int,
                  assignments: Counter[str], item_types: Counter[str], dry_run: bool,
                  book_backfill: bool, curated_total: int, curated_created: int,
                  curated_known: int, curated_pending: int) -> None:
    lines = ["## Legal CDR Zotero Monitor", "", f"- Candidates retrieved: **{found}**",
             f"- New Zotero records: **{created}**", f"- Duplicates or previously known: **{skipped}**",
             f"- Inbox records classified: **{classified}**", f"- Sent to Excluded or Tangential: **{excluded}**"]
    lines.extend([
        "- Historical article backfill: **disabled**",
        f"- One-time books and chapters backfill: **{'enabled' if book_backfill else 'not requested'}**",
        f"- Curated treaty and legislation records: **{curated_total}**",
        f"- New curated legal records: **{curated_created}**",
        f"- Curated legal records already known: **{curated_known}**",
        f"- Curated legal records pending or previewed: **{curated_pending}**",
    ])
    if dry_run:
        lines.append("- Mode: **dry run — no Zotero records changed**")
    if item_types:
        lines.extend(["", "### Retrieved bibliographic types", ""])
        lines.extend(f"- {name}: **{count}**" for name, count in sorted(item_types.items()))
    if assignments:
        lines.extend(["", "### Collection assignments", ""])
        lines.extend(f"- {name}: **{count}**" for name, count in sorted(assignments.items()))
    summary = "\n".join(lines)
    print(summary)
    if path := os.getenv("GITHUB_STEP_SUMMARY"):
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(summary + "\n")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--setup", action="store_true", help="Create the collection structure and exit")
    parser.add_argument("--dry-run", action="store_true", help="Simulate discovery and classification without writing")
    parser.add_argument(
        "--backfill-books", action="store_true",
        help="Run a one-time books and book chapters search without a publication-date cutoff",
    )
    args = parser.parse_args()
    group_id = os.getenv("ZOTERO_GROUP_ID", "6614701")
    api_key = os.getenv("ZOTERO_API_KEY", "")
    email = os.getenv("CONTACT_EMAIL", "gustavo@example.org")
    lookback_days = int(os.getenv("LOOKBACK_DAYS", "120"))
    book_backfill_pages = int(os.getenv("BOOK_BACKFILL_PAGES", "1"))
    if not api_key:
        print("ZOTERO_API_KEY is required. Store it as a protected secret.", file=sys.stderr)
        return 2

    client = HttpClient(email)
    library = ZoteroLibrary(client, group_id, api_key)
    library.verify()
    collection_keys = library.ensure_collections(load_json("collections.json"))
    if args.setup:
        print(f"Collection structure ready: {len(collection_keys)} collections available.")
        return 0

    classifier = Classifier(load_json("classification.json"))
    queries = load_json("queries.json")
    existing_rows = library.items()
    curated_definitions = load_json("legal_sources.json")
    curated_total, curated_created, curated_known, curated_pending = seed_curated_sources(
        library, collection_keys, curated_definitions, existing_rows, args.dry_run,
    )
    today = date.today()
    recent_from = (today - timedelta(days=lookback_days)).isoformat()
    if args.backfill_books:
        discovered = discover_period(
            client, queries, None, None, email, current_pages=0,
            book_pages=book_backfill_pages, include_current=False, include_books=True,
        )
    else:
        discovered = discover_period(client, queries, recent_from, None, email)
    unique: dict[str, Candidate] = {}
    for candidate in discovered:
        if not candidate.identity:
            continue
        if candidate.identity in unique:
            unique[candidate.identity] = merge_candidate(unique[candidate.identity], candidate)
        else:
            unique[candidate.identity] = candidate
    existing = library.existing_identities(existing_rows)
    existing.update(curated_identity(definition) for definition in curated_definitions)
    new_candidates = [candidate for key, candidate in unique.items() if key not in existing]
    payloads = [candidate.zotero_item(collection_keys[INBOX]) for candidate in new_candidates]
    created = 0 if args.dry_run else library.add_items(payloads)

    classified, excluded, assignments = reclassify_inbox(library, collection_keys, classifier, args.dry_run)
    if args.dry_run:
        for candidate in new_candidates:
            result = classifier.classify_text(" ".join([candidate.title, candidate.abstract, candidate.publication]))
            classified += 1
            excluded += int(not result.relevant)
            assignments.update(result.collections)
            if result.relevant and candidate.item_type in {"book", "bookSection"}:
                assignments.update([BOOKS])
    item_types = Counter(candidate.item_type for candidate in unique.values())
    write_summary(len(unique), created, len(unique) - len(new_candidates), classified, excluded,
                  assignments, item_types, args.dry_run, args.backfill_books, curated_total,
                  curated_created, curated_known, curated_pending)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
