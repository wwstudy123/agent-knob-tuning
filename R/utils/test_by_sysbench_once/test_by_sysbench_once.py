"""
一次性Sysbench压测脚本

功能：
1. 接收一套数据库旋钮配置（真实旋钮名称）
2. 应用配置并重启MySQL
3. 执行Sysbench性能测试
4. 打印测试结果和重要日志

使用方式：
    方式1：通过命令行参数传递JSON格式的配置
    python utils/test_by_sysbench_once/test_by_sysbench_once.py '{"innodb_buffer_pool_size": 1073741824, "max_connections": 200}'

    NOTE：方式2：通过JSON文件传递配置
    python utils/test_by_sysbench_once/test_by_sysbench_once.py --config-file utils/test_by_sysbench_once/test_by_sysbench_once_config.json

    方式3：直接运行，不传递任何参数则使用当前数据库配置
    python utils/test_by_sysbench_once/test_by_sysbench_once.py
"""

import configparser
import json
import os
import re
import socket
import subprocess
import sys
import time
import argparse
from typing import Dict, Optional, Union


# ==================== 配置读取 ====================

# 读取配置文件
config_parser = configparser.ConfigParser()
config_parser.read('./config.ini')

# 数据库连接配置
db_config = {
    'user': config_parser['configuration recommender']['DB_User'],
    'password': config_parser['configuration recommender']['DB_Password'],
    'host': config_parser['configuration recommender']['DB_Host'],
    'database': config_parser['configuration recommender']['DB_Name'],
    'port': int(config_parser['configuration recommender']['DB_Port'])
}

# MySQL安装路径配置
MYSQL_BASE_DIR = '/workspace/setup/mysql'
MYSQL_CONFIG_FILE = f'{MYSQL_BASE_DIR}/my.cnf'
MYSQL_CONFIG_BACKUP = f'{MYSQL_BASE_DIR}/my.cnf.bak'
MYSQLADMIN_BIN = f'{MYSQL_BASE_DIR}/bin/mysqladmin'
MYSQLD_SAFE_BIN = f'{MYSQL_BASE_DIR}/bin/mysqld_safe'


# ==================== 工具函数 ====================

def get_mysql_connection():
    """
    获取MySQL数据库连接（统一使用TCP/IP连接）

    Note: 这个函数每次调用都会创建一个新的连接，使用后记得关闭连接

    Returns:
        pymysql.Connection: 数据库连接对象

    Raises:
        RuntimeError: 连接失败时抛出异常
    """
    try:
        import pymysql
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


def _is_port_open(host: str, port: int, timeout_seconds: float = 1.5) -> bool:
    """
    检查指定主机端口是否可连接

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


def wait_for_mysql(timeout_seconds: int = 60, interval_seconds: int = 2) -> bool:
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
        # 先检查端口是否开放
        if not _is_port_open(db_config['host'], db_config['port']):
            time.sleep(interval_seconds)
            continue

        # 端口开放后，尝试建立实际连接以验证MySQL可用
        try:
            conn = get_mysql_connection()
            with conn.cursor() as cursor:
                cursor.execute("SELECT 1")
            conn.close()
            print("✓ MySQL服务已就绪")
            return True
        except Exception as e:
            last_error = str(e)

        time.sleep(interval_seconds)

    # 超时处理
    if last_error:
        print(f"✗ 等待MySQL超时（{timeout_seconds}秒），最后错误: {last_error}")
    else:
        print(f"✗ 等待MySQL超时（{timeout_seconds}秒）")
    return False


def _build_mysqladmin_shutdown_command() -> str:
    """
    构建基于TCP/IP的mysqladmin关闭命令

    Returns:
        str: mysqladmin shutdown命令
    """
    base_cmd = (
        f"{MYSQLADMIN_BIN} -u {db_config['user']} "
        f"-h {db_config['host']} -P {db_config['port']}"
    )
    if db_config.get('password'):
        base_cmd += f" -p{db_config['password']}"
    return f"{base_cmd} shutdown"


def _wait_for_mysql_stop(timeout_seconds: int = 30, interval_seconds: int = 1) -> bool:
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
            print("✓ MySQL服务已完全停止")
            return True
        time.sleep(interval_seconds)

    print(f"✗ 等待MySQL停止超时（{timeout_seconds}秒），端口仍在监听")
    return False


def _restart_mysql() -> bool:
    """
    重启MySQL服务：
    1. 同步关闭旧实例
    2. 确认完全停止
    3. 启动新实例
    4. 等待服务可用

    Returns:
        bool: 重启成功且可用返回True，否则False
    """
    shutdown_cmd = _build_mysqladmin_shutdown_command()
    start_cmd = (
        f"{MYSQLD_SAFE_BIN} "
        f"--defaults-file={MYSQL_CONFIG_FILE} > /dev/null 2>&1 &"
    )

    # 步骤1：同步关闭MySQL
    print("正在关闭MySQL服务...")
    state = os.system(shutdown_cmd)
    if state != 0:
        print("  提示: mysqladmin关闭命令执行失败（MySQL可能已停止）")

    # 步骤2：等待MySQL完全停止
    if _is_port_open(db_config['host'], db_config['port']):
        print("等待MySQL完全停止...")
        _wait_for_mysql_stop(timeout_seconds=30)

    # 步骤3：额外等待，确保资源清理完毕
    time.sleep(3)

    # 步骤4：后台启动新MySQL实例
    print("正在启动MySQL服务...")
    os.system(start_cmd)

    # 步骤5：等待新MySQL实例就绪
    return wait_for_mysql()


def get_current_metric() -> Dict[str, int]:
    """
    获取当前InnoDB测试指标（使用TCP/IP连接）

    Returns:
        dict: 指标dict{指标名称: 指标值}，失败则返回空字典
    """
    if not wait_for_mysql():
        print("等待MySQL服务超时，无法获取指标")
        return {}

    try:
        conn = get_mysql_connection()
        cursor = conn.cursor()

        # 查询所有已启用的InnoDB指标
        sql = "SELECT name, count FROM information_schema.INNODB_METRICS WHERE status = 'enabled'"
        cursor.execute(sql)
        result = cursor.fetchall()

        inner_metrics = {name: int(count) for name, count in result}

        cursor.close()
        conn.close()

        return inner_metrics
    except Exception as e:
        print(f"获取InnoDB指标失败: {e}")
        return {}


def print_metrics(metrics: Dict[str, int]):
    """
    格式化打印性能指标

    Args:
        metrics: 指标字典{指标名称: 指标值}
    """
    if not metrics:
        print("\n⚠ 没有获取到性能指标")
        return

    print("\n" + "="*60)
    print("InnoDB内部指标")
    print("="*60)

    # 按指标名称排序
    sorted_metrics = sorted(metrics.items())

    # 分组显示指标
    print(f"\n共获取到 {len(sorted_metrics)} 个指标:\n")

    # 分列显示，每行显示2个指标
    for i in range(0, len(sorted_metrics), 2):
        left_name, left_value = sorted_metrics[i]
        if i + 1 < len(sorted_metrics):
            right_name, right_value = sorted_metrics[i + 1]
            print(f"  {left_name:40s} = {left_value:>15,}    {right_name:40s} = {right_value:>15,}")
        else:
            print(f"  {left_name:40s} = {left_value:>15,}")

    print("\n" + "="*60)


def apply_mysql_config(knob_config: Dict[str, Union[int, str]]) -> bool:
    """
    应用旋钮配置到my.cnf并重启MySQL

    Args:
        knob_config: 旋钮配置字典 {真实旋钮名称: 值（整数或字符串）}

    Returns:
        bool: 成功返回True，失败返回False
    """
    if not knob_config:
        print("⚠ 旋钮配置为空，跳过配置应用")
        return True

    print("\n" + "="*60)
    print("开始应用旋钮配置")
    print("="*60)

    # 检查备份文件是否存在
    if not os.path.exists(MYSQL_CONFIG_BACKUP):
        print(f"✗ 错误: 备份配置文件不存在: {MYSQL_CONFIG_BACKUP}")
        return False

    # 构建配置写入命令
    set_knobs_command = f"\\cp {MYSQL_CONFIG_BACKUP} {MYSQL_CONFIG_FILE}"
    for param_name, param_value in knob_config.items():
        set_knobs_command += f' && echo "{param_name}={param_value}" >> {MYSQL_CONFIG_FILE}'

    # 写入配置
    print(f"\n配置内容（共{len(knob_config)}个旋钮）:")
    for param_name, param_value in knob_config.items():
        value_type = "整数" if isinstance(param_value, int) else "枚举/字符串"
        print(f"  {param_name} = {param_value} ({value_type})")

    state = os.system(set_knobs_command)
    if state != 0:
        print("\n✗ 配置写入my.cnf失败，跳过重启")
        return False

    print("\n✓ 配置写入成功")

    # 重启MySQL
    print("\n尝试重启MySQL服务...")
    if _restart_mysql():
        print("\n✓ MySQL服务重启成功，配置已生效")
        return True

    # 重启失败，回滚配置
    print("\n✗ MySQL启动失败，尝试恢复到备份配置...")
    rollback_command = f'\\cp {MYSQL_CONFIG_BACKUP} {MYSQL_CONFIG_FILE}'
    os.system(rollback_command)

    if _restart_mysql():
        print("✓ 配置已恢复到备份状态，MySQL服务重启成功")
    else:
        print("✗ 严重错误: 配置已恢复但MySQL仍然无法重启")

    return False


def execute_sysbench() -> Optional[float]:
    """
    执行Sysbench基准测试

    Returns:
        float: 测试成功返回平均TPS，失败返回None
    """
    print("\n" + "="*60)
    print("开始Sysbench性能测试")
    print("="*60)

    # 预热延迟
    print("\n等待数据库预热（5秒）...")
    time.sleep(5)

    # 生成日志文件名
    log_file = f'./utils/test_by_sysbench_once/log/sysbench_{time.strftime("%Y_%m_%d_%H-%M-%S")}.log'
    print(f"\n日志文件: {log_file}")

    # 构建sysbench命令
    password_option = f'--mysql-password={db_config["password"]} ' if db_config.get('password') else ''
    command_run = (
        'sysbench --db-driver=mysql --threads=32 '
        '--mysql-host={host} --mysql-port={port} '
        '--mysql-user={user} {password_option}'
        '--mysql-db={database} '
        '--tables=50 --table-size=1000000 '
        '--time=120 --report-interval=5 ' # 测试120s，每5s输出一次中间结果，监控性能趋势
        '--warmup-time=90 ' # 预热时间90s，确保冷启动完成
        '--rand-seed=42 ' # 随机种子固定，确保每轮测试数据访问模式一致
        'oltp_read_write run'
    ).format(
        host=db_config['host'],
        port=db_config['port'],
        user=db_config['user'],
        password_option=password_option,
        database=db_config['database']
    )

    print("\n测试参数:")
    print(f"  数据库: {db_config['database']}")
    print(f"  连接: {db_config['host']}:{db_config['port']}")
    print(f"  线程数: 32")
    print(f"  表数量: 50")
    print(f"  表大小: 1,000,000行")
    print(f"  测试时长: 120秒")
    print(f"  报告间隔: 60秒")
    print(f"  测试类型: oltp_read_write")

    print("\n执行测试中...")
    print("-" * 60)

    # 执行sysbench测试
    try:
        with open(log_file, 'w') as log_fp:
            result = subprocess.run(
                command_run,
                shell=True,
                stdout=log_fp,
                stderr=log_fp,
                check=False
            )

        if result.returncode != 0:
            print(f"\n✗ sysbench执行失败，返回码: {result.returncode}")
            print(f"  请查看日志: {log_file}")
            return None

        print("-" * 60)
        print("✓ 测试执行完成")

    except Exception as e:
        print(f"\n✗ 执行sysbench失败: {e}")
        return None

    # 解析测试结果
    return parse_sysbench_result(log_file)


def parse_sysbench_result(log_file: str) -> Optional[float]:
    """
    解析Sysbench测试结果，提取TPS

    Args:
        log_file: 日志文件路径

    Returns:
        float: 平均TPS，失败返回None
    """
    try:
        with open(log_file, 'r') as f:
            content = f.read()

        # 方法1：从"transactions:"行提取TPS（最准确）
        trans_match = re.search(r'transactions:\s+\d+\s+\(([0-9.]+)\s+per sec', content, re.IGNORECASE)
        if trans_match:
            tps = float(trans_match.group(1))
            print("成功准确计算出TPS！")
            return tps

        # 方法2：从报告间隔行提取tps（多个值取平均）
        tps_lines = re.findall(r'tps:\s*([0-9.]+)', content, re.IGNORECASE)
        if tps_lines:
            recent_tps = [float(x) for x in tps_lines[-2:]]  # 取最后2个报告间隔
            avg_tps = sum(recent_tps) / len(recent_tps)
            print("基于报告间隔计算TPS")
            return avg_tps

        # 方法3：从qps计算tps（假设每个事务约20个查询）
        qps_lines = re.findall(r'qps:\s*([0-9.]+)', content, re.IGNORECASE)
        if qps_lines:
            recent_qps = [float(x) for x in qps_lines[-2:]]
            avg_qps = sum(recent_qps) / len(recent_qps)
            tps = avg_qps / 20.0
            print(f"基于平均QPS={avg_qps:.2f}估算TPS")
            return tps

        print(f"\n✗ 错误: 日志文件中未找到TPS/QPS数据")
        print(f"  日志文件: {log_file}")

        # 打印日志末尾便于调试
        lines = content.split('\n')
        print("\n日志末尾内容（最后20行）:")
        for line in lines[-20:]:
            if line.strip():
                print(f"  {line}")

        return None

    except Exception as e:
        print(f"\n✗ 解析日志失败: {e}")
        return None


def print_test_summary(knob_config: Dict[str, Union[int, str]], tps: Optional[float]):
    """
    打印测试结果摘要

    Args:
        knob_config: 旋钮配置
        tps: 测试得到的TPS值
    """
    print("\n" + "="*60)
    print("测试结果摘要")
    print("="*60)

    if not knob_config:
        print("\n旋钮配置: 使用默认数据库配置")

    print("\n性能指标:")
    if tps is not None:
        print(f"  TPS: {tps:.2f} 事务/秒")
        print(f"\n✓ 测试成功完成")
    else:
        print(f"  TPS: 测试失败")
        print(f"\n✗ 测试失败")

    print("="*60 + "\n")


def load_config_from_file(file_path: str) -> Dict[str, Union[int, str]]:
    """
    从JSON文件加载旋钮配置

    Args:
        file_path: JSON配置文件路径

    Returns:
        Dict[str, Union[int, str]]: 旋钮配置字典
    """
    try:
        with open(file_path, 'r') as f:
            config = json.load(f)

        if not isinstance(config, dict):
            raise ValueError("配置文件格式错误: 应为JSON对象")

        # 智能转换值：尝试转换为整数，失败则保留字符串
        converted_config = {}
        for key, value in config.items():
            # 如果值已经是整数，直接使用
            if isinstance(value, int):
                converted_config[key] = value
            # 如果是字符串，尝试转换为整数
            elif isinstance(value, str):
                # 尝试转换为整数
                if value.isdigit() or (value.startswith('-') and value[1:].isdigit()):
                    converted_config[key] = int(value)
                else:
                    # 无法转换为整数，保留字符串（枚举类型）
                    converted_config[key] = value
            # 如果是浮点数，转换为整数
            elif isinstance(value, float):
                converted_config[key] = int(value)
            else:
                print(f"⚠ 警告: 旋钮'{key}'的值类型'{type(value)}'不支持，跳过")

        return converted_config

    except FileNotFoundError:
        print(f"✗ 错误: 配置文件不存在: {file_path}")
        sys.exit(1)
    except json.JSONDecodeError as e:
        print(f"✗ 错误: 配置文件JSON格式错误: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"✗ 错误: 加载配置文件失败: {e}")
        sys.exit(1)


# ==================== 主函数 ====================

def main():
    """
    主函数：一次性Sysbench压测流程
    """
    # 解析命令行参数
    parser = argparse.ArgumentParser(
        description='一次性Sysbench压测脚本',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
使用示例:
  # 方式1: 通过命令行参数传递JSON配置
  python test_by_sysbench_once.py '{"innodb_buffer_pool_size": 1073741824}'

  # 方式2: 通过JSON文件传递配置
  python test_by_sysbench_once.py --config-file ./my_config.json

  # 方式3: 使用当前数据库配置
  python test_by_sysbench_once.py
        """
    )
    parser.add_argument(
        'config_json',
        nargs='?',
        default=None,
        help='JSON格式的旋钮配置字符串'
    )
    parser.add_argument(
        '--config-file',
        type=str,
        help='JSON配置文件路径'
    )

    args = parser.parse_args()

    # 打印欢迎信息
    print("\n" + "="*60)
    print("一次性Sysbench压测脚本")
    print("="*60)

    # 加载旋钮配置
    knob_config = {}

    if args.config_file:
        # 从文件加载
        print(f"\n配置来源: 文件 {args.config_file}")
        knob_config = load_config_from_file(args.config_file)
    elif args.config_json:
        # 从命令行参数加载
        print(f"\n配置来源: 命令行参数")
        try:
            knob_config = json.loads(args.config_json)
            if not isinstance(knob_config, dict):
                raise ValueError("配置格式错误: 应为JSON对象")

            # 智能转换值：尝试转换为整数，失败则保留字符串
            converted_config = {}
            for key, value in knob_config.items():
                # 如果值已经是整数，直接使用
                if isinstance(value, int):
                    converted_config[key] = value
                # 如果是字符串，尝试转换为整数
                elif isinstance(value, str):
                    # 尝试转换为整数
                    if value.isdigit() or (value.startswith('-') and value[1:].isdigit()):
                        converted_config[key] = int(value)
                    else:
                        # 无法转换为整数，保留字符串（枚举类型）
                        converted_config[key] = value
                # 如果是浮点数，转换为整数
                elif isinstance(value, float):
                    converted_config[key] = int(value)
                else:
                    print(f"⚠ 警告: 旋钮'{key}'的值类型'{type(value)}'不支持，跳过")
            knob_config = converted_config

        except json.JSONDecodeError as e:
            print(f"✗ 错误: JSON格式错误: {e}")
            sys.exit(1)
        except Exception as e:
            print(f"✗ 错误: 解析配置失败: {e}")
            sys.exit(1)
    else:
        # 使用当前数据库配置
        print(f"\n配置来源: 当前数据库配置")

    # 应用配置（如果提供了配置）
    if knob_config:
        if not apply_mysql_config(knob_config):
            print("\n✗ 配置应用失败，退出")
            sys.exit(1)
    else:
        # 确保MySQL正在运行
        print("\n检查MySQL服务状态...")
        if not wait_for_mysql():
            print("\n✗ MySQL服务不可用，退出")
            sys.exit(1)

    # 执行压测
    tps = execute_sysbench()

    # 查询并输出InnoDB内部指标
    print("\n正在查询InnoDB内部指标...")
    metrics = get_current_metric()
    if metrics:
        print("\n✓ 成功获取InnoDB内部指标：")
        print_metrics(metrics)
    else:
        print("⚠ 未能获取内部指标（可能InnoDB指标未启用）")

    # 打印测试摘要
    print_test_summary(knob_config, tps)

    # 返回状态码
    if tps is not None:
        sys.exit(0)
    else:
        sys.exit(1)


if __name__ == "__main__":
    main()
