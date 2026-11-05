# OSE visual identity

<a href="../../README.md"><img align="right" src="ose-logo.svg" alt="OSE project home" width="64" height="64"></a>

The project uses the existing turquoise mark from the OSE Web Console's sign-in
page and sidebar. Its source is
[`console-mark.svg`](../../ose-ws/frontend/src/assets/console-mark.svg).
The project logo preserves that drawing and adds the console's rounded dark tile
so it stays legible on both light and dark documentation backgrounds.

| Asset | Use |
| --- | --- |
| [Logo (SVG)](ose-logo.svg) | README headers; scalable and transparent outside the tile. |
| [Logo (512 × 512 PNG)](ose-logo.png) | Tools that require a raster image. |
| [Banner (SVG)](ose-banner.svg) | The repository's main README, displayed on its GitHub landing page. |
| [Social preview (1280 × 640 PNG)](ose-social-preview.png) | GitHub repository link previews. |
| [Social preview (SVG)](ose-social-preview.svg) | Scalable source for the raster social preview. |

The palette comes from the console: turquoise `#7dd6cb`, tile `#1b3743`,
border `#41616a`, navy background `#101e2d`, and light text `#dae4ed`.
Keep the mark's proportions and provide alternative text when embedding it.

Regenerate the SVG assets after changing the source mark:

```sh
python3 docs/branding/generate.py
```

To also export the PNG assets, install CairoSVG (with its system Cairo library)
and the DejaVu Sans fonts, then run:

```sh
python3 -m pip install CairoSVG
python3 docs/branding/generate.py --png
```

The README banner appears automatically when these files are published to GitHub.
The separate repository social preview is configured in GitHub under
**Settings → General → Social preview → Edit → Upload an image** using
`ose-social-preview.png`. Committing the image alone does not change that setting.
