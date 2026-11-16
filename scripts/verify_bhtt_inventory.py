"""Inspect a local BHTT questionnaire and verify the approved release inventory.

Usage: python -m scripts.verify_bhtt_inventory QQ.docx [--inventory approved.json]
The caller can supply evidence, but the repository approval record is the trust anchor.
"""
import argparse
import hashlib
import json
import os
import re
import sys
from dataclasses import asdict
from datetime import date
from pathlib import Path

from app.services.survey_qq_parser import parse_questionnaire

BHTT_POLICY = Path(__file__).resolve().parent.parent / "release" / "bhtt_approval.json"
REQUIRED = 62


def _sha(content):
    return hashlib.sha256(content).hexdigest()


def verify_evidence(source_hash, questions, inventory_bytes, policy):
    if policy.get("status") != "approved":
        raise ValueError("Authoritative BHTT inventory has not been approved.")
    approval = policy.get("approval")
    if (not isinstance(approval, dict) or set(approval) != {"reviewer", "approved_on", "review_record"}
            or not all(isinstance(value, str) and value.strip() for value in approval.values())
            or not approval["review_record"].startswith("https://")):
        raise ValueError("An independent reviewer, approval date and review record are required.")
    date.fromisoformat(approval["approved_on"])
    if policy.get("study") != "BHTT-W3B" or policy.get("required_substantive_questions") != REQUIRED:
        raise ValueError("Invalid BHTT release policy.")
    if not re.fullmatch(r"[a-f0-9]{64}", str(policy.get("source_sha256"))) or source_hash != policy["source_sha256"]:
        raise ValueError("Source questionnaire hash does not match the BHTT approval record.")
    if inventory_bytes is None or _sha(inventory_bytes) != policy.get("approved_inventory_sha256"):
        raise ValueError("The supplied inventory does not match the independently approved inventory hash.")
    inventory = json.loads(inventory_bytes)
    if not isinstance(inventory, dict) or inventory.get("source_sha256") != source_hash:
        raise ValueError("Inventory source hash does not match the questionnaire.")
    entries = inventory.get("questions", [])
    if not isinstance(entries, list) or len(entries) != REQUIRED or len(questions) != REQUIRED:
        raise ValueError("Exactly 62 substantive questions are required in the extraction and inventory.")
    visited = set()
    for entry, question in zip(entries, questions, strict=True):
        if not isinstance(entry, dict):
            raise ValueError("Each inventory entry must contain traceable question evidence.")
        key = question.question_number.strip().casefold()
        if not key or key in visited or entry.get("id") != question.question_number:
            raise ValueError("Duplicate, missing or mismatched question identifier.")
        visited.add(key)
        if (entry.get("classification") != "substantive" or not question.source_line
                or entry.get("source_line") != question.source_line
                or entry.get("source_text_sha256") != _sha(question.source_text.encode("utf-8"))
                or entry.get("question_type") != question.question_type
                or not isinstance(entry.get("evidence_reference"), str) or not entry["evidence_reference"].strip()):
            raise ValueError("Question classification, type or source evidence does not match the approved inventory.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("questionnaire", type=Path)
    parser.add_argument("--inventory", type=Path)
    parser.add_argument("--show-extraction", action="store_true", help="Output question and exclusion text for local review.")
    parser.add_argument("--require-ci-pin", action="store_true", help="Require the protected CI approval-record hash.")
    args = parser.parse_args()
    try:
        policy_bytes = BHTT_POLICY.read_bytes()
        if args.require_ci_pin and os.getenv("BHTT_RELEASE_APPROVAL_SHA256", "").lower() != _sha(policy_bytes):
            raise ValueError("A trusted release approval record hash is missing or does not match.")
        policy = json.loads(policy_bytes)
        if not isinstance(policy, dict) or policy.get("version") != 1:
            raise ValueError("Invalid BHTT approval record format.")
        content = args.questionnaire.read_bytes()
        digest = _sha(content)
        if args.questionnaire.suffix.lower() != ".docx" or digest != policy.get("source_sha256"):
            raise ValueError("Expected the exact BHTT source questionnaire DOCX.")
        parsed = parse_questionnaire(content, args.questionnaire.name)
        evidence = {"inventory_version": 1, "sha256": digest, "substantive_candidate_count": len(parsed.questions),
                    "excluded_block_count": len(parsed.excluded_blocks)}
        if args.show_extraction:
            evidence.update(questions=[asdict(q) for q in parsed.questions], exclusions=[asdict(b) for b in parsed.excluded_blocks])
        print(json.dumps(evidence, ensure_ascii=True, indent=2))
        inventory_bytes = args.inventory.read_bytes() if args.inventory else None
        verify_evidence(digest, parsed.questions, inventory_bytes, policy)
    except (OSError, ValueError, TypeError) as exc:
        print(f"BHTT release gate blocked: {exc}", file=sys.stderr)
        return 1
    print("BHTT release acceptance passed for the approved source and inventory.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
