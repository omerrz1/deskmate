import json

from . import db, llm
from .crm import call_out, contact_out


async def client_report(contact_id):
    c = db.q1("SELECT * FROM contacts WHERE id=?", (contact_id,))
    if not c:
        raise ValueError("contact not found")
    contact = contact_out(c)
    calls = [call_out(x, with_transcript=False) for x in db.q("SELECT * FROM calls WHERE contact_id=? ORDER BY id", (contact_id,))]
    hist = [{"attempt": x["attempt"], "status": x["status"], "outcome": x["outcome"], "sentiment": x["sentiment"],
             "summary": x["summary"], "follow_up": x["follow_up"], "duration": x["duration"]} for x in calls]
    if not hist:
        rep = {"headline": "No calls yet", "profile": f"{contact['name']} has not been contacted.", "history": "",
               "sentiment_trend": "n/a", "risks": [], "next_best_action": "Include in the next campaign.", "score": None}
    else:
        rep = await llm.complete_json(
            "You are a CRM analyst. From a customer record and their call history write a concise client report as JSON with keys: "
            "headline (one line), profile (2 sentences), history (2-3 sentences about the attempts and what happened), "
            "sentiment_trend (string), risks (array of short strings), next_best_action (one concrete sentence), "
            "score (integer 0-100: likelihood the customer will convert / cooperate). Write in English.",
            json.dumps({"customer": contact, "calls": hist}, ensure_ascii=False, default=str))
    rep["generated_at"] = db.now()
    rep["calls"] = len(hist)
    db.update("contacts", contact_id, report=rep, report_at=db.now())
    db.log("report", f"Client report generated for {contact['name']}", contact_id=contact_id)
    return rep


async def call_report(call_id):
    """Generate and persist a detailed, transcript-grounded report for one call."""
    row = db.q1("SELECT c.*, k.name contact_name, k.phone contact_phone, p.objective, p.name campaign_name FROM calls c LEFT JOIN contacts k ON k.id=c.contact_id LEFT JOIN campaigns p ON p.id=c.campaign_id WHERE c.id=?", (call_id,))
    if not row:
        raise ValueError("call not found")
    saved = db.jload(row.get("report"), {})
    transcript = db.jload(row.get("transcript"), [])
    transcript_text = "\n".join(f"{('AGENT' if t.get('role') == 'agent' else 'CUSTOMER')}: {t.get('text', '')}" for t in transcript)
    if saved:
        report = saved
    elif transcript_text:
        try:
            report = await llm.complete_json(
                "Write a thorough, factual call report in English as JSON. Keys: executive_summary (3-5 sentences), objective, objective_result (achieved|partially_achieved|not_achieved|unclear), conversation_flow (short account of the main stages), customer_needs (array), questions_and_answers (array of objects with question and answer_status: answered|partly_answered|unanswered, plus answer_or_gap), objections_or_concerns (array), commitments (array identifying who committed to what), outcome, sentiment, key_facts_shared (array), follow_up_actions (array of objects with action, owner, timing), risks_or_gaps (array), language. Use only the call record and transcript. Do not infer facts or invent commitments. If the call did not connect or evidence is missing, say so plainly.",
                json.dumps({"call_id": call_id, "contact": row.get("contact_name"), "phone": row.get("contact_phone"), "campaign": row.get("campaign_name"), "objective": row.get("objective") or row.get("reason"), "status": row.get("status"), "duration_seconds": row.get("duration"), "stored_summary": row.get("summary"), "stored_outcome": row.get("outcome"), "sentiment": row.get("sentiment"), "follow_up": row.get("follow_up"), "key_points": db.jload(row.get("key_points"), []), "transcript": transcript_text}, ensure_ascii=False))
        except Exception:
            report = {"executive_summary": row.get("summary") or "A transcript is available, but report analysis could not be generated.", "objective": row.get("objective") or row.get("reason") or "", "objective_result": "unclear", "conversation_flow": "See the attached transcript; automated analysis was unavailable.", "customer_needs": [], "questions_and_answers": [], "objections_or_concerns": [], "commitments": [], "outcome": row.get("outcome") or "unknown", "sentiment": row.get("sentiment") or "unknown", "key_facts_shared": [], "follow_up_actions": ([{"action": row["follow_up"], "owner": "team", "timing": "not specified"}] if row.get("follow_up") else []), "risks_or_gaps": ["Automated transcript analysis was unavailable; review the transcript before relying on this report."], "language": row.get("language") or "unknown"}
    else:
        report = {"executive_summary": row.get("summary") or f"No conversation transcript is available (call status: {row.get('status')}).", "objective": row.get("objective") or row.get("reason") or "", "objective_result": "unclear", "conversation_flow": "No transcript available.", "customer_needs": [], "questions_and_answers": [], "objections_or_concerns": [], "commitments": [], "outcome": row.get("outcome") or row.get("status"), "sentiment": row.get("sentiment") or "unknown", "key_facts_shared": [], "follow_up_actions": ([{"action": row["follow_up"], "owner": "team", "timing": "not specified"}] if row.get("follow_up") else []), "risks_or_gaps": ["No call transcript is available; conversation details could not be assessed."], "language": row.get("language") or "unknown"}
    if not saved:
        db.update("calls", call_id, report=report)
        db.log("report", f"Detailed call report generated for call {call_id}", call_id=call_id, contact_id=row.get("contact_id"))
    return {**report, "call_id": call_id, "contact": row.get("contact_name"), "phone": row.get("contact_phone"),
            "status": row.get("status"), "duration_seconds": round(row.get("duration") or 0), "transcript": transcript,
            "generated_at": row.get("ended_at") or row.get("created_at")}
