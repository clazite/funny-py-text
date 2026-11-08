# Dum shit i made for fun 

current font is from: https://github.com/Gissio/font_Tiny5Mono

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
## uhh 
this thing has anim state: idle, animate, hold

idle is self explanatory u get it
animate is shaders duration
hold is same as idle i just put it here cuz yeah

they're configurable, jus add options to da renderer.py or export.py `--idle` .. wuter

shader speed is `--speed` default `1.0`

## shaders 
open shaders/ duh, everythin is in it

```glsl
uniform vec2      uResolution;  // render size in px 
uniform float     uTime;        // time 
uniform int       uFrame;       // frame 
uniform float     uProgress;    // 0 idle, 0 < x < 1 animate, 1 hold
uniform sampler2D uSdf;         // sdf tex 
uniform vec2      uHalfSize;    // 1/2 size of the sdf box in p-space
uniform float     uRange;       // sdf distance range in p-space
```
(default) the config:
```txt
uSdf 0
uDrip 1.0
uAmp 1.0
uBlur 0.05
uFreq 1.3, 1.3, 1.2
```

# big thanks to 0b5vr, i borrowed his emphemera shaders thing from shadertoy, it's reallllly cool i love u 0b5vr!!!
i also modified da shaders so it's not entirely a copy and paste
