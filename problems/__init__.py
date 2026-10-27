"""Problem registry for codeEd.

Each problem lives in its own module and exposes a PROBLEM dict:
    id            short id used everywhere (e.g. "sum_list")
    function      the function name the student must define
    title         short title
    description   student-facing statement (what the website shows)
    tests         list of (args_list, expected) -- args_list is passed as *args
    solutions     4 correct solutions in different styles
    buggy_examples  hand-made buggy programs: {name, bug_type, code, bug_lines}
                  (seed examples for P2's dataset and for testing the localizer)
"""
import os
from importlib import import_module

PROBLEM_IDS = ["sum_list", "factorial", "max_in_list", "count_evens", "is_palindrome"]

# The 5 NUS (Refactory) problems, loaded from the unzipped dataset (see nus.py).
# Data folder: $CODEED_NUS_DATA, else <project>/refactory_data/data.
NUS_IDS = [f"nus_q{i}" for i in range(1, 6)]


def nus_data_dir():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.environ.get("CODEED_NUS_DATA", os.path.join(root, "refactory_data", "data"))


def available_nus_ids():
    """NUS problem ids whose folders exist in the data directory."""
    return [i for i in NUS_IDS if os.path.isdir(os.path.join(nus_data_dir(), f"question_{i[-1]}"))]

# The 5 bug types from the project plan (agree on these names with P2/P3).
BUG_TYPES = [
    "off_by_one",
    "wrong_operator",
    "missing_base_case",
    "wrong_init",
    "missing_return",
]

_cache = {}


def get_problem(problem_id):
    if problem_id not in PROBLEM_IDS and problem_id not in NUS_IDS:
        raise KeyError(f"Unknown problem '{problem_id}'. Choose from {PROBLEM_IDS + NUS_IDS}")
    if problem_id not in _cache:
        if problem_id in NUS_IDS:
            import nus  # project-root module; imported here to keep the core problems independent
            try:
                _cache[problem_id] = nus.load_question(nus_data_dir(), int(problem_id[-1]))
            except OSError as exc:
                raise FileNotFoundError(
                    f"NUS data not found in '{nus_data_dir()}'. Unzip data.zip into "
                    f"<project>/refactory_data or set CODEED_NUS_DATA. ({exc})") from exc
        else:
            _cache[problem_id] = import_module(f"problems.{problem_id}").PROBLEM
    return _cache[problem_id]


def all_problems():
    return [get_problem(pid) for pid in PROBLEM_IDS]
