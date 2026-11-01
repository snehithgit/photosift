import test_preparation as preparation_fixtures

from agent.__main__ import execute

prepared_workspace = preparation_fixtures.prepared_workspace


def axis(index):
    return [float(i == index) for i in range(16)]


def register(client, backend="siglip2"):
    response = client.post(
        "/api/indexes",
        json={
            "backend": backend,
            "checkpoint": "test-fixture-only",
            "revision": "a" * 40,
            "dimension": 16,
            "preprocessing": {"testOnly": True},
            "precision": "float32",
            "normalization": "l2",
            "runtime": "fixture-v1",
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_isolated_embedding_acceptance_comparison_and_explicit_promotion(
    prepared_workspace,
):
    client, db, source, root, config, claim = prepared_workspace
    execute(client, claim, {root: str(source)}, config)
    index = register(client)
    alternative = register(client, "mobileclip2")
    assert index["role"] == alternative["role"] == "eval"
    assert (
        client.post(
            "/api/indexes/" + index["id"] + "/promote",
            json={"evaluationId": "not-measured"},
        ).status_code
        == 409
    )
    queued = client.post(
        "/api/indexes/" + index["id"] + "/queue", json={"contentIds": [claim["sha256"]]}
    )
    assert queued.status_code == 200, queued.text
    job = client.post(
        "/api/agent/claim",
        json={"owner": "laptop", "stage": "embed", "fingerprint": index["fingerprint"]},
    ).json()
    body = {key: job[key] for key in ("owner", "token", "attempt", "fingerprint")}
    body.update(
        {
            "sha256": job["sha256"],
            "payload": {"indexId": alternative["id"], "vector": axis(0)},
        }
    )
    assert (
        client.post(
            "/api/agent/jobs/" + job["jobId"] + "/result", json=body
        ).status_code
        == 409
    )
    body["payload"]["indexId"] = index["id"]
    body["payload"]["vector"] = [1] * 16
    assert (
        client.post(
            "/api/agent/jobs/" + job["jobId"] + "/result", json=body
        ).status_code
        == 422
    )
    body["payload"]["vector"] = axis(0)
    accepted = client.post("/api/agent/jobs/" + job["jobId"] + "/result", json=body)
    assert accepted.status_code == 200, accepted.text
    assert (
        client.post("/api/agent/jobs/" + job["jobId"] + "/result", json=body).json()
        == accepted.json()
    )
    sample = client.post(
        "/api/comparison/samples",
        json={
            "members": [
                {
                    "contentId": claim["sha256"],
                    "category": "people",
                    "eventGroup": "held-out-family",
                    "split": "test",
                }
            ]
        },
    )
    assert sample.status_code == 200, sample.text
    prompts = client.post(
        "/api/indexes/" + index["id"] + "/prompts",
        json={
            "version": "fixture-v1",
            "indexFingerprint": index["fingerprint"],
            "prompts": [
                {"category": "people", "text": "fixture family", "vector": axis(0)},
                {
                    "category": "screenshots",
                    "text": "fixture screenshot",
                    "vector": axis(1),
                },
            ],
        },
    )
    assert prompts.status_code == 200, prompts.text
    report = client.post(
        "/api/indexes/" + index["id"] + "/evaluate",
        json={"sampleId": sample.json()["id"], "promptVersion": "fixture-v1"},
    )
    assert report.status_code == 200, report.text
    assert (
        report.json()["macroF1"] == 1
    )  # arithmetic fixture; NOT a real model-quality claim
    promoted = client.post(
        "/api/indexes/" + index["id"] + "/promote",
        json={"evaluationId": report.json()["id"]},
    )
    assert promoted.status_code == 200, promoted.text
    assert promoted.json()["role"] == "primary"
    scored = client.post(
        "/api/pipeline/score",
        json={
            "query": {"root": root},
            "indexId": index["id"],
            "promptVersion": "fixture-v1",
            "evaluationId": report.json()["id"],
            "highMargin": 0.5,
            "mediumMargin": 0.2,
        },
    )
    assert scored.status_code == 200, scored.text
    assert scored.json()["scored"] == 1 and scored.json()["decisionsChanged"] == 0
    p = client.get("/api/photos/" + claim["sha256"]).json()
    assert p["groupId"] == "people" and p["triage"] != "clutter_candidate"
    assert p["sensitive"] == "unknown" and p["decision"] == "undecided"
    assert (
        client.get(
            "/api/search", params={"root": root, "like": claim["sha256"]}
        ).status_code
        == 200
    )
    with db.read() as c:
        assert (
            c.execute(
                "SELECT COUNT(*) FROM embedding_indexes WHERE role='primary'"
            ).fetchone()[0]
            == 1
        )


def test_comparison_rejects_duplicate_members_and_missing_backfill(prepared_workspace):
    client, _, _, _, _, claim = prepared_workspace
    member = {
        "contentId": claim["sha256"],
        "category": "people",
        "eventGroup": "same-event",
        "split": "train",
    }
    response = client.post(
        "/api/comparison/samples",
        json={"members": [member, {**member, "split": "test"}]},
    )
    assert response.status_code == 422
    index = register(client)
    assert (
        client.post(
            "/api/indexes/" + index["id"] + "/queue",
            json={"contentIds": [claim["sha256"]]},
        ).status_code
        == 409
    )
