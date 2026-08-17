#!/usr/bin/env python3
"""Deprecated Phase-2 entry point.

The original baseline combined workload size and shape and called an API that
no longer exists. It is retained as a discoverable migration shim because old
reports and result artifacts refer to this filename.
"""


def main() -> None:
    raise SystemExit(
        "scripts/run_baseline.py is deprecated because its workload model was "
        "methodologically invalid. Run `python scripts/run_smoke_experiment.py` "
        "for the integration smoke test or `python scripts/build_canonical_results.py` "
        "for release evidence. See METHODOLOGY_CORRECTION_REPORT.md."
    )


if __name__ == "__main__":
    main()

