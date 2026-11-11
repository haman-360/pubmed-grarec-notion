#!/usr/bin/env python3
"""Prepare private reporter configuration; the Mac app runs its updater."""
import argparse
import base64
import getpass
import json
import os
import secrets
from pathlib import Path
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

REPO = Path(__file__).resolve().parents[1]
FOLDER = REPO / '.remote_status'


def prepare(url, service_token, name):
    FOLDER.mkdir(mode=0o700, exist_ok=True)
    os.chmod(FOLDER, 0o700)
    config_path = FOLDER / 'config.json'
    if config_path.exists():
        raise ValueError('設定は作成済みです。既存の通知鍵を保持してください。')
    private_key = ec.generate_private_key(ec.SECP256R1())
    private_path = FOLDER / 'vapid-private.pem'
    with private_path.open('xb') as handle:
        os.chmod(private_path, 0o600)
        handle.write(private_key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    public_key = base64.urlsafe_b64encode(private_key.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)).rstrip(b'=').decode()
    config = dict(url=url, device_name=name, source_id=secrets.token_hex(16), reporter_token=secrets.token_urlsafe(32),
                  service_token=service_token, vapid_private_key=str(private_path), vapid_public_key=public_key)
    with config_path.open('x') as handle:
        os.chmod(config_path, 0o600)
        json.dump(config, handle, ensure_ascii=False, indent=2)
    return config



if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--url', default='https://pubmed-progress-thama.fairkayak3.chatgpt.site')
    parser.add_argument('--device-name', default='自宅 MacBook Air')
    args = parser.parse_args()
    prepare(args.url, getpass.getpass('Sitesサービス用トークン（非表示）: '), args.device_name)
    print('設定を保存しました。サイトへ送信者設定を登録後、Macアプリを起動すると共有が始まります。')
