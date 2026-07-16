# Applying the v2 classification update

1. Upload the contents of this package to the repository root, preserving the `config/` and `tests/` folders.
2. Replace `monitor.py`, `README.md`, `config/collections.json`, and `config/queries.json` when prompted.
3. Confirm that the new file `config/classification.json` exists.
4. In GitHub Actions, run `Legal CDR Zotero Monitor` with `setup_only` selected. This creates only missing collections.
5. Run it again with `dry_run` selected. Review the classification counts; no Zotero item will change.
6. If the preview is satisfactory, run once with both options cleared. This classifies all records currently in `00 — New Publications`.

The normal daily schedule then imports new records, deduplicates them, classifies them into all applicable collections, and sends unmatched records to `90 — Excluded or Tangential`.

This version includes both open and closed-access publications. DOI links lead to the official journal page; restricted PDFs are not downloaded.
