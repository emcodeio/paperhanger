# paperhanger

A paperhanger measures, trims, and hangs wallpaper. This tool does the same for images: it takes a folder of photos, decides whether each one suits a desktop or a phone, crops tall or wide images into thirds where that yields better wallpapers, upscales with a machine-learning model only when the source is too small, resizes to the exact target, and writes HEIC files ready to hang.

It runs on macOS and is designed to need almost nothing installed: the Upscayl ncnn command-line upscaler (a single self-contained binary plus one model file) and the `sips` tool that ships with macOS.

## Status

**Design not started.** This repository currently holds:

- `docs/research/` — the research that established how to replace the previous pipeline's dependencies (Pixelmator Pro and ImageMagick), with test evidence. Read this first.
- `legacy/` — the zsh scripts and Automator workflows this project replaces, copied verbatim from the author's dotfiles for reference.

Design documents and implementation plans will land under `docs/superpowers/` as the project is built.

## Lineage

The original scripts live in `~/.dotfiles/bin/shell_scripts/make_wallpaper/` and remain in use until paperhanger replaces them. See `legacy/README.md` for what each file was.

## License

MIT. See `LICENSE`.
