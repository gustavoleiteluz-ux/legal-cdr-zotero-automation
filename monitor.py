#!/usr/bin/env python3
"""Legal CDR literature monitor for a private Zotero group library."""

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
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parent
CONFIG = ROOT / "config"
ZOTERO_API = "https://api.zotero.org"
OPENALEX_API = "https://api.openalex.org/works"
CROSSREF_API = "https://api.crossref.org/works"


def normalize_text(value: str) -> str:
    value = unicodedata.normalize("NFKD", value or "")
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()


def normalize_doi(value: str | None) -> str:
    if not value:
        return ""
    value = re.sub(r"^https?://(dx\.)?doi\.org/", "", value.strip(), flags=re.I)
    return value.lower().strip()


def chunks(items: list[Any], size: int) -> Iterable[list[Any]]:
    for start in range(0, len(items), size):
        yield items[start : start + size]


class ApiError(RuntimeError):
    pass


class HttpClient:
    def __init__(self, contact_email: str):
        self.user_agent = f"Legal-CDR-Zotero-Monitor/1.0 (mailto:{contact_email})"

    def request(
        self,
        url: str,
        *,
        method: str = "GET",
        headers: dict[str, str] | None = None,
        payload: Any | None = None,
        attempts: int = 4,
    ) -> tuple[Any, dict[str, str]]:
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
                    result = json.loads(body) if body else {}
                    return result, dict(response.headers.items())
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
    topic: str
    source: str

    @property
    def identity(self) -> str:
        return self.doi or normalize_text(self.title)

    def zotero_item(self, collection_key: str) -> dict[str, Any]:
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
                {"tag": f"topic:{self.topic}"},
                {"tag": f"source:{self.source.lower()}"},
            ],
            "collections": [collection_key],
        }
        if item_type == "journalArticle":
            item.update({"publicationTitle": self.publication, "DOI": self.doi})
        else:
            item.update({"institution": self.publication, "reportType": "Research output"})
            if self.doi:
                item["extra"] += f"\nDOI: {self.doi}"
        return item


class ZoteroLibrary:
    def __init__(self, client: HttpClient, group_id: str, api_key: str):
        self.client = client
        self.group_id = group_id
        self.headers = {"Zotero-API-Key": api_key, "Zotero-API-Version": "3"}
        self.base = f"{ZOTERO_API}/groups/{group_id}"

    def verify(self) -> None:
        data, _ = self.client.request(f"{ZOTERO_API}/keys/current", headers=self.headers)
        access = data.get("access", {})
        groups = access.get("groups", {})
        group_access = groups.get(self.group_id) or groups.get(str(self.group_id)) or groups.get("all")
        can_write = group_access.get("write", False) if isinstance(group_access, dict) else group_access in {"write", True}
        if not can_write:
            raise ApiError(f"The API key does not have access to Zotero group {self.group_id}.")

    def collections(self) -> list[dict[str, Any]]:
        return self._all("collections")

    def items(self) -> list[dict[str, Any]]:
        return self._all("items/top")

    def _all(self, resource: str) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        start = 0
        while True:
            params = urllib.parse.urlencode({"limit": 100, "start": start, "format": "json"})
            page, headers = self.client.request(f"{self.base}/{resource}?{params}", headers=self.headers)
            results.extend(page)
            total = int(headers.get("Total-Results", len(results)))
            if len(results) >= total or not page:
                break
            start += len(page)
        return results

    def ensure_collections(self, definitions: list[dict[str, str]]) -> dict[str, str]:
        existing = {row["data"]["name"]: row["key"] for row in self.collections()}
        missing = [{"name": row["name"], "parentCollection": ""} for row in definitions if row["name"] not in existing]
        for batch in chunks(missing, 50):
            response, _ = self.client.request(
                f"{self.base}/collections",
                method="POST",
                headers=self.headers,
                payload=batch,
            )
            for index, result in response.get("successful", {}).items():
                created = result.get("data", result)
                existing[created["name"]] = result.get("key") or created.get("key")
        return existing

    def existing_identities(self) -> set[str]:
        identities: set[str] = set()
        for row in self.items():
            data = row.get("data", row)
            doi = normalize_doi(data.get("DOI"))
            extra = data.get("extra", "")
            if not doi:
                match = re.search(r"(?:DOI:\s*|doi\.org/)(10\.\S+)", extra, flags=re.I)
                doi = normalize_doi(match.group(1)) if match else ""
            identities.add(doi or normalize_text(data.get("title", "")))
        return {identity for identity in identities if identity}

    def add_items(self, items: list[dict[str, Any]]) -> int:
        created = 0
        for batch in chunks(items, 50):
            response, _ = self.client.request(
                f"{self.base}/items",
                method="POST",
                headers=self.headers,
                payload=batch,
            )
            created += len(response.get("successful", {}))
            failures = response.get("failed", {})
            if failures:
                print(f"Warning: Zotero rejected {len(failures)} item(s): {failures}", file=sys.stderr)
        return created


def topic_for(title: str, rules: list[dict[str, Any]]) -> str:
    normalized = normalize_text(title)
    for rule in rules:
        if any(normalize_text(keyword) in normalized for keyword in rule["keywords"]):
            return rule["topic"]
    return "cross-cutting"


def discover_openalex(client: HttpClient, query: str, topic: str, from_date: str, email: str) -> list[Candidate]:
    params = urllib.parse.urlencode(
        {
            "search": query,
            "filter": f"from_publication_date:{from_date}",
            "sort": "publication_date:desc",
            "per-page": 50,
            "mailto": email,
        }
    )
    data, _ = client.request(f"{OPENALEX_API}?{params}")
    candidates = []
    for work in data.get("results", []):
        title = work.get("title") or ""
        if not title:
            continue
        authors = [a.get("author", {}).get("display_name", "") for a in work.get("authorships", [])[:20]]
        primary = work.get("primary_location") or {}
        source = primary.get("source") or {}
        candidates.append(
            Candidate(
                title=title,
                authors=[a for a in authors if a],
                year=work.get("publication_year") or 0,
                doi=normalize_doi(work.get("doi")),
                url=work.get("doi") or primary.get("landing_page_url") or work.get("id", ""),
                abstract="",
                publication=source.get("display_name") or "OpenAlex",
                item_type="journalArticle" if work.get("type") == "article" else "report",
                topic=topic,
                source="OpenAlex",
            )
        )
    return candidates


def discover_crossref(client: HttpClient, query: str, topic: str, from_date: str, email: str) -> list[Candidate]:
    params = urllib.parse.urlencode(
        {
            "query.bibliographic": query,
            "filter": f"from-pub-date:{from_date}",
            "sort": "published",
            "order": "desc",
            "rows": 50,
            "mailto": email,
        }
    )
    data, _ = client.request(f"{CROSSREF_API}?{params}")
    candidates = []
    for work in data.get("message", {}).get("items", []):
        title = (work.get("title") or [""])[0]
        if not title:
            continue
        authors = [" ".join(filter(None, [a.get("given"), a.get("family")])) for a in work.get("author", [])[:20]]
        parts = (work.get("published") or {}).get("date-parts") or [[0]]
        candidates.append(
            Candidate(
                title=title,
                authors=[a for a in authors if a],
                year=parts[0][0] if parts and parts[0] else 0,
                doi=normalize_doi(work.get("DOI")),
                url=work.get("URL") or (f"https://doi.org/{work['DOI']}" if work.get("DOI") else ""),
                abstract=re.sub(r"<[^>]+>", "", work.get("abstract", "")),
                publication=(work.get("container-title") or ["Crossref"])[0],
                item_type="journalArticle" if work.get("type") == "journal-article" else "report",
                topic=topic,
                source="Crossref",
            )
        )
    return candidates


def load_json(name: str) -> Any:
    return json.loads((CONFIG / name).read_text(encoding="utf-8"))


def write_summary(found: int, created: int, skipped: int) -> None:
    lines = [
        "## Legal CDR Zotero Monitor",
        "",
        f"- Candidates retrieved: **{found}**",
        f"- New Zotero records: **{created}**",
        f"- Duplicates or previously known: **{skipped}**",
    ]
    summary = "\n".join(lines)
    print(summary)
    if path := os.getenv("GITHUB_STEP_SUMMARY"):
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(summary + "\n")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--setup", action="store_true", help="Create the collection structure and exit")
    parser.add_argument("--dry-run", action="store_true", help="Discover and deduplicate without writing")
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
    collections = library.ensure_collections(load_json("collections.json"))
    if args.setup:
        print(f"Collection structure ready: {len(collections)} collections available.")
        return 0

    queries = load_json("queries.json")
    from_date = (date.today() - timedelta(days=lookback_days)).isoformat()
    discovered: list[Candidate] = []
    for rule in queries:
        discovered.extend(discover_openalex(client, rule["query"], rule["topic"], from_date, email))
        discovered.extend(discover_crossref(client, rule["query"], rule["topic"], from_date, email))

    unique: dict[str, Candidate] = {}
    for candidate in discovered:
        if candidate.identity:
            unique[candidate.identity] = candidate
    existing = library.existing_identities()
    new_candidates = [item for key, item in unique.items() if key not in existing]
    queue_key = collections["00 — New Publications"]
    payloads = [candidate.zotero_item(queue_key) for candidate in new_candidates]
    created = 0 if args.dry_run else library.add_items(payloads)
    write_summary(len(unique), created, len(unique) - len(new_candidates))
    if args.dry_run:
        print(f"Dry run: {len(new_candidates)} candidate(s) would be written.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
