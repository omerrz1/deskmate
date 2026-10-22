import json
from app import agents, db, files, file_search, project_briefs
from .test_people import Company


class InternalFileSearch(Company):
    def add(self, owner, title, body, folder='Documents'):
        return db.run("INSERT INTO documents(agent_id,title,kind,body,folder_id,created_at,updated_at) VALUES(?,?,'doc',?,?,?,?)", (owner, title, body, files.folder_id(owner, folder), db.now(), db.now()))

    async def test_search_finds_content_typos_and_other_agent_folders(self):
        with db.use(self.ws):
            aid = agents.create('Search tester', 'female', 'Support', '', '', bio='')
            other = agents.create('Other colleague', 'female', 'Support', '', '', bio='')
            doc = self.add(other, 'Admissions instructions', 'Use the cobalt reference for scholarship applications.', 'From admins/Admissions')
            private = self.add(files.person_owner(42), 'Admissions private', 'cobalt')
            for query in ('cobalt details missing', 'instrauctions', 'Admissions'):
                result = file_search.search(agents.get(aid), query)
                ids = [r['id'] for r in result['results']]
                self.assertIn(doc, ids)
                self.assertNotIn(private, ids)
            result = file_search.search(agents.get(aid), '', limit=1)
            self.assertLessEqual(len(result['results']), 1)
            self.assertGreater(result['searched_files'], 0)

    async def test_brief_creates_one_project_and_is_reloaded_for_its_tasks(self):
        with db.use(self.ws):
            aid = agents.create('Planner', 'female', 'Manager', '', '', bio='')
            agent = agents.get(aid)
            original = self.add(aid, 'Instructions', 'Call only the cobalt leads. Report every outcome.')
            other = self.add(aid, 'Unrelated instructions', 'This different project uses vermilion.', 'From admins')
            result = project_briefs.organize(agent, original, 'Cobalt outreach')
            again = project_briefs.organize(agent, original, 'Cobalt outreach')
            self.assertEqual(result['project_id'], again['project_id'])
            self.assertTrue(again['already_organized'])
            self.assertEqual(project_briefs.organize(agent, result['brief_doc_id'], 'Retry')['project_id'], result['project_id'])
            self.assertTrue(db.q1('SELECT id FROM duties WHERE id=?', (result['planning_task'],)))
            self.assertTrue(db.q1('SELECT id FROM documents WHERE id=?', (result['plan_doc_id'],)))
            context = agents._task_document_context(agent, {'title':'Execute project', 'project_id':result['project_id']})
            self.assertIn('cobalt leads', context)
            self.assertNotIn('vermilion', context)
            self.assertIn(str(result['brief_doc_id']), context)
            self.assertEqual(db.q1('SELECT title FROM documents WHERE id=?', (original,))['title'], 'Instructions')
