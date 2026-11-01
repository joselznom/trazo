"""trazo setup's unpacking of the Pandoc and Chrome archives: zips and,
for Pandoc on Linux, tarballs, and nothing written outside the folder."""

import gzip
import io
import shutil
import stat
import tarfile
import tempfile
import unittest
import zipfile
from pathlib import Path

import _support  # noqa: F401  (sets TRAZO_ROOT first)

from trazo import tool_setup


def make_zip(entries) -> bytes:
    """entries: (name, data, mode) with mode None for a directory."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, data, mode in entries:
            info = zipfile.ZipInfo(name)
            info.external_attr = (mode or (stat.S_IFDIR | 0o755)) << 16
            archive.writestr(info, data)
    return buffer.getvalue()


def make_tar(entries) -> bytes:
    """A .tar.gz of the same entries as make_zip: a link's data is its target."""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for name, data, mode in entries:
            info = tarfile.TarInfo(name.rstrip("/"))
            info.mode = (mode or 0o755) & 0o777
            if mode is None:
                info.type = tarfile.DIRTYPE
            elif stat.S_ISLNK(mode):
                info.type, info.linkname = tarfile.SYMTYPE, data.decode()
            else:
                info.size = len(data)
            archive.addfile(info, io.BytesIO(data) if info.isfile() else None)
    return buffer.getvalue()


class UnpackTests(unittest.TestCase):
    def setUp(self):
        self.folder = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.folder, True)
        self.data = self.folder / "data"

    def test_unpack_refuses_paths_and_links_outside_the_folder(self):
        link = stat.S_IFLNK | 0o777
        for entries in ([("../evil", b"x", stat.S_IFREG | 0o644)],
                        [("a/link", b"../../evil", link)],
                        [("a/link", b"/etc", link)],
                        # Each link stays inside when made; together L points outside.
                        [("d1/d2/L", b"x/../..", link), ("d1/d2/x", b"../..", link)],
                        [("d1/d2/x", b"../..", link)]):
            for make, suffix in ((make_zip, ".zip"), (make_tar, ".tar.gz")):
                archive = self.folder / f"bad{suffix}"
                archive.write_bytes(make(entries))
                with self.subTest(entries=entries, archive=suffix), self.assertRaises(tool_setup.SetupError):
                    tool_setup.unpack(archive, self.data / "out")
                shutil.rmtree(self.data / "out", ignore_errors=True)
        # A tarball's hard link could name any file the archive wrote before it, or outside it.
        buffer = io.BytesIO()
        hard = tarfile.TarInfo("a/hard")
        hard.type, hard.linkname = tarfile.LNKTYPE, "/etc/passwd"
        with tarfile.open(fileobj=buffer, mode="w:gz") as tarred:
            tarred.addfile(hard)
        archive = self.folder / "hard.tar.gz"
        archive.write_bytes(buffer.getvalue())
        with self.assertRaisesRegex(tool_setup.SetupError, "is not a file, folder or link"):
            tool_setup.unpack(archive, self.data / "out")
        self.assertFalse((self.data / "evil").exists())
        self.assertFalse((self.data.parent / "evil").exists())



    def test_a_cut_tarball_is_a_setup_error(self):
        # A member cut short inside a valid gzip fails while its data is read, not its header.
        whole = io.BytesIO()
        with tarfile.open(fileobj=whole, mode="w") as tarred:
            info = tarfile.TarInfo("a/file")
            info.size = 4096
            tarred.addfile(info, io.BytesIO(b"x" * 4096))
        archive = self.folder / "short.tar.gz"
        archive.write_bytes(gzip.compress(whole.getvalue()[:1024]))
        with self.assertRaises(tool_setup.SetupError):
            tool_setup.unpack(archive, self.data / "short")


if __name__ == "__main__":
    unittest.main()
