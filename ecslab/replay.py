"""Turn the learner's call log into a replay. Rules only, no model."""
from __future__ import annotations


def clock(seconds: float) -> str:
    seconds = max(0, int(seconds))
    return f"{seconds // 60:02d}:{seconds % 60:02d}"


def duration(seconds: float) -> str:
    seconds = max(0, int(round(seconds)))
    m, s = divmod(seconds, 60)
    if m and s:
        return f"{m}m {s:02d}s"
    return f"{m}m" if m else f"{s}s"


def minutes(seconds: float) -> str:
    m = round(seconds / 60)
    return f"{m} minute{'s' if m != 1 else ''}" if m >= 1 else f"{int(seconds)} seconds"


def _matches(call: dict, spec: dict) -> bool:
    names = [spec.get("action")] + list(spec.get("alternatives", []))
    if call.get("action") not in names:
        return False
    return "detail" not in spec or call.get("detail") == spec["detail"]


def groups(calls: list[dict]) -> list[dict]:
    """Collapse consecutive calls to the same action into one row."""
    out = []
    for c in calls:
        key = (c.get("action"), c.get("detail"))
        if out and out[-1]["key"] == key:
            out[-1]["count"] += 1
            out[-1]["last"] = c["ts"]
        else:
            out.append({"key": key, "action": c.get("action"), "detail": c.get("detail"),
                        "first": c["ts"], "last": c["ts"], "count": 1, "clicked": c.get("source") == "console"})
    for i, g in enumerate(out):
        end = out[i + 1]["first"] if i + 1 < len(out) else g["last"]
        g["spent"] = end - g["first"]
    return out


def analyse(calls: list[dict], rules: dict, started_at: float, solved_at: float) -> dict:
    """The replay as data: one row per step of the learner's path, plus the insights."""
    rules = rules or {}
    calls = sorted((c for c in calls if c.get("action") and started_at <= c["ts"] <= solved_at + 1),
                   key=lambda c: c["ts"])
    out = {"solved_in": duration(solved_at - started_at), "seconds": round(solved_at - started_at),
           "calls": len(calls), "rows": [], "insights": []}
    if not calls:
        return out

    notes = dict(rules.get("notes") or {})
    key, answer = rules.get("key_call"), rules.get("answer_call")
    used = set()
    for g in groups(calls):
        extra, kind = [], ""
        if g["count"] > 1:
            spent = f", {duration(g['spent'])} spent here" if g["spent"] >= 1 else ""
            extra.append(f"x {g['count']} calls{spent}")
        probe = {"action": g["action"], "detail": g["detail"]}
        for name, spec in (("key", key), ("answer", answer)):
            if spec and name not in used and _matches(probe, spec):
                extra.append(spec["note"])
                used.add(name)
                kind = name
        if g["action"] in notes and g["action"] not in used:
            # The first-move note is only earned if it really was the first call.
            if g["action"] != rules.get("first_move") or calls[0]["action"] == g["action"]:
                extra.append(notes[g["action"]])
            used.add(g["action"])
        out["rows"].append({"at": clock(g["first"] - started_at),
                            "label": g["action"] + (f"  ({g['detail']})" if g["detail"] else ""),
                            "notes": extra, "kind": kind, "count": g["count"], "spent": round(g["spent"]),
                            "clicked": g["clicked"]})

    insights = out["insights"]
    t_key = next((c["ts"] for c in calls if key and _matches(c, key)), None)
    horizon = t_key if t_key is not None else solved_at
    before = groups([c for c in calls if c["ts"] <= horizon])
    for sink in rules.get("time_sinks") or []:
        mine = [g for g in before if g["action"] == sink["action"]]
        spent, count = sum(g["spent"] for g in mine), sum(g["count"] for g in mine)
        if "min_seconds" in sink and spent >= sink["min_seconds"]:
            insights.append(sink["message"].format(minutes=minutes(spent), count=count).rstrip())
        elif "min_count" in sink:
            total = sum(1 for c in calls if c.get("action") == sink["action"])
            if total >= sink["min_count"]:
                insights.append(sink["message"].format(minutes=minutes(spent), count=total).rstrip())
    if key and t_key is None and rules.get("missed_key_call"):
        insights.append(rules["missed_key_call"].rstrip())
    elif key and t_key is not None and not insights:
        # Clock starts at the deploy that broke things, if there was one.
        deploys = [c["ts"] for c in calls if c.get("action") == "UpdateService" and c["ts"] < t_key]
        took = t_key - (deploys[0] if deploys else started_at)
        since = "after deploying" if deploys else "in"
        what = key.get("describe", "reading a stopped task")
        if took <= rules.get("efficient_within_seconds", 180):
            insights.append(f"You were {what} {duration(took)} {since}. That is the efficient path.")
        else:
            reach = key.get("reach", "reach a stopped task")
            insights.append(f"It took {duration(took)} {since} to {reach}. That is where the "
                            f"failure is named; get there first next time.")
            if rules.get("efficient_path"):
                insights.append("Efficient path: " + " -> ".join(rules["efficient_path"]))
    return out


def build(calls: list[dict], rules: dict, started_at: float, solved_at: float) -> str:
    a = analyse(calls, rules, started_at, solved_at)
    lines = [f"Solved in {a['solved_in']}.", ""]
    if not a["rows"]:
        return "\n".join(lines + ["No AWS calls were recorded, so there is no replay.",
                                  "Point the AWS CLI at the lab proxy next time (AWS_ENDPOINT_URL)."])
    lines.append("Your path:")
    for r in a["rows"]:
        lines.append(f"  {r['at']}  {r['label']:<28}{'  '.join(r['notes'])}".rstrip())
    return "\n".join(lines + [""] + "\n\n".join(a["insights"]).splitlines())
