"""
Jiminy — Agent Accountability Layer
Schema Validator CLI v1.0

Usage:
    python validator/validate.py traces/trace_01_approval.json
    python validator/validate.py traces/trace_02_denial_breach.json --verbose
    python validator/validate.py traces/trace_03_escalation.json

Exit codes:
    0   Trace is valid (may include warnings)
    1   Trace is invalid — field-level errors reported
    2   File not found or not valid JSON

Output format:
    Human-readable by default. Designed for terminal/CI use — a caller that
    wants the trace object directly should consume it via Python import
    instead of this CLI.
"""

from __future__ import annotations

import json
import sys
import warnings
from pathlib import Path

# Allow running from project root or from within the validator/ directory
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pydantic import ValidationError

from schema.trace_schema import DecisionTrace

# ---------------------------------------------------------------------------
# ANSI colour codes — minimal, purposeful (matches Jiminy brand restraint)
# ---------------------------------------------------------------------------

RESET = "\033[0m"
BOLD = "\033[1m"
GREEN = "\033[32m"  # PASS / valid
YELLOW = "\033[33m"  # WARNING
RED = "\033[31m"  # FAIL / invalid
DIM = "\033[2m"


def _line(char: str = "─", width: int = 60) -> str:
    return char * width


def _header(title: str) -> str:
    return f"\n{BOLD}{_line()}{RESET}\n{BOLD}{title}{RESET}\n{_line()}"


# ---------------------------------------------------------------------------
# Core validation logic
# ---------------------------------------------------------------------------


def validate_trace(path: Path, verbose: bool = False) -> bool:
    """
    Load and validate a trace JSON file against the Jiminy DecisionTrace schema.

    Returns True if valid, False if invalid.
    Prints field-level errors on failure. Prints warnings for soft issues.
    """

    # -- 1. File existence and JSON parse -----------------------------------

    print(_header("JIMINY TRACE VALIDATOR"))
    print(
        f"{DIM}Schema version: 1.0  |  Criteria: C1–C6  |  File: {path.name}{RESET}\n"
    )

    if not path.exists():
        print(f"{RED}ERROR  File not found: {path}{RESET}")
        return False

    try:
        raw = path.read_text(encoding="utf-8")
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        print(f"{RED}ERROR  Invalid JSON at line {exc.lineno}, col {exc.colno}:{RESET}")
        print(f"       {exc.msg}")
        return False

    if verbose:
        print(f"{DIM}Raw trace loaded. Keys present: {list(data.keys())}{RESET}\n")

    # -- 2. Pydantic validation (capture warnings) --------------------------

    validation_warnings: list[str] = []

    with warnings.catch_warnings(record=True) as caught_warnings:
        warnings.simplefilter("always")
        try:
            trace = DecisionTrace.model_validate(data)
        except ValidationError as exc:
            _print_validation_errors(exc)
            return False

    # Collect Jiminy-specific warnings
    for w in caught_warnings:
        if issubclass(w.category, UserWarning):
            validation_warnings.append(str(w.message))

    # -- 3. Print valid result ----------------------------------------------

    _print_success(trace, validation_warnings, verbose)
    return True


def _print_validation_errors(exc: ValidationError) -> None:
    """Print structured field-level errors from a Pydantic ValidationError."""

    error_count = exc.error_count()
    print(f"{RED}{BOLD}VALIDATION FAILED  —  {error_count} error(s) found{RESET}\n")

    for i, error in enumerate(exc.errors(), start=1):
        location = " → ".join(str(loc) for loc in error["loc"]) or "(root)"
        message = error["msg"]
        err_type = error["type"]
        value = error.get("input", "<not captured>")

        print(f"  {RED}[{i}]{RESET} Field:   {BOLD}{location}{RESET}")
        print(f"       Error:   {message}")
        print(f"       Type:    {DIM}{err_type}{RESET}")
        if value is not None and str(value) != "<not captured>":
            display_val = repr(value)
            if len(display_val) > 80:
                display_val = display_val[:77] + "..."
            print(f"       Value:   {DIM}{display_val}{RESET}")
        print()

    print(_line())
    msg = "This trace will not be accepted by the Jiminy evaluation pipeline."
    print(f"{RED}{msg}{RESET}")
    print("Resolve the error(s) above and re-run the validator.\n")


def _print_success(
    trace: DecisionTrace,
    validation_warnings: list[str],
    verbose: bool,
) -> None:
    """Print the success summary, including any soft warnings."""

    if validation_warnings:
        status_colour = YELLOW
        status_label = "VALID WITH WARNINGS"
    else:
        status_colour = GREEN
        status_label = "VALID"

    print(f"{status_colour}{BOLD}{status_label}{RESET}")
    print()

    # -- Trace summary ------------------------------------------------------
    print(f"  Trace ID        {DIM}{trace.trace_id}{RESET}")
    print(f"  Agent           {trace.agent_id}")
    task_display = trace.task_description[:72]
    if len(trace.task_description) > 72:
        task_display += "…"
    print(f"  Task            {task_display}")
    print(f"  Domain profile  {trace.domain_profile}")
    print(f"  Timestamp       {trace.timestamp.isoformat()}")
    print(f"  Steps           {len(trace.steps)}")
    print(f"  Escalations     {len(trace.escalation_events)}")
    print(f"  Error events    {len(trace.error_events)}")
    print()

    # -- Step summary (verbose) or step count (default) --------------------
    if verbose:
        print(f"  {BOLD}Steps:{RESET}")
        for step in trace.steps:
            if step.reasoning is None:
                reasoning_flag = f"{YELLOW}⚠ reasoning null{RESET}"
            else:
                reasoning_flag = f"{DIM}reasoning present{RESET}"
            print(f"    [{step.step_id}] {step.tool:<35} {reasoning_flag}")
        print()

    # -- Warnings -----------------------------------------------------------
    if validation_warnings:
        print(f"  {YELLOW}WARNINGS  ({len(validation_warnings)}){RESET}")
        for w in validation_warnings:
            # Strip the [JIMINY WARNING] prefix for cleaner output
            clean = w.replace("[JIMINY WARNING] ", "")
            # Word-wrap at 70 chars
            words = clean.split()
            line, lines = [], []
            for word in words:
                if sum(len(w) + 1 for w in line) + len(word) > 70:
                    lines.append(" ".join(line))
                    line = [word]
                else:
                    line.append(word)
            if line:
                lines.append(" ".join(line))
            for j, text_line in enumerate(lines):
                prefix = f"  {YELLOW}⚠{RESET}  " if j == 0 else "      "
                print(f"{prefix}{text_line}")
        print()
        print(
            f"  {DIM}Warnings do not prevent evaluation. They are noted in the{RESET}"
        )
        print(f"  {DIM}evidence report to annotate affected criterion findings.{RESET}")
        print()

    # -- Ready for next step ------------------------------------------
    print(_line())
    msg = "Trace accepted. Ready for Claude-as-Judge evaluation."
    print(f"{GREEN}{msg}{RESET}\n")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(
        prog="jiminy-validate",
        description=(
            "Jiminy trace schema validator. Validates a JSON trace file "
            "against the DecisionTrace schema."
        ),
    )
    parser.add_argument(
        "trace_file",
        type=Path,
        help="Path to the JSON trace file to validate.",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Print step-by-step detail in addition to the summary.",
    )

    args = parser.parse_args()
    valid = validate_trace(args.trace_file, verbose=args.verbose)
    sys.exit(0 if valid else 1)


if __name__ == "__main__":
    main()
