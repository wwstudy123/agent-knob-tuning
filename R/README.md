# AgentTune 复现：面向数据库旋钮调优的多智能体协同框架  *[SIGMOD 2026]*

> This repository should be forked from **https://github.com/intlyy/AgentTune**

本仓库是基于论文 AgentTune 与原始开源仓库的复现版本，将论文原型重构为一个能够在 **WSL2 + MySQL 5.7 + Sysbench** 本地环境中稳定闭环运行的数据库旋钮自动调优系统。

## 目录概览

```text
workload analyzer/           工作负载解析与特征提取
knob selector/               候选旋钮、匿名化与重要旋钮筛选
range pruner/                范围裁剪与白盒校验
configuration recommender/   LLM 服务端、数据库控制闭环与实验记录
utils/                       一次性压测、候选集检查、Top-25 等辅助工具
config.ini                   主配置文件
run.bash                     主入口脚本
```

## 项目定位

`AgentTune` 将数据库旋钮调优拆解为四个协作智能体：

1. `Workload Analyzer`
2. `Knob Selector`
3. `Range Pruner`
4. `Configuration Recommender`

核心是：让不同智能体分别负责工作负载理解、旋钮筛选、范围裁剪和基于反馈的配置推荐，并通过树搜索与白盒规则约束降低非法配置和数据库崩溃风险。

本仓库保留了论文的主线思想，但实现目标不是复刻一个“演示原型”，而是完成 **面向 MySQL 5.7 + Sysbench 场景的本地化、工程化、可重复运行的深度复现**。

## 复现范围

- 数据库内核：`MySQL 5.7.19`（源码编译）
- 工作负载：`Sysbench oltp_read_write`
- 数据规模：`50 tables × 1,000,000 rows`
- 运行环境：`WSL2 Ubuntu 22.04`
- 调优目标：`throughput`

## 原始仓库的限制

原始代码已经给出了四阶段流水线，但整体存在一些明显限制：

- 实验环境假设较强，默认更偏向 `RDS MySQL 5.7 + 远程 SSH` 的实验方式
- `DB_client.py` 直接依赖 `sshpass + ssh + /etc/my.cnf + service mysqld restart`
- LLM 输出大量依赖自由文本和正则抽取，结构脆弱
- 范围校验、异常回滚、日志治理和长时间运行稳定性不足
- 原始 README 中 Sysbench 的准备参数写为 `150 tables × 800000 rows`，与论文正文常见设置 `50 tables × 1,000,000 rows` 不一致

因此，这个仓库的核心工作是围绕论文思想做了一次可执行、可验证、可排障的本地闭环重构。

## 完成的主要工作

### 1. 搭建并固化本地实验环境

- 将项目从原始代码假设的远程环境迁移到 `WSL2 Ubuntu 22.04`
- 使用 `Miniconda(agenttune)` 管理 Python 运行环境
- 本地源码编译并部署 `MySQL 5.7.19`，手动处理 `OpenSSL 1.1.1w` 兼容性问题
- 将数据库固定为 `127.0.0.1:3307` 的 TCP/IP 访问模式
- 使用源码编译的 `Sysbench`，并将测试规模对齐到论文场景：`50 tables × 1,000,000 rows`

### 2. 重构四个智能体之间的数据流

- **优化 Prompt**，使工作负载、硬件、数据库规模、内部指标和历史配置都能被明确注入上下文
- 将多个阶段从“自由文本 + 正则抽取”改为“受约束输出 + 结构化解析”
- 在需要插入 Prompt 的 JSON 内容处统一使用格式化字符串，提高可读性与稳定性
- 将智能体间交互从脆弱文本耦合改造成更稳定的结构化数据流

### 3. 强化四个核心模块

| 模块 | 原始仓库 | 本仓库复现与改造 |
| --- | --- | --- |
| `workload analyzer/WorkloadParser.py` | 更偏统计打印 | 输出面向 Prompt 的 Markdown 结构化特征报告，补充读写比例、聚合、谓词、表列访问等信息 |
| `knob selector/knob_select.py` | 自由回答后用 `**knobX**` 正则抽取 | 显式绑定优化目标，约束输出为 `<knobX>` 格式，降低解析脆弱性 |
| `range pruner/range_pruner.py` | 文本段落抽取范围 | 改为 JSON 结构化输出，结合 `Pydantic` 动态模型与白盒规则校验 |
| `configuration recommender/LLM_server.py` | 用正则表达式从自由文本中解析 JSON | 直接约束模型输出配置 JSON，增加范围验证、步长对齐、去重和历史窗口 |
| `configuration recommender/DB_client.py` | 远程 SSH 改配置、重启数据库 | 改为本地 TCP/IP 探活、优雅启停、异常回滚、日志排障和持久化记录 |
| `configuration recommender/config_rank.py` | 质心排序雏形 | 补齐缺失项、增强异常处理，稳定支撑 beam/top-k 排序 |
| `run.bash` | 线性执行脚本 | 增加端口检查、后台服务治理、退出清理，避免 Flask 重复启动和脚本卡死 |

### 4. 为 Range Pruner 和 Recommender 加入安全约束

- 范围裁剪阶段会校验旋钮是否存在、字段是否完整、数值能否转换、是否越界、`min <= max`、`step > 0` 等条件
- 配置推荐阶段会再次检查配置是否缺失旋钮、是否包含未知旋钮、是否越界、是否需要步长对齐、是否命中特殊值
- MySQL 启动失败时会自动回滚 `my.cnf` 到备份配置，避免一次错误推荐直接破坏后续实验

### 5. 重写数据库控制闭环，解决长时运行稳定性

与原始 `DB_client.py` 相比，重点做了以下改造：

- 统一使用 TCP/IP，而不是依赖远程 socket / SSH
- 在重启流程中增加端口探活、`SELECT 1` 真连接检查、进程退出等待和异常情况下的兜底清理
- 关闭前禁用 `innodb_buffer_pool_dump_at_shutdown`，减少反复调优时的停机成本
- 启动失败时自动打印最近 MySQL 日志，便于快速定位错误
- 每次应用新配置都从 `my.cnf.bak` 恢复基线，再逐项追加参数；失败则自动回滚
- 重写 Sysbench 执行与解析逻辑，加入 `--warmup-time=80`、`--report-interval=5`、`--rand-seed=42` 等参数，增强测试稳定性

### 6. 补全候选旋钮知识底座与实验辅助工具

除了主流程本身，还补做了很多“论文外但复现必需”的基础设施：

- 在 `knob selector/get_candidate_knobs/` 中增加官方 XML 文档解析、候选旋钮合并与精简脚本
- 形成从 `official_doc.xml` -> `xml_output.json` -> `candidate_knobs_merged.json` 的候选空间构建链路
- 编写 `utils/consistency_check.py` 检查候选旋钮定义与范围信息的一致性
- 编写 `utils/test_by_sysbench_once/test_by_sysbench_once.py`，支持绕开完整闭环对单套配置做独立复测
- 追加日志与记录体系，包括 `benchmark_feedback`、`top_k`、`turn_n`、`optimal configuration`、MySQL 启动日志、Sysbench 日志等

## 当前复现结果

- 四智能体主链路已经打通，可完成“工作负载解析 -> 旋钮筛选 -> 范围裁剪 -> 配置推荐 -> 基准测试反馈 -> 下一轮推荐”的自动化闭环
- 实现了单机在 Sysbench `50 表 × 100 万行` 的压测中，将 MySQL 数据库系统的吞吐量从 `341.30 TPS` 提升到 `650.72 TPS`

## 当前仓库的边界

目前仍然是一个 **聚焦式复现版本**：

- 深度优化并验证的是 `MySQL 5.7 + Sysbench`
- 尚未完整覆盖论文中的 PostgreSQL、TiDB、JOB、TPC-C、TPC-DS 等全部实验矩阵
- 当前运行环境是单机 `WSL2`，数据库、LLM 客户端、Flask 服务和 Sysbench 会共享本机资源，实验环境和论文并不等价

## 说明

下方保留了原始开源仓库 `README.md` 的原文内容，便于对照论文原型与本仓库复现版本之间的差异。

---

# AgentTune: A Multi-Agent Collaborative Framework for Database Knob Tuning *[Accepted by SIGMOD 2026]*

This is the source code to the paper **"AgentTune: A Multi-Agent Collaborative Framework for Database Knob Tuning"**. Please refer to the paper for the experimental details.

## Table of Content
* [Environment Installation](#environment-installation)
* [Workload Preparation](#workload-preparation)
* [Quick Start](#quick-start)
* [Scalability Study](#scalability-study)
* [Ablation Study](#ablation-study)

## Environment Installation

In our experiments,  We conduct experiments on MySQL 5.7.

1. Preparations: Python == 3.10

2. Install packages

   ```shell
   pip install -r requirements.txt
   pip install .
   ```

3. Download and install MySQL 5.7 and boost

   ```shell
   wget http://sourceforge.net/projects/boost/files/boost/1.59.0/boost_1_59_0.tar.gz
   wget https://dev.mysql.com/get/Downloads/MySQL-5.7/mysql-boost-5.7.19.tar.gz
   
   sudo cmake . -DCMAKE_INSTALL_PREFIX=PATH_TO_INSTALL -DMYSQL_DATADIR=PATH_TO_DATA -DDEFAULT_CHARSET=utf8 -DDEFAULT_COLLATION=utf8_general_ci -DMYSQL_TCP_PORT=3306 -DWITH_MYISAM_STORAGE_ENGINE=1 -DWITH_INNOBASE_STORAGE_ENGINE=1 -DWITH_ARCHIVE_STORAGE_ENGINE=1 -DWITH_BLACKHOLE_STORAGE_ENGINE=1 -DWITH_MEMORY_STORAGE_ENGINE=1 -DENABLE_DOWNLOADS=1 -DDOWNLOAD_BOOST=1 -DWITH_BOOST=PATH_TO_BOOST;
   sudo make -j 16;
   sudo make install;
   ```



## Workload Preparation 

### SYSBENCH

Download and install

   ```shell
   git clone https://github.com/akopytov/sysbench.git
   ./autogen.sh
   ./configure
   make && make install
   ```

Load data

   ```shell
   sysbench --db-driver=mysql --mysql-host=$HOST --mysql-socket=$SOCK --mysql-port=$MYSQL_PORT --mysql-user=root --mysql-password=$PASSWD --mysql-db=sbtest --table_size=800000 --tables=150 --events=0 --threads=32 oltp_read_write prepare > sysbench_prepare.out
   ```

### Join-Order-Benchmark (JOB)

Download IMDB Data Set from http://homepages.cwi.nl/~boncz/job/imdb.tgz.

Follow the instructions of https://github.com/winkyao/join-order-benchmark to load data into MySQL.

### TPCC and TPC-DS
Follow the instructions of https://www.tpc.org/default5.asp to prepare TPC benchmarks.



## Quick Start

1. Modify `config.ini`
2. Set benchmark path in `./configuration recommender/DB_client.py`
3. Run
   ```shell
   ./run.bash
   ```

The four agents in **AgentTune**—**Workload Analyzer, Knob Selector, Range Pruner, and Configuration Recommender**—will execute sequentially. Intermediate results will be saved to the location you specified in `config.ini`, and all tuning records and results will be stored in the `./configuration recommender/record` directory.

## Scalability Study
This section introduces how to expand **AgentTune** to encompass new database scales, engines, and hardware environments. Detailed experimental results and analysis can be found in **Section 8.3** of the paper.
### Database Scale
When the database scale changes, simply modify `config.ini` by setting:
- `database_scale` = new database scale
- `database_name` = DB_Name *(If you create a new database)*

### Hardware
When the hardware environment changes, simply modify `config.ini` by setting:
- `hardware` = new hardware configuration

### Database Engine
1. Download and install the new database engine
2. Prepare workload in the new database engine
3. Prepare candidate knobs

   In `./knob selector/get_candidate_knobs/`, we use MySQL as an example to demonstrate how to quickly obtain candidate knobs by processing the official documentation.
4. Modify database connection method and knob setting method in `./configuration recommender/DB_client.py`
5. Modify `config.ini` by setting:
   - `database_kernel` = new database engine
   - `database_name` = DB_Name

   and other database configurations including *DB_User, DB_Password, DB_Host* and *DB_Port*.

## Ablation Study
This section introduces how to evaluate the effectiveness of each component in **AgentTune** and the impact of different large language models (LLMs). Detailed experimental results and analysis can be found in **Section 8.4** of the paper.
### Ablation Study - Components
The four agents in **AgentTune** execute sequentially and intermediate results for each component are saved to the location you specified in `config.ini`. So you are free to remove or replace any component to evaluate the effectiveness. However, since the subsequent pruning and tuning stages depend on the selected knobs from the `Knob Selector`, it is recommended to replace this step with alternative methods (e.g., manual selection or ML-based approaches) rather than removing it entirely.

### Ablation Study - Knob Size
To change the knob size in **AgentTune**, simply modify `config.ini` by setting:
- `knob_num` = new knob size

### Ablation study - Beam Size
To change the beam size in **AgentTune**, simply modify `config.ini` by setting:
- `top_k` = new beam size

### Ablation study - LLMs
To change the LLM in **AgentTune**, simply modify `config.ini` by setting:
- `model` = new large language model
