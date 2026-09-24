"""Create a blind reading pack and summarize human preferences, without API calls.

python benchmarks/evaluate_prose.py cases.json --output evaluation
python benchmarks/evaluate_prose.py --votes evaluation/votes.json --key evaluation/key.json

Cases are objects with id, kind, original and candidate strings. For actual runs,
use saved chapter_XX.draft.txt and chapter_XX.txt as the original/candidate texts.
"""

import argparse
import json
import random
from pathlib import Path


CRITERIA = ["continuity", "specificity", "voice", "emotional_credibility", "economy"]


def prepare(cases, output, seed=7):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    rng = random.Random(seed)
    key, votes = {}, {}
    pages = ["# Blind prose comparison\n\nRead both versions before opening key.json. "
             "Choose A, B or tie for each criterion in votes.json. "
             "For content_loss, choose A, B, both or neither. Record specific evidence.\n"]
    for case in cases:
        ident = case["id"]
        if not isinstance(ident, str) or not ident or ident in key:
            raise ValueError("Each case needs a unique nonempty id")
        if any(not isinstance(case.get(k), str) or not case[k].strip() for k in ("kind", "original", "candidate")):
            raise ValueError(f"Incomplete case: {ident}")
        labels = ["original", "candidate"]
        rng.shuffle(labels)
        key[ident] = {"A": labels[0], "B": labels[1], "kind": case["kind"]}
        votes[ident] = {**{criterion: "" for criterion in CRITERIA}, "content_loss": "", "evidence": ""}
        pages.append(f"## {ident} — {case['kind']}\n\n### A\n\n{case[labels[0]]}\n\n### B\n\n{case[labels[1]]}\n")
    # Exclusive creation prevents accidentally overwriting someone's completed votes.
    for name, text in (("blind_review.md", "\n".join(pages)),
                       ("key.json", json.dumps(key, indent=2)),
                       ("votes.json", json.dumps(votes, indent=2))):
        with (output / name).open("x", encoding="utf-8") as stream:
            stream.write(text)


def summarize(votes, key):
    totals = {criterion: {"candidate": 0, "original": 0, "tie": 0} for criterion in CRITERIA}
    losses = {"candidate": 0, "original": 0}
    for ident, vote in votes.items():
        labels = key[ident]
        if not isinstance(vote.get("evidence"), str) or not vote["evidence"].strip():
            raise ValueError(f"Missing evidence for {ident}")
        for criterion in CRITERIA:
            choice = vote.get(criterion)
            if choice not in ("A", "B", "tie"):
                raise ValueError(f"Missing/invalid {criterion} vote for {ident}")
            totals[criterion]["tie" if choice == "tie" else labels[choice]] += 1
        loss = vote.get("content_loss")
        if loss not in ("A", "B", "both", "neither"):
            raise ValueError(f"Missing/invalid content loss vote for {ident}")
        for label in ("A", "B"):
            losses[labels[label]] += int(loss in (label, "both"))
    return {"rated_cases": len(votes), "preferences": totals, "content_loss_cases": losses}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("cases", nargs="?")
    parser.add_argument("--output", default="evaluation")
    parser.add_argument("--votes")
    parser.add_argument("--key")
    args = parser.parse_args()
    if args.votes and args.key:
        print(json.dumps(summarize(json.loads(Path(args.votes).read_text(encoding="utf-8")),
                                   json.loads(Path(args.key).read_text(encoding="utf-8"))), indent=2))
    elif args.cases:
        prepare(json.loads(Path(args.cases).read_text(encoding="utf-8")), args.output)
        print(f"Blind reading pack: {Path(args.output).resolve() / 'blind_review.md'}")
    else:
        parser.error("provide cases.json, or both --votes and --key")
