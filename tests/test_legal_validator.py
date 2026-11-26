import unittest
import tempfile
from unittest.mock import patch

from core import legal_validator as validator


class LegalValidatorTests(unittest.TestCase):
    def test_extracts_law_year_and_article(self):
        item = {"law_name": "قانون رقم 12 لسنة 2003", "article_number": "7", "text": "Article content"}
        self.assertEqual(validator._law_identity(item), ("12", "2003", "7"))

    def test_unchanged_law_is_not_assumed_valid(self):
        results = [{"title": "قانون رقم 12 لسنة 2003", "body": "النص الرسمي" , "href": "https://manshurat.org/law"}]
        status, _ = validator._classify(results, "12", "2003", "")
        self.assertEqual(status, "unverified")

    def test_missing_article_does_not_use_shared_law_level_cache(self):
        with patch.object(validator, "DBConnection") as db_connection:
            self.assertIsNone(validator._cached("12", "2003", ""))
            validator._persist_verification({"text": ""}, "12", "2003", "", {"status": "unverified"})
            db_connection.assert_not_called()

    def test_proposed_amendment_is_not_enacted(self):
        results = [{"title": "مشروع قانون رقم 12 لسنة 2003", "body": "تعديل المادة 7", "href": "https://manshurat.org/law"}]
        self.assertEqual(validator._classify(results, "12", "2003", "7")[0], "unverified")

    def test_conflicting_search_findings_remain_unverified(self):
        results = [
            {"title": "قانون رقم 12 لسنة 2003 تعديل", "body": "صدر القانون المعدل", "href": "https://manshurat.org/a"},
            {"title": "مشروع تعديل القانون 12 لسنة 2003", "body": "مشروع قانون", "href": "https://example.org/b"},
        ]
        self.assertEqual(validator._classify(results, "12", "2003", "")[0], "unverified")

    @patch.object(validator, "_persist_verification")
    @patch.object(validator, "_ddg_search", side_effect=RuntimeError("offline"))
    def test_failed_search_returns_unverified(self, _search, _persist):
        result = validator.verify_law({"law_name": "قانون رقم 9 لسنة 1990", "text": "نص"}, force=True)
        self.assertEqual(result["status"], "unverified")

    @patch.object(validator, "_persist_verification")
    @patch.object(validator, "_ddg_search", return_value=[])
    def test_historical_question_does_not_claim_historical_applicability(self, _search, _persist):
        item = {"law_name": "قانون رقم 9 لسنة 1990", "text": "نص"}
        validator.validate_retrieved_laws([item], "الواقعة حدثت في سنة 2010", force=True)
        self.assertTrue(item["historical_question"])
        self.assertEqual(item["historical_applicability"], "unverified")

    @patch.object(validator, "DBConnection")
    def test_historical_version_requires_full_year_coverage(self, db_connection):
        class DB:
            def __enter__(self):
                return self
            def __exit__(self, *args):
                pass
            def fetchall(self, *args):
                return [{"version_status": "amended", "text": "version", "valid_from": "2010-01-01",
                         "valid_until": "2010-06-30", "publication_date": "2009-12-01",
                         "effective_date": "2010-01-01", "source_url": "https://manshurat.org/law"}]
        db_connection.return_value = DB()
        self.assertIsNone(validator.resolve_applicable_version("9", "1990", "2", "2010"))

    def test_direct_partial_repeal_snippet_is_not_proof(self):
        results = [{"title": "إلغاء المادة 7 من القانون 12 لسنة 2003", "body": "صدر تعديل", "href": "https://manshurat.org/law"}]
        self.assertEqual(validator._classify(results, "12", "2003", "7")[0], "unverified")

    def test_failed_or_empty_newer_law_search_stays_unverified(self):
        self.assertEqual(validator._classify([], "12", "2003", "7")[0], "unverified")

    def test_chroma_update_invalidates_bm25_without_eager_corpus_rebuild(self):
        from core.retrieval import HybridRetriever

        class Collection:
            def __init__(self):
                self.upserts = []
                self.deletes = []
            def get(self, **kwargs):
                return {"ids": ["old-article"], "documents": ["old"], "metadatas": [{}]}
            def upsert(self, **kwargs):
                self.upserts.append(kwargs)
            def delete(self, **kwargs):
                self.deletes.append(kwargs)
            def count(self):
                return 1

        collection = Collection()
        retriever = HybridRetriever(collection=collection)
        retriever._bm25_built = True
        with tempfile.TemporaryDirectory() as tempdir, patch("core.retrieval.CHROMA_DB_PATH", tempdir):
            retriever.replace_legal_version("12", "2003", "7", "new article", {"title": "Law", "legal_status": "repealed"})
        self.assertEqual(collection.upserts[0]["ids"], ["old-article"])
        self.assertFalse(retriever._bm25_built)
        self.assertIsNone(retriever._bm25)
        self.assertEqual(collection.upserts[0]["metadatas"][0]["legal_status"], "repealed")


if __name__ == "__main__":
    unittest.main()
