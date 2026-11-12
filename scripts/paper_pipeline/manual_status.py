"""Share only the desktop's aggregate manual progress with the private status site."""
import json
import threading
from pathlib import Path

from .manual import SCHEMA, STAGES
from .store import now


class StatusReporter:
    def __init__(self, repo):
        self.config_path = Path(repo) / '.remote_status/config.json'
        self.pending = None
        self.lock = threading.Lock()
        self.ready = threading.Event()
        self.last_shared_at = ''
        self.message = '共有待ち' if self.config_path.is_file() else 'このMacは状況共有が未設定です'

    def submit(self, snapshot):
        if not self.config_path.is_file():
            return
        # Never queue paper data, paths, notes, credentials, or the old API ledger.
        payload = dict(workflow=SCHEMA, device_name=snapshot['device'], observed_at=now(),
                       busy=False, counts={k: snapshot['counts'][k] for k in ('total', *STAGES)},
                       last_event=None)
        with self.lock:
            self.pending = payload
        self.ready.set()

    def status(self):
        with self.lock:
            return dict(configured=self.config_path.is_file(), message=self.message,
                        last_shared_at=self.last_shared_at)

    def publish(self):
        with self.lock:
            payload = self.pending
        if payload is None:
            return
        try:
            from .remote_status import Client
            config = json.loads(self.config_path.read_text())
            Client(config).call('/api/report', dict(payload, source_id=config['source_id']))
        except Exception:
            # Keep the last observation for retry. Never log credentials or HTTP bodies.
            with self.lock:
                self.message = '共有できませんでした。通信・共有設定を確認してください。約30秒後に再試行します'
            return
        with self.lock:
            if self.pending is payload:
                self.pending = None
            self.last_shared_at = payload['observed_at']
            self.message = 'iPhone用の状況を共有しました'

    def watch(self):
        while True:
            self.ready.wait(30)
            self.ready.clear()
            self.publish()
            # Retry failures at the next observation or after 30 seconds.
