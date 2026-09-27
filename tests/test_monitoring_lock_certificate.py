from mission_control.monitoring_lock_certificate import snapshot
def test_monitoring_lock_uses_verified_hashes():
 x=snapshot();assert x['repository_count']==14 and x['status']=='PASS'
 assert x['hashes']['agent_preflight'].startswith('8839d2c9')
 assert x['hashes']['certificate'].startswith('b9272f5f')
 assert all(v=='NOT_PERFORMED' for v in x['publication'].values())
