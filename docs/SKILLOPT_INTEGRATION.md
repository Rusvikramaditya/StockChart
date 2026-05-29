# SkillOpt Integration

Pattern Finder uses SkillOpt only as an offline rulebook experiment. The daily
scanner stays deterministic: SkillOpt can propose a better alert-selection
skill, but Pattern Finder backtests decide whether any rule is worth keeping.

## What Gets Exported

`scripts/export_skillopt_dataset.py` runs the walk-forward backtest and turns
each historical trade into a SkillOpt SearchQA item:

- `context`: only signal-time facts, such as pattern, conviction score, filters,
  reward/risk, sector strength, market regime, and pattern traits.
- `answers`: `PROMOTE` if the future backtest outcome hit target first, otherwise
  `REJECT`.
- `metadata`: audit-only future outcome fields. SkillOpt SearchQA does not show
  this metadata to the target model.

The split is chronological by signal date: earliest trades train the skill,
middle trades validate edits, latest trades test the final skill.

## Export A Dataset

From the Pattern Finder repo:

```powershell
python scripts\export_skillopt_dataset.py `
  --universe small_mid_liquid `
  --years 3 `
  --min-conviction 50 `
  --output-dir output\skillopt\small_mid_liquid
```

For a quick smoke test, reduce the work:

```powershell
python scripts\export_skillopt_dataset.py `
  --universe watchlist `
  --limit-symbols 25 `
  --max-days 180 `
  --output-dir output\skillopt\watchlist_probe
```

## Run SkillOpt

Clone and install SkillOpt separately:

```powershell
git clone https://github.com/microsoft/SkillOpt.git A:\Tools\SkillOpt
cd A:\Tools\SkillOpt
pip install -e .
```

Then run the generated command from the dataset folder:

```powershell
.\output\skillopt\small_mid_liquid\skillopt_command.ps1 -SkillOptPath A:\Tools\SkillOpt
```

Set the model credentials that SkillOpt expects before running. The generated
command uses SkillOpt's `searchqa` adapter and the exported `initial_skill.md`.

## How To Judge The Result

Do not copy a learned `best_skill.md` straight into live trading. Read the rules
it learned, convert only sensible ones into explicit Pattern Finder thresholds,
then rerun the normal backtest and quality gate. A useful SkillOpt run should
reduce bad loud alerts on the held-out test split and improve Pattern Finder's
own metrics, not just sound smarter.
