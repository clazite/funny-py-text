from __future__ import annotations

import argparse
import tempfile
import time
from pathlib import Path

from PIL import Image

from renderer import (
    DEF_FRAMEDIR,
    DEFG,
    RenderConfig,
    SHADCOMERR,
    SHADCONERR,
    ShaderRenderer,
    Timeline,
    add_common_arguments,
    config_from_args,
)


def export_frames(
    renderer: ShaderRenderer,
    timeline: Timeline,
    *,
    fps: int,
    cycles: int,
    frames_dir: Path,
    progress: bool = True,
) -> list[Path]:
    frames_dir = Path(frames_dir).expanduser()
    frames_dir.mkdir(parents=True, exist_ok=True)

    for stale in frames_dir.glob("frame_*.png"):
        stale.unlink()

    frampercyc = timeline.frame_count(fps)
    total = frampercyc * cycles
    paths: list[Path] = []
    started = time.perf_counter()

    for index in range(total):
        t = index / fps
        state = timeline.sample(t)
        renderer.render(state)
        image = renderer.read_image()

        path = frames_dir / f"frame_{index:05d}.png"
        image.save(path)
        paths.append(path)

        if progress:
            step = max(1, total // 20)
            if index % step == 0 or index == total - 1:
                pct = (index + 1) / total * 100.0
                print(
                    f"\rRender: {index + 1}/{total} ({pct:5.1f}%)",
                    end="",
                    flush=True,
                )

    if progress:
        elapsed = time.perf_counter() - started
        print(f"\nRendered {total} frames takes {
              elapsed:.1f}s -> {frames_dir}")

    return paths


def _frame_durations_ms(count: int, fps: int) -> list[int]:
    durations: list[int] = []
    previous_cs = 0
    for index in range(count):
        target_cs = round((index + 1) * 100 / fps)
        durations.append((target_cs - previous_cs) * 10)
        previous_cs = target_cs
    return durations


def _build_palette(frame_paths: list[Path], sample_count: int = 12) -> Image.Image:
    step = max(1, len(frame_paths) // sample_count)
    sampled_paths = frame_paths[::step][:sample_count]

    samples: list[Image.Image] = []
    for path in sampled_paths:
        with Image.open(path) as image:
            samples.append(image.convert("RGB"))

    width, height = samples[0].size
    strip = Image.new("RGB", (width, height * len(samples)))
    for index, sample in enumerate(samples):
        strip.paste(sample, (0, index * height))

    return strip.quantize(colors=256, method=Image.Quantize.MEDIANCUT)


def assemble_gif(
    frame_paths: list[Path],
    gif_path: Path,
    *,
    fps: int,
    loop: int = 0,
    collapse_identical: bool = True,
    dither: bool = True,
    progress: bool = True,
) -> Path:
    if not frame_paths:
        raise ValueError("no frames to make gif")

    gif_path = Path(gif_path).expanduser()
    gif_path.parent.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()

    palette = _build_palette(frame_paths)
    frame_durations = _frame_durations_ms(len(frame_paths), fps)
    dither_mode = (
        Image.Dither.FLOYDSTEINBERG if dither else Image.Dither.NONE
    )

    gif_frames: list[Image.Image] = []
    durations: list[int] = []
    previous_pixels: bytes | None = None
    total = len(frame_paths)

    for index, path in enumerate(frame_paths):
        with Image.open(path) as image:
            rgb = image.convert("RGB")

        frame = rgb.quantize(
            palette=palette,
            dither=dither_mode,
        )
        pixels = frame.tobytes()

        if (
            collapse_identical
            and gif_frames
            and previous_pixels == pixels
        ):
            # duplicate fram extend da previous frame instead of storing it... um yeah cuz it simply look like shit
            durations[-1] += frame_durations[index]
        else:
            gif_frames.append(frame)
            durations.append(frame_durations[index])
            previous_pixels = pixels

        if progress:
            step = max(1, total // 20)
            if index % step == 0 or index == total - 1:
                print(
                    f"\rmaking GIF: {index + 1}/{total}",
                    end="",
                    flush=True,
                )

    gif_frames[0].save(
        gif_path,
        save_all=True,
        append_images=gif_frames[1:],
        duration=durations,
        loop=loop,
        optimize=True,
        disposal=2,
    )

    if progress:
        elapsed = time.perf_counter() - started
        size_mib = gif_path.stat().st_size / (1024 * 1024)
        print(
            f"\nWrote {gif_path} "
            f"({len(gif_frames)} GIF frames, {size_mib:.1f} MiB) "
            f"in {elapsed:.1f}s"
        )

    return gif_path


def export_gif(
    config: RenderConfig,
    timeline: Timeline,
    *,
    fps: int,
    cycles: int,
    gif_path: Path,
    frames_dir: Path = DEF_FRAMEDIR,
    keep_frames: bool = True,
    dither: bool = True,
    progress: bool = True,
) -> Path:
    if cycles < 1:
        raise ValueError("cycles must be >= 1")
    if fps <= 0:
        raise ValueError("fps must be > 0")

    if progress:
        print(
            f"[export] {config.width}x{config.height} @ {fps}fps, "
            f"{timeline.cycle:.2f}s cycle x {cycles} "
            f"= {timeline.cycle * cycles:.2f}s"
        )

    renderer = ShaderRenderer(config)
    try:
        if keep_frames:
            frame_paths = export_frames(
                renderer,
                timeline,
                fps=fps,
                cycles=cycles,
                frames_dir=frames_dir,
                progress=progress,
            )
            return assemble_gif(
                frame_paths,
                gif_path,
                fps=fps,
                dither=dither,
                progress=progress,
            )

        with tempfile.TemporaryDirectory(prefix="bday_frames_") as temp_dir:
            frame_paths = export_frames(
                renderer,
                timeline,
                fps=fps,
                cycles=cycles,
                frames_dir=Path(temp_dir),
                progress=progress,
            )
            return assemble_gif(
                frame_paths,
                gif_path,
                fps=fps,
                dither=dither,
                progress=progress,
            )
    finally:
        renderer.release()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="export.py",
        description="export thing as frame in png and then make gif out of those frames duh",
    )
    add_common_arguments(parser)
    parser.add_argument(
        "--gif",
        type=Path,
        default=DEFG,
        help="output GIF path (default: output/resultt.gif)",
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
        help="delete PNG frames after GIF is made",
    )
    parser.add_argument(
        "--no-dither",
        action="store_true",
        help="disable GIF dithering",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        config, timeline = config_from_args(args)
    except ValueError as exc:
        parser.error(str(exc))

    if args.cycles < 1:
        parser.error("--cycles must be >= 1")

    try:
        export_gif(
            config,
            timeline,
            fps=args.fps,
            cycles=args.cycles,
            gif_path=args.gif.expanduser(),
            frames_dir=args.frames_dir.expanduser(),
            keep_frames=not args.no_keep_frames,
            dither=not args.no_dither,
        )
    except (OSError, SHADCOMERR, SHADCONERR) as exc:
        print(f"Error:\n{exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\ninterrupted.", file=sys.stderr)
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
