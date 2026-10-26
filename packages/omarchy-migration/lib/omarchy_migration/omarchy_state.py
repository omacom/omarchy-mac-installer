"""The theme and background a Try home chose, which Omarchy applies itself.

Omarchy keeps the current theme in ~/.local/state/omarchy/current: its name, a
folder it generates from the theme, a background link naming the home's own
path, and a generation counter. Copied files would be stale, so an import
leaves that folder alone and applies the choice with Omarchy's own commands.
"""

import re

GENERATED = (".local/state/omarchy/current",)
THEME_NAME = ".local/state/omarchy/current/theme.name"
BACKGROUND = ".local/state/omarchy/current/background"
THEME_BACKGROUNDS = ".local/state/omarchy/current/theme/backgrounds/"
NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9 ._-]{0,63}")
HOME_PATH = re.compile(r"/home/[^/]+/(.+)")


def _relative(path):
    parts = path.split("/")
    if any(part in ("", ".", "..") for part in parts) or any(ord(char) < 32 or char == "\x7f" for char in path):
        return None
    return path


def choices(bundle):
    """{"theme": name or None, "background": a path relative to the home, or None}."""
    theme = None
    data = bundle.read_file(THEME_NAME, 256)
    if data is not None:
        try:
            name = data.decode("utf-8").strip()
        except UnicodeDecodeError:
            name = ""
        theme = name if NAME.fullmatch(name) else None
    background = None
    target = bundle.link_target(BACKGROUND)
    match = HOME_PATH.fullmatch(target) if target else None
    if match and _relative(match.group(1)):
        background = match.group(1)
    return {"theme": theme, "background": background}
