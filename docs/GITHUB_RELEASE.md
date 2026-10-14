# Publish the prepared repository

The main source repository is [morphopilot/morphopilot](https://github.com/morphopilot/morphopilot). The project website is [morphopilot.github.io](https://morphopilot.github.io/). Its deployment configuration lives in [morphopilot/morphopilot.github.io](https://github.com/morphopilot/morphopilot.github.io); the website source stays in this repository's `website/` directory.

## Update the organization website

1. Commit and push reviewed changes to this repository. Wait for **Check source** to pass.
2. In `morphopilot/morphopilot.github.io`, run **Deploy MorphoPilot website**, setting `source_ref` to the reviewed commit SHA from this repository. The workflow checks out that revision and publishes only `website/`.
3. Verify the deployment URL, comparison controls, and Z playback. The deployment summary records the exact source revision.

With the GitHub CLI:

```bash
gh workflow run pages.yml --repo morphopilot/morphopilot.github.io -f source_ref="$(git rev-parse HEAD)"
```

The deployment needs no cross-repository token or copied website assets: it reads the public source and uses its own repository-scoped Pages permission. The main repository's manual Pages workflow remains available for forks that prefer a project-site URL.

## Review and create

1. Inspect the staged file list, license, third-party notices, synthetic assets, and publication scan.
2. Choose the GitHub user or organization that should own the project. Confirm the owner has authority to publish the project-owned code.
3. From a Git repository containing the reviewed files, create the public repository when you are ready:

```bash
# Replace YOUR_OWNER with the intended account or organization.
gh repo create YOUR_OWNER/morphopilot --public --source=. --remote=origin --push \
  --description "Self-hosted 3D annotation and iterative segmentation with interactive segmentation."
```

If the remote already exists, inspect it before adding or pushing an origin; do not overwrite another project's history. Repository initialization and the first commit may already be present in a local delivery. Check `git status` and `git log -1` first.

## Connect the website

The project introduction is in `website/`. To host it with GitHub Pages:

1. Set `repositoryUrl` in `website/config.js` to the real public repository URL. Set `studioUrl` only if an intentional, accessible demo deployment exists; otherwise leave it blank.
2. In the GitHub repository, open Settings → Pages and choose **GitHub Actions** as the build source.
3. Run the **Publish project website** workflow manually. It uploads only `website/`.
4. Use the actual Pages URL reported by the workflow as the repository's Website URL and optionally add it near the README heading.

The site contains synthetic visualizations and project documentation links. It is an introduction, not a running GPU application. The standalone server must be installed separately for annotation and training.

## Before announcing a release

- Confirm frontend checks and CPU tests pass on GitHub, not only locally.
- Enable private vulnerability reporting if available; review default branch protection and required checks.
- Add project topics such as `3d-segmentation`, `microscopy`, `annotation`, `interactive-segmentation`, and `human-in-the-loop`.
- Check all README links, the website, and the documented clean-install path as a new visitor.
- Tag a reviewed commit (for example `v0.1.0`) and attach a source archive produced from that commit.
- Cite only validation actually performed. Do not publish private ten-round data, learned weights, or screenshots without separate redistribution rights.

The fund application draft is deliberately kept outside this repository. Review personal details, collaborators, the public URLs, and the submission acknowledgements before submitting it.

To package a clean committed source tree (including the website and public synthetic fixtures, excluding ignored runtime/dependency files):

```bash
python3 scripts/package_source.py --output ../morphopilot-v0.1.0-source.zip
```

The command refuses to overwrite an existing ZIP, checks the extracted archive for common publication mistakes, and writes a SHA-256 sidecar.
