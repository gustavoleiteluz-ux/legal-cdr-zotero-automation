import json
import unittest
from pathlib import Path

from monitor import (
    BOOKS,
    Candidate,
    Classifier,
    EXCLUDED,
    crossref_item_type,
    curated_zotero_item,
    merge_candidate,
    openalex_item_type,
)


class ClassifierTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        config = json.loads((Path(__file__).parents[1] / "config" / "classification.json").read_text())
        cls.classifier = Classifier(config)

    def names(self, text):
        return self.classifier.classify_text(text).collections

    def test_multilabel_legal_oae(self):
        names = self.names("International law and permitting for ocean alkalinity enhancement under UNCLOS")
        self.assertIn("07 — Ocean Alkalinity Enhancement", names)
        self.assertIn("04 — International Law", names)
        self.assertIn("17 — Governance, Permitting and Field Trials", names)

    def test_scientific_mrv(self):
        names = self.names("Modelling environmental impacts and MRV for marine carbon dioxide removal")
        self.assertIn("03 — Scientific and Technical Literature", names)
        self.assertIn("10 — MRV and Environmental Integrity", names)
        self.assertIn("23 — Modelling, LCA and Techno-Economics", names)

    def test_medical_false_positive_excluded(self):
        result = self.classifier.classify_text("Extracorporeal carbon dioxide removal for respiratory failure")
        self.assertFalse(result.relevant)
        self.assertEqual([EXCLUDED], result.collections)

    def test_unrelated_ocean_article_excluded(self):
        result = self.classifier.classify_text("Deep-sea fish migration in the Atlantic Ocean")
        self.assertFalse(result.relevant)

    def test_unrelated_marine_regulation_excluded(self):
        result = self.classifier.classify_text("Regulation of marine fisheries and vessel licensing")
        self.assertFalse(result.relevant)

    def test_offshore_ccs(self):
        names = self.names("Legal governance of offshore carbon capture and storage in the North Sea")
        self.assertIn("16 — Offshore CCS and Geological Storage", names)
        self.assertIn("27 — European Union and United Kingdom", names)

    def test_closed_article_links_to_publisher(self):
        candidate = Candidate("Marine carbon dioxide removal law", [], 2026, "10.1234/example", "", "", "Journal", "journalArticle", "law", "OpenAlex", "closed")
        item = candidate.zotero_item("INBOX")
        self.assertEqual("https://doi.org/10.1234/example", item["url"])
        self.assertIn({"tag": "access:closed"}, item["tags"])
        self.assertIn({"tag": "link:publisher"}, item["tags"])

    def test_duplicate_merge_preserves_access_status(self):
        openalex = Candidate("Title", [], 2026, "10.1/x", "", "Abstract", "Journal", "journalArticle", "core", "OpenAlex", "closed")
        crossref = Candidate("Title", [], 2026, "10.1/x", "", "", "Journal", "journalArticle", "core", "Crossref", "unknown")
        merged = merge_candidate(openalex, crossref)
        self.assertEqual("closed", merged.access_status)

    def test_book_has_native_zotero_type_and_metadata(self):
        candidate = Candidate(
            "Ocean Carbon Dioxide Removal", ["A. Author"], 2024, "10.1/book", "", "",
            "Publisher", "book", "core", "Crossref", publisher="Publisher", isbn="9780000000000",
        )
        item = candidate.zotero_item("INBOX")
        self.assertEqual("book", item["itemType"])
        self.assertEqual("Publisher", item["publisher"])
        self.assertEqual("9780000000000", item["ISBN"])

    def test_book_chapter_has_native_zotero_type_and_container(self):
        candidate = Candidate(
            "Marine CDR Governance", ["A. Author"], 2024, "10.1/chapter", "", "",
            "Handbook of Ocean Governance", "bookSection", "law", "Crossref",
            book_title="Handbook of Ocean Governance", publisher="Publisher", pages="25-48",
        )
        item = candidate.zotero_item("INBOX")
        self.assertEqual("bookSection", item["itemType"])
        self.assertEqual("Handbook of Ocean Governance", item["bookTitle"])
        self.assertEqual("25-48", item["pages"])

    def test_source_type_mapping(self):
        self.assertEqual("book", openalex_item_type("book"))
        self.assertEqual("bookSection", openalex_item_type("book-chapter"))
        self.assertEqual("book", crossref_item_type("edited-book"))
        self.assertEqual("bookSection", crossref_item_type("book-chapter"))

    def test_curated_statute_uses_native_zotero_fields(self):
        definition = {
            "itemType": "statute",
            "nameOfAct": "Oceans Act",
            "code": "S.C. 1996, c. 31",
            "dateEnacted": "1996",
            "url": "https://laws-lois.justice.gc.ca/eng/acts/O-2.4/",
            "tags": ["workflow:curated"],
            "collections": ["31 — Canadian Legislation and Regulations"],
        }
        item = curated_zotero_item(definition, {"31 — Canadian Legislation and Regulations": "LAW"})
        self.assertEqual("statute", item["itemType"])
        self.assertEqual("Oceans Act", item["nameOfAct"])
        self.assertEqual(["LAW"], item["collections"])

    def test_curated_collection_names_exist(self):
        root = Path(__file__).parents[1]
        collections = {row["name"] for row in json.loads((root / "config" / "collections.json").read_text())}
        sources = json.loads((root / "config" / "legal_sources.json").read_text())
        self.assertIn(BOOKS, collections)
        for source in sources:
            self.assertTrue(set(source["collections"]).issubset(collections))


if __name__ == "__main__":
    unittest.main()
