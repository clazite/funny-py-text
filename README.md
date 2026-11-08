# Birthday Shader Renderer

A small Python + moderngl renderer for a looping birthday animation:

```
text idle  ->  shader animation  ->  final frame hold  ->  repeat
```

The renderer rasterises the text from `assets/coolfont.ttf` into a signed
distance field (SDF), runs the shaders in `shaders/`, and can either show a live
preview window or export PNG frames and assemble them into a looping GIF.

The background is solid black. There is no gradient, glow, or extra filter: the
framebuffer is cleared to black and the shader itself outputs black outside the
glyphs. The text mask is white so the shader can read it as an SDF.

## Requirements

The included `.venv` already has everything:

- `moderngl` + `numpy` for offscreen GPU rendering
- `Pillow` for text rasterisation, SDF generation, PNG frames, and GIF assembly
- `tkinter` (Python standard library) for the preview window

Use the venv Python:

```bash
.venv/bin/python renderer.py
```

## Preview window

```bash
.venv/bin/python renderer.py
```

This opens a live preview. It renders at `--preview-scale` of the export
resolution (default `0.65`, so `624x624` for the default `960x960`). A smaller
preview is much faster because the image upload to Tk is the main bottleneck.
Use `--preview-scale 1` for a full-size preview, or lower it further for more
speed. GIF export always uses `--width`/`--height`.

The preview controls are:

| Key | Action |
| --- | --- |
| `Space` | Pause / resume |
| `R` | Restart the sequence |
| `F5` | Reload the shaders and `shaders/config.txt` |
| `S` | Save the current full-resolution frame to `output/preview_frame.png` |
| `Q` / `Esc` | Quit |

Shaders and config are also hot-reloaded automatically when their files change.

To use two lines of text (bash):

```bash
.venv/bin/python renderer.py --text $'HAPPY\nBIRTHDAY'
```

Change the export render size with `--width` and `--height` (default `960x960`).
The preview is scaled relative to that size.

Preview performance options:

```bash
# Full-size preview (slower, ~27 fps on a software-rendered X11 session)
.venv/bin/python renderer.py --preview-scale 1

# Smaller, faster preview
.venv/bin/python renderer.py --preview-scale 0.5

# Cap the preview refresh rate
.venv/bin/python renderer.py --preview-fps 30
```

The status bar shows the measured preview frame rate.

## Export a GIF

```bash
.venv/bin/python renderer.py --export output/birthday.gif
```

This writes every frame to `output/frames/frame_XXXXX.png`, then assembles
`output/birthday.gif`. The GIF loops forever (`loop=0`).

Useful export options:

```bash
# Faster / smaller
.venv/bin/python renderer.py --export output/birthday.gif --width 480 --height 480 --fps 20

# Two full idle/animate/hold cycles
.venv/bin/python renderer.py --export output/birthday.gif --cycles 2

# Delete PNG frames after the GIF is built
.venv/bin/python renderer.py --export output/birthday.gif --no-keep-frames

# Smaller GIF (no dithering)
.venv/bin/python renderer.py --export output/birthday.gif --no-dither
```

`export.py` can also be run directly:

```bash
.venv/bin/python export.py --gif output/birthday.gif
```

## Timeline

Defaults:

| Phase | Duration | Shader state |
| --- | --- | --- |
| Idle | 0.5 s | `uTime = 0`, `uProgress = 0`, `uFrame = 0` |
| Animate | 2 s | `uTime` runs 0 → 2, `uProgress` 0 → 1 |
| Hold | 0.5 s | `uTime = 2`, `uProgress = 1` |

Change them with `--idle`, `--animate`, and `--hold`. Idle and hold are frozen,
so their exported frames are identical and the GIF can collapse them into single
long-duration frames.

The shader's internal time is scaled by `--speed` (default `1.0`). With the
default `--animate 2 --speed 1.0`, `uTime` runs 0 → 2 over the 2-second animate
phase. Use a smaller `--speed` to slow the noise further, or a larger
`--animate` to stretch the pulse sweep.

## Shaders and config

The pipeline uses three files:

| File | Purpose |
| --- | --- |
| `shaders/emphemera.vert.glsl` | Full-screen triangle generated from `gl_VertexID` (no vertex buffers) |
| `shaders/emphemera.frag.glsl` | SDF text effect |
| `shaders/config.txt` | Uniform values for the effect |

The renderer supplies these uniforms every frame:

```glsl
uniform vec2      uResolution;  // render size in pixels
uniform float     uTime;        // animation-local seconds (frozen in idle/hold)
uniform int       uFrame;       // animation frame index (frozen in idle/hold)
uniform float     uProgress;    // 0 idle, 0..1 while animating, 1 hold
uniform sampler2D uSdf;         // float SDF texture, white text on black
uniform vec2      uHalfSize;    // half-size of the SDF box in shader p-space
uniform float     uRange;       // SDF distance range in shader p-space
```

`shaders/config.txt` uses one `name value` pair per line. Vectors are
comma-separated. Blank lines and `#` / `//` comments are ignored:

```txt
uSdf 0
uDrip 1.0
uAmp 0.5
uBlur 0.05
uFreq 0.3, 0.3, 0.2
```

`uSdf` is special: it is the texture unit the SDF texture is bound to (usually
`0`). The other names are set as shader uniforms. The defaults are the values
shown above, so the effect still runs if `config.txt` is missing.

### How the text SDF works

1. The text is rendered white-on-black at 2x and downsampled for a smooth mask.
2. A centred crop is taken with padding, and a Euclidean distance transform
   produces a signed distance: positive outside the glyphs, negative inside.
3. The distance is encoded into a single-channel float texture as
   `0.5 - signed_distance / (2 * range)`, i.e. `0.5` at the glyph edge, `1`
   inside, and `0` far outside.
4. The shader samples that texture with `uHalfSize` and decodes it with
   `uRange`. The SDF box is centred on the canvas.

The shader files target WebGL2 (`#version 300 es`). They are converted to
desktop `#version 330 core` automatically, and ES-only `precision` statements
are dropped. A missing `#version` directive is added automatically.

If a shader fails to compile at startup or during export, the error is fatal so
you never silently render a broken frame. During a preview hot reload, the error
is printed and the last working shader stays active.

## Project layout

```
renderer.py                   renderer, timeline, SDF generation, preview, CLI
export.py                     PNG frame export + GIF assembly
assets/coolfont.ttf           text font
shaders/emphemera.vert.glsl   full-screen triangle vertex shader
shaders/emphemera.frag.glsl   SDF text effect fragment shader
shaders/config.txt            shader uniform values
output/                       generated frames and GIFs
```

## How the GIF stays small

Identical consecutive frames are merged into one GIF frame with a longer
duration. The idle and hold phases therefore become a single frame each instead
of dozens of duplicates. A single shared 256-color palette is used across the
whole GIF to avoid palette flicker.
# cooltexthing
