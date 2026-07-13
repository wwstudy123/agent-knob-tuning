# from typing import Optional, Dict
# import configparser
# import re
# import json

# config = configparser.ConfigParser()
# config.read('./config.ini')

# # 读取匿名旋钮及其详细描述knob_details
# knob_details_path = "./range pruner/renamed_knobs"
# with open(knob_details_path, 'r') as f:
#     knob_details = json.load(f) # dict: {knob_name: {type, min, max, description}}

# # 读取筛选出的旋钮knob_names
# knob_list_path = config['knob selector']['output_file']
# with open(knob_list_path, "r") as f:
#     knob_names = json.load(f) # list:['knob94', 'knob12',...]
# print(f"过滤之前有{len(knob_names)}个旋钮: {knob_names}")

# # 过滤
# knob_names = [name for name in knob_names if (name in knob_details and knob_details[name].get('type') == 'integer')]
# print(f"过滤之后有{len(knob_names)}个旋钮: {knob_names}")


from flask import Flask, request, jsonify
from openai import OpenAI
import json
import re
import os
import sys
import heapq
import configparser

config = configparser.ConfigParser()
config.read('./config.ini')

file_path = config['workload analyzer']['output_file']
with open(file_path, "r") as f:
    workload_features = f.read().strip()
print("Workload features loaded:\n", workload_features)