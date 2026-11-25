# Custom icons

Put your own badge icons here as `icons/<name>.svg`, then use `icon: <name>` on a badge in `xp-config.yml`.

Rules (the build checks these and tells you exactly what's wrong):

- A square `viewBox`. `-16 -16 32 32` is native; `0 0 24 24` icon sets like Lucide or Tabler work too.
- Draw in `currentColor` (or `none`). The build swaps in the badge's color.
- Only shapes: `path`, `circle`, `ellipse`, `rect`, `line`, `polyline`, `polygon`, `g`.
- No `<text>`, images, links, scripts, styles, gradients or filters.

Generate one with any LLM using prompt #2 in [PROMPTS.md](../PROMPTS.md), then check it:

```bash
python scripts/xp.py check-icon icons/my-icon.svg
python scripts/xp.py preview        # open _preview/index.html
```

If you use icons from an icon set, keep its license notice (most are MIT/ISC and just ask for attribution).
`controller.svg` is an example used by `examples/configs/esports.yml`. Delete it if you don't need it.
