# QuantGPT Roadmap

> Last updated: 2026-05-20

QuantGPT 的方向：从个人研究工具走向开放因子研究生态。本地引擎负责挖掘和验证，社区负责算子扩展和知识积累。

---

## Done

- [x] 60+ 因子算子（截面、时序、非线性、三元、技术指标）
- [x] 多维评分体系（IC/IR/稳定性/反过拟合/分组回测）
- [x] 4 项反过拟合检验 + Walk-forward 验证
- [x] 8 方向进化突变 + 交叉引擎
- [x] WQ BRAIN 集成（可选，模拟 + 提交）
- [x] MCP 15 工具 + REST API + Web UI
- [x] 双 LLM 交叉评审（DeepSeek Reasoner）

---

## Planned

### 1. 对抗性检验 (Adversarial Validation)

**目标**：在现有反过拟合体系上增加"破坏性测试"层，降低假阳性率。

- [x] **标签置换检验** — 随机打乱 forward returns，因子如果仍然"有效"，说明是过拟合
- [x] **时间序列打乱** — 破坏时序结构（block shuffle），检验因子是否依赖真实时序模式
- [x] **随机股票池** — 从全市场随机抽样构造伪 universe，因子在伪池上不应显著
- [x] **噪声注入** — 对因子值加高斯噪声，评估 IC 衰减速率（衰减快=信号脆弱）

实现：`adversarial_validator.py`，作为 `anti_overfit.py` 的补充层，已集成到回测流水线。

### 2. 因子生命周期管理

**目标**：避免知识固化——因子会衰减，规则会过时。

- [ ] **OOS 半衰期监控** — 对因子库中的因子定期滚动计算样本外 IC，低于 50% 峰值时标记"衰减中"
- [ ] **知识库规则过期** — `rules/` 中每条规则标注来源因子和验证日期，OOS 失效时自动降级为 `findings/`
- [ ] **因子库自动清理** — 本地因子库中长期未通过 OOS 检验的因子标记为 archived

### 3. 评价体系解耦，脱离 BRAIN 依赖

**目标**：让回测和进化引擎服务于通用量化框架，不绑定 WQ BRAIN。

当前状态：评分函数 `compute_factor_score()` 为 5 维等权（不含外部平台对齐项），但进化引擎的 fitness 用 WQ 的 `ls_sharpe * sqrt(|ls_annual| / turnover)` 公式。

- [ ] **目标适配器抽象** — 将评分标准抽象为可配置接口：
  ```python
  class ScoringTarget(Protocol):
      def compute_fitness(self, backtest_result: dict) -> float: ...
      def compute_score(self, backtest_result: dict) -> dict: ...
  ```
  内置两个实现：`ICTarget`（以本地 IC/IR 为标准）、`WQTarget`（现有 WQ 逻辑）
- [ ] **自定义适配器文档** — 让社区可以对接 Zipline、Backtrader、vnpy 等框架

### 4. 算子贡献指南

**目标**：降低社区贡献门槛，让外部开发者能添加新算子。

- [ ] **`docs/OPERATOR_GUIDE.md`** — 算子贡献指南：
  - 算子注册机制说明
  - 截面 vs 时序 vs 非线性的分类规范
  - 必须提供的测试用例模板
  - PR checklist
- [ ] **算子测试框架** — 标准化测试模板，一行注册、自动生成 edge case 测试
- [ ] **高频/另类数据算子** — 预留接口支持分钟级数据、舆情数据、链上数据等

### 5. 因子挖掘大赛

**目标**：社区参与知识积累，从"个人作品"走向"协作生态"。

- [ ] **公开回测数据片段** — 在 repo 中发布标准化历史数据子集（如 CSI500 2023-2024），社区可本地复现
- [ ] **大赛评分标准** — 基于本地验证结果排名（IC IR + 反过拟合分 + 去重）
- [ ] **优秀因子合并知识库** — 获奖因子的设计思路和验证数据进入 `research_notes/knowledge/`

---

## How to Contribute

See [CONTRIBUTING.md](CONTRIBUTING.md) for general guidelines. For roadmap items:

1. Pick an unchecked item above
2. Open an issue to discuss approach
3. Submit a PR referencing the issue

Priority items are marked in the **Planned** section. Start with the ones that interest you — we value working code over perfect plans.
