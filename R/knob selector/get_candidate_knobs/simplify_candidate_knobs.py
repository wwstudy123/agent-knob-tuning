'''
用于从合并之后文件candidate_knobs_merged.json的详细描述中提取候选参数的简化描述
生成新的简化文件simplify_candidate_knobs_merged.json
'''
import json
import re

def extract_first_sentence(text):
    match = re.match(r'([^.]*[.])', text)
    if match:
        return match.group(1)
    else:
        return text 

with open('./knob selector/get_candidate_knobs/candidate_knobs_merged.json', 'r', encoding='utf-8') as f:
    data = json.load(f)



clean_data = {}

for name in data.keys():
    if data[name].get("type") == "enum": 
        clean_data[name] = {
        "enum_values": data[name].get("enum_values"),
        "type": data[name].get("type"),
        "description":extract_first_sentence(data[name].get('p')),
    }
    else:
        clean_data[name] = {
            "max": data[name].get("max"),
            "min": data[name].get("min"),
            "type": data[name].get("type"),
            "description": extract_first_sentence(data[name].get('p')),
        }

with open('./knob selector/get_candidate_knobs/simplify_candidate_knobs_merged.json', 'w', encoding='utf-8') as f:
    json.dump(clean_data, f, ensure_ascii=False, indent=2)
# 得到文件：simplify_candidate_knobs_merged.json
# 包含的字段：
# enum_values (仅enum类型)
# type
# description
# max (非enum类型)
# min (非enum类型)
