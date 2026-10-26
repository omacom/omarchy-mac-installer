"""User-facing migration categories, assigned by a home entry's top-level name.

Collection selects whole top-level roots, so a category is the set of
top-level names it owns. Credential stores are selected separately by id.
"""

CATEGORIES = ("files-and-projects", "configuration", "caches")
# Reproducible caches are omitted unless the user asks for them.
DEFAULT_SELECTED = {"files-and-projects": True, "configuration": True, "caches": False}
CACHE_ROOTS = (".cache",)


def category(path):
    top = path.split("/")[0]
    if top in CACHE_ROOTS:
        return "caches"
    return "configuration" if top.startswith(".") else "files-and-projects"
