# 此脚本可以一键运行
# 用于从xml文件中生成候选参数的简化字段描述{max,min/enum_values,type,description}
set -e

python "./knob selector/get_candidate_knobs/xml_parser.py"
python "./knob selector/get_candidate_knobs/get_candidate_knobs.py"
python "./knob selector/get_candidate_knobs/simplify_candidate_knobs.py"