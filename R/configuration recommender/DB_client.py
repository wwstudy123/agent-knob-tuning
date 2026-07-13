"""
数据库客户端：执行基准测试、采集指标并与LLM服务交互

1. 管理MySQL数据库连接（统一使用TCP/IP连接）
2. 应用旋钮配置并重启MySQL
3. 执行工作负载基准测试
4. 采集数据库性能指标
5. 与LLM_server通信，迭代优化配置
"""

import requests
import json
import pymysql
import sys
import os
import time
import re
import paramiko
import configparser
import subprocess
import socket
import glob

# 读取配置文件
config_parser = configparser.ConfigParser()
config_parser.read('./config.ini')

# 数据库连接配置项
db_ip = config_parser['configuration recommender']['DB_IP']
ip_password = config_parser['configuration recommender']['DB_IP_Password']
db_config = {
    'user': config_parser['configuration recommender']['DB_User'],       
    'password': config_parser['configuration recommender']['DB_Password'],   
    'host': config_parser['configuration recommender']['DB_Host'],          
    'database': config_parser['configuration recommender']['DB_Name'],    
    'port': int(config_parser['configuration recommender']['DB_Port'])
}

# 其他配置项
llm_server_ip = config_parser['configuration recommender']['LLM_server_IP']
llm_server_port = int(config_parser['configuration recommender']['LLM_server_port'])
iteration_count = int(config_parser['configuration recommender']['iteration'])
benchmark_name = config_parser['configuration recommender']['benchmark'].strip().upper()
candidate_knobs_file = config_parser['knob selector']['candidate_knobs']
pruned_knobs_file = config_parser['range pruner']['output_file']

# 创建必需的目录
os.makedirs('./configuration recommender/log', exist_ok=True)
os.makedirs('./configuration recommender/record', exist_ok=True)

# 所有筛选之前的旋钮及其字段
with open(candidate_knobs_file, 'r') as f:
    original = json.load(f)
    original_keys = list(original.keys()) # 真实的旋钮名称列表，按顺序排列，一一对应匿名[knob1,knob2...]

# 所有筛选之前的旋钮及其字段（匿名版）
with open('./range pruner/renamed_knobs', 'r') as f:
    knob_details_full = json.load(f)

# 经过范围剪枝的重要旋钮及其字段
with open(pruned_knobs_file, 'r') as f:
    pruned_knobs = json.load(f)


def get_mysql_connection():
    """
    辅助函数：获取MySQL数据库连接（统一使用TCP/IP连接）

    NOTE：这个函数每次调用都会创建一个新的连接，使用后记得关闭连接;
    创建连接->断开连接 ≠ 重启数据库
    Returns:
        pymysql.Connection: 数据库连接对象
    Raises:
        RuntimeError: 连接失败时抛出异常
    """
    try:
        conn = pymysql.connect(
            host=db_config['host'],
            port=db_config['port'],
            user=db_config['user'],
            password=db_config['password'],
            database=db_config['database'],
            connect_timeout=5,
            read_timeout=10,
            write_timeout=10
        )
        return conn
    except Exception as e:
        raise RuntimeError(f"MySQL连接失败: {e}") from e


def _is_port_open(host, port, timeout_seconds=2):
    """
    辅助函数：检查指定主机端口是否可连接
    Args:
        host: 主机地址
        port: 端口号
        timeout_seconds: 连接超时时间(s)
    Returns:
        bool: 可连接返回True，否则False
    """
    try:
        with socket.create_connection((host, port), timeout=timeout_seconds):
            return True
    except Exception:
        return False


def wait_for_mysql_ready(timeout_seconds=60, interval_seconds=2):
    """
    等待MySQL服务可用（通过TCP/IP探活）
    Args:
        timeout_seconds: 最长等待时间(s)
        interval_seconds: 轮询间隔(s)
    Returns:
        bool: MySQL可用则返回True，否则False
    """
    start_time = time.time()
    last_error = None
    while time.time() - start_time <= timeout_seconds:
        if not _is_port_open(db_config['host'], db_config['port']):
            time.sleep(interval_seconds)
            continue
        try:
            conn = get_mysql_connection()
            with conn.cursor() as cursor:
                cursor.execute("SELECT 1")
            conn.close()
            print("MySQL服务已就绪，可以进行TCP/IP连接")
            return True
        except Exception as e:
            last_error = str(e)
        
        time.sleep(interval_seconds)
    
    if last_error:
        print(f"等待MySQL超时（{timeout_seconds}秒），最后错误: {last_error}")
    else:
        print(f"等待MySQL超时（{timeout_seconds}秒）")
    return False


def _build_mysqladmin_shutdown_command():
    """
    构建基于TCP/IP的mysqladmin关闭命令
    Returns:
        str: mysqladmin shutdown命令
    """
    base_cmd = (
        f"/workspace/setup/mysql/bin/mysqladmin -u {db_config['user']} "
        f"-h {db_config['host']} -P {db_config['port']}"
    )
    if db_config.get('password'):
        base_cmd += f" -p{db_config['password']}"
    return f"{base_cmd} shutdown"


def _wait_for_mysql_stop(timeout_seconds=60, interval_seconds=2):
    """
    等待MySQL服务完全停止（端口不再监听）
    Args:
        timeout_seconds: 最长等待时间(s)
        interval_seconds: 轮询间隔(s)
    Returns:
        bool: MySQL已停止返回True，超时返回False
    """
    start_time = time.time()
    while time.time() - start_time <= timeout_seconds:
        if not _is_port_open(db_config['host'], db_config['port']):
            print("MySQL服务已完全停止")
            return True
        time.sleep(interval_seconds)
    print(f"等待MySQL停止超时（{timeout_seconds}秒），端口仍在监听")
    return False


def _is_mysql_process_alive():
    """
    辅助函数：检查是否存在活跃的mysqld进程

    Returns:
        bool: 存在mysqld进程返回True，否则False
    """
    result = subprocess.run(
        ['pgrep', '-x', 'mysqld'],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL
    )
    return result.returncode == 0


def _wait_for_mysql_process_exit(timeout_seconds=90, interval_seconds=1):
    """
    等待所有mysqld进程完全退出

    关键说明：端口关闭 ≠ 进程退出。执行mysqladmin shutdown后，MySQL会先关闭端口
    （停止接受新连接），然后InnoDB在后台继续刷脏页、dump缓冲池。如果在进程退出前
    就强制kill，会导致下次启动触发crash recovery（可能耗时数分钟）。

    Args:
        timeout_seconds: 最长等待时间(s)
        interval_seconds: 轮询间隔(s)
    Returns:
        bool: 进程已全部退出返回True，超时返回False
    """
    start_time = time.time()
    while time.time() - start_time <= timeout_seconds:
        if not _is_mysql_process_alive():
            print("mysqld进程已完全退出")
            return True
        time.sleep(interval_seconds)
    print(f"等待mysqld进程退出超时（{timeout_seconds}秒）")
    return False


def _clean_pid_files():
    """
    清理残留的PID文件，防止mysqld_safe误判实例仍在运行
    """
    pid_files = glob.glob('/workspace/setup/mysql/data/*.pid')
    for pid_file in pid_files:
        try:
            os.remove(pid_file)
            print(f"已清理残留PID文件: {pid_file}")
        except OSError:
            pass


def _force_kill_mysql():
    """
    强制杀死所有mysqld和mysqld_safe残留进程，并清理PID文件

    仅在以下场景使用（非正常路径）：
    - MySQL因不良配置崩溃，mysqld_safe在后台循环尝试重启
    - 优雅关闭超时，进程无法正常退出
    - 需要从完全异常的状态中恢复
    """
    print("执行强制清理：杀死所有MySQL相关进程...")
    for proc_name in ['mysqld_safe', 'mysqld']:
        subprocess.run(
            ['pkill', '-9', proc_name],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )

    # 等待OS回收进程和释放文件锁，然后确认进程已退出
    time.sleep(3)
    _wait_for_mysql_process_exit()
    _clean_pid_files()


def _restart_mysql():
    """
    健壮的MySQL重启逻辑：
    1. 优雅关闭：发送shutdown命令后，等待mysqld进程完全退出（而非仅等端口关闭）
    2. 仅在异常情况下才使用force kill（避免打断InnoDB的正常关闭流程导致crash recovery）
    3. 启动新实例并保留错误日志以便排查

    核心修复：之前的版本在mysqladmin shutdown后仅等待端口关闭就执行pkill -9，
    但此时InnoDB仍在刷脏页和dump缓冲池。这导致下次启动必须做crash recovery，
    对于大缓冲池(5-9GB)可能耗时数分钟，远超等待超时。

    Returns:
        bool: 重启成功且可用返回True，否则False
    """
    shutdown_cmd = _build_mysqladmin_shutdown_command()
    # 使用带时间戳的唯一日志文件名，避免成功启动覆盖失败时的日志
    log_dir = './configuration recommender/log/mysql_log'
    os.makedirs(log_dir, exist_ok=True)
    log_path = f'{log_dir}/start_{time.strftime("%Y_%m_%d_%H-%M-%S")}.log'
    start_cmd = (
        f"/workspace/setup/mysql/bin/mysqld_safe "
        f"--defaults-file=/workspace/setup/mysql/my.cnf > {log_path} 2>&1 &"
    )

    print("正在重启MySQL...")

    # --- 步骤1：关闭MySQL ---
    if _is_port_open(db_config['host'], db_config['port']):
        # MySQL正在运行，执行优雅关闭
        print("服务正在运行，执行正常关闭...")

        # 关闭前禁用缓冲池dump，避免大缓冲池(5-9GB)的dump操作耗时过长
        # 调优场景下每次配置不同，缓冲池内容无复用价值，跳过dump大幅缩短关闭时间
        try:
            conn = get_mysql_connection()
            with conn.cursor() as cursor:
                cursor.execute("SET GLOBAL innodb_buffer_pool_dump_at_shutdown = OFF")
                cursor.execute("SET GLOBAL innodb_fast_shutdown = 1")
            conn.close()
            print("已禁用缓冲池dump，加速关闭")
        except Exception as e:
            print(f"禁用缓冲池dump失败（不影响关闭）: {e}")

        state = os.system(shutdown_cmd)
        if state != 0:
            # mysqladmin命令本身失败，直接force kill
            print("mysqladmin关闭命令失败，改用强制杀死...")
            _force_kill_mysql()
        else:
            # mysqladmin成功发送了shutdown信号
            # 先等端口关闭（通常很快）
            if not _wait_for_mysql_stop(timeout_seconds=30):
                print("端口关闭超时，改用强制杀死...")
                _force_kill_mysql()
            else:
                # 端口已关闭，但mysqld可能仍在刷脏页/dump缓冲池
                # 【关键】必须等待mysqld进程完全退出，否则kill会导致crash recovery
                print("端口已关闭，等待mysqld进程完全退出（InnoDB正在刷脏页）...")
                if not _wait_for_mysql_process_exit(timeout_seconds=130):
                    print("进程退出超时，改用强制杀死...")
                    _force_kill_mysql()
                else:
                    # 优雅关闭完全成功，仅清理PID文件
                    _clean_pid_files()
    elif _is_mysql_process_alive():
        # 端口已关闭但存在mysqld进程（崩溃/异常状态）
        print("端口已关闭但mysqld进程仍存在（崩溃状态），强制清理...")
        _force_kill_mysql()
    else:
        # 完全停止状态
        print("MySQL已完全停止，跳过关闭步骤")
        _clean_pid_files()

    # --- 步骤2：启动新MySQL实例 ---
    print("正在启动新MySQL实例...")
    os.system(start_cmd)

    # --- 步骤3：等待就绪（120秒，覆盖大缓冲池初始化和可能的crash recovery） ---
    if wait_for_mysql_ready(timeout_seconds=130):
        print("MySQL服务重启成功")
        return True
    else:
        print("错误：MySQL重启超时！")
        print(f"请检查启动日志: {log_path}")
        # 打印日志末尾帮助诊断
        try:
            result = subprocess.run(
                ['tail', '-n', '20', log_path],
                capture_output=True, text=True
            )
            if result.stdout.strip():
                print("--- 启动日志末尾 ---")
                print(result.stdout)
                print("--- 日志结束 ---")
        except Exception:
            pass
        # 同时检查MySQL自身的错误日志
        try:
            result = subprocess.run(
                ['tail', '-n', '10', '/workspace/setup/mysql/data/LAPTOP-SQRHDF68.err'],
                capture_output=True, text=True
            )
            if result.stdout.strip():
                print("--- MySQL错误日志末尾 ---")
                print(result.stdout)
                print("--- 日志结束 ---")
        except Exception:
            pass
        return False

def _get_knob_name_from_key(key):
    """
    从knobN格式的匿名旋钮获取真实的旋钮名称
    Args:
        key: knobN格式的匿名旋钮
    Returns:
        str: 真实的旋钮名称，若无法识别则返回None
    """
    if key.startswith('knob') and key[4:].isdigit():
        index = int(key[4:]) - 1
        if 0 <= index < len(original_keys):
            return original_keys[index]
    else:
        print(f"无法识别匿名旋钮: {key}")
    return None


def _convert_real_to_anonymous(real_knob_dict):
    """
    将真实旋钮名称转换为匿名名称（knobN格式），用于向LLM_server发送一致的数据
    Args:
        real_knob_dict: 真实旋钮名称dict{real_name: value}
    Returns:
        dict: 匿名旋钮名称dict{knobN: value, ...}
    """
    anon_dict = {}
    for anon_key in pruned_knobs.keys():
        index = int(anon_key.replace("knob", "")) - 1
        if 0 <= index < len(original_keys):
            real_name = original_keys[index]
            if real_name in real_knob_dict:
                anon_dict[anon_key] = real_knob_dict[real_name]
    return anon_dict


def convert_anonymous_to_real(knob):
    """
    将旋钮配置中的匿名旋钮转换为真实旋钮名称
    注意：到达此函数时，配置已在LLM_server中完成白盒验证和修正
    Args:
        knob: 匿名旋钮dict{knob1: value1, knob2: value2, ...}
    Returns:
        dict: 转换后的真实旋钮名称及其值
    """
    temp_config = {}

    for key, value in knob.items():
        # 匿名转为真实名称
        knob_name = _get_knob_name_from_key(key)
        if knob_name is None:
            continue
        # 直接使用LLM_server验证并修正后的值
        try:
            temp_config[knob_name] = int(value)
        except (ValueError, TypeError):
            print(f"错误: 旋钮{knob_name}的值'{value}'无法转换为整数，使用原值")
            temp_config[knob_name] = value

    return temp_config


def apply_mysql_config(temp_config):
    """
    将旋钮配置写入my.cnf并重启MySQL，失败则回滚my.cnf文件
    Args:
        temp_config: 旋钮配置字典
    Returns:
        bool: 成功返回True，失败返回False
    """
    if not temp_config:
        print("旋钮配置为空，无法写入，跳过重启")
        return False

    # 构建配置写入命令
    set_knobs_command = f"\\cp /workspace/setup/mysql/my.cnf.bak /workspace/setup/mysql/my.cnf"
    for param_name, param_value in temp_config.items():
        set_knobs_command += f' && echo "{param_name}={param_value}" >> /workspace/setup/mysql/my.cnf'

    # NOTE：旋钮配置写入到my.cnf文件，并且MySQL重启之后旋钮配置才可以生效
    state = os.system(set_knobs_command)
    if state != 0:
        print("旋钮配置命令写入my.cnf失败，跳过重启")
        return False

    print("配置写入成功，尝试重启 MySQL 服务...")
    if _restart_mysql():
        print("MySQL服务重启成功")
        return True

    print("错误：MySQL启动失败，尝试将my.cnf恢复到初始备份状态并重启...")
    # 回滚前先彻底杀死旧进程，防止mysqld_safe还在用坏配置反复重启mysqld
    _force_kill_mysql()
    rollback_command = '\\cp /workspace/setup/mysql/my.cnf.bak /workspace/setup/mysql/my.cnf'
    os.system(rollback_command)
    if _restart_mysql():
        print("my.cnf已恢复到初始备份状态，MySQL服务重启成功")
        return False
    else:
        print("my.cnf已恢复到初始备份状态，MySQL服务仍然无法重启")
        return False

def get_current_metric():
    """
    获取运行当前配置后的几十个InnoDB测试指标（TCP/IP连接）
    Returns:
        dict: 指标dict{指标名称: 指标值}，失败则返回空字典
    """
    if not wait_for_mysql_ready():
        print("等待MySQL服务超时，无法获取指标")
        return {}

    try:
        conn = get_mysql_connection()

        # 使用cursor执行SQL查询并获取结果
        cursor = conn.cursor()
        sql = "SELECT name, count FROM information_schema.INNODB_METRICS WHERE status = 'enabled'"
        cursor.execute(sql)
        result = cursor.fetchall()
        
        # InnoDB内部指标，挺多的，大约65个
        inner_metrics = {name: int(count) for name, count in result}
        
        cursor.close()
        conn.close()
        
        return inner_metrics
    except Exception as e:
        print(f"获取InnoDB指标失败: {e}")
        return {}

def get_current_knob():
    """
    获取数据库关键参数的默认配置值（使用TCP/IP连接）
    Returns:
        dict: {真实旋钮名称: 当前值}，失败则返回空字典
    """
    if not wait_for_mysql_ready():
        print("等待MySQL服务超时，无法获取参数配置，返回空配置")
        return {}

    try:
        conn = get_mysql_connection()
        cursor = conn.cursor()

        # 构建需要查询的旋钮列表（即范围裁剪之后的重要旋钮）
        parameters = []
        for key in pruned_knobs.keys():
            index = int(key.replace("knob", "")) - 1
            if 0 <= index < len(original_keys):
                param_name = original_keys[index]
                parameters.append(param_name)

        knobs = {}
        # 批量查询筛选出的旋钮的默认配置值（使用IN语句）
        if parameters:
            placeholders = ','.join(['%s'] * len(parameters))
            sql = f"SHOW VARIABLES WHERE Variable_name IN ({placeholders})"
            cursor.execute(sql, parameters)
            results = cursor.fetchall()
            
            for var_name, var_value in results:
                try:
                    # 尝试转换为数值类型
                    if var_value.isdigit():
                        knobs[var_name] = int(var_value)
                    else:
                        try:
                            knobs[var_name] = round(float(var_value))
                        except ValueError:
                            # 无法转换为数值，保留字符串（如'ON'/'OFF'）
                            knobs[var_name] = var_value
                except AttributeError:
                    # var_value不是字符串（可能已经是数值）
                    knobs[var_name] = var_value

        cursor.close()
        conn.close()
        
        print("\n得到当前的旋钮配置:")
        print(json.dumps(knobs, indent=2))
        return knobs
    except Exception as e:
        print(f"获取数据库参数配置失败: {e}")
        return {}

def test_by_job(self, log_file):
    """
    执行JOB基准测试
    Args:
        log_file: 日志文件路径
    Returns:
        float: 总耗时，失败返回-1
    """

    temp_config = {}
    knobs_detail = pruned_knobs
    for key in knobs_detail.keys():
        if key in knob.keys():
            if knobs_detail[key]['type'] == 'integer':
                temp_config[key] = knob.get(key) 
            elif knobs_detail[key]['type'] == 'enum':
                temp_config[key] = knobs_detail[key]['enum_values'][knob.get(key)]
    
    # set knobs and restart databases 
    # my.cnf文件路径需要手动修改
    set_knobs_command = '\cp {} {};'.format('/workspace/setup/mysql/my.cnf.bak' , '/workspace/setup/mysql/my.cnf')
    for knobs in temp_config:
        index = int(knobs.replace("knob", "")) - 1
        knob_name = original_keys[index]
        set_knobs_command += 'echo "{}"={} >> {};'.format(knob_name,temp_config[knobs],'/workspace/setup/mysql/my.cnf')
    
    # 修改点：执行命令(WSL2不需要SSH)
    # head_command = 'sshpass -p {} ssh {} '.format(ip_password, db_ip)
    # set_knobs_command = head_command + '"' + set_knobs_command + '"' 
    state = os.system(set_knobs_command)

    time.sleep(10)

    print("success set knobs")
    #exit()

    # 修改点：使用TCP/IP重启并等待MySQL就绪
    if _restart_mysql():
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
    print('database restarting failed')
    return -1

    
def test_by_tpcc(knob):
    """
    执行TPCC基准测试
    Args:
        knob: 旋钮配置dict
    Returns:
        float: TPS值，失败返回0
    """
    #load knobs
    temp_config = {}
    knobs_detail = pruned_knobs
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
    # 修改my.cnf文件路径
    set_knobs_command = '\cp {} {};'.format('/workspace/setup/mysql/my.cnf.bak' , '/workspace/setup/mysql/my.cnf')
    for knobs in temp_config:
        set_knobs_command += 'echo "{}"={} >> {};'.format(knobs,temp_config[knobs],'/workspace/setup/mysql/my.cnf')
    
    # 执行命令(WSL2不需要SSH)
    state = os.system(set_knobs_command)

    time.sleep(10)

    print("success set knobs")

    # 修改点：使用TCP/IP重启并等待MySQL就绪
    if _restart_mysql():
        print('database has been restarted')
        log_file = './configuration recommender/log/' + '{}.log'.format(int(time.time()))
        ip = ''
        username = ''
        password_flag = f"-p {db_config['password']}" if db_config.get('password') else ""
        command = (
            f"tpcc_start -h {db_config['host']} -P {db_config['port']} "
            f"-u {db_config['user']} {password_flag} -d -w -c -r -l"
        ).strip()
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
    print('database restarting failed')
    return 0
    

def test_by_sysbench(knob, isStart=False):
    """
    使用Sysbench进行基准测试
    Args:
        knob: 旋钮配置dict
        isStart: 是否为初始配置测试（若是，则不需要进行匿名转换）
    Returns:
        float: 吞吐量指标：平均TPS（事务/秒），失败返回0
    """
    # 构建MySQL配置
    temp_config = knob if isStart else convert_anonymous_to_real(knob)
    
    # 应用配置并重启MySQL
    if not apply_mysql_config(temp_config):
        print("旋钮配置应用失败，跳过本轮测试，test_by_sysbench()返回0")
        return 0.0

    # 预热延迟：等待MySQL完成缓冲池加载等后台初始化，确保可稳定处理并发连接
    print('配置应用成功且MySQL已重启，sleep等待数据库5秒...')
    time.sleep(5)

    print('开始基准测试...')
    log_file = './configuration recommender/log/sysbench_log/log_{}.log'.format(time.strftime('%Y_%m_%d_%H-%M-%S'))

    # 构建sysbench命令（使用TCP/IP连接）
    password_option = f'--mysql-password={db_config["password"]} ' if db_config.get('password') else ''
    command_run = (
        'sysbench --db-driver=mysql --threads=32 '
        '--mysql-host={host} --mysql-port={port} '
        '--mysql-user={user} {password_option}'
        '--mysql-db={database} '
        '--tables=50 --table-size=1000000 '
        '--time=120 --report-interval=5 ' # 测试120s，每5s输出一次中间结果，监控性能趋势
        '--warmup-time=80 ' # 预热时间，确保冷启动完成
        '--rand-seed=42 ' # 随机种子固定，确保每轮测试数据访问模式一致
        'oltp_read_write run'
    ).format(
        host=db_config['host'],
        port=db_config['port'],
        user=db_config['user'],
        password_option=password_option,
        database=db_config['database']
    )
    
    # 执行sysbench测试
    try:
        with open(log_file, 'w') as log_fp:
            result = subprocess.run(command_run, shell=True, stdout=log_fp, stderr=log_fp, check=False)
        if result.returncode != 0:
            print(f"sysbench执行失败，返回码: {result.returncode}")
            return 0.0
    except Exception as e:
        print(f"执行sysbench失败: {e}")
        return 0.0
    
    # 解析TPS
    try:
        with open(log_file, 'r') as f:
            content = f.read()
            
        # 方法1：从"transactions:"行提取TPS（最准确）
        trans_match = re.search(r'transactions:\s+\d+\s+\(([0-9.]+)\s+per sec', content, re.IGNORECASE)
        if trans_match:
            tps = float(trans_match.group(1))
            print(f"测试完成: 准确TPS={tps:.2f}")
            return tps
        
        # 方法2：从报告间隔行提取tps（多个值取平均）
        tps_lines = re.findall(r'tps:\s*([0-9.]+)', content, re.IGNORECASE)
        if tps_lines:
            recent_tps = [float(x) for x in tps_lines[-int(120/60):]]
            avg_tps = sum(recent_tps) / len(recent_tps)
            print(f"测试完成: 平均TPS={avg_tps:.2f}")
            return avg_tps
        
        # 方法3：从qps计算tps（假设每个事务约20个查询）
        qps_lines = re.findall(r'qps:\s*([0-9.]+)', content, re.IGNORECASE)
        if qps_lines:
            recent_qps = [float(x) for x in qps_lines[-int(120/60):]]
            avg_qps = sum(recent_qps) / len(recent_qps)
            tps = avg_qps / 20.0
            print(f"测试完成: 基于平均QPS={avg_qps:.2f}, 估算TPS={tps:.2f}")
            return tps
        
        print(f"错误: 日志文件中未找到TPS/QPS数据: {log_file}")
        # 打印日志末尾便于调试
        lines = content.split('\n')
        print("日志末尾内容:")
        for line in lines[-20:]:
            if line.strip():
                print(line)
        return 0.0
    except Exception as e:
        print(f"解析日志文件时发生错误: {e}")
        return 0.0


def test_by_tpcds(self, log_file):
    """
    执行TPC-DS基准测试
    Args:
        log_file: 日志文件路径
    Returns:
        float: 总耗时，失败返回-1
    """

    temp_config = {}
    knobs_detail = pruned_knobs
    for key in knobs_detail.keys():
        if key in knob.keys():
            if knobs_detail[key]['type'] == 'integer':
                temp_config[key] = knob.get(key) 
            elif knobs_detail[key]['type'] == 'enum':
                temp_config[key] = knobs_detail[key]['enum_values'][knob.get(key)]
    
    #set knobs and restart databases
    set_knobs_command = '\cp {} {};'.format('/workspace/setup/mysql/my.cnf.bak' , '/workspace/setup/mysql/my.cnf')
    for knobs in temp_config:
        index = int(knobs.replace("knob", "")) - 1
        knob_name = original_keys[index]
        set_knobs_command += 'echo "{}"={} >> {};'.format(knob_name,temp_config[knobs],'/workspace/setup/mysql/my.cnf')
    
    # 修改点：执行命令(WSL2不需要SSH)
    state = os.system(set_knobs_command)

    time.sleep(10)

    print("success set knobs")
    #exit()

    # 使用TCP/IP重启并等待MySQL就绪
    if _restart_mysql():
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
    print('database restarting failed')
    return -1


if __name__ == "__main__":
    """
    主程序：与LLM_server交互，迭代优化数据库配置
    
    流程：
    1. 获取初始数据库旋钮配置和性能指标
    2. 运行基准测试获得初始吞吐量
    3. 发送反馈给LLM_server，获得Top-K配置
    4. 应用Top-K配置，采集反馈指标
    5. 与LLM_server通信，迭代测试，更新最佳配置
    """
    # 最开始先是DB_client.py启动
    print("="*60)
    print("开始数据库旋钮调优任务")
    
    # NOTE：获取初始时的数据库默认配置
    print("\n[初始阶段]获取初始配置并运行基准测试...")
    knob = get_current_knob()
    if not knob:
        raise RuntimeError("无法获取当前数据库配置，程序终止")
    
    throughput = test_by_sysbench(knob, isStart=True)
    metric = get_current_metric()
    
    # 将真实旋钮名称转为匿名名称，保持与LLM_server的命名一致性
    knob_anonymous = _convert_real_to_anonymous(knob)
    
    # 封装初始数据（使用匿名名称发送给LLM_server）
    data = {
        "knob": knob_anonymous,
        "throughput": throughput,
        "metric": metric
    }
    data_list = [data]
    
    # 向LLM_server发送初始配置的测试反馈
    print(f"\n[初始阶段]向LLM_server发送初始配置的测试反馈...")
    url = f'http://{llm_server_ip}:{llm_server_port}/process'
    
    try:
        response = requests.post(url, json=data_list, timeout=60) # NOTE：进入到LLM_server的process_data()函数
        if response.status_code != 200:
            raise RuntimeError(f"LLM_server返回异常状态码: {response.status_code}")
        result = response.json()
    except requests.exceptions.RequestException as e:
        raise RuntimeError(f"与LLM_server通信失败: {e}") from e
    except Exception as e:
        raise RuntimeError(f"LLM_server返回内容无法解析: {e}") from e
    
    print(f"[初始阶段] 收到LLM_server生成的{len(result)}个配置")
    
    # 初始化全局最佳配置（使用真实旋钮名称）
    best_knob = knob
    best_metric = metric
    best_throughput = throughput
    
    # 开始迭代优化
    print(f"\n开始迭代优化（共{iteration_count}轮）...")
    iteration = 0
    while iteration < iteration_count:
        iteration += 1
        print(f"\n{'='*60}")
        print(f"迭代轮次: {iteration}/{iteration_count}")
        
        data_list = []
        
        # 对LLM_server返回的Top-K配置，逐一测试
        for idx, knobs in enumerate(result, 1):
            if not isinstance(knobs, dict):
                print(f"错误: 配置{idx}格式错误，跳过")
                continue
            
            print(f"\n配置 {idx}/{len(result)} 开始测试...")
            
            # 执行基准测试
            benchmark_func = {
                "SYSBENCH": test_by_sysbench,
                "TPCC": test_by_tpcc,
                "JOB": test_by_job,
                "TPCDS": test_by_tpcds
            }.get(benchmark_name, lambda _: (print(f"未知的benchmark: {benchmark_name}"), 0)[1])
            
            throughput = benchmark_func(knobs)
            metric = get_current_metric() if throughput > 0 else {}
            
            # 封装测试结果
            data = {
                "knob": knobs,
                "throughput": throughput,
                "metric": metric
            }
            data_list.append(data)
            
            # 记录每一套配置及其测试反馈
            with open('./configuration recommender/record/benchmark_feedback', "a") as f:
                json.dump(data, f, indent=2)
                f.write('\n')
            
            # 更新全局最佳配置（将匿名名称转为真实旋钮名称保存）
            if throughput > best_throughput:
                best_knob = convert_anonymous_to_real(knobs)
                best_metric = metric
                best_throughput = throughput
                print(f"发现更优配置：TPS={throughput:.2f}")
        
        # 向LLM_server发送本轮测试结果
        print(f"\n[迭代 {iteration}] 向LLM_server发送{len(data_list)}个配置的测试结果...")
        try:
            response = requests.post(url, json=data_list, timeout=60) # NOTE：进入到LLM_server的process_data()函数
            if response.status_code != 200:
                raise RuntimeError(f"LLM_server返回异常状态码: {response.status_code}")
            result = response.json()
            print(f"[迭代 {iteration}] 收到LLM_server生成的{len(result)}个新配置")
        except Exception as e:
            print(f"错误: 与LLM_server通信出错: {e}")
            break
    
    # 输出最终结果
    print("\n" + "="*60)
    print("调优完成！最佳配置如下：")
    print(f"最佳吞吐量: {best_throughput:.2f} TPS")
    print(f"最佳旋钮配置:\n{json.dumps(best_knob, indent=2)}")
    
    with open("./configuration recommender/record/optimal configuration", "w", encoding="utf-8") as f:
        f.write(f"最佳吞吐量: {best_throughput}\n\n")
        f.write("最佳旋钮配置:\n")
        f.write(json.dumps(best_knob, indent=2))
        f.write("\n\n最佳性能指标:\n")
        f.write(json.dumps(best_metric, indent=2))
    
    print("\n结果已保存到: ./configuration recommender/record/optimal configuration")
    print("="*60 + "\n")