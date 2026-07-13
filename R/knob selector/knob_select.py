"""
[已优化]调用LLM筛选旋钮，并提取旋钮名称，保存到文件
"""

from openai import OpenAI
import configparser
import re
import json

config = configparser.ConfigParser()
config.read('./config.ini')

# 读取工作负载特征workload features
file_path = config['workload analyzer']['output_file']
with open(file_path, "r") as f:
    workload_features = f.read().strip()  

# 读取匿名旋钮及其描述knobs
candidate_knobs = "./knob selector/renamed_knobs"
with open(candidate_knobs, "r") as f:
    knobs = f.read().strip() # 读取纯文本->str，格式化了的JSON String

# Prompt中的变量
knob_num=config['knob selector']['knob_num']
database_kernel=config['knob selector']['database_kernel']
hardware=config['knob selector']['hardware']
database_scale=config['knob selector']['database_scale']
db_metric = config['configuration recommender']['db_metric']
# TODO：Prompt优化
messages = [
    {"role": "system", "content": "You are an experienced database administrators, skilled in database knob tuning. You will determine which knobs are worth tuning. You only tune knobs that have a significant impact on DBMS performance."},
    {
        "role": "user",
        "content": f"""## Task Overview
Select the {knob_num} most important knobs from the provided for the current tuning task in order to optmize the **{db_metric}** metric. 

## Candidate Knobs
{knobs}


## Workload and Database Information

### 1. Environment & Database Configuration
- **Database Kernel**: {database_kernel}
- **Database Scale**: {database_scale}
- **Hardware**: {hardware}

### 2. Workload Features Details
{workload_features}


## Output Format Requirements
Only output strictly in the following format (ensure that knob names are enclosed in **angle brackets**, e.g., `<knob1>`), and **do not** output any other extraneous content:
```
1. <knob1>: (brief explanation)
2. <knob2>: (brief explanation)
...
```

Now let us think step by step. Your answer is:"""
    }
]


def call_llm1(model):
    """
    调用LLM筛选旋钮，并从其response中提取旋钮名称
    Args:
        model: LLM model名称
    Returns:
        筛选出的匿名旋钮集合
    """
    client = OpenAI(
        api_key=config['knob selector']['api_key'], 
        base_url=config['knob selector']['base_url']
    )

    completion = client.chat.completions.create(
        model=model,
        messages=messages, # Prompt
        temperature = 0
    )

    if not completion.choices:
        raise ValueError("API 返回的回复列表completion.choices为空")
    
    for choice in completion.choices: # 一个回复，以列表的形式返回
        print("\n--------------------------")
        print("LLM返回的内容如下:")
        print(choice.message.content)
        print("--------------------------\n")
    # e.g.
    # mes = """
    # 'Given the workload and database information, we need to select the 20 most impactful knobs to optimize throughput for an RDS MySQL 5.7 instance running on hardware with 8 cores and 16 GB RAM. The workload is heavily read-oriented with a high aggregation ratio, and it involves multiple tables with varying access patterns.\n\nHere are the top 20 knobs selected based on their potential impact on performance, particularly focusing on memory management, query execution efficiency, and I/O optimization:\n\n1. **knob10**: Buffer pool size - Critical for caching table and index data, directly impacts memory usage and I/O operations.\n2. **knob4**: Maximum number of threads - Important for concurrency and utilizing multiple cores effectively.\n3. **knob51**: I/O threads for read operations - Optimizes read operations which are predominant in the workload.\n4. **knob21**: I/O threads for write operations - Ensures efficient write operations, balancing the I/O subsystem.\n5. **knob3**: Persistent buffer size - Affects statement parsing and execution, important for a read-heavy workload.\n6. **knob6**: Sort buffer size per session - Directly impacts the efficiency of sorting operations in queries.\n7. **knob20**: Maximum dirty pages percentage in the buffer pool - Controls when flushing happens, affecting I/O and performance.\n8. **knob19**: Flush operation control - Influences the behavior of flushing dirty pages, impacting I/O performance.\n9. **knob12**: InnoDB purge threads - Helps in maintaining the efficiency of the DB by purging old versions of rows.\n10. **knob94**: Page cleaner threads - Critical for maintaining buffer pool health by flushing dirty pages.\n11. **knob93**: Sort buffer size - Affects the performance of queries involving sorting, which is significant given the aggregation ratio.\n12. **knob27**: Concurrency tickets for InnoDB - Allows more threads to enter InnoDB concurrently, leveraging multi-core processing.\n13. **knob16**: Temporary log file size during online DDL - Important for maintaining performance during schema changes.\n14. **knob43**: InnoDB full-text search index cache - Optimizes full-text search operations by caching index data.\n15. **knob82**: Bulk load for creating indexes - Enhances performance during index creation, important for maintaining query performance.\n16. **knob68**: Query cache size - Although deprecated in later versions, in MySQL 5.7 it can still boost read performance by caching query results.\n17. **knob61**: Aggressive flushing rate - Helps in managing I/O capacity more aggressively under heavy write load.\n18. **knob11**: Preflushing threshold for dirty pages - Controls the adaptive flushing mechanism based on the state of the buffer pool.\n19. **knob52**: Binary log file size - Affects replication performance and log management.\n20. **knob42**: Adaptive hash index aging - Allows InnoDB to adjust the hash index structure based on workload, which can improve lookup performance.\n\nThese knobs are chosen to directly or indirectly impact the performance metrics relevant to the described workload, focusing on optimizing memory usage, query performance, and I/O operations.'
    # """
    # 这里并没有将旋钮名称输入给LLM，但是它依然能够推理出旋钮名称
    # NOTE：理论上LLM有可能筛选出enum类型的旋钮，但是后面的range pruner无法处理这种类型的旋钮

    content = completion.choices[0].message.content or ""
    # 使用正则表达式解析匿名旋钮
    # knob_names = re.findall(r"\*\*(.+?)\*\*", content)
    knob_names = re.findall(r"<(.+?)>", content) # 旋钮名称改为被尖括号包裹
    knob_set = set(knob_names)
    return knob_set

if __name__ == '__main__':
    model = config['knob selector']['model']
    knob_set = call_llm1(model)
    print(f"LLM筛选出的{len(knob_set)}个旋钮如下:\n{knob_set}\n")

    # 将筛选出的旋钮保存到./knob selector/selected_knobs
    output_path = config['knob selector']['output_file']
    with open(output_path, 'w') as f:
        json.dump(list(knob_set), f)