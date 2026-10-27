# codeEd

An AI teaching assistant for beginner Python students. Paste your code and it runs it
against test cases, points to the exact line that is going wrong, names the type of mistake
(off-by-one, wrong operator, missing base case, and so on), and gives a guiding hint instead
of the solution. It works even when the code has several bugs, and says "not sure" when it
can't tell.

This folder is **P1: code runner and detective**. Everyone else builds on it.

## Layout

```
problems/        5 problems: statement, tricky tests, 4 correct solutions, 2 buggy examples each
runner.py        run_code(code, problem_id)            -> test results
localize.py      find_suspicious_lines(code, problem_id) -> ranked suspicious lines
_worker.py       runs one test in a separate process (used by runner.py)
self_test.py     python self_test.py   (checks everything; takes about 20 s)
scripts/get_mbpp.py   optional: download MBPP from Hugging Face
```

## The contract (agree with P2, P3, P4)

```python
from runner import run_code
from localize import find_suspicious_lines

run_code(code, "sum_list")
# {
#   "status": "passed" | "failed" | "syntax_error",
#   "syntax_error": None | {"line": 3, "message": "..."},
#   "warnings": [{"line": 4, "message": "undefined name 'totl'"}],
#   "num_passed": 2, "num_total": 7,
#   "tests": [{"input": [[1, 2, 3]], "expected": 6, "actual": 3,
#              "passed": False, "error": None}]      # + "lines": [...] when trace=True
# }

find_suspicious_lines(code, "sum_list")
# [{"line": 3, "score": 0.8452}, {"line": 2, "score": 0.8452}, ...]   highest score first
```

- Problem ids: `sum_list`, `factorial`, `max_in_list`, `count_evens`, `is_palindrome`.
- Bug types (`problems.BUG_TYPES`): `off_by_one`, `wrong_operator`, `missing_base_case`,
  `wrong_init`, `missing_return`.
- `problems.get_problem(id)` gives P2 the tests, the 4 correct solutions (`solutions`) to
  break, and seed buggy programs (`buggy_examples`, with the true bug line).
- A test passes only if the value AND the type match, so `1` does not pass for `True` and
  `None` does not pass for `False`.

## Safety limits

Each test runs in its own Python process (`-I` isolated mode, 512 MB memory cap on Linux).
A test gets 2 seconds (including starting Python). Only tests that hit that limit count
against a 4-second budget; once it is used up the remaining tests are skipped, so a program that
hangs on everything is stopped after about 4 seconds, while a slow start-up is never a timeout. Infinite loops and crashes are
reported as a test error and never freeze the site. This is a project-level sandbox, not a
hardened one: do not run untrusted code from the public internet without a container.

## Known limits (good for the report)

- Fault localization is a ranking, not a verdict. In `self_test.py` the true bug line is
  ranked first (ties count) in 7 of 10 examples and within the top 3 in 10 of 10.
- Bugs of omission (a missing `return` or base case) have no line that is "wrong", so they
  rank lower. P3's repair search and P2's classifier help confirm the cause.
- Several lines often tie, because they run in every failing test. That is why Check 5 of
  the plan (compare step by step with a correct solution) adds information.
- Undefined-name detection uses `pyflakes` if installed, otherwise a simpler scope-blind check.

## Run

```
python self_test.py
python runner.py sum_list my_attempt.py
```

## Real student programs (NUS / Refactory data)

The project plan uses the Refactory dataset (`data.zip` from github.com/githubhuyang/refactory,
5 NUS assignments; 2,442 correct and 1,783 wrong student programs). Unzip it so that the folders
`question_1` .. `question_5` are in `refactory_data/data` (or set the environment variable
`CODEED_NUS_DATA`). The problems then work by id, exactly like the first 5:

```python
run_code(code, "nus_q3")              # ids: nus_q1 .. nus_q5
find_suspicious_lines(code, "nus_q3")
```

- `nus.py` loads a question: tests (the input files are calls like `search(42, (-5, 1))`), the
  reference solution, the optional `global.py` (run before the student's code, so line numbers
  stay correct), and lists the correct/wrong student programs.
- Question 2 tests three functions, so its tests are `(args, expected, function_name)`.
- Values are compared by value AND type (`6` is not `6.0`, a list is not a tuple). Set
  `problem["strict_types"] = False` to compare with plain `==`.
- `runner.failure_kind(result)` says why a run failed: `passes_all | syntax_error | timeout |
  runtime_error | wrong_answer`.
- Test inputs may use variable names defined in `global.py` (question 2 uses
  `tuple_of_possible_birthdays`); `nus.py` looks them up there.
- Some NUS "wrong" programs return the right answer but modify their input (e.g. `lst.sort()` in
  place, question 4). `problem["check_mutation"] = True` (or `validate_nus.py --check-mutation`)
  also fails a program whose function changes its input. Off by default: it is an experiment.
- The data has no bug-type labels, only correct/wrong.
- Cite: Hu, Ahmed, Mechtaev, Leong, Roychoudhury, "Re-factoring based Program Repair applied to
  Programming Assignments", ASE 2019.

Scripts (run from the codeEd folder, one command at a time):

```
py scripts/validate_nus.py --questions 1 2 3 4 5 --limit 0 --csv nus_report.csv
py scripts/make_label_sheet.py --per-question 20
py scripts/check_leakage.py --max-wrong 300
```

- `validate_nus.py`: runs the programs through our runner and checks it agrees with the dataset's
  correct/wrong labels; writes one CSV row per program, with the failure kind.
- `make_label_sheet.py`: random sample of real wrong programs (code, first failing test, reference
  solution) plus a CSV for two people to label by bug type.
- `check_leakage.py`: how close the real wrong programs are to the correct ones that P2 will break
  for training (near-twins make the test too easy).
