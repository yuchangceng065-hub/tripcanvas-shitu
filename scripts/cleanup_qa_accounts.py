#!/usr/bin/env python3
"""Remove throwaway QA accounts left by live capture / verification scripts.

The API has no account-deletion endpoint, so `capture_*` / `verify_*` rows
accumulate in the production database. This operator script deletes exactly
those accounts and their owned rows, in one transaction.

SAFETY: DRY-RUN by default. `--apply` additionally requires `--yes`.
`DATABASE_URL` must be supplied by the operator; no production credential is
stored in this repository. Never point it at a database you have not backed up.
"""
import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

DEFAULT_PREFIXES = ('capture_', 'verify_')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--prefix', action='append', dest='prefixes',
                        help='username prefix (repeatable); defaults to capture_, verify_')
    parser.add_argument('--apply', action='store_true', help='actually delete rows')
    parser.add_argument('--yes', action='store_true',
                        help='explicit confirmation required together with --apply')
    args = parser.parse_args()

    if not os.environ.get('DATABASE_URL'):
        parser.error('set DATABASE_URL explicitly; this script never reads a committed credential')
    if args.apply and not args.yes:
        parser.error('--apply requires --yes (destructive operation)')

    from sqlalchemy import delete, func, or_, select
    from backend.db import (AuthSession, PlanningSession, Run, RunEvent,
                            ScenarioBranch, SessionLocal, Share, TripRow, User,
                            UserPreference, VersionRow)

    prefixes = args.prefixes or list(DEFAULT_PREFIXES)
    # `_` is a LIKE wildcard; escape it so `capture_` matches only that prefix.
    condition = or_(*[User.username.like(prefix.replace('_', r'\_') + '%', escape='\\')
                      for prefix in prefixes])

    with SessionLocal() as session:
        users = list(session.scalars(select(User).where(condition)))
        owners = [user.id for user in users]
        run_ids = list(session.scalars(select(Run.id).where(Run.owner.in_(owners)))) if owners else []
        trip_ids = list(session.scalars(select(TripRow.id).where(TripRow.owner.in_(owners)))) if owners else []

        def count(model, column, values):
            if not values:
                return 0
            return session.scalar(
                select(func.count()).select_from(model).where(column.in_(values))) or 0

        plan = {
            'prefixes': prefixes,
            'dryRun': not args.apply,
            'accounts': len(users),
            'usernames': [user.username for user in users],
            'rows': {
                'run_events': count(RunEvent, RunEvent.run_id, run_ids),
                'versions': count(VersionRow, VersionRow.trip_id, trip_ids),
                'runs': len(run_ids),
                'trips': len(trip_ids),
                'shares': count(Share, Share.owner, owners),
                'scenario_branches': count(ScenarioBranch, ScenarioBranch.owner, owners),
                'planning_sessions': count(PlanningSession, PlanningSession.owner, owners),
                'sessions': count(AuthSession, AuthSession.owner, owners),
                'user_preferences': count(UserPreference, UserPreference.owner, owners),
            },
        }
        print(json.dumps(plan, ensure_ascii=False, indent=2))
        if not args.apply or not owners:
            return 0

        with SessionLocal.begin() as tx:
            if run_ids:
                tx.execute(delete(RunEvent).where(RunEvent.run_id.in_(run_ids)))
            if trip_ids:
                tx.execute(delete(VersionRow).where(VersionRow.trip_id.in_(trip_ids)))
            for model, column in ((Share, Share.owner), (ScenarioBranch, ScenarioBranch.owner),
                                  (PlanningSession, PlanningSession.owner),
                                  (AuthSession, AuthSession.owner),
                                  (UserPreference, UserPreference.owner),
                                  (TripRow, TripRow.owner), (Run, Run.owner)):
                tx.execute(delete(model).where(column.in_(owners)))
            tx.execute(delete(User).where(User.id.in_(owners)))
        print(f'deleted {len(owners)} QA account(s) and their owned rows')
    return 0


if __name__ == '__main__':
    sys.exit(main())
