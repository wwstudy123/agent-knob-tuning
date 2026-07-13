'''
[已优化]将旋钮名称匿名
'''

import json
import configparser
def rename_knobs(input_file, output_file):
    with open(input_file, 'r') as f:
        original_data = json.load(f)

    new_data = {}
    for idx, (old_key, value) in enumerate(original_data.items(), start=1):
        new_key = f"knob{idx}"
        new_data[new_key] = value

    with open(output_file, 'w') as f:
        json.dump(new_data, f, indent=2, ensure_ascii=False)



config = configparser.ConfigParser()
config.read('./config.ini')
input_file=config['range pruner']['knob_details']
output_file="./range pruner/renamed_knobs"

# 将旋钮名称匿名并保存到文件（仅仅改了旋钮的名称）
rename_knobs(input_file, output_file)