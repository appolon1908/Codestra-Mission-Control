from mission_control.monitoring_evidence import snapshot


def test_verified_monitoring_evidence_counts_and_payload():
 x=snapshot();assert x['governance_verified']==13 and x['preservation_verified']==4
 assert all(r['governance_commit_verified'] and len(r['governance_files'])==3 for r in x['repositories'])
 assert sum(r['dirty_root'] is False for r in x['repositories'])==4
