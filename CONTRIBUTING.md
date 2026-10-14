# Contributing to MorphoPilot

Thank you for helping make volumetric annotation easier to reproduce. Small, complete improvements are welcome: an install fix with reproduction steps, a coordinate regression test, a clearer user flow, or a documented adapter.

## Set up

Follow [Deployment](docs/DEPLOYMENT.md) for a CPU workspace first. Use a fresh runtime directory and synthetic data; most storage, UI, and category changes do not require a GPU. Node and Python versions are specified there.

```bash
npm ci
npm run typecheck
npm test
npm run lint
npm run build
.venv/bin/python -m pip install -r deploy/requirements-test.lock
.venv/bin/python -m pytest tests/test_*.py deploy/test_gateway.py -q
.venv/bin/python deploy/smoke-cpu.py
```

GPU changes need an isolated runtime and real neural checks in addition to these commands. Record the GPU, library versions, model revision, job configuration, and outcome. Do not represent a mocked prediction or a synthetic reference as model inference.

## Open an issue or pull request

- Describe the user-visible problem, a minimal reproduction, and expected behavior.
- Include browser/OS/runtime versions and redacted logs. Use synthetic images whenever possible.
- Keep changes scoped; explain state migrations, compatibility, and any untested boundaries.
- Add a focused regression check for substantive bugs, and run checks appropriate to the change.
- For UI changes, include a screenshot made with synthetic or explicitly redistributable data.

Never include tokens, local configuration, user datasets, model binaries, or access links in a commit or issue. Never train on another person's unconfirmed annotations. Model architecture and checkpoint changes must preserve the semantics documented in [Architecture](docs/ARCHITECTURE.md).

## Contribution license

By intentionally submitting a contribution, you agree to license your contribution under this repository's Apache-2.0 license, unless a different compatible license is explicitly documented and accepted. You must have the rights to contribute the code and assets. Keep third-party attribution and notices intact.

## Useful first contributions

See [Roadmap](docs/ROADMAP.md). Cross-platform installation reports, accessible keyboard interactions, TIFF-axis validation, and small synthetic regression cases are particularly useful. Discuss major model or storage changes before undertaking a large rewrite.
