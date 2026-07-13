"""
利用LLM，缩减筛选出的重要旋钮的取值范围
"""

from openai import OpenAI
from pydantic import BaseModel, create_model
from typing import Optional, Dict
import configparser
import re
import json

class KnobInterval(BaseModel):
    min_value: int
    max_value: int
    step: int
    special_value: Optional[int] = None # int or None

# knob_list是旋钮名称列表["knob1", "knob2"...]
def get_response_model(knob_list):
    '''
    动态生成Pydantic模型，用于约束LLM输出json格式
    Returns:
         名为"KnobResponse"的pydantic.BaseModel
    '''
    fields = {name: (KnobInterval, ...) for name in knob_list}
    return create_model("KnobResponse", **fields)
# 类似定义数据模型：
# class KnobResponse(BaseModel):
#     knob1: KnobInterval  # ...表示必填字段
#     knob2: KnobInterval

config = configparser.ConfigParser()
config.read('./config.ini')

# 读取匿名旋钮及其详细描述knob_details
knob_details_path = "./range pruner/renamed_knobs"
with open(knob_details_path, 'r') as f:
    knob_details = json.load(f) # dict: {knob_name: {type, min, max, description}}

# 读取筛选出的旋钮knob_names
knob_list_path = config['knob selector']['output_file']
with open(knob_list_path, "r") as f:
    knob_names = json.load(f) # list:['knob94', 'knob12',...]
n = len(knob_names)
# NOTE：这里只处理整数类型的旋钮，过滤掉enum类型的旋钮
knob_names = [name for name in knob_names if (name in knob_details and knob_details[name].get('type') == 'integer')]
print(f"在{n}个筛选出的重要旋钮中，进一步过滤enum类型的旋钮，\n剩余整数类型旋钮数量为: {len(knob_names)}")

# 组装筛选出的匿名旋钮的详细信息
knob_details_selected = {name: knob_details[name] for name in knob_names}
# 格式化为JSON String
knobs = json.dumps(knob_details_selected, indent=2, ensure_ascii=False) # json string

# Prompt填充
workload_features_path = config['workload analyzer']['output_file']
with open(workload_features_path, "r") as f:
    workload_features = f.read().strip()  
database_kernel=config['knob selector']['database_kernel']
hardware=config['knob selector']['hardware']
database_scale=config['knob selector']['database_scale']


def parse_and_verify(json_string):
    """
    从LLM返回的JSON字符串中解析旋钮范围配置，并进行基于规则的白盒验证
    Args:
        json_string(str): LLM返回的JSON String
    Returns:
        dict: 包含旋钮范围配置的字典
        格式为 {knob_id: {min_value, max_value, step, type, description, special_value}}
    """
    knobs_result = {}
    validation_errors = [] # 记录错误旋钮的信息，并跳过此旋钮

    # 解析JSON对象
    try:
        data = json.loads(json_string) # dict
    except json.JSONDecodeError as e:
        print(f"JSON解析失败: {e}")
        return {}
    
    if not isinstance(data, dict):
        print("错误：LLM返回的json string无法解析为dict")
        return {}
    
    # 遍历每个旋钮，进行校验
    for knob_id, spec in data.items(): # data:dict{knob_id:dict}
        # 检查旋钮ID是否有效
        if knob_id not in knob_details:
            validation_errors.append({
                "parameter": knob_id,
                "errors": [f"未知的旋钮ID: {knob_id}"]
            })
            continue
        
        # 检查数据格式
        if not isinstance(spec, dict):
            validation_errors.append({
                "parameter": knob_id,
                "errors": ["旋钮配置不是字典格式"]
            })
            continue
        
        # 提取字段
        min_val = spec.get("min_value")
        max_val = spec.get("max_value")
        step_val = spec.get("step")
        special_val = spec.get("special_value")
        
        # 检查必填字段
        if min_val is None or max_val is None or step_val is None:
            validation_errors.append({
                "parameter": knob_id,
                "errors": ["缺少必填字段 (min_value, max_value, step)"]
            })
            continue
        
        # 转换为整数
        try:
            min_val = int(min_val)
            max_val = int(max_val)
            step_val = int(step_val)
            if special_val is not None:
                special_val = int(special_val)
        except (ValueError, TypeError) as e:
            validation_errors.append({
                "parameter": knob_id,
                "errors": [f"数值转换失败: {e}"]
            })
            continue

        # 基于规则的白盒验证
        error_messages = [] # 记录旋钮白盒验证下的错误信息
        config_min = knob_details[knob_id]["min"]
        config_max = knob_details[knob_id]["max"]
        
        # 验证min_value是否在允许范围内
        if not (config_min <= min_val <= config_max):
            error_messages.append(f"min_value={min_val} 超出允许范围 [{config_min}, {config_max}]")
        
        # 验证max_value是否在允许范围内
        if not (config_min <= max_val <= config_max):
            error_messages.append(f"max_value={max_val} 超出允许范围 [{config_min}, {config_max}]")
        
        # 验证min_value <= max_value
        if min_val > max_val:
            error_messages.append(f"min_value={min_val} 大于 max_value={max_val}")
        
        # 验证step是否合理
        if step_val <= 0:
            error_messages.append(f"step={step_val} 必须大于0")
        elif (max_val - min_val) > 0 and step_val > (max_val - min_val):
            error_messages.append(f"step={step_val} 大于范围跨度 {max_val - min_val}")
        
        # 如果有验证错误，记录并跳过该旋钮
        if error_messages:
            validation_errors.append({
                "parameter": knob_id,
                "errors": error_messages,
                "received": {
                    "min_value": min_val,
                    "max_value": max_val,
                    "step": step_val,
                    "allowed_range": [config_min, config_max]
                }
            })
            continue
        
        # 验证通过，存储旋钮配置
        knobs_result[knob_id] = {
            "min_value": min_val,
            "max_value": max_val,
            "step": step_val,
            "type": knob_details[knob_id]["type"],
            "description": knob_details[knob_id]["description"]
        }
        
        # 如果有特殊值，也存储
        if special_val is not None:
            knobs_result[knob_id]["special_value"] = special_val
    
    # 打印验证错误信息
    if validation_errors:
        print("\n" + "-"*60)
        print("旋钮验证错误信息：")
        for error in validation_errors:
            print(f"\n旋钮: {error['parameter']}")
            print("错误:")
            for err_msg in error['errors']:
                print(f"[✗]{err_msg}")
            if 'received' in error:
                print(f"接收到的值: {json.dumps(error['received'], indent=2, ensure_ascii=False)}")
    
    return knobs_result

def call_llm2(model,knobs):
    '''
    调用LLM，缩减重要旋钮的取值范围，并进行校验，最后将结果写入文件
    Returns:
        None，结果写入文件
    '''
    client = OpenAI(
        api_key=config['knob selector']['api_key'], 
        base_url=config['knob selector']['base_url']
    )

    messages = [
    {"role": "system", "content": "You are an experienced database administrators, skilled in database knob tuning."},
    {
        "role": "user",
        "content": f"""## Task Overview
The provided knob ranges are too broad for efficient tuning. Your task is to generate an optimized configuration by analyzing each knob's information:
1. **Range Pruning**: Shrink the original [min, max] to a **narrower**, high-potential interval [min_value, max_value] that aligns with the workload and hardware constraints.
2. **Step Definition**: Assign a reasonable and efficient search step size (`step`) based on the knob's semantics (e.g., use power-of-2 or memory block sizes for buffers, and unit increments for counts).
3. **Special Value**: Carefully determine for each knob whether any special values exist (e.g., 0 or -1 indicating "disabled" or "auto"); if no special value exists, set `special_value` to null.

## Knobs
{knobs}


## Workload and Database Information

### 1. Environment & Database Configuration
- **Database Kernel**: {database_kernel}
- **Database Scale**: {database_scale}
- **Hardware**: {hardware}

### 2. Workload Features Details
{workload_features}


## Output Format Requirements
Output strictly in the following format as **standard JSON**, ensuring it **contains only** knob names and corresponding values. Do not include comments, explanations, or additional fields:

{{
    "knob1":
    {{
        "min_value": MIN_VALUE,
        "max_value": MAX_VALUE,
        "step": STEP_SIZE,
        "special_value": SPECIAL_VALUE
    }},
    "knob2":
    {{
        ...
    }}
    ...
}}

Now let us think step by step. Give your json response:"""
    }
    ]
    ResponseModel = get_response_model(knob_names)
    completion = client.beta.chat.completions.parse(
        model=model,
        messages=messages, # Prompt
        temperature = 0,
        response_format=ResponseModel
        # NOTE：对于GPT4，不支持structed output，只能用JSON model，并且其输出可能需要额外的解析
        # client.chat.completions.create(...) + response_format={"type": "json_object"}
    )

    for choice in completion.choices: # 其实只有一个回复
        print("\n" + "-"*60)
        print("[range pruner]LLM返回的原始内容:")
        # content字段永远是字符串类型
        response = choice.message.content # 格式化的json string
        print(response)
        
        try:
            # 解析并验证旋钮配置
            result = parse_and_verify(response) # dict
            
            # 写入文件
            output = config['range pruner']['output_file']
            with open(output, "w", encoding='utf-8') as f:
                json.dump(result, f, indent=2, ensure_ascii=False)
            
            print(f"\n成功处理 {len(result)} 个旋钮配置，已保存到: {output}")
            
        except Exception as e:
            print(f"\n处理response时发生错误: {e}")
            import traceback
            traceback.print_exc()


if __name__ == '__main__':
    model = config['range pruner']['model']
    call_llm2(model,knobs)