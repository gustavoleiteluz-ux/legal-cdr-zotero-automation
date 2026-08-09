# Applying the books and legal-sources update

1. Upload the contents of this package to the repository root, preserving the `config/` and `tests/` folders.
2. Confirm that `config/legal_sources.json` exists.
3. In GitHub Actions, run `Legal CDR Zotero Monitor` with `setup_only` selected. This creates only the three new collections and preserves all existing names.
4. Run it with both `dry_run` and `backfill_books` selected. Review the candidate, exclusion and bibliographic-type counts; no Zotero item will change.
5. If the preview is satisfactory, run once with only `backfill_books` selected. This performs the one-time all-date book/chapter search and adds the curated legal records.
6. Leave `backfill_books` cleared after that execution.

The normal daily schedule then imports recent articles, reports, books and chapters; deduplicates them; classifies them into all applicable collections; and sends unmatched bibliographic records to `90 — Excluded or Tangential`.

This version includes both open and closed-access publications. DOI links lead to the official journal page; restricted PDFs are not downloaded.

The seven-window historical article backfill is disabled. Curated treaties and legislation are checked on each run but are added only when absent. Do not rename any automated collection without updating `config/collections.json`, `config/classification.json` and `config/legal_sources.json` together.
