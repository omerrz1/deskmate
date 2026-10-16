"""What the admin sees of an employee's real browser window on their Computer: the window's state, and a live picture of it. Watch only."""
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from . import agents, browser_tools

router = APIRouter()


@router.get("/api/agents/{aid}/browser")
async def browser_state(aid: int):
    if not agents.get(aid):
        raise HTTPException(404, "No such employee.")
    return await browser_tools.live_state(aid)


@router.get("/api/agents/{aid}/browser/live")
async def browser_live(aid: int, request: Request):
    if not agents.get(aid):
        raise HTTPException(404, "No such employee.")
    if not browser_tools.is_open(aid):
        raise HTTPException(404, "No browser window is open.")
    return StreamingResponse(browser_tools.mjpeg(aid, request), media_type="multipart/x-mixed-replace; boundary=frame",
                             headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})
