"""trazo setup's pinned downloads: which Pandoc and Chrome archive each platform gets."""

import unittest

import _support  # noqa: F401  (sets TRAZO_ROOT first)

from trazo import tool_setup


class AssetTests(unittest.TestCase):
    def test_platform_to_asset(self):
        self.assertEqual(tool_setup.pandoc_asset("Darwin", "arm64").name, "pandoc-3.9.0.2-arm64-macOS.zip")
        self.assertEqual(tool_setup.pandoc_asset("Darwin", "x86_64").name, "pandoc-3.9.0.2-x86_64-macOS.zip")
        for name in ("AMD64", "x86_64"):
            self.assertEqual(tool_setup.pandoc_asset("Windows", name).name, "pandoc-3.9.0.2-windows-x86_64.zip")
        self.assertEqual(tool_setup.pandoc_asset("Linux", "x86_64").name, "pandoc-3.9.0.2-linux-amd64.tar.gz")
        self.assertEqual(tool_setup.pandoc_asset("Linux", "aarch64").name, "pandoc-3.9.0.2-linux-arm64.tar.gz")
        for system, name in (("Linux", "riscv64"), ("Windows", "ARM64"), ("Darwin", "ppc")):
            self.assertIsNone(tool_setup.pandoc_asset(system, name))

    def test_pins_are_consistent(self):
        for asset in tool_setup.PANDOC_ASSETS.values():
            self.assertIn(tool_setup.PANDOC_VERSION, asset.name)
            self.assertRegex(asset.sha256, "^[0-9a-f]{64}$")
            self.assertTrue(asset.binary.endswith(("/pandoc", "/pandoc.exe")))

    def test_chrome_platform_to_asset_and_url(self):
        base = "https://storage.googleapis.com/chrome-for-testing-public/" + tool_setup.CHROME_VERSION
        arm, intel = tool_setup.chrome_asset("Darwin", "arm64"), tool_setup.chrome_asset("Darwin", "x86_64")
        self.assertEqual(tool_setup.chrome_url(arm), f"{base}/mac-arm64/chrome-headless-shell-mac-arm64.zip")
        self.assertEqual(tool_setup.chrome_url(intel), f"{base}/mac-x64/chrome-headless-shell-mac-x64.zip")
        linux = tool_setup.chrome_asset("Linux", "x86_64")
        self.assertEqual(tool_setup.chrome_url(linux), f"{base}/linux64/chrome-headless-shell-linux64.zip")
        win = tool_setup.chrome_asset("Windows", "x86_64")
        self.assertEqual(tool_setup.chrome_url(win), f"{base}/win64/chrome-headless-shell-win64.zip")
        # Chrome for Testing has no Linux arm64 build.
        for system, name in (("Linux", "aarch64"), ("Windows", "ARM64"), ("Darwin", "ppc")):
            self.assertIsNone(tool_setup.chrome_asset(system, name))

    def test_chrome_pins_are_consistent(self):
        for asset in tool_setup.CHROME_ASSETS.values():
            stem = asset.name.removesuffix(".zip")
            self.assertRegex(asset.sha256, "^[0-9a-f]{64}$")
            self.assertEqual(asset.binary.removesuffix(".exe"), f"{stem}/chrome-headless-shell")
            self.assertGreater(asset.unpacked, asset.size)


if __name__ == "__main__":
    unittest.main()
