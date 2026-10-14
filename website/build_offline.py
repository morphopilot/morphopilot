#!/usr/bin/env python3
"""Embed the introduction's visuals and code in one locally viewable HTML file."""
import argparse
import base64
import html
import json
import mimetypes
import os
from pathlib import Path
import re

SITE = Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=SITE / 'offline.html')
    args = parser.parse_args()
    output = args.output.expanduser().resolve()
    if output == SITE / 'index.html':
        raise SystemExit('Choose an output other than the source index.html.')

    assets = {}
    for path in sorted((SITE / 'assets').iterdir()):
        if path.suffix not in {'.webp', '.png', '.svg', '.json'}:
            continue
        mime = mimetypes.guess_type(path.name)[0] or 'application/octet-stream'
        encoded = base64.b64encode(path.read_bytes()).decode('ascii')
        assets[path.relative_to(SITE).as_posix()] = f'data:{mime};base64,{encoded}'

    page = (SITE / 'index.html').read_text()
    css = (SITE / 'style.css').read_text()
    css = re.sub(r"url\(['\"]?(assets/[^)'\"]+)['\"]?\)",
                 lambda match: f'url("{assets[match[1]]}")', css)
    page = re.sub(r'<link[^>]+href="style\.css"[^>]*>',
                  lambda _: '<style>' + css + '</style>', page)
    page = re.sub(r'<script[^>]+src="[^"]+"[^>]*></script>', '', page)
    page = re.sub(r'(src|href)="(assets/[^"]+)"',
                  lambda match: f'{match[1]}="{assets[match[2]]}"', page)

    # Documentation and TIFF download links remain relative to the real source
    # directory. Keep that directory next to an exported Desktop HTML file.
    if not re.search(r'repositoryUrl\s*:\s*[\'\"]https?://', (SITE / 'config.js').read_text()):
        page = re.sub(r'href="(\.\./[^"]+)"', lambda match:
                      'href="' + html.escape(os.path.relpath(
                          (SITE / match[1]).resolve(), output.parent), quote=True) + '"', page)
    scripts = '\n'.join([
        (SITE / 'config.js').read_text(),
        'window.MORPHOPILOT_ASSET_URLS=' + json.dumps(assets) + ';',
        (SITE / 'assets/stack-data.js').read_text(),
        (SITE / 'app.js').read_text(),
    ]).replace('</script', '<\\/script')
    page = page.replace('</body>', '<script>' + scripts + '</script></body>')
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(page)
    print(f'Wrote {output.name} ({output.stat().st_size:,} bytes).')


if __name__ == '__main__':
    main()
