import requests
import json
import pymysql
import os
import sys
import time
import re
import paramiko
import configparser

cfg = configparser.ConfigParser()
cfg.read('./config.ini')

db_ip = cfg['configuration recommender']['DB_IP']
ip_password = cfg['configuration recommender']['DB_IP_Password']
db_config = {
    'user': cfg['configuration recommender']['DB_User'],       
    'password': cfg['configuration recommender']['DB_Password'],   
    'host': cfg['configuration recommender']['DB_Host'],          
    'database': cfg['configuration recommender']['DB_Name'],    
    'port': int(cfg['configuration recommender']['DB_Port']),
}

with open(cfg['knob selector']['candidate_knobs'], 'r') as f:
    original = json.load(f)
    original_keys = list(original.keys())

with open(cfg['range pruner']['output_file'], 'r') as f:
    selected_knobs = json.load(f)

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

    cursor.close()
    conn.close()
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
        cursor.execute("SHOW VARIABLES LIKE %s", (param,))
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
    with open(cfg['range pruner']['output_file'], 'r') as f:
        content = json.load(f)
    #content = set_expert_rule(content)

    result = {}
    count = 0
    for i in content.keys():
        result[i] = content[i]
        count += 1
    
    return result

def test_by_job(self,log_file):

    temp_config = {}
    knobs_detail = get_knobs_detail()
    for key in knobs_detail.keys():
        if key in knob.keys():
            if knobs_detail[key]['type'] == 'integer':
                temp_config[key] = knob.get(key) 
            elif knobs_detail[key]['type'] == 'enum':
                temp_config[key] = knobs_detail[key]['enum_values'][knob.get(key)]
    
    #set knobs and restart databases
    set_knobs_command = '\cp {} {};'.format('/etc/my.cnf.bak' , '/etc/my.cnf')
    for knobs in temp_config:
        index = int(knobs.replace("knob", "")) - 1
        knob_name = original_keys[index]
        set_knobs_command += 'echo "{}"={} >> {};'.format(knob_name,temp_config[knobs],'/etc/my.cnf')
    
    head_command = 'sshpass -p {} ssh {} '.format(ip_password, db_ip)
    set_knobs_command = head_command + '"' + set_knobs_command + '"' 
    state = os.system(set_knobs_command)

    time.sleep(10)

    print("success set knobs")
    #exit()

    restart_knobs_command = head_command + '"service mysqld restart"' 
    state = os.system(restart_knobs_command)

    if state == 0:
        print('database has been restarted')
        conn = pymysql.connect(**db_config)
        cursor = conn.cursor()
        # query file
        query_dir = ''
        query_files = [os.path.join(query_dir, f) for f in os.listdir(query_dir) if f.endswith('.sql')]
        total_time = 0
        i = 0 
        for i in range(1):
            i = i+1
            for query_file in query_files:
                print(f"Running {query_file}")
                elapsed_time = self.run_benchmark(query_file, cursor)
                print(f"Time taken: {elapsed_time:.2f} seconds")
                total_time += elapsed_time
        
        print(f"Total time for 5 runs: {total_time:.2f} seconds")

        cursor.close()
        conn.close()
        return total_time
    else:
        print('database restarting failed')
        return -1

    
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
    
    #set knobs and restart databases
    set_knobs_command = '\cp {} {};'.format('/etc/my.cnf.bak' , '/etc/my.cnf')
    for knobs in temp_config:
        set_knobs_command += 'echo "{}"={} >> {};'.format(knobs,temp_config[knobs],'/etc/my.cnf')
    
    head_command = 'sshpass -p {} ssh {} '.format(ip_password, db_ip)
    set_knobs_command = head_command + '"' + set_knobs_command + '"' 
    state = os.system(set_knobs_command)

    time.sleep(10)

    print("success set knobs")

    restart_knobs_command = head_command + '"service mysqld restart"' 
    state = os.system(restart_knobs_command)

    if state == 0:
        print('database has been restarted')
        log_file = './configuration recommender/log/' + '{}.log'.format(int(time.time()))
        ip = ''
        username = ''
        command = 'tpcc_start -S /var/lib/mysql/mysql.sock -d -u -p -w -c -r -l'
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
    
    #set knobs and restart databases (local execution, no SSH)
    set_knobs_command = '\cp {} {};'.format('/etc/my.cnf.bak' , '/etc/my.cnf')
    for knobs in temp_config:
        set_knobs_command += 'echo "{}"={} >> {};'.format(knobs,temp_config[knobs],'/etc/my.cnf')
    
    state = os.system(set_knobs_command)

    time.sleep(2)

    print("success set knobs")
    #exit()

    # Restart MySQL locally: shutdown then start via mysqld_safe
    mysql_admin = '/workspace/setup/mysql/bin/mysqladmin -u root -p{} -S /tmp/mysql.sock shutdown'.format(db_config.get('password'))
    os.system(mysql_admin)
    time.sleep(3)

    # Remove stale socket file before starting
    if os.path.exists('/tmp/mysql.sock'):
        os.remove('/tmp/mysql.sock')

    # Use subprocess.Popen with start_new_session to detach mysqld_safe from parent
    # so it won't be killed when os.system returns
    mysql_start_cmd = [
        '/bin/sh', 'bin/mysqld_safe',
        '--basedir=/workspace/setup/mysql',
        '--datadir=/workspace/setup/mysql/data',
    ]
    devnull = open(os.devnull, 'w')
    subprocess.Popen(
        mysql_start_cmd,
        cwd='/workspace/setup/mysql',
        stdout=devnull,
        stderr=devnull,
        stdin=devnull,
        start_new_session=True,
    )

    # Wait for MySQL socket to be ready (up to 60 seconds)
    mysql_ready = False
    for i in range(60):
        time.sleep(1)
        if os.path.exists('/tmp/mysql.sock'):
            mysql_ready = True
            break

    if not mysql_ready:
        print('database restarting failed: socket not ready after 60s')
        return 0

    # Extra grace period after socket appears
    time.sleep(3)

    print('database has been restarted')
    log_file = './configuration recommender/log/' + '{}.log'.format(int(time.time()))
    command_run = 'sysbench --db-driver=mysql --threads=32 --mysql-socket=/tmp/mysql.sock --mysql-user={} --mysql-password={} --mysql-db={} --tables=50 --table-size=1000000 --time=120 --report-interval=60 oltp_read_write run'.format(
                        db_config.get('user'),
                        db_config.get('password'),
                        db_config.get('database')
                        )
    
    os.system(command_run + ' > "{}" '.format(log_file))

    try:
        qps = sum([float(line.split()[8]) for line in open(log_file,'r').readlines() if 'qps' in line][-int(120/60):]) / (int(120/60))
    except (FileNotFoundError, IndexError, ZeroDivisionError):
        print(f"Failed to parse sysbench log: {log_file}")
        return 0
    tps = float(qps/20.0)
    return tps

def unknown_benchmark(name):
    print(f"Unknown benchmark: {name}")

def test_by_tpcds(self,log_file):

    temp_config = {}
    knobs_detail = get_knobs_detail()
    for key in knobs_detail.keys():
        if key in knob.keys():
            if knobs_detail[key]['type'] == 'integer':
                temp_config[key] = knob.get(key) 
            elif knobs_detail[key]['type'] == 'enum':
                temp_config[key] = knobs_detail[key]['enum_values'][knob.get(key)]
    
    #set knobs and restart databases
    set_knobs_command = '\cp {} {};'.format('/etc/my.cnf.bak' , '/etc/my.cnf')
    for knobs in temp_config:
        index = int(knobs.replace("knob", "")) - 1
        knob_name = original_keys[index]
        set_knobs_command += 'echo "{}"={} >> {};'.format(knob_name,temp_config[knobs],'/etc/my.cnf')
    
    head_command = 'sshpass -p {} ssh {} '.format(ip_password, db_ip)
    set_knobs_command = head_command + '"' + set_knobs_command + '"' 
    state = os.system(set_knobs_command)

    time.sleep(10)

    print("success set knobs")
    #exit()

    restart_knobs_command = head_command + '"service mysqld restart"' 
    state = os.system(restart_knobs_command)

    if state == 0:
        print('database has been restarted')
        conn = pymysql.connect(**db_config)
        cursor = conn.cursor()
        # query file
        query_dir = ''
        query_files = [os.path.join(query_dir, f) for f in os.listdir(query_dir) if f.endswith('.sql')]
        total_time = 0
        i = 0 
        for i in range(1):
            i = i+1
            for query_file in query_files:
                print(f"Running {query_file}")
                elapsed_time = self.run_benchmark(query_file, cursor)
                print(f"Time taken: {elapsed_time:.2f} seconds")
                total_time += elapsed_time
        
        print(f"Total time for 5 runs: {total_time:.2f} seconds")

        cursor.close()
        conn.close()
        return total_time
    else:
        print('database restarting failed')
        return -1

if __name__ == "__main__":

    knob = get_current_knob()
    throughput =  test_by_sysbench(knob)
    metric = get_current_metric()
    data = {
        "knob": knob,
        "throughput": throughput,
        "metric": metric
        } 
    data1 = [data]


    url = 'http://{}:{}/process'.format(cfg['configuration recommender']['LLM_server_IP'], cfg['configuration recommender']['LLM_server_port'])
    
    # Return the result to LLM_server 
    response = requests.post(url, json=data1)

    result = response.json()
    print(result)
    
    
    iteration = 0
    best_knob = None
    best_metric = []
    best_throughput = 0
    while iteration < int(cfg['configuration recommender']['iteration']):
        data_list = []
        # result 可能是 dict、list 或 str，统一处理
        if isinstance(result, dict):
            items = list(result.values())
        elif isinstance(result, list):
            items = result
        elif isinstance(result, str):
            items = [result]
        else:
            items = []

        for knobs in items:
            if isinstance(knobs, str):
                if not knobs.strip():
                    continue
                knob = json.loads(knobs)
            else:
                knob = knobs

            benchmark = cfg['configuration recommender']['benchmark'].strip().upper()
            benchmark_switch = {
                "SYSBENCH": test_by_sysbench,
                "TPCC": test_by_tpcc,
                "JOB": test_by_job,
                "TPCDS": test_by_tpcds
            }
            benchmark_func = benchmark_switch.get(benchmark)
            if benchmark_func is None:
                unknown_benchmark(benchmark)
                continue
            throughput = benchmark_func(knob)
            # 严格类型保护：确保 throughput 是数字
            if isinstance(throughput, bool) or not isinstance(throughput, (int, float)):
                print(f"Warning: throughput is {type(throughput).__name__}: {throughput!r}, converting to 0")
                throughput = 0
            else:
                throughput = float(throughput)
            if(throughput == 0):
                metric = []
            else:
                metric = get_current_metric()
            data = {
                "knob": knob,
                "throughput": throughput,
                "metric": metric
            }
            data_list.append(data)
            with open('./configuration recommender/record/benmark_history',"a") as f:
                json.dump(data, f, indent=4)
            # 确保 best_throughput 是数字
            if not isinstance(best_throughput, (int, float)):
                best_throughput = 0
            if throughput > best_throughput:
                best_knob = knob
                best_metric = metric
                best_throughput = throughput
        

        url = 'http://{}:{}/process'.format(cfg['configuration recommender']['LLM_server_IP'], cfg['configuration recommender']['LLM_server_port'])
        #  Return the result to LLM_server 
        response = requests.post(url, json=data_list)

        result = response.json()
        #print(result)
        iteration = iteration+1
    
    #The optimal configuration found
    with open("./configuration recommender/record/optimal configuration", "w", encoding="utf-8") as f:
        print("best_knob:", best_knob, file=f)
        print("best_throughput:", best_throughput, file=f)
