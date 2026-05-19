"""Project quality gate for Pattern Finder changes.

Runs the checks we repeat before committing phase work:
- Python compile check
- Unit tests
- Git whitespace check
- Secret-pattern scan
- Sensitive staged path guard
- Plan document consistency check
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PLAN_PATH = ROOT / "docs" / "NSE_PATTERN_ENGINE_PLAN_Final.html"
COMPILE_TARGETS = ["filters", "engine", "patterns", "setup", "tests", "scripts"]
SENSITIVE_STAGED_PATHS = (".env", "data/", "output/")
TOKEN_RE = re.compile(r"\b\d{6,}:[A-Za-z0-9_-]{20,}\b")
TASK_RE = re.compile(r'data-task="([^"]+)"')
DEFAULT_DONE_RE = re.compile(r"const DEFAULT_DONE=new Set\(\[([\s\S]*?)\]\);")
DEFAULT_ITEM_RE = re.compile(r"'([^']+)'")


def main() -> int:
    args = parse_args()
    checks = [
        ("compile", check_compile),
        ("unit_tests", check_unit_tests),
        ("git_whitespace", check_git_whitespace),
        ("sensitive_staged_paths", check_sensitive_staged_paths),
        ("secret_scan", check_secret_scan),
        ("plan_defaults", lambda: check_plan_defaults(args.max_completed_phase)),
    ]

    failed = False
    for name, check in checks:
        print(f"[quality_gate] {name} ...", flush=True)
        try:
            check()
        except QualityGateError as exc:
            failed = True
            print(f"[quality_gate] {name} FAIL: {exc}", flush=True)
            if not args.keep_going:
                return 1
        else:
            print(f"[quality_gate] {name} PASS", flush=True)

    return 1 if failed else 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Pattern Finder quality checks.")
    parser.add_argument(
        "--max-completed-phase",
        type=int,
        default=None,
        help="Fail if DEFAULT_DONE includes tasks from a later phase.",
    )
    parser.add_argument(
        "--keep-going",
        action="store_true",
        help="Run all checks before returning failure.",
    )
    return parser.parse_args()


def check_compile() -> None:
    run([sys.executable, "-m", "compileall", "-q", *COMPILE_TARGETS], capture=True)


def check_unit_tests() -> None:
    run([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"], capture=True)


def check_git_whitespace() -> None:
    run(["git", "diff", "--check"])
    run(["git", "diff", "--cached", "--check"])


def check_sensitive_staged_paths() -> None:
    staged = git_lines(["diff", "--cached", "--name-only"])
    blocked = [
        path
        for path in staged
        if path == ".env" or path.startswith(SENSITIVE_STAGED_PATHS)
    ]
    if blocked:
        raise QualityGateError("sensitive staged path(s): " + ", ".join(blocked))


def check_secret_scan() -> None:
    haystacks = [
        ("working diff", git_text(["diff"])),
        ("staged diff", git_text(["diff", "--cached"])),
    ]
    for path in changed_untracked_files():
        text = read_text_if_reasonable(ROOT / path)
        if text is not None:
            haystacks.append((path, text))

    hits = []
    for label, text in haystacks:
        if TOKEN_RE.search(text):
            hits.append(label)
    if hits:
        raise QualityGateError("possible token pattern in " + ", ".join(hits))


def check_plan_defaults(max_completed_phase: int | None) -> None:
    html = PLAN_PATH.read_text(encoding="utf-8")
    task_ids = TASK_RE.findall(html)
    default_match = DEFAULT_DONE_RE.search(html)
    if not default_match:
        raise QualityGateError("DEFAULT_DONE set not found")

    defaults = DEFAULT_ITEM_RE.findall(default_match.group(1))
    duplicate_tasks = duplicates(task_ids)
    duplicate_defaults = duplicates(defaults)
    missing = sorted(set(defaults) - set(task_ids))
    future = []
    if max_completed_phase is not None:
        future = [
            task_id
            for task_id in defaults
            if task_phase(task_id) is not None and task_phase(task_id) > max_completed_phase
        ]

    problems = []
    if duplicate_tasks:
        problems.append("duplicate task ids: " + ", ".join(duplicate_tasks))
    if duplicate_defaults:
        problems.append("duplicate DEFAULT_DONE ids: " + ", ".join(duplicate_defaults))
    if missing:
        problems.append("DEFAULT_DONE ids missing data-task: " + ", ".join(missing))
    if future:
        problems.append(
            f"DEFAULT_DONE has task(s) beyond phase {max_completed_phase}: "
            + ", ".join(future)
        )
    if problems:
        raise QualityGateError("; ".join(problems))
    print(f"[quality_gate] plan_defaults count={len(defaults)}", flush=True)


def changed_untracked_files() -> list[str]:
    paths = []
    paths.extend(git_lines(["diff", "--name-only"]))
    paths.extend(git_lines(["diff", "--cached", "--name-only"]))
    paths.extend(git_lines(["ls-files", "--others", "--exclude-standard"]))
    return sorted(set(paths))


def read_text_if_reasonable(path: Path) -> str | None:
    try:
        if not path.is_file() or path.stat().st_size > 1_000_000:
            return None
        return path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return None


def duplicates(values: list[str]) -> list[str]:
    seen = set()
    dupes = set()
    for value in values:
        if value in seen:
            dupes.add(value)
        seen.add(value)
    return sorted(dupes)


def task_phase(task_id: str) -> int | None:
    try:
        return int(task_id.split("-", 1)[0])
    except (TypeError, ValueError):
        return None


def git_text(args: list[str]) -> str:
    return run(["git", *args], capture=True)


def git_lines(args: list[str]) -> list[str]:
    text = git_text(args)
    return [line.strip().replace("\\", "/") for line in text.splitlines() if line.strip()]


def run(command: list[str], *, capture: bool = False) -> str:
    result = subprocess.run(
        command,
        cwd=ROOT,
        check=False,
        text=True,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.STDOUT if capture else None,
    )
    if result.returncode != 0:
        output = (result.stdout or "").strip()
        raise QualityGateError(output or f"{' '.join(command)} exited {result.returncode}")
    return result.stdout or ""


class QualityGateError(RuntimeError):
    """Raised when a quality gate check fails."""


if __name__ == "__main__":
    raise SystemExit(main())
