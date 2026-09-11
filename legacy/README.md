# legacy

Verbatim copies of the scripts paperhanger replaces. Source: `~/.dotfiles/bin/shell_scripts/make_wallpaper/` at dotfiles commit `fdc79c4` ("Finalized wallpaper processing scripts and removed wallpapers from this repository"). Nothing here is meant to run from this repo; it exists so the design spec can cite exact behavior.

| File | Role |
|---|---|
| `make_wallpaper.zsh` | **The current pipeline.** Drives Pixelmator Pro via AppleScript for ML Super Resolution and HEIC export; uses ImageMagick for measuring and cropping. Flags `-d`, `-p`, `-b`. |
| `check_error_images.zsh` | Rescue pass for images that landed in the `error/` folder. Simpler rules, single 4.25x ceiling, writes to `4x_upscaled/`. |
| `make_wallpaper_old.zsh` | Previous generation. Called the Automator workflows below via the `automator` command, then downscaled with ImageMagick and converted to HEIC with a second workflow. Superseded. |
| `notes.org` | Design notes for the resizing decision tree that `make_wallpaper.zsh` implements. Contains one self-contradictory line in the phone-width section. |
| `apply_super_res_wallpaper_safe.workflow` | Automator: copy to `~/Pictures/wallpaper/processing`, convert to Pixelmator format, Increase Resolution, convert to PNG, append `_super_res_output`. Superseded. |
| `apply_super_res_wallpaper_nsfw.workflow` | Identical chain with copy destination `~/Pictures/wallpaper/nsfw`. Superseded. |
| `convert_to_heic.workflow` | Automator: single Pixelmator "Change Type of Images" action to HEIC. Superseded. |

Shell aliases that pointed at the current script (`mkw`, `mkwproc`, `mkwdesk`, `mkwphone`) live in the dotfiles at `shell/old/aliases_shared.zsh`.
