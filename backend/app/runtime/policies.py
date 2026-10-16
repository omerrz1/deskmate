"""The rules every employee tool call passes through, listed in the order they are checked (see `install`)."""
import re

from harness import Decision, Policy, PolicyEngine

from .. import apps, capabilities, db, training

LIVE_KINDS = ("one_to_one", "group")  # conversations where the admin is talking to the employee

# Jobs that take real work. In a meeting they go on a to-do list instead (done right after the meeting), unless the admin says to do it now.
HEAVY = {"create_document", "create_spreadsheet", "export_document", "make_chart", "edit_sheet", "update_document", "sheet_to_customers", "learn_from_website", "create_knowledge_base",
         "add_knowledge", "update_knowledge_base", "create_campaign", "start_campaign", "call_contact", "schedule_call"}
NOW_WORDS = re.compile(r"\b(now|right now|right away|straight away|straightaway|immediately|asap|at once|this minute|this second|go ahead and do)\b", re.I)


def meeting_gate(mid, name):
    """-> an error for the agent when it starts heavy work during a meeting without being told to do it now, else None."""
    if not mid or name not in HEAVY:
        return None
    for r in db.q("SELECT text,created_at FROM meeting_msgs WHERE meeting_id=? AND role='admin' ORDER BY id DESC LIMIT 3", (mid,)):
        if db.now() - r["created_at"] < 300 and NOW_WORDS.search(r["text"]):
            return None
    return {"error": "You are in a meeting: don't do this work now. Put it on the right person's to-do list with add_todo (they start right after the meeting) and tell the admin in one short sentence that it is on the list (no need to email anyone). "
                     "Only if the admin says to do it right now: call add_todo with do_now=true, or just do it."}


def _writes_to_app(inv):
    return apps.is_write(inv.args.get("app"), inv.args.get("action"))


class MeetingGate(Policy):
    """In a meeting with the admin, heavy work goes on a to-do list instead of being started on the spot."""
    name = "meeting_gate"

    def check(self, ctx, inv):
        if ctx.kind not in LIVE_KINDS:
            return None
        gate = meeting_gate(ctx.get("meeting_id"), inv.name)
        return Decision.deny(gate, "heavy work during a meeting") if gate else None


class CustomerChatReadOnly(Policy):
    """A stranger writing in a customer chat may not make the employee change anything in the connected apps."""
    name = "customer_chat_read_only"

    def check(self, ctx, inv):
        if ctx.kind == "customer_chat" and inv.name == "use_app" and _writes_to_app(inv):
            return Decision.deny({"error": "In a customer chat you may read from connected apps but not change anything. Say a colleague will take care of it and use ask_for_help or notify_admin."},
                                 "write to a connected app from a customer chat")
        return None


class PracticeMode(Policy):
    """In a practice run, anything that is not a pure read is pretended instead of done."""
    name = "practice_mode"

    def check(self, ctx, inv):
        if not ctx.dry_run:
            return None
        if training.is_readonly(inv.name) or (inv.name == "use_app" and not _writes_to_app(inv)):
            return None
        return Decision.simulate({"practice_mode": True, "note": f"Simulated. In real life this would run {inv.name}. Nothing happened."}, "practice run")


class Capability(Policy):
    """Each employee's abilities can be switched off by the admin (Settings, per person). A switched-off tool is not even shown to the model."""
    name = "capability"

    def visible(self, ctx, tool):
        return capabilities.allows(ctx.agent, tool.name)

    def check(self, ctx, inv):
        if ctx.agent and not capabilities.allows(ctx.agent, inv.name):
            return Decision.deny({"error": "That ability is switched off for you. Ask your manager, or use request_meeting to ask the admin."}, "ability switched off")
        return None


class ManagerOnly(Policy):
    """Tools registered with requires="manager"."""
    name = "manager_only"

    def check(self, ctx, inv):
        if inv.tool and inv.tool.requires == "manager" and not (ctx.agent and ctx.agent.get("is_manager")):
            return Decision.deny({"error": "Only a manager can do that."}, "not a manager")
        return None


class HideForManagers(Policy):
    """Managers have hire_agent; two names for one job confuse the model, so the older alias is not offered to them."""
    name = "hide_for_managers"

    def __init__(self, *names):
        self.names = set(names)

    def visible(self, ctx, tool):
        return not ((ctx.agent or {}).get("is_manager") and tool.name in self.names)


def install(engine: PolicyEngine) -> None:
    """The order matters: the first policy with an opinion decides."""
    engine.add(MeetingGate())
    engine.add(CustomerChatReadOnly())
    engine.add(PracticeMode())
    engine.add(Capability())
    engine.add(ManagerOnly())
    engine.add(HideForManagers("create_agent"))
