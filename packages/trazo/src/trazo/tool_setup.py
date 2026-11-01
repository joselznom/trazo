"""Download Pandoc, and Chrome when none is installed, into the per-user data folder and record the machine's tools.

    trazo setup          download, check and unpack Pandoc when no Pandoc is on
                            PATH and a headless Chrome when no Chrome, Edge or
                            Chromium is installed, then write machine.json
    trazo setup --plan   print what would be downloaded and where; change nothing

The Pandoc archive (a zip, or a .tar.gz on Linux) is the official release
asset for this platform, pinned below by version and sha256 (record 002);
Chrome is Chrome for Testing's headless shell (record 009). Each is
downloaded to a temporary file in the
data folder, checked, unpacked into <data>/pandoc/<version>/ or
<data>/chrome/<version>/ and deleted; temporary folders an interrupted run
left are removed first, and so are the folders of other versions once the
new one is unpacked.
Nothing is written outside the data folder (trazo.machine); PATH and
shell profiles are not changed. A Pandoc on PATH or where Homebrew puts it,
and an installed Chrome, Edge or Chromium, are used instead of downloading;
a second run with them present only refreshes machine.json. A failed Pandoc
download exits 1; Chrome is optional, so a failed one is only reported.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import platform
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import urllib.request
import zipfile
from collections.abc import Callable, Iterator
from functools import partial
from pathlib import Path
from typing import IO, NamedTuple, cast

from . import machine


class Asset(NamedTuple):
    name: str     # release file name
    size: int     # bytes
    sha256: str
    binary: str   # the executable inside the archive
    unpacked: int  # bytes once unpacked


PANDOC_VERSION = "3.9.0.2"  # the version CI pins (scripts/ci-versions.sh)
PANDOC_URL = "https://github.com/jgm/pandoc/releases/download/{version}/{name}"
# (platform.system(), platform.machine()) -> asset. Sizes and sha256 are the
# release's own (api.github.com/repos/jgm/pandoc/releases/tags/3.9.0.2,
# "digest"), checked by downloading and hashing each zip on 2026-10-22 and
# each Linux tarball on 2026-10-31.
PANDOC_ASSETS = {
    ("Darwin", "arm64"): Asset(
        "pandoc-3.9.0.2-arm64-macOS.zip", 36687858,
        "6e9eca844076bcbb599bbeebbba78a70f93b5307782b85c2c272872812c88875",
        "pandoc-3.9.0.2-arm64/bin/pandoc", 188036330),
    ("Darwin", "x86_64"): Asset(
        "pandoc-3.9.0.2-x86_64-macOS.zip", 25764704,
        "b9fbceabccbc8f34ac021a50483fc32f8160568d0b4b2c22d81bb29e3054fd82",
        "pandoc-3.9.0.2-x86_64/bin/pandoc", 120132426),
    ("Windows", "AMD64"): Asset(
        "pandoc-3.9.0.2-windows-x86_64.zip", 41250425,
        "c97542f2800f446e788d9f74237856d995421ad1bb3cc8324286840c5f272d3a",
        "pandoc-3.9.0.2/pandoc.exe", 231506020),
    ("Linux", "x86_64"): Asset(
        "pandoc-3.9.0.2-linux-amd64.tar.gz", 34520947,
        "a69abfababda8a56969a254b09f9553a7be89ddec00d4e0fe9fd585d71a67508",
        "pandoc-3.9.0.2/bin/pandoc", 161708607),
    ("Linux", "aarch64"): Asset(
        "pandoc-3.9.0.2-linux-arm64.tar.gz", 36948718,
        "b6d21e8f9c3b15744f5a7ab40248019157ed7793875dbe0383d4c82ff572b528",
        "pandoc-3.9.0.2/bin/pandoc", 173231343),
}
CHROME_VERSION = "156.0.8078.73"  # Chrome for Testing's stable channel on 2026-10-29
CHROME_URL = "https://storage.googleapis.com/chrome-for-testing-public/{version}/{platform}/{name}"
# The headless shell (record 009). Chrome for Testing publishes no digests:
# sizes and sha256 are those of the zips as downloaded on 2026-10-29 (Linux
# on 2026-10-31). There is no Linux arm64 build.
CHROME_ASSETS = {
    ("Darwin", "arm64"): Asset(
        "chrome-headless-shell-mac-arm64.zip", 103949688,
        "512c7d34cd5b65d617a39a48915b490eed4208a63e9d47b0fd7893e83be85765",
        "chrome-headless-shell-mac-arm64/chrome-headless-shell", 213311055),
    ("Darwin", "x86_64"): Asset(
        "chrome-headless-shell-mac-x64.zip", 109508986,
        "be798095cd693357a7a6a0e7a0c4ebd81563e16f51225eb98aacc906d6326519",
        "chrome-headless-shell-mac-x64/chrome-headless-shell", 217459239),
    ("Linux", "x86_64"): Asset(
        "chrome-headless-shell-linux64.zip", 126938225,
        "77eb8d52798efc7a857ab61abcc62a456d8ea9635b02341d06dffee2761768c8",
        "chrome-headless-shell-linux64/chrome-headless-shell", 287433281),
    # Windows has Edge, but an EEA user may remove it.
    ("Windows", "AMD64"): Asset(
        "chrome-headless-shell-win64.zip", 125506562,
        "e6d26810df71efd0a3dfd0f7246d75d279ebdf8a8ec153d1ac44069657298628",
        "chrome-headless-shell-win64/chrome-headless-shell.exe", 294930633),
}
QUARANTINE = "com.apple.quarantine"


class SetupError(Exception):
    """A download, check or unpack failed."""


def platform_key(system: str | None = None, machine_name: str | None = None) -> tuple[str, str]:
    """(platform.system(), platform.machine()) as the asset tables name them."""
    system = system or platform.system()
    machine_name = machine_name or machine.machine_name()
    if system == "Windows" and machine_name.lower() in ("amd64", "x86_64"):
        machine_name = "AMD64"
    return system, machine_name


def pandoc_asset(system: str | None = None, machine_name: str | None = None) -> Asset | None:
    """The Pandoc zip for this platform; None where trazo downloads none."""
    return PANDOC_ASSETS.get(platform_key(system, machine_name))


def chrome_asset(system: str | None = None, machine_name: str | None = None) -> Asset | None:
    """The Chrome zip for this platform; None where trazo downloads none."""
    return CHROME_ASSETS.get(platform_key(system, machine_name))


def pandoc_folder() -> Path:
    return machine.data_dir() / "pandoc" / PANDOC_VERSION


def pandoc_binary(asset: Asset) -> Path:
    return pandoc_folder() / asset.binary


def chrome_folder() -> Path:
    return machine.data_dir() / "chrome" / CHROME_VERSION


def chrome_binary(asset: Asset) -> Path:
    return chrome_folder() / asset.binary


def chrome_url(asset: Asset) -> str:
    """The zip's address: Chrome for Testing keeps each platform in its own folder, named in the zip."""
    where = asset.name.removeprefix("chrome-headless-shell-").removesuffix(".zip")
    return CHROME_URL.format(version=CHROME_VERSION, platform=where, name=asset.name)


def megabytes(size: int) -> str:
    return f"{size / 1e6:.1f} MB"


def log(text: str) -> None:
    print(text, flush=True)


def download(url: str, target: Path, asset: Asset) -> None:
    """Fetch url into target and check its size and sha256; a wrong file is deleted."""
    digest = hashlib.sha256()
    read = 0
    try:
        with urllib.request.urlopen(url, timeout=60) as response, open(target, "wb") as out:
            while chunk := response.read(1 << 20):
                read += len(chunk)
                if read > asset.size:
                    raise SetupError(f"{url} is larger than the expected {asset.size} bytes")
                digest.update(chunk)
                out.write(chunk)
    except OSError as exc:
        target.unlink(missing_ok=True)
        raise SetupError(f"cannot download {url}: {exc}") from exc
    except SetupError:
        target.unlink(missing_ok=True)
        raise
    if read != asset.size:
        target.unlink(missing_ok=True)
        raise SetupError(f"{url}: got {read} of {asset.size} bytes; check the connection and run again")
    if digest.hexdigest() != asset.sha256:
        target.unlink(missing_ok=True)
        raise SetupError(f"{asset.name}: sha256 {digest.hexdigest()} is not the expected {asset.sha256}; "
                         "the download was deleted and nothing was installed")


def members(archive: Path) -> Iterator[tuple[str, int, str | Callable[[], IO[bytes]] | None]]:
    """(name, Unix mode, content) for each entry of a zip or a .tar.gz:
    content is None for a folder, the target for a link and an opener for
    a file (also for a zip's link on Windows, written as a file). Other tar
    entries (hard links, devices) are refused: Pandoc's tarballs have none."""
    if archive.name.endswith(".tar.gz"):
        import tarfile  # see unpack
        with tarfile.open(archive, "r:gz") as tarred:
            for entry in tarred:
                if entry.isdir() or entry.issym():
                    yield entry.name, entry.mode, entry.linkname if entry.issym() else None
                elif entry.isfile():
                    # extractfile returns None only for what is not a file.
                    yield entry.name, entry.mode, cast(Callable[[], IO[bytes]], partial(tarred.extractfile, entry))
                else:
                    raise SetupError(f"{archive.name}: {entry.name} is not a file, folder or link")
        return
    with zipfile.ZipFile(archive) as zipped:
        for member in zipped.infolist():
            mode = member.external_attr >> 16
            if member.is_dir():
                yield member.filename, mode, None
            elif stat.S_ISLNK(mode) and os.name != "nt":
                yield member.filename, mode, zipped.read(member).decode("utf-8")
            else:
                yield member.filename, mode, partial(zipped.open, member)


def unpack(archive: Path, folder: Path) -> None:
    """Unpack a zip or a .tar.gz into folder (which must not exist), keeping
    the Unix permission bits and relative symbolic links without "..":
    Pandoc's links are bare names, and such a link cannot be redirected
    outside the folder by a later one."""
    root = folder.resolve()
    tar_errors: tuple[type[Exception], ...] = ()  # a zip's errors are its callers' to catch
    if archive.name.endswith(".tar.gz"):
        # Imported here: only Linux's Pandoc is a tarball, and an import writes
        # tarfile's .pyc into the Python setup installed, which must stay as it is.
        import tarfile
        tar_errors = (tarfile.TarError,)
    try:
        for name, mode, content in members(archive):
            target = (folder / name).resolve()
            if not target.is_relative_to(root):
                raise SetupError(f"{archive.name}: {name} would be written outside {folder}")
            if content is None:
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            if isinstance(content, str):
                if os.path.isabs(content) or ".." in Path(content).parts:
                    raise SetupError(f"{archive.name}: link {name} points outside {folder}")
                target.symlink_to(content)
                continue
            with content() as source, open(target, "wb") as out:
                shutil.copyfileobj(source, out)
            if mode & 0o777 and os.name != "nt":
                target.chmod(mode & 0o755)
    except tar_errors as exc:
        raise SetupError(f"{archive.name}: {exc}") from exc


def quarantine(path: Path) -> str | None:
    """macOS: the com.apple.quarantine value on path, "" without one; None elsewhere."""
    if sys.platform != "darwin":
        return None
    result = subprocess.run(["/usr/bin/xattr", "-p", QUARANTINE, str(path)], capture_output=True, text=True)
    return result.stdout.strip() if result.returncode == 0 else ""


def install(asset: Asset, url: str, folder: Path, log=log) -> Path:
    """Download, check and unpack a zip into folder; the binary's path."""
    folder.parent.mkdir(parents=True, exist_ok=True)
    for leftover in folder.parent.glob(".setup-*"):  # from a run that was killed
        shutil.rmtree(leftover, ignore_errors=True)
    with tempfile.TemporaryDirectory(prefix=".setup-", dir=folder.parent) as temporary:
        archive = Path(temporary) / asset.name
        log(f"Downloading {url} ({megabytes(asset.size)})")
        started = time.monotonic()
        download(url, archive, asset)
        log(f"  sha256 checked ({time.monotonic() - started:.1f} s)")
        unpacked = Path(temporary) / folder.name
        unpack(archive, unpacked)
        if not (unpacked / asset.binary).is_file():
            raise SetupError(f"{asset.name} has no {asset.binary}")
        shutil.rmtree(folder, ignore_errors=True)  # a partial folder from an older run
        os.replace(unpacked, folder)
    log(f"Unpacked into {folder}")
    for old in folder.parent.iterdir():  # the versions an earlier trazo unpacked, now unused
        if old.is_dir() and old != folder and machine.release_key(old.name):
            shutil.rmtree(old, ignore_errors=True)
            log(f"Removed {old}")
    return folder / asset.binary


def install_pandoc(asset: Asset, log=log) -> Path:
    return install(asset, PANDOC_URL.format(version=PANDOC_VERSION, name=asset.name), pandoc_folder(), log)


def install_chrome(asset: Asset, log=log) -> None:
    """Chrome is optional: a failed download is reported and setup goes on."""
    try:
        install(asset, chrome_url(asset), chrome_folder(), log)
    except (SetupError, OSError, zipfile.BadZipFile) as exc:
        print(f"trazo setup: could not download Chrome: {exc}", file=sys.stderr)


def existing_pandoc() -> tuple[str, str]:
    """(path, where) of a Pandoc already on this machine: PATH, then where
    Homebrew puts it (a GUI app's PATH lacks it)."""
    if path := shutil.which("pandoc"):
        return path, "PATH"
    return (path, "known location") if (path := machine.known_pandoc()) else ("", "")


def download_lines(label: str, url: str, asset: Asset, folder: Path) -> list[str]:
    return [f"{label}: download {url}", f"  {megabytes(asset.size)}, sha256 {asset.sha256}",
            f"  unpacked into {folder} ({megabytes(asset.unpacked)})"]


def plan(asset: Asset | None, existing: tuple[str, str], chrome: Asset | None, browser: tuple[str, str]) -> list[str]:
    data = machine.data_dir()
    lines = [f"Data folder: {data}"]
    if existing[0]:
        lines.append(f"Pandoc: {existing[0]} ({existing[1]}); nothing to download")
    elif asset is None:
        lines.append(f"Pandoc: trazo downloads Pandoc only for macOS, Linux and Windows x64, not "
                     f"{platform.system()} {platform.machine()}; install it with the system's package manager")
    elif pandoc_binary(asset).is_file():
        lines.append(f"Pandoc {PANDOC_VERSION}: {pandoc_binary(asset)}; nothing to download")
    else:
        lines += download_lines(f"Pandoc {PANDOC_VERSION}", PANDOC_URL.format(version=PANDOC_VERSION, name=asset.name),
                                asset, pandoc_folder())
    if browser[0]:
        lines.append(f"Chrome: {browser[0]} ({browser[1]}); nothing to download")
    elif chrome is None:
        lines.append(f"Chrome: trazo downloads Chrome only for macOS, Linux x64 and Windows x64, not "
                     f"{platform.system()} {platform.machine()}; "
                     "install Google Chrome, Edge or Chromium for diagrams and PNG/PDF exports")
    else:
        lines += download_lines(f"Chrome {CHROME_VERSION} (headless)", chrome_url(chrome), chrome, chrome_folder())
    lines += [f"Machine file: {machine.machine_path()}",
              "Nothing is written outside the data folder; PATH and shell profiles are not changed."]
    return lines


def report(data: dict) -> list[str]:
    lines = []
    for tool, entry in data["tools"].items():
        if entry["path"]:
            version = f" {entry['version']}" if entry["version"] else ""
            lines.append(f"  {tool}:{version} ({entry['path']}, {entry['found']})")
        else:
            lines.append(f"  {tool}: not found")
    return lines


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="trazo setup", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--plan", action="store_true", help="print what would be downloaded and where; change nothing")
    parser.add_argument("--offline", action="store_true",
                        help="download nothing: exit 3 when Pandoc must be downloaded, "
                             "and skip Chrome, which is optional (the preview's update)")
    args = parser.parse_args(argv)
    asset = pandoc_asset()
    existing = existing_pandoc()
    chrome = chrome_asset()
    browser = machine.find_chrome()
    if args.plan:
        print("\n".join(plan(asset, existing, chrome, browser)))
        return 0
    try:
        if not existing[0] and asset is not None and not pandoc_binary(asset).is_file():
            if args.offline:
                print(f"trazo setup: offline: Pandoc {PANDOC_VERSION} must be downloaded", file=sys.stderr)
                return 3
            install_pandoc(asset)
        if not browser[0] and chrome is not None and not args.offline:
            install_chrome(chrome)
        data = machine.write()
    except (SetupError, OSError, zipfile.BadZipFile) as exc:
        print(f"trazo setup: {exc}", file=sys.stderr)
        return 1
    pandoc = data["tools"]["pandoc"]
    print(f"Wrote {machine.machine_path()}")
    print("\n".join(report(data)))
    if pandoc["path"] and (mark := quarantine(Path(pandoc["path"]))) is not None:
        print(f"  quarantine: {mark or 'none'} ({QUARANTINE} on {pandoc['path']})")
    browser_entry = data["tools"]["chrome"]
    if not browser_entry["path"]:
        print(f"  {machine.CHROME_MISSING}.\n  To turn them on: {machine.CHROME_FIX}.")
    elif not machine.chrome_starts(browser_entry["path"], browser_entry["version"]):
        print(f"  Chrome {machine.CHROME_BROKEN}.\n  To turn them on: {machine.CHROME_BROKEN_FIX}.")
    if not pandoc["path"]:
        print("trazo setup: no Pandoc found" + ("" if asset else
              "; install it with the system's package manager"), file=sys.stderr)
        return 1
    if not pandoc["version"]:
        print(f"trazo setup: {pandoc['path']} --version failed", file=sys.stderr)
        return 1
    return 0
