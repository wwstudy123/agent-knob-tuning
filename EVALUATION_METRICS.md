# ADMTune 评估指标文档

## 📊 评估维度

ADMTune 从**调优效果**、**调优成本**和**调优安全性**三个核心维度进行评估。

---

## 1. 调优效果指标

### PIE - 性能提升效率 (Performance Improvement Efficiency)

**定义**：
```
PIE = (P_best - P_0) / T_opt
```

**参数说明**：
- `P_0`：默认配置下的基准性能
  - OLTP 负载：吞吐量 (throughput)
  - OLAP 负载：查询延迟 (latency)
- `P_best`：调优过程中获得的最优性能
- `T_opt`：达到最优性能所需的迭代次数

**意义**：
- 同时考虑性能收益和收敛速度
- PIE 越高，说明方法收敛越快且提升越大
- 避免了单纯追求 P_best 而忽略成本的问题

**计算方法**：
```python
from pie_analyzer import analyze_record_dir

result = analyze_record_dir(Path("record-p1-verify"))
print(f"PIE: {result['pie']}")
print(f"Improvement: {result['improvement_percent']:.2f}%")
```

---

## 2. 调优安全性指标

### HCBR - 高危配置拦截率 (High-risk Configuration Blocking Rate)

**定义**：
```
HCBR = N_blocked / N_high_risk × 100%
```

**参数说明**：
- `N_high_risk`：被判定为高风险的候选配置数量
- `N_blocked`：在真实数据库执行前被成功拦截的高危配置数量

**高风险配置判定标准**：
1. **执行失败**：throughput = 0（数据库启动失败或查询超时）
2. **性能严重退化**：性能下降超过 50%（相比基线）
3. **显著低于基线**：性能 < mean - 2×std

**意义**：
- 衡量代理模型的风险感知能力
- HCBR 越高，说明安全机制越有效
- 避免了危险配置对数据库造成损害

**计算方法**：
```python
from hcbr_analyzer import analyze_record_dir

result = analyze_record_dir(Path("record-p1-verify"))
print(f"HCBR: {result['hcbr']:.2f}%")
print(f"High-risk configs: {result['n_high_risk']}")
print(f"Blocked configs: {result['n_blocked']}")
```

---

## 3. 调优成本指标

### Token 消耗

**数据来源**：`token_usage.jsonl`

**计算方法**：
```python
import json
from pathlib import Path

token_log = Path("record-p1-verify/token_usage.jsonl")
with open(token_log) as f:
    tokens = [json.loads(line) for line in f if line.strip()]

total_tokens = tokens[-1]['cumulative_total_tokens']
print(f"Total tokens: {total_tokens}")
```

### Benchmark 次数

**数据来源**：`decision_log.jsonl` 或 `benmark_history`

**计算方法**：
```python
import json
from pathlib import Path

history = Path("record-p1-verify/benmark_history")
with open(history) as f:
    records = [json.loads(line) for line in f if line.strip()]

total_benchmarks = len(records)
print(f"Total benchmarks: {total_benchmarks}")
```

---

## 4. 综合评估报告

### 使用方法

```bash
# 分析单个实验
python evaluation_metrics.py record-p1-verify

# 分析多个实验（对比）
python evaluation_metrics.py record-1 record-2 record-3

# 保存到文件
python evaluation_metrics.py record-p1-verify --output report.json
```

### 输出示例

```
======================================================================
ADMTune Evaluation Report
======================================================================
Generated at: 2026-10-15T10:30:00
Experiments analyzed: 1
======================================================================

======================================================================
Experiment: record-p1-verify
======================================================================

📊 Performance Metrics (PIE)
----------------------------------------------------------------------
  P_0 (baseline):            0.100213
  P_best (optimal):          0.100842
  T_opt (iterations):        2
  PIE:                       0.000314
  Improvement:               0.63%

🛡️  Safety Metrics (HCBR)
----------------------------------------------------------------------
  Baseline mean:             0.100213
  High-risk configs:         0
  Blocked configs:           0
  HCBR:                      0.00%

======================================================================
Summary
======================================================================

Average PIE:  0.000314
Average HCBR: 0.00%

======================================================================
```

---

## 5. 实验设计建议

### 对比实验

为了验证 ADMTune 的有效性，建议与以下方法进行对比：

| 方法 | 说明 | 实现状态 |
|------|------|---------|
| **Default** | 数据库默认配置 | ✅ 基线 |
| **OtterTune** | 基于 GP 的调优方法 | ⏳ 待实现 |
| **ResTune** | 基于推荐系统的调优方法 | ⏳ 待实现 |
| **LLM-Tune** | 基于 LLM 的调优方法（无自适应控制） | ⏳ 待实现 |
| **ADMTune (full)** | 本文方法（完整版本） | ✅ 已实现 |

### 消融实验

为了验证各组件的贡献，建议进行以下消融实验：

| 变体 | Controller | Surrogate | Reflection | 说明 |
|------|-----------|-----------|------------|------|
| **Baseline** | ❌ | ❌ | ❌ | 纯 LLM 推荐 |
| **+Controller** | ✅ | ❌ | ❌ | 验证自适应控制 |
| **+Surrogate** | ❌ | ✅ | ❌ | 验证代理预筛 |
| **+Reflection** | ❌ | ❌ | ✅ | 验证反思记忆 |
| **Full** | ✅ | ✅ | ✅ | 完整方法 |

**运行消融实验**：
```bash
# 使用 run_ablation.py 工具
python tools/run_ablation.py \
  --variants baseline controller surrogate reflection full \
  --seeds 42 123 456 \
  --workloads tpcds tpcc
```

---

## 6. 统计显著性检验

### 多次运行

为了消除随机性影响，建议：
- 每个实验运行 **5 次**（不同随机种子）
- 报告**均值 ± 95% 置信区间**

### 示例代码

```python
import numpy as np
from scipy import stats

# 5 次运行的 PIE 值
pie_values = [0.001, 0.0012, 0.0009, 0.0011, 0.0010]

mean_pie = np.mean(pie_values)
ci_95 = stats.t.interval(0.95, len(pie_values)-1, loc=mean_pie, scale=stats.sem(pie_values))

print(f"PIE: {mean_pie:.6f} ± {ci_95[1] - mean_pie:.6f} (95% CI)")
```

---

## 7. 可视化建议

### 收敛曲线

**X 轴**：迭代次数  
**Y 轴**：性能（吞吐量/延迟）

```python
import matplotlib.pyplot as plt

history = load_benchmark_history(record_dir)
throughputs = [h['throughput'] for h in history]

plt.plot(throughputs, marker='o')
plt.xlabel('Iteration')
plt.ylabel('Throughput')
plt.title('Convergence Curve')
plt.savefig('convergence.png')
```

### 成本分解饼图

```python
import matplotlib.pyplot as plt

labels = ['Benchmark', 'LLM Tokens', 'Rollback']
sizes = [benchmark_cost, token_cost, rollback_cost]

plt.pie(sizes, labels=labels, autopct='%1.1f%%')
plt.title('Cost Breakdown')
plt.savefig('cost_breakdown.png')
```

### 配置稳定性热力图

```python
import seaborn as sns
import numpy as np

# 配置参数 vs 迭代次数
config_matrix = np.array([
    [h['knob'][f'knob{i}'] for i in range(1, 11)]
    for h in history
])

sns.heatmap(config_matrix, cmap='viridis')
plt.xlabel('Knob Index')
plt.ylabel('Iteration')
plt.title('Configuration Stability')
plt.savefig('config_stability.png')
```

---

## 8. 快速开始

### 步骤 1：运行实验

```bash
# 启动 LLM server
python "configuration recommender/LLM_server.py"

# 启动 DB client
python "configuration recommender/DB_client.py"
```

### 步骤 2：分析结果

```bash
# 运行评估
python evaluation_metrics.py record-p1-verify --output report.json

# 查看报告
cat report.json | python -m json.tool
```

### 步骤 3：可视化

```bash
# 生成收敛曲线
python visualize_convergence.py record-p1-verify

# 生成成本分解
python visualize_cost_breakdown.py record-p1-verify
```

---

## 9. 常见问题

### Q: 如何计算 OLAP 负载的 PIE？

A: 对于 OLAP 负载，使用延迟而不是吞吐量：

```python
result = analyze_record_dir(record_dir, metric_type='latency')
```

### Q: HCBR 为 0% 是否正常？

A: 可能正常，说明：
1. 没有触发高风险配置
2. 所有配置都在安全范围内
3. 或者代理模型没有拦截（需要检查 surrogate_diagnostics）

### Q: 如何提高 PIE？

A: 可以：
1. 增加迭代次数（但可能降低效率）
2. 优化 surrogate 模型（更快收敛）
3. 调整 controller 参数（更激进的搜索策略）

---

## 10. 参考文献

- **PIE 指标**：参考 OtterTune 和 ResTune 的评估方法
- **HCBR 指标**：本文提出的安全评估指标
- **统计检验**：使用 scipy.stats 进行显著性检验

---

**文档版本**：1.0  
**最后更新**：2026-10-15  
**维护者**：ADMTune 团队
