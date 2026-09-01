# PBV Concept — Report Cover Artwork

Futuristic Purpose Built Vehicle (PBV) concept cover in a dramatic split-view
composition, 1920 × 1080, designed as a professional report cover.

| File | Description |
| --- | --- |
| `pbv-concept-cover.svg` | Master artwork (hand-built vector, fully editable) |
| `pbv-concept-cover.png` | Rasterized export, 1920 × 1080 |

## Composition

- **Left half — lounge interior cutaway**: swiveling lounge seats facing each
  other, wood-plank floor, oval side table with a warm lamp, amber ambient LED
  lines along ceiling and floor, panoramic glass roof with a soft daylight
  shaft, pixel-style rear lamps.
- **Right half — X-ray safety cutaway**: translucent shell over a glowing blue
  ultra-high-strength steel skeleton (rocker, floor rails, cross members,
  B/C-pillars, roof rail, front crash boxes with X-bracing), three
  crash-impact energy flow lines running from the front impact point into the
  load paths, impact ripples at the bumper, and a holographic safety-shield
  icon floating above the hood.
- **Styling**: dark navy and silver palette, cinematic studio beams,
  reflective floor with warm/cool light pools, blueprint grid behind the
  X-ray half, clean negative space across the top for a report title.

## Regenerating the PNG

```sh
chromium_headless_shell --no-sandbox --hide-scrollbars \
  --window-size=1920,1080 --force-device-scale-factor=1 \
  --screenshot=pbv-concept-cover.png "file://$PWD/pbv-concept-cover.svg"
```

Note: use the headless *shell* (or Playwright with an explicit viewport).
New headless Chromium's `--window-size` includes ~95 px of browser chrome,
which clips the bottom of the frame and pads the screenshot with white.
