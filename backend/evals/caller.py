"""A production-like caller for the evaluation: the same prompt the ElevenLabs agent gets, the same tools (lookup_info, send_information, ask_admin, end_call, do_not_call…),
the same model and the same reply-length ceiling, talking to a simulated customer in text. Only the audio and the phone line are missing."""
import asyncio
import json
import random

from app import callengine, db, eleven_agents, llm, wa_call

TOOLLOG = {}   # call id -> [(tool, arguments, what came back)]


class _Session:
    def __init__(self, call_id):
        self.call_id = call_id


def chat_tools():
    return [{"type": "function", "function": {"name": d["name"], "description": d["description"][:1000], "parameters": d["parameters"]}} for d in wa_call._tool_defs()]


async def run_call(call_id, max_turns=12, miss_rate=0.15, rng=random):
    call, contact, camp = callengine.call_context(call_id)
    if call["status"] not in ("queued", "ringing"):
        return
    callengine.SIMS.add(call_id)
    try:
        persona = rng.choice(callengine.PERSONAS)[0]
        db.update("calls", call_id, status="ringing", ringing_at=db.now(), mode="sim", persona=persona)
        if rng.random() < miss_rate:
            callengine.mark_missed(call_id, rng.choice(["no_answer", "declined"]))
            return
        callengine.answer(call_id)
        prompt = callengine.system_prompt(call, contact, camp, realtime=True, external=True) + eleven_agents.tags_guide({"language": contact.get("language") or "English"})
        first = eleven_agents.first_line_for(contact)
        msgs = [{"role": "system", "content": prompt}, {"role": "assistant", "content": first}]
        transcript, base, log, ended = [], db.now(), [], False
        TOOLLOG[call_id] = log
        tools = chat_tools()

        def add(role, text):
            transcript.append({"role": role, "text": text, "t": round(db.now() - base, 1), "audio": []})
            db.update("calls", call_id, transcript=json.dumps(transcript))
        add("agent", first)
        for _ in range(max_turns):
            said = await callengine.customer_reply(contact, persona, transcript)
            add("customer", said)
            msgs.append({"role": "user", "content": said})
            reply = ""
            for _round in range(5):             # the caller may use several tools before it speaks
                r = await llm.client().chat.completions.create(model=eleven_agents.AGENT_LLM, messages=msgs, tools=tools, tool_choice="auto",
                                                               max_tokens=eleven_agents.AGENT_MAX_TOKENS, temperature=0.7)
                m = r.choices[0].message
                if not m.tool_calls:
                    reply = m.content or ""
                    break
                msgs.append({"role": "assistant", "content": m.content or "", "tool_calls": [{"id": tc.id, "type": "function", "function": {"name": tc.function.name, "arguments": tc.function.arguments}} for tc in m.tool_calls]})
                if m.content:
                    add("agent", eleven_agents.strip_tags(m.content))
                for tc in m.tool_calls:
                    try:
                        args = json.loads(tc.function.arguments or "{}")
                    except ValueError:
                        args = {}
                    if tc.function.name == "end_call":
                        ended, result = True, "Call ended."
                    else:
                        try:
                            result = await wa_call.run_tool(_Session(call_id), tc.function.name, args)
                        except Exception as e:
                            result = f"That didn't work ({type(e).__name__})."
                    log.append((tc.function.name, args, str(result)[:300]))
                    msgs.append({"role": "tool", "tool_call_id": tc.id, "content": str(result)[:3000]})
                if ended:
                    break
            if reply:
                add("agent", eleven_agents.strip_tags(reply))
                msgs.append({"role": "assistant", "content": reply})
            if ended:
                break
        await callengine.finalize(call_id, "completed")
    except Exception as e:
        db.update("calls", call_id, status="failed", summary=f"Simulation failed: {type(e).__name__}: {e}", ended_at=db.now())
    finally:
        callengine.SIMS.discard(call_id)


async def run_campaign(campaign_id, limit=60, concurrency=4):
    ids = [r["id"] for r in db.q("SELECT id FROM calls WHERE campaign_id=? AND status='queued' AND not_before<=? ORDER BY id LIMIT ?", (campaign_id, db.now(), limit))]
    sem = asyncio.Semaphore(concurrency)

    async def one(i):
        async with sem:
            await run_call(i)
    await asyncio.gather(*(one(i) for i in ids))
    return len(ids)
