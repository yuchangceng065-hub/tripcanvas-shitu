# RAG Lab · 检索增强问答练习项目

一个**零第三方依赖**即可运行的小型 RAG 系统，覆盖检索增强生成的完整链路：文档切分 → 嵌入向量化 → 向量检索 → 重排序 → 带引用溯源的答案生成，并叠加一层语义缓存。语料是杭州旅行主题的 12 篇短文，与仓库主应用的主题一致，但**代码与主应用完全隔离**，不参与主应用的构建、部署或 CI 评测。

> 定位：独立的检索/缓存练习项目，用于理解并验证 RAG 各阶段的工程取舍。它不是生产系统，语料为练习用短文，指标只在这个固定套件上有意义，不能外推为通用准确率。

## 为什么这样设计

很多 RAG demo 只是调一个向量数据库 SDK。这里把每一层都写成可读的实现，方便逐层调整参数并观察指标变化：

| 层 | 文件 | 实现要点 |
|---|---|---|
| 文档切分 | `rag/chunking.py` | 字符窗口 + overlap（保证跨边界的答案仍可召回），结束位置吸附到句读边界，避免把句子切断 |
| 嵌入向量化 | `rag/embeddings.py` | 本地 hashed TF-IDF：CJK 单字 + bigram、ASCII 词、sublinear TF、平滑 IDF、L2 归一化；另提供 OpenAI 兼容 `/embeddings` 适配器 |
| 向量检索 | `rag/vectorstore.py` | 精确余弦检索 + 倒排 posting 剪枝（结果与暴力检索完全一致，有测试验证），支持索引持久化 |
| 重排序 | `rag/rerank.py` | 词项覆盖率与向量分融合 + MMR 去冗余；`context_budget()` 限制上下文长度 |
| 答案生成 | `rag/generation.py` | 抽取式答案 + 引用溯源；分数或覆盖率不足时**明确拒答**；可选接入 chat 模型并被要求「资料不足」时弃答 |
| 语义缓存 | `rag/cache.py` | Redis 后端（`SET ... PX` / `GET` / `DEL`）与内存 LRU 后端可插拔；处理穿透、雪崩、击穿三种经典问题 |
| 编排与追踪 | `rag/pipeline.py` | 逐步 trace（embed/retrieve/rerank/generate 各自耗时与命中数） |

**默认不需要任何模型、网络或额外安装**：`pip` 依赖只有主仓库已有的 `pytest`（跑测试）与 `httpx`（仅在启用远程后端时才用到）。`redis`、`numpy`、`faiss`、embedding 模型都不是必需项。

## 快速开始

```sh
# 单条问答
python labs/rag-qa/cli.py "曲院风荷什么时候看荷花最好"
python labs/rag-qa/cli.py "量子纠错码的表面码阈值是多少"   # 应当拒答
python labs/rag-qa/cli.py "西湖有哪些主要景点" --trace --json

# 交互模式
python labs/rag-qa/cli.py --interactive

# 测试与评估门禁
python -m pytest labs/rag-qa/tests -q
python labs/rag-qa/evaluate.py --fail-on-regression
```

输出示例（拒答）：

```
Q: 量子纠错码的表面码阈值是多少
A: [拒答] 知识库中没有足够依据回答这个问题。（reason=no_evidence）
```

## 评估口径

固定 16 个问题：12 个语料内问题 + 4 个语料外负例。报告指标彼此独立，不合并成单一「准确率」：

| 指标 | 当前结果 | 说明 |
|---|---|---|
| Hit@1 / Hit@3 | 1.0 / 1.0 | top-1 / top-3 引用中命中目标文档的比例 |
| MRR | 1.0 | 正确答案首次出现排名的倒数均值 |
| 拒答准确率 | 1.0 | 4 个语料外问题全部拒答，0 次编造 |
| 缓存 | 重复 16 问 0 次重算 | 单飞 + NULL 短 TTL 生效 |
| 延迟 p50 / p95 | 约 0.63 / 0.71 ms | 本地纯 CPU、无网络；**不是性能承诺** |

`--fail-on-regression` 会把 Hit@3 ≥ 0.8、MRR ≥ 0.6、拒答准确率 = 1.0、重复查询零重算作为门禁，方便改参数时立刻发现回退。

## 缓存：三种故障模式

| 问题 | 处理 | 代码位置 |
|---|---|---|
| 缓存穿透（查询永远没有结果） | 拒答结果写成 `__null__` 标记，短 TTL（5s）缓存 | `SemanticCache.get_or_compute` |
| 缓存雪崩（大批 key 同时过期） | TTL 加 ±20% 抖动 | `SemanticCache._ttl` |
| 缓存击穿（热点 key 过期瞬间并发重算） | 按 key 的 `threading.Lock` 单飞，只有一个线程重算 | `SemanticCache._lock_for` |
| 内存占用 | `OrderedDict` LRU + 条数上限 | `MemoryBackend` |

Redis 后端在设置 `RAG_REDIS_URL` 且安装了 `redis` 时自动启用；否则静默降级到内存后端，行为一致。测试里用一个记录命令形状的 `FakeRedis` 验证 `SET ... PX`、命名空间前缀和 JSON 载荷，不需要真的起 Redis。

## 可调参数（建议动手试）

```python
RAGPipeline(chunk_size=90, chunk_overlap=25,   # 改小/改大观察切分数量与 Hit@1
            top_k=8, top_n=3,                   # 召回深度 vs 上下文噪声
            max_context_chars=900)              # 上下文预算
ExtractiveGenerator(min_score=0.22, min_coverage=0.15)  # 拒答阈值
rerank(..., coverage_weight=0.45, diversity_weight=0.25)
```

把 `min_score` / `min_coverage` 调低可以观察到负例开始被错误回答——这正是用来理解「召回率 vs 幻觉风险」权衡的入口。

## 可选：接真实 embedding / chat 端点

```sh
export MODEL_BASE_URL=https://your-provider/v1
export MODEL_API_KEY=...          # 只在环境变量中，不写入仓库
export EMBEDDING_MODEL=your-embedding-model
export MODEL_NAME=your-chat-model
export RAG_REDIS_URL=redis://localhost:6379/0
```

未配置时自动使用本地 hashed TF-IDF + 抽取式生成 + 内存缓存。远程后端只做接口适配，不改变评估口径；把真实模型结果和固定套件结果混为一谈是常见错误，本项目刻意分开。

## 面试速查

- **chunk size 怎么定**：按文档结构与问题粒度折中；太小丢上下文，太大引入噪声。本例 90 字 / 25 字 overlap，`chunk_corpus()` 可直接改；评估脚本会立刻显示 Hit@1 变化。
- **embedding 选型**：本地 hashed TF-IDF 是词法向量，无预训练语义；换成稠密模型需保持同一 `Embedder` 接口（`fit` / `encode` / `dimension`），并注意维度一致（检索前 `cosine` 会校验）。
- **top-k 与阈值**：top-k 控制召回，重排后的 top-n 控制进入 prompt 的证据条数；拒答阈值同时看向量分与词项覆盖率，避免只靠单一信号。
- **重排为什么需要**：向量检索优化整体相似度，短事实查询常更依赖精确词项；MMR 避免最终上下文是三段重复内容。
- **怎么评**：Hit@k / MRR 衡量检索，拒答准确率衡量幻觉控制，缓存命中率与 p50/p95 衡量工程行为；`abstainAccuracy` 与 `hitAt1` 是不同维度，不能合并。
- **幻觉怎么控**：答案只能来自检索片段，每个结论带引用；证据不足时拒答；生成模型被显式要求「资料不足」时弃答。
- **Redis 三问**：穿透（NULL 短 TTL）、击穿（单飞锁）、雪崩（TTL 抖动），外加 LRU 淘汰与 `SET ... PX` 的过期语义。
