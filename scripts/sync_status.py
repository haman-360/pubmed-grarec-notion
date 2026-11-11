#!/usr/bin/env python3
"""One bounded status update, also called by the Mac app every minute."""
import argparse
import sys
from pathlib import Path
from paper_pipeline.remote_status import sync_once

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='件数とJSON保存完了を共有します（論文の処理・有料API依頼は行いません）')
    parser.add_argument('--config', type=Path)
    args = parser.parse_args()
    try:
        sync_once(Path(__file__).resolve().parents[1], args.config)
    except Exception:
        # Never print raw HTTP/crypto exceptions.
        print('状況共有に失敗しました。設定・ネット接続を確認してください。次回再試行します。', file=sys.stderr)
        sys.exit(1)
