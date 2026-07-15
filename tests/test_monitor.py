import json
import unittest
from pathlib import Path

from monitor import Classifier, EXCLUDED


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


if __name__ == "__main__":
    unittest.main()
