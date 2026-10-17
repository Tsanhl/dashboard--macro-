"""One locked, staged data-refresh path for the local button, nightly task and Pages.

Never generates or publishes an AI summary. Failed batches never replace the served snapshot.
"""
import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
import uuid

from market_clock import completed_session, week_complete

ROOT = Path(__file__).resolve().parent


class Busy(RuntimeError):
    pass


@contextmanager
def refresh_lock(root=ROOT):
    folder = root / '.refresh'; folder.mkdir(exist_ok=True)
    with (folder/'lock').open('a') as handle:
        try: fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError: raise Busy('已有更新進行中，請稍候。')
        try: yield
        finally: fcntl.flock(handle, fcntl.LOCK_UN)


def read(path, default=None):
    return json.loads(path.read_text()) if path.exists() else default


def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name+'.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2)+'\n')
    os.replace(temp, path)


def validate(data, session, previous=None):
    from build_site import DATASETS
    rows = {}
    for key in DATASETS:
        rows[key] = read(data/f'{key}.json')
        if not isinstance(rows[key], list) or not rows[key]:
            raise ValueError(f'Missing required dataset: {key}')
    for key, field in [('breadth_now','asof'), ('flowmodel_now','date')]:
        if rows[key][0].get(field) != session:
            raise ValueError(f'{key} does not match completed session {session}')
    for key in ['breadth','check_prices','flowmodel','ratios','rrg_daily']:
        if max(r.get('date','') for r in rows[key]) != session:
            raise ValueError(f'{key} latest observation is incomplete')
    for row in rows['sectors']:
        if row.get('week_done') != week_complete(session):
            raise ValueError('Sector dates / completed-week flag mismatch')
        if not row.get('week_fw') or row['week_fw'] > session or not week_complete(row['week_fw']):
            raise ValueError('Invalid confirmed-week observation')
    for key, fields in [('breadth_now', ('pct50','pct200','mcclellan','adv','dec')),
                        ('flowmodel_now', ('cta','vc','rv1','rv3','vix_ratio'))]:
        if any(not isinstance(rows[key][0].get(f), (int,float)) or not math.isfinite(rows[key][0][f]) for f in fields):
            raise ValueError(f'Incomplete core calculations: {key}')
    for row in rows['positioning_now']:
        if row['date'] > session: raise ValueError('Future CFTC observation')
    old = read(previous/'breadth_now.json', []) if previous else []
    if old and old[0]['asof'] > session: raise ValueError('Refusing to move stock dates backwards')
    rt = rows['rates_now'][0]
    for col in ('nominal2','nominal10','real10','hy_oas','ig_oas','ccc_oas','bb_oas'):
        d = rt.get(col+'_date')
        if not d or rt.get(col) is None or d > datetime.now(timezone.utc).date().isoformat():
            raise ValueError(f'Unconfirmed source date/value: {col}')
    if previous:
        prior = read(previous/'rates_now.json', [{}])[0]
        for key, value in rt.items():
            if key.endswith('_date') and prior.get(key) and value and value < prior[key]:
                raise ValueError(f'Source date regressed: {key}')
        prior_pos = {r['market']: r['date'] for r in read(previous/'positioning_now.json', [])}
        if set(prior_pos) - {r['market'] for r in rows['positioning_now']}:
            raise ValueError('CFTC markets disappeared from the new batch')
        if any(r['date'] < prior_pos.get(r['market'],'') for r in rows['positioning_now']):
            raise ValueError('CFTC report regressed')
    return rows


def promote(pairs, backup):
    """Same-volume renames; restore every previous destination if a later rename fails."""
    backup.mkdir(parents=True, exist_ok=True)
    moved = []
    try:
        for n, (source, dest) in enumerate(pairs):
            dest.parent.mkdir(parents=True, exist_ok=True)
            old = backup/f'{n}-{dest.name}'
            had_old = dest.exists()
            if had_old: os.replace(dest, old)
            moved.append((source, dest, old, had_old))
            os.replace(source, dest)
    except Exception:
        for source, dest, old, had_old in reversed(moved):
            if dest.exists(): os.replace(dest, source)
            if had_old: os.replace(old, dest)
        raise


def metadata(rows, previous, bonds, session):
    rt = rows['rates_now'][0]
    prior_rt = read(previous/'rates_now.json', [{}])[0]
    old_pos = read(previous/'positioning_now.json', [])
    groups = []
    def add(name, cadence, dates, old_dates):
        dates = sorted(set(d for d in dates if d))
        groups.append(dict(name=name, cadence=cadence, dates=dates,
                           state='new_observation' if dates != sorted(set(d for d in old_dates if d)) else 'same_observation_dates'))
    old_br = read(previous/'breadth_now.json', [{}])[0]
    add('股價／市寬／每日輪動／模型', 'daily', [session], [old_br.get('asof')])
    add('利率', 'daily_release', [rt.get(k+'_date') for k in ('nominal2','nominal10','real10')],
        [prior_rt.get(k+'_date') for k in ('nominal2','nominal10','real10')])
    add('信用', 'daily_release', [rt.get(k+'_date') for k in ('hy_oas','ig_oas','ccc_oas','bb_oas')],
        [prior_rt.get(k+'_date') for k in ('hy_oas','ig_oas','ccc_oas','bb_oas')])
    add('CFTC持倉', 'weekly', [r['date'] for r in rows['positioning_now']], [r['date'] for r in old_pos])
    add('聯儲／淨流動性', 'weekly', [rt.get('liq_date')], [prior_rt.get('liq_date')])
    add('已確認週線', 'completed_week', [rows['sectors'][0].get('week_fw')],
        [r.get('week_fw') for r in read(previous/'sectors.json', [])])
    return dict(checked_at=datetime.now(timezone.utc).isoformat(), session=session, groups=groups,
                borrower_references=bonds, ai_summary_updated=False)


def summary_review_state(work, root, public, render_only):
    """A late same-session credit release can invalidate a narrative without a new stock date."""
    target = 'site' if public else 'site_private'
    previous = read(root/target/'data/summary.json', [])
    if public:
        path = work/'summary.md'
        if path.exists():
            first, _, body = path.read_text().partition('\n')
            current = [{'session':first.removeprefix('session: ').strip(), 'text':body}]
        else: current = []
    else:
        files = sorted((work.parent/'daily_summaries').glob('20??-??-??.md'))
        current = [{'session':files[-1].stem, 'text':files[-1].read_text()}] if files else []
    if not current: return False
    # A newly reviewed narrative clears the previous reminder; its session date is checked by the UI too.
    if previous and current != previous: return False
    prior_meta = read(root/target/'data/refresh_meta.json', [{}])[0]
    if prior_meta.get('summary_needs_review'): return True
    if render_only: return False
    from build_site import DATASETS
    def normalized(value):
        if isinstance(value, dict): return {k:normalized(v) for k,v in value.items() if k not in ('built','retrieved_at','last_checked_at')}
        if isinstance(value, list): return [normalized(v) for v in value]
        return value
    if any(normalized(read(work/'data'/f'{k}.json')) != normalized(read(root/'data'/f'{k}.json')) for k in DATASETS):
        return True
    if not public:
        path = Path('board_evidence/borrower_credit.json')
        return normalized(read(work.parent/path)) != normalized(read(root.parent/path))
    return False


def run(public=False, render_only=False, root=ROOT):
    with refresh_lock(root):
        session = completed_session()
        stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'_'+uuid.uuid4().hex[:6]
        run_dir = (root/'.refresh/runs' if public else root.parent/'board_updates'/session)/(stamp+'_data_refresh')
        stage = run_dir/'staged'; work = stage/'dashboard'
        work.mkdir(parents=True)
        for p in root.iterdir():
            if p.is_file() and p.suffix in ('.py','.js','.html','.md','.txt'):
                shutil.copy2(p, work/p.name)
        if (root/'data').exists(): shutil.copytree(root/'data', work/'data')
        if not public:
            # Copy only explicitly needed private inputs, never the whole parent workspace.
            for name in ('holdings.json',):
                if (root.parent/name).exists(): shutil.copy2(root.parent/name, stage/name)
            for name in ('daily_summaries','board_evidence','ai_cycle_watch'):
                if (root.parent/name).exists():
                    shutil.copytree(root.parent/name, stage/name, ignore=shutil.ignore_patterns('__pycache__'))
            reg = root.parent/'company_tracking/active_registry.json'
            if reg.exists():
                (stage/'company_tracking').mkdir(); shutil.copy2(reg, stage/'company_tracking/active_registry.json')
        env = dict(os.environ, BOARD_ASOF=session, BOARD_STRICT_REFRESH='1', PYTHONUNBUFFERED='1')
        def command(args):
            with (run_dir/'execution.log').open('a') as log:
                subprocess.run([sys.executable, *args], cwd=work, env=env, stdout=log, stderr=log,
                               check=True, timeout=1800)
        try:
            if not render_only:
                for attempt in range(2):
                    try: command(['build_data.py']); break
                    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
                        if attempt: raise
            else:
                session = read(work/'data/breadth_now.json')[0]['asof']
            rows = validate(work/'data', session, root/'data')
            bond_results = []
            if not public and not render_only:
                from bond_reference import collect
                bond_results = collect(stage/'board_evidence/borrower_credit.json', session, run_dir)
                tracker = stage/'ai_cycle_watch/track.py'
                if tracker.exists():
                    command([str(tracker)])
                    if read(stage/'ai_cycle_watch/data/latest.json').get('asof') != session:
                        raise ValueError('AI cycle tracker date does not match')
                # This optional forward record is immutable once created; never pass --force.
                forward = root.parent/'backtests/scripts/forward_log.py'
                if forward.exists():
                    (stage/'backtests/scripts').mkdir(parents=True)
                    shutil.copy2(forward, stage/'backtests/scripts/forward_log.py')
                    if (root.parent/'backtests/forward').exists():
                        shutil.copytree(root.parent/'backtests/forward', stage/'backtests/forward')
                    command([str(stage/'backtests/scripts/forward_log.py')])
            meta = metadata(rows, root/'data', bond_results, session)
            if render_only:
                meta = read(root/'data/refresh_meta.json', [meta])[0]
            meta['summary_needs_review'] = summary_review_state(work, root, public, render_only)
            atomic_json(work/'data/refresh_meta.json', [meta])
            command(['build_site.py'] + ([] if public else ['--private']))
            target = 'site' if public else 'site_private'
            pairs = [(work/'data', root/'data'), (work/target, root/target)]
            if not public and not render_only:
                pairs.append((stage/'board_evidence/borrower_credit.json', root.parent/'board_evidence/borrower_credit.json'))
                for rel in ('ai_cycle_watch/data', 'backtests/forward'):
                    if (stage/rel).exists(): pairs.append((stage/rel, root.parent/rel))
            promote(pairs, run_dir/'previous')
            result = dict(state='success', session=session, checked_at=meta['checked_at'], groups=meta['groups'],
                          borrower_references=bond_results, ai_summary_updated=False)
            atomic_json(run_dir/'result.json', result)
            return result
        except Exception as exc:
            atomic_json(run_dir/'result.json', {'state':'failed','error':str(exc),'previous_snapshot_preserved':True})
            raise


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--public', action='store_true')
    ap.add_argument('--render-only', action='store_true', help='Rebuild display after an independently reviewed summary; no downloads')
    args = ap.parse_args()
    try: print(json.dumps(run(args.public, args.render_only), ensure_ascii=False))
    except Exception as exc:
        print(str(exc), file=sys.stderr); sys.exit(1)
