"""D3 Nano's answers become evidence and holds; they never make a Keep or Clutter decision."""

import json
from typing import Literal

from fastapi import HTTPException
from pydantic import BaseModel, Field, ValidationError

from server.contracts import Contract
from server.db import bump, fingerprint, now, packed, uid
from server.leases import valid_lease
from server.queries import detail

UNCONFIGURED = "d3-systemone:unconfigured"
# Between the two cut-offs an axis stays Unknown, which keeps the photo held. "Not sensitive"
# lets a photo reach Organized/ and show unblurred, so that cut-off is the strictest. On 24
# generated IDs, cards and records the lowest score was 0.14; real IDs are not measured.
POLICY = {
    "version": "photosift-d3-policy-v1",
    "sensitive": {"yes": 0.5, "no": 0.02},
    "important": {"yes": 0.5, "no": 0.2},
    "people": {"yes": 0.5, "no": 0.1},
}
REQUIRED = {"sensitive", "important", "people", "text", "content"}


class AssessmentConfig(Contract):
    protocol: Literal["ollama-systemone"]
    model: str = Field(min_length=1, max_length=500)
    modelDigest: str = Field(pattern=r"^[a-f0-9]{64}$")
    runtime: str = Field(min_length=1, max_length=200)
    questionVersion: str = Field(min_length=1, max_length=200)
    state: str = Field(min_length=1, max_length=2000)
    questions: dict
    input: dict


class NoulAnswer(BaseModel):
    type: Literal["noul"]
    noul: float = Field(ge=0, le=1, allow_inf_nan=False)


class ChoiceAnswer(BaseModel):
    type: Literal["choice"]
    choice: str = Field(min_length=1, max_length=200)
    confidence: float = Field(ge=0, le=1, allow_inf_nan=False)


class Answers(BaseModel):
    sensitive: NoulAnswer
    important: NoulAnswer
    people: NoulAnswer
    text: NoulAnswer
    content: ChoiceAnswer


class AssessmentResult(BaseModel):
    model: str
    answers: Answers
    durationMs: float = Field(ge=0, allow_inf_nan=False)


def triage(photo, band):
    if photo["decision"] == "keep":
        return "likely_keep"
    if (
        photo["protected"]
        or photo["sensitive"] != "no"
        or photo["importance"] != "no"
        or (photo["faces"] == "not_checked" and photo.get("people") != "no")
    ):
        return "needs_review"
    if photo["groupId"] in ("people", "events", "places", "documents"):
        return "likely_keep" if band != "low" else "needs_review"
    if photo["groupId"] in ("screenshots", "memes", "badshots") and band == "high":
        return "clutter_candidate"
    return "needs_review"


def active_spec(c):
    row = c.execute(
        "SELECT fingerprint,config FROM stage_specs WHERE stage='assess' AND active=1"
    ).fetchone()
    if not row:
        return None
    return {"fingerprint": row["fingerprint"], "config": json.loads(row["config"])}


def queue_active(c, content):
    spec = active_spec(c)
    if spec:
        c.execute(
            "INSERT OR IGNORE INTO stage_jobs(id,content_id,stage,fingerprint) VALUES (?,?,'assess',?)",
            (uid(), content, spec["fingerprint"]),
        )
    return bool(spec)


def configure(c, config):
    value = config.model_dump()
    if not REQUIRED <= set(value["questions"]):
        raise HTTPException(422, "The assessment must ask every required question")
    digest = fingerprint(value)
    c.execute(
        "DELETE FROM stage_jobs WHERE stage='assess' AND fingerprint=?",
        (UNCONFIGURED,),
    )
    c.execute(
        "UPDATE stage_jobs SET state='skipped',token=NULL,expires=NULL,error='Assessment contract superseded' WHERE stage='assess' AND fingerprint!=? AND state IN ('queued','running','paused')",
        (digest,),
    )
    c.execute("UPDATE stage_specs SET active=0 WHERE stage='assess'")
    c.execute(
        "INSERT INTO stage_specs VALUES (?,'assess',?,1,?) ON CONFLICT(fingerprint) DO UPDATE SET active=1",
        (digest, packed(value), now()),
    )
    queued = c.execute(
        "UPDATE stage_jobs SET state='queued',error=NULL WHERE stage='assess' AND fingerprint=? AND state='skipped'",
        (digest,),
    ).rowcount
    for row in c.execute(
        "SELECT DISTINCT p.content_id FROM preparation_outputs p JOIN stage_specs s ON s.fingerprint=p.fingerprint AND s.active=1 JOIN file_instances f ON f.content_id=p.content_id AND f.state='available'"
    ).fetchall():
        queued += c.execute(
            "INSERT OR IGNORE INTO stage_jobs(id,content_id,stage,fingerprint) VALUES (?,?,'assess',?)",
            (uid(), row[0], digest),
        ).rowcount
    return {"fingerprint": digest, "queued": queued}


def axis(name, value):
    cut = POLICY[name]
    return "yes" if value >= cut["yes"] else "no" if value <= cut["no"] else "unknown"


def evidence_row(label, verdict, value):
    word = {"yes": "yes", "no": "no", "unknown": "unclear"}[verdict]
    return {
        "source": "d3nano",
        "text": f"{label}: {word} (score {value:.2f}; a model signal, not proof)",
        "strength": "small_margin" if verdict == "unknown" else "strong",
    }


def retriage(c, content):
    photo = detail(c, content)
    c.execute(
        "UPDATE photos SET triage=? WHERE content_id=?",
        (triage(photo, photo["band"]), content),
    )


def accept(c, identifier, request):
    previous = c.execute(
        "SELECT * FROM stage_results WHERE job_id=?", (identifier,)
    ).fetchone()
    if previous:
        job = c.execute(
            "SELECT owner,attempt FROM stage_jobs WHERE id=?", (identifier,)
        ).fetchone()
        if (
            previous["token"] != request.token
            or previous["payload"] != packed(request.payload)
            or previous["fingerprint"] != request.fingerprint
            or previous["content_id"] != request.sha256
            or job["owner"] != request.owner
            or job["attempt"] != request.attempt
        ):
            raise HTTPException(409, "Conflicting duplicate assessment")
        return json.loads(previous["ack"])
    job = valid_lease(c, identifier, request)
    spec = active_spec(c)
    if (
        job["stage"] != "assess"
        or not spec
        or spec["fingerprint"] != request.fingerprint
        or job["fingerprint"] != request.fingerprint
        or job["content_id"] != request.sha256
    ):
        raise HTTPException(409, "Assessment contract or input mismatch")
    try:
        result = AssessmentResult.model_validate(request.payload)
    except ValidationError as exc:
        raise HTTPException(422, str(exc)) from exc
    answers = result.answers
    if (
        result.model != spec["config"]["model"]
        or answers.content.choice
        not in spec["config"]["questions"]["content"]["criteria"]
    ):
        raise HTTPException(422, "Answer does not match the configured assessment")
    source = c.execute(
        "SELECT * FROM file_instances WHERE id=?", (job["source_instance"],)
    ).fetchone()
    if (
        not source
        or source["state"] != "available"
        or (source["content_id"], source["size"], source["mtime_ns"])
        != (request.sha256, job["source_size"], job["source_mtime_ns"])
    ):
        raise HTTPException(409, "Assessment source version superseded")
    sensitive = axis("sensitive", answers.sensitive.noul)
    important = axis("important", answers.important.noul)
    people = axis("people", answers.people.noul)
    before = detail(c, request.sha256)
    evidence = [
        row
        for row in before["evidence"]
        if row["source"] != "d3nano"
        and not (
            sensitive != "unknown"
            and important != "unknown"
            and "importance and sensitivity unresolved" in row["text"]
        )
    ]
    evidence += [
        evidence_row("Sensitive content", sensitive, answers.sensitive.noul),
        evidence_row("Worth keeping", important, answers.important.noul),
        evidence_row("People visible", people, answers.people.noul),
        {
            "source": "d3nano",
            "text": f"Looks like: {answers.content.choice} (confidence {answers.content.confidence:.2f}); text visible score {answers.text.noul:.2f}",
            "strength": "strong" if answers.content.confidence >= 0.5 else "weak",
        },
    ]
    c.execute(
        "UPDATE photos SET sensitive=?,importance=?,people=?,evidence=?,review_version=review_version+1 WHERE content_id=?",
        (sensitive, important, people, packed(evidence), request.sha256),
    )
    if people == "yes":
        c.execute(
            "UPDATE photos SET protection=json_insert(protection,'$[#]','faces') WHERE content_id=? AND NOT EXISTS(SELECT 1 FROM json_each(photos.protection) WHERE value='faces')",
            (request.sha256,),
        )
    retriage(c, request.sha256)
    ack = {
        "accepted": True,
        "jobId": identifier,
        "sensitive": sensitive,
        "importance": important,
        "people": people,
        "policy": POLICY["version"],
    }
    c.execute(
        "INSERT INTO stage_results VALUES (?,?,?,?,?,?,?,?)",
        (
            request.sha256,
            "assess",
            request.fingerprint,
            identifier,
            request.token,
            packed(request.payload),
            packed(ack),
            now(),
        ),
    )
    c.execute(
        "UPDATE stage_jobs SET state='done',expires=NULL,error=NULL WHERE id=?",
        (identifier,),
    )
    bump(c)
    return ack
