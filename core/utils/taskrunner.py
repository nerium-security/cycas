'''
Run installer provisioning tasks in parallel and show their progress.

Each task runs in its own thread. Output from a task (step/info lines and
console log records) is handed to a status display instead of being printed
directly, so parallel tasks don't interleave:

- LiveDisplay: a table redrawn in place, one row per task. Used in
  interactive terminals.
- PlainDisplay: start/finish lines plus a periodic heartbeat. Used when
  output is not a terminal (CI, pipes, files). Under GitHub Actions each
  task's output is printed as a collapsible group when it finishes, failures
  and warnings become annotations, and a summary table is written to the
  job summary.

The full output of every task always goes to the log file.
'''

import logging
import os
import secrets
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, wait

log = logging.getLogger(__name__)

_task       = threading.local()
_display    = None
_print_lock = threading.Lock()


def current_task():
    '''Name of the task running in this thread, or None outside a task.'''
    return getattr(_task, 'name', None)


def emit(text='', file=None, warning=False, status=True):
    '''
    Print a line, or hand it to the status display when called from a task.

    warning marks lines that should be shown immediately even by displays
    that otherwise only show task start/finish. status=False keeps the line
    out of the task's status message (it is still kept in the task output).
    '''
    name = current_task()
    if name and _display:
        _display.line(name, text, warning, status)
        return
    with _print_lock:
        print(text, file=file or sys.stdout)


def format_duration(seconds):
    if seconds is None:
        return ''
    seconds = int(seconds)
    hours, rest = divmod(seconds, 3600)
    return f'{hours}:{rest // 60:02}:{rest % 60:02}' if hours else f'{rest // 60}:{rest % 60:02}'


def _status_text(text):
    text = text.strip()
    for prefix in ('>> ', '[OK] '):
        if text.startswith(prefix):
            return text[len(prefix):]
    return text


class _Task:
    def __init__(self, name, deps):
        self.name    = name
        self.deps    = deps
        self.state   = 'waiting'  # waiting, running, done, failed, skipped
        self.message = ''
        self.started = None
        self.ended   = None
        self.lines   = []

    def elapsed(self):
        if self.started is None:
            return None
        return (self.ended or time.monotonic()) - self.started


class _Display:
    def __init__(self, title, tasks):
        self.title   = title
        self.tasks   = {name: _Task(name, deps) for name, (_, deps) in tasks.items()}
        self.lock    = threading.Lock()
        self.started = time.monotonic()

    def start(self):
        pass

    def stop(self):
        pass

    def running(self, name):
        with self.lock:
            task = self.tasks[name]
            task.state, task.started = 'running', time.monotonic()
        self._on_running(task)

    def line(self, name, text, warning, status):
        with self.lock:
            task = self.tasks[name]
            task.lines.append(text)
            if status and text.strip():
                task.message = _status_text(text)
        if warning:
            self._on_warning(task, _status_text(text))

    def finish(self, name, state, error=None):
        with self.lock:
            task = self.tasks[name]
            task.state, task.ended = state, time.monotonic()
            if error:
                task.message = error
        self._on_finish(task)

    def waiting_for(self, task):
        return [d for d in task.deps if self.tasks[d].state in ('waiting', 'running')]

    def _on_running(self, task):
        pass

    def _on_warning(self, task, text):
        pass

    def _on_finish(self, task):
        pass


class LiveDisplay(_Display):
    '''A table redrawn in place: one row per task with its state, latest
    step, and elapsed time.'''

    _ICONS = {
        'waiting': '[dim]◦[/]',
        'done':    '[green]✔[/]',
        'failed':  '[red]✘[/]',
        'skipped': '[yellow]–[/]',
    }

    def start(self):
        from rich.console import Console
        from rich.live import Live
        from rich.spinner import Spinner

        self._spinners = {name: Spinner('dots', style='cyan') for name in self.tasks}
        self._live = Live(
            get_renderable=self._render, console=Console(), refresh_per_second=8,
            redirect_stdout=True, redirect_stderr=True,
        )
        self._live.start()

    def stop(self):
        self._live.stop()

    def _render(self):
        from rich.markup import escape
        from rich.table import Table

        table = Table(
            box=None, show_header=False, expand=True, padding=(0, 1, 0, 2),
            title=f'[bold]{self.title}[/]  [dim]{format_duration(time.monotonic() - self.started)}[/]',
            title_justify='left',
        )
        table.add_column(width=1)
        table.add_column(style='bold', no_wrap=True)
        table.add_column(ratio=1, no_wrap=True, overflow='ellipsis')
        table.add_column(justify='right', style='dim', no_wrap=True)

        with self.lock:
            for name, task in self.tasks.items():
                if task.state == 'running':
                    icon, message = self._spinners[name], escape(task.message)
                elif task.state == 'waiting':
                    icon    = self._ICONS['waiting']
                    message = f'[dim]waiting for {", ".join(self.waiting_for(task))}[/]'
                elif task.state == 'failed':
                    icon, message = self._ICONS['failed'], f'[red]{escape(task.message)}[/]'
                elif task.state == 'skipped':
                    icon, message = self._ICONS['skipped'], f'[yellow]{escape(task.message)}[/]'
                else:
                    icon, message = self._ICONS['done'], escape(task.message)
                table.add_row(icon, name, message, format_duration(task.elapsed()))
        return table


class PlainDisplay(_Display):
    '''Start/finish lines and a periodic heartbeat, for output that is not a
    terminal. Adds GitHub Actions groups, annotations, and a job summary when
    running in a GitHub Actions workflow.'''

    HEARTBEAT_SECONDS = 60

    def __init__(self, title, tasks):
        super().__init__(title, tasks)
        self.github = os.environ.get('GITHUB_ACTIONS') == 'true'
        self._stop  = threading.Event()

    def start(self):
        self._print(f'  {self.title}')
        threading.Thread(target=self._heartbeat, daemon=True).start()

    def stop(self):
        self._stop.set()
        if self.github:
            self._write_job_summary()

    def _print(self, text, file=None):
        with _print_lock:
            print(text, file=file or sys.stdout, flush=True)

    def _heartbeat(self):
        while not self._stop.wait(self.HEARTBEAT_SECONDS):
            with self.lock:
                running = [f'{t.name} ({format_duration(t.elapsed())})'
                           for t in self.tasks.values() if t.state == 'running']
            if running:
                self._print(f'  … still running: {", ".join(running)}')

    def _on_running(self, task):
        if not self.github:
            self._print(f'  ▶ {task.name}')

    def _on_warning(self, task, text):
        if self.github:
            self._print(f'::warning title={_gh_escape(task.name, prop=True)}::{_gh_escape(text)}')
        else:
            self._print(f'  ! {task.name}: {text}', file=sys.stderr)

    def _on_finish(self, task):
        duration = format_duration(task.elapsed())
        if task.state == 'done':
            summary = f'✔ {task.name} ({duration})'
        elif task.state == 'failed':
            summary = f'✘ {task.name} ({duration}): {task.message}'
        else:
            summary = f'– {task.name} skipped: {task.message}'

        if not self.github:
            self._print(f'  {summary}', file=sys.stderr if task.state != 'done' else None)
            return

        # Print the task output as one collapsible group. Workflow commands are
        # disabled inside it so tool output can't be interpreted as commands.
        token = secrets.token_hex(8)
        with _print_lock:
            print(f'::group::{summary}')
            print(f'::stop-commands::{token}')
            for line in task.lines:
                print(line)
            print(f'::{token}::')
            print('::endgroup::', flush=True)
        if task.state == 'failed':
            self._print(f'::error title={_gh_escape(task.name, prop=True)}::{_gh_escape(task.message)}')

    def _write_job_summary(self):
        path = os.environ.get('GITHUB_STEP_SUMMARY')
        if not path:
            return
        icons = {'done': '✔ done', 'failed': '✘ failed', 'skipped': '– skipped',
                 'running': 'running', 'waiting': 'not started'}
        rows = [f'### {self.title}', '', '| Task | Status | Duration |', '|---|---|---|']
        with self.lock:
            for task in self.tasks.values():
                rows.append(f'| {task.name} | {icons[task.state]} | {format_duration(task.elapsed())} |')
        try:
            with open(path, 'a', encoding='utf-8') as f:
                f.write('\n'.join(rows) + '\n\n')
        except OSError as e:
            log.warning(f'Could not write job summary: {e}')


def _gh_escape(text, prop=False):
    text = text.replace('%', '%25').replace('\r', '%0D').replace('\n', '%0A')
    if prop:
        text = text.replace(':', '%3A').replace(',', '%2C')
    return text


def _select_display():
    if sys.stdout.isatty() and os.environ.get('GITHUB_ACTIONS') != 'true':
        try:
            import rich  # noqa: F401
            return LiveDisplay
        except ImportError:
            pass
    return PlainDisplay


class DependencyFailed(Exception):
    pass


def run_tasks(tasks, title, log_file=None, detail_log=None):
    '''
    Run provisioning tasks concurrently and return their results by name.

    tasks maps a task name to (fn, deps). fn is called with a dict holding the
    results of the tasks named in deps, once those have finished; a task whose
    dependency failed is skipped. Deps must be listed before the tasks that
    use them.

    A failure does not cancel the other tasks: Azure operations already in
    flight cannot be aborted cleanly, and every step is idempotent, so letting
    them finish means a re-run has less left to do. Exits once all tasks are
    done if any of them failed.

    detail_log receives task tracebacks; it should write to the log file only,
    since the display already shows a one-line error.
    '''
    global _display

    detail_log = detail_log or log
    for name, (_, deps) in tasks.items():
        unknown = [d for d in deps if d not in list(tasks)[:list(tasks).index(name)]]
        if unknown:
            raise ValueError(f"Task '{name}' depends on {unknown}, which must be listed before it.")

    display  = _select_display()(title, tasks)
    executor = ThreadPoolExecutor(max_workers=len(tasks))
    futures  = {}

    def runner(name, fn, deps):
        threading.current_thread().name = name
        _task.name = name
        try:
            results = {}
            for dep in deps:
                try:
                    results[dep] = futures[dep].result()
                except BaseException as e:
                    raise DependencyFailed(dep) from e
            display.running(name)
            result = fn(results)
            display.finish(name, 'done')
            return result
        except DependencyFailed as e:
            display.finish(name, 'skipped', f"dependency '{e}' failed")
            detail_log.error("Task '%s' skipped: dependency '%s' failed.", name, e)
            raise
        except BaseException as e:
            display.finish(name, 'failed', str(e).strip().splitlines()[0] if str(e).strip() else type(e).__name__)
            detail_log.exception("Task '%s' failed.", name)
            raise
        finally:
            _task.name = None

    _display = display
    display.start()
    try:
        for name, (fn, deps) in tasks.items():
            futures[name] = executor.submit(runner, name, fn, deps)
        wait(futures.values())
    except KeyboardInterrupt:
        # Worker threads cannot be interrupted, and the interpreter would wait
        # for them on exit, so leave without waiting.
        display.stop()
        print('\n  Interrupted. Re-run easy_install.py and resume the saved session to continue.')
        logging.warning('Interrupted by user during parallel provisioning.')
        logging.shutdown()
        os._exit(130)
    display.stop()
    _display = None
    executor.shutdown()

    failed = [name for name, f in futures.items() if f.exception()]
    if failed:
        print(file=sys.stderr)
        print(f'  [ERROR] {title} failed: {", ".join(failed)}.', file=sys.stderr)
        if log_file:
            print(f'  Details in {log_file}', file=sys.stderr)
        print('  Fix the cause and re-run easy_install.py; completed resources are reused.', file=sys.stderr)
        sys.exit(1)

    return {name: f.result() for name, f in futures.items()}
