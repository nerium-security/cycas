import json
import os
import csv
import logging as log
from typing import Any, Dict, List, Optional
from datetime import datetime
from copy import deepcopy

log = log.getLogger(__name__)

def _format_size(num_bytes):
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if num_bytes < 1024:
            return f'{num_bytes:.2f} {unit}' if unit != 'B' else f'{num_bytes} {unit}'
        num_bytes /= 1024
    return f'{num_bytes:.2f} TB'


def define_results_dict():
    return {
        'summary': [],
        'postprocessing': [],
        'uploads': [],
        'files_in_zip_ignored': [],
        'added_hostname_as_column_to_file': [],
        'statistics': {}
    }

def define_results_postprocess_dict():
    return {
        'fullpath': None,
        'basename': None,
        'artifact': None,
        'size': None,
        'success': None,
        'duration_in_sec': None,
        'error': None,
        'stdout': None,
        'stderr': None,
        'returncode': None
    }

def define_results_upload_dict():
    return {
        'location_in_zip': None,
        'basename': None,
        'size': None,
        'upload_initiated': None,
        'upload_initiated_timestamp': None,
        'upload_duration_in_sec': None,
        'upload_error': None,
        'was_postprocessed_with': None,
        'ignored_upload': None,
        'ignored_reason': None,
        'ignored_pattern': None,
        'added_hostname': None,
        'added_hostname_duration': None,
        'added_hostname_error': None
    }

def summary_per_zip_to_file(summary, extract_path, filename):
    '''Writes summary text to _summary.txt in the extraction folder.'''

    out_path = os.path.join(extract_path, filename)

    try:
        with open(out_path, 'w') as f:
            f.write(summary)
            log.info(f'Output summary file to: {out_path}')
    except Exception as e:
        log.error(f'Could not output summary. Error: {e}')

    return out_path

def pretty_print_summary_per_zip(zipfile, results, mode='full'):
    '''
    Builds and returns the formatted summary text for a ZIP file (new schema).

    mode:
        - 'full'    -> print full table
        - 'limited' -> print limited table, BUT still return full table text
    '''

    if mode not in ('full', 'limited'):
        mode = 'full'

    zipfile_fullpath = zipfile or results.get('zipfile_fullpath', '')
    hostname = results.get('hostname')
    zipfile_size = results.get('zipfile_size')

    artifacts = results.get('artifacts', []) or []

    def build_table(table_mode):
        output_lines = []

        # ---- Header ----
        output_lines.append(f'ZIP fullpath:\t {zipfile_fullpath}')
        output_lines.append(f'ZIP size:\t {_format_size(zipfile_size)}')
        if hostname:
            output_lines.append(f'Hostname:\t {hostname}')
        output_lines.append('Summary table:\n')

        rows_data = []
        total_size = 0
        total_duration = 0.0
        success_count = 0
        total_items = len(artifacts)

        for a in artifacts:
            artifact = a.get('artifact', '')
            size = a.get('size')
            duration = a.get('duration')
            success = a.get('success')
            error = a.get('error')
            returncode = a.get('returncode')

            # ---- status ----
            if success is True:
                status_str = 'OK'
                success_count += 1
            elif success is False:
                status_str = 'FAILED'
            else:
                status_str = ''

            # ---- size ----
            if isinstance(size, int):
                size_str = str(size)
                total_size += size
            else:
                size_str = ''

            # ---- duration ----
            if isinstance(duration, (int, float)):
                dur_str = f'{duration:.2f} sec'
                total_duration += float(duration)
            else:
                dur_str = ''

            # ---- error ----
            if error is None:
                error_str = ''
            else:
                error_str = str(error)

            # ---- returncode ----
            if isinstance(returncode, int):
                returncode_str = str(returncode)
            else:
                returncode_str = ''

            rows_data.append({
                'artifact': artifact,
                'size': size_str,
                'duration': dur_str,
                'status': status_str,
                'error': error_str,
                'returncode': returncode_str,
            })

        totals_row_data = {
            'artifact': 'TOTALS',
            'size': str(total_size),
            'duration': f'{total_duration:.2f} sec' if total_duration else '',
            'status': f'{success_count} out of {total_items}',
            'error': '',
            'returncode': '',
        }

        if table_mode == 'full':
            headers = [
                'artifact',
                'outputfile size',
                'duration',
                'status',
                'error',
                'returncode',
            ]
            order = ['artifact', 'size', 'duration', 'status', 'error', 'returncode']
        else:
            headers = [
                'artifact',
                'outputfile size',
                'duration',
                'status',
            ]
            order = ['artifact', 'size', 'duration', 'status']

        rows = []
        for rd in rows_data:
            rows.append([rd[key] for key in order])

        totals_row = [totals_row_data[key] for key in order]
        rows.append(totals_row)

        col_widths = [
            max(len(str(row[i])) for row in rows + [headers])
            for i in range(len(headers))
        ]

        def line():
            return '+' + '+'.join('-' * (w + 2) for w in col_widths) + '+'

        def row(values):
            return '|' + '|'.join(
                f' {str(values[i]).ljust(col_widths[i])} ' for i in range(len(values))
            ) + '|'

        output_lines.append(line())
        output_lines.append(row(headers))
        output_lines.append(line())

        for r in rows[:-1]:
            output_lines.append(row(r))

        output_lines.append(line())
        output_lines.append(row(totals_row))
        output_lines.append(line())

        return '\n'.join(output_lines)

    full_text = build_table('full')
    print_text = full_text if mode == 'full' else build_table('limited')

    print('\n')
    print(print_text, '\n')

    return full_text

def merge_master_table_with_file(master_results, output_path):
    '''
    Writes master_results (list of dicts) to a CSV file, appending if file exists,
    and writing the header only when needed.
    '''

    if not master_results:
        raise ValueError('master_results is empty, nothing to write.')

    fieldnames = list(master_results[0].keys())

    try:
        # Ensure directory exists
        os.makedirs(os.path.dirname(output_path), exist_ok=True)

        # Determine whether to write header
        write_header = not os.path.exists(output_path) or os.path.getsize(output_path) == 0

        # Open file in append mode ('a')
        with open(output_path, 'a', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)

            if write_header:
                writer.writeheader()

            writer.writerows(master_results)

        print(f'CSV updated: {output_path}')
        return output_path

    except Exception as e:
        print(f'Failed to write CSV: {e}')
        return None
    
def build_all_zip_summary(per_zip_results):
    ''' Builds the entry for all zip summary (master summary) for the new schema. '''

    artifacts = per_zip_results.get('artifacts', []) or []

    total_processing = sum(
        a.get('duration') for a in artifacts
        if isinstance(a.get('duration'), (int, float))
    )

    success_count = sum(1 for a in artifacts if a.get('success') is True)
    fail_count = sum(1 for a in artifacts if a.get('success') is False)

    return {
        'zipfile': per_zip_results.get('zipfile_fullpath'),
        'hostname': per_zip_results.get('hostname'),
        'total_processing': total_processing,
        'total_size_bytes': per_zip_results.get('zipfile_size'),
        'success_count': success_count,
        'fail_count': fail_count,
    }


def get_duration_from_timespan(start):
    ''' 
    Used to calculate the duration to output it in a human-friendly manner.
    
    Args:
        start = datetime.now()
    '''

    end = datetime.now()
    duration_seconds = (end - start).total_seconds()
    minutes, seconds = divmod(duration_seconds, 60)

    return f'{int(minutes)}m {int(seconds)}s'

def adding_seconds(summary):
    '''
    Adds the unit "seconds" to be able to pretty print the results
    of artifact post-processing.
    '''

    return {k: f"{v:.2f} seconds" for k, v in summary.items()}

def calculate_total(summary):
    ''' 
    Sums the total of post-processing time and returns it with two
    decimals after the comma
    '''

    return round(sum(v for v in summary.values() if v is not None), 2)

def collecting_data_for_summary(duration):
    ''' Returns value with only 2 decimals after comma '''

    return round(duration, 2)

def generate_summary_postprocessing(summary, zipfile, extracted_zip, filename):
    ''' Generates a summary of post-processing time and dumps it to stdout and a file '''

    summary[zipfile]['total'] = calculate_total(summary[zipfile])
    summary[zipfile] = adding_seconds(summary[zipfile])
    summary_json = json.dumps(summary, indent=4)

    log.info('Printing summary:')
    print('\n', summary_json, '\n')

    fullpath = os.path.join(extracted_zip, filename)
    log.info(f'Outputting summary to: {fullpath}')
    with open(fullpath, 'w') as f:
        f.write(summary_json)


def pretty_print_master_table(master_results):
    ''' Pretty prints the master table to terminal AND returns the final text. '''

    output_lines = []   # collect lines for return

    header_text = '\nMaster summary table:\n'
    print(header_text)
    output_lines.append(header_text.rstrip('\n'))

    headers = ['ZIP File', 'Hostname', 'Duration', 'Size', 'OK', 'Failed']

    # ---- Build regular rows ----
    rows = []
    for r in master_results:
        rows.append([
            os.path.basename(r['zipfile']),
            r['hostname'],
            f'{r["total_processing"]:.2f} sec',
            _format_size(r['total_size_bytes']),
            str(r['success_count']),
            str(r['fail_count'])
        ])

    # ---- Compute TOTALS ----
    total_processing = round(
        sum(r['total_processing'] for r in master_results),
        2
    )

    total_size_bytes = sum(r['total_size_bytes'] for r in master_results)
    total_size_hr = _format_size(total_size_bytes)

    # The TOTAL row uses empty placeholders for non-summary columns
    total_row = [
        '',
        '',
        f'{total_processing:.2f} sec',
        f'{total_size_hr}',
        '',
        ''
    ]

    # Append TOTAL row
    rows.append(total_row)

    # ---- Column width calculation ----
    col_widths = [
        max(len(str(row[i])) for row in rows + [headers])
        for i in range(len(headers))
    ]

    def line():
        return '+' + '+'.join('-' * (w + 2) for w in col_widths) + '+'

    def row(values):
        return '|' + '|'.join(
            f' {str(values[i]).ljust(col_widths[i])} '
            for i in range(len(values))
        ) + '|'

    # ---- Build table output ----
    table_lines = [
        line(),
        row(headers),
        line(),
    ]

    # all normal rows except final row
    for r in rows[:-1]:
        table_lines.append(row(r))

    # separator + totals row
    table_lines.append(line())
    table_lines.append(row(rows[-1]))
    table_lines.append(line())
    table_lines.append('')   # blank line

    # ---- Print + record each line ----
    for t in table_lines:
        print(t)
        output_lines.append(t)

    # ---- Return the entire table as a string ----
    return '\n'.join(output_lines)
