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

config_parser = configparser.ConfigParser()
config_parser.read('./config.ini')

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

MYSQL_BASE = '/workspace/setup/mysql-8.0'
MYCNF_PATH = '/etc/my8.cnf'
MYCNF_BAK = '/etc/my8.cnf.bak'
MYSQL_RESTART_CMD = 'sudo service mysql8 restart'
MYSQL_SAFE_FALLBACK_CMD = (
    f'{MYSQL_BASE}/bin/mysqld_safe --defaults-file={MYCNF_PATH} '
    '>/tmp/mysqld8_safe.out 2>&1 &'
)

with open(config_parser['knob selector']['candidate_knobs'], 'r') as f:
    original = json.load(f)
    original_keys = list(original.keys())

with open(config_parser['range pruner']['output_file'], 'r') as f:
    selected_knobs = json.load(f)


def _restart_mysql():
    """Restart MySQL 8.0 via service; fall back to mysqld_safe if needed."""
    state = os.system(MYSQL_RESTART_CMD)
    if state != 0:
        print(f'{MYSQL_RESTART_CMD} exit={state}; trying mysqld_safe', flush=True)
        os.system('pkill -9 mysqld mysqld_safe 2>/dev/null; sleep 2')
        state = os.system(MYSQL_SAFE_FALLBACK_CMD)
    return state


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
    for key in selected_knobs.keys():
        index = int(key.replace("knob", "")) - 1
        param_name = original_keys[index]
        parameters.append(param_name)

    for param in parameters:
        cursor.execute(f"SHOW VARIABLES LIKE '{param}'")
        result = cursor.fetchone()
        if result:
            try:
                # Attempt to convert to integer if it's a digit, otherwise to float
                knobs[param] = int(result[1]) if result[1].isdigit() else round(float(result[1]))
            except ValueError:
                # If conversion fails, assign the string directly (e.g., 'ON' or 'OFF')
                knobs[param] = result[1]

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
            value = str(knob.get(key))
            enum_values = knobs_detail[key].get('enum_values') or []
            if value in enum_values:
                temp_config[key] = value
            else:
                print(f"Warning: {value} not found in enum values for {key}")
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


def _apply_knobs_and_restart(knob):
    temp_config = _build_temp_config_from_knob(knob)
    apply_temp_config_to_mycnf(temp_config)
    time.sleep(10)
    print("success set knobs")
    state = _restart_mysql()
    if state != 0:
        return state
    if not _wait_for_mysql(timeout_sec=90):
        print(
            'MySQL failed to start after knob apply. Check error log, e.g.\n'
            f'  ls -t {MYSQL_BASE}/data/*.err | head -1 | xargs tail -n 100\n'
            'Often caused by bad knobs in my.cnf — restore backup:\n'
            f'  sudo cp {MYCNF_BAK} {MYCNF_PATH}',
            flush=True,
        )
        return 1
    return 0


def _load_sql_statements(sql_path):
    with open(sql_path, 'r', encoding='utf-8', errors='ignore') as f:
        content = f.read()
    content = re.sub(r'/\*.*?\*/', '', content, flags=re.DOTALL)
    lines = content.splitlines()
    while lines and lines[0].startswith('--'):
        lines.pop(0)
    content = '\n'.join(lines).lstrip('\n')
    parts = re.split(r';\s*\n', content)
    return [p.strip() for p in parts if p.strip()]


def _run_sql_file(cursor, sql_path, log_file=None, stmt_timeout_ms=180000):
    """Execute all statements in a .sql file; return (ok_count, fail_count, elapsed_seconds)."""
    statements = _load_sql_statements(sql_path)
    ok_count = 0
    fail_count = 0
    start = time.time()
    # MySQL 5.7.8+: abort SELECT after N ms (prevents multi-hour inventory/self-join hangs)
    if stmt_timeout_ms and stmt_timeout_ms > 0:
        try:
            cursor.execute(f'SET SESSION max_execution_time = {int(stmt_timeout_ms)}')
        except Exception as e:
            print(f'Warning: cannot set max_execution_time: {e}')
    total = len(statements)
    print(f'[{sql_path}] running {total} statements (timeout={stmt_timeout_ms}ms)')
    for idx, stmt in enumerate(statements, 1):
        t0 = time.time()
        print(f'[{sql_path}] stmt#{idx}/{total} ...', flush=True)
        try:
            cursor.execute(stmt)
            try:
                cursor.fetchall()
            except Exception:
                pass
            ok_count += 1
            print(f'[{sql_path}] stmt#{idx}/{total} ok in {time.time() - t0:.1f}s', flush=True)
        except Exception as e:
            fail_count += 1
            msg = f'[{sql_path}] stmt#{idx}/{total} failed after {time.time() - t0:.1f}s: {e}'
            print(msg, flush=True)
            if log_file:
                with open(log_file, 'a', encoding='utf-8') as f:
                    f.write(msg + '\n')
    elapsed = time.time() - start
    return ok_count, fail_count, elapsed


def test_by_job(knob):
    """Run JOB (IMDB) queries; return queries/sec (higher is better)."""
    state = _apply_knobs_and_restart(knob)
    if state != 0:
        print('database restarting failed')
        return 0.0

    print('database has been restarted')
    os.makedirs('./configuration recommender/log', exist_ok=True)
    log_file = './configuration recommender/log/job_{}.log'.format(int(time.time()))
    sql_path = './benchmark_queries/job_all.sql'

    try:
        conn = pymysql.connect(**db_config)
        cursor = conn.cursor()
        ok_count, fail_count, total_time = _run_sql_file(cursor, sql_path, log_file=log_file)
        cursor.close()
        conn.close()
    except Exception as e:
        print(f'JOB benchmark failed: {e}')
        return 0.0

    print(f'JOB done: ok={ok_count} fail={fail_count} time={total_time:.2f}s log={log_file}')
    if total_time <= 0 or ok_count == 0:
        return 0.0
    return float(ok_count) / total_time


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

    restart_knobs_command = MYSQL_RESTART_CMD
    state = os.system(restart_knobs_command)

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

    restart_knobs_command = MYSQL_RESTART_CMD
    state = os.system(restart_knobs_command)

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
    """Run TPC-DS queries from tpcds_all.sql; return queries/sec (higher is better)."""
    state = _apply_knobs_and_restart(knob)
    if state != 0:
        print('database restarting failed')
        return 0.0

    print('database has been restarted')
    os.makedirs('./configuration recommender/log', exist_ok=True)
    log_file = './configuration recommender/log/tpcds_{}.log'.format(int(time.time()))
    sql_path = './benchmark_queries/tpcds_all.sql'

    try:
        print(f'connecting to MySQL via {db_config.get("unix_socket") or db_config.get("host")} ...', flush=True)
        conn = pymysql.connect(**db_config)
        print('connected; starting TPC-DS workload', flush=True)
        cursor = conn.cursor()
        ok_count, fail_count, total_time = _run_sql_file(cursor, sql_path, log_file=log_file)
        cursor.close()
        conn.close()
    except Exception as e:
        print(f'TPC-DS benchmark failed: {e}')
        return 0.0

    print(f'TPC-DS done: ok={ok_count} fail={fail_count} time={total_time:.2f}s log={log_file}')
    if total_time <= 0 or ok_count == 0:
        return 0.0
    # Optimize for higher QPS (same direction as SYSBENCH throughput)
    return float(ok_count) / total_time


if __name__ == "__main__":

    # ---------- record dir + checkpoint ----------
    RECORD_DIR_NAME = config_parser['configuration recommender'].get('record_dir', 'record').strip()
    RECORD_DIR = os.path.join('./configuration recommender', RECORD_DIR_NAME)
    CHECKPOINT_PATH = os.path.join(RECORD_DIR, 'checkpoint.json')
    RUN_MODE = config_parser['configuration recommender'].get('run_mode', 'auto').strip().lower()
    HISTORY_PATH = os.path.join(RECORD_DIR, 'benmark_history')
    OPTIMAL_PATH = os.path.join(RECORD_DIR, 'optimal configuration')

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
        with open(CHECKPOINT_PATH, 'w', encoding='utf-8') as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)

    def clear_record_progress():
        os.makedirs(RECORD_DIR, exist_ok=True)
        for name in os.listdir(RECORD_DIR):
            path = os.path.join(RECORD_DIR, name)
            if name.startswith('turn_') or name in (
                'benmark_history', 'top_k', 'optimal configuration', 'checkpoint.json'
            ):
                try:
                    if os.path.isfile(path):
                        os.remove(path)
                except OSError as e:
                    print(f'Warning: cannot remove {path}: {e}')

    def resolve_run_mode():
        ckpt = load_checkpoint()
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
            )
        return response_json, 0, [], ''

    def restore_llm_server(process_url, ckpt_data):
        restore_url = process_url.rsplit('/process', 1)[0] + '/restore'
        payload = {
            'request_count': ckpt_data.get('request_count', 0),
            'history_top': ckpt_data.get('history_top', []),
            'last_result': ckpt_data.get('last_result', ''),
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

    url = 'http://{}:{}/process'.format(
        config_parser['configuration recommender']['LLM_server_IP'],
        int(config_parser['configuration recommender']['LLM_server_port']),
    )
    max_iteration = int(config_parser['configuration recommender']['iteration'])
    benchmark = config_parser['configuration recommender']['benchmark'].strip().upper()
    benchmark_switch = {
        "SYSBENCH": test_by_sysbench,
        "TPCC": test_by_tpcc,
        "JOB": test_by_job,
        "TPCDS": test_by_tpcds,
    }

    if mode == 'fresh':
        clear_record_progress()
        knob = get_current_knob()
        benchmark_func = benchmark_switch.get(benchmark)
        if benchmark_func is None:
            unknown_benchmark(benchmark)
            sys.exit(1)
        throughput = benchmark_func(knob)
        metric = [] if throughput == 0 else get_current_metric()
        data1 = [{
            "knob": knob,
            "throughput": throughput,
            "metric": metric,
        }]
        with open(HISTORY_PATH, "a", encoding="utf-8") as f:
            json.dump(data1[0], f, indent=4)
            f.write("\n")

        response = requests.post(url, json=data1)
        response_json = response.json()
        result, request_count, history_top, last_result = parse_llm_response(response_json)
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
        })
    else:
        print(
            f'Resuming: iteration={ckpt.get("iteration")}, '
            f'result_index={ckpt.get("result_index")}'
        )
        restore_llm_server(url, ckpt)
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
                })
                continue

            benchmark_func = benchmark_switch.get(benchmark)
            if benchmark_func is None:
                unknown_benchmark(benchmark)
                result_index = idx + 1
                continue

            throughput = benchmark_func(knob)
            if not isinstance(throughput, (int, float)) or isinstance(throughput, bool):
                throughput = 0.0
            else:
                throughput = float(throughput)

            metric = [] if throughput == 0 else get_current_metric()
            data = {
                "knob": knob,
                "throughput": throughput,
                "metric": metric,
            }
            data_list.append(data)
            with open(HISTORY_PATH, "a", encoding="utf-8") as f:
                json.dump(data, f, indent=4)
                f.write("\n")

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
            })

        if not data_list and result_index >= len(items):
            print('No valid knob configs in this iteration, stop.')
            break

        response = requests.post(url, json=data_list)
        response_json = response.json()
        result, request_count, history_top, last_result = parse_llm_response(response_json)
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
    })
    print(f'Done. Results saved under {RECORD_DIR}')