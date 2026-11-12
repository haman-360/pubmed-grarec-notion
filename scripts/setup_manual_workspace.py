#!/usr/bin/env python3
"""Initialize/connect a shared folder; optionally copy the old local ledger's files."""
import argparse
import json
from pathlib import Path
from paper_pipeline.manual import Workspace, local_settings, save_settings, suggested_root
from paper_pipeline.manual_migration import migrate
from paper_pipeline.workspace import select_state_dir


def main():
    repo = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description='APIキーなしのOneDrive共有フォルダを設定')
    parser.add_argument('--root', default=suggested_root())
    parser.add_argument('--device-name', required=True)
    parser.add_argument('--copy-legacy', action='store_true')
    args = parser.parse_args()
    if not args.root or not Path(args.root).expanduser().is_absolute():
        parser.error('--rootにOneDrive内の共通フォルダの絶対パスを指定してください')
    settings = local_settings(repo)
    workspace = Workspace.create(args.root, settings['device_id'], args.device_name)
    result = migrate(workspace, select_state_dir(repo)) if args.copy_legacy else {}
    _, scan_errors = workspace.scan()
    snapshot = workspace.snapshot()
    workspace.export_view(snapshot)
    settings.update(shared_root=str(workspace.root), device_name=args.device_name)
    save_settings(repo, settings)
    print(json.dumps(dict(root=str(workspace.root), migration=result, counts=snapshot['counts'],
                         scan_errors=scan_errors), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
