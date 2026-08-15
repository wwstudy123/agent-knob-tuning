import requests
import json
import pymysql
import os
import sys
import time
import re
import shutil
import paramiko
import configparser
import hashlib
import statistics
import platform
from decision_controller import DecisionController, TrialResult

config_parser = configparser.ConfigParser()
config_parser.read(os.environ.get('AGENTTUNE_CONFIG', './config.ini'))

db_ip = config_parser['configuration recommender']['DB_IP']
ip_password = config_parser['configuration recommender']['DB_IP_Password']
db_config = {
    'user': config_parser['configuration recommender']['DB_User'],
    'password': config_parser['configuration recommender']['DB_Password'],
    'host': config_parser['configuration recommender']['DB_Host'],
    'database': config_parser['configuration recommender']['DB_Name'],
    'port': int(config_parser['configuration recommender']['DB_Port']),
    'unix_socket': '/tmp/mysql8.sock',
    'connect_timeout': 30,
    'read_timeout': 300,
    'write_timeout': 300,
}
LAST_BENCHMARK_RESULT = {}

MYSQL_BASE = '/workspace/setup/mysql-8.0'
MYCNF_PATH = '/etc/my8.cnf'
MYCNF_BAK = '/etc/my8.cnf.bak'
MYSQL_START_CMD = (
    f'{MYSQL_BASE}/bin/mysqld_safe --defaults-file={MYCNF_PATH} --user=mysql '
    '>/tmp/mysqld8_safe.out 2>&1 &'
)

with open(config_parser['knob selector']['candidate_knobs'], 'r') as f:
    original = json.load(f)
    original_keys = list(original.keys())

with open(config_parser['range pruner']['output_file'], 'r') as f:
    selected_knobs = json.load(f)

BENCHMARK_WARMUP_RUNS = config_parser.getint(
    'configuration recommender', 'benchmark_warmup_runs', fallback=0
)
BENCHMARK_REPETITIONS = config_parser.getint(
    'configuration recommender', 'benchmark_repetitions', fallback=1
)
BENCHMARK_REQUIRE_ALL = config_parser.getboolean(
    'configuration recommender', 'benchmark_require_all', fallback=True
)
BENCHMARK_STATEMENT_TIMEOUT_MS = config_parser.getint(
    'configuration recommender', 'statement_timeout_ms', fallback=180000
)
EXPECTED_STATEMENT_COUNT = config_parser.getint(
    'configuration recommender', 'expected_statement_count', fallback=0
)
POST_RESTART_STABILIZATION_SEC = config_parser.getint(
    'configuration recommender', 'post_restart_stabilization_sec', fallback=10
)
# Comma-separated basenames without .sql, e.g. q012,q021,q034
_TPCDS_SKIP_RAW = config_parser.get(
    'configuration recommender', 'tpcds_skip_queries', fallback=''
)
TPCDS_SKIP_QUERIES = set()
for name in _TPCDS_SKIP_RAW.split(','):
    name = name.strip().lower()
    if name.endswith('.sql'):
        name = name[:-4]
    if name:
        TPCDS_SKIP_QUERIES.add(name)
_UNSUPPORTED_SQL = re.compile(r'(?is)\b(intersect|except)\b')
# INTERSECT/EXCEPT require MySQL >= 8.0.31. Cached after first probe.
_MYSQL_VERSION_TUPLE = None
_MYSQL_SUPPORTS_INTERSECT = None


def _parse_mysql_version(version_str):
    """Return (major, minor, patch) from a MySQL VERSION() string."""
    match = re.search(r'(\d+)\.(\d+)\.(\d+)', str(version_str) or '')
    if not match:
        return (0, 0, 0)
    return tuple(int(x) for x in match.groups())


def _probe_mysql_intersect_support(cursor=None):
    """Detect whether the live server supports INTERSECT/EXCEPT (>= 8.0.31)."""
    global _MYSQL_VERSION_TUPLE, _MYSQL_SUPPORTS_INTERSECT
    if _MYSQL_SUPPORTS_INTERSECT is not None:
        return _MYSQL_SUPPORTS_INTERSECT

    own_conn = None
    try:
        if cursor is None:
            own_conn = pymysql.connect(**db_config)
            cursor = own_conn.cursor()
        cursor.execute('SELECT VERSION()')
        version_str = cursor.fetchone()[0]
        _MYSQL_VERSION_TUPLE = _parse_mysql_version(version_str)
        _MYSQL_SUPPORTS_INTERSECT = _MYSQL_VERSION_TUPLE >= (8, 0, 31)
        print(
            f'MySQL version={version_str} '
            f'intersect_except_supported={_MYSQL_SUPPORTS_INTERSECT}',
            flush=True,
        )
    except Exception as e:
        # Fail closed for old environments if probe fails.
        _MYSQL_VERSION_TUPLE = (0, 0, 0)
        _MYSQL_SUPPORTS_INTERSECT = False
        print(f'Warning: cannot probe MySQL version ({e}); '
              f'assuming INTERSECT/EXCEPT unsupported', flush=True)
    finally:
        if own_conn is not None:
            own_conn.close()
    return _MYSQL_SUPPORTS_INTERSECT


def _restart_mysql():
    """Restart MySQL 8.0 using the user-provided mysqld_safe command."""
    stop_cmd = (
        "pkill -9 -f '/workspace/setup/mysql/bin/mysqld' 2>/dev/null; "
        "pkill -9 -f '/workspace/setup/mysql-8.0/bin/mysqld' 2>/dev/null; "
        "pkill -9 -f 'mysqld_safe' 2>/dev/null; "
        "rm -f /tmp/mysql8.sock /tmp/mysql8.sock.lock 2>/dev/null; "
        "sleep 2"
    )
    os.system(stop_cmd)
    print(f'starting MySQL with: {MYSQL_START_CMD}', flush=True)
    return os.system(MYSQL_START_CMD)


def _format_mycnf_value(value):
    if isinstance(value, bool):
        return '1' if value else '0'
    return str(value)


def _knob_key_to_mysql_name(knob_key):
    if isinstance(knob_key, str) and knob_key.startswith('knob') and knob_key[4:].isdigit():
        index = int(knob_key.replace('knob', '')) - 1
        if 0 <= index < len(original_keys):
            return original_keys[index]
    return knob_key


def apply_knobs_to_mycnf(knob_vars, mycnf_path=MYCNF_PATH, backup_path=MYCNF_BAK, section='mysqld'):
    """Restore my.cnf from backup and merge knob vars into [mysqld]."""
    if os.path.exists(backup_path):
        shutil.copy2(backup_path, mycnf_path)
    elif not os.path.exists(mycnf_path):
        print(f'Error: neither {backup_path} nor {mycnf_path} exists')
        return False
    else:
        # Capture a stable baseline once. Every trial starts from this file so
        # knobs from prior trials cannot leak into the next configuration.
        try:
            shutil.copy2(mycnf_path, backup_path)
            print(f'created baseline MySQL config backup: {backup_path}', flush=True)
        except OSError as e:
            print(f'Error: cannot create baseline backup {backup_path}: {e}', flush=True)
            return False

    if not knob_vars:
        return True

    with open(mycnf_path, 'r', encoding='utf-8') as f:
        lines = f.readlines()

    section_lower = section.lower()
    in_target = False
    section_found = False
    remaining = dict(knob_vars)
    new_lines = []

    for line in lines:
        stripped = line.strip()
        if stripped.startswith('[') and stripped.endswith(']'):
            if in_target and remaining:
                for key, val in remaining.items():
                    new_lines.append(f'{key}={_format_mycnf_value(val)}\n')
                remaining.clear()
            sec_name = stripped[1:-1].strip().lower()
            in_target = sec_name == section_lower
            if in_target:
                section_found = True
            new_lines.append(line)
            continue

        if in_target and stripped and not stripped.startswith('#') and '=' in stripped:
            key = stripped.split('=', 1)[0].strip()
            if key in remaining:
                new_lines.append(f'{key}={_format_mycnf_value(remaining.pop(key))}\n')
                continue

        new_lines.append(line)

    if in_target and remaining:
        for key, val in remaining.items():
            new_lines.append(f'{key}={_format_mycnf_value(val)}\n')
        remaining.clear()

    if not section_found:
        if new_lines and not new_lines[-1].endswith('\n'):
            new_lines.append('\n')
        new_lines.append(f'[{section}]\n')
        for key, val in knob_vars.items():
            new_lines.append(f'{key}={_format_mycnf_value(val)}\n')

    with open(mycnf_path, 'w', encoding='utf-8') as f:
        f.writelines(new_lines)
    return True


def apply_temp_config_to_mycnf(temp_config):
    mysql_vars = {
        _knob_key_to_mysql_name(key): temp_config[key]
        for key in temp_config
    }
    return apply_knobs_to_mycnf(mysql_vars)

def get_current_metric():

    conn = pymysql.connect(**db_config)
    cursor = conn.cursor()

    sql = "select name,count from information_schema.INNODB_METRICS where status = 'enabled'"
    cursor.execute(sql)
    result = cursor.fetchall() 
    knobs = {}
    for i in result:
        #print(f"\"{i[0]}\" : {i[1]},")
        knobs[i[0]] = int(i[1])
    json_data = json.dumps(knobs, indent=4)
    #print(json_data)
    return knobs

def get_current_knob():

    conn = pymysql.connect(**db_config)
    cursor = conn.cursor()

    knobs = {}
    parameters = []
    for knob_key in selected_knobs.keys():
        parameters.append((knob_key, _knob_key_to_mysql_name(knob_key)))

    for knob_key, param in parameters:
        cursor.execute(f"SHOW VARIABLES LIKE '{param}'")
        result = cursor.fetchone()
        if result:
            try:
                # Keep the same canonical knobN key space used by the LLM,
                # surrogate and persisted history.
                knobs[knob_key] = int(result[1]) if result[1].isdigit() else round(float(result[1]))
            except ValueError:
                knobs[knob_key] = result[1]

    cursor.close()
    conn.close()
    json_data = json.dumps(knobs, indent=4)
    print(json_data)
    return knobs


def get_knobs_detail():
    f = open(config_parser['range pruner']['output_file'], 'r')
    content = json.load(f)
    #content = set_expert_rule(content)

    result = {}
    count = 0
    for i in content.keys():
        result[i] = content[i]
        count += 1
    
    return result

def _resolve_enum_values(knob_key, knobs_detail):
    enum_values = knobs_detail.get(knob_key, {}).get('enum_values') or []
    if enum_values:
        return enum_values
    # pruned_knobs sometimes drops enum_values; fall back to candidate_knobs
    mysql_name = _knob_key_to_mysql_name(knob_key)
    return (original.get(mysql_name) or {}).get('enum_values') or []


def _build_temp_config_from_knob(knob):
    temp_config = {}
    knobs_detail = get_knobs_detail()
    for key in knobs_detail.keys():
        if key not in knob.keys():
            continue
        knob_type = knobs_detail[key].get('type')
        if knob_type == 'integer':
            temp_config[key] = knob.get(key)
        elif knob_type == 'enum':
            raw = knob.get(key)
            enum_values = _resolve_enum_values(key, knobs_detail)
            value = str(raw)
            if value in enum_values:
                temp_config[key] = value
            elif isinstance(raw, int) and 0 <= raw < len(enum_values):
                temp_config[key] = enum_values[raw]
            elif value.isdigit() and 0 <= int(value) < len(enum_values):
                temp_config[key] = enum_values[int(value)]
            else:
                print(f"Warning: {value} not found in enum values for {key}: {enum_values}")
    return temp_config


def _wait_for_mysql(timeout_sec=90, poll_sec=2):
    """Return True once pymysql can connect; False if MySQL never comes up."""
    deadline = time.time() + timeout_sec
    last_err = None
    sock = db_config.get('unix_socket')
    while time.time() < deadline:
        if sock and not os.path.exists(sock):
            print(f'waiting for mysql socket {sock} ...', flush=True)
        else:
            try:
                conn = pymysql.connect(**db_config)
                conn.close()
                print('mysql is accepting connections', flush=True)
                return True
            except Exception as e:
                last_err = e
                print(f'waiting for mysql connect: {e}', flush=True)
        time.sleep(poll_sec)
    print(f'mysql did not become ready within {timeout_sec}s; last_error={last_err}', flush=True)
    return False


def _rollback_to_baseline():
    """Restore, restart and verify the known-good baseline configuration."""
    if not os.path.exists(MYCNF_BAK):
        print(f'Rollback unavailable: missing {MYCNF_BAK}', flush=True)
        return False
    try:
        shutil.copy2(MYCNF_BAK, MYCNF_PATH)
    except OSError as e:
        print(f'Rollback copy failed: {e}', flush=True)
        return False
    if _restart_mysql() != 0:
        print('Rollback restart command failed', flush=True)
        return False
    ready = _wait_for_mysql(timeout_sec=90)
    print(
        'baseline rollback verified' if ready else 'baseline rollback did not become ready',
        flush=True,
    )
    return ready


def _apply_knobs_and_restart(knob):
    temp_config = _build_temp_config_from_knob(knob)
    if not apply_temp_config_to_mycnf(temp_config):
        return 1
    time.sleep(10)
    print("success set knobs")
    state = _restart_mysql()
    if state != 0:
        _rollback_to_baseline()
        return state
    if not _wait_for_mysql(timeout_sec=90):
        print(
            'MySQL failed to start after knob apply. Check error log, e.g.\n'
            f'  ls -t {MYSQL_BASE}/data/*.err | head -1 | xargs tail -n 100\n'
            'Often caused by bad knobs in my.cnf — restore backup:\n'
            f'  sudo cp {MYCNF_BAK} {MYCNF_PATH}',
            flush=True,
        )
        # Best-effort rollback to a known-good baseline. The failed candidate is
        # still returned as throughput=0 by the caller.
        _rollback_to_baseline()
        return 1
    if POST_RESTART_STABILIZATION_SEC > 0:
        print(
            f'waiting {POST_RESTART_STABILIZATION_SEC}s for post-restart stabilization',
            flush=True,
        )
        time.sleep(POST_RESTART_STABILIZATION_SEC)
    return 0


def _split_sql_content(content, source_label, allow_intersect_except=None):
    """Split one SQL file body into executable statements."""
    if allow_intersect_except is None:
        allow_intersect_except = bool(_MYSQL_SUPPORTS_INTERSECT)
    content = re.sub(r'/\*.*?\*/', '', content, flags=re.DOTALL)
    lines = content.splitlines()
    while lines and lines[0].startswith('--'):
        lines.pop(0)
    content = '\n'.join(lines).lstrip('\n')
    statements = []
    for part in re.split(r';\s*\n', content):
        stmt = part.strip()
        if not stmt:
            continue
        if (not allow_intersect_except) and _UNSUPPORTED_SQL.search(stmt):
            ver = '.'.join(str(x) for x in (_MYSQL_VERSION_TUPLE or (8, 0, 21)))
            raise ValueError(
                f'{source_label} contains INTERSECT/EXCEPT unsupported by MySQL {ver} '
                f'(need >= 8.0.31)'
            )
        statements.append(stmt)
    return statements


def _parse_sql_file(sql_path, allow_intersect_except=None):
    with open(sql_path, 'r', encoding='utf-8', errors='ignore') as f:
        content = f.read()
    return _split_sql_content(content, sql_path, allow_intersect_except=allow_intersect_except)


def _workload_entries(workload_path, allow_intersect_except=None):
    """
    Return ordered list of {source, sql} entries.
    workload_path may be a single .sql file or a directory of q*.sql files.
    """
    if allow_intersect_except is None:
        allow_intersect_except = bool(_MYSQL_SUPPORTS_INTERSECT)
    configured = config_parser['workload analyzer']['workload_file']
    is_configured = os.path.normpath(workload_path) == os.path.normpath(configured)

    if os.path.isdir(workload_path):
        files = sorted(
            f for f in os.listdir(workload_path)
            if re.fullmatch(r'q\d{3}\.sql', f, flags=re.IGNORECASE)
        )
        if not files:
            raise ValueError(f'{workload_path} has no qNNN.sql files')
        entries = []
        skipped = []
        for name in files:
            qid = os.path.splitext(name)[0].lower()
            path = os.path.join(workload_path, name)
            if qid in TPCDS_SKIP_QUERIES:
                skipped.append(name)
                continue
            try:
                stmts = _parse_sql_file(path, allow_intersect_except=allow_intersect_except)
            except ValueError as e:
                # Keep directory mode useful: skip known-incompatible files loudly.
                print(f'[skip] {name}: {e}', flush=True)
                skipped.append(name)
                continue
            for i, stmt in enumerate(stmts, 1):
                label = name if len(stmts) == 1 else f'{name}#{i}'
                entries.append({'source': label, 'file': name, 'sql': stmt})
        if skipped:
            print(
                f'[{workload_path}] skipped {len(skipped)} files: {", ".join(skipped)}',
                flush=True,
            )
        if not entries:
            raise ValueError(f'{workload_path} produced zero executable statements')
        # Directory workloads change size when files are skipped; do not enforce
        # expected_statement_count (use 0 in config.ini for directory mode).
        if EXPECTED_STATEMENT_COUNT and is_configured:
            print(
                f'[{workload_path}] loaded {len(entries)} statements '
                f'(expected_statement_count={EXPECTED_STATEMENT_COUNT} ignored for directory)',
                flush=True,
            )
        return entries

    stmts = _parse_sql_file(workload_path, allow_intersect_except=allow_intersect_except)
    if (
        EXPECTED_STATEMENT_COUNT
        and is_configured
        and len(stmts) != EXPECTED_STATEMENT_COUNT
    ):
        raise ValueError(
            f'{workload_path} statement manifest changed: expected '
            f'{EXPECTED_STATEMENT_COUNT}, got {len(stmts)}'
        )
    return [
        {'source': f'stmt#{i}', 'file': os.path.basename(workload_path), 'sql': s}
        for i, s in enumerate(stmts, 1)
    ]


def _load_sql_statements(sql_path):
    """Backward-compatible helper: return raw SQL strings only."""
    return [e['sql'] for e in _workload_entries(sql_path)]


def _hash_workload(workload_path):
    """Stable hash for a file or directory workload."""
    if os.path.isdir(workload_path):
        digest = hashlib.sha256()
        for name in sorted(
            f for f in os.listdir(workload_path)
            if re.fullmatch(r'q\d{3}\.sql', f, flags=re.IGNORECASE)
        ):
            qid = os.path.splitext(name)[0].lower()
            if qid in TPCDS_SKIP_QUERIES:
                continue
            path = os.path.join(workload_path, name)
            with open(path, 'rb') as f:
                digest.update(name.encode('utf-8'))
                digest.update(b'\0')
                digest.update(f.read())
                digest.update(b'\0')
        return digest.hexdigest()
    if os.path.exists(workload_path):
        with open(workload_path, 'rb') as f:
            return hashlib.sha256(f.read()).hexdigest()
    return hashlib.sha256(b'').hexdigest()


def _run_sql_file(cursor, sql_path, log_file=None, stmt_timeout_ms=None, run_index=0, warmup=False):
    """Execute a fixed SQL manifest and return counts, elapsed time and report."""
    allow_intersect = _probe_mysql_intersect_support(cursor)
    entries = _workload_entries(sql_path, allow_intersect_except=allow_intersect)
    stmt_timeout_ms = (
        BENCHMARK_STATEMENT_TIMEOUT_MS if stmt_timeout_ms is None else stmt_timeout_ms
    )
    ok_count = 0
    fail_count = 0
    connection_lost = False
    statement_results = []
    start = time.time()
    # MySQL 5.7.8+: abort SELECT after N ms (prevents multi-hour inventory/self-join hangs)
    if stmt_timeout_ms and stmt_timeout_ms > 0:
        try:
            cursor.execute(f'SET SESSION max_execution_time = {int(stmt_timeout_ms)}')
        except Exception as e:
            print(f'Warning: cannot set max_execution_time: {e}')
    total = len(entries)
    print(f'[{sql_path}] running {total} statements (timeout={stmt_timeout_ms}ms)')
    for idx, entry in enumerate(entries, 1):
        stmt = entry['sql']
        source = entry['source']
        t0 = time.time()
        print(f'[{source}] {idx}/{total} ...', flush=True)
        try:
            cursor.execute(stmt)
            cursor.fetchall()
            ok_count += 1
            latency = time.time() - t0
            statement_results.append({
                'statement_index': idx,
                'source': source,
                'file': entry['file'],
                'latency_seconds': latency,
                'status': 'ok',
            })
            print(f'[{source}] {idx}/{total} ok in {latency:.1f}s', flush=True)
        except Exception as e:
            fail_count += 1
            latency = time.time() - t0
            err_str = str(e)
            lost_conn = (
                'Lost connection to MySQL server' in err_str
                or 'MySQL server has gone away' in err_str
                or getattr(e, 'args', [None])[0] in (2003, 2006, 2013)
            )
            connection_lost = connection_lost or lost_conn
            statement_results.append({
                'statement_index': idx,
                'source': source,
                'file': entry['file'],
                'latency_seconds': latency,
                'status': 'lost_connection' if lost_conn else 'failed',
                'error': err_str,
            })
            msg = f'[{source}] {idx}/{total} failed after {latency:.1f}s: {e}'
            print(msg, flush=True)
            if log_file:
                with open(log_file, 'a', encoding='utf-8') as f:
                    f.write(msg + '\n')
                    # Helpful for diagnosing crashes: record the failing SQL prefix.
                    if lost_conn or idx <= 5:
                        snippet = ' '.join(stmt.replace('\n', ' ').split())
                        f.write(f'FAILED_STMT_PREFIX {source}: {snippet[:1200]}\n')
                    if lost_conn:
                        # If mysqld crashed / restarted, continuing will only produce (0, '') noise.
                        f.write('STOPPING remaining stmts due to lost connection.\n')
                        break
    elapsed = time.time() - start
    manifest_text = '\n;\n'.join(e['sql'] for e in entries)
    report = {
        'sql_path': sql_path,
        'manifest_sha256': hashlib.sha256(manifest_text.encode('utf-8')).hexdigest(),
        'expected_statements': len(entries),
        'executed_statements': len(statement_results),
        'ok_count': ok_count,
        'fail_count': fail_count,
        'connection_lost': connection_lost,
        'elapsed_seconds': elapsed,
        'run_index': run_index,
        'warmup': warmup,
        'statement_results': statement_results,
    }
    report['valid'] = (
        not connection_lost
        and (not BENCHMARK_REQUIRE_ALL or (
            fail_count == 0 and ok_count == len(entries)
        ))
    )
    if log_file:
        with open(log_file + '.jsonl', 'a', encoding='utf-8') as f:
            f.write(json.dumps(report, ensure_ascii=False) + '\n')
    return ok_count, fail_count, elapsed, report


def _run_sql_benchmark(sql_path, log_file, label):
    """Run warmups and measured repetitions; reject any incomplete run."""
    global LAST_BENCHMARK_RESULT
    measured_qps = []
    measured_reports = []
    total_runs = BENCHMARK_WARMUP_RUNS + max(1, BENCHMARK_REPETITIONS)
    manifest_hash = None
    for run_index in range(total_runs):
        warmup = run_index < BENCHMARK_WARMUP_RUNS
        conn = pymysql.connect(**db_config)
        cursor = conn.cursor()
        try:
            ok_count, fail_count, total_time, report = _run_sql_file(
                cursor,
                sql_path,
                log_file=log_file,
                run_index=run_index,
                warmup=warmup,
            )
        finally:
            cursor.close()
            conn.close()

        if manifest_hash is None:
            manifest_hash = report['manifest_sha256']
        elif report['manifest_sha256'] != manifest_hash:
            print(f'{label} invalid: SQL manifest changed between repetitions', flush=True)
            LAST_BENCHMARK_RESULT = {
                'benchmark': label,
                'valid': False,
                'failure_reason': 'manifest_changed_between_repetitions',
                'manifest_sha256': report['manifest_sha256'],
                'report_path': log_file + '.jsonl',
            }
            return 0.0

        if not report['valid']:
            failed_sources = [
                r.get('source', f"stmt#{r['statement_index']}")
                for r in report.get('statement_results', [])
                if r.get('status') != 'ok'
            ]
            print(
                f'{label} invalid: run={run_index} ok={ok_count} '
                f'fail={fail_count} expected={report["expected_statements"]}'
                + (f' failed={failed_sources}' if failed_sources else ''),
                flush=True,
            )
            LAST_BENCHMARK_RESULT = {
                'benchmark': label,
                'valid': False,
                'failure_reason': (
                    'connection_lost' if report['connection_lost']
                    else 'incomplete_workload'
                ),
                'manifest_sha256': report['manifest_sha256'],
                'report_path': log_file + '.jsonl',
                'run_index': run_index,
                'failed_sources': failed_sources,
            }
            return 0.0
        if not warmup:
            measured_reports.append(report)
            measured_qps.append(float(ok_count) / total_time if total_time > 0 else 0.0)

    score = statistics.median(measured_qps) if measured_qps else 0.0
    successful_latencies = [
        stmt['latency_seconds']
        for report in measured_reports
        for stmt in report['statement_results']
        if stmt['status'] == 'ok'
    ]
    sorted_latencies = sorted(successful_latencies)
    p95_index = max(0, int(0.95 * len(sorted_latencies)) - 1)
    summary = {
        'benchmark': label,
        'valid': score > 0,
        'manifest_sha256': manifest_hash,
        'warmup_runs': BENCHMARK_WARMUP_RUNS,
        'repetitions': len(measured_qps),
        'qps_values': measured_qps,
        'median_qps': score,
        'median_elapsed_seconds': statistics.median(
            [r['elapsed_seconds'] for r in measured_reports]
        ) if measured_reports else None,
        'query_latency_seconds': {
            'min': min(successful_latencies) if successful_latencies else None,
            'mean': statistics.mean(successful_latencies) if successful_latencies else None,
            'median': statistics.median(successful_latencies) if successful_latencies else None,
            'p95': sorted_latencies[p95_index] if sorted_latencies else None,
            'max': max(successful_latencies) if successful_latencies else None,
        },
        'report_path': log_file + '.jsonl',
    }
    with open(log_file + '.summary.json', 'w', encoding='utf-8') as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)
    LAST_BENCHMARK_RESULT = summary
    return score


def _record_benchmark_failure(label, reason, report_path=None):
    global LAST_BENCHMARK_RESULT
    LAST_BENCHMARK_RESULT = {
        'benchmark': label,
        'valid': False,
        'failure_reason': reason,
        'manifest_sha256': None,
        'report_path': report_path,
    }


def test_by_job(knob):
    """Run JOB (IMDB) queries; return queries/sec (higher is better)."""
    state = _apply_knobs_and_restart(knob)
    if state != 0:
        print('database restarting failed')
        _record_benchmark_failure('JOB', 'database_restart_failed')
        return 0.0

    print('database has been restarted')
    os.makedirs('./configuration recommender/log', exist_ok=True)
    log_file = './configuration recommender/log/job_{}.log'.format(int(time.time()))
    sql_path = './benchmark_queries/job_all.sql'

    try:
        score = _run_sql_benchmark(sql_path, log_file, 'JOB')
    except Exception as e:
        print(f'JOB benchmark failed: {e}')
        _record_benchmark_failure('JOB', f'benchmark_exception:{type(e).__name__}', log_file)
        return 0.0

    print(f'JOB done: median_qps={score:.6f} log={log_file}')
    return score


def test_by_tpcc(knob):
    #load knobs
    temp_config = {}
    knobs_detail = get_knobs_detail()
    for key in knobs_detail.keys():
        if key in knob.keys():
            if knobs_detail[key]['type'] == 'integer':
                temp_config[key] = knob.get(key) 
            elif knobs_detail[key]['type'] == 'enum':
                value = str(knob.get(key))
                if value in knobs_detail[key]['enum_values']:
                    temp_config[key] = value
                else:
                    # Handle case where value is not in the enum_values list
                    print(f"Warning: {value} not found in enum values for {key}")
    
    apply_temp_config_to_mycnf(temp_config)

    time.sleep(10)

    print("success set knobs")

    state = _restart_mysql()

    if state == 0:
        print('database has been restarted')
        log_file = './configuration recommender/log/' + '{}.log'.format(int(time.time()))
        ip = ''
        username = ''
        command = f'tpcc_start -S {db_config["unix_socket"]} -d -u -p -w -c -r -l'
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        tps = 0
        try:

            client.connect(hostname=ip, username=username, password=ip_password)
            

            stdin, stdout, stderr = client.exec_command(command)
            
            trx_values = []
            
            with open(log_file, 'a') as f: 

                for line in stdout:
                    line = line.strip()
                    print(line, file=f)  

                    match = re.search(r'trx:\s*(\d+)', line)
                    if match:
                        trx_values.append(int(match.group(1)))
            

                error = stderr.read().decode()
                if error:
                    print(f"Error: {error}", file=f)
                
                if trx_values:
                    average = sum(trx_values) / len(trx_values)
                    print(f"trx average: {average:.2f}", file=f)
                    tps = average
                else:
                    print("Error! No trx values found in the log file.", file=f)
                
        except Exception as e:
            print(f"Error: {e}")
        finally:
            client.close()
        return tps
    else:
        print('database restarting failed')
        return 0
    

def test_by_sysbench(knob):
    #load knobs
    temp_config = {}
    knobs_detail = get_knobs_detail()
    for key in knobs_detail.keys():
        if key not in knob.keys():
            continue
        knob_type = knobs_detail[key].get('type')
        if knob_type == 'integer':
            temp_config[key] = knob.get(key)
        elif knob_type == 'enum':
            value = str(knob.get(key))
            enum_values = knobs_detail[key].get('enum_values') or []
            if value in enum_values:
                temp_config[key] = value
            else:
                print(f"Warning: {value} not found in enum values for {key}")
    
    apply_temp_config_to_mycnf(temp_config)

    time.sleep(10)

    print("success set knobs")
    #exit()

    state = _restart_mysql()

    if state == 0:
        print('database has been restarted')
        os.makedirs('./configuration recommender/log', exist_ok=True)
        log_file = './configuration recommender/log/' + '{}.log'.format(int(time.time()))
        command_run = 'sysbench --db-driver=mysql --threads=32 --mysql-socket={} --mysql-user={} --mysql-password={} --mysql-db={} --tables=50 --table-size=1000000 --time=120 --report-interval=60 oltp_read_write run'.format(
                            db_config.get('unix_socket'),
                            db_config.get('user'),
                            db_config.get('password'),
                            db_config.get('database')
                            )
        
        # quote path: "configuration recommender" contains a space
        os.system(command_run + ' > "{}" '.format(log_file))
        
        try:
            qps = sum([float(line.split()[8]) for line in open(log_file,'r').readlines() if 'qps' in line][-int(120/60):]) / (int(120/60))
        except (FileNotFoundError, IndexError, ZeroDivisionError, ValueError) as e:
            print(f'Failed to parse sysbench log {log_file}: {e}')
            return 0
        tps = float(qps/20.0)
        return tps
    else:
        print('database restarting failed')
        return 0

def unknown_benchmark(name):
    print(f"Unknown benchmark: {name}")


def test_by_tpcds(knob):
    """Run TPC-DS queries from workload_file (single .sql or tpcds/ dir)."""
    state = _apply_knobs_and_restart(knob)
    if state != 0:
        print('database restarting failed')
        _record_benchmark_failure('TPC-DS', 'database_restart_failed')
        return 0.0

    print('database has been restarted')
    os.makedirs('./configuration recommender/log', exist_ok=True)
    log_file = './configuration recommender/log/tpcds_{}.log'.format(int(time.time()))
    sql_path = config_parser['workload analyzer']['workload_file']

    try:
        print(f'connecting to MySQL via {db_config.get("unix_socket") or db_config.get("host")} ...', flush=True)
        score = _run_sql_benchmark(sql_path, log_file, 'TPC-DS')
    except Exception as e:
        print(f'TPC-DS benchmark failed: {e}')
        _record_benchmark_failure(
            'TPC-DS', f'benchmark_exception:{type(e).__name__}', log_file
        )
        return 0.0

    print(f'TPC-DS done: median_qps={score:.6f} log={log_file}')
    return score


if __name__ == "__main__":

    # ---------- record dir + checkpoint ----------
    RECORD_DIR_NAME = config_parser['configuration recommender'].get('record_dir', 'record').strip()
    RECORD_DIR = os.path.join('./configuration recommender', RECORD_DIR_NAME)
    CHECKPOINT_PATH = os.path.join(RECORD_DIR, 'checkpoint.json')
    RUN_MODE = config_parser['configuration recommender'].get('run_mode', 'auto').strip().lower()
    HISTORY_PATH = os.path.join(RECORD_DIR, 'benmark_history')
    OPTIMAL_PATH = os.path.join(RECORD_DIR, 'optimal configuration')

    def make_trial_id(iteration_value, result_index_value, knob_value):
        payload = json.dumps(
            {
                'iteration': iteration_value,
                'result_index': result_index_value,
                'knob': knob_value,
            },
            sort_keys=True,
            separators=(',', ':'),
            ensure_ascii=False,
        )
        return hashlib.sha256(payload.encode('utf-8')).hexdigest()

    def append_history_record(record):
        trial_id = record.get('trial_id')
        if trial_id and os.path.exists(HISTORY_PATH):
            with open(HISTORY_PATH, 'r', encoding='utf-8') as existing:
                if trial_id in existing.read():
                    print(f'skip duplicate history trial {trial_id[:12]}', flush=True)
                    return False
        with open(HISTORY_PATH, 'a', encoding='utf-8') as history_file:
            json.dump(record, history_file, ensure_ascii=False)
            history_file.write('\n')
        return True

    def load_checkpoint():
        if not os.path.exists(CHECKPOINT_PATH):
            return None
        try:
            with open(CHECKPOINT_PATH, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            print(f'Failed to load checkpoint: {e}')
            return None

    def save_checkpoint(payload):
        os.makedirs(RECORD_DIR, exist_ok=True)
        payload = dict(payload)
        payload['record_dir'] = RECORD_DIR_NAME
        payload['updated_at'] = int(time.time())
        payload['environment_snapshot_sha256'] = globals().get('SNAPSHOT_HASH')
        temp_path = CHECKPOINT_PATH + '.tmp'
        with open(temp_path, 'w', encoding='utf-8') as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp_path, CHECKPOINT_PATH)

    def clear_record_progress():
        os.makedirs(RECORD_DIR, exist_ok=True)
        for name in os.listdir(RECORD_DIR):
            path = os.path.join(RECORD_DIR, name)
            if name.startswith('turn_') or name in (
                'benmark_history',
                'top_k',
                'optimal configuration',
                'checkpoint.json',
                'decision_log.jsonl',
                'token_usage.jsonl',
                'environment_snapshot.json',
            ):
                try:
                    if os.path.isfile(path):
                        os.remove(path)
                except OSError as e:
                    print(f'Warning: cannot remove {path}: {e}')

    def resolve_run_mode():
        ckpt = load_checkpoint()
        if (
            RUN_MODE != 'fresh'
            and os.path.exists(CHECKPOINT_PATH)
            and ckpt is None
        ):
            raise RuntimeError(
                'Checkpoint exists but is unreadable; refusing to clear experiment state'
            )
        resumable = (
            ckpt is not None
            and ckpt.get('status') == 'running'
            and ckpt.get('result') is not None
        )
        if RUN_MODE == 'fresh':
            return 'fresh', None
        if RUN_MODE == 'resume':
            if resumable:
                return 'resume', ckpt
            print('run_mode=resume but no incomplete checkpoint found; abort.')
            sys.exit(1)
        if resumable:
            return 'resume', ckpt
        return 'fresh', None

    def parse_llm_response(response_json):
        if isinstance(response_json, dict) and 'recommendations' in response_json:
            return (
                response_json.get('recommendations') or [],
                int(response_json.get('request_count', 0)),
                response_json.get('history_top') or [],
                response_json.get('last_result') or '',
                response_json.get('reflection_state') or {},
                response_json.get('surrogate_diagnostics') or {},
            )
        return response_json, 0, [], '', {}, {}

    def restore_llm_server(process_url, ckpt_data):
        restore_url = process_url.rsplit('/process', 1)[0] + '/restore'
        payload = {
            'request_count': ckpt_data.get('request_count', 0),
            'history_top': ckpt_data.get('history_top', []),
            'last_result': ckpt_data.get('last_result', ''),
            'reflection_state': ckpt_data.get('reflection_state', {}),
        }
        try:
            r = requests.post(restore_url, json=payload, timeout=30)
            print('LLM_server restore:', r.status_code, r.text[:200])
        except Exception as e:
            print(f'Warning: failed to restore LLM_server state: {e}')

    def normalize_result_items(result_obj):
        if isinstance(result_obj, list):
            return result_obj
        if isinstance(result_obj, dict):
            return [result_obj]
        if isinstance(result_obj, str):
            return [result_obj]
        return []

    os.makedirs(RECORD_DIR, exist_ok=True)
    os.makedirs('./configuration recommender/log', exist_ok=True)

    mode, ckpt = resolve_run_mode()
    print(f'run_mode={RUN_MODE} -> {mode}; record_dir={RECORD_DIR}')
    if mode == 'fresh':
        clear_record_progress()

    workload_path = config_parser['workload analyzer']['workload_file']
    snapshot_core = {
        'python': sys.version,
        'platform': platform.platform(),
        'config_path': os.environ.get('AGENTTUNE_CONFIG', './config.ini'),
        'database_kernel': config_parser['knob selector']['database_kernel'],
        'database_scale': config_parser['knob selector']['database_scale'],
        'hardware': config_parser['knob selector']['hardware'],
        'benchmark': config_parser['configuration recommender']['benchmark'],
        'random_seed': config_parser.getint(
            'configuration recommender', 'random_seed', fallback=42
        ),
        'workload_file': workload_path,
        'workload_sha256': _hash_workload(workload_path),
        'benchmark_protocol': {
            'warmup_runs': BENCHMARK_WARMUP_RUNS,
            'repetitions': BENCHMARK_REPETITIONS,
            'require_all': BENCHMARK_REQUIRE_ALL,
            'statement_timeout_ms': BENCHMARK_STATEMENT_TIMEOUT_MS,
            'tpcds_skip_queries': sorted(TPCDS_SKIP_QUERIES),
        },
    }
    snapshot_path = os.path.join(RECORD_DIR, 'environment_snapshot.json')
    snapshot_core_json = json.dumps(
        snapshot_core, sort_keys=True, separators=(',', ':'), ensure_ascii=False
    )
    SNAPSHOT_HASH = hashlib.sha256(snapshot_core_json.encode('utf-8')).hexdigest()
    if mode == 'fresh':
        snapshot = {'created_at': int(time.time()), **snapshot_core}
        with open(snapshot_path, 'w', encoding='utf-8') as f:
            json.dump(snapshot, f, indent=2, ensure_ascii=False)
    else:
        checkpoint_hash = (ckpt or {}).get('environment_snapshot_sha256')
        if not os.path.exists(snapshot_path):
            print(
                'Warning: checkpoint exists but environment_snapshot.json is missing; '
                'starting fresh instead of resume.',
                flush=True,
            )
            mode = 'fresh'
            ckpt = None
            clear_record_progress()
            snapshot = {'created_at': int(time.time()), **snapshot_core}
            with open(snapshot_path, 'w', encoding='utf-8') as f:
                json.dump(snapshot, f, indent=2, ensure_ascii=False)
        elif checkpoint_hash and checkpoint_hash != SNAPSHOT_HASH:
            raise RuntimeError(
                'Cannot resume: workload or benchmark protocol differs from checkpoint'
            )

    url = 'http://{}:{}/process'.format(
        config_parser['configuration recommender']['LLM_server_IP'],
        int(config_parser['configuration recommender']['LLM_server_port']),
    )
    max_iteration = int(config_parser['configuration recommender']['iteration'])
    controller_enabled = config_parser.getboolean(
        'configuration recommender', 'adaptive_controller_enabled', fallback=False
    )
    benchmark = config_parser['configuration recommender']['benchmark'].strip().upper()
    benchmark_switch = {
        "SYSBENCH": test_by_sysbench,
        "TPCC": test_by_tpcc,
        "JOB": test_by_job,
        "TPCDS": test_by_tpcds,
    }

    def new_controller():
        return DecisionController(
            window_size=config_parser.getint(
                'configuration recommender', 'plateau_window', fallback=4
            ),
            min_trials=3,
            improvement_stagnant=config_parser.getfloat(
                'configuration recommender', 'plateau_threshold', fallback=0.01
            ),
            failure_rate_rollback=config_parser.getfloat(
                'configuration recommender', 'failure_rate_threshold', fallback=0.50
            ),
            failure_rate_shrink=min(
                0.25,
                config_parser.getfloat(
                    'configuration recommender', 'failure_rate_threshold', fallback=0.50
                ) / 2.0,
            ),
            benchmark_budget=config_parser.getint(
                'configuration recommender', 'benchmark_budget', fallback=61
            ),
            token_budget=config_parser.getint(
                'configuration recommender', 'token_budget', fallback=2_000_000
            ),
        )

    def record_controller_trial(controller, knob, throughput, metric, token_cost=0, confidence=None):
        if not controller_enabled:
            return
        controller.record_trial(TrialResult(
            throughput=throughput if throughput > 0 else None,
            success=throughput > 0,
            configuration=knob or {},
            surrogate_confidence=confidence,
            token_cost=max(0, int(token_cost)),
            metadata={'metric_count': len(metric or {})},
        ))

    if mode == 'fresh':
        controller = new_controller()
        reflection_state = {}
        surrogate_diagnostics = {}
        last_token_total = 0
        knob = get_current_knob()
        benchmark_func = benchmark_switch.get(benchmark)
        if benchmark_func is None:
            unknown_benchmark(benchmark)
            sys.exit(1)
        evaluation_started = time.time()
        throughput = benchmark_func(knob)
        evaluation_seconds = time.time() - evaluation_started
        metric = []
        if throughput != 0:
            try:
                metric = get_current_metric()
            except Exception as e:
                print(f'Warning: get_current_metric failed (MySQL may be down): {e}', flush=True)
        data1 = [{
            "trial_id": make_trial_id(-1, 0, knob),
            "knob": knob,
            "throughput": throughput,
            "metric": metric,
            "valid": bool(LAST_BENCHMARK_RESULT.get('valid', throughput > 0)),
            "failure_reason": LAST_BENCHMARK_RESULT.get('failure_reason'),
            "manifest_sha256": LAST_BENCHMARK_RESULT.get('manifest_sha256'),
            "report_path": LAST_BENCHMARK_RESULT.get('report_path'),
            "iteration": -1,
            "evaluation_seconds": evaluation_seconds,
            "timestamp": int(time.time()),
        }]
        record_controller_trial(controller, knob, throughput, metric)
        append_history_record(data1[0])

        decision = controller.decide() if controller_enabled else None
        request_payload = {
            'trials': data1,
            'decision': decision.to_dict() if decision else {},
        }
        response = requests.post(url, json=request_payload, timeout=300)
        response.raise_for_status()
        response_json = response.json()
        (
            result,
            request_count,
            history_top,
            last_result,
            reflection_state,
            surrogate_diagnostics,
        ) = parse_llm_response(response_json)
        new_token_total = int(
            (reflection_state.get('token_usage') or {}).get('total', 0)
        )
        pending_token_cost = max(0, new_token_total - last_token_total)
        last_token_total = new_token_total
        print(result)

        iteration = 0
        result_index = 0
        data_list = []
        best_knob = knob
        best_metric = metric
        best_throughput = float(throughput) if isinstance(throughput, (int, float)) else 0.0

        save_checkpoint({
            'status': 'running',
            'iteration': iteration,
            'result_index': result_index,
            'result': result,
            'data_list': data_list,
            'best_knob': best_knob,
            'best_metric': best_metric,
            'best_throughput': best_throughput,
            'request_count': request_count,
            'history_top': history_top,
            'last_result': last_result,
            'decision_controller': controller.to_dict(),
            'last_decision': decision.to_dict() if decision else {},
            'reflection_state': reflection_state,
            'surrogate_diagnostics': surrogate_diagnostics,
            'last_token_total': last_token_total,
            'pending_token_cost': pending_token_cost,
        })
    else:
        print(
            f'Resuming: iteration={ckpt.get("iteration")}, '
            f'result_index={ckpt.get("result_index")}'
        )
        restore_llm_server(url, ckpt)
        controller_state = ckpt.get('decision_controller')
        controller = (
            DecisionController.from_dict(controller_state)
            if controller_enabled and controller_state
            else new_controller()
        )
        iteration = int(ckpt.get('iteration', 0))
        result_index = int(ckpt.get('result_index', 0))
        result = ckpt.get('result')
        data_list = ckpt.get('data_list') or []
        best_knob = ckpt.get('best_knob') or []
        best_metric = ckpt.get('best_metric') or []
        best_throughput = float(ckpt.get('best_throughput') or 0)
        request_count = int(ckpt.get('request_count', 0))
        history_top = ckpt.get('history_top') or []
        last_result = ckpt.get('last_result') or ''
        reflection_state = ckpt.get('reflection_state') or {}
        surrogate_diagnostics = ckpt.get('surrogate_diagnostics') or {}
        last_token_total = int(ckpt.get('last_token_total') or 0)
        pending_token_cost = int(ckpt.get('pending_token_cost') or 0)

    stopped_by_controller = False
    while iteration < max_iteration:
        items = normalize_result_items(result)

        for idx in range(result_index, len(items)):
            item = items[idx]
            if isinstance(item, str):
                if not item.strip():
                    result_index = idx + 1
                    continue
                knob = json.loads(item)
            elif isinstance(item, dict):
                knob = item
            else:
                result_index = idx + 1
                continue

            if not knob:
                print('Skip empty knob config')
                result_index = idx + 1
                save_checkpoint({
                    'status': 'running',
                    'iteration': iteration,
                    'result_index': result_index,
                    'result': result,
                    'data_list': data_list,
                    'best_knob': best_knob,
                    'best_metric': best_metric,
                    'best_throughput': best_throughput,
                    'request_count': request_count,
                    'history_top': history_top,
                    'last_result': last_result,
                    'decision_controller': controller.to_dict(),
                    'reflection_state': reflection_state,
                    'surrogate_diagnostics': surrogate_diagnostics,
                    'last_token_total': last_token_total,
                    'pending_token_cost': pending_token_cost,
                })
                continue

            benchmark_func = benchmark_switch.get(benchmark)
            if benchmark_func is None:
                unknown_benchmark(benchmark)
                result_index = idx + 1
                continue

            evaluation_started = time.time()
            throughput = benchmark_func(knob)
            evaluation_seconds = time.time() - evaluation_started
            if not isinstance(throughput, (int, float)) or isinstance(throughput, bool):
                throughput = 0.0
            else:
                throughput = float(throughput)

            metric = []
            if throughput != 0:
                try:
                    metric = get_current_metric()
                except Exception as e:
                    print(f'Warning: get_current_metric failed (MySQL may be down): {e}', flush=True)
            data = {
                "trial_id": make_trial_id(iteration, idx, knob),
                "knob": knob,
                "throughput": throughput,
                "metric": metric,
                "valid": bool(LAST_BENCHMARK_RESULT.get('valid', throughput > 0)),
                "failure_reason": LAST_BENCHMARK_RESULT.get('failure_reason'),
                "manifest_sha256": LAST_BENCHMARK_RESULT.get('manifest_sha256'),
                "report_path": LAST_BENCHMARK_RESULT.get('report_path'),
                "iteration": iteration,
                "evaluation_seconds": evaluation_seconds,
                "timestamp": int(time.time()),
            }
            confidence = surrogate_diagnostics.get('confidence')
            record_controller_trial(
                controller,
                knob,
                throughput,
                metric,
                token_cost=pending_token_cost,
                confidence=confidence,
            )
            pending_token_cost = 0
            data_list.append(data)
            append_history_record(data)

            if throughput > best_throughput:
                best_knob = knob
                best_metric = metric
                best_throughput = throughput

            result_index = idx + 1
            save_checkpoint({
                'status': 'running',
                'iteration': iteration,
                'result_index': result_index,
                'result': result,
                'data_list': data_list,
                'best_knob': best_knob,
                'best_metric': best_metric,
                'best_throughput': best_throughput,
                'request_count': request_count,
                'history_top': history_top,
                'last_result': last_result,
                'decision_controller': controller.to_dict(),
                'reflection_state': reflection_state,
                'surrogate_diagnostics': surrogate_diagnostics,
                'last_token_total': last_token_total,
                'pending_token_cost': pending_token_cost,
            })

        if not data_list and result_index >= len(items):
            print('No valid knob configs in this iteration, stop.')
            break

        decision = controller.decide() if controller_enabled else None
        if decision:
            with open(os.path.join(RECORD_DIR, 'decision_log.jsonl'), 'a', encoding='utf-8') as f:
                f.write(json.dumps(decision.to_dict(), ensure_ascii=False) + '\n')
        if decision and decision.name == 'stop':
            print(f'Adaptive controller stopped search: {decision.reason}', flush=True)
            stopped_by_controller = True
            break
        response = requests.post(
            url,
            json={
                'trials': data_list,
                'decision': decision.to_dict() if decision else {},
            },
            timeout=300,
        )
        response.raise_for_status()
        response_json = response.json()
        (
            result,
            request_count,
            history_top,
            last_result,
            reflection_state,
            surrogate_diagnostics,
        ) = parse_llm_response(response_json)
        new_token_total = int(
            (reflection_state.get('token_usage') or {}).get('total', last_token_total)
        )
        pending_token_cost = max(0, new_token_total - last_token_total)
        last_token_total = new_token_total
        print(result)

        iteration += 1
        result_index = 0
        data_list = []
        save_checkpoint({
            'status': 'running',
            'iteration': iteration,
            'result_index': result_index,
            'result': result,
            'data_list': data_list,
            'best_knob': best_knob,
            'best_metric': best_metric,
            'best_throughput': best_throughput,
            'request_count': request_count,
            'history_top': history_top,
            'last_result': last_result,
            'decision_controller': controller.to_dict(),
            'last_decision': decision.to_dict() if decision else {},
            'reflection_state': reflection_state,
            'surrogate_diagnostics': surrogate_diagnostics,
            'last_token_total': last_token_total,
            'pending_token_cost': pending_token_cost,
        })

    with open(OPTIMAL_PATH, "w", encoding="utf-8") as f:
        print("best_knob:", best_knob, file=f)
        print("best_metric:", best_metric, file=f)
        print("best_throughput:", best_throughput, file=f)

    save_checkpoint({
        'status': 'completed',
        'iteration': iteration,
        'result_index': result_index,
        'result': result,
        'data_list': data_list,
        'best_knob': best_knob,
        'best_metric': best_metric,
        'best_throughput': best_throughput,
        'request_count': request_count,
        'history_top': history_top,
        'last_result': last_result,
        'decision_controller': controller.to_dict(),
        'reflection_state': reflection_state,
        'surrogate_diagnostics': surrogate_diagnostics,
        'last_token_total': last_token_total,
        'pending_token_cost': pending_token_cost,
        'stop_reason': 'adaptive_controller' if stopped_by_controller else 'iteration_limit',
    })
    print(f'Done. Results saved under {RECORD_DIR}')