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
        'zipfile_fullpath': None,
        'zipfile_size': None,
        'hostname': None,
        'artifacts': [],
        'artifacts_uploaded': [],
        'json_files_in_zip_uploaded': [],
        'added_hostname_as_column_to_file': [],
        'artifacts_to_be_processed': None,
        'statistics': {},
        'finished': None
    }

def define_results_postprocess_dict():
    return {
        'fullpath': None,
        'basename': None,
        'artifact': None,
        'size': None,
        'success': None,
        'duration': None,
        'error': None,
        'stdout': None,
        'stderr': None,
        'returncode': None
    }

def define_results_upload_dict():
    return {
        'fullpath': None,
        'basename': None,
        'size': None,
        'success': None,
        'duration': None,
        'error': None
    }

def define_results_addhostname_dict():
    return {
        'fullpath': None,
        'basename': None,
        'duration': None
    }

def add_results_as_list(result_zip, result_within_zip, key):
    ''' Adds results to a list in a dictionary. Either "artifacts" or "uploads" '''

    result_zip[key].append(result_within_zip)

    return result_zip
    
def add_info_to_results(result_zip, key, value):
    ''' Adds values from zip file to dictionary'''

    result_zip[key] = value
 
    return result_zip

def add_statistics_to_results(results: Dict[str, Any]) -> Dict[str, Any]:
    '''
    Adds a 'statistics' block to results without modifying or removing
    any existing keys or values.
    '''

    artifacts: List[Dict[str, Any]] = list(results.get('artifacts') or [])
    json_files_in_zip_uploaded: List[Dict[str, Any]] = list(results.get('json_files_in_zip_uploaded') or [])
    artifacts_uploaded: List[Dict[str, Any]] = list(results.get('artifacts_uploaded') or [])
    hostname_steps: List[Dict[str, Any]] = list(
        results.get('added_hostname_as_column_to_file') or []
    )

    def _is_num(v: Any) -> bool:
        return isinstance(v, (int, float)) and not isinstance(v, bool)

    def _sum(items: List[Dict[str, Any]], field: str) -> float:
        total = 0.0
        for item in items:
            v = item.get(field)
            if _is_num(v):
                total += float(v)
        return total

    def _safe_name(item: Optional[Dict[str, Any]]) -> Optional[str]:
        if not item:
            return None
        return item.get('artifact') or item.get('basename') or item.get('fullpath')

    # ---- slowest artifact ----
    slowest = None
    slowest_dur = None
    for a in artifacts:
        d = a.get('duration')
        if _is_num(d) and (slowest_dur is None or float(d) > float(slowest_dur)):
            slowest = a
            slowest_dur = d

    # ---- zero-byte artifacts ----
    zero_byte_artifacts = []
    for a in artifacts:
        size = a.get('size')
        if _is_num(size) and int(size) == 0:
            zero_byte_artifacts.append({
                'artifact': a.get('artifact'),
                'basename': a.get('basename'),
                'fullpath': a.get('fullpath'),
                'size': int(size),
            })

    nr_of_artifacts_to_be_postprocessed = len(results.get('artifacts_to_be_processed'))
    nr_of_artifacts_postprocessed = len(artifacts)
    nr_of_artifacts_failed_to_postprocess = nr_of_artifacts_to_be_postprocessed - nr_of_artifacts_postprocessed

    statistics = {
        'slowest_artifact': {
            'name': _safe_name(slowest),
            'duration_s': float(slowest_dur) if _is_num(slowest_dur) else None,
        },
        'zero_byte_artifacts': zero_byte_artifacts,
        'total_adding_hostname_duration_s': _sum(hostname_steps, 'duration'),
        'total_json_files_in_zip_upload_duration_s': _sum(json_files_in_zip_uploaded, 'duration'),
        'total_artifact_postprocessing_duration_s': _sum(artifacts, 'duration'),
        'nr_of_artifacts_failed_to_postprocess': nr_of_artifacts_failed_to_postprocess,
        'nr_of_artifacts_to_be_postprocessed': nr_of_artifacts_to_be_postprocessed,
        'nr_of_artifacts_postprocessed': nr_of_artifacts_postprocessed,
        'nr_of_json_files_in_zip_uploaded': len(json_files_in_zip_uploaded),
        'nr_of_postprocessed_artifacts_uploaded': len(artifacts_uploaded)
    }

    results['statistics'] = statistics
    return results

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
