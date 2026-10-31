"""Configured figure paths through the public CLI and workspace HTTP boundary."""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import _support

from trazo.browser import CHROME
from trazo.project import PACKAGE_DIR


class CatalogTests(unittest.TestCase):
    def test_multipanel_figures_keep_parent_caption_and_embedded_diagram_uses(self):
        from trazo import preview, workspace
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder)
            (output / "manuscript.html").write_text('''
<header><figure id="fig:header"><figcaption>Header image.</figcaption></figure></header>
<figure id="fig:combined" class="subfigures">
  <figure id="fig:panel-a"><img src="figures/output/png/plot.png"><figcaption>Panel A.</figcaption></figure>
  <figure id="fig:panel-b"><img src="figures/external/example.svg"><figcaption>Panel B.</figcaption></figure>
  <figcaption>Figure 1. Combined <strong>results</strong>.</figcaption>
</figure>
<table id="tbl:results"><caption>Table 1. Results.</caption><tr><td>1</td></tr></table>
<div id="refs"><figure id="fig:reference"><figcaption>Reference image.</figcaption></figure></div>
''')
            with patch.object(preview, "DOCS", {"manuscript": "unused"}), \
                    patch.object(preview, "OUT_DIR", output):
                result = workspace.catalog()
            self.assertEqual([item["id"] for item in result["objects"]],
                             ["fig:combined", "tbl:results"])
            figure = result["objects"][0]
            self.assertEqual(figure["caption"], "Figure 1. Combined results.")
            self.assertIsNone(figure["diagram"])
            diagram = next(item for item in result["diagrams"] if item["name"] == "example")
            self.assertEqual([item["id"] for item in diagram["uses"]], ["fig:combined"])


@unittest.skipUnless(CHROME and shutil.which("pandoc"), "needs Chrome and Pandoc")
class WorkspaceTests(unittest.TestCase):
    def test_configured_figure_paths_render_export_and_resolve_in_the_workspace(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder).resolve() / "project"
            shutil.copytree(_support.FIXTURE, root)
            (root / "figures").rename(root / "art")
            with (root / "trazo.toml").open("a") as config:
                config.write('\n[paths]\nfigures="art"\ndiagrams="art/diagrams"\n'
                             'diagram_output="art/published"\nfigure_tokens="art/style/tokens.json"\n'
                             'figure_fonts="art/fonts"\nfigure_embeds="art/output/svg"\n')
            source = root / "manuscript/main/02_figure.md"
            source.write_text('# Figure\n\n![Configured figure.](art/published/example.svg){#fig:configured}\n')
            code = '''import json, threading
from urllib.request import urlopen
from trazo import cli, preview, server
from trazo.local_server import LocalServer
assert cli.main(['diagram', 'export', 'example', '--stale']) == 0
assert preview.build('manuscript')
with LocalServer(('127.0.0.1', 0), server.Handler) as app:
    thread = threading.Thread(target=app.serve_forever, daemon=True)
    thread.start()
    base = f'http://127.0.0.1:{app.server_port}'
    try:
        payload = json.load(urlopen(base + '/__diagrams/payload/example'))
        catalog = json.load(urlopen(base + '/__catalog'))
        exported = urlopen(base + '/art/published/example.svg').read()
        page = urlopen(base + '/diagrams').read().decode()
        print(json.dumps({'font_url': payload['font_url'],
            'diagram': next(o['diagram'] for o in catalog['objects'] if o['id'] == 'fig:configured'),
            'exported': b'<svg' in exported, 'configured_in_page': 'art/fonts' in page}))
    finally:
        app.shutdown()
        thread.join()
'''
            env = {**os.environ, "TRAZO_ROOT": str(root), "PYTHONPATH": str(PACKAGE_DIR.parent)}
            result = subprocess.run([sys.executable, "-c", code], cwd=root, env=env,
                                    capture_output=True, text=True, timeout=60)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            state = json.loads(result.stdout.splitlines()[-1])
            self.assertEqual(state, {"font_url": "/art/fonts/", "diagram": "example",
                                    "exported": True, "configured_in_page": True})
