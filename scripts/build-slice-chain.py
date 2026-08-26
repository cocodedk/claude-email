#!/usr/bin/env python3
"""Generate an agent-loop chain from a machine-readable slice plan.

Reads .agent-loops/<name>/state/slices.json and emits chain.json plus one stage
folder per loop-eligible slice, in dependency order, via the skill's
scaffold-loop.sh. Linear stages run in the foreground under `set -e`, so the chain
HALTS on the first slice that fails instead of running later slices against a
broken base — which is what a dependency-ordered security campaign needs and what
fan-out cannot give (it backgrounds its children and only reports at the join).

Gates are frozen 0444 and their hashes recorded in state/gate-hashes.txt, which
each verify.sh checks before doing anything else.
"""
import argparse
import hashlib
import json
import os
import subprocess
import sys

SKILL = os.path.expanduser("~/.claude-work/skills/agent-loop")
SIGNOFF = "99-signoff"


def stage_id(slice_):
    return f"{slice_['order']:02d}-{slice_['id']}"


def check_order(eligible, all_ids):
    """Every prereq must be satisfied before the slice that needs it."""
    problems = []
    seen = set()
    for s in eligible:
        for p in s.get("prereqs", []):
            if p in seen:
                continue
            if p not in all_ids:
                problems.append(f"{s['id']}: unknown prereq {p!r}")
            elif p in {e["id"] for e in eligible}:
                problems.append(f"{s['id']}: prereq {p!r} is ordered AFTER it")
        seen.add(s["id"])
    return problems


def verify_refs(plan, repo):  # noqa: C901
    """A line number is a fragile pointer into a document that will be edited. Each slice
    carries an `anchor` phrase that MUST appear inside its cited range — so a stale ref
    fails the build loudly instead of silently handing a builder the wrong brief."""
    doc = open(os.path.join(plan.get("plan_repo", repo), plan["plan_file"])).read().split("\n")
    problems = []
    for s_ in plan["slices"]:
        if not s_.get("eligible"):
            continue
        refs = ([s_["plan_ref"]] if "plan_ref" in s_ else []) + s_.get("also_read", [])
        if not refs and not (s_.get("source") or plan.get("origin")):
            problems.append(f"{s_['id']}: no plan_ref and no `source` explaining where it came from")
        for ref in refs:
            anchor = ref.get("anchor")
            if not anchor:
                problems.append(f"{s_['id']}: ref has no anchor")
                continue
            lo, _, hi = ref["lines"].partition("-")
            lo, hi = int(lo), int(hi or lo)
            if not any(anchor in l for l in doc[lo - 1:hi]):
                where = [i + 1 for i, l in enumerate(doc) if anchor in l]
                problems.append(
                    f"{s_['id']}: anchor {anchor[:40]!r} not in lines {ref['lines']}"
                    f" (found at {where or 'nowhere'})")
    return problems


def scaffold(dest, template, sets):
    cmd = [f"{SKILL}/scripts/scaffold-loop.sh", "--template", template, "--dest", dest]
    for k, v in sets.items():
        assert "\n" not in v, f"{k} must be single-line (sed substitution): {v[:60]!r}"
        cmd += ["--set", f"{k}={v}"]
    subprocess.run(cmd, check=True, capture_output=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("workspace")
    ap.add_argument("--max-cost", default="",
                    help="USD ceiling per slice. Empty (default) = no ceiling: on a flat-rate "
                         "subscription the reported total_cost_usd is an API-price equivalent, "
                         "not a charge, so iteration caps (--max/--stall) are the real rails.")
    ap.add_argument("--only", default="", help="comma-separated slice ids (smoke test)")
    args = ap.parse_args()

    ws = os.path.abspath(args.workspace)
    plan = json.load(open(f"{ws}/state/slices.json"))
    repo = plan["repo"]
    all_ids = {s["id"] for s in plan["slices"]}

    rubric_extra = " ".join(
        f"{i}. {r}" for i, r in enumerate(plan.get("rubric_additions", []), 1)
    ) or "(none beyond the standard rules above)"

    docs_paths = plan.get("docs_paths", [
        "README.md", "CLAUDE.md", "website/index.html", "website/fa/index.html",
    ])

    eligible = sorted(
        (s for s in plan["slices"] if s.get("eligible")), key=lambda s: s["order"]
    )
    if args.only:
        want = {x.strip() for x in args.only.split(",")}
        eligible = [s for s in eligible if s["id"] in want]
        missing = want - {s["id"] for s in eligible}
        if missing:
            sys.exit(f"--only names unknown or non-eligible slices: {sorted(missing)}")

    problems = verify_refs(plan, repo) + check_order(eligible, all_ids)
    # A slice flagged blocks_campaign must be first in line after the human preflight — it
    # exists because something already broke, and running the rest before it repeats that.
    blockers = [s_["id"] for s_ in eligible if s_.get("blocks_campaign")]
    if blockers and not args.only and eligible[0]["id"] != blockers[0]:
        problems.append(
            f"{blockers[0]} is flagged blocks_campaign but is not the first stage")
    if problems:
        sys.exit("plan refs / dependency order are wrong:\n  " + "\n  ".join(problems))

    stages = [{"id": "00-preflight", "next": None, "fanout": None}]
    ids = ["00-preflight"] + [stage_id(s) for s in eligible] + [SIGNOFF]
    for i, sid in enumerate(ids[:-1]):
        if i == 0:
            stages[0]["next"] = ids[1]
        else:
            stages.append({"id": sid, "next": ids[i + 1], "fanout": None})
    stages.append({"id": SIGNOFF, "next": None, "fanout": None})

    # human gates: preflight (someone must land the in-flight tree) and sign-off
    for sid, why in ((("00-preflight"), "land or park the in-flight working tree; baseline green"),
                     (SIGNOFF, "review every slice branch before merge")):
        os.makedirs(f"{ws}/{sid}", exist_ok=True)
        json.dump(
            {"goal_file": "prompt.md", "gate": {"type": "human"}, "inputs": [],
             "outputs": [], "engine": {}, "next": None},
            open(f"{ws}/{sid}/loop.json", "w"), indent=2)
        open(f"{ws}/{sid}/prompt.md", "w").write(f"# {sid}\n\nHuman gate: {why}.\n")

    for i, s in enumerate(eligible):
        sid = stage_id(s)
        nxt = ids[ids.index(sid) + 1]
        ref = s.get("plan_ref") or {
            "section": "(not from the plan)", "lines": "-",
        }
        scaffold(f"{ws}/{sid}", "implement-slice", {
            "SLICE_ID": s["id"],
            "TITLE": s["title"],
            "PHASE": s.get("phase", "-"),
            "PLAN_FILE": plan["plan_file"] if s.get("plan_ref") else "(none)",
            "BRIEF": s.get("source") or plan.get("origin")
                     or "your section of the plan, cited above",
            "SECTION": ref["section"],
            "PLAN_LINES": ref["lines"],
            "REPO": s.get("repo_override", repo),
            "TEST_TARGET": s.get("test_target", ""),
            "ACCEPTANCE": s["acceptance"],
            # The prompt REQUIRES updating the docs invariant, so those paths must be inside
            # the declared scope or the judge rejects correct work for straying. Same for the
            # slice's own completion note.
            "SCOPE_FILES": ", ".join(s["scope_files"] + docs_paths),
            "NOTES": s.get("notes", "(none)"),
            "RUBRIC_ADDITIONS": rubric_extra,
            "ALSO_READ": "; ".join(
                f"{r['anchor']} (lines {r['lines']})" for r in s.get("also_read", [])
            ) or "(nothing beyond your own section)",
            "NEXT": nxt,
            "MAX_COST": args.max_cost,
        })

    json.dump({"objective": plan["objective"],
               "slug": os.path.basename(ws),
               "stages": stages},
              open(f"{ws}/chain.json", "w"), indent=2)

    os.makedirs(f"{ws}/state/slices", exist_ok=True)

    # The repo-specific half of the gate. One file per workspace, hash-frozen with the rest,
    # so the template itself stays repo-agnostic (pytest here, gradle in the app repo).
    gate_cmds = plan.get("gates") or [
        "scripts/check-line-limit.sh",
        ".venv/bin/pytest tests/ -q",
        '.venv/bin/pytest "$1" -q',
    ]
    gpath = f"{ws}/state/gate-cmds.sh"
    if os.path.exists(gpath):
        os.chmod(gpath, 0o644)
    with open(gpath, "w") as fh:
        fh.write(
            "#!/usr/bin/env bash\n"
            "# Repo gate. $1 = this slice's test target. Frozen + hash-checked.\n"
            "set -eu\n"
            "\n"
            "# A worktree pre-commit hook exports an ABSOLUTE GIT_DIR pointing into the real\n"
            "# repository (.git/worktrees/<name>), and GIT_DIR overrides cwd — so a test that\n"
            "# shells out to git against a tmp_path repo gets silently redirected onto the real\n"
            "# one, rewriting its HEAD, index and refs. In a normal checkout GIT_DIR is unset,\n"
            "# so this hazard is introduced by running the loop in a worktree. Scrub it here,\n"
            "# at the gate, rather than depending on any individual test remembering to.\n"
            "unset GIT_DIR GIT_WORK_TREE GIT_INDEX_FILE GIT_COMMON_DIR \\\n"
            "      GIT_OBJECT_DIRECTORY GIT_ALTERNATE_OBJECT_DIRECTORIES GIT_PREFIX\n"
            "\n"
        )
        fh.write("\n".join(gate_cmds) + "\n")

    # Freeze the gates and record their hashes. verify.sh checks this file first, so a
    # loop that edits its own gate fails instead of passing.
    lines = [f"{hashlib.sha256(open(gpath,'rb').read()).hexdigest()}  {gpath}"]
    for s in eligible:
        for f in ("verify.sh", "rubric.md"):
            path = f"{ws}/{stage_id(s)}/{f}"
            lines.append(f"{hashlib.sha256(open(path,'rb').read()).hexdigest()}  {path}")
            os.chmod(path, 0o444)
    os.chmod(gpath, 0o444)
    open(f"{ws}/state/gate-hashes.txt", "w").write("\n".join(lines) + "\n")
    os.chmod(f"{ws}/state/gate-hashes.txt", 0o444)

    print(f"chain: {len(eligible)} slice stages + 2 human gates -> {ws}/chain.json")
    for s in eligible:
        sec = (s.get('plan_ref') or {}).get('section', '(off-plan)')
        print(f"  {stage_id(s):34s} {sec:14s} {s['title'][:58]}")
    print(f"\nrun: bash {SKILL}/scripts/run-chain.sh {ws} --approve")


if __name__ == "__main__":
    main()
