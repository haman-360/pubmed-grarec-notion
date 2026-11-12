#!/usr/bin/env python3
"""One-shot intake; no AI/Notion calls or background polling."""
import argparse
import json
from pathlib import Path

from paper_pipeline.intake import inventory, organize, result_message, workspace_lock
from paper_pipeline.manual import Workspace, local_settings
from paper_pipeline.manual_status import StatusReporter


def main(argv=None):
    parser = argparse.ArgumentParser(description='OneDriveの取り込み待ちを一括整理')
    parser.add_argument('--root', help='設定済みの共有フォルダ（省略時はこのMacの設定）')
    args = parser.parse_args(argv)
    repo = Path(__file__).resolve().parents[1]
    settings = local_settings(repo)
    root = args.root or settings['shared_root']
    if not root:
        print('先にアプリでOneDrive共通フォルダを設定してください。')
        return 1
    try:
        workspace = Workspace(root, settings['device_id'], settings['device_name'])
        with workspace_lock(workspace):
            result = organize(workspace)
            snapshot = workspace.snapshot()
            snapshot['intake'] = inventory(workspace)
            workspace.export_view(snapshot)
        # Existing opt-in aggregate reporting only; credentials stay on this Mac.
        reporter = StatusReporter(repo)
        reporter.submit(snapshot)
        reporter.publish()
        print(result_message(result))
        for error in result['errors']:
            print('要確認: ' + error)
        print('状況ファイル: ' + str(workspace.path('状況/' + workspace.device_id)))
        print('OneDriveの同期完了はOneDriveアプリで確認してください。')
        print(reporter.status()['message'])
        return 1 if result['errors'] else 0
    except (ValueError, OSError, json.JSONDecodeError) as error:
        print('整理できませんでした: ' + str(error))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
