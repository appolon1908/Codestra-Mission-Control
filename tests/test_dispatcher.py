import pytest

from mission_control.dispatcher import DispatchError, MissionDispatcher
from mission_control.lease import LeaseConflict, LeaseManager
from mission_control.models import Mission
from mission_control.store import MissionStore


def make_store(tmp_path):
    store = MissionStore(tmp_path / 'mission.db')
    store.initialize()
    for mid in ('PAS-1','PAS-2'):
        store.upsert_mission(Mission(mid, 'repo', f'goal {mid}'))
    return store

def evidence():
    return {'implementation_complete': True, 'tests_complete': True, 'github_updated': True, 'linear_updated': True, 'notion_updated': True, 'handoff_posted': True}

def assign(d, mission='PAS-1', when=None):
    return d.assign(lane_id='agent-a', mission_id=mission, actor='mission-control', repository='repo', base_sha='a'*40, worktree='/tmp/wt', goal='finish', acceptance=['green'], dependencies=[], do_not_touch=['main'], assigned_at=when)

def test_one_current_task_per_lane(tmp_path):
    d=MissionDispatcher(make_store(tmp_path)); assign(d)
    with pytest.raises(DispatchError, match='already has CURRENT_TASK'): assign(d,'PAS-2')

def test_agent_cannot_self_assign_successor(tmp_path):
    d=MissionDispatcher(make_store(tmp_path))
    with pytest.raises(DispatchError, match='only coordinator'):
        d.assign(lane_id='agent-a', mission_id='PAS-1', actor='builder', repository='repo', base_sha='a'*40, worktree='/tmp/wt', goal='x')

@pytest.mark.parametrize('next_requested,tests,head,dirty,blockers', [(False,evidence(),'b'*40,0,[]),(True,{},'b'*40,0,[]),(True,evidence(),None,0,[]),(True,evidence(),'b'*40,1,[]),(True,evidence(),'b'*40,0,['blocked'])])
def test_completion_requires_full_evidence(tmp_path,next_requested,tests,head,dirty,blockers):
    store=make_store(tmp_path); d=MissionDispatcher(store); assign(d); LeaseManager(store).claim('PAS-1','builder')
    store.record_checkpoint('PAS-1','builder','DONE',head_sha=head,dirty_count=dirty,tests=tests,blockers=blockers,next_task_requested=next_requested)
    with pytest.raises(DispatchError): d.complete_current(lane_id='agent-a',mission_id='PAS-1',agent_id='builder',actor='mission-control')

def test_coordinator_dispatches_successor_and_latest_is_resume_point(tmp_path):
    store=make_store(tmp_path); d=MissionDispatcher(store); first=assign(d,when='2026-09-20T10:00:00+00:00'); LeaseManager(store).claim('PAS-1','builder')
    store.record_checkpoint('PAS-1','builder','DONE',head_sha='b'*40,dirty_count=0,tests=evidence(),blockers=[],next_task_requested=True)
    d.complete_current(lane_id='agent-a',mission_id='PAS-1',agent_id='builder',actor='mission-control')
    second=assign(d,'PAS-2',when='2026-09-21T10:00:00+00:00')
    assert second.sequence == first.sequence + 1
    assert d.latest_assignment('agent-a').mission_id == 'PAS-2'
    assert second.as_handoff()['NEXT_TASK_ASSIGNED_DATE'] == '2026-09-21T10:00:00+00:00'

def test_stale_assignment_cannot_override_newer_lane_history(tmp_path):
    store=make_store(tmp_path); d=MissionDispatcher(store)
    assign(d,when='2026-09-20T10:00:00+00:00'); LeaseManager(store).claim('PAS-1','builder')
    store.record_checkpoint('PAS-1','builder','DONE',head_sha='b'*40,dirty_count=0,tests=evidence(),blockers=[],next_task_requested=True)
    d.complete_current(lane_id='agent-a',mission_id='PAS-1',agent_id='builder',actor='mission-control')
    with pytest.raises(DispatchError, match='stale assignment date'):
        assign(d,'PAS-2',when='2020-01-01T00:00:00+00:00')

def test_writer_lease_exclusivity_preserved(tmp_path):
    store=make_store(tmp_path); leases=LeaseManager(store); leases.claim('PAS-1','a')
    with pytest.raises(LeaseConflict): leases.claim('PAS-1','b')
