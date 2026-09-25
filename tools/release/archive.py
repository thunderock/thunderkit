"""Inspect every archive member before a filtered extraction into an empty directory."""

import hashlib
import json
import sys
import tarfile
import zlib
from pathlib import Path, PurePosixPath

MAX_MEMBERS = 10000
MAX_TOTAL_BYTES = 64 * 1024 * 1024
FORBIDDEN_PAX_PREFIXES = ("GNU.sparse", "SCHILY.")


def safe_path(name: str) -> str:
    """Reject ambiguous or escaping names before any normalization happens."""
    if not name or name.startswith("/") or "\\" in name:
        raise ValueError("unsafe path")
    if any(ord(char) < 32 or ord(char) == 127 for char in name):
        raise ValueError("unsafe path")
    parts = name.rstrip("/").split("/")
    if any(part in ("", ".", "..") for part in parts) or ":" in parts[0]:
        raise ValueError("unsafe path")
    return str(PurePosixPath(name))


def check_member(member: tarfile.TarInfo, destination: Path) -> str:
    """Allow only plain regular files and directories with ordinary modes."""
    name = safe_path(member.name)
    if not (member.isfile() or member.isdir()):
        raise ValueError("unsupported member type")
    if member.linkname or member.sparse is not None or member.mode & 0o7000:
        raise ValueError("unsupported member")
    if member.size < 0 or member.uid < 0 or member.gid < 0:
        raise ValueError("unsupported member")
    for key, value in member.pax_headers.items():
        if key == "path":
            safe_path(value)
        if key.startswith(FORBIDDEN_PAX_PREFIXES) or key == "linkpath":
            raise ValueError("unsupported extended header")
    tarfile.data_filter(member, str(destination))
    return name


def inspect_members(
    archive: tarfile.TarFile, destination: Path
) -> list[tarfile.TarInfo]:
    """Collect members, rejecting duplicates, limits and files reused as directories."""
    members: list[tarfile.TarInfo] = []
    seen: set[str] = set()
    files: set[str] = set()
    total = 0
    for member in archive:
        name = check_member(member, destination)
        if name in seen:
            raise ValueError("duplicate member")
        total += member.size
        if total > MAX_TOTAL_BYTES or len(members) >= MAX_MEMBERS:
            raise ValueError("archive limit exceeded")
        seen.add(name)
        if member.isfile():
            files.add(name)
        members.append(member)
    for name in seen:
        if any(str(parent) in files for parent in PurePosixPath(name).parents):
            raise ValueError("file used as directory")
    manifest = next((m for m in members if m.name in ("package.json", "package/package.json")), None)
    stream = None if manifest is None else archive.extractfile(manifest)
    if stream is None or not isinstance(json.loads(stream.read().decode("utf8")), dict):
        raise ValueError("invalid package manifest")
    return members


def describe(destination: Path, members: list[tarfile.TarInfo]) -> dict[str, object]:
    """Report extracted regular files with sizes, modes and SHA-256 digests."""
    root = (
        destination
        if (destination / "package.json").is_file()
        else destination / "package"
    )
    package = json.loads((root / "package.json").read_text(encoding="utf8"))
    files: list[dict[str, object]] = []
    for member in members:
        if not member.isfile():
            continue
        path = destination / member.name
        info = path.lstat()
        files.append(
            {
                "path": member.name,
                "size": info.st_size,
                "mode": info.st_mode & 0o777,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
        )
    files.sort(key=lambda entry: str(entry["path"]))
    return {
        "name": package.get("name"),
        "version": package.get("version"),
        "bin": package.get("bin"),
        "files": files,
    }


def inspect_archive(tarball: Path, destination: Path) -> dict[str, object]:
    """Extract only vetted regular files and directories beneath an empty root."""
    if sys.version_info[:2] != (3, 12):
        raise ValueError("unsupported Python")
    if (
        destination.is_symlink()
        or not destination.is_dir()
        or any(destination.iterdir())
    ):
        raise ValueError("destination is not empty")
    if tarball.is_symlink() or not tarball.is_file():
        raise ValueError("archive is not a regular file")
    with tarfile.open(tarball, "r:*") as archive:
        members = inspect_members(archive, destination)
        archive.extractall(destination, members=members, filter="data")
    return describe(destination, members)


def main() -> int:
    """Emit bounded, nonsecret errors rather than archive-controlled diagnostics."""
    try:
        if len(sys.argv) != 3:
            raise ValueError("invalid arguments")
        result = inspect_archive(Path(sys.argv[1]), Path(sys.argv[2]))
        print(json.dumps(result, separators=(",", ":")))
        return 0
    except (
        OSError,
        ValueError,
        TypeError,
        KeyError,
        EOFError,
        zlib.error,
        tarfile.TarError,
    ):
        print("E_ARTIFACT: archive verification failed", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
