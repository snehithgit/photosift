"""One-off requests from the web app to the laptop. The agent pulls them like photo jobs, so Zima
never has to reach the laptop. Only these fixed kinds exist, and they take no arguments."""

import time
from typing import Literal

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field

from server.db import uid

KINDS = ("turn-on-models", "encode-prompts")
OPEN = ("queued", "running")
ONLINE_SECONDS = 60
# Long enough for a slow model; an older request must not run later as a surprise.
EXPIRE_SECONDS = 600
EXPIRED = (
    "The laptop didn't finish this. Check that its agent is running, then try again."
)
OFFLINE = "The laptop agent isn't connected. Start it on the laptop, then try again."


class CommandRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["turn-on-models", "encode-prompts"]


class CommandResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    state: Literal["done", "failed"]
    message: str = Field(max_length=1000)


def online(c):
    contact = c.execute("SELECT MAX(last_contact) FROM agent_contacts").fetchone()[0]
    return bool(contact and time.time() - contact < ONLINE_SECONDS)


def expired(row):
    return row["state"] in OPEN and time.time() - row["created"] > EXPIRE_SECONDS


def view(row):
    state, message = row["state"], row["message"]
    if expired(row):
        state, message = "failed", EXPIRED
    return {"id": row["id"], "kind": row["kind"], "state": state, "message": message}


def latest(c):
    rows = c.execute("SELECT * FROM agent_commands ORDER BY created").fetchall()
    found = {kind: None for kind in KINDS}
    found.update({row["kind"]: view(row) for row in rows})
    return found


def expire(c):
    c.execute(
        "UPDATE agent_commands SET state='failed',message=? WHERE state IN ('queued','running') AND created<?",
        (EXPIRED, time.time() - EXPIRE_SECONDS),
    )


def ask(c, kind):
    expire(c)
    waiting = c.execute(
        "SELECT * FROM agent_commands WHERE kind=? AND state IN ('queued','running')",
        (kind,),
    ).fetchone()
    if waiting:
        return view(waiting)
    if not online(c):
        raise HTTPException(409, OFFLINE)
    c.execute("DELETE FROM agent_commands WHERE kind=?", (kind,))
    c.execute(
        "INSERT INTO agent_commands(id,kind,created) VALUES (?,?,?)",
        (uid(), kind, time.time()),
    )
    return view(
        c.execute("SELECT * FROM agent_commands WHERE kind=?", (kind,)).fetchone()
    )


def claim(c):
    expire(c)
    row = c.execute(
        "SELECT * FROM agent_commands WHERE state='queued' ORDER BY created LIMIT 1"
    ).fetchone()
    if not row:
        return None
    c.execute("UPDATE agent_commands SET state='running' WHERE id=?", (row["id"],))
    return {"id": row["id"], "kind": row["kind"]}


def finish(c, identifier, result):
    # A command that expired meanwhile keeps saying so: the owner may already have asked again.
    c.execute(
        "UPDATE agent_commands SET state=?,message=? WHERE id=? AND state='running'",
        (result.state, result.message, identifier),
    )
    return {"state": result.state}
