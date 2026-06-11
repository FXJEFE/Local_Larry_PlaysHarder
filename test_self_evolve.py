"""
test_self_evolve.py - exercise the self_evolve skill.

  python test_self_evolve.py            # fast checks only (no LLM)
  python test_self_evolve.py --llm [model]   # also test create_skill + heal_skill

Fast checks: discovery, tasks, memory, copy_skill, version naming, isolated
validation (good + bad source). LLM checks: generate a new skill and run it;
break a skill and heal it into a new working version.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from skills import discover_skills, run_skill          # noqa: E402
from skills import self_evolve as se                    # noqa: E402

SKILLS_DIR = se.SKILLS_DIR
created_files = []
passed, failed = 0, 0


def check(label, cond, extra=""):
    global passed, failed
    mark = "PASS" if cond else "FAIL"
    if cond:
        passed += 1
    else:
        failed += 1
    print(f"  [{mark}] {label}" + (f" -> {extra}" if extra else ""))


def fast_checks():
    print("== discovery ==")
    skills = discover_skills()
    check("self_evolve discovered", "self_evolve" in skills,
          skills.get("self_evolve", {}).get("category", ""))

    print("== tasks ==")
    r = se.run("add_task", title="self-evolve smoke task", priority=1)
    tid = r.get("task_id")
    check("add_task", r.get("success") and tid is not None, f"id={tid}")
    lst = se.run("list_tasks")
    check("list_tasks contains it", any(t["id"] == tid for t in lst["tasks"]))
    c = se.run("complete_task", task_id=tid, result="done")
    check("complete_task", c.get("status") == "completed")

    print("== memory ==")
    rem = se.run("remember", text="Larry keepalive lives in ~/larry-keepalive.sh",
                 metadata={"kind": "test"})
    check("remember", rem.get("success"), rem.get("id", rem.get("error", "")))
    rec = se.run("recall", query="where does the keepalive script live", n=3)
    hit = isinstance(rec.get("results"), list) and len(rec["results"]) > 0
    check("recall returns results", hit,
          (rec["results"][0]["text"][:50] if hit else rec.get("error", "")))

    print("== validation (isolated subprocess) ==")
    good = ("description='x'\ncategory='t'\nparameters={}\n"
            "def run(**k):\n    return {'ok': True}\n")
    bad = "def run(:\n  pass\n"           # syntax error
    noru = "description='x'\nx = 1\n"      # no run()
    check("good source validates", se._validate_source(good)[0])
    check("syntax error rejected", not se._validate_source(bad)[0])
    check("missing run() rejected", not se._validate_source(noru)[0])

    print("== copy_skill + versioning ==")
    cp = se.run("copy_skill", name="example_local_tool")
    ok = cp.get("success") and cp["to"].startswith("example_local_tool")
    check("copy_skill made a version", ok, cp.get("to", cp.get("error", "")))
    if ok:
        created_files.append(SKILLS_DIR / cp["to"])
    base, ver, path = se._next_version_path("example_local_tool")
    check("next version increments", ver >= 2, f"next={path.name}")


def llm_checks(model):
    print(f"\n== create_skill (model={model or 'default'}) ==")
    r = se.run("create_skill", name="reverse_text",
               spec="reverse the characters of an input string `text` and return "
                    "{'reversed': <reversed string>}",
               model=model)
    print("   create_skill ->", r)
    ok = r.get("success")
    check("create_skill generated+validated", ok, r.get("detail", r.get("error", "")))
    if ok:
        created_files.append(SKILLS_DIR / f"{r['skill']}.py")
        run_res = run_skill("reverse_text", text="larry")
        print("   run reverse_text('larry') ->", run_res)
        check("generated skill runs", run_res.get("success"))

    print(f"\n== heal_skill (model={model or 'default'}) ==")
    broken = SKILLS_DIR / "broken_demo.py"
    broken.write_text(
        "description='adds two numbers'\ncategory='math'\n"
        "parameters={'a':{'type':'number'},'b':{'type':'number'}}\n"
        "def run(a=0,b=0,**k):\n"
        "    return {'sum': a + b + undefined_variable}\n",  # NameError at call
        encoding="utf-8")
    created_files.append(broken)
    err = run_skill("broken_demo", a=2, b=3)
    print("   broken run ->", err)
    h = se.run("heal_skill", name="broken_demo",
               error=str(err.get("error", "NameError: undefined_variable")),
               model=model)
    print("   heal_skill ->", h)
    ok = h.get("success")
    check("heal produced a new version (original preserved)",
          ok and broken.exists(), h.get("new_version", h.get("error", "")))
    if ok:
        newmod = h["skill"]
        created_files.append(SKILLS_DIR / f"{newmod}.py")
        healed = run_skill(newmod, a=2, b=3)
        print(f"   run {newmod}(2,3) ->", healed)
        check("healed version runs", healed.get("success"))


def cleanup():
    print("\n== cleanup (removing test artifacts only) ==")
    for f in created_files:
        try:
            f.unlink(missing_ok=True)
            print(f"   removed {f.name}")
        except Exception as e:
            print(f"   could not remove {f.name}: {e}")


def main():
    do_llm = "--llm" in sys.argv
    model = None
    if do_llm:
        i = sys.argv.index("--llm")
        if i + 1 < len(sys.argv):
            model = sys.argv[i + 1]
    try:
        fast_checks()
        if do_llm:
            llm_checks(model)
    finally:
        cleanup()
    print(f"\n=== {passed} passed, {failed} failed ===")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
