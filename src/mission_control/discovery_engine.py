from __future__ import annotations
import json,threading,time
from datetime import UTC,datetime
from pathlib import Path
from .local_work_discovery import LocalWorkDiscovery

class DiscoveryEngine:
 def __init__(self,discovery=None,interval_seconds=30,snapshot_path=None):
  self.discovery=discovery or LocalWorkDiscovery();self.interval=max(5,interval_seconds)
  self.snapshot_path=Path(snapshot_path or "/tmp/mission-control-local-work.json");self._stop=threading.Event();self._thread=None;self.latest={"discovered_at":None,"worktrees":[]}
 def _write(self):
  tmp=self.snapshot_path.with_suffix(".tmp");tmp.write_text(json.dumps(self.latest,sort_keys=True));tmp.replace(self.snapshot_path)
 def scan_once(self):
  rows=[];started=datetime.now(UTC).isoformat()
  for repository in self.discovery.repository_names():
   try: rows.extend(self.discovery.scan(repository,48))
   except (RuntimeError,OSError): continue
   self.latest={"discovered_at":started,"completed_at":None,"state":"SCANNING","worktrees":rows.copy()};self._write()
  self.latest={"discovered_at":started,"completed_at":datetime.now(UTC).isoformat(),"state":"READY","worktrees":rows};self._write();return self.latest
 def start(self):
  if self._thread and self._thread.is_alive(): return
  def run():
   while not self._stop.is_set():
    try:self.scan_once()
    except Exception:pass
    self._stop.wait(self.interval)
  self._thread=threading.Thread(target=run,name="local-work-discovery",daemon=True);self._thread.start()
 def stop(self):self._stop.set()
