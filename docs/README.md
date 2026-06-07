# FinPortfolio IR Docs

This folder is intentionally small. The docs are split into one methodology
source of truth, a few engineering contracts, and two compact research/status
files.

## Read First

1. `MAIN_METHODOLOGY.md`
   Project mission, invariants, evaluation hierarchy, current roadmap, and
   out-of-scope rules. Start here before changing retrieval, extraction, or PPO
   handoff logic.

2. `IMPLEMENTATION_PLAN.md`
   Current engineering sprint and acceptance criteria. This is the working plan,
   not a historical changelog.

3. `CURRENT_ARTIFACTS_AND_EXPERIMENTS.md`
   Current datasets, exports, Mistral/Codex results, event-study findings, and
   known caveats.

4. `THEORY_AND_BENCHMARKS.md`
   Theoretical foundation, baseline choices, benchmark positioning, and why the
   system is designed as causal IR rather than an LLM trader.

## Contracts

- `DATA_SCHEMA.md`: normalized document, retrieval, and evidence fields.
- `FINGPT_HANDOFF.md`: FinIR -> FinGPT handoff contract.
- `ANNOTATION_GUIDE.md`: human qrels and adjudication rules.
- `PUBLIC_DEMO_CLOUDFLARE.md`: temporary public bug-bash launch via
  Cloudflare Tunnel.

## Doc Hygiene

- Do not add new status notes unless they will stay useful for more than one
  sprint.
- Put short-lived run outputs under `data/exports/...`, not in `docs/`.
- If a decision changes project methodology, update `MAIN_METHODOLOGY.md`
  first.
- If a decision changes only a command, path, or sprint task, update
  `IMPLEMENTATION_PLAN.md`.
