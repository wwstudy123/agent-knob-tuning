'''
提取指定25个常用旋钮的详细信息，输出到单独的JSON文件中
输出：25_knobs_details.json
'''
import json

knobs_list = [
    "innodb_buffer_pool_size",
    "innodb_log_file_size",
    "innodb_flush_log_at_trx_commit",
    "sync_binlog",
    "innodb_thread_concurrency",
    "tmp_table_size",
    "max_heap_table_size",
    "innodb_io_capacity",
    "innodb_io_capacity_max",
    "innodb_flush_method",
    "table_open_cache",
    "table_open_cache_instances",
    "innodb_read_io_threads",
    "innodb_write_io_threads",
    "sort_buffer_size",
    "innodb_purge_threads",
    "innodb_adaptive_flushing",
    "innodb_max_dirty_pages_pct",
    "innodb_lru_scan_depth",
    "innodb_adaptive_hash_index_parts",
    "thread_cache_size",
    "innodb_stats_on_metadata",
    "innodb_log_buffer_size",
    "join_buffer_size",
    "innodb_read_ahead_threshold"
]

knob_details_path = "./utils/top_25/most_knobs.json"

# 加载旋钮详细信息
with open(knob_details_path, "r") as f:
    knob_details = json.load(f)  # dict {knob_id: {type, description, valid_range, special_value}}
# 组装筛选出的匿名旋钮的详细信息
knob_details_selected = {name: knob_details[name] for name in knobs_list if name in knob_details}
print(f"成功提取了{len(knob_details_selected)}个旋钮的详细信息")

# 格式化为JSON String
knobs = json.dumps(knob_details_selected, indent=2, ensure_ascii=False)
# 输出到文件
output_path = "./utils/top_25/25_knobs_details.json"
with open(output_path, "w") as f:
    f.write(knobs)