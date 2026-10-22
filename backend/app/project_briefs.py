"""Durable links from source instructions to projects, plans and task context."""
from . import db


def organize(agent, doc_id, name, goal=''):
    from . import files, team
    doc, error = files.resolve_document(agent, doc_id=doc_id)
    if error:
        return {'error': error}
    if not agent:
        return {'error': 'An employee must own the instruction project.'}
    if doc['kind'] == 'sheet':
        return {'error': 'Use the instructions document as the brief; attach the lead sheet as supporting data.'}
    canonical = db.q1('SELECT source_doc_id FROM chat_file_copies WHERE doc_id=?', (doc_id,))
    source_id = canonical['source_doc_id'] if canonical else doc_id
    prior = db.q1('SELECT b.* FROM project_briefs b JOIN projects p ON p.id=b.project_id WHERE (b.source_doc_id=? OR b.brief_doc_id=?) AND p.status<>\'deleted\'', (source_id, doc_id))
    if prior:
        db.run("INSERT OR IGNORE INTO project_members(project_id,agent_id,role,joined_at) VALUES(?,?,'member',?)", (prior['project_id'], agent['id'], db.now()))
        return {**prior, 'already_organized': True}
    name = str(name or doc['title']).strip()[:80] or 'Instruction project'
    pid = db.run('INSERT INTO projects(name,goal,created_by,created_at) VALUES(?,?,?,?)', (name, str(goal or f'Complete the work described in source file #{source_id}.')[:1000], agent['id'], db.now()))
    db.run("INSERT INTO project_members(project_id,agent_id,role,joined_at) VALUES(?,?,'owner',?)", (pid, agent['id'], db.now()))
    folder = files.folder_id(-pid, 'Briefs')
    brief_id = files.copy_doc(doc, -pid, folder)
    title = f'Project {pid} — {name} — Source brief'
    db.update('documents', brief_id, title=title[:160], updated_at=db.now())
    # Keep original bytes/title intact; the project copy has a unique, stable identity.
    db.run('INSERT OR REPLACE INTO project_briefs(source_doc_id,project_id,brief_doc_id,original_title,created_at) VALUES(?,?,?,?,?)', (source_id, pid, brief_id, doc['title'], db.now()))
    plan = f'# Plan: {name}\n\nSource: document #{brief_id} ({title}). Original: #{source_id} ({doc["title"]}).\n\n1. Read the entire source brief and linked company data.\n2. Extract requirements, constraints and acceptance criteria.\n3. Create and assign concrete project to-dos with this brief reference.\n4. Execute in resumable batches, recording progress and results.\n5. Re-read the brief and verify each requirement before reporting completion.\n'
    plan_id = db.run("INSERT INTO documents(agent_id,title,kind,body,folder_id,created_at,updated_at) VALUES(?,?,'doc',?,?,?,?)", (-pid, f'Project {pid} — {name} — Working plan', plan, files.folder_id(-pid, 'Plans'), db.now(), db.now()))
    duty = team.plan_project(pid, agent['id'])
    if duty:
        row = db.q1('SELECT detail FROM duties WHERE id=?', (duty,))
        db.update('duties', duty, detail=row['detail'] + f'\nRequired source brief: document #{brief_id}. Working plan: document #{plan_id}. Read the whole brief before planning; re-check it during execution and review.', ref=f'brief:{brief_id};source:{source_id}')
    return {'project_id': pid, 'brief_doc_id': brief_id, 'source_doc_id': source_id, 'plan_doc_id': plan_id, 'planning_task': duty, 'brief_title': title, 'already_organized': False}
