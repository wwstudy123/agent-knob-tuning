"""
基于Flask框架搭建的HTTP服务端：
1. 接收来自客户端(DB_client.py)的数据库性能反馈
2. 调用LLM生成新的旋钮配置
3. 返回推荐的配置列表给客户端
"""

from flask import Flask, request, jsonify
from openai import OpenAI
import json
import re
import os
import sys
import heapq
from config_rank import sort_list
import configparser
from pydantic import create_model

def get_tuning_response_model(knob_names):
    """
    动态生成Pydantic模型，用于约束LLM输出json旋钮配置
    Args:
        knob_names(list): 需要调优的重要旋钮名称列表
    Returns:
         名为"TuningResponse"的pydantic.BaseModel
    """
    # NOTE：数据库旋钮通常为int类型，MySQL这里处理为int类型够用
    fields = {name: (int, ...) for name in knob_names}
    return create_model("TuningResponse", **fields)

config = configparser.ConfigParser()
config.read('./config.ini')

# 读取Prompt填充内容
file_path = config['workload analyzer']['output_file']
with open(file_path, "r") as f:
    workload_features = f.read().strip()
database_kernel=config['knob selector']['database_kernel']
hardware=config['knob selector']['hardware']
database_scale=config['knob selector']['database_scale']

# 经过范围剪枝的重要旋钮及其字段
knob_list_path = config['range pruner']['output_file']
with open(knob_list_path,"r") as f:
    knobs_spec = json.load(f) # dict: {knob_name: {min_value, max_value, step, type, description, special_value}}
knob_names = list(knobs_spec.keys())
knobs_json_str = json.dumps(knobs_spec, indent=2, ensure_ascii=False) # json string用于Prompt填充

# 加载数据库性能指标的详细描述
# NOTE:这个实际上没用到
metric_path = config['configuration recommender']['metric_file']
with open(metric_path, "r") as f:
    inner_metrics = f.read().strip()

db_metric = config['configuration recommender']['db_metric']
history_num = int(config['configuration recommender']['history_num']) # Memory Window
node_count = int(config['configuration recommender']['node_count']) # R
llm_server_port = int(config['configuration recommender']['LLM_server_port'])


def _clamp_int(value, min_value, max_value, step_value):
    """
    对整数值进行步长对齐（四舍五入）
    Args:
        value: 待对齐的值
        min_value: 最小值
        max_value: 最大值
        step_value: 步长
    Returns:
        int: 步长对齐后的值
    """
    if step_value and step_value > 0:
        steps = round((value - min_value) / step_value)
        aligned_value = min_value + int(steps * step_value)
        # 嵌套约束：确保在 [min, max] 范围内
        return max(min_value, min(max_value, aligned_value))
    return max(min_value, min(max_value, value))


def validate_knob_config(config_dict, knobs_spec):
    """
    白盒验证：验证LLM生成的旋钮配置
    - 超出范围的配置直接拒绝
    - 步长不对齐的配置自动修正
    Args:
        config_dict(dict): LLM返回的旋钮配置 dict{knob_name: value}
        knobs_spec(dict): 旋钮规范 dict{knob_name: {多个字段}}
    Returns:
        tuple: (is_valid: bool, corrected_config: dict, error_messages: list)
    """
    error_messages = []  # 严重错误，导致配置被拒绝
    corrected_config = {}  # 修正后的配置（仅步长对齐）
    
    # 1. 检查是否包含所有必需的旋钮
    for knob_name in knobs_spec.keys():
        if knob_name not in config_dict:
            error_messages.append(f"缺少必需旋钮: {knob_name}")
    
    # 2. 检查每个旋钮的值
    for knob_name, value in config_dict.items():
        # 检查旋钮是否在规范中
        if knob_name not in knobs_spec:
            error_messages.append(f"未知的旋钮: {knob_name}")
            continue
        
        spec = knobs_spec[knob_name]
        min_val = spec['min_value']
        max_val = spec['max_value']
        step = spec['step']
        special_val = spec.get('special_value')
        
        # 转换为整数（MySQL旋钮通常为整数类型）
        try:
            value_int = int(value)
        except (ValueError, TypeError):
            error_messages.append(f"{knob_name}: 值'{value}'不是有效的整数")
            continue
        
        # 如果有特殊值且等于特殊值，则跳过范围和步长检查
        if special_val is not None and value_int == special_val:
            corrected_config[knob_name] = value_int
            continue
        
        # 严格检查：值必须在允许范围内[min_val, max_val]，超出直接拒绝
        if value_int < min_val or value_int > max_val:
            error_messages.append(
                f"{knob_name}: 值{value_int}超出允许范围[{min_val}, {max_val}]"
            )
            continue
        
        # 步长对齐
        corrected_value = _clamp_int(value_int, min_val, max_val, step)
        corrected_config[knob_name] = corrected_value
    
    is_valid = len(error_messages)==0
    return is_valid, corrected_config, error_messages


def call_llm3(model, messages, filename):
    """
    调用LLM生成旋钮配置，并写入结果文件
    Args:
        model: LLM名称
        messages: 对话消息列表
        filename: 输出文件路径
    Returns:
        bool: 成功返回True，失败返回False
    """
    try:
        client = OpenAI(
            api_key=config['knob selector']['api_key'], 
            base_url=config['knob selector']['base_url']
        )
        ResponseModel = get_tuning_response_model(knob_names)
        completion = client.beta.chat.completions.parse(
            model=model,
            messages=messages,
            temperature=1,
            top_p=0.98,
            response_format=ResponseModel
        )

        # 为了兼容旧的LLM，采用json.loads解析content字段
        if completion.choices and completion.choices[0]:
            response = completion.choices[0].message.content # 格式化的json string
            config_dict = json.loads(response) if response else {} # dict

            if not config_dict:
                print("错误: LLM返回的旋钮配置不是有效的JSON格式")
                return False
            
            # 基于规则的白盒验证
            # 白盒验证：检查旋钮配置（移除非法和不安全的候选配置，自动修正步长对齐）
            is_valid, corrected_config, error_messages = validate_knob_config(config_dict, knobs_spec)
            
            # 如果存在错误，拒绝配置
            if not is_valid:
                print("\n白盒验证失败:")
                for error_msg in error_messages:
                    print(f"  [✗]{error_msg}")
                print(f"拒绝的配置: {json.dumps(config_dict, ensure_ascii=False)}")
                return False
            
            print("\n白盒验证通过，尝试写入配置文件")
            # 使用修正后的配置进行去重和写入
            # 序列化为JSON String，没有格式化
            corrected_json_str = json.dumps(corrected_config, ensure_ascii=False)
            
            # 去重：避免重复写入相同配置
            try:
                with open(filename, 'r') as f:
                    existing_content = f.read()
                existing_configs = [s for s in existing_content.strip().split('\n') if s.strip()]
            except FileNotFoundError:
                existing_configs = []
            if corrected_json_str in existing_configs:
                print(f"配置已存在，跳过此配置的写入")
                return False
            
            # 将修正后的配置追加写入文件
            with open(filename, 'a') as f:
                if existing_configs:
                    f.write('\n')
                f.write(corrected_json_str) # 没有格式化，每行一个JSON字符串
            
            print(f"成功生成并写入配置: {corrected_config}")
            return True
        else:
            print("错误: LLM未返回有效的content")
            return False
            
    except Exception as e:
        print(f"调用LLM失败: {e}")
        return False

request_count = 0 # 请求计数器
history_top = [] # 优先队列的容器，存储历史最优配置
heap_seq = 0 # 堆排序辅助序列号

# 创建Web服务应用
app = Flask(__name__)

# 路由装饰器@app.route(...)
# 接口定义：/process表示访问路径;POST方法表示该路由只接受POST请求
# 当客户端发来POST请求时，会自动调用process_data()函数
# NOTE：每次请求，函数内的局部变量都会重新初始化，请求结束即销毁
@app.route('/process', methods=['POST'])
def process_data():
    """
    Flask路由核心处理函数
    处理DB_client返回的数据并返回推荐旋钮配置
    """
    global request_count, history_top, heap_seq
    request_count += 1

    # 为每一轮次创建空文件，用于存储当前请求的结果
    filename = f'./configuration recommender/record/turn_{request_count}'
    file = open(filename, 'w')
    file.close()

    # 接收客户端发来的上一轮k套旋钮配置的测试反馈（初始时只有1套配置）
    data = request.get_json() # list of dict，反序列化
    if not isinstance(data, list):
        return jsonify({"error": "Invalid JSON payload, expected a list."}), 400
        # jsonify用于将Python数据结构转换为JSON格式的HTTP响应
    
    # 遍历每一套旋钮配置，基于该配置节点调用LLM生成R个新配置节点
    k = len(data)
    # item:dict, item.keys():"knob","throughput","metric"
    for index, item in enumerate(data, 1): # len(data)=1 or k
        # 提取本套旋钮配置及其测试结果反馈
        current_knobs = item.get('knob', {})
        current_inner_metrics = item.get('metric', {})
        current_throughput = item.get('throughput', 0)
        
        # 格式化为JSON String
        current_knobs_json = json.dumps(current_knobs, indent=2, ensure_ascii=False)
        current_inner_metrics_json = json.dumps(current_inner_metrics, indent=2, ensure_ascii=False)

        print("\n"+'='*60)
        print(f"[请求{request_count}] 配置{index}/{k}")
        print(f"当前旋钮配置:\n{current_knobs_json}")
        print(f"当前吞吐量: {current_throughput}")
        print(f"当前内部指标数量: {len(current_inner_metrics)}")

        try:
            throughput_value = float(current_throughput)
        except (TypeError, ValueError):
            throughput_value = 0.0

        # 基于每一套旋钮配置，尝试更新Top-N历史最优（存储全局吞吐量最大的N个配置）
        heap_seq += 1
        # 三元组(吞吐量，堆序号，dict{"knob","throughput","metric"})
        # NOTE：每一个三元组都是一套配置及其性能表现
        entry = (throughput_value, heap_seq, item)
        if len(history_top) < history_num: # 堆未满，直接入堆
            heapq.heappush(history_top, entry)
        else:
            heapq.heappushpop(history_top, entry) # 即push+pop

        throughput_text = str(current_throughput)
        if throughput_value == 0.0:
            throughput_text = "0, because **errors encountered** during the process, such as failed knob configuration application or workload testing failures."
        
        # 将堆中存储的三元组，按照吞吐量降序排序
        sorted_history = sorted(history_top, key=lambda x: -x[0])
        # 构建最新的历史最优Prompt
        history_entries = []
        for idx, (throughput, _, history_item) in enumerate(sorted_history, 1):
            knob_json = json.dumps(history_item['knob'], indent=2, ensure_ascii=False)
            metric_json = json.dumps(history_item['metric'], indent=2, ensure_ascii=False)
            history_entries.append(f"""No.{idx}:
- Knob Configuration:
{knob_json}
- Throughput: {throughput}
- Metrics:
{metric_json}""")
        
        history_entries_text = "\n\n".join(history_entries)
        messages = [
        {
            "role": "system",
            "content": """You are an experienced database administrators, skilled in database knob tuning."""
        },
        {
            "role": "user",
            "content": f"""## Task Overview
Your goal is to recommend a database configuration that maximizes **{db_metric}**. Based on the Workload, Hardware, and History provided below, determine the optimal value for each knob.
**VERY IMPORTANT**: Ensure to provide a set of knob configurations **different** from historical configuration records to expedite the exploration of the knob configuration space, thereby finding the globally optimal configuration!

For each knob, you MUST follow these principles when selecting values:
1. **Range Compliance**: Strictly ensure the value is within `[min_value, max_value]`.
2. **Granularity Adherence**: Use `step` as the search granularity. Select values that align with this step (e.g., multiples of `step` or semantically aligned units) to avoid insignificant micro-adjustments.
3. **Semantic Validity**: Base your decision on the knob's `description` and actual impact.

## Knobs List
> **VERY IMPORTANT**: Ensure that the knob values are strictly **within the [min_value, max_value] range**!

{knobs_json_str}


## Workload and Database Information

### 1. Environment & Database Configuration
- **Database Kernel**: {database_kernel}
- **Database Scale**: {database_scale}
- **Hardware**: {hardware}

### 2. Workload Features Details
{workload_features}


## Historical Optimal Examples of Knob Tuning
{history_entries_text}

## Current Knob Configuration
{current_knobs_json}

## Performance Feedback After Executing Current Configuration
- **Performance ({db_metric})** : {throughput_text}
- **Internal Metrics**:
{current_inner_metrics_json}

## Output Format Requirements
Output strictly in the following format as **standard JSON**, ensuring it **contains only** knob names and corresponding values. Do not include comments, explanations, or additional fields:

{{
    "knob1": value1,
    "knob2": value2,
    ...
}}

**VERY IMPORTANT**: Ensure that the knob values provided are strictly **within the [min_value, max_value] range**!
Now, let's think step by step. Give your JSON response:"""
        }
        ]
        # 并行调用LLM
        # 将当前节点扩展生成R个候选节点，并把候选配置写入到当前轮次文件turn_{request_count}        
        model = config['configuration recommender']['model']
        success_count = 0
        for _ in range(node_count):
            if call_llm3(model, messages, filename):
                success_count += 1
        # 每次调用LLM，生成R个新配置节点，并追加写入文件turn_{request_count}
        # 最终文件turn_{request_count}中会有k*R个新配置节点
        
        print()
        print(f"\n配置{index}完成，成功生成{success_count}/{node_count}个新配置")
            
    # 读取当前轮次生成的所有配置
    try:
        with open(filename, 'r') as f:
            content = f.read() # str
        json_strings_list = [s for s in content.strip().split('\n') if s.strip()]
        # 当前轮次中所有生成的配置：[config1_json_str, config2_json_str...]
    except FileNotFoundError:
        json_strings_list = []
    
    print(f"[请求{request_count}] 本轮共生成{len(json_strings_list)}个配置")
    if not json_strings_list:
        print("错误: 本轮未生成任何有效配置，返回空列表top-k=[]")
        top_k_configs = []
    else:
        # 基于质心的排序：从本轮生成的所有配置中选出Top-K
        top_k_configs = sort_list(json_strings_list) # list of dict
        print(f"经过排序，选出Top-{len(top_k_configs)}配置返回给客户端")
    
    # 记录本轮推荐的Top-K配置，追加写入日志
    with open('./configuration recommender/record/top_k', 'a') as f:
        f.write(f"\nRequest {request_count}\n")
        json.dump(top_k_configs, f, indent=2, ensure_ascii=False)
        f.write('\n')
    
    print("="*60 + "\n")
    return jsonify(top_k_configs) # 序列化为json，向客户端返回新生成的Top-K配置

# 启动Flask服务器
if __name__ == '__main__':
    app.run(host='0.0.0.0', port=llm_server_port, use_reloader=False)