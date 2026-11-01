from server.db import revision
from server.queries import BASE, detail, validate_scope, where


def list_sets(c, query):
    validate_scope(c, query)
    condition, params = where(query)
    sets = (
        BASE
        + """, candidate_sets AS (
        SELECT 'exact-'||content_id AS id,'exact' AS kind,content_id AS anchor,group_id,NULL AS duplicate_set
        FROM base b WHERE """
        + condition
        + """ AND copies>1
        UNION ALL
        SELECT duplicate_set||':'||group_id AS id,'possible' AS kind,MIN(content_id) AS anchor,group_id,duplicate_set
        FROM base b WHERE """
        + condition
        + """ AND duplicate_set IS NOT NULL
        GROUP BY duplicate_set,group_id HAVING COUNT(*)>1
    ) """
    )
    total = c.execute(
        sets + "SELECT COUNT(*) FROM candidate_sets", [*params, *params]
    ).fetchone()[0]
    rows = c.execute(
        sets + "SELECT * FROM candidate_sets ORDER BY id LIMIT ? OFFSET ?",
        [*params, *params, query.limit, (query.page - 1) * query.limit],
    ).fetchall()
    items = []
    for row in rows:
        if row["kind"] == "exact":
            p = detail(c, row["anchor"])
            paths = c.execute(
                "SELECT r.path,f.path FROM file_instances f JOIN roots r ON r.id=f.root_id WHERE content_id=? AND state='available' ORDER BY r.id,f.path",
                (row["anchor"],),
            ).fetchall()
            items.append(
                {
                    "id": row["id"],
                    "kind": "exact",
                    "contentIds": [row["anchor"]],
                    "paths": p["paths"],
                    "extraCopies": len(paths) - 1,
                    "contentDecisionChanged": False,
                    "extrasPreselected": True,
                    "reason": "Identical SHA-256; one content, multiple paths",
                }
            )
        else:
            ids = [
                r[0]
                for r in c.execute(
                    BASE
                    + "SELECT content_id FROM base b WHERE "
                    + condition
                    + " AND duplicate_set=? AND group_id=? ORDER BY content_id",
                    [*params, row["duplicate_set"], row["group_id"]],
                )
            ]
            items.append(
                {
                    "id": row["id"],
                    "kind": "burst"
                    if row["duplicate_set"].startswith("burst-")
                    else "possible",
                    "contentIds": ids,
                    "preselected": False,
                }
            )
    return {"items": items, "total": total, "page": query.page, "revision": revision(c)}
