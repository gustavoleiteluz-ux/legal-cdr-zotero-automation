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
EXCLUDED = "90 — Excluded or Tangential"


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

    @property
    def identity(self) -> str:
        return self.doi or normalize_text(self.title)

    def zotero_item(self, inbox_key: str) -> dict[str, Any]:
        item_type = "journalArticle" if self.item_type == "journalArticle" else "report"
        item: dict[str, Any] = {
            "itemType": item_type,
            "title": self.title,
            "creators": [{"creatorType": "author", "name": name} for name in self.authors],
            "abstractNote": self.abstract[:10000],
            "date": str(self.year) if self.year else "",
            "url": self.url,
            "accessDate": date.today().isoformat(),
            "language": "",
            "rights": "",
            "extra": f"Discovered by Legal CDR Monitor\nDiscovery source: {self.source}",
            "tags": [
                {"tag": "workflow:new"},
                {"tag": f"discovery:{self.discovery_topic}"},
                {"tag": f"source:{self.source.lower()}"},
            ],
            "collections": [inbox_key],
        }
        if item_type == "journalArticle":
            item.update({"publicationTitle": self.publication, "DOI": self.doi})
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
            identities.add(doi or normalize_text(data.get("title", "")))
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
        target_keys = [collection_keys[name] for name in result.collections]
        manual_keys = [key for key in data.get("collections", []) if key != inbox_key]
        data["collections"] = list(dict.fromkeys(manual_keys + target_keys))
        manual_tags = [tag for tag in data.get("tags", [])
                       if not tag.get("tag", "").startswith(managed_tag_prefixes)]
        data["tags"] = manual_tags + [{"tag": tag} for tag in result.tags]
        updates.append(data)
        for name in result.collections:
            assignments[name] += 1
        excluded += int(not result.relevant)
    written = 0 if dry_run else library.update_items(updates)
    return len(updates), excluded, assignments


def discover_openalex(client: HttpClient, query: str, topic: str, from_date: str, email: str) -> list[Candidate]:
    params = urllib.parse.urlencode({"search": query, "filter": f"from_publication_date:{from_date}",
                                     "sort": "publication_date:desc", "per-page": 50, "mailto": email})
    data, _ = client.request(f"{OPENALEX_API}?{params}")
    output = []
    for work in data.get("results", []):
        title = work.get("title") or ""
        if not title:
            continue
        primary = work.get("primary_location") or {}
        source = primary.get("source") or {}
        authors = [a.get("author", {}).get("display_name", "") for a in work.get("authorships", [])[:20]]
        output.append(Candidate(title, [a for a in authors if a], work.get("publication_year") or 0,
                                normalize_doi(work.get("doi")), work.get("doi") or primary.get("landing_page_url") or work.get("id", ""),
                                openalex_abstract(work.get("abstract_inverted_index")), source.get("display_name") or "OpenAlex",
                                "journalArticle" if work.get("type") == "article" else "report", topic, "OpenAlex"))
    return output


def discover_crossref(client: HttpClient, query: str, topic: str, from_date: str, email: str) -> list[Candidate]:
    params = urllib.parse.urlencode({"query.bibliographic": query, "filter": f"from-pub-date:{from_date}",
                                     "sort": "published", "order": "desc", "rows": 50, "mailto": email})
    data, _ = client.request(f"{CROSSREF_API}?{params}")
    output = []
    for work in data.get("message", {}).get("items", []):
        title = (work.get("title") or [""])[0]
        if not title:
            continue
        authors = [" ".join(filter(None, [a.get("given"), a.get("family")])) for a in work.get("author", [])[:20]]
        parts = (work.get("published") or {}).get("date-parts") or [[0]]
        output.append(Candidate(title, [a for a in authors if a], parts[0][0] if parts and parts[0] else 0,
                                normalize_doi(work.get("DOI")), work.get("URL") or "",
                                re.sub(r"<[^>]+>", "", work.get("abstract", "")),
                                (work.get("container-title") or ["Crossref"])[0],
                                "journalArticle" if work.get("type") == "journal-article" else "report", topic, "Crossref"))
    return output


def load_json(name: str) -> Any:
    return json.loads((CONFIG / name).read_text(encoding="utf-8"))


def write_summary(found: int, created: int, skipped: int, classified: int, excluded: int,
                  assignments: Counter[str], dry_run: bool) -> None:
    lines = ["## Legal CDR Zotero Monitor", "", f"- Candidates retrieved: **{found}**",
             f"- New Zotero records: **{created}**", f"- Duplicates or previously known: **{skipped}**",
             f"- Inbox records classified: **{classified}**", f"- Sent to Excluded or Tangential: **{excluded}**"]
    if dry_run:
        lines.append("- Mode: **dry run — no Zotero records changed**")
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
    args = parser.parse_args()
    group_id = os.getenv("ZOTERO_GROUP_ID", "6614701")
    api_key = os.getenv("ZOTERO_API_KEY", "")
    email = os.getenv("CONTACT_EMAIL", "gustavo@example.org")
    lookback_days = int(os.getenv("LOOKBACK_DAYS", "120"))
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
    from_date = (date.today() - timedelta(days=lookback_days)).isoformat()
    discovered: list[Candidate] = []
    for rule in queries:
        discovered.extend(discover_openalex(client, rule["query"], rule["topic"], from_date, email))
        discovered.extend(discover_crossref(client, rule["query"], rule["topic"], from_date, email))
    unique = {candidate.identity: candidate for candidate in discovered if candidate.identity}
    existing = library.existing_identities()
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
    write_summary(len(unique), created, len(unique) - len(new_candidates), classified, excluded, assignments, args.dry_run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
