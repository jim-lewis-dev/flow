"""One serialized Android worker owns this adapter and its SQLite connection.

Amounts cross the JavaScript boundary as strings. All validation, arithmetic,
transactions and undo use the desktop engine, unchanged.
"""
from __future__ import annotations

from datetime import datetime
from collections import Counter
from contextlib import closing
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import uuid

from flowledger.core import (
    APPLICATION_ID, SCHEMA_VERSION, Repository, STATUSES, decimal_sum, flow_sort_key,
    ledger_balance_at, normalize_date, normalize_flow_date, parse_quantity, quantity_text,
)
from flowledger.flow_motion import flow_direction, grouped_events, pulse_radius, select_events
from flowledger.graphing import graph_timeline
from flowledger.history import SessionHistory
from flowledger.numeric_entry import evaluate_quantity
from flowledger.units import format_display


BASES = {
    'Confirmed': ['Complete'],
    'Including Pending': ['Complete', 'Pending'],
    'Including Planned': ['Complete', 'Pending', 'Planned'],
}
REQUIRED = [('date', 'Date'), ('to_account', 'To'), ('from_account', 'From'),
            ('amount', 'Amount'), ('units', 'Units')]


def required_hint(data):
    names = [label for key, label in REQUIRED if not str(data.get(key, '')).strip()]
    if not names:
        return ''
    text = names[0] if len(names) == 1 else (
        ' and '.join(names) if len(names) == 2 else ', '.join(names[:-1]) + ', and ' + names[-1])
    return text + ' required'


def dataset_path(root, name):
    if not isinstance(name, str) or not name.strip():
        raise ValueError('Enter a filename.')
    name = name.strip()
    if name in ('.', '..') or name.startswith('.') or any(c in name for c in '/\\\x00'):
        raise ValueError('Use a filename, without folders.')
    if not name.endswith('.sqlite3'):
        name += '.sqlite3'
    if len(name.encode('utf-8')) > 200:
        raise ValueError('Use a shorter filename.')
    return root / name


class MobileStore:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        from .peers import service
        self.peers = service(self.root)
        self._peer_version = None
        self.repo = None
        self.history = None
        self.dataset = self.dataset_name = None
        self._no_file = False
        self.as_of = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        self.basis = 'Including Pending'

    def close(self):
        if self.history:
            self.history.close()
        if self.repo:
            self.repo.close()
        self.history = self.repo = None
        self.dataset = self.dataset_name = None

    def path(self, name):
        return dataset_path(self.root, name)

    def open(self, name='flow', *, create=False):
        from .peers import key_path, file_key, file_label
        target = self.path(name) if create else key_path(self.root, name)
        if create and target.exists():
            raise ValueError('A file with that name already exists.')
        if not create and not target.is_file():
            raise ValueError('That file is no longer available.')
        self._open_target(target, file_key(self.root, target), file_label(self.root, target))

    def local_files(self):
        from .peers import file_key, file_label
        entries = []
        for path in sorted(self.root.glob('*.sqlite3')):
            if path.is_file() and not path.name.startswith('.'):
                entries.append({'key': path.name, 'name': path.name, 'location': 'Local - on this phone'})
        for path in sorted((self.root / '.shared-files').glob('*/*.sqlite3')):
            if path.is_file() and not path.name.startswith('.') and re.fullmatch('[0-9a-f]{32}', path.parent.name):
                entries.append({'key': file_key(self.root, path), 'name': file_label(self.root, path),
                                'location': 'Shared copy - ' + path.parent.name[:10]})
        return entries

    def _prepare_target(self, target, identity, display_name):
        # SQLite integrity and schema checks do not validate every stored value.
        # Build the same state the UI receives before changing the active file.
        candidate = Repository(target)
        history = None
        try:
            history = SessionHistory(candidate)
            self._state_for(candidate, history, identity, display_name)
        except Exception:
            if history:
                history.close()
            candidate.close()
            raise
        return candidate, history

    def _open_target(self, target, identity, display_name):
        candidate, history = self._prepare_target(target, identity, display_name)
        self.close()
        self.repo, self.history = candidate, history
        self._peer_version = self.data_version()
        self.dataset, self.dataset_name = identity, display_name
        self._no_file = False

    def document_path(self, key):
        if not isinstance(key, str) or not re.fullmatch(r'[0-9a-f]{64}', key):
            raise ValueError('Invalid document reference.')
        return self.root / '.documents' / (key + '.sqlite3')

    def open_document(self, key, display_name):
        target = self.document_path(key)
        if not target.is_file():
            raise ValueError('The saved working file is no longer available. Open the file again.')
        if not isinstance(display_name, str) or not display_name.strip():
            raise ValueError('The selected file has no name.')
        self._open_target(target, 'document:' + key, display_name)

    def state(self):
        if self.repo is None:
            return {
                'dataset': '', 'dataset_name': '',
                'datasets': sorted(p.name for p in self.root.glob('*.sqlite3') if not p.name.startswith('.')),
            'local_files': self.local_files(),
                'flows': [], 'accounts': [], 'as_of': self.as_of, 'basis': self.basis,
                'map_positions': {},
                'vocabulary': {'accounts': [], 'units': [], 'categories': [],
                               'tags': [], 'names': [], 'statuses': list(STATUSES)},
                'undo': '', 'redo': '',
                'device': self.peers.status(), 'peer_file': {},
            }
        return self._state_for(self.repo, self.history, self.dataset, self.dataset_name)

    def delete_dataset(self, name):
        from .peers import key_path, file_key
        target = key_path(self.root, name)
        if target.is_symlink() or not target.is_file():
            raise ValueError('That local file is no longer available.')
        if any(f['key'] == file_key(self.root, target) for f in self.peers.status()['files']):
            raise ValueError('Stop sharing this file before deleting its local copy.')
        # Validate the selected file without changing it or the open ledger.
        try:
            with closing(sqlite3.connect(target.as_uri() + '?mode=ro', uri=True)) as check:
                check.execute('SELECT name FROM sqlite_master LIMIT 1').fetchall()
        except sqlite3.DatabaseError as error:
            raise ValueError('This file cannot be checked before deletion.') from error
        active = self.repo is not None and Path(self.repo.path) == target
        if active:
            self.close()
        try:
            target.unlink()
        except OSError:
            if active:
                self.open(file_key(self.root, target))
            raise
        if active:
            self.dataset = self.dataset_name = ''
            self._no_file = True
        for suffix in ('-wal', '-shm', '-journal'):
            try:
                target.with_name(target.name + suffix).unlink(missing_ok=True)
            except OSError:
                # The database has been deleted. Preserve that successful state
                # even if an obsolete SQLite sidecar cannot be removed.
                pass

    def _state_for(self, repo, history, identity, display_name):
        from .revisions import read_snapshot
        with read_snapshot(repo):
            return self._snapshot_for(repo, history, identity, display_name)

    def _snapshot_for(self, repo, history, identity, display_name):
        flows = sorted(repo.list_flows(), key=flow_sort_key)
        flows.sort(key=lambda f: normalize_date(f['date']), reverse=True)
        accounts = repo.list_accounts(referenced_only=True)
        for account in accounts:
            for key, statuses in [('confirmed', BASES['Confirmed']), ('balance', BASES[self.basis])]:
                value = ledger_balance_at(account, flows, self.as_of, statuses)
                account[key] = None if value is None else quantity_text(value)
                account[key + '_display'] = 'Unknown' if value is None else format_display(value, account['units'])
        for flow in flows:
            flow['amount_display'] = format_display(flow['amount'], flow['units'])
        from .revisions import decorate
        decorate(repo, flows, accounts)
        names = sorted({a['name'] for a in accounts}, key=str.casefold)
        return {
            'dataset': identity, 'dataset_name': display_name,
            'datasets': sorted(p.name for p in self.root.glob('*.sqlite3') if not p.name.startswith('.')),
            'local_files': self.local_files(),
            'flows': flows, 'accounts': accounts, 'as_of': self.as_of, 'basis': self.basis,
            'map_positions': repo.load_map_positions(),
            'vocabulary': {
                'accounts': names, 'units': repo.list_units(referenced_only=True),
                'categories': repo.list_categories(referenced_only=True),
                'tags': repo.list_tags(referenced_only=True), 'names': repo.list_names(),
                'statuses': list(STATUSES),
            },
            'undo': history.undo_label, 'redo': history.redo_label,
            'device': self.peers.status(), 'peer_file': self.peer_file(repo),
        }

    @staticmethod
    def peer_file(repo):
        from flowledger import peer_store
        file_id = peer_store.dataset(repo)
        return {'dataset': file_id} if file_id else {}

    def data_version(self):
        return self.repo.conn.execute('PRAGMA data_version').fetchone()[0] if self.repo else None

    def poll_peers(self):
        # The peer service uses its own SQLite connections. Only redraw ledger
        # data when commits from a different connection changed this view.
        current = self.data_version()
        if current != self._peer_version:
            self._peer_version = current
            return {'state': self.state()}
        return {'device': self.peers.status()}

    def share_peer_file(self, args):
        if not self.repo or args.get('current') != self.dataset:
            raise ValueError('The current file changed. Choose Share this file again.')
        from flowledger import peer_store
        from .peers import file_key, shared_path
        external = self.dataset.startswith('document:')
        label = self.dataset_name
        node = self.peers.node()
        target = Path(self.repo.path)
        if external:
            dataset = peer_store.dataset(self.repo) or str(uuid.uuid4())
            if any(f['dataset'] == dataset for f in node.local_files()):
                raise ValueError('This dataset already has a registered copy. Open the shared copy in Your files.')
            target = shared_path(self.root, dataset, label)
            target.parent.mkdir(mode=0o700, exist_ok=True, parents=True)
            if list(target.parent.glob('*.sqlite3')):
                raise ValueError('This dataset already has a phone copy. Open it in Your files.')
            with target.open('xb'):
                pass
            try:
                self.repo.backup(target)
                with closing(Repository(target)) as copy:
                    peer_store.share(copy, label, dataset_id=dataset)
            except BaseException:
                target.unlink(missing_ok=True)
                raise
        node.share(str(target), name=label)
        self.open(file_key(self.root, target))
        return {'state': self.state()}

    def save_flow(self, data, *, existing=False, baseline=None, revision=None):
        # Read the baseline and apply the patch in one transaction: a peer
        # worker can commit incoming changes through another SQLite connection.
        return self.history.perform('Edit flow' if existing else 'Add flow',
            lambda: self._write_flow(data, existing, baseline, revision))

    def _write_flow(self, data, existing, baseline, revision):
        from .revisions import check, FLOW_FIELDS
        data = dict(data)
        if existing:
            check(self.repo, 'flows', [data.get('flow_id', '')], revision, set(data) & FLOW_FIELDS.keys())
        if existing and baseline is not None:
            current = self.repo.get_flow(data.get('flow_id', ''))
            if current is None:
                raise ValueError('This flow no longer exists.')
            changes = {key: value for key, value in data.items() if key != 'flow_id'}
            allowed = set(current) - {'flow_id'}
            if not isinstance(baseline, dict) or set(changes) != set(baseline) or set(changes) - allowed:
                raise ValueError('Invalid flow edit.')
            for key, value in changes.items():
                if current[key] != baseline[key] and current[key] != value:
                    raise ValueError(key.replace('_', ' ').title() + ' changed on another device. Reopen this flow before editing that field.')
            data = {**current, **changes}
        data.pop('amount_display', None)
        message = required_hint(data)
        if message:
            raise ValueError(message)
        if existing:
            if not self.repo.get_flow(data.get('flow_id', '')):
                raise ValueError('This flow no longer exists.')
        else:
            data.pop('flow_id', None)
        try:
            data['date'] = normalize_flow_date(data['date'])
        except ValueError as error:
            raise ValueError('Date: ' + str(error)) from error
        try:
            data['amount'] = quantity_text(evaluate_quantity(data['amount']))
        except ValueError as error:
            raise ValueError('Amount: ' + str(error)) from error
        return self.repo.save_flow(data)

    def install_document(self, key, display_name):
        """Replace one URI's working cache, never create a numbered dataset.

        The Android host owns URI permissions and writeback. It calls this only
        after checking that the previous working file has no unsaved changes.
        All validation happens in temporary files before replacing
        the cache; a bad selected file leaves the active database untouched.
        """
        incoming = self.root / '.incoming'
        candidate = None
        candidate_history = None
        staged = None
        try:
            target = self.document_path(key)
            if not isinstance(display_name, str) or not display_name.strip():
                raise ValueError('The selected file has no name.')
            if not incoming.is_file():
                raise ValueError('The selected file could not be read.')
            with incoming.open('rb') as file:
                if file.read(16) != b'SQLite format 3\x00':
                    raise ValueError('Choose a Flow SQLite database.')
            with closing(sqlite3.connect(f'{incoming.as_uri()}?mode=ro', uri=True)) as check:
                if check.execute('PRAGMA application_id').fetchone()[0] != APPLICATION_ID:
                    raise ValueError('Choose a Flow SQLite database.')
                version = check.execute('PRAGMA user_version').fetchone()[0]
                if version != SCHEMA_VERSION:
                    raise ValueError(f'Unsupported database version {version}; expected {SCHEMA_VERSION}.')
                if check.execute('PRAGMA quick_check').fetchall() != [('ok',)]:
                    raise ValueError('The selected database is damaged.')
            candidate, candidate_history = self._prepare_target(
                incoming, 'document:' + key, display_name)
            target.parent.mkdir(parents=True, exist_ok=True)
            staged = target.with_name('.' + target.name + '.incoming')
            staged.unlink(missing_ok=True)
            candidate.backup(staged)
            candidate_history.close()
            candidate_history = None
            candidate.close()
            candidate = None
            # The published cache is a self-contained database, with no WAL
            # sidecars which could be left behind by a document replacement.
            with closing(sqlite3.connect(staged)) as prepared:
                prepared.execute('PRAGMA journal_mode=DELETE')
            with staged.open('rb') as prepared:
                os.fsync(prepared.fileno())
            active_target = self.repo and Path(self.repo.path) == target
            if active_target:
                self.close()
            try:
                os.replace(staged, target)
            except OSError:
                if active_target:
                    self.open_document(key, display_name)
                raise
            self.open_document(key, display_name)
        finally:
            if candidate_history:
                candidate_history.close()
            if candidate:
                candidate.close()
            if staged:
                staged.unlink(missing_ok=True)
            incoming.unlink(missing_ok=True)

    def graph(self, args):
        basis = args.get('basis', self.basis)
        if basis not in BASES:
            raise ValueError('Choose a balance basis.')
        pairs = {tuple(p) for p in args.get('pairs', [])}
        accounts = [a for a in self.repo.list_accounts(referenced_only=True)
                    if (a['name'], a['units']) in pairs]
        flows = self.repo.list_flows()
        missing = [f"{a['name']} / {a['units']}" for a in accounts if a['opening_amount'] is None]
        if missing:
            raise ValueError('Set a starting balance for ' + ', '.join(missing) + '.')
        if not accounts:
            return {'series': [], 'start': '', 'end': ''}
        relevant = [f for f in flows if f['status'] in BASES[basis] and
                    ((f['from_account'], f['units']) in pairs or (f['to_account'], f['units']) in pairs)]
        dates = [normalize_date(f['date']) for f in relevant]
        dates += [normalize_date(a['opening_at']) for a in accounts]
        start = normalize_date(args.get('start') or min(dates))
        end = normalize_date(args.get('end') or max(dates))
        if args.get('end') and normalize_flow_date(args['end']) == end[:10]:
            end = end[:10] + ' 23:59:59'
        series = list(graph_timeline(accounts, flows, start, end, BASES[basis]).items())
        if args.get('combined', False):
            combined = graph_timeline(accounts, flows, start, end, BASES[basis], combined=True)
            series.extend((('Combined: ' + ' + '.join(a['name'] for a in accounts if a['units'] == units), units), points)
                          for (_, units), points in combined.items())
        return {
            'start': start, 'end': end,
            'series': [{'name': name, 'units': units,
                        'points': [[date, quantity_text(value), format_display(value, units)]
                                   for date, value in points]}
                       for (name, units), points in series],
        }

    def _experimental_selection(self, args):
        """Filter map events; keep the whole ledger for actual account balances."""
        basis = args.get('basis', 'Confirmed')
        if basis not in BASES:
            raise ValueError('Choose a balance basis.')
        flows = sorted(self.repo.list_flows(), key=flow_sort_key)
        selected = args.get('flow_ids')
        if selected is not None:
            if not isinstance(selected, list) or any(not isinstance(value, str) for value in selected):
                raise ValueError('Choose flows from the current ledger.')
            selected = set(selected)
        visible = [flow for flow in flows if selected is None or flow['flow_id'] in selected]
        units = sorted({flow['units'] for flow in visible})
        unit = args.get('units') or (units[0] if units else '')
        if not isinstance(unit, str):
            raise ValueError('Choose a unit.')
        dates = [normalize_date(flow['date']) for flow in visible if flow['units'] == unit]
        start = args.get('start') or (min(dates)[:10] if dates else '')
        end = args.get('end') or (max(dates)[:10] if dates else '')
        for label, value in [('Start', start), ('End', end)]:
            if value:
                try:
                    normalize_flow_date(value)
                except ValueError as error:
                    raise ValueError(label + ': ' + str(error)) from error
        events = select_events(visible, start or end, end or start, unit, BASES[basis]) if start or end else []
        return flows, events, {
            'units': unit, 'available_units': units, 'basis': basis,
            'start': normalize_flow_date(start) if start else '',
            'end': normalize_flow_date(end) if end else ''
        }

    def experimental(self, args):
        _, events, result = self._experimental_selection(args)
        names = sorted({flow[key] for flow in events for key in ('from_account', 'to_account')},
                       key=lambda value: (value.casefold(), value))
        positions = self.repo.load_map_positions()
        # Use the desktop scene's normalized coordinates, including positions
        # outside its initial bounds. The phone only changes the camera scale.
        degree = Counter(name for flow in events for name in (flow['from_account'], flow['to_account']))
        ordered = sorted(names, key=lambda name: (-degree[name], name.casefold(), name))
        if len(names) >= 7 and degree[ordered[0]] >= max(3, len(names) // 3):
            positions.setdefault(ordered.pop(0), (0.5, 0.5))
        if len(names) == 1:
            positions.setdefault(names[0], (0.5, 0.5))
        elif len(names) == 2:
            positions.setdefault(ordered[0], (0.24, 0.47))
            positions.setdefault(ordered[1], (0.76, 0.47))
        else:
            for index, name in enumerate(ordered):
                angle = -math.pi / 2 + 2 * math.pi * index / len(ordered)
                positions.setdefault(name, (0.5 + 0.39 * math.cos(angle), 0.48 + 0.31 * math.sin(angle)))
        nodes = []
        for name in names:
            account = self.repo.get_account(name, result['units'])
            opening = account['opening_amount'] if account else None
            nodes.append({
                'name': name, 'x': positions[name][0], 'y': positions[name][1],
                'opening_amount': opening,
                'opening_display': 'Unknown' if opening is None else format_display(opening, result['units']),
                'opening_at': account['opening_at'] if account else ''
            })
        maximum = max((parse_quantity(flow['amount']).copy_abs() for flow in events), default=0)
        groups = []
        for when, group in grouped_events(events):
            motions = []
            for flow in group:
                source, target = flow_direction(flow)
                motions.append({**flow, 'source': source, 'target': target,
                                'amount_display': format_display(flow['amount'], flow['units']),
                                'radius': pulse_radius(flow['amount'], maximum)})
            groups.append({'date': when, 'flows': motions})
        return {**result, 'nodes': nodes, 'groups': groups}

    def experimental_details(self, args):
        all_flows, events, result = self._experimental_selection(args)
        name = args.get('name', '')
        if not isinstance(name, str) or not any(name in (flow['from_account'], flow['to_account']) for flow in events):
            raise ValueError('This account is not in the selected map.')
        groups = grouped_events(events)
        step = args.get('step', -1)
        if isinstance(step, bool) or not isinstance(step, int) or not -1 <= step < len(groups):
            raise ValueError('Choose a current map step.')
        stamp = groups[step][0] if step >= 0 else ''
        shown = [flow for flow in events if not stamp or normalize_date(flow['date']) <= stamp]
        incoming, outgoing = [], []
        for flow in shown:
            source, target = flow_direction(flow)
            amount = parse_quantity(flow['amount']).copy_abs()
            if source == name:
                outgoing.append(amount)
            if target == name:
                incoming.append(amount)
        incoming, outgoing = decimal_sum(incoming), decimal_sum(outgoing)
        account = self.repo.get_account(name, result['units'])
        opening = account['opening_amount'] if account else None
        balance = ledger_balance_at(account, all_flows, stamp, BASES[result['basis']]) if account and stamp else None
        display = lambda value: 'Unknown' if value is None else format_display(value, result['units'])
        return {
            'name': name, 'units': result['units'], 'basis': result['basis'],
            'opening_amount': opening, 'opening_display': display(opening),
            'opening_at': account['opening_at'] if account else '',
            'at': stamp, 'balance': None if balance is None else quantity_text(balance),
            'balance_display': display(balance),
            'incoming': quantity_text(incoming), 'incoming_display': display(incoming),
            'outgoing': quantity_text(outgoing), 'outgoing_display': display(outgoing),
            'scope': 'Shown through this step' if stamp else 'Shown in selected period'
        }

    def save_positions(self, args):
        self.history.perform('Move account', lambda: self._write_positions(args))
        return {'positions': self.repo.load_map_positions(), 'state': self.state()}

    def _write_positions(self, args):
        # Drag responses may arrive after switching files. Never apply an old
        # dataset's positions to the newly opened file.
        if args.get('dataset') != self.dataset:
            raise ValueError('The active dataset changed; reopen the map.')
        updates = args.get('positions')
        if not isinstance(updates, dict):
            raise ValueError('Account positions must map names to coordinates.')
        names = {account['name'] for account in self.repo.list_accounts(referenced_only=True)}
        if set(updates) - names:
            raise ValueError('Account positions must refer to current accounts.')
        before = self.repo.load_map_positions()
        baseline = args.get('baseline')
        if baseline is not None:
            if not isinstance(baseline, dict) or set(baseline) != set(updates):
                raise ValueError('Invalid account position edit.')
            for name, point in updates.items():
                original = tuple(baseline[name]) if baseline[name] is not None else None
                if before.get(name) != original and before.get(name) != tuple(point):
                    raise ValueError(name + ' moved on another device. Move it again from its received position.')
        positions = dict(before)
        positions.update(updates)
        self.repo.save_map_positions(positions)

    def _write_anchor(self, args):
        from .revisions import check, ANCHOR_FIELDS
        check(self.repo, 'account_balances', [args['name'].casefold(), args['units']],
              args.get('revision'), set(args) & ANCHOR_FIELDS.keys())
        account = self.repo.get_account(args['name'], args['units'])
        if not account:
            raise ValueError('This account and unit no longer exist.')
        baseline = args.get('baseline')
        if baseline is not None:
            changes = {key: args[key] for key in ('opening_at', 'opening_amount') if key in args}
            if not isinstance(baseline, dict) or set(changes) != set(baseline):
                raise ValueError('Invalid starting balance edit.')
            for key, value in changes.items():
                original = account[key] if account[key] is not None else ''
                if original != baseline[key] and original != value:
                    raise ValueError('The starting balance changed on another device. Reopen this account before editing it.')
            args = {**account, **changes}
            if args['opening_amount'] is None:
                args['opening_amount'] = ''
        try:
            when = normalize_date(args['opening_at'])
        except ValueError as error:
            raise ValueError('Starting date: ' + str(error)) from error
        try:
            amount = (quantity_text(evaluate_quantity(args['opening_amount']))
                      if args['opening_amount'].strip() else None)
        except ValueError as error:
            raise ValueError('Starting balance: ' + str(error)) from error
        self.repo.save_account(
            account['name'], amount, when, account['units'])

    def dispatch(self, action, args):
        if self.repo is None and action not in {
                'init', 'state', 'view', 'new_dataset', 'open_dataset', 'delete_dataset',
                'peer_poll', 'peer_pair', 'peer_catalog', 'peer_join', 'peer_forget',
                'camp', 'install_document', 'open_document', 'normalize_input'}:
            raise ValueError('Open or create a file first.')
        if action == 'init':
            if args.get('dataset') == '' and not self.repo:
                self._no_file = True
            if not self.repo and not self._no_file:
                name = args.get('dataset') or 'flow.sqlite3'
                from .peers import key_path
                path = key_path(self.root, name)
                # A missing remembered file is an error, not a new blank file.
                empty = not self.local_files() and not any(
                    (self.root / '.documents').glob('*.sqlite3'))
                self.open(name, create=empty and name in ('flow', 'flow.sqlite3') and not path.exists())
        elif action == 'peer_poll':
            return self.poll_peers()
        elif action == 'peer_share':
            return self.share_peer_file(args)
        elif action == 'peer_unshare':
            if not self.repo or args.get('current') != self.dataset:
                raise ValueError('The current file changed. Select it again.')
            current = self.peer_file(self.repo)
            if not current or not any(f['dataset'] == current['dataset'] and f['key'] == self.dataset
                                      for f in self.peers.status()['files']):
                raise ValueError('This file is not shared from this phone.')
            self.peers.node().unshare(current['dataset'])
        elif action in ('peer_pair', 'peer_catalog', 'peer_join', 'peer_forget'):
            # Native Android handles LAN calls outside the SQLite UI worker.
            result = self.peers.call(action, args)
            if action == 'peer_join':
                self.open(result['name'])
                return {'state': self.state()}
            return result
        elif action == 'new_dataset':
            self.open(args.get('name', ''), create=True)
        elif action == 'open_dataset':
            self.open(args.get('name', ''))
        elif action == 'delete_dataset':
            self.delete_dataset(args.get('name', ''))
        elif action == 'camp':
            from examples.camp import create_camp
            target = self.path('camp')
            if not target.exists():
                create_camp(target)
            self.open(target.name)
        elif action in ('add_flow', 'update_flow'):
            record = self.save_flow(args['flow'], existing=action == 'update_flow', baseline=args.get('baseline'), revision=args.get('revision'))
            return {'state': self.state(), 'record': record}
        elif action == 'copy_flow':
            old = self.repo.get_flow(args['flow_id'])
            if old is None:
                raise ValueError('This flow no longer exists.')
            old.pop('flow_id')
            record = self.history.perform('Copy flow', lambda: self.repo.save_flow(old))
            return {'state': self.state(), 'record': record}
        elif action == 'delete_flow':
            def remove():
                from .revisions import check, FLOW_FIELDS
                check(self.repo, 'flows', [args['flow_id']], args.get('revision'), FLOW_FIELDS)
                return self.repo.delete_flow(args['flow_id'])
            self.history.perform('Delete flow', remove)
        elif action == 'save_anchor':
            self.history.perform('Edit starting balance', lambda: self._write_anchor(args))
        elif action == 'view':
            when = normalize_date(args.get('as_of', self.as_of))
            basis = args.get('basis', self.basis)
            if basis not in BASES:
                raise ValueError('Choose a balance basis.')
            self.as_of, self.basis = when, basis
        elif action == 'undo':
            self.history.undo()
        elif action == 'redo':
            self.history.redo()
        elif action == 'graph':
            return self.graph(args)
        elif action == 'experimental':
            return self.experimental(args)
        elif action == 'experimental_details':
            return self.experimental_details(args)
        elif action == 'save_positions':
            return self.save_positions(args)
        elif action == 'normalize_input':
            field, value = args['field'], args['value']
            try:
                if field == 'date':
                    return {'value': normalize_flow_date(value)}
                if field == 'amount':
                    return {'value': quantity_text(evaluate_quantity(value))}
                raise ValueError('Unknown entry field.')
            except ValueError as error:
                raise ValueError(field.title() + ': ' + str(error)) from error
        elif action == 'install_document':
            self.install_document(args.get('key', ''), args.get('name', ''))
        elif action == 'open_document':
            self.open_document(args.get('key', ''), args.get('name', ''))
        elif action == 'prepare_export':
            target = self.root / '.export.sqlite3'
            target.unlink(missing_ok=True)
            self.repo.backup(target)
            return {'path': str(target), 'name': self.dataset_name}
        elif action != 'state':
            raise ValueError('Unknown action.')
        return {'state': self.state()}


_store = None


def request(root, payload):
    """Only called by the Android serial worker (or the local test harness)."""
    global _store
    try:
        if _store is None:
            _store = MobileStore(root)
        data = json.loads(payload)
        result = _store.dispatch(data['action'], data.get('args', {}))
        return json.dumps({'ok': True, 'result': result}, ensure_ascii=False)
    except (ValueError, KeyError, TypeError, RuntimeError, sqlite3.Error, OSError) as error:
        return json.dumps({'ok': False, 'error': str(error)}, ensure_ascii=False)

