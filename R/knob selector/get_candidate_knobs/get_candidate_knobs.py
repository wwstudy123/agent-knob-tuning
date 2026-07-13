'''
xml_output.json解析自xml文件; mysql_197.json的来源未知

本脚本作用：
将两个json文件合并，生成包含详细描述的候选参数文件candidate_knobs_merged.json
'''

import json

with open('./knob selector/get_candidate_knobs/xml_output.json', 'r', encoding='utf-8') as f:
    data = json.load(f)

with open('./knob selector/get_candidate_knobs/mysql_197.json', 'r', encoding='utf-8') as f:
    config = json.load(f)

# Create a new dictionary to store the merged result
merged_data = {}

print("以下参数在xml_output.json中存在，但在mysql_197.json中不存在：")
# Traverse the data in the first JSON file
for item in data:
    name = item.get('name')
    if name:
        # 如果参数存在于mysql_197, 合并信息
        if name in config:
            if config[name].get("type") == "enum": 
                merged_data[name] = {
                "default": config[name].get("default"),
                "dynamic": config[name].get("dynamic"),
                "enum_values": config[name].get("enum_values"),
                "scope": config[name].get("scope"),
                "type": config[name].get("type"),
                "td": item.get('td'),
                "p": item.get('decription'),
                "rank": config[name].get("important_rank") # rank字段就是mysql_197.json中的important_rank
            }
            else:
                merged_data[name] = {
                    "default": config[name].get("default"),
                    "dynamic": config[name].get("dynamic"),
                    "max": config[name].get("max"),
                    "min": config[name].get("min"),
                    "scope": config[name].get("scope"),
                    "type": config[name].get("type"),
                    "td": item.get('td'),
                    "p": item.get('decription'),
                    "rank": config[name].get("important_rank")
                }
        else:
            print(f"  - {name}")
# NOTE：xml_output.json中有155个旋钮
# 其中83个旋钮已经存在于mysql_197.json，2个为无效噪声，69个旋钮没有在mysql_197.json中
# 但之后的candidate_knobs等文件中的旋钮，均来自mysql_197.json

sorted_merged_data = {k: v for k, v in sorted(merged_data.items(), key=lambda item: (item[1].get("rank") is None, item[1].get("rank")))}
print(f"总共生成 {len(sorted_merged_data)} 个包含详细描述的候选参数")
with open('./knob selector/get_candidate_knobs/candidate_knobs_merged.json', 'w', encoding='utf-8') as f:
    json.dump(sorted_merged_data, f, ensure_ascii=False, indent=2)
# 包含的字段：
# default
# dynamic
# scope
# type
# td
# p
# rank
# max
# min
# enum_values
