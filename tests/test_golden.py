"""The golden dataset is reproducible, self-consistent, and the full pipeline passes offline."""

import json
import types

import pytest

from experiment import golden
from experiment.run import run_one
from experiment.workloads import extract_json


@pytest.fixture(scope="module")
def dataset():
    return golden.load()


def test_dataset_matches_generators(dataset):
    assert dataset == golden.build(), "golden/v1.json is stale: run `python -m experiment.golden build`"
    ids = [c["id"] for c in dataset["cases"]]
    assert len(ids) == len(set(ids)) == 6
    for case in dataset["cases"]:
        golden.task_for(case)  # sandbox hash + questions + expected answers all match


def test_expected_answers_grade_perfectly_and_wrong_ones_do_not(dataset):
    for case in dataset["cases"]:
        task = golden.task_for(case)
        for turn in task.turns:
            text = "Answer:\n```json\n" + json.dumps(turn.expected) + "\n```"
            assert turn.grade(extract_json(text), turn.expected)[0] == 1.0, (case["id"], turn.kind)
            wrong = {k: ("nope" if isinstance(v, str) else -1 if isinstance(v, int) else ["nope"]) for k, v in turn.expected.items()}
            assert turn.grade(wrong, turn.expected)[0] < 1.0, (case["id"], turn.kind)


def test_offline_pipeline_on_golden_case(tmp_path):
    from experiment.agents import make_llm

    _, [(case, task)] = golden.load_cases(cases := golden.DEFAULT_PATH, "audit-s37-medium")
    args = types.SimpleNamespace(window=6000, baseline_window=0, verbose=False, tpm=0, max_daily_wait=0,
                                 max_steps=40, fake=True)
    compact = run_one(case["id"], task, task.turns, "compact", make_llm("fake", task=task), args, tmp_path)
    baseline = run_one(case["id"], task, task.turns, "baseline", make_llm("fake", task=task), args, tmp_path)
    assert compact["status"] == "ok" and compact["mean_score"] == 1.0
    assert compact["input_tokens_peak"] <= 6000 and compact["compaction"]["folds_model"] >= 1
    assert baseline["status"] == "request_too_large"  # the simulated 8K free-tier wall
