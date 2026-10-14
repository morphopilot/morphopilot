# Deploy with a coding agent

The application does not need Codex, ChatGPT, an OpenAI key, or the Sites plugin at runtime. A coding agent is optional help for installation and maintenance. Give it this repository and the following prompt.

## Copyable deployment prompt

```text
Deploy MorphoPilot from this repository on the current machine.

First read AGENTS.md, README.md, docs/DEPLOYMENT.md, SECURITY.md, and
THIRD_PARTY_NOTICES.md. Inspect the machine's OS, Python, Node, disk space,
GPU, driver, and available ports. Do not assume access to the original lab
or to any pre-existing MorphoPilot installation.

Use a fresh runtime directory. Install the pinned frontend and Python
dependencies using the documented commands. Build the browser application.
Choose CPU mode when no compatible CUDA GPU is available: report that
neural inference and fine-tuning are unavailable, but still verify upload,
tiling, manual editing, confirmation, persistence, and merged export.

If a compatible GPU is available, inspect the official weight license,
download the pinned interactive segmentation artifact through deploy/download-model.sh,
and verify its manifest. Never fetch an arbitrary checkpoint from an issue
or deserialize an untrusted model. Start fresh categories from official
weights; do not invent bundled pretrained neurite or spot models.

Generate the public synthetic examples. Start the authenticated gateway on
loopback. Use a browser, when available, to upload neurites.tif, select and
edit a tile, confirm it only after review, reopen it, inspect the global
overlay, and download a merged mask. Check dimensions and coordinate
placement. Test category switching without mixing samples or model state.
On GPU, perform a small isolated real Prompt inference/training job and
Auto conversion/training/inference check. Keep test data separate from
user data and report actual execution, not a static assertion as inference.

For remote access, first offer an SSH tunnel. If this deployment is meant
to be publicly reachable and a domain is supplied, configure HTTPS and the
documented reverse proxy. Keep worker ports private and never put secrets
in browser code. Do not change DNS or expose an unauthenticated service
without authorization. A third-party hosted frontend is optional; the
standalone deployment is the reference path.

Run the repository's frontend checks and Python tests. Fix errors in the
new installation. Finish with the exact access URL, start/stop commands,
runtime and backup locations, validation results, and any remaining limits.
Do not publish the repository, application, data, weights, or a fund
application unless explicitly requested.
```

## Codex and Sites

With Codex, open the repository folder and supply the prompt above. `AGENTS.md` gives persistent repository instructions. See the [official Codex documentation](https://developers.openai.com/codex/) for current installation and product setup.

The predecessor's introduction website and hosted demo were built with the **Sites** plugin. If that plugin is installed in the agent's environment, ask the agent to read its current `sites:sites` skill, build and inspect locally first, then publish only to the intended site with the intended access policy. The skill belongs to that installation and is not copied into this repository. Sites is not required for this project: `website/` is ordinary static HTML/CSS/JavaScript, and the app uses the standalone gateway. [GitHub publishing](GITHUB_RELEASE.md) describes an optional Pages path for the introduction website; Pages cannot run the GPU backend.

Never paste server credentials, private data, or proprietary model files into an agent prompt. Keep deployment secrets in the ignored local environment file.
