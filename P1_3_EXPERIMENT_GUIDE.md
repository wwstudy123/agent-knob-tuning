# P1.3 端到端实验已启动

## 🚀 实验状态

**启动时间**：2026-10-15 09:43  
**实验配置**：`config_p1_verification.ini`  
**记录目录**：`configuration recommender/record-p1-verify/`

### 运行中的进程

```bash
# LLM_server (端口 4000)
root       87278  python configuration recommender/LLM_server.py

# DB_client (运行 TPCDS 基准测试)
root       92499  python configuration recommender/DB_client.py
```

---

## 📋 实验配置

### 关键参数

- **迭代次数**：3 次（短实验）
- **基准测试**：TPCDS
- **自适应控制器**：✅ 启用
- **Surrogate 模型**：✅ 启用
- **反思记忆**：✅ 启用
- **随机种子**：42

### 预期行为

实验应该触发以下决策之一：
- `shrink_ranges`：当失败率 > 25% 时
- `expand_ranges`：当改进率 > 5% 且置信度 > 70% 时
- `rollback_safe`：当失败率 > 50% 时
- `continue_local`：默认决策

---

## 🔍 监控实验进度

### 方法 1：状态检查脚本
```bash
cd /workspace/AgentTune
python check_experiment_status.py
```

### 方法 2：查看日志
```bash
# DB_client 日志
tail -f /tmp/db_client.log

# LLM_server 日志
tail -f /tmp/llm_server.log
```

### 方法 3：检查记录目录
```bash
ls -la "configuration recommender/record-p1-verify/"
```

---

## ✅ 验证清单

实验完成后，检查以下证据：

### 1. Checkpoint 包含 search_space
```bash
python -c "import json; ckpt=json.load(open('configuration recommender/record-p1-verify/checkpoint.json')); print('search_space present:', 'search_space' in ckpt)"
```

### 2. Decision log 包含 shrink/expand
```bash
cat "configuration recommender/record-p1-verify/decision_log.jsonl" | python -m json.tool | grep -E '"name"|"params"'
```

### 3. SearchSpace 范围发生变化
```bash
python -c "
import json
ckpt = json.load(open('configuration recommender/record-p1-verify/checkpoint.json'))
if 'search_space' in ckpt:
    ss = ckpt['search_space']
    for key in list(ss['current'].keys())[:3]:
        orig = ss['original'][key]
        curr = ss['current'][key]
        changed = orig.get('min_value') != curr.get('min_value') or orig.get('max_value') != curr.get('max_value')
        print(f'{key}: original=[{orig.get(\"min_value\")}, {orig.get(\"max_value\")}], current=[{curr.get(\"min_value\")}, {curr.get(\"max_value\")}], changed={changed}')
"
```

---

## 📊 预期结果

### 成功标准

1. ✅ 实验完成 3 次迭代
2. ✅ checkpoint.json 包含 `search_space` 字段
3. ✅ decision_log.jsonl 至少包含 1 次非 `continue_local` 决策
4. ✅ search_space 的范围发生了变化（或至少被正确序列化）
5. ✅ 所有进程正常退出（status=completed）

### 如果失败

查看日志定位问题：
```bash
# 检查错误
grep -i error /tmp/db_client.log
grep -i error /tmp/llm_server.log

# 检查异常
grep -i exception /tmp/db_client.log
```

---

## 🎯 下一步

### 实验完成后

1. **运行验证脚本**
   ```bash
   python verify_p1_3.py
   ```

2. **分析结果**
   - 检查 shrink/expand 是否被触发
   - 对比修改前后的搜索范围
   - 评估对性能和成本的影响

3. **记录结论**
   - P1.3 是否正常工作
   - 是否需要调整参数
   - 对评估指标的影响

### 如果实验成功

继续实现评估指标：
- PIE（性能提升效率）
- HCBR（高危配置拦截率）

### 如果实验失败

调试并修复问题，可能原因：
- LLM API 调用失败
- MySQL 连接问题
- 配置参数不当
- 代码 bug

---

## 📝 实时状态

运行以下命令查看实时状态：
```bash
python check_experiment_status.py
```

---

**实验正在运行中...** ⏳

预计完成时间：约 30-60 分钟（取决于 LLM 响应速度和基准测试时间）
