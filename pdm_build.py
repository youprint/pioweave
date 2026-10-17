from pdm.backend._vendor.packaging.version import Version


# This is done in a PDM build hook without specifying `dynamic = [..., "documentation"]` to put all of the static metadata into pyproject.toml, as amaranth-soc does. Tools other
# than PDM will not execute this script and will use the generic URL (the main branch of the documentation sources).
def pdm_build_initialize(context):
    version = Version(context.config.metadata["version"])
    if version.is_prerelease or version.local:
        url_version = "main"
    else:
        url_version = f"v{version}"
    context.config.metadata["urls"]["Documentation"] += url_version + "/docs"
