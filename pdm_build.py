import re


# This is done in a PDM build hook without specifying `dynamic = [..., "documentation"]` to put all of the static metadata into pyproject.toml, as amaranth-soc does. Tools other
# than PDM will not execute this script and will use the generic URL (the main branch of the documentation sources).
# A release version is digits and dots (and `.postN`); anything else (a dev or pre-release, a local `+...` label such as the placeholder of a build without git) points at main.
# No pdm-backend internals are imported: the vendored `packaging` of pdm-backend is a private path.
def pdm_build_initialize(context):
    version = str(context.config.metadata["version"])
    if re.fullmatch(r"\d+(\.\d+)*(\.post\d+)?", version):
        url_version = f"v{version}"
    else:
        url_version = "main"
    context.config.metadata["urls"]["Documentation"] += url_version + "/docs"
