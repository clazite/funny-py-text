# Dum shit i made for fun 

## previews

<img src="output/prev1.gif" alt="Skibidi GIF preview" width="400">
<img src="output/prev2.gif" alt="Skibidi GIF preview" width="400">

default font: `assets/DepartureMono.otf`

## requirementssssss 

- `moderngl` and `numpy` gpu render stuff
- `Pillow` text rasterisation, sdf, png export, gif
- `tkinter` uhh yeah obviously

```bash
.venv/bin/python renderer.py
```
default res is 960x480

meow
`--preview-scale 1` for full size prev
use `--width` / `--height` to change yeh

keys:::::
space pause/continue
r is restart
f5 reload shaders
s save is export current frame into png in output
q/esc quit

```bash
.venv/bin/python renderer.py --text $'change ur text here to wutever\\n this is line break'
```
yeh `\\n` to new line

```bash
.venv/bin/python renderer.py --preview-fps 30
```
u can change the fps

## export as gif 

```bash
.venv/bin/python renderer.py --export output/shit.gif
## or
.venv/bin/python export.py output/shit.gif
```

## shaders 
open shaders/ duh, everythin is in it

```glsl
uniform vec2      uResolution;  // render size in px 
uniform float     uTime;        // time 
uniform int       uFrame;       // frame 
uniform float     uProgress;    // normalized progress within reveal/dissolve
uniform float     uHoldProgress; // residue progress from 0 to 1 during hold
uniform int       uPhase;       // 0 idle, 1 reveal, 2 hold, 3 dissolve
uniform int       uAlwaysVisible; // keep text visible, 1 = pulse 
uniform sampler2D uSdf;         // sdf tex 
uniform vec2      uHalfSize;    // 1/2 size of the sdf box in p-space
uniform float     uRange;       // sdf distance range in p-space
uniform int uFracDivision; // how much smokeyy u want 
uniform float uFractStep; // how detailed ur smoke is 

```
`uLead`, `uTrail`, and `uResidue` control the pulse width, trailing length,
and residue strength in the fragment shader.

# big thanks to 0b5vr, i borrowed his emphemera shaders thing from shadertoy, it's reallllly cool i love u 0b5vr!!!
i also modified da shaders so it's not entirely a copy and paste
