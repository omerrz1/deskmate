"""Internal, workspace-scoped file discovery with ranked excerpts and broad fallback."""
import difflib
import re
from pathlib import Path
from . import db


def search(agent, query, offset=0, limit=20):
    from . import files, knowledge, team
    query = str(query or '').strip()[:500]
    terms = knowledge._tok(query) - knowledge._STOP - {'file', 'files', 'find', 'document', 'search'}
    ranked, unreadable, scanned = [], [], 0
    for doc in db.q('SELECT d.*,a.name owner_name FROM documents d LEFT JOIN agents a ON a.id=d.agent_id ORDER BY d.updated_at DESC'):
        if not team.can_access_doc(agent, doc):
            continue
        scanned += 1
        path = files.path_of(doc.get('folder_id'))
        title = doc['title'] or ''
        metadata = f"{title} {path} {doc.get('owner_name') or 'Company drive'}"
        body = doc.get('body') or ''
        if not body and doc.get('file_path'):
            try:
                body = knowledge.extract_text(title, (db.data_dir() / 'files' / Path(doc['file_path']).name).read_bytes())
            except Exception:
                unreadable.append({'id': doc['id'], 'title': title})
        meta_terms = knowledge._tok(metadata)
        body_terms = knowledge._tok(body)
        exact = bool(query and query.casefold() in metadata.casefold())
        matched = terms & (meta_terms | body_terms)
        fuzzy = {term for term in terms if term not in matched and difflib.get_close_matches(term, meta_terms, n=1, cutoff=.75)}
        score = 30 * exact + 8 * len(terms & meta_terms) + 2 * len(matched) + len(fuzzy)
        if terms and not score:
            continue
        position = min((body.lower().find(term) for term in matched if body.lower().find(term) >= 0), default=0)
        start = max(0, position - 120)
        ranked.append((score, doc.get('updated_at') or 0, {'id': doc['id'], 'title': title, 'kind': doc['kind'],
                       'drive_id': doc['agent_id'], 'owner': doc.get('owner_name') or ('Company drive' if doc['agent_id'] == 0 else 'Project drive'),
                       'folder': path, 'excerpt': body[start:start + 750], 'read_offset': start,
                       'matched_terms': sorted(matched), 'approximate_terms': sorted(fuzzy), 'updated_at': doc.get('updated_at')}))
    ranked.sort(key=lambda row: (row[0], row[1]), reverse=True)
    offset = max(0, int(offset or 0)); limit = max(1, min(100, int(limit or 20)))
    return {'query': query, 'searched_files': scanned, 'total_matches': len(ranked),
            'results': [row[2] for row in ranked[offset:offset + limit]],
            'has_more': offset + limit < len(ranked), 'next_offset': offset + limit if offset + limit < len(ranked) else None,
            'unreadable_files': unreadable[:20], 'unreadable_count': len(unreadable),
            'scope': 'This company: company drive, employee drives, authorized project drives and shared files.',
            'next_action': 'Read the matching files using their ids and offsets; inspect all relevant results before deciding information is missing.' if ranked else
                           'No match in readable accessible files. Search again with an empty query to browse all files, a shorter term, folder or colleague name. Unreadable files are not evidence of absence.'}
