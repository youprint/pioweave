import re

DOCUMENTATION_BASE = "https://github.com/youprint/pioweave/tree/"      # the value in pyproject.toml; the version and "/docs" are added here


def documentation_url(url, version):
    """The Documentation URL for `version`: `url` with the tag (or "main" for a development or local version) and "/docs" added. A URL that already has them (a wheel built from
    an sdist, whose metadata was rewritten when the sdist was made) is returned unchanged."""
    if url != DOCUMENTATION_BASE:
        return url
    version = str(version)
    url_version = f"v{version}" if re.fullmatch(r"\d+(\.\d+)*(\.post\d+)?", version) else "main"
    return url + url_version + "/docs"


# This is done in a PDM build hook without specifying `dynamic = [..., "documentation"]` to put all of the static metadata into pyproject.toml, as amaranth-soc does. Tools other
# than PDM will not execute this script and will use the generic URL (the main branch of the documentation sources).
# A release version is digits and dots (and `.postN`); anything else (a dev or pre-release, a local `+...` label such as the placeholder of a build without git) points at main.
# No pdm-backend internals are imported: the vendored `packaging` of pdm-backend is a private path.
def pdm_build_initialize(context):
    urls = context.config.metadata["urls"]
    urls["Documentation"] = documentation_url(urls["Documentation"], context.config.metadata["version"])
