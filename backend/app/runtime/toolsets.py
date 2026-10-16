"""Every tool an employee can use, registered in one place.

The ORDER is the order the model is shown the tools in, and some models accept only so many, so the last ones are cut first.
Do not reorder without meaning to."""
from .. import admin_agent, apps, app_requests, browser_tools, chats, db, discuss, meeting_bot, messaging, office, public_tools, team, workbench
from . import employee_tools


def _with_agent(run_tool):
    """Most modules expose `run_tool(agent, name, args)`; the harness hands a context, so unwrap the agent."""
    async def run(ctx, name, a):
        return await run_tool(ctx.agent, name, a)
    return run


async def _platform(ctx, name, a):
    """The tools the admin's copilot also has. A campaign an employee creates is theirs."""
    res, link = await admin_agent.run_tool(name, a)
    if name == "create_campaign" and isinstance(res, dict):
        cid = res.get("campaign_id") or res.get("id")
        if cid and ctx.agent:
            db.run("UPDATE campaigns SET agent_id=? WHERE id=?", (ctx.agent["id"], cid))
    return res, link


def register(registry):
    add = registry.add_toolset
    add(admin_agent.TOOLS, _platform, group="platform")
    add(employee_tools.AGENT_TOOLS, employee_tools.run, group="employee")
    add(office.TOOLS, _with_agent(office.run_tool), group="office")  # includes the file tools
    add(team.ALL, _with_agent(team.run_tool), group="team")
    add(employee_tools.CONNECTOR_TOOLS, employee_tools.run, group="connectors", requires="manager")
    add(apps.TOOLS, _with_agent(apps.run_tool), group="apps")
    add(messaging.TOOLS, _with_agent(messaging.run_tool), group="messaging")
    add(messaging.MANAGER_TOOLS, _with_agent(messaging.run_tool), group="messaging", requires="manager")
    add(chats.TOOLS, _with_agent(chats.run_tool), group="chats")
    add(workbench.TOOLS, _with_agent(workbench.run_tool), group="workbench")
    add(meeting_bot.TOOLS, _with_agent(meeting_bot.run_tool), group="meeting_bot")
    add(discuss.TOOLS, _with_agent(discuss.run_tool), group="discuss")
    add(app_requests.TOOLS, _with_agent(app_requests.run_tool), group="app_requests")
    add(public_tools.TOOLS, _with_agent(public_tools.run_tool), group="public")
    add(browser_tools.TOOLS, _with_agent(browser_tools.run_tool), group="browser")
