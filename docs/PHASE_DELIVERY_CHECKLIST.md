# Phase Delivery Checklist

Use this checklist before marking a phase complete or committing phase work.

1. Confirm the scope in `docs/NSE_PATTERN_ENGINE_PLAN_Final.html`.
2. Implement only the current phase slice.
3. Add focused tests that prove the new behavior.
4. For visual output, render a sample and check desktop plus mobile.
5. Update `DEFAULT_DONE` only after the implementation is proven.
6. Run the quality gate:

```powershell
python scripts/quality_gate.py
```

Use this stricter form when you want to prevent accidentally marking a later phase complete:

```powershell
python scripts/quality_gate.py --max-completed-phase 5
```

For Phase 6, use `--max-completed-phase 6`.

7. Review the diff:

```powershell
git diff --check
git diff --stat
git diff --name-only
```

8. Stage only phase files. Do not stage `.env`, `data/`, `output/`, screenshots, or sample dashboards.
9. Commit only after the gate and review are clean.
