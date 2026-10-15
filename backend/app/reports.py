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
