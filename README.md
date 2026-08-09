# Legal CDR Zotero Automation

Daily automation for Zotero Group Library **6614701**. The monitor searches OpenAlex and Crossref for journal articles, reports, books and book chapters; deduplicates results; uses `00 — New Publications` as a temporary inbox; and then classifies each inbox record into every applicable thematic collection.

The search is not limited to open-access literature. Both open and closed-access records are retained. When a DOI is available, the Zotero URL points to the DOI resolver and therefore to the official journal landing page; the monitor never attempts to bypass a paywall or download a restricted PDF. Records are tagged `access:open`, `access:closed`, or `access:unknown`.

Each scheduled run searches the most recent 120 days. The former seven-window historical article backfill is disabled because its two-week validation cycle was completed without identifying additional records. Recent journal articles, reports, books and chapters continue to be checked every day.

Books and chapters are stored using Zotero's native `book` and `bookSection` item types and relevant records are also assigned to `29 — Books and Book Chapters`. A manual `backfill_books` workflow option performs a one-time relevance-ranked book/chapter search without a publication-date cutoff. It is not part of the daily schedule and should not remain selected after the one-time run.

Treaties, other core international instruments and Canadian legislation are drawn from the curated official-source list in `config/legal_sources.json`. They are written directly to `30 — Treaties and International Instruments` or `31 — Canadian Legislation and Regulations`, as applicable, and may also be assigned to relevant thematic collections. These legal records use official UN, IMO, treaty-secretariat, OSPAR, UNECE and Justice Laws links. Inclusion identifies plausible project relevance; it does not assert that every instrument applies to every marine CDR pathway or project.

Records that do not meet the marine-CDR relevance threshold are removed from the inbox and placed in `90 — Excluded or Tangential`. Relevant records are also placed in `01 — Review Queue`. `12 — Reviewed and Approved` remains exclusively under human editorial control.

## Classification safeguards

- Classification uses title, abstract, publication or institution, extra metadata, and tags.
- A record may be assigned to multiple thematic collections.
- Discovery-query tags do not by themselves establish relevance.
- Existing manual collection assignments and non-automated tags are preserved.
- Medical, indoor-air, terrestrial, and other common false positives are explicitly excluded.
- OpenAlex abstracts are reconstructed from their inverted indexes to improve accuracy.
- OpenAlex and Crossref return up to 100 recent results for each configured query and bibliographic stream; no open-access filter is applied.
- The historical article backfill is disabled. A book/chapter backfill without a date cutoff is available only through the manual workflow option.
- Curated legal sources are deduplicated by DOI or normalized title before insertion.

The editable taxonomy is in `config/classification.json`; source searches are in `config/queries.json`; collection names are in `config/collections.json`; and curated treaties and Canadian legislation are in `config/legal_sources.json`.

## Security

Never commit a Zotero API key. The workflow reads it only from the protected GitHub Actions secret `ZOTERO_API_KEY`. The key should have read/write access only to group `6614701`.

## Running

The scheduled workflow runs daily at 10:17 UTC. To preview a cycle without modifying Zotero, use **Run workflow** with `dry_run` selected. Use `setup_only` to create any missing collections.

For the one-time historical discovery of books and chapters:

1. Run with `dry_run` and `backfill_books` selected.
2. Review the candidate, exclusion and item-type counts.
3. Run again with only `backfill_books` selected.
4. Leave `backfill_books` cleared thereafter; the daily schedule never selects it automatically.

## Local validation

```bash
python -m unittest discover -s tests -v
python -m json.tool config/collections.json >/dev/null
python -m json.tool config/queries.json >/dev/null
python -m json.tool config/classification.json >/dev/null
python -m json.tool config/legal_sources.json >/dev/null
```
