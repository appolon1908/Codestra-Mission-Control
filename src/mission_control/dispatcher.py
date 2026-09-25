from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime

from .lease import LeaseManager
from .store import MissionStore


class DispatchError(RuntimeError):
    pass


@dataclass(frozen=True)
class AssignmentPacket:
    lane_id: str
    mission_id: str
    assigned_at: str
    assigned_by: str
    repository: str
    base_sha: str
    worktree: str
    goal: str
    acceptance: list[str]
    dependencies: list[str]
    do_not_touch: list[str]
    state: str
    sequence: int

    def as_handoff(self) -> dict[str, object]:
        return {
            'NEXT_TASK_ASSIGNED_DATE': self.assigned_at,
            'NEXT_MISSION_ID': self.mission_id,
            'REPOSITORY': self.repository,
            'BASE_SHA': self.base_sha,
            'LOCAL_WORKTREE/PATH': self.worktree,
            'GOAL': self.goal,
            'ACCEPTANCE': self.acceptance,
            'DEPENDENCIES': self.dependencies,
            'DO_NOT_TOUCH': self.do_not_touch,
        }


class MissionDispatcher:
    def __init__(self, store: MissionStore, *, coordinator_id: str = 'mission-control'):
        self.store = store
        self.leases = LeaseManager(store)
        self.coordinator_id = coordinator_id

    def _row_to_packet(self, row) -> AssignmentPacket:
        return AssignmentPacket(
            lane_id=row['lane_id'],
            mission_id=row['mission_id'],
            assigned_at=row['assigned_at'],
            assigned_by=row['assigned_by'],
            repository=row['repository'],
            base_sha=row['base_sha'],
            worktree=row['worktree'],
            goal=row['goal'],
            acceptance=json.loads(row['acceptance_json']),
            dependencies=json.loads(row['dependencies_json']),
            do_not_touch=json.loads(row['do_not_touch_json']),
            state=row['state'],
            sequence=int(row['sequence']),
        )

    def latest_assignment(self, lane_id: str) -> AssignmentPacket | None:
        with self.store.connection() as conn:
            row = conn.execute(
                'SELECT * FROM dispatcher_assignments WHERE lane_id=? ORDER BY sequence DESC LIMIT 1',
                (lane_id,),
            ).fetchone()
        return self._row_to_packet(row) if row else None

    def assign(self, *, lane_id: str, mission_id: str, actor: str, repository: str,
               base_sha: str, worktree: str, goal: str, acceptance: list[str] | None = None,
               dependencies: list[str] | None = None, do_not_touch: list[str] | None = None,
               assigned_at: str | None = None) -> AssignmentPacket:
        if actor != self.coordinator_id:
            raise DispatchError('only coordinator may assign or advance a write lane')
        if not self.store.get_mission(mission_id):
            raise DispatchError(f'mission not found: {mission_id}')
        assigned_at = assigned_at or datetime.now(UTC).isoformat()
        acceptance = list(acceptance or [])
        dependencies = list(dependencies or [])
        do_not_touch = list(do_not_touch or [])
        with self.store.connection() as conn:
            conn.execute('BEGIN IMMEDIATE')
            lane = conn.execute('SELECT * FROM dispatcher_lanes WHERE lane_id=?', (lane_id,)).fetchone()
            if lane and lane['current_mission_id']:
                conn.execute('ROLLBACK')
                raise DispatchError(f"lane {lane_id} already has CURRENT_TASK={lane['current_mission_id']}")
            if lane:
                previous = conn.execute(
                    'SELECT assigned_at FROM dispatcher_assignments WHERE lane_id=? ORDER BY sequence DESC LIMIT 1',
                    (lane_id,),
                ).fetchone()
                if previous and assigned_at <= previous['assigned_at']:
                    conn.execute('ROLLBACK')
                    raise DispatchError('stale assignment date cannot override newer lane history')
            seq = int(lane['assignment_seq']) + 1 if lane else 1
            if lane:
                conn.execute('UPDATE dispatcher_lanes SET current_mission_id=?, assignment_seq=?, updated_at=? WHERE lane_id=?', (mission_id, seq, assigned_at, lane_id))
            else:
                conn.execute('INSERT INTO dispatcher_lanes (lane_id,current_mission_id,assignment_seq,updated_at) VALUES (?,?,?,?)', (lane_id, mission_id, seq, assigned_at))
            conn.execute(
                '''INSERT INTO dispatcher_assignments
                (lane_id,mission_id,assigned_at,assigned_by,repository,base_sha,worktree,goal,
                 acceptance_json,dependencies_json,do_not_touch_json,state,sequence)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,'CURRENT',?)''',
                (lane_id, mission_id, assigned_at, actor, repository, base_sha, worktree, goal,
                 json.dumps(acceptance), json.dumps(dependencies), json.dumps(do_not_touch), seq),
            )
            self.store._event(conn, mission_id, 'LANE_TASK_ASSIGNED', actor, {'lane_id': lane_id, 'sequence': seq, 'base_sha': base_sha})
            conn.execute('COMMIT')
        packet = self.latest_assignment(lane_id)
        assert packet is not None
        return packet

    def validate_completion(self, *, lane_id: str, mission_id: str, agent_id: str) -> None:
        lane = self.latest_assignment(lane_id)
        if lane is None or lane.mission_id != mission_id or lane.state != 'CURRENT':
            raise DispatchError('mission is not the authoritative CURRENT_TASK for lane')
        lease = self.leases.current(mission_id)
        if not lease or lease['agent_id'] != agent_id or lease['role'] != 'WRITER':
            raise DispatchError('completion requires the current WRITER lease owner')
        checkpoint = self.store.latest_checkpoint(mission_id)
        if checkpoint is None:
            raise DispatchError('completion requires a checkpoint')
        if not checkpoint['next_task_requested']:
            raise DispatchError('completion requires NEXT_TASK_REQUEST=YES')
        if checkpoint['dirty_count'] not in (0, None):
            raise DispatchError('completion requires clean or explicitly unknown dirty state')
        if not checkpoint['head_sha']:
            raise DispatchError('completion requires recorded HEAD SHA')
        tests = json.loads(checkpoint['tests_json'])
        required = {'implementation_complete','tests_complete','github_updated','linear_updated','notion_updated','handoff_posted'}
        missing = sorted(key for key in required if tests.get(key) is not True)
        if missing:
            raise DispatchError('completion evidence missing: ' + ', '.join(missing))
        if json.loads(checkpoint['blockers_json']):
            raise DispatchError('completion checkpoint still has blockers')

    def complete_current(self, *, lane_id: str, mission_id: str, agent_id: str, actor: str) -> None:
        if actor != self.coordinator_id:
            raise DispatchError('only coordinator may advance a lane')
        self.validate_completion(lane_id=lane_id, mission_id=mission_id, agent_id=agent_id)
        now = datetime.now(UTC).isoformat()
        with self.store.connection() as conn:
            conn.execute('BEGIN IMMEDIATE')
            changed = conn.execute('UPDATE dispatcher_lanes SET current_mission_id=NULL, updated_at=? WHERE lane_id=? AND current_mission_id=?', (now, lane_id, mission_id)).rowcount
            if changed != 1:
                conn.execute('ROLLBACK')
                raise DispatchError('lane current task changed during completion')
            conn.execute("UPDATE dispatcher_assignments SET state='COMPLETED' WHERE lane_id=? AND mission_id=? AND state='CURRENT'", (lane_id, mission_id))
            self.store._event(conn, mission_id, 'LANE_TASK_COMPLETED', actor, {'lane_id': lane_id})
            conn.execute('COMMIT')
