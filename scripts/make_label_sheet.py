"""Make a labeling sheet: a random sample of real wrong programs to read and label by bug type.

Run from the codeEd folder (one command):

    py scripts/make_label_sheet.py --per-question 20

Creates the folder labeling/ with
  labeling_sheet.md     for each sampled program: the code, the first failing test, and the
                        question's reference solution (read this file)
  labels_template.csv   one row per program; labeller_1 / labeller_2 are blank for two people to fill

Only programs that run but give wrong answers (or crash) are sampled, because syntax errors
and infinite loops are caught by earlier steps of the pipeline (use --include-all to keep them).
Use the same seed (--seed) so both labellers get the same sample.
"""
import argparse
import csv
import os
import random
import sys
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import nus  # noqa: E402
from problems import BUG_TYPES  # noqa: E402
from runner import failure_kind, run_code  # noqa: E402

LABEL_HELP = """\
# Bug labeling sheet

Read each wrong program, compare it with the reference solution and the failing test, and write the
label(s) in labels_template.csv. If a program has several bugs, write all labels separated by `;`.

| Label | Meaning | Example |
|---|---|---|
| `off_by_one` | A loop or index stops one too early or too late | `range(len(x) - 1)` |
| `wrong_operator` | Wrong comparison or arithmetic operator | `<` instead of `<=` |
| `missing_base_case` | A recursive function has no stopping case | no `if n == 0` |
| `wrong_init` | A variable starts at the wrong value | `total = 1` instead of `0` |
| `missing_return` | The function never returns the answer (or returns the wrong thing) | no final `return` |
| `other` | Does not fit the five (describe it in notes) | wrong logic, wrong data structure, ... |

Label what is *actually wrong*, not what you would have written. Two people should label
independently, then compare (report how often you agree).

"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="refactory_data/data")
    ap.add_argument("--questions", type=int, nargs="+", default=[1, 2, 3, 4, 5])
    ap.add_argument("--per-question", type=int, default=20)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--out", default="labeling")
    ap.add_argument("--include-all", action="store_true")
    ap.add_argument("--workers", type=int, default=8)
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)

    md, csv_rows = [LABEL_HELP], []
    allowed = {"wrong_answer", "runtime_error"} | ({"syntax_error", "timeout"} if args.include_all else set())
    for q in args.questions:
        try:
            problem = nus.load_question(args.data, q)
        except (ValueError, OSError) as exc:
            print(f"question_{q}: skipped ({exc})")
            continue
        candidates = nus.list_programs(problem, "wrong")
        random.Random(args.seed).shuffle(candidates)
        candidates = candidates[: args.per_question * 4]       # look at 4x as many as we need

        def run(path):
            code = nus.program_code(path)
            return path, code, run_code(code, problem)

        with ThreadPoolExecutor(args.workers) as pool:
            runs = list(pool.map(run, candidates))
        chosen = [r for r in runs if failure_kind(r[2]) in allowed][: args.per_question]
        print(f"question_{q}: {len(chosen)} programs sampled")

        md.append(f"\n---\n\n## Question {q}\n\n{problem['description']}\n\n"
                  f"**Reference solution**\n\n```python\n{problem['solutions'][0].rstrip()}\n```\n")
        for path, code, res in chosen:
            name = os.path.basename(path)
            bad = next((t for t in res["tests"] if not t["passed"]), None)
            fail = (f"input `{bad['input']}`, expected `{bad['expected']!r}`, got `{bad['actual']!r}`"
                    + (f", error: {bad['error']}" if bad["error"] else "")) if bad else "(none)"
            md.append(f"\n### q{q} / {name}\n\n```python\n{code.rstrip()}\n```\n\nFirst failing test: {fail}\n")
            csv_rows.append({"question": q, "file": name, "failure_kind": failure_kind(res),
                             "labeller_1": "", "notes": ""})

    with open(os.path.join(args.out, "labeling_sheet.md"), "w", encoding="utf-8") as f:
        f.write("".join(md))
    if csv_rows:
        with open(os.path.join(args.out, "labels_template.csv"), "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(csv_rows[0].keys()))
            w.writeheader()
            w.writerows(csv_rows)
    print(f"Wrote {len(csv_rows)} programs to {args.out}/labeling_sheet.md and labels_template.csv "
          f"(bug types: {', '.join(BUG_TYPES)}, other)")


if __name__ == "__main__":
    main()
