"""Download the Singapore (NUS / Refactory) student submissions from Hugging Face.

Run from the codeEd folder (one command, nothing else on the line):

    py scripts/get_singapore.py

What it does:
  1. Lists the files in the Hugging Face dataset repo 'koutch/intro_prog' and picks the
     ones with 'singapore' in the name.  (We download the raw .jsonl files directly,
     because newer versions of the `datasets` library refuse dataset loading scripts.)
  2. Copies them into the data/ folder.
  3. Prints a summary: how many correct vs buggy submissions, per assignment, plus one example.

Fields in singapore_data_train.jsonl: submission_id, func_code (the student's code),
assignment_id, func_name, description, test, correct (True/False).
singapore_metadata_train.jsonl has the reference solution and tests per assignment.
"""
import json
import os
import shutil
from collections import Counter

from huggingface_hub import hf_hub_download, list_repo_files

REPO = "koutch/intro_prog"
os.makedirs("data", exist_ok=True)

files = [f for f in list_repo_files(REPO, repo_type="dataset") if "singapore" in f.lower()]
print("Singapore files found in the repo:", files)
if not files:
    raise SystemExit("No Singapore files found. Open https://huggingface.co/datasets/" + REPO +
                     " and check the 'Files and versions' tab.")

saved = []
for name in files:
    cached = hf_hub_download(REPO, name, repo_type="dataset")
    target = os.path.join("data", os.path.basename(name))
    shutil.copyfile(cached, target)
    saved.append(target)
    print("saved", target)


def read_jsonl(path):
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


data_file = next((p for p in saved if p.endswith("singapore_data_train.jsonl")), None)
if data_file is None:
    print("\nCould not find singapore_data_train.jsonl; look in the data/ folder to see what was saved.")
else:
    rows = read_jsonl(data_file)
    print(f"\n{len(rows)} submissions in {data_file}")
    print("fields:", sorted(rows[0].keys()))
    print("correct vs buggy:", Counter("correct" if r.get("correct") else "buggy" for r in rows))
    print("per assignment:", dict(Counter(r.get("assignment_id") for r in rows)))
    example = next((r for r in rows if not r.get("correct")), rows[0])
    print("\n--- one buggy example ---")
    print("assignment:", example.get("assignment_id"), "| function:", example.get("func_name"))
    print(example.get("func_code"))
