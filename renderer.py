from __future__ import annotations

import argparse
import contextlib
import re
import sys
import time
from dataclasses import dataclass, replace
from pathlib import Path

import moderngl
import numpy as np
from PIL import Image, ImageDraw, ImageFont

DROOT = Path(__file__).resolve().parent
DEF_FON = DROOT / "assets" / "DepartureMono.otf"
DEF_VERT = DROOT / "shaders" / "emphemera.vert.glsl"
DEF_SHADER = DROOT / "shaders" / "emphemera.frag.glsl"
DEF_SHADCONF = DROOT / "shaders" / "config.txt"
DEF_OUT = DROOT / "output"
DEF_FRAMEDIR = DEF_OUT / "frames"
DEFG = DEF_OUT / "resultt.gif"

# defau vals
DEF_IDLE = 0.0
DEF_ANIMATE = 3.0
DEF_HOLD = 1.0
DEF_DISSOLVE = 4.0
DEF_SHADSPEED = 1.0
DEF_TEXT = "i hate python and i must code\\nHello, World!"

# when config.txt is missing
FALLBACK_SHAD: dict[str, float | tuple[float, ...]] = {
    "uDrip": 1.0,
    "uAmp": 0.5,
    "uBlur": 0.08,
    "uFreq": (1.3, 1.3, 1.2),
}

# Internal pass that copies the render target into a second framebuffer while
# flipping it vertically. OpenGL framebuffers are bottom-up, but PIL images are
# top-down; doing the flip on the GPU avoids a full-resolution CPU transpose
# (~13 ms at 960x960) on every preview frame. 

# WHO WROTE THIS? IDK PROBABLY GITHUB COPILOT IMA KEEP IT HERE SINCE I FORGET EASILY
FLIPVERT = """\
#version 330 core
out vec2 v_uv;
void main() {
    vec2 p = vec2(
        gl_VertexID == 1 ? 3.0 : -1.0,
        gl_VertexID == 2 ? 3.0 : -1.0
    );
    v_uv = p * 0.5 + 0.5;
    gl_Position = vec4(p, 0.0, 1.0);
}
"""

FLIPFRAG = """\
#version 330 core
in vec2 v_uv;
uniform sampler2D uSource;
out vec4 fragColor;
void main() {
    fragColor = texture(uSource, vec2(v_uv.x, 1.0 - v_uv.y));
}
"""


class SHADCOMERR(RuntimeError):
    """when fail to render"""


class SHADCONERR(ValueError):
    """when parse error in config.txt"""


@dataclass(frozen=True)
class FrameState:
    phase: str
    phase_time: float
    time: float
    progress: float
    idle: float
    hold: float
    phase_index: float
    clock: float


@dataclass(frozen=True)
class Timeline:
    idle: float = DEF_IDLE
    animate: float = DEF_ANIMATE
    hold: float = DEF_HOLD
    dissolve: float = DEF_DISSOLVE
    def __post_init__(self) -> None:
        if self.idle < 0:
            raise ValueError("idle duration must be >= 0")
        if self.animate <= 0:
            raise ValueError("animate duration must be > 0")
        if self.hold < 0:
            raise ValueError("hold duration must be >= 0")
        if self.dissolve <= 0:
            raise ValueError("dissolve duration must be > 0")
    @property
    def cycle(self) -> float:
        return self.idle + self.animate + self.hold + self.dissolve

    def frame_count(self, fps: int) -> int:
        return max(1, round(self.cycle * fps))

    def sample(self, t: float) -> FrameState:
        local = t % self.cycle

        if local < self.idle:
            return FrameState(
                phase="idle",
                phase_time=local,
                time=0.0,
                progress=0.0,
                idle=1.0,
                hold=0.0,
                phase_index=0.0,
                clock=t,
            )

        local -= self.idle
        if local < self.animate:
            return FrameState(
                phase="animate",
                phase_time=local,
                time=local,
                progress=local / self.animate,
                idle=0.0,
                hold=0.0,
                phase_index=1.0,
                clock=t,
            )

        local -= self.animate
        if local < self.hold:
            return FrameState(
                phase="hold", phase_time=local, time=self.animate + local,
                progress=1.0,
                idle=0.0, hold=local / self.hold,
                phase_index=2.0, clock=t,
            )

        local -= self.hold
        return FrameState(
            phase="dissolve", phase_time=local, time=self.animate + local,
            progress=local / self.dissolve, idle=0.0, hold=0.0,
            phase_index=3.0, clock=t,
        )


DEF_TIMELINE = Timeline()


@dataclass(frozen=True)
class ShaderConfig:
    path: Path
    uniforms: dict[str, float | tuple[float, ...]]
    sdf_unit: int = 0


@dataclass(frozen=True)
class TextSdf:
    width: int
    height: int
    data: np.ndarray
    half_size: tuple[float, float]
    range_p: float


@dataclass
class RenderConfig:
    width: int = 960
    height: int = 480
    text: str = DEF_TEXT
    font_path: Path = DEF_FON
    vertex_shader_path: Path = DEF_VERT
    shader_path: Path = DEF_SHADER
    shader_config_path: Path = DEF_SHADCONF
    shader_speed: float = DEF_SHADSPEED
    text_max_width: float = 0.94
    text_max_height: float = 0.62
    text_line_spacing: float = 0.0

    def __post_init__(self) -> None:
        if self.width < 16 or self.height < 16:
            raise ValueError("width and height must be at least 16 pixels")
        if self.shader_speed <= 0:
            raise ValueError("shader_speed must be > 0")
        if not (0.0 < self.text_max_width <= 1.0):
            raise ValueError("text_max_width must be in (0, 1]")
        if not (0.0 < self.text_max_height <= 1.0):
            raise ValueError("text_max_height must be in (0, 1]")
        self.font_path = Path(self.font_path)
        self.vertex_shader_path = Path(self.vertex_shader_path)
        self.shader_path = Path(self.shader_path)
        self.shader_config_path = Path(self.shader_config_path)


def ensure_version(source: str) -> str:
    source = source.lstrip("\ufeff")
    source = re.sub(
        r"^\s*#version\s+300\s+es\s*$",
        "#version 330 core",
        source,
        flags=re.MULTILINE,
    )
    source = re.sub(
        r"^\s*precision\s+(?:lowp|mediump|highp)\s+\w+\s*;\s*$",
        "",
        source,
        flags=re.MULTILINE,
    )
    if not re.search(r"^\s*#version\b", source, re.MULTILINE):
        source = "#version 330 core\n" + source
    return source


def parse_shader_config(path: Path) -> ShaderConfig:
    """read [name value] lines from config.txt into uniforms

    vec3 are written as numbers separated by comma:::: [uFreq 0.3, 0.3, 0.2]
    dont touch uSDF juuust keep it as what it is
    """

    path = Path(path)
    uniforms = dict(FALLBACK_SHAD)
    sdf_unit = 0

    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return ShaderConfig(path=path, uniforms=uniforms, sdf_unit=sdf_unit)

    for lineno, raw in enumerate(text.splitlines(), 1):
        line = re.split(r"(?:#|//)", raw, maxsplit=1)[0].strip()
        if not line:
            continue

        parts = line.split(None, 1)
        if len(parts) != 2:
            raise SHADCONERR(
                f"{path}:{lineno}: expected 'name value', got {raw!r}"
            )
        name, value_text = parts[0], parts[1].strip()

        try:
            if "," in value_text:
                value: float | tuple[float, ...] = tuple(
                    float(part) for part in value_text.split(",")
                )
            else:
                value = float(value_text)
        except ValueError as exc:
            raise SHADCONERR(
                f"{path}:{lineno}: bad value for {name}: {value_text!r}"
            ) from exc

        if name == "uSdf":
            if isinstance(value, tuple) or not float(value).is_integer():
                raise SHADCONERR(
                    f"{path}:{lineno}: uSdf must be an integer texture unit"
                )
            sdf_unit = int(value)
            if sdf_unit < 0:
                raise SHADCONERR(
                    f"{path}:{lineno}: uSdf texture unit must be >= 0"
                )
            continue

        uniforms[name] = value

    return ShaderConfig(path=path, uniforms=uniforms, sdf_unit=sdf_unit)


def _load_font(path: Path, size: int) -> ImageFont.FreeTypeFont:
    try:
        return ImageFont.truetype(str(path), size)
    except OSError as exc:
        raise FileNotFoundError(f"could not load font: {path}") from exc


def _edt_1d(f: np.ndarray) -> np.ndarray:
    """i took this from a random forum post i forgor what it was"""

    n = f.size
    d = np.empty(n, dtype=np.float64)
    v = np.zeros(n, dtype=np.int64)
    z = np.empty(n + 1, dtype=np.float64)

    k = 0
    v[0] = 0
    z[0] = -np.inf
    z[1] = np.inf

    for q in range(1, n):
        s = ((f[q] + q * q) - (f[v[k]] + v[k] * v[k])) / (2.0 * q - 2.0 * v[k])
        while s <= z[k]:
            k -= 1
            s = ((f[q] + q * q) - (f[v[k]] + v[k] * v[k])) / (
                2.0 * q - 2.0 * v[k]
            )
        k += 1
        v[k] = q
        z[k] = s
        z[k + 1] = np.inf

    k = 0
    for q in range(n):
        while z[k + 1] < q:
            k += 1
        d[q] = (q - v[k]) ** 2 + f[v[k]]
    return d


def _distance_transform(mask: np.ndarray) -> np.ndarray:
    big = 1e12
    field = np.where(mask, 0.0, big).astype(np.float64)
    for y in range(field.shape[0]):
        field[y] = _edt_1d(field[y])
    for x in range(field.shape[1]):
        field[:, x] = _edt_1d(field[:, x])
    return np.sqrt(field)


def _valfc(
    config: RenderConfig, text: str, measure: ImageDraw.ImageDraw
) -> int:
    max_width = config.width * config.text_max_width
    max_height = config.height * config.text_max_height

    lo = 8
    hi = max(8, int(config.height * 0.75))
    best_size = lo
    while lo <= hi:
        size = (lo + hi) // 2
        font = _load_font(config.font_path, size)
        spacing = max(1, int(round(size * config.text_line_spacing)))
        bbox = measure.multiline_textbbox(
            (0, 0), text, font=font, spacing=spacing, align="center"
        )
        text_width = bbox[2] - bbox[0]
        text_height = bbox[3] - bbox[1]
        if text_width <= max_width and text_height <= max_height:
            best_size = size
            lo = size + 1
        else:
            hi = size - 1
    return best_size


def buildtextsdf(config: RenderConfig) -> TextSdf:
    """rasterise text and convert it to float sdf

        since texture use same row order as image (row0 is top)
        matches shader uv in screen space
        so ye
    """

    text = config.text.replace("\\n", "\n")

    if not text.strip():
        tiny = 1.0 / config.height
        return TextSdf(
            width=1,
            height=1,
            data=np.zeros((1, 1), dtype="f4"),
            half_size=(tiny, tiny),
            range_p=tiny,
        )
    measure = ImageDraw.Draw(Image.new("L", (1, 1)))
    font_size = _valfc(config, text, measure)
    spacing = max(1, int(round(font_size * config.text_line_spacing)))
    # insert lazy downsample adn thres
    supersample = 2 if max(config.width, config.height) <= 2048 else 1
    big_width = config.width * supersample
    big_height = config.height * supersample
    big = Image.new("L", (big_width, big_height), 0)
    big_draw = ImageDraw.Draw(big)
    big_font = _load_font(config.font_path, font_size * supersample)
    big_spacing = max(1, int(round(spacing * supersample)))
    big_draw.multiline_text(
        (big_width / 2, big_height / 2),
        text,
        font=big_font,
        fill=255,
        anchor="mm",
        align="center",
        spacing=big_spacing,
    )
    mask_image = big.resize((config.width, config.height), Image.Resampling.LANCZOS)
    mask = np.asarray(mask_image) > 127

    ys, xs = np.nonzero(mask)
    if xs.size == 0:
        tiny = 1.0 / config.height
        return TextSdf(
            width=1,
            height=1,
            data=np.zeros((1, 1), dtype="f4"),
            half_size=(tiny, tiny),
            range_p=tiny,
        )
    range_px = max(8, int(round(config.height * 0.06)))
    pad = range_px + 2
    center_x = config.width // 2
    center_y = config.height // 2
    half_w = int(max(abs(int(xs.min()) - center_x),abs(int(xs.max()) - center_x)))
    half_h = int(max(abs(int(ys.min()) - center_y),abs(int(ys.max()) - center_y)))
    half_w = min(max(half_w + pad, 1), config.width)
    half_h = min(max(half_h + pad, 1), config.height)
    # center and crop the mask
    left, top = center_x - half_w, center_y - half_h
    right, bottom = center_x + half_w, center_y + half_h
    crop = np.zeros((bottom - top, right - left), dtype=bool)
    sx0, sy0 = max(0, left), max(0, top)
    sx1, sy1 = min(config.width, right), min(config.height, bottom)
    crop[sy0 - top: sy1 - top, sx0 - left: sx1 - left] = mask[sy0:sy1, sx0:sx1]
    to_inside = _distance_transform(crop)
    to_outside = _distance_transform(~crop)
    signed = to_inside - to_outside
    encoded = np.clip(0.5 - signed / (2.0 * range_px), 0.0, 1.0).astype("f4")
    return TextSdf(
        width=encoded.shape[1],
        height=encoded.shape[0],
        data=encoded,
        half_size=(
            2.0 * half_w / config.height,
            2.0 * half_h / config.height,
        ),
        range_p=2.0 * range_px / config.height,
    )


class ShaderRenderer:
    def __init__(self, config: RenderConfig):
        self.config = config
        self.ctx = moderngl.create_context(standalone=True, require=330)
        self._program: moderngl.Program | None = None
        self._vao: moderngl.VertexArray | None = None
        self._shader_mtimes: tuple[float | None, float | None] | None = None
        self._config_mtime: float | None = None
        try:
            self.ctx.viewport = (0, 0, config.width, config.height)
            self.color_texture = self.ctx.texture((config.width, config.height), 4)
            self.color_texture.filter = (moderngl.LINEAR, moderngl.LINEAR)
            self.fbo = self.ctx.framebuffer(color_attachments=[self.color_texture])
            self.display_texture = self.ctx.texture((config.width, config.height), 4)
            self.display_texture.filter = (moderngl.LINEAR, moderngl.LINEAR)
            self.display_fbo = self.ctx.framebuffer(color_attachments=[self.display_texture])
            self._flip_program = self.ctx.program(vertex_shader=FLIPVERT,fragment_shader=FLIPFRAG,)
            self._flip_vao = self.ctx.vertex_array(self._flip_program, [])
            self.text_sdf = buildtextsdf(config)
            self.sdf_texture = self.ctx.texture((self.text_sdf.width, self.text_sdf.height),1,self.text_sdf.data.tobytes(),dtype="f4")
            self.sdf_texture.filter = (moderngl.LINEAR, moderngl.LINEAR)
            self.sdf_texture.repeat_x = False
            self.sdf_texture.repeat_y = False

            self.shader_config = parse_shader_config(config.shader_config_path)
            self._config_mtime = self._mtime(config.shader_config_path)

            self._loadinitshad()
            self._shader_mtimes = self._current_shader_mtimes()
        except Exception:
            self.release()
            raise

    @staticmethod
    def _mtime(path: Path) -> float | None:
        try:
            return Path(path).stat().st_mtime
        except OSError:
            return None

    def _current_shader_mtimes(self) -> tuple[float | None, float | None]:
        return (
            self._mtime(self.config.vertex_shader_path),
            self._mtime(self.config.shader_path),
        )

    def readshads(self) -> tuple[str, str]:
        try:
            vertex_source = self.config.vertex_shader_path.read_text(
                encoding="utf-8", errors="replace"
            )
            fragment_source = self.config.shader_path.read_text(
                encoding="utf-8", errors="replace"
            )
        except OSError as exc:
            raise SHADCOMERR(f"could not read shader: {exc}") from exc
        return ensure_version(vertex_source), ensure_version(fragment_source)

    def _compile(self, vertex_source: str, fragment_source: str) -> moderngl.Program:
        try:
            return self.ctx.program(
                vertex_shader=vertex_source,
                fragment_shader=fragment_source,
            )
        except moderngl.Error as exc:
            raise SHADCOMERR(str(exc)) from exc

    def _progins(self, program: moderngl.Program) -> None:
        vao = self.ctx.vertex_array(program, [])
        old_program, old_vao = self._program, self._vao
        self._program = program
        self._vao = vao
        if old_vao is not None:
            old_vao.release()
        if old_program is not None:
            old_program.release()

    def _loadinitshad(self) -> None:
        vertex_source, fragment_source = self.readshads()
        program = self._compile(vertex_source, fragment_source)
        self._progins(program)
        print(
            "[renderer] shaders: "
            f"{self.config.vertex_shader_path} + {self.config.shader_path}"
        )

    def maybe_reload_shader(self, *, force: bool = False) -> bool:
        """reload config.txt and shader sources on new change."""

        reloaded = False

        config_mtime = self._mtime(self.config.shader_config_path)
        if force or config_mtime != self._config_mtime:
            self._config_mtime = config_mtime
            try:
                self.shader_config = parse_shader_config(
                    self.config.shader_config_path
                )
            except (OSError, SHADCONERR) as exc:
                print(
                    f"[renderer] shader config reload failed:\n{exc}",
                    file=sys.stderr,
                )
            else:
                print(
                    f"[renderer] reloaded shader config: "
                    f"{self.config.shader_config_path}"
                )
                reloaded = True

        mtimes = self._current_shader_mtimes()
        if force or mtimes != self._shader_mtimes:
            self._shader_mtimes = mtimes
            try:
                vertex_source, fragment_source = self.readshads()
                program = self._compile(vertex_source, fragment_source)
            except (OSError, SHADCOMERR) as exc:
                print(f"[renderer] shader reload failed:\n{
                      exc}", file=sys.stderr)
            else:
                self._progins(program)
                print("[renderer] reloaded shaders")
                reloaded = True

        return reloaded

    def _uniform(self, name: str, value: object) -> None:
        try:
            self._program[name].value = value  # type: ignore[index]
        except KeyError:
            pass

    def render(self, state: FrameState) -> None:
        assert self._program is not None and self._vao is not None

        self.fbo.use()
        self.ctx.viewport = (0, 0, self.config.width, self.config.height)
        self.ctx.clear(0.0, 0.0, 0.0, 1.0)

        sdf_unit = self.shader_config.sdf_unit
        self.sdf_texture.use(location=sdf_unit)

        for name, value in self.shader_config.uniforms.items():
            self._uniform(name, value)

        self._uniform("uResolution", (float(self.config.width),float(self.config.height)))
        shader_time = state.time * self.config.shader_speed
        self._uniform("uTime", float(shader_time))
        self._uniform("uFrame", int(round(shader_time * 60.0)))
        self._uniform("uProgress", float(state.progress))
        self._uniform("uHoldProgress", float(state.hold))
        self._uniform("uPhase", int(state.phase_index))
        self._uniform("uSdf", sdf_unit)
        self._uniform("uHalfSize", self.text_sdf.half_size)
        self._uniform("uRange", float(self.text_sdf.range_p))
        self._vao.render(moderngl.TRIANGLES, vertices=3)
        self.color_texture.use(location=0)
        self._flip_program["uSource"].value = 0
        self.display_fbo.use()
        self.ctx.viewport = (0, 0, self.config.width, self.config.height)
        self._flip_vao.render(moderngl.TRIANGLES, vertices=3)

    def read_image(self) -> Image.Image:
        """Read the current framebuffer into a top-left-origin RGB PIL image."""
        # github copilot wrote this i aint touching it

        self.ctx.finish()
        data = self.display_fbo.read(components=3, alignment=1)
        return Image.frombytes(
            "RGB", (self.config.width, self.config.height), data
        )

    def release(self) -> None:
        ctx = getattr(self, "ctx", None)
        if ctx is None:
            return

        for obj in (
            self._vao,
            self._program,
            getattr(self, "_flip_vao", None),
            getattr(self, "_flip_program", None),
            getattr(self, "sdf_texture", None),
            getattr(self, "fbo", None),
            getattr(self, "color_texture", None),
            getattr(self, "display_fbo", None),
            getattr(self, "display_texture", None),
        ):
            if obj is not None:
                with contextlib.suppress(Exception):
                    obj.release()

        self._vao = None
        self._program = None
        self._flip_vao = None
        self._flip_program = None
        with contextlib.suppress(Exception):
            ctx.release()
        self.ctx = None  # type: ignore[assignment]

    def __enter__(self) -> ShaderRenderer:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.release()


class PreviewWindow:
    def __init__(
        self,
        root: object,
        renderer: ShaderRenderer,
        timeline: Timeline,
        fps: int,
        *,
        preview_seconds: float | None = None,
        save_config: RenderConfig | None = None,
    ):
        import tkinter as tk
        from PIL import ImageTk

        self.tk = tk
        self.ImageTk = ImageTk
        self.root: tk.Tk = root  # type: ignore[assignment]
        self.renderer = renderer
        self.timeline = timeline
        self.fps = fps
        self.preview_seconds = preview_seconds
        self.save_config = save_config

        self.elapsed = 0.0
        self.playing = True
        self.closed = False
        self._last = time.perf_counter()
        self._photo: ImageTk.PhotoImage | None = None
        self._current_image: Image.Image | None = None
        self._fps_window_start = self._last
        self._fps_frames = 0
        self._display_fps = 0.0
        self._last_status = 0.0
        self._status_phase: str | None = None

        config = renderer.config
        self.root.title("kill me")
        self.root.configure(bg="#000000")
        self.root.resizable(False, False)

        self.label = tk.Label(
            self.root,
            bg="#000000",
            borderwidth=0,
            highlightthickness=0,
        )
        self.label.pack()

        self.status = tk.Label(
            self.root,
            text="",
            bg="#000000",
            fg="#9a9a9a",
            font=("TkDefaultFont", 9),
            anchor="w",
            padx=8,
            pady=4,
        )
        self.status.pack(fill="x")

        screen_w = self.root.winfo_screenwidth()
        screen_h = self.root.winfo_screenheight()
        scale = min(
            1.0,
            max(0.1, (screen_w - 80) / config.width),
            max(0.1, (screen_h - 120) / config.height),
            1200 / max(config.width, config.height),
        )
        self.scale = scale
        self.display_size = (max(1, round(config.width * scale)),max(1, round(config.height * scale)))
        self._photo = self.ImageTk.PhotoImage(Image.new("RGB", self.display_size, (0, 0, 0)))

        self.root.bind("<space>", self._toggle_pause)
        self.root.bind("<KeyPress-r>", self._restart)
        self.root.bind("<KeyPress-R>", self._restart)
        self.root.bind("<F5>", self._reload_shader)
        self.root.bind("<KeyPress-s>", self._save_frame)
        self.root.bind("<KeyPress-S>", self._save_frame)
        self.root.bind("<KeyPress-q>", lambda _event: self.close())
        self.root.bind("<KeyPress-Q>", lambda _event: self.close())
        self.root.bind("<Escape>", lambda _event: self.close())
        self.root.protocol("WM_DELETE_WINDOW", self.close)

        if preview_seconds is not None:
            self.root.after(max(0, int(preview_seconds * 1000)), self.close)

    def run(self) -> None:
        self._tick()
        self.root.mainloop()

    def _tick(self) -> None:
        if self.closed:
            return

        frame_start = time.perf_counter()
        if self.playing:
            self.elapsed += frame_start - self._last
        self._last = frame_start

        state = self.timeline.sample(self.elapsed)
        self.renderer.maybe_reload_shader()
        self.renderer.render(state)
        image = self.renderer.read_image()
        self._current_image = image

        if image.size != self.display_size:
            image = image.resize(self.display_size, Image.Resampling.LANCZOS)

        self._photo.paste(image)
        self.label.configure(image=self._photo)

        self._fps_frames += 1
        fps_elapsed = frame_start - self._fps_window_start
        if fps_elapsed >= 0.5:
            self._display_fps = self._fps_frames / fps_elapsed
            self._fps_window_start = frame_start
            self._fps_frames = 0
        if (
            state.phase != self._status_phase
            or frame_start - self._last_status >= 0.25
        ):
            self._status_phase = state.phase
            self._last_status = frame_start
            cycle_pos = self.elapsed % self.timeline.cycle
            play_state = f"{'prog' if self.playing else 'gone':<7}"
            self.status.configure(
                text=(
                    f"{state.phase:<7}-{cycle_pos:5.2f}/{self.timeline.cycle:.2f}s"
                    f" {play_state}"
                    f" {self._display_fps:4.1f}fps"
                    " sp  R  F5  S  Q"
                )
            )

        # only sleep for part of frame that rendering dont use.
        work_seconds = time.perf_counter() - frame_start
        delay_ms = max(
            1, int(round(1000.0 / self.fps - work_seconds * 1000.0)))
        self.root.after(delay_ms, self._tick)

    def _toggle_pause(self, _event: object = None) -> None:
        self.playing = not self.playing
        self._last = time.perf_counter()
        self._last_status = 0.0

    def _restart(self, _event: object = None) -> None:
        self.elapsed = 0.0
        self._last = time.perf_counter()
        self._last_status = 0.0
        self.playing = True

    def _reload_shader(self, _event: object = None) -> None:
        self.renderer.maybe_reload_shader(force=True)

    def _save_frame(self, _event: object = None) -> None:
        if self._current_image is None:
            return
        DEF_OUT.mkdir(parents=True, exist_ok=True)
        path = DEF_OUT / "preview_frame.png"

        image = self._current_image
        if self.save_config is not None and (
            image.size != (self.save_config.width, self.save_config.height)
        ):
            state = self.timeline.sample(self.elapsed)
            try:
                with ShaderRenderer(self.save_config) as full_renderer:
                    full_renderer.render(state)
                    image = full_renderer.read_image()
            except (OSError, SHADCOMERR, SHADCONERR) as exc:
                print(
                    f"[renderer] full-size save failed, saving preview frame:\n{
                        exc}",
                    file=sys.stderr,
                )

        image.save(path)
        print(f"[renderer] saved {path} ({image.width}x{image.height})")

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        with contextlib.suppress(self.tk.TclError):
            self.root.destroy()


def run_preview(
    config: RenderConfig,
    timeline: Timeline,
    fps: int,
    *,
    preview_seconds: float | None = None,
    save_config: RenderConfig | None = None,
) -> int:
    try:
        import tkinter as tk
        from PIL import ImageTk  # noqa: F401
    except ImportError as exc:
        print(
            f"Preview requires tkinter and Pillow's ImageTk support ({exc}). "
            "Use --export to render a GIF instead.",
            file=sys.stderr,
        )
        return 1

    try:
        renderer = ShaderRenderer(config)
    except (OSError, SHADCOMERR, SHADCONERR) as exc:
        print(f"Could not initialise the renderer:\n{exc}", file=sys.stderr)
        return 1

    root: tk.Tk | None = None
    try:
        try:
            root = tk.Tk()
        except tk.TclError as exc:
            print(
                f"Could not open a preview window ({exc}). "
                "Use --export to render a GIF instead.",
                file=sys.stderr,
            )
            return 1

        try:
            app = PreviewWindow(
                root,
                renderer,
                timeline,
                fps,
                preview_seconds=preview_seconds,
                save_config=save_config,
            )
            app.run()
        finally:
            with contextlib.suppress(tk.TclError):
                if root.winfo_exists():
                    root.destroy()
    finally:
        renderer.release()
    return 0



def add_common_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--width", type=int, default=960, help="render width in px (default: 960)"
    )
    parser.add_argument(
        "--height", type=int, default=480, help="render height in px (default: 480)"
    )
    parser.add_argument("--fps", type=int, default=25,
                        help="export's fps (default: 25)")
    parser.add_argument(
        "--text",
        default=DEF_TEXT,
        help=(
            "text to render; use \\n for a line break fuck u "
            f"(default: {DEF_TEXT})"
        ),
    )
    parser.add_argument("--font", type=Path,
                        default=DEF_FON, help="TTF/OTF font path")
    parser.add_argument(
        "--shader",
        type=Path,
        default=DEF_SHADER,
        help="fragment shader path (default: shaders/emphemera.frag.glsl)",
    )
    parser.add_argument(
        "--vertex-shader",
        type=Path,
        default=DEF_VERT,
        help="vertex shader path (default: shaders/emphemera.vert.glsl)",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=DEF_SHADCONF,
        help="shader uniform config path (default: shaders/config.txt)",
    )
    parser.add_argument(
        "--idle",
        type=float,
        default=DEF_TIMELINE.idle,
        help=f"idle seconds before the animation (default: {
            DEF_TIMELINE.idle:g})",
    )
    parser.add_argument(
        "--animate",
        type=float,
        default=DEF_TIMELINE.animate,
        help=f"animation duration in seconds (default: {
            DEF_TIMELINE.animate:g})",
    )
    parser.add_argument(
        "--hold",
        type=float,
        default=DEF_TIMELINE.hold,
        help=f"wait duration before dissolve (default: {DEF_TIMELINE.hold:g})",
    )
    parser.add_argument(
        "--dissolve",
        type=float,
        default=DEF_TIMELINE.dissolve,
        help=f"dissolve duration (default: {DEF_TIMELINE.dissolve:g})",
    )
    parser.add_argument(
        "--speed",
        type=float,
        default=DEF_SHADSPEED,
        help=(
            "shader internal time multiplier; lower is slower "
            f"(default: {DEF_SHADSPEED:g})"
        ),
    )


def config_from_args(args: argparse.Namespace) -> tuple[RenderConfig, Timeline]:
    try:
        timeline = Timeline(
            idle=args.idle, animate=args.animate, hold=args.hold,
            dissolve=args.dissolve)
        config = RenderConfig(
            width=args.width,
            height=args.height,
            text=args.text,
            font_path=args.font.expanduser(),
            vertex_shader_path=args.vertex_shader.expanduser(),
            shader_path=args.shader.expanduser(),
            shader_config_path=args.config.expanduser(),
            shader_speed=args.speed,
        )
    except ValueError as exc:
        raise ValueError(str(exc)) from exc

    if args.fps <= 0:
        raise ValueError("--fps must be > 0")
    if not config.font_path.exists():
        raise ValueError(f"font not found: {config.font_path}")
    if not config.vertex_shader_path.exists():
        raise ValueError(f"vertex shader not found: {
                         config.vertex_shader_path}")
    if not config.shader_path.exists():
        raise ValueError(f"fragment shader not found: {config.shader_path}")
    return config, timeline


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="renderer.py",
        description=(
            "open preview window for da shaders"
            "use --export to write PNG frames and result as GIF"
        ),
    )
    add_common_arguments(parser)
    parser.add_argument(
        "--export",
        "--gif",
        dest="export",
        type=Path,
        metavar="GIF",
        help="it literally says what it means dawg what the fuck",
    )
    parser.add_argument(
        "--cycles",
        type=int,
        default=1,
        help="number of idle/animate/hold cycles to export (default: 1)",
    )
    parser.add_argument(
        "--frames-dir",
        type=Path,
        default=DEF_FRAMEDIR,
        help="directory for exported PNG frames (default: output/frames)",
    )
    parser.add_argument(
        "--no-keep-frames",
        action="store_true",
        help="delete PNG frames after the GIF is made",
    )
    parser.add_argument(
        "--no-dither",
        action="store_true",
        help="disable GIF dithering",
    )
    parser.add_argument(
        "--preview-fps",
        type=int,
        default=60,
        help="preview fps (default: 60)",
    )
    parser.add_argument(
        "--preview-scale",
        type=float,
        default=0.65,
        help=(
            "preview window scale (default: 0.65) "
            "Export always uses --width/--height."
        ),
    )
    parser.add_argument(
        "--preview-seconds",
        type=float,
        default=None,
        help="auto close window after amount of seconds",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        config, timeline = config_from_args(args)
    except ValueError as exc:
        parser.error(str(exc))

    if args.export is not None:
        if args.cycles < 1:
            parser.error("--cycles must be >= 1")
        from export import export_gif

        try:
            export_gif(
                config,
                timeline,
                fps=args.fps,
                cycles=args.cycles,
                gif_path=args.export.expanduser(),
                frames_dir=args.frames_dir.expanduser(),
                keep_frames=not args.no_keep_frames,
                dither=not args.no_dither,
            )
        except (OSError, SHADCOMERR, SHADCONERR) as exc:
            print(f"Render error:\n{exc}", file=sys.stderr)
            return 1
        except KeyboardInterrupt:
            print("\nCancelled.", file=sys.stderr)
            return 130
        return 0

    if args.preview_fps <= 0:
        parser.error("--preview-fps must be > 0")
    if not 0.0 < args.preview_scale <= 1.0:
        parser.error("--preview-scale must be in (0, 1]")

    preview_config = config
    if args.preview_scale != 1.0:
        preview_config = replace(
            config,
            width=max(16, round(config.width * args.preview_scale)),
            height=max(16, round(config.height * args.preview_scale)),
        )

    return run_preview(
        preview_config,
        timeline,
        args.preview_fps,
        preview_seconds=args.preview_seconds,
        save_config=config,
    )


if __name__ == "__main__":
    sys.modules["renderer"] = sys.modules[__name__]
    raise SystemExit(main())
