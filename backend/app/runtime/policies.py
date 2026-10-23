"""The rules every employee tool call passes through, listed in the order they are checked (see `install`)."""
import re

from harness import Decision, Policy, PolicyEngine

from .. import apps, capabilities, db, training

LIVE_KINDS = ("one_to_one", "group")  # conversations where the admin is talking to the employee

# Jobs that take real work. In a meeting they go on a to-do list instead (done right after the meeting), unless the admin says to do it now.
HEAVY = {"organize_instructions", "create_document", "create_spreadsheet", "export_document", "make_chart", "edit_sheet", "update_document", "sheet_to_customers", "learn_from_website", "create_knowledge_base",
         "sheet_clean", "sheet_merge", "sheet_job", "start_mission", "mission",
         "add_knowledge", "update_knowledge_base", "create_campaign", "start_campaign", "call_contact", "schedule_call"}
NOW_WORDS = re.compile(r"\b(now|right now|right away|straight away|straightaway|immediately|asap|at once|this minute|this second|go ahead and do)\b", re.I)


def meeting_gate(mid, name, args=None):
    """Changes need a timing choice in the current admin turn, never an old 'now'."""
    if not mid or (training.is_readonly(name) and name not in HEAVY):
        return None
    if name == "use_app" and not apps.is_write((args or {}).get("app"), (args or {}).get("action")):
        return None
    r = db.q1("SELECT text FROM meeting_msgs WHERE meeting_id=? AND role='admin' ORDER BY id DESC LIMIT 1", (mid,))
    text = (r or {}).get("text", "")
    later = re.search(r"\b(after (?:the |this )?meeting|later|afterwards)\b", text, re.I)
    negative = re.search(r"\b(don't|do not|not now|cancel|hold off|wait)\b", text, re.I)
    if later:
        if name == "add_todo" and not (args or {}).get("do_now") and not re.search(r"\b(don't|do not|cancel)\b", text, re.I):
            return None
    elif not negative and (NOW_WORDS.search(text) or re.search(r"\b(call|phone|hire|create|start|run|process|import|update|send|assign|delegate|make|build|prepare|clean|merge|combine|dedupe|tidy|fix|fill|sort|classify)\b", text, re.I)):
        return None
    return {"error": "You are in a meeting. Before changing anything or adding a task, ask: Would you like me to do that now or after the meeting? Wait for the admin's timing choice. If they chose later, only use add_todo with do_now=false. Do not perform the work yet."}


def _writes_to_app(inv):
    return apps.is_write(inv.args.get("app"), inv.args.get("action"))


class MeetingGate(Policy):
    """In a meeting with the admin, heavy work goes on a to-do list instead of being started on the spot."""
    name = "meeting_gate"

    def check(self, ctx, inv):
        if ctx.kind not in LIVE_KINDS:
            return None
        gate = meeting_gate(ctx.get("meeting_id"), inv.name, inv.args)
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
