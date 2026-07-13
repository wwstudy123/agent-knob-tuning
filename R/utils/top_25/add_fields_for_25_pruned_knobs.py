'''
从most_knobs.json中补全25_knobs_details_pruned.json中的字段type和desription，
输出到25_knobs_details_pruned_full.json
'''

input_file1 = "./utils/top_25/25_knobs_details_pruned.json"
input_file2 = "./utils/top_25/most_knobs.json"
output_file = "./utils/top_25/25_knobs_details_pruned_full.json"

import json
with open(input_file1, "r") as f:
    knobs_pruned = json.load(f)

with open(input_file2, "r") as f:
    knobs_most = json.load(f)

for knob_id, spec in knobs_pruned.items():
    if knob_id in knobs_most:
        spec_most = knobs_most[knob_id]
        # 补全type字段
        if "type" in spec_most:
            spec["type"] = spec_most["type"]
        # 补全description字段
        if "description" in spec_most:
            spec["description"] = spec_most["description"]

print(f"成功补全{len(knobs_pruned)}个knob的字段")
with open(output_file, "w") as f:
    json.dump(knobs_pruned, f, indent=4)
print(f"已保存到文件: {output_file}")