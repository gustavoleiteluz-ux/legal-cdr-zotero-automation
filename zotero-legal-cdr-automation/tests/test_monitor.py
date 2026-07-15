import unittest

from monitor import Candidate, chunks, normalize_doi, normalize_text


class MonitorTests(unittest.TestCase):
    def test_normalize_doi(self):
        self.assertEqual(normalize_doi("https://doi.org/10.1000/ABC"), "10.1000/abc")

    def test_normalize_title(self):
        self.assertEqual(normalize_text("Marine CDR: Law & Policy"), "marine cdr law policy")

    def test_chunks(self):
        self.assertEqual(list(chunks([1, 2, 3], 2)), [[1, 2], [3]])

    def test_zotero_item_targets_queue(self):
        item = Candidate(
            title="A title", authors=["A. Author"], year=2026, doi="10.1/test",
            url="https://doi.org/10.1/test", abstract="", publication="Journal",
            item_type="journalArticle", topic="law", source="Crossref"
        ).zotero_item("QUEUE")
        self.assertEqual(item["collections"], ["QUEUE"])
        self.assertEqual(item["DOI"], "10.1/test")


if __name__ == "__main__":
    unittest.main()
