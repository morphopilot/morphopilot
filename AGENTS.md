# Working on MorphoPilot

Read `README.md`, `docs/ARCHITECTURE.md`, and the relevant deployment/user guide before editing. This is the portable MorphoPilot project. Do not assume access to the original lab, server, data, or hosting account.

- Work in an isolated runtime. Never change someone else's annotations, model selection, jobs, or credentials to make a test pass.
- Keep all inference and training on the configured server. OpenAI APIs and coding agents are optional development tools, not application runtime dependencies.
- Preserve ZYX coordinate conventions, mask revisions, confirmed snapshot immutability, category isolation, and explicit test exclusion.
- During Prompt training, the Auto stem is frozen and unused. Conversion must preserve the Prompt parameters. Auto training updates shared parameters and can change Prompt behavior.
- User drawings are not automatically ground truth; only confirmed snapshots enter training. Do not silently confirm data or reinterpret draft coverage.
- Do not bundle private images, model binaries, research screenshots, local paths, tokens, or hosted-site identities. Use the synthetic generator for examples and screenshots.
- Never claim a real GPU inference/training check from a CPU stub, static source assertion, or synthetic reference. Report exactly what ran.
- Use the commands in CONTRIBUTING.md. A GPU-dependent change needs an isolated real GPU check; document the limitation when hardware is unavailable.
- Keep the README, deployment instructions, and website consistent with implemented capabilities. Label planned work as planned.
- Preserve third-party licenses and model-license separation. Run the publication scan before packaging.

For a ready-to-use deployment assignment, see `docs/AGENT_PROMPT.md`.
