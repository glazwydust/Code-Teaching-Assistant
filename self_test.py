"""Self-test for P1's parts. Run:  python self_test.py

Checks:
  A. Every correct solution passes every test (so any correct style is accepted).
  B. Every buggy example really fails (so P2 can trust the runner's verdicts).
  C. The localizer puts the true bug line near the top (reports the rank honestly).
  D. Safety: syntax error, undefined name, infinite loop, crash, print() noise.
"""
import sys
import time

from localize import find_suspicious_lines
from problems import all_problems
from runner import run_code

problems = all_problems()
failures = []


def check(cond, msg):
    if not cond:
        failures.append(msg)
        print("  FAIL:", msg)


print("A. correct solutions pass all tests")
for p in problems:
    assert len(p["solutions"]) == 4, p["id"]
    for i, sol in enumerate(p["solutions"], 1):
        r = run_code(sol, p["id"])
        check(r["status"] == "passed", f"{p['id']} solution {i}: {r['num_passed']}/{r['num_total']} {[t['error'] for t in r['tests'] if t['error']]}")
    print(f"  {p['id']}: 4 solutions ok ({len(p['tests'])} tests)")

print("B. buggy examples fail")
for p in problems:
    for b in p["buggy_examples"]:
        r = run_code(b["code"], p["id"])
        check(r["status"] == "failed", f"{p['id']}/{b['name']} should fail but {r['status']}")
        print(f"  {p['id']}/{b['name']}: {r['num_passed']}/{r['num_total']} tests pass")

print("C. suspicious-line ranking (rank 1 = top; ties share the best rank)")
ranks = []
for p in problems:
    for b in p["buggy_examples"]:
        scored = find_suspicious_lines(b["code"], p["id"])
        by_line = {d["line"]: d["score"] for d in scored}
        best_bug = max((by_line.get(l, 0) for l in b["bug_lines"]), default=0)
        rank = 1 + sum(1 for s in by_line.values() if s > best_bug)  # competition rank
        ties = sum(1 for s in by_line.values() if s == best_bug)
        ranks.append(rank)
        print(f"  {p['id']}/{b['name']}: bug line {b['bug_lines']} score {best_bug} -> rank {rank} (tied with {ties - 1} others)")
print(f"  bug line in top-1: {sum(r == 1 for r in ranks)}/{len(ranks)}   top-3: {sum(r <= 3 for r in ranks)}/{len(ranks)}")

print("D. safety checks")
r = run_code("def sum_list(nums:\n    return 0\n", "sum_list")
check(r["status"] == "syntax_error" and r["syntax_error"]["line"] >= 1, "syntax error not caught")
r = run_code("def sum_list(nums):\n    return totl\n", "sum_list")
check(any("totl" in w["message"] for w in r["warnings"]), "undefined name not flagged")
t0 = time.time()
r = run_code("def sum_list(nums):\n    while True:\n        pass\n", "sum_list")
check(time.time() - t0 < 8, f"infinite loop took {time.time() - t0:.1f}s")
check(r["status"] == "failed" and any("Timed out" in (t["error"] or "") for t in r["tests"]), "timeout not reported")
print(f"  infinite loop stopped after {time.time() - t0:.1f}s")
r = run_code("def sum_list(nums):\n    return nums[99]\n", "sum_list")
check(any("IndexError" in (t["error"] or "") for t in r["tests"]), "crash not reported")
r = run_code("def sum_list(nums):\n    print('hi')\n    return sum(nums)\n", "sum_list")
check(r["status"] == "passed", "print() noise broke the runner")
r = run_code("x = 1\n", "sum_list")
check(any("not defined" in (t["error"] or "") for t in r["tests"]), "missing function not reported")

print()
if failures:
    print(f"{len(failures)} FAILURE(S)")
    sys.exit(1)
print("ALL CHECKS PASSED")
