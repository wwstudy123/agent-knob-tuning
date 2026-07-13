"""
旋钮配置文件一致性检查脚本

1. 检查knob_details.json和candidate_knobs中数值范围不一致的旋钮
2. 检查mysql_197.json和knob_details.json中旋钮集合的差异

输出结果到控制台
"""

import json
from pathlib import Path
from typing import Dict, Set, Tuple


def load_json_file(file_path: str) -> Dict:
  """
  加载JSON文件并返回字典对象

  Args:
    file_path: 文件路径

  Returns:
    返回解析后的字典对象

  Raises:
    FileNotFoundError: 文件不存在时
    json.JSONDecodeError: JSON格式错误时
  """
  try:
    with open(file_path, 'r', encoding='utf-8') as f:
      return json.load(f)
  except FileNotFoundError:
    print(f"❌ 错误: 文件不存在 - {file_path}")
    raise
  except json.JSONDecodeError as e:
    print(f"❌ 错误: JSON解析失败 - {file_path}")
    print(f"   详情: {str(e)}")
    raise


def main() -> None:
  """
  主函数：执行所有一致性检查任务
  """
  # 任务1: 范围一致性检查
  print("=" * 80)
  print("任务1: 检查knob_details.json和candidate_knobs中的数值范围一致性")
  print("=" * 80)

  knob_details = load_json_file(
    "./range pruner/knob_details.json"
  )
  candidate_knobs = load_json_file(
    "./knob selector/candidate_knobs"
  )

  print(f"\n✓ knob_details.json 包含 {len(knob_details)} 个旋钮")
  print(f"✓ candidate_knobs 包含 {len(candidate_knobs)} 个旋钮")

  # 找出范围不一致的旋钮
  inconsistent_knobs = []

  for knob_name in knob_details:
    if knob_name not in candidate_knobs:
      print("knob_details.json中存在，但candidate_knobs中不存在的旋钮: " + knob_name)
      continue

    knob_detail = knob_details[knob_name]
    candidate_knob = candidate_knobs[knob_name]

    # 比较max和min字段（仅当两个文件都有这些字段时）
    has_range_detail = "max" in knob_detail and "min" in knob_detail
    has_range_candidate = "max" in candidate_knob and "min" in candidate_knob

    if has_range_detail and has_range_candidate:
      detail_max = knob_detail["max"]
      detail_min = knob_detail["min"]
      candidate_max = candidate_knob["max"]
      candidate_min = candidate_knob["min"]

      if detail_max != candidate_max or detail_min != candidate_min:
        inconsistent_knobs.append({
          "knob_name": knob_name,
          "knob_details_max": detail_max,
          "knob_details_min": detail_min,
          "candidate_knobs_max": candidate_max,
          "candidate_knobs_min": candidate_min,
        })

  if inconsistent_knobs:
    print(f"\n❌ 发现 {len(inconsistent_knobs)} 个范围不一致的旋钮:\n")
    for knob in inconsistent_knobs:
      print(f"  旋钮名称: {knob['knob_name']}")
      print(f"    - knob_details.json: max={knob['knob_details_max']}, "
            f"min={knob['knob_details_min']}")
      print(f"    - candidate_knobs: max={knob['candidate_knobs_max']}, "
            f"min={knob['candidate_knobs_min']}")
      print()
  else:
    print("\n✓ 所有旋钮的数值范围都一致!")

  # 任务2: 旋钮集合差异检查
  print("\n" + "=" * 80)
  print("任务2: 检查mysql_197.json和knob_details.json中的旋钮集合差异")
  print("=" * 80)

  mysql_197 = load_json_file(
    "./knob selector/get_candidate_knobs/mysql_197.json"
  )

  print(f"\n✓ mysql_197.json 包含 {len(mysql_197)} 个旋钮")
  print(f"✓ knob_details.json 包含 {len(knob_details)} 个旋钮")

  # 获取旋钮名称集合
  mysql_197_knobs: Set[str] = set(mysql_197.keys())
  knob_details_knobs: Set[str] = set(knob_details.keys())

  # 集合运算，找出差异
  only_in_mysql_197 = mysql_197_knobs - knob_details_knobs
  only_in_knob_details = knob_details_knobs - mysql_197_knobs

  if only_in_mysql_197:
    print(f"\n❌ 只在mysql_197.json中存在的旋钮 ({len(only_in_mysql_197)} 个):")
    for knob in sorted(only_in_mysql_197):
      print(f"  - {knob}")
  else:
    print("\n✓ mysql_197.json中没有knob_details.json中不存在的旋钮")

  if only_in_knob_details:
    print(f"\n❌ 只在knob_details.json中存在的旋钮 ({len(only_in_knob_details)} 个):")
    for knob in sorted(only_in_knob_details):
      print(f"  - {knob}")
  else:
    print("\n✓ knob_details.json中没有mysql_197.json中不存在的旋钮")

  print("\n" + "=" * 80)
  print("一致性检查完成")
  print("=" * 80)


if __name__ == "__main__":
  main()
