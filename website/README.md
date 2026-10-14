# MorphoPilot project website

**Teach the model. Reveal the structure.**

A responsive, buildless English introduction to MorphoPilot. It presents the annotation and learning workflow with reproducible synthetic examples, without private research data, experiment results, screenshots, hosted domains or authentication URLs.

## Preview locally

From the **repository root**:

```bash
python3 -m http.server 9000 --bind 127.0.0.1
```

Open [http://127.0.0.1:9000/website/](http://127.0.0.1:9000/website/).

There is no Node build step or external font/CDN dependency. Serving the repository root keeps links to `../docs/`, `../examples/` and `../README.md` functional. Do not expose a working directory containing runtime data, credentials or private files publicly. For public hosting, use a clean release/source checkout and a static publishing process that includes the required public documentation and examples.

Opening `index.html` directly also supports the interactive illustrations, but local HTTP is the recommended preview. The page is an introduction and client-side reference visualization: it does **not** run model inference, training or live segmentation.

To export the illustrations and interactions as a single HTML file:

```bash
python3 website/build_offline.py --output ../MorphoPilot_Website.html
```

Open the exported HTML in a browser. Its visuals, styles and scripts are embedded. Documentation and TIFF download links still point to the source folder, so keep that folder in its original location alongside the HTML. The export is not the annotation server.

## Configure public links

Edit `config.js`:

```js
window.MORPHOPILOT_CONFIG = {
  repositoryUrl: '', // The actual public GitHub repository URL, once published.
  repositoryBranch: 'main', // Branch used by documentation and fixture links.
  studioUrl: '',     // Optional separately deployed MorphoPilot UI URL.
};
```

Blank defaults link to the local project README and self-hosting instructions. When a repository URL is configured, links to project documentation and checked-in synthetic fixture downloads (`examples/fixtures/`) are redirected to that repository/branch; a website-only GitHub Pages deployment therefore works without copying the whole repository into the site artifact. The script accepts only HTTP(S) URLs without embedded usernames or passwords. Never put secrets, tokens, authentication callback URLs or private infrastructure addresses in this file.

For a website-only Pages deployment, **set `repositoryUrl` before publishing**. Without it, public-site documentation and fixture buttons show an explicit configuration notice instead of navigating to missing parent files. `examples/fixtures/` is tracked source data; `examples/generated/` is an ignored, local regeneration directory.

## Included interactions

- Neurite-like and spot-like synthetic examples with input/reference comparison.
- A 25-slice depth slider and per-dataset selection; Play advances immediately, then loops through every plane, with an explicit Pause control.
- A clean original image and its matching full reference overlay. Use the draggable divider, or choose **Original only**, **Split view**, and **Full mask**. The legend matches the lime neurite mask or orange spot mask.
- Keyboard-accessible workflow tabs and model-mode explanation.
- A workflow figure distinguishes **training** (source image + confirmed binary mask pairs) from **review** (a colored mask overlay for visual inspection). It opens in a larger dialog.
- Deployment guide dialog, FAQ and mobile navigation.
- Reduced-motion support and visible keyboard focus.

All overlays are **known procedural references**, not neural predictions. The synthetic website fixtures do not claim model accuracy or clinical performance. No private research images, trained weights, research screenshots or original hosted access links are included.

## Rebuild the assets

```bash
python examples/generate_synthetic.py \
  --output-dir examples/generated \
  --website-assets website/assets
```

See [examples/README.md](../examples/README.md) for the generator and file layout. `assets/source-notes.json` records generation metadata and asset hashes. `assets/training-pairs.png` supplies the Fine-tune tab; `assets/workflow-figure.png` supplies the complete workflow overview. `assets/hero.png` and `assets/workflow-figure.png` can be embedded in the repository README. `assets/mark.svg` is the project-generated MorphoPilot mark.

## Checks

```bash
node --check website/app.js
node --check website/config.js
```

Node is optional for these syntax checks, not required to serve the website. Browser checks should cover keyboard navigation, Z playback, both reference comparisons, mobile layout and dialog closing. No server credentials are required for the marketing site.

## License and lineage

This site, its new mark and synthetic illustrations are Apache-2.0 project assets; see the root [LICENSE](../LICENSE). Upstream nnInteractive and other dependencies retain their own licenses and attribution in [Third-party notices](../THIRD_PARTY_NOTICES.md); model weights are obtained separately under the applicable upstream terms.
