'''
两个匿名化脚本的功能完全相同，都是将旋钮名称匿名
'''

import json
import configparser
def rename_knobs(input_file, output_file):
    # Read the original file
    with open(input_file, 'r') as f:
        original_data = json.load(f)

    # Generate new key names and retain the original values
    new_data = {}
    for idx, (old_key, value) in enumerate(original_data.items(), start=1):
        new_key = f"knob{idx}"
        new_data[new_key] = value

    # Write a new file
    with open(output_file, 'w') as f:
        json.dump(new_data, f, indent=2, ensure_ascii=False)



config = configparser.ConfigParser()
config.read('./config.ini')
input_file=config['knob selector']['candidate_knobs']
output_file="./knob selector/renamed_knobs"

# 将旋钮名称匿名并保存到文件（仅仅改了旋钮的名称）
rename_knobs(input_file, output_file)