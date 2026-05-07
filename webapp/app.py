import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from flask import Flask, render_template, request, flash
from azure.identity import DefaultAzureCredential
from azure.data.tables import EntityProperty
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

PARTITION_KEY = 'pipeline-uploads'


def _unwrap(entry):
    return {k: (v.value if isinstance(v, EntityProperty) else v) for k, v in entry.items()}


def get_table_client():
    config = load_config()
    credential = DefaultAzureCredential()
    manager = TablestorageManager(credential, config.blob_logtable_uri, config.blob_logtable_name)
    manager.authenticate(verify_enabled=False)
    return manager, config.blob_logtable_name


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

        entries = sorted(raw, key=lambda e: e.get('StartTime') or '', reverse=True)

    except Exception as exc:
        error_msg = str(exc)
        table_name = os.environ.get('BLOB_LOGTABLE_NAME', 'status table')

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
    )


if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)
