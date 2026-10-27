"""Run the NUS student programs through our runner and compare with the dataset's labels.

The dataset says which programs are 'correct' (pass every test) and which are 'wrong'. If
our runner is trustworthy it should agree. This is P1's "run all the wrong programs through
the runner" job from the project plan.

Run from the codeEd folder (one command):

    py scripts/validate_nus.py --questions 1 2 3 4 5 --limit 0 --csv nus_report.csv

  --limit N   programs sampled per kind (correct/wrong) per question; 0 = ALL (can take a while)
  --csv FILE  write one row per program (see columns below)
  --loose     compare with == only: 6.0 equals 6 and True equals 1 (a list still never equals a tuple)
  --check-mutation  experiment: also count a program as failing if its function modifies its input
              (for example list.sort() in place). Some NUS "wrong" programs only do that. If, with
              this flag, the correct programs still all agree AND the wrong ones now agree too, the
              dataset was using that rule.
  --data DIR  folder containing question_1 .. question_5 (default refactory_data/data)

CSV columns: question, dataset_label, file, runner_status, failure_kind, passed, total,
agree, first_failure.
failure_kind is one of passes_all | syntax_error | timeout | runtime_error | wrong_answer.
"""
import argparse
import csv
import os
import sys
import threading
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import nus  # noqa: E402
from runner import failure_kind, run_code  # noqa: E402


def first_failure(res):
    bad = next((t for t in res["tests"] if not t["passed"]), None)
    if bad is None:
        return res["syntax_error"]["message"] if res["syntax_error"] else ""
    text = f"input {bad['input']} expected {bad['expected']!r} got {bad['actual']!r}"
    return text + (f" error: {bad['error']}" if bad["error"] else "") + \
        (f" note: {bad['note']}" if bad.get("note") else "")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="refactory_data/data")
    ap.add_argument("--questions", type=int, nargs="+", default=[1, 2, 3, 4, 5])
    ap.add_argument("--limit", type=int, default=40)
    ap.add_argument("--workers", type=int, default=4)   # fewer parallel runs = fewer false timeouts
    ap.add_argument("--csv", default=None)
    ap.add_argument("--loose", action="store_true")
    ap.add_argument("--check-mutation", action="store_true",
                    help="also fail a program whose function changes its input (experiment, see docstring)")
    args = ap.parse_args()

    rows = []
    for q in args.questions:
        try:
            problem = nus.load_question(args.data, q)
        except (ValueError, OSError) as exc:
            print(f"question_{q}: skipped ({exc})\n")
            continue
        if args.loose:
            problem["strict_types"] = False
        if args.check_mutation:
            problem["check_mutation"] = True
        ref = run_code(problem["solutions"][0], problem)
        print(f"question_{q}: {', '.join(problem['functions'])}; {len(problem['tests'])} tests "
              f"({problem['skipped_tests']} skipped); reference passes "
              f"{ref['num_passed']}/{ref['num_total']}", flush=True)

        for kind in ("correct", "wrong"):
            paths = nus.list_programs(problem, kind, limit=args.limit or None)
            lock, done = threading.Lock(), [0]

            def check(path):
                res = run_code(nus.program_code(path), problem)
                fk = failure_kind(res)
                agree = (fk == "passes_all") if kind == "correct" else (fk != "passes_all")
                with lock:
                    done[0] += 1
                    if done[0] % 100 == 0:
                        print(f"    {kind}: {done[0]}/{len(paths)} done", flush=True)
                return {"question": q, "dataset_label": kind, "file": os.path.basename(path),
                        "runner_status": res["status"], "failure_kind": fk,
                        "passed": res["num_passed"], "total": res["num_total"],
                        "agree": agree, "first_failure": first_failure(res)}

            with ThreadPoolExecutor(args.workers) as pool:
                results = list(pool.map(check, paths))
            rows += results
            agree = sum(r["agree"] for r in results)
            print(f"  {kind:7s}: runner agrees with the dataset on {agree}/{len(results)}")
            if kind == "wrong":
                print("           how the wrong programs fail:", dict(Counter(r["failure_kind"] for r in results)))
            for r in [r for r in results if not r["agree"]][:3]:
                print(f"      disagreement: {r['file']} -> {r['failure_kind']}: {r['first_failure']}")
        print(flush=True)

    if args.csv and rows:
        with open(args.csv, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            w.writeheader()
            w.writerows(rows)
        print(f"Wrote {len(rows)} rows to {args.csv}")


if __name__ == "__main__":
    main()
