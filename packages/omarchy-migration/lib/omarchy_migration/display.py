"""Text shown on a terminal that carries names from a home or an export."""


def shown(text):
    """TEXT with control characters, which could move the cursor, redraw a
    report or reach the clipboard, written as escapes."""
    return "".join(char if char.isprintable() else char.encode("unicode_escape").decode("ascii") for char in text)
