from mission_control.store import MissionStore
from mission_control.repository_sync import RepositorySyncStore
def test_delivery_state_tracks_ci_cd_and_last_pr(tmp_path):
 s=MissionStore(tmp_path/'db');s.initialize();r=RepositorySyncStore(s);r.initialize()
 r.upsert_delivery('Middleware-',ci_defined=True,ci_connected=True,ci_healthy=0,cd_defined=True,production_locked=True,last_pr_number=390,last_pr_head_sha='abc',last_pr_pushed_at='2026-09-27T00:00:00Z')
 with s.connection() as c:x=c.execute("select * from repository_delivery_state where repository='Middleware-'").fetchone()
 assert x['ci_defined']==1 and x['ci_connected']==1 and x['ci_healthy']==0 and x['last_pr_number']==390
