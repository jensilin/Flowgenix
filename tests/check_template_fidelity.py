"""Compare a converted flow definition against the flow it came from.

Not part of the pytest suite (no `test_` prefix): point it at any real template
or flow definition to confirm the migrated JSON still describes the same canvas.
The checks themselves live in `fidelity.py`, which the web app also runs.

    python tests/check_template_fidelity.py path/to/template.xml [target_version]
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from fidelity import compare_flows  # noqa: E402
from flow_document import parse_flow_file  # noqa: E402
from migration_engine import analyze_migration, generate_migrated_flow  # noqa: E402


def main() -> int:
    # Check details contain arrows; a Windows console codepage cannot encode them.
    sys.stdout.reconfigure(errors="replace")
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    path = Path(sys.argv[1])
    target = sys.argv[2] if len(sys.argv) > 2 else "2.6.0"

    doc = parse_flow_file(path.read_text(encoding="utf-8"), path.name)
    analysis = analyze_migration(doc, doc.detected_version or "1.25.0", target)
    generation = generate_migrated_flow(doc, analysis)
    report = compare_flows(doc, generation.migrated, generation.applied_changes)

    for check in report["checks"]:
        detail = check["detail"] or f"{check['checked'] - check['failed']}/{check['checked']}"
        print(f"{'PASS' if check['passed'] else 'FAIL'}  {check['label']} - {detail}")
        for example in check["examples"]:
            print(f"        {example}")
    for note in report["notes"]:
        print(f"\nNOTE  {note}")

    print()
    if not report["allPassed"]:
        print(f"{report['total'] - report['passed']} check(s) failed.")
        return 1
    print(f"All {report['total']} fidelity checks passed for {path.name} -> NiFi {target}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
