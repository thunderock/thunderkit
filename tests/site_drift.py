#!/usr/bin/env python3
"""Compare a disposable build with the complete published tree without repairing it."""
import contextlib
from html.parser import HTMLParser
import importlib.util
import io
from pathlib import Path
import sys
import tempfile
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.dont_write_bytecode = True
spec = importlib.util.spec_from_file_location("site_build", ROOT / "site/build.py")
assert spec is not None and spec.loader is not None
site_build = importlib.util.module_from_spec(spec)
spec.loader.exec_module(site_build)


class Links(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[str] = []
        self.ids: set[str] = set()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        for key, value in attrs:
            if value is not None:
                if key in ("href", "src"):
                    self.links.append(value)
                if key == "id":
                    self.ids.add(value)


def public_files(root: Path) -> dict[str, Path]:
    return {p.relative_to(root).as_posix(): p for p in sorted(root.rglob("*"))
            if p.is_file() or p.is_symlink()}


def validate_links(out: Path) -> list[str]:
    root = out.resolve()
    errors: list[str] = []
    pages: dict[Path, Links] = {}
    for name, path in public_files(root).items():
        if path.is_symlink():
            errors.append(f"symlink in public output: {name}")
        elif path.suffix == ".html":
            parser = Links()
            parser.feed(path.read_text(encoding="utf-8"))
            pages[path] = parser
    for path, parser in pages.items():
        for href in parser.links:
            label = f"{path.relative_to(root)}: {href}"
            try:
                parts = urlsplit(site_build.safe_url(href))
            except ValueError:
                errors.append(f"unsafe link: {label}")
                continue
            if parts.scheme:
                continue
            target = (path.parent / unquote(parts.path)).resolve() if parts.path else path
            if not target.is_relative_to(root):
                errors.append(f"escaping link: {label}")
            elif not target.is_file():
                errors.append(f"missing link target: {label}")
            elif parts.fragment and target in pages and unquote(parts.fragment) not in pages[target].ids:
                errors.append(f"missing link anchor: {label}")
    return errors


def check(root: Path = ROOT) -> list[str]:
    published = root / "site/_site"
    errors: list[str] = []
    with tempfile.TemporaryDirectory(prefix="site-drift-") as temporary:
        generated = Path(temporary)
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                site_build.build(generated, root)
        except (OSError, ValueError) as error:
            return [f"build failed: {error}"]
        errors.extend(validate_links(generated))
        errors.extend(validate_links(published))
        expected, actual = public_files(generated), public_files(published)
        errors.extend(f"missing: {name}" for name in sorted(expected.keys() - actual.keys()))
        errors.extend(f"extra: {name}" for name in sorted(actual.keys() - expected.keys()))
        for name in sorted(expected.keys() & actual.keys()):
            if not actual[name].is_symlink() and expected[name].read_bytes() != actual[name].read_bytes():
                errors.append(f"changed: {name}")
    return errors


def main() -> int:
    try:
        errors = check()
    except (OSError, ValueError) as error:
        errors = [f"cannot compare public output: {error}"]
    if errors:
        print("FAIL: site drift — committed output does not match a valid current build")
        for error in errors:
            print(f"  {error}")
        return 1
    print("OK: site drift gate — complete public file set, contents and local links match")
    return 0


if __name__ == "__main__":
    sys.exit(main())
