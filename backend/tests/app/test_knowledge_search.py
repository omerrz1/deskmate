import unittest
import io
import zipfile
from unittest import mock

from app import db, knowledge, voices
from .test_people import Company


class KnowledgeSearchTests(Company):
    def test_docx_extracts_body_table_and_headers(self):
        document = '''<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>Call instructions:</w:t></w:r></w:p><w:tbl><w:tr><w:tc><w:p><w:r><w:t>Ask for a short update.</w:t></w:r></w:p></w:tc></w:tr></w:tbl></w:body></w:document>'''
        header = '''<w:hdr xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:p><w:r><w:t>Confidential team brief</w:t></w:r></w:p></w:hdr>'''
        raw = io.BytesIO()
        with zipfile.ZipFile(raw, "w") as archive:
            archive.writestr("word/document.xml", document)
            archive.writestr("word/header1.xml", header)
        text = knowledge.extract_text("call-instructions.docx", raw.getvalue())
        self.assertIn("Call instructions:", text)
        self.assertIn("Ask for a short update.", text)
        self.assertIn("Confidential team brief", text)

    async def test_indexed_website_chunks_keep_source_urls_and_are_retrievable(self):
        with db.use(self.ws):
            kb_id = db.run("INSERT INTO knowledge_bases(name,created_at) VALUES(?,?)", ("Website: Acme", db.now()))
            await knowledge.ingest(kb_id, "Course fees", "The advanced robotics course costs £425 and includes lab access.",
                                   source="https://courses.acme.co.uk/fees")
            hits = knowledge.search(kb_id, "advanced robotics course fee", k=8)
            self.assertTrue(hits)
            self.assertTrue(any("£425" in hit for hit in hits))
            self.assertTrue(any("https://courses.acme.co.uk/fees" in hit for hit in hits))

    async def test_agent_search_lazily_builds_and_uses_the_linked_company_site(self):
        from app.runtime import employee_tools
        with db.use(self.ws):
            voices.set_setting("company_website", "https://acme.example")
            async def learn(url, kb_id=None):
                self.assertEqual(url, "https://acme.example")
                self.assertIsNone(kb_id)
                kid = db.run("INSERT INTO knowledge_bases(name,created_at) VALUES(?,?)", ("Website: Acme", db.now()))
                knowledge.add_doc(kid, "Fees", "The design course costs £300.", source="https://acme.example/fees")
                return {"kb_id": kid}
            with mock.patch("app.webdemo.learn", side_effect=learn):
                result, _ = await employee_tools.search_company_website(None, {"query": "design course price"})
            self.assertIn("£300", "\n".join(result["results"]))
            self.assertEqual(result["sources"], ["https://acme.example/fees"])
            self.assertTrue(voices.get_setting("company_website_kb_id"))


if __name__ == "__main__":
    unittest.main()
