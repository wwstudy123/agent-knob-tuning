"""
候选旋钮配置的排序算法

基于质心的配置排序算法，从候选配置中选择Top-K最优配置。
核心思想：
- 假设接近候选集中心的配置代表主导投票结果，确保候选配置之间的自洽性
- 通过多维度排名汇总，引导后续迭代朝着有希望的方向优化
"""

import json
import unittest
import configparser
import re

config_parser = configparser.ConfigParser()
config_parser.read('./config.ini')

# 读取范围剪枝后的重要旋钮及其字段
file_path = config_parser['range pruner']['output_file']
with open(file_path, "r") as f:
    pruned_knobs_dict = json.load(f)


def sort_list(json_strings_list):
    """
    基于质心的排序算法：对候选配置进行排序并返回Top-K
    步骤：
    1. 对每个旋钮，计算所有候选配置中该旋钮的平均值（质心）
    2. 对每个旋钮，根据值与质心的距离进行排名
    3. 对每个配置，汇总其在所有旋钮上的排名得到总分
    4. 按总分升序排序，选择Top-K配置（总分越小越好，表示越接近质心）
    Args:
        json_strings_list (list[str]): 某轮次中所有生成的配置列表[config1_json_str, config2_json_str...]
    Returns:
        list[dict]: 排序后的Top-K配置列表，每个元素是dict旋钮配置
    """
    # JSON String -> dict
    parsed_configs = []
    for json_str in json_strings_list:
        if not json_str or not json_str.strip():
            continue
        try:
            config_dict = json.loads(json_str) # dict
            parsed_configs.append(config_dict)
        except json.JSONDecodeError as e:
            print(f"错误：跳过无效的JSON配置: {e}")
            continue
    if not parsed_configs:
        return []
    
    # 配置完整性校验与补全
    # NOTE: 理论上LLM输出应包含所有旋钮。这里做保险处理：缺失旋钮用min_value补全
    knob_names = list(pruned_knobs_dict.keys())
    valid_configs = []
    
    # 遍历每一套配置字典
    for config_dict in parsed_configs:
        # 检查是否包含所有必需的旋钮
        complete_config = {}
        has_missing = False
        
        for knob_name in knob_names:
            if knob_name in config_dict:
                complete_config[knob_name] = config_dict[knob_name]
            else:
                # 缺失旋钮：使用min_value作为默认值
                default_value = pruned_knobs_dict[knob_name]['min_value']
                complete_config[knob_name] = default_value
                has_missing = True
                print(f"错误：本套配置缺失旋钮 {knob_name}，使用min_value {default_value}")
        
        valid_configs.append(complete_config)
    
    if not valid_configs:
        return []
    
    # 计算每个旋钮的平均值（质心）
    knob_averages = {}
    for knob_name in knob_names:
        # 对于每一个旋钮：提取所有配置中的该旋钮值并计算平均值
        knob_values = [config[knob_name] for config in valid_configs]
        knob_averages[knob_name] = sum(knob_values) / len(knob_values)
    
    # 对每个旋钮维度，根据距离质心的远近进行排名
    # 距离越近，排名越靠前（排名值越小）
    knob_rank_mappings = {}
    
    for knob_name in knob_names:
        # 计算所有配置在该旋钮维度上与质心的距离
        distances = [abs(config[knob_name] - knob_averages[knob_name]) 
                    for config in valid_configs]
        
        # 对距离进行升序排序
        sorted_distances = sorted(set(distances))  # 使用set去重
        
        # 构建距离到排名的映射：距离相同的配置排名相同
        distance_to_rank = {}
        for rank, dist in enumerate(sorted_distances, start=1):
            distance_to_rank[dist] = rank
        # {knob1:dict{dist:rank},...}
        knob_rank_mappings[knob_name] = distance_to_rank
    
    # 计算每个配置的总排名分数
    config_scores = []
    for config in valid_configs:
        total_rank = 0
        for knob_name in knob_names:
            distance = abs(config[knob_name] - knob_averages[knob_name])
            rank = knob_rank_mappings[knob_name][distance]
            total_rank += rank
        config_scores.append(total_rank)
        # config_scores与valid_configs一一对应
    
    # 按总排名分数升序排序，选择Top-K配置
    # zip()将分数与配置相关联
    sorted_configs = sorted(zip(config_scores, valid_configs), key=lambda x: x[0])
    top_k_configs = [config for _, config in sorted_configs]
    
    k = int(config_parser['configuration recommender']['top_k'])
    print(f"排序算法成功计算出Top-{k}配置")
    return top_k_configs[:k]