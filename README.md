# Legal CDR Zotero Automation

Private automation for Zotero Group Library **6614701**. It creates a standardized collection structure, searches OpenAlex and Crossref daily, deduplicates against the existing group library, and places only new records in `00 — New Publications`.

## Security

Never commit a Zotero API key. The workflow reads it only from the protected GitHub Actions secret `ZOTERO_API_KEY`. The key should have read/write access only to group `6614701`, not to the personal library.

## One-time activation

1. Create a private GitHub repository and copy these files into it.
2. In **Settings → Secrets and variables → Actions → Secrets**, create `ZOTERO_API_KEY`.
3. Under **Variables**, create `CONTACT_EMAIL` with the email used for polite OpenAlex/Crossref API access.
4. Open **Actions → Legal CDR Zotero Monitor → Run workflow**.
5. Select `setup_only` for the first run. This creates the complete collection structure.
6. Run it again with the default options to perform the first discovery cycle.

The scheduled workflow runs daily at 10:17 UTC. GitHub may delay scheduled jobs during periods of high demand.

## Editorial workflow

- Automated discoveries enter `00 — New Publications` with `workflow:new`.
- A team member validates the metadata and relevance.
- Relevant records move to `01 — Review Queue` and the appropriate thematic collection.
- After substantive review, items move to `12 — Reviewed and Approved`.
- Tangential or excluded material moves to `90 — Excluded or Tangential`.

## Local validation

```bash
python -m unittest discover -s tests -v
python -m json.tool config/collections.json >/dev/null
python -m json.tool config/queries.json >/dev/null
```

For a local dry run, set `ZOTERO_API_KEY` in the terminal environment and run:

```bash
ZOTERO_GROUP_ID=6614701 CONTACT_EMAIL=your-email@example.org python monitor.py --dry-run
```

Do not place the API key in `.env` files that might be committed.
