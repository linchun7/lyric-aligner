#!/usr/bin/env python3
"""One default launcher for the project's confirmed listening/review UI 3.2."""
from __future__ import annotations
import argparse
import json
import sys
import threading
import webbrowser
from pathlib import Path
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from scripts import v4_human_boundary_anchor_audit_ui as ui
from lyric_aligner.review.listening import ListeningReviewApp, digest, file_ref, read, require, evaluate_review

DEFAULT_CONFIG = ROOT / "private/listening/current.json"


class TimingReviewApp:
    """Serve the existing candidate-blind manifest on UI 3.2; export is unchanged."""
    def __init__(self, directory):
        self.pack = Path(directory).resolve()
        self.manifest = read(self.pack / "manifest.json")
        m = self.manifest
        require(m.get("schema_version") == "timing-decision-review-manifest-1.0", "unsupported timing review manifest")
        require(m.get("manifest_sha256") == digest({k: v for k, v in m.items() if k != "manifest_sha256"}), "timing manifest hash mismatch")
        require(m.get("candidate_positions_hidden") is True and bool(m.get("cases")), "timing review must remain candidate-position-blind")
        require(len({c['id'] for c in m['cases']}) == len(m['cases']), "duplicate timing case")
        for c in m["cases"]:
            p = (self.pack / c["clip_file"]).resolve()
            require(p.is_relative_to(self.pack) and p.suffix.lower() == ".wav" and p.is_file(), "missing/unsafe timing clip")
        receipt = self.pack / "materialization.json"
        materialization = read(receipt)
        require(materialization.get('review_manifest_sha256') == m['manifest_sha256'], 'timing materialization belongs to another manifest')
        clips = materialization.get('clips', [])
        require(len(clips) == len(m['cases']) and {r['file'] for r in clips} == {c['clip_file'] for c in m['cases']}, 'timing materialization coverage differs')
        for r in clips:
            require(file_ref(self.pack / r['file'])['sha256'] == r['sha256'], "timing clip changed")
        self.html = ui.render_review_extension(timing_manifest=m)
        self.identity = m['manifest_sha256']

    def state(self):
        return {"ui_revision": "3.0", "ui_ux_revision": "3.2", "mode": "timing_blind_export", "total": len(self.manifest['cases']), "complete": 0}

    def save(self, payload):
        raise ValueError("Timing review exports its original response JSON; use the existing ingest verifier")


def open_app(directory, consensus=None):
    ui.verify_ui32_core()
    directory = Path(directory).resolve()
    if (directory / "review.pack.json").is_file():
        require(consensus is None, "comparison must not expose a consensus answer")
        app = ListeningReviewApp(directory)
        app.html = ui.render_review_extension()
        identity = app.identity
    elif (directory / "manifest.json").is_file() and read(directory / "manifest.json").get("schema_version") == "timing-decision-review-manifest-1.0":
        require(consensus is None, "blind timing does not accept consensus candidates")
        app = TimingReviewApp(directory)
        identity = app.identity
    elif (directory / "outer/human_audit.csv").is_file():
        app = ui.AnchorAuditApp(directory, consensus_path=consensus)
        identity = app.lock['lock_sha256']
    else:
        raise ValueError("Unsupported/legacy page. Supply a UI 3.2 review pack or original anchor/timing pack; legacy HTML is not a default entry.")
    app.service_identity = {"app": "lyric-aligner-listening", "ui_revision": "3.2", "pack": str(directory), "pack_sha256": identity,
                            "candidate_consensus_sha256": file_ref(consensus)['sha256'] if consensus else None,
                            "ui_code_sha256": file_ref(Path(ui.__file__))['sha256'], "extension_sha256": file_ref(ROOT/'scripts/ui32_review.js')['sha256'],
                            "store_sha256": file_ref(ROOT/'lyric_aligner/review/listening.py')['sha256'], "launcher_sha256": file_ref(Path(__file__))['sha256']}
    return app


def configured_pack(config):
    value = read(config)
    require(value.get('ui_revision') == '3.2', 'default configuration must select UI 3.2')
    return Path(value['pack_dir']), value


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--pack-dir', type=Path)
    p.add_argument('--config', type=Path, default=DEFAULT_CONFIG)
    p.add_argument('--candidate-consensus', type=Path)
    p.add_argument('--set-default', action='store_true')
    p.add_argument('--check', action='store_true')
    p.add_argument('--report', type=Path, help='Write descriptive paired metrics to a new JSON file; never promotes a product')
    p.add_argument('--no-open', action='store_true')
    p.add_argument('--port', type=int, default=ui.DEFAULT_PORT)
    args = p.parse_args()
    try:
        require(1024 <= args.port <= 65535, 'port must be 1024..65535; no silent fallback')
        configured = None
        directory = args.pack_dir
        if directory is None:
            directory, configured = configured_pack(args.config)
            candidate = configured.get('candidate_consensus')
            if candidate and args.candidate_consensus is None:
                require(file_ref(candidate['path'])['sha256'] == candidate['sha256'], 'configured consensus changed')
                args.candidate_consensus = Path(candidate['path'])
        app = open_app(directory, args.candidate_consensus)
        if configured:
            require(configured['pack_sha256'] == app.service_identity['pack_sha256'], 'default pack has changed; explicitly select the new pack')
        if args.report:
            result = evaluate_review(directory)
            with args.report.open('x',encoding='utf-8') as f:
                json.dump(result,f,ensure_ascii=False,indent=2)
            print(json.dumps(result,ensure_ascii=False))
            return 0
        if args.set_default:
            require(args.pack_dir is not None, '--set-default requires --pack-dir')
            args.config.parent.mkdir(parents=True, exist_ok=True)
            temp = args.config.with_suffix('.tmp')
            pointer = {'ui_revision':'3.2','pack_dir':str(app.pack),'pack_sha256':app.service_identity['pack_sha256']}
            if args.candidate_consensus:
                pointer['candidate_consensus'] = file_ref(args.candidate_consensus)
            temp.write_text(json.dumps(pointer,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
            temp.replace(args.config)
        print(json.dumps(app.service_identity, ensure_ascii=False), flush=True)
        if args.check:
            app.state()
            return 0
        url = f'http://{ui.HOST}:{args.port}/'
        try:
            with urlopen(url+'api/identity', timeout=1) as response:
                existing = json.load(response)
        except (OSError, ValueError):
            existing = None
        if existing == app.service_identity:
            if not args.no_open:
                webbrowser.open(url)
            print('Reused the verified existing UI 3.2 instance.', flush=True)
            return 0
        server = ui._bind_server(app, args.port)
        print('UI 3.2: '+url, flush=True)
        if not args.no_open:
            threading.Timer(.5, lambda: webbrowser.open(url)).start()
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()
        return 0
    except (ValueError, RuntimeError, OSError, KeyError) as e:
        print('UI 3.2 launch failed: '+str(e), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
