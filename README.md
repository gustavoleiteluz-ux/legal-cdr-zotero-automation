# Legal CDR Zotero Automation

Daily automation for Zotero Group Library **6614701**. The monitor searches OpenAlex and Crossref, deduplicates results, uses `00 — New Publications` as a temporary inbox, and then classifies each inbox record into every applicable thematic collection.

The search is not limited to open-access literature. Both open and closed-access records are retained. When a DOI is available, the Zotero URL points to the DOI resolver and therefore to the official journal landing page; the monitor never attempts to bypass a paywall or download a restricted PDF. Records are tagged `access:open`, `access:closed`, or `access:unknown`.

Each scheduled run has two discovery tracks: a rolling search covering the most recent 120 days and a rotating historical search. The historical component is limited to seven five-year windows: `2021–2025`, `2016–2020`, `2011–2015`, `2006–2010`, `2001–2005`, `1996–2000`, and `1991–1995`. One window is searched per day, so the complete historical cycle takes seven daily runs and then repeats. Recent literature continues to be checked every day.

Records that do not meet the marine-CDR relevance threshold are removed from the inbox and placed in `90 — Excluded or Tangential`. Relevant records are also placed in `01 — Review Queue`. `12 — Reviewed and Approved` remains exclusively under human editorial control.

## Classification safeguards

- Classification uses title, abstract, publication or institution, extra metadata, and tags.
- A record may be assigned to multiple thematic collections.
- Discovery-query tags do not by themselves establish relevance.
- Existing manual collection assignments and non-automated tags are preserved.
- Medical, indoor-air, terrestrial, and other common false positives are explicitly excluded.
- OpenAlex abstracts are reconstructed from their inverted indexes to improve accuracy.
- OpenAlex and Crossref return up to 100 recent results for each configured query; no open-access filter is applied.
- Historical backfilling runs automatically alongside the daily current-literature search and uses Zotero deduplication to avoid re-adding known records.

The editable taxonomy is in `config/classification.json`; source searches are in `config/queries.json`; collection names are in `config/collections.json`.

## Security

Never commit a Zotero API key. The workflow reads it only from the protected GitHub Actions secret `ZOTERO_API_KEY`. The key should have read/write access only to group `6614701`.

## Running

The scheduled workflow runs daily at 10:17 UTC. To preview a cycle without modifying Zotero, use **Run workflow** with `dry_run` selected. Use `setup_only` to create any missing collections.

## Local validation

```bash
python -m unittest discover -s tests -v
python -m json.tool config/collections.json >/dev/null
python -m json.tool config/queries.json >/dev/null
python -m json.tool config/classification.json >/dev/null
```
