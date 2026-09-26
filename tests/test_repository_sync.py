from mission_control.store import MissionStore
from mission_control.repository_sync import RepositorySync,RepoSnapshot

def test_repo_registry_tracks_remote_truth_and_drift(tmp_path):
 s=MissionStore(tmp_path/"db");s.initialize();r=RepositorySync(s);r.initialize()
 r.apply(RepoSnapshot("WhatsApp","ingtrader21-spec/WhatsApp","main","aaa",False,"private"))
 r.apply(RepoSnapshot("WhatsApp","ingtrader21-spec/WhatsApp","main","bbb",False,"private"))
 with s.connection() as c:
  row=c.execute("select * from repository_registry where repository='WhatsApp'").fetchone()
  assert row["remote_head"]=="bbb" and row["status"]=="SYNCED"
  assert c.execute("select count(*) n from repository_sync_events where repository='WhatsApp'").fetchone()["n"]==1
 r.mark_missing(set())
 with s.connection() as c:assert c.execute("select status from repository_registry where repository='WhatsApp'").fetchone()["status"]=="MISSING_REMOTE"
