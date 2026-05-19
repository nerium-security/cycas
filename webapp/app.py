import os
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import json
import yaml
from flask import Flask, render_template, request, flash, jsonify
from azure.identity import DefaultAzureCredential
from azure.data.tables import EntityProperty
from azure.storage.blob import BlobServiceClient
from azure.kusto.data import KustoClient, KustoConnectionStringBuilder
from core.manager.table import TablestorageManager
from core.utils.config import load_config
from core.utils.status import Status

app = Flask(__name__)
app.secret_key = os.environ.get('FLASK_SECRET_KEY', 'dev-secret-change-in-prod')

ALL_STATUSES = [
    Status.NEW, Status.PROCESSING, Status.DOWNLOADING, Status.DOWNLOADED,
    Status.DOWNLOADFAILED, Status.POSTPROCESSING, Status.UPLOADING,
    Status.FAILED, Status.QUEUED, Status.UNQUEUED, Status.FINISHED,
]

PARTITION_KEY     = 'pipeline-uploads'
POLL_INTERVAL_SEC = 10
POLL_TIMEOUT_MIN  = int(os.environ.get('ADX_POLL_TIMEOUT_MIN', '30'))


# ---------------------------------------------------------------------------
# ADX ingestion poller
# ---------------------------------------------------------------------------

class IngestionPoller:
    '''
    Background thread that confirms when ingested data becomes queryable in ADX.

    Tracks uploadids that have finished processing. Every POLL_INTERVAL_SEC seconds
    it fires a single batched KQL query against _status_summary for all pending
    entries. Polling stops per-entry once confirmed or after POLL_TIMEOUT_MIN minutes.
    '''

    def __init__(self, cluster_uri, database, summary_table):
        self._cluster_uri  = cluster_uri
        self._database     = database
        self._summary_table = summary_table
        self._pending     = {}    # uploadid -> finished_at (UTC-aware datetime)
        self._confirmed   = set()
        self._timed_out   = set()
        self._lock        = threading.Lock()
        self._init_lock   = threading.Lock()
        self._thread      = None
        self._client      = None

    def _build_client(self):
        kcsb = KustoConnectionStringBuilder.with_azure_token_credential(
            self._cluster_uri, DefaultAzureCredential()
        )
        return KustoClient(kcsb)

    def track(self, uploadid, finished_at):
        '''Register an uploadid for ADX confirmation. Idempotent.'''
        with self._lock:
            if (uploadid not in self._confirmed
                    and uploadid not in self._timed_out
                    and uploadid not in self._pending):
                self._pending[uploadid] = finished_at
        self._ensure_running()

    def get_status(self, uploadid):
        with self._lock:
            if uploadid in self._confirmed:
                return 'confirmed'
            if uploadid in self._timed_out:
                return 'timeout'
            if uploadid in self._pending:
                return 'pending'
        return 'unknown'

    def _ensure_running(self):
        with self._init_lock:
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(target=self._loop, daemon=True)
                self._thread.start()

    def _loop(self):
        if self._client is None:
            try:
                self._client = self._build_client()
            except Exception:
                return

        while True:
            with self._lock:
                cutoff = datetime.now(timezone.utc) - timedelta(minutes=POLL_TIMEOUT_MIN)
                for uid, ts in list(self._pending.items()):
                    if ts < cutoff:
                        del self._pending[uid]
                        self._timed_out.add(uid)
                pending = list(self._pending.keys())

            if not pending:
                break

            try:
                ids_kql = ', '.join(f'"{uid}"' for uid in pending)
                query   = f'{self._summary_table} | where uploadid in ({ids_kql}) | project uploadid'
                result  = self._client.execute(self._database, query)
                found   = {row['uploadid'] for row in result.primary_results[0]}
                with self._lock:
                    for uid in found:
                        self._pending.pop(uid, None)
                        self._confirmed.add(uid)
            except Exception:
                pass

            time.sleep(POLL_INTERVAL_SEC)


_poller      = None
_poller_lock = threading.Lock()


def get_poller():
    global _poller
    if _poller is not None:
        return _poller
    with _poller_lock:
        if _poller is None:
            try:
                config = load_config()
                if config.adx_cluster_enabled:
                    _poller = IngestionPoller(config.adx_cluster_uri, config.adx_database_name, config.adx_status_table_summary)
            except Exception:
                pass
    return _poller


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _unwrap(entry):
    return {k: (v.value if isinstance(v, EntityProperty) else v) for k, v in entry.items()}


def _compute_duration(entry):
    '''
    Calculate a human-readable duration for a Table Storage entry.

    For finished entries the stored Duration string is used (recorded at
    finish time from the true pipeline start). For all other statuses the
    elapsed time is computed live from StartTime (set only at creation).
    '''
    status = (entry.get('Status') or '').lower()
    if status == Status.FINISHED:
        return entry.get('Duration') or '-'
    start_str = entry.get('StartTime') or ''
    if not start_str:
        return entry.get('Duration') or '-'
    try:
        start   = datetime.fromisoformat(start_str.replace('Z', '+00:00'))
        elapsed = int((datetime.now(timezone.utc) - start).total_seconds())
        h, rem  = divmod(elapsed, 3600)
        m, s    = divmod(rem, 60)
        return f'{h}h {m}m {s}s'
    except Exception:
        return entry.get('Duration') or '-'


def _parse_finished_at(start_time_str):
    try:
        return datetime.fromisoformat(start_time_str.replace('Z', '+00:00'))
    except Exception:
        return datetime.now(timezone.utc)


def get_table_client():
    config = load_config()
    credential = DefaultAzureCredential()
    manager = TablestorageManager(credential, config.blob_logtable_uri, config.blob_logtable_name)
    manager.authenticate(verify_enabled=False)
    return manager, config.blob_logtable_name


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route('/')
def index():
    q = request.args.get('q', '').strip()
    status_filter = request.args.get('status', '').strip()

    entries = []
    error_msg = None

    try:
        manager, table_name = get_table_client()
        raw = [_unwrap(e) for e in manager.table_client.query_entities(
            f"PartitionKey eq '{PARTITION_KEY}'"
        )]

        if status_filter:
            raw = [e for e in raw if (e.get('Status') or '').lower() == status_filter.lower()]

        if q:
            q_lower = q.lower()
            raw = [
                e for e in raw
                if q_lower in (e.get('ZipfileBasename') or '').lower()
                or q_lower in (e.get('Extracted_Hostname') or '').lower()
            ]

        for e in raw:
            e['ComputedDuration'] = _compute_duration(e)
        entries = sorted(raw, key=lambda e: e.get('StartTime') or '', reverse=True)

    except Exception as exc:
        error_msg = str(exc)
        table_name = os.environ.get('BLOB_LOGTABLE_NAME', 'status table')

    # Register FINISHED entries with the ADX poller, then read back their status
    poller = get_poller()
    for e in entries:
        upload_id = e.get('UploadId') or ''
        if poller and e.get('Status') == Status.FINISHED and upload_id:
            finished_at = _parse_finished_at(e.get('StartTime', ''))
            poller.track(upload_id, finished_at)
            e['AdxStatus'] = poller.get_status(upload_id)
        else:
            e['AdxStatus'] = ''

    if error_msg:
        flash(f'Could not connect to Table Storage: {error_msg}', 'error')

    stats = {
        'total':      len(entries),
        'finished':   sum(1 for e in entries if (e.get('Status') or '') == Status.FINISHED),
        'processing': sum(1 for e in entries if (e.get('Status') or '') in (
            Status.PROCESSING, Status.DOWNLOADING, Status.DOWNLOADED,
            Status.POSTPROCESSING, Status.UPLOADING,
        )),
        'failed':     sum(1 for e in entries if (e.get('Status') or '') == Status.FAILED),
    }

    return render_template(
        'index.html',
        entries=entries,
        stats=stats,
        statuses=ALL_STATUSES,
        status_filter=status_filter,
        q=q,
        table_name=table_name,
        adx_enabled=(poller is not None),
    )


@app.route('/api/entries')
def api_entries():
    '''Return all table entries as JSON for live table refresh.'''
    q = request.args.get('q', '').strip()
    status_filter = request.args.get('status', '').strip()

    try:
        manager, _ = get_table_client()
        raw = [_unwrap(e) for e in manager.table_client.query_entities(
            f"PartitionKey eq '{PARTITION_KEY}'"
        )]

        if status_filter:
            raw = [e for e in raw if (e.get('Status') or '').lower() == status_filter.lower()]

        if q:
            q_lower = q.lower()
            raw = [
                e for e in raw
                if q_lower in (e.get('ZipfileBasename') or '').lower()
                or q_lower in (e.get('Extracted_Hostname') or '').lower()
            ]

        entries = sorted(raw, key=lambda e: e.get('StartTime') or '', reverse=True)

        poller = get_poller()
        result = []
        for e in entries:
            upload_id = e.get('UploadId') or ''
            adx = poller.get_status(upload_id) if (poller and upload_id) else 'unknown'
            size = e.get('Size')
            result.append({
                'RowKey':            e.get('RowKey') or '',
                'ZipfileBasename':   e.get('ZipfileBasename') or '',
                'Extracted_Hostname': e.get('Extracted_Hostname') or '',
                'Status':            e.get('Status') or '',
                'Source':            e.get('Source') or '',
                'StartTime':         e.get('StartTime') or '',
                'Duration':          e.get('Duration') or '',
                'Size':              int(size) if size else None,
                'UploadId':          upload_id,
                'AdxStatus':         adx,
            })
        return jsonify(result)
    except Exception as exc:
        return jsonify({'error': str(exc)}), 500


@app.route('/api/retry', methods=['POST'])
def api_retry():
    '''Reset selected entries to unqueued so the watcher re-queues them.'''
    data = request.get_json() or {}
    row_keys = data.get('row_keys', [])
    if not row_keys:
        return jsonify({'error': 'No row keys provided'}), 400
    try:
        manager, _ = get_table_client()
        for rk in row_keys:
            manager.update_log_entry({
                'PartitionKey': PARTITION_KEY,
                'RowKey':       rk,
                'Status':       Status.UNQUEUED,
            })
        return jsonify({'retried': len(row_keys)})
    except Exception as exc:
        return jsonify({'error': str(exc)}), 500


@app.route('/api/status/<uploadid>')
def api_status_detail(uploadid):
    '''Fetch the status JSON blob for a finished or failed pipeline run.'''
    try:
        config = load_config()
        credential = DefaultAzureCredential()
        blob_service = BlobServiceClient(
            account_url=config.blob_storageaccount_uri,
            credential=credential,
        )
        blob_client = blob_service.get_blob_client(
            container=config.blob_container_status,
            blob=f'{uploadid}.json',
        )
        data = blob_client.download_blob().readall()
        return jsonify(json.loads(data))
    except Exception as exc:
        return jsonify({'error': str(exc)}), 404


@app.route('/api/adx-status')
def adx_status():
    '''
    Return ADX queryability status for all tracked uploadids.

    The frontend polls this every 10 s and stops once all pending entries
    are either confirmed or timed out.
    '''
    poller = get_poller()
    if not poller:
        return jsonify({})

    with poller._lock:
        result = {}
        for uid in poller._confirmed:
            result[uid] = 'confirmed'
        for uid in poller._timed_out:
            result[uid] = 'timeout'
        for uid in poller._pending:
            result[uid] = 'pending'

    return jsonify(result)


# ---------------------------------------------------------------------------
# Artifact management helpers
# ---------------------------------------------------------------------------

def _artifact_name(entry):
    return entry.split('(')[0].strip()

def _yaml_path(config, name):
    return Path(config.velociraptor_definitions) / f'{name}.yaml'

def _load_artifacts_json(config):
    with open(config.velociraptor_artifactslist) as f:
        return json.load(f)

def _save_artifacts_json(config, data):
    path = Path(config.velociraptor_artifactslist)
    tmp  = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(data, indent=2))
    tmp.replace(path)


# ---------------------------------------------------------------------------
# Artifact management routes
# ---------------------------------------------------------------------------

@app.route('/artifacts')
def artifacts_page():
    return render_template('artifacts.html')


@app.route('/api/artifacts')
def api_artifacts_get():
    try:
        config = load_config()
        data   = _load_artifacts_json(config)
        result = {}
        for category, entries in data.items():
            result[category] = []
            for entry in entries:
                name      = _artifact_name(entry)
                yf        = _yaml_path(config, name)
                params    = []
                if yf.exists():
                    parsed = yaml.safe_load(yf.read_text()) or {}
                    params = [
                        {'name': p['name'], 'default': str(p.get('default', '') or '')}
                        for p in (parsed.get('parameters') or [])
                    ]
                result[category].append({
                    'entry':    entry,
                    'name':     name,
                    'has_yaml': yf.exists(),
                    'params':   params,
                })
        return jsonify(result)
    except Exception as exc:
        return jsonify({'error': str(exc)}), 500


@app.route('/api/artifacts', methods=['POST'])
def api_artifacts_save():
    try:
        config = load_config()
        data   = request.get_json() or {}
        for key in ('essential', 'full', 'skip'):
            if key not in data or not isinstance(data[key], list):
                return jsonify({'error': f'Missing or invalid key: {key}'}), 400
        _save_artifacts_json(config, {k: data[k] for k in ('essential', 'full', 'skip')})
        return jsonify({'saved': True})
    except Exception as exc:
        return jsonify({'error': str(exc)}), 500


@app.route('/api/artifacts/upload', methods=['POST'])
def api_artifacts_upload():
    try:
        config = load_config()
        if 'file' not in request.files:
            return jsonify({'error': 'No file provided'}), 400
        file     = request.files['file']
        category = request.form.get('category', 'essential')
        if category not in ('essential', 'full', 'skip'):
            return jsonify({'error': 'Invalid category'}), 400

        content = file.read().decode('utf-8')
        parsed  = yaml.safe_load(content)
        name    = (parsed or {}).get('name')
        if not name:
            return jsonify({'error': 'YAML file has no name field'}), 400

        _yaml_path(config, name).write_text(content)

        data        = _load_artifacts_json(config)
        all_entries = data['essential'] + data['full'] + data['skip']
        if not any(_artifact_name(e) == name for e in all_entries):
            data[category].append(f'{name}()')
            _save_artifacts_json(config, data)

        return jsonify({'name': name, 'category': category})
    except Exception as exc:
        return jsonify({'error': str(exc)}), 500


@app.route('/api/artifacts/<path:name>/yaml', methods=['POST'])
def api_artifact_yaml_save(name):
    try:
        config = load_config()
        yf     = _yaml_path(config, name)
        if not yf.exists():
            return jsonify({'error': 'YAML file not found'}), 404

        params  = (request.get_json() or {}).get('parameters', [])
        content = yaml.safe_load(yf.read_text()) or {}
        lookup  = {p['name']: p for p in (content.get('parameters') or [])}
        for p in params:
            if p['name'] in lookup:
                lookup[p['name']]['default'] = p['default']
        content['parameters'] = list(lookup.values())
        yf.write_text(yaml.dump(content, allow_unicode=True, sort_keys=False))
        return jsonify({'saved': True})
    except Exception as exc:
        return jsonify({'error': str(exc)}), 500


if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)
