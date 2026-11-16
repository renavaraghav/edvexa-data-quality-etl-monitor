"""Tests of the gate only. Synthetic material cannot approve BHTT itself."""

import hashlib
import json
from types import SimpleNamespace

import pytest

from scripts.verify_bhtt_inventory import BHTT_POLICY, verify_evidence


def _digest(value):
    return hashlib.sha256(value).hexdigest()


def _synthetic_packet():
    source_hash = _digest(b"synthetic source only")
    questions = [SimpleNamespace(question_number=f"Q{i}", source_line=i, source_text=f"Synthetic question {i}",
                                 question_type="single_choice") for i in range(1, 63)]
    inventory = {"source_sha256": source_hash, "questions": [
        dict(id=q.question_number, source_line=q.source_line, source_text_sha256=_digest(q.source_text.encode()),
             classification="substantive", question_type=q.question_type, evidence_reference="Synthetic benchmark")
        for q in questions
    ]}
    policy = {"study": "BHTT-W3B", "status": "approved", "required_substantive_questions": 62, "source_sha256": source_hash,
              "approval": {"reviewer": "Synthetic reviewer", "approved_on": "2026-01-01", "review_record": "https://example.test/review"}}
    return source_hash, questions, inventory, policy


def _seal(inventory, policy):
    content = json.dumps(inventory).encode()
    policy["approved_inventory_sha256"] = _digest(content)
    return content


def test_arbitrary_inventory_cannot_override_current_blocked_approval():
    source, questions, inventory, fake = _synthetic_packet()
    with pytest.raises(ValueError, match="has not been approved|Source questionnaire hash"):
        verify_evidence(source, questions, _seal(inventory, fake), json.loads(BHTT_POLICY.read_bytes()))


@pytest.mark.parametrize("invalid", ["missing", "duplicate", "incomplete", "approval", "source", "hash", "type"])
def test_release_check_rejects_untrusted_or_incomplete_evidence(invalid):
    source, questions, inventory, policy = _synthetic_packet()
    # Exercise the verification algorithm independently of the blocked real policy.
    verify_evidence(source, questions, _seal(inventory, policy), policy)
    if invalid == "duplicate":
        questions[1].question_number = inventory["questions"][1]["id"] = "Q1"
    elif invalid == "incomplete":
        questions = questions[:59]
        inventory["questions"] = inventory["questions"][:59]
    elif invalid == "approval":
        policy["approval"] = None
    elif invalid == "source":
        inventory["source_sha256"] = "0" * 64
    elif invalid == "type":
        inventory["questions"][1]["question_type"] = "metadata"
    content = _seal(inventory, policy)
    if invalid == "missing":
        content = None
    elif invalid == "hash":
        content += b"\n"
    with pytest.raises(ValueError):
        verify_evidence(source, questions, content, policy)
