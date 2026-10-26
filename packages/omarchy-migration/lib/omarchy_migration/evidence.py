"""Check a policy's source evidence against a provider checkout.

A policy cites the provider files it was derived from, with digests where the
rule depends on exact content. When the provider changes one of them, the
policy needs a new revision before it is used with that provider build.
"""

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys

from . import contract


def drift(policy, checkout):
    """Return evidence records whose file is missing or whose digest changed."""
    root = Path(checkout).resolve()
    records = [mount["evidence"] for mount in policy["mounts"]]
    records += [rule["evidence"] for rule in policy["rules"]]
    changed = []
    for record in records:
        path = (root / record["path"]).resolve()
        if root not in path.parents or not path.is_file():
            changed.append({"path": record["path"], "status": "missing"})
        elif "sha256" in record:
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            if digest != record["sha256"]:
                changed.append({"path": record["path"], "status": "changed", "sha256": digest})
    for rule in policy["rules"]:
        for item in rule.get("transform", {}).get("earlier", []):
            if not _written_at(root, item):
                changed.append({"path": item["path"], "status": "earlier-block-not-found"})
    unique = {json.dumps(item, sort_keys=True): item for item in changed}
    return sorted(unique.values(), key=lambda item: item["path"])


def _written_at(root, item):
    """Whether an earlier block's text is in its cited file at its cited commit."""
    shown = subprocess.run(["git", "-C", str(root), "show", f"{item['commit']}:{item['path']}"],
                           capture_output=True)
    return shown.returncode == 0 and item["block"].strip("\n").encode() in shown.stdout


def main(argv=None):
    parser = argparse.ArgumentParser(description="Report policy evidence that changed in a provider checkout.")
    parser.add_argument("policy")
    parser.add_argument("checkout")
    arguments = parser.parse_args(argv)
    policy = contract.parse(Path(arguments.policy).read_bytes())
    if policy["schema"] != contract.POLICY:
        parser.error("not a policy document")
    head = subprocess.run(["git", "-C", arguments.checkout, "rev-parse", "HEAD"],
                          capture_output=True, text=True, check=True).stdout.strip()
    changed = drift(policy, arguments.checkout)
    print(json.dumps({"revision": policy["revision"], "policy_commit": policy["source"]["commit"],
                      "checkout_commit": head, "changed": changed}, indent=2, sort_keys=True))
    return 1 if changed else 0


if __name__ == "__main__":
    sys.exit(main())
