'''
Summary and reporting utilities for ingestion pipeline results.

Provides helper functions to:
    - Define standard results dictionaries used across the pipeline
    - Format sizes and durations for human-readable output
    - Build and pretty-print per-zip and master summary tables
    - Write per-zip summaries to disk and append master summaries to CSV

Most functions operate on the pipeline 'results' structure, which contains
a 'summary' list and detailed lists such as 'postprocessing' and 'uploads'.
'''

import json
import os
import csv
import logging as log
from typing import Any, Dict, List, Optional
from datetime import datetime
from copy import deepcopy

log = log.getLogger(__name__)

def _format_size(num_bytes):
    '''
    Format a byte count as a human-readable size string.

    Args:
        num_bytes (int | float): Size in bytes.

    Returns:
        str: Human-readable size (e.g. '532 B', '1.23 MB', '4.00 GB').
    '''

    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if num_bytes < 1024:
            return f'{num_bytes:.2f} {unit}' if unit != 'B' else f'{num_bytes} {unit}'
        num_bytes /= 1024
    return f'{num_bytes:.2f} TB'

def define_results_dict():
    '''
    Create a new empty results dictionary for a single zipfile run.

    Returns:
        dict: Results dictionary
    '''

    return {
        'summary': [],
        'postprocessing': [],
        'uploads': [],
        'files_in_zip_ignored': [],
        'added_hostname_as_column_to_file': []
    }

def define_results_postprocess_dict():
    '''
    Create a default results dictionary for a single post-processing artifact.

    Returns:
        dict: Post-processing result structure with standard fields.
    '''

    return {
        'fullpath': None,
        'basename': None,
        'artifact': None,
        'cmd': None,
        'size': None,
        'success': None,
        'duration_in_sec': None,
        'error': None,
        'stdout': None,
        'stderr': None,
        'returncode': None
    }

def define_results_upload_dict():
    '''
    Create a default results dictionary for a single upload operation.

    Returns:
        dict: Upload result structure
    '''

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
    '''
    Write a per-zip summary string to a file in the extraction directory.

    Args:
        summary (str): Summary text to write.
        extract_path (str): Directory where the summary file should be written.
        filename (str): Output filename to use.

    Returns:
        str: Full path to the written summary file.
    '''

    out_path = os.path.join(extract_path, filename)

    try:
        with open(out_path, 'w') as f:
            f.write(summary)
            log.info(f'Output summary file to: {out_path}')
    except Exception as e:
        log.error(f'Could not output summary. Error: {e}')

    return out_path

def pretty_print_summary_per_zip(outputfolder, results, mode='full'):
    '''
    Build and print a formatted summary table for a single zipfile.

    The function always builds a full summary table and returns its text.
    Depending on `mode`, it prints either the full table or a limited table.

    Args:
        outputfolder (str): Output folder path shown in the header.
        results (dict): Results dictionary for a single zipfile run. Expects
            `results['summary'][0]` and `results['postprocessing']`.
        mode (str): Print mode. Supported values:
            - 'full': print the full table
            - 'limited': print a limited table, while still returning the full text

    Returns:
        str: Full summary table text.
    '''

    if mode not in ('full', 'limited'):
        mode = 'full'

    # ---- Pull summary info (new schema) ----
    summary_list = results.get('summary', [])
    summary = summary_list[0]

    zipfile_fullpath = summary.get('zipfile_fullpath')
    hostname = summary.get('hostname', '<NotExtracted>')
    zipfile_size = summary.get('zipfile_size')

    # Artifacts now live under "postprocessing"
    artifacts = results.get('postprocessing')

    def build_table(table_mode):
        output_lines = []
        
        # ---- Header ----
        output_lines.append(f'ZIP fullpath:\t {zipfile_fullpath}')
        output_lines.append(f'ZIP size:\t {_format_size(zipfile_size)}')
        output_lines.append(f'Hostname:\t {hostname}')
        output_lines.append(f'Outputfolder:\t {outputfolder}')
        output_lines.append('Summary table:\n')

        rows_data = []
        total_size = 0
        total_duration = 0.0
        success_count = 0
        total_items = len(artifacts)

        for a in artifacts:
            artifact = a.get('artifact', '')
            size = a.get('size')
            duration = a.get('duration_in_sec')  # <-- new key
            success = a.get('success')
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
                dur_str = f'{float(duration):.2f} sec'
                total_duration += float(duration)
            else:
                dur_str = ''

            # ---- returncode ----
            returncode_str = str(returncode) if isinstance(returncode, int) else ''

            rows_data.append({
                'artifact': artifact,
                'size': size_str,
                'duration': dur_str,
                'status': status_str,
                'returncode': returncode_str,
            })

        totals_row_data = {
            'artifact': 'TOTALS',
            'size': str(total_size),
            'duration': f'{total_duration:.2f} sec' if total_duration else '',
            'status': f'{success_count} out of {total_items}',
            'returncode': '',
        }

        if table_mode == 'full':
            headers = [
                'artifact',
                'outputfile size',
                'duration',
                'status',
                'returncode',
            ]
            order = ['artifact', 'size', 'duration', 'status', 'returncode']
        else:
            headers = [
                'artifact',
                'outputfile size',
                'duration',
                'status',
            ]
            order = ['artifact', 'size', 'duration', 'status']

        rows = [[rd[key] for key in order] for rd in rows_data]
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
    Append master summary rows to a CSV file, creating it if needed.

    Writes `master_results` (a list of dictionaries) into `output_path`.
    If the file does not exist or is empty, a header row is written first.

    Args:
        master_results (list[dict]): Master summary rows to write.
        output_path (str): Destination CSV file path.

    Returns:
        str or None: Output path if writing succeeds, otherwise None.
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
    '''
    Build a master summary row for a single zipfile run.

    Aggregates post-processing durations, output sizes, and success/failure
    counts across artifacts for the provided per-zip results structure.

    Args:
        per_zip_results (dict): Results dictionary for a single zipfile run.
            Expects `per_zip_results['summary'][0]` and `per_zip_results['postprocessing']`.

    Returns:
        dict: Aggregated summary row containing:
            - zipfile
            - hostname
            - total_processing
            - total_size_bytes
            - success_count
            - fail_count
    '''

    artifacts = per_zip_results.get('postprocessing', [])
    hostname = per_zip_results['summary'][0].get('hostname')
    basename = per_zip_results['summary'][0].get('zipfile_basename')

    total_processing_duration = sum(
        a.get('duration_in_sec') for a in artifacts
        if isinstance(a.get('duration_in_sec'), (int, float))
    )

    total_processing_bytes = sum(
        a.get('size') for a in artifacts
        if isinstance(a.get('size'), (int, float))
    )

    success_count = sum(1 for a in artifacts if a.get('success') is True)
    fail_count = sum(1 for a in artifacts if a.get('success') is False)

    return {
        'zipfile': basename,
        'hostname': hostname,
        'total_processing': total_processing_duration,
        'total_size_bytes': total_processing_bytes,
        'success_count': success_count,
        'fail_count': fail_count,
    }

def get_duration_from_timespan(start):
    '''
    Format the elapsed time since `start` as minutes and seconds.

    Args:
        start (datetime): Start timestamp.

    Returns:
        str: Duration formatted as '<minutes>m <seconds>s'.
    '''

    end = datetime.now()
    duration_seconds = (end - start).total_seconds()
    minutes, seconds = divmod(duration_seconds, 60)

    return f'{int(minutes)}m {int(seconds)}s'

def adding_seconds(summary):
    '''
    Append a 'seconds' unit to numeric duration values for display.

    Args:
        summary (dict[str, float]): Mapping of labels to duration values
            in seconds.

    Returns:
        dict[str, str]: Mapping with durations formatted as strings with
        two decimals and a trailing 'seconds'.
    '''

    return {k: f"{v:.2f} seconds" for k, v in summary.items()}

def calculate_total(summary):
    '''
    Sum all non-None values in a duration mapping.

    Args:
        summary (dict[str, float | None]): Mapping of labels to duration values.

    Returns:
        float: Total duration rounded to two decimals.
    '''

    return round(sum(v for v in summary.values() if v is not None), 2)

def collecting_data_for_summary(duration):
    '''
    Round a duration value to two decimal places.

    Args:
        duration (int | float): Duration value in seconds.

    Returns:
        float: Duration rounded to two decimals.
    '''

    return round(duration, 2)

def generate_summary_postprocessing(summary, zipfile, extracted_zip, filename):
    '''
    Generate and persist a JSON summary of post-processing durations.

    Computes the total duration for a zipfile entry, formats values for
    readability, prints the JSON summary to stdout, and writes it to a file
    under the extracted zip directory.

    Args:
        summary (dict): Summary mapping keyed by zipfile, containing timing values.
        zipfile (str): Zipfile key used to locate the per-zip summary entry.
        extracted_zip (str): Directory where the summary file will be written.
        filename (str): Output filename for the summary JSON.
    '''

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
    '''
    Pretty-print the master summary table and return its full text.

    Builds an ASCII table showing per-zip totals (duration, output size,
    success/failure counts) and appends a final TOTAL row aggregating
    duration and output size across all rows.

    Args:
        master_results (list[dict]): Master summary rows. Each row is expected
            to include keys such as 'zipfile', 'hostname', 'total_processing',
            'total_size_bytes', 'success_count', and 'fail_count'.

    Returns:
        str: Full printed table text.
    '''

    output_lines = []   # collect lines for return

    header_text = '\nMaster summary table:\n'
    print(header_text)
    output_lines.append(header_text.rstrip('\n'))

    headers = ['ZIP File', 'Hostname', 'Total duration', 'Total output size', 'OK', 'Failed']

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