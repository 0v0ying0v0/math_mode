# 2026 中国研究生数学建模竞赛 D 题 —— 提交成品

**题目**：山区洪涝灾害下无人机运输与通信协同优化

---

## 一、快速开始

```bash
cd 本目录
python3 -m venv .venv
.venv/bin/pip install numpy scipy pandas openpyxl matplotlib h5py networkx ortools

# 一键复现全部结果
bash run_all.sh
```

`run_all.sh` 会依次重算四问、生成图表、导出论文，并跑完全部验证套件。

---

## 二、提交文件清单

### 2.1 结果文件（对齐 `结果提交模板.xlsx`，列名与顺序逐字符一致）

| 文件 | 行数 | 内容 |
|---|---|---|
| `out/Q1_单点组批.csv` | 18 | 问题一 单点往返组批 |
| `out/Q2_运输架次.csv` | 37 | 问题二 运输架次 |
| `out/Q2_逐箱交付.csv` | 80 | 问题二 逐箱交付时刻 |
| `out/Q3_中继架次.csv` | 15 | 问题三 中继架次 |
| `out/Q3_通信保障.csv` | 320 | 问题三 通信保障分阶段明细 |
| `out/Q4_分区配置.csv` | 5 | 问题四 K=2/K=3 各区资源明细 |

### 2.2 论文

**三条导出链路并存**（各有分工）：

| 链路 | 命令 | 产出 | 数学渲染 | 图片 |
|---|---|---|---|---|
| **pandoc（首选）** | `code/export_pandoc.py` | `论文_pandoc.html`（MathJax）/ `论文_pandoc.docx`（**Word 原生公式**）/ `论文.tex` | **原生** | 7/7 内嵌 |
| 自实现 HTML | `code/export_paper.py` | `main.html` | Unicode 化 | 7/7 引用 |
| 自实现 DOCX | `code/export_docx.py` | `*.docx` | Unicode 化 | 7/7 内嵌 |

> pandoc 由 pip 包 `pypandoc_binary` 提供（自包含二进制，无需系统安装）。
> 本机**无法安装 LaTeX 引擎**（conda 因 TOS 缓存权限失败、tectonic 不在 PyPI、无 texlive），
> 故 PDF 需在有 TeX 环境的机器上对 `paper/export/pandoc/论文.tex` 执行 `xelatex` 获得。


| 文件 | 说明 |
|---|---|
| `paper/main.md` | 论文源文件（Markdown，八章 + 3 个附录） |
| `提交/论文/论文.pdf` | **论文 PDF**：A4，40 页，包含正文与 7 张图表 |
| `paper/export/main.html` | 打印就绪 HTML 源，可在浏览器中打印为 PDF |
| `paper/export/山区洪涝灾害下无人机运输与通信协同优化.docx` | Word 版 |
| `paper/figs/fig1..fig7.png` | 7 张图表 |

> PDF 由 Edge 根据打印就绪 HTML 导出；A4、40 页。正文文本可提取，末尾附有七张图表。

### 2.3 程序

```
code/
  core.py                 公共物理规则层（四问唯一口径，禁止他处重写公式）
  dem_io.py               30 m DEM 解码（平铺 PackBits GeoTIFF）
  comm_geo.py             通信几何：中继候选格点、链路掩膜
  q1_grouping.py          问题一 求解
  q2_schedule.py          问题二 求解
  q3_joint.py             问题三 求解
  q4_partition.py         问题四 求解
  verify.py               独立校验器（不 import 任何求解模块）
  render.py               出图
  gis_analysis.py         GIS 地形分析
  q2_merge_accounting.py  G-B 代价核算（Q2 多服务区合并）
  q2_scale_probe.py       求解器规模探针（CP-SAT 实测）
  q3_diagnose.py          问题三通信覆盖诊断
  export_paper.py         论文导出（Markdown→HTML→DOCX）
run_all.sh                一键复现
```

### 2.4 口径契约与治理（可审计的"检查说明"）

```
spec/
  task_spec.md            判定口径契约（每条约束 → 可执行断言）
  symbols.md              唯一符号表
  decisions.md            D1–D14 + D-ALT 歧义裁决记录
  assumptions.md          A/B/C 三类假设显式清单
  data_contract.md        附件字段映射 + 校验式
  rubric.md               自建评分要点
  iteration_ledger.md     迭代账本（23 轮 × 8 组字段 + 2 次回退记录）
  problem_registry.md     问题注册表（44 个问题的 ID/状态/结论）
  effectiveness_rubric.md 有效性评分 + G-A/G-B 硬门
```

---

## 三、主要结果

| 问题 | 关键结果 |
|---|---|
| **一** | 18 架次（C×11 + B×7）、**63.2416 kWh**、**33043.2 s**、最低返航 SOC **22.71%** |
| **二** | 37 架次、makespan **12527.0 s**（较串行缩短 62%）、首批与医疗时限 **100% 满足** |
| **三** | 15 中继架次、总能耗 **94.7400 kWh**、**通信中断 0/348 需中继样本** |
| **四** | K=2：11 架运输机 + 4 中继（缺口率 2.4167）；K=3：13 架 + 6 中继（缺口率 4.25） |

**三个反直觉发现**

1. **中继不扩距、只绕障**：`T↔G01` 门限 122 dB / 12.583 km，而 `T↔RA` 仅 116 dB / 6.307 km
   （中继接入端 6 dBi < 网关 12 dBi）。加中继反而把可用距离砍半。
2. **能量是实质约束**：45 个（机型×服务区）组合中 37 个在 ρ≤60% 内受能量约束；
   C 型在 S008 满载往返 SOC = −1.0%（不可行）。
3. **中继需求的机理**：O01 网关在洼地，运输机需飞约 **4 km** 才能越过遮蔽山脊；
   故航线长度 < 遮蔽起始距离 ⇔ 全程可直连（15/15 准确），但存在反例（S004）。

---

## 四、验证状态（全部可复跑）

| 套件 | 命令 | 结果 |
|---|---|---|
| 治理硬检查 | `bash tests/governance_check.sh` | ✅ 全绿 |
| 问题验证 V1–V5 | `.venv/bin/python tests/run_verification.py` | ✅ **51/51** |
| 独立校验 A–F + R1–R5 | `.venv/bin/python code/verify.py` | ✅ **52/52** |
| 通信零中断 | `.venv/bin/python tests/verify_q3_comm.py` | ✅ **中断 0/348 需中继样本** |
| 模板对齐 | `.venv/bin/python tests/check_template.py` | ✅ **6/6** |
| 全局验证 | `.venv/bin/python tests/run_global_verify.py` | ✅ **13/13** |
| 账本结算 | `.venv/bin/python tests/run_settlement.py` | ✅ **7/7** |
| 论文-结果交叉对表 | `.venv/bin/python tests/check_consistency.py` | ✅ **66/66** |
| 导出结构验证 | `.venv/bin/python tests/check_export.py` | ✅ **31/31** |

### 原「不可判定项」GR-1 已消除

`run_global_verify.py` 的 `GR-1`（±5% 逐箱载荷扰动下方案可行率）经 Q1 均衡装箱后由 **63% 提升至 100%**，
全局验证现为 **13/13 全绿**。

- **消除方式**：原失效集中于 Q1-003（S002）与 Q1-005（S003）两个 C 型远距离架次（装载 67 kg 达能量上限 68.13 kg，
  基准返航 SOC 仅 21.16% / 20.89%）。均衡装箱后两站改为 **42/39 kg** 分箱，SOC 升至 **34.11% / 33.85%**，
  ±5% 扰动下不再越限。
- 已写入 `paper/main.md` §7.4 与 `reports/B8_global.md`。

---

## 五、迭代过程（23 轮，含 2 次实质回退）

| 轮次 | 模块 | 反馈 | 动作 |
|---|---|---|---|
| IT-00 | 环境 + DEM 解码 + 公共规则层 | ✅ | 保留 |
| IT-01–02 | 治理层 + λ 旋钮审计 | ✅ | 保留 |
| IT-03 | spec 契约 + 验证测试 | ✅ | 保留 + **RB-01 回退** |
| IT-04 | 问题一求解 | ✅ | 保留（标注脆弱解） |
| IT-05 | 求解器规模探针（CP-SAT 实测） | ✅ | 保留 |
| IT-06 | 问题三求解 | ✅ | 保留（3 次实现缺陷修正） |
| IT-07 | 问题四求解 | ✅ | 保留 |
| IT-08 | 问题二求解 + 独立校验 | ✅ | 保留（6 表齐备） |
| IT-09 | 全局验证 | ✅ | 保留（登记不可判定项） |
| IT-10 | 评审 + 账本结算 | ✅ | 保留（λ 复核通过） |
| IT-11 | 图表 + 论文 | ✅ | 保留（C1–C8 达成） |
| IT-12 | 论文导出 HTML/DOCX | ✅ | 保留 |
| IT-13 | Q3 中继合并优化 | ❌ **负反馈** | **RB-02 回退**（合并净负） |
| IT-14 | Q2 多服务区合并 G-B 核算 | ⚪ | **否决**（零实现代码） |
| IT-15 | PDF/打印就绪导出 | ✅ | 保留（权限阻断已登记） |
| IT-16 | GIS 地形分析 | ✅ | 保留 |
| IT-17 | 整合与成品打包 | ✅ | 保留 |
| IT-18 | C-2 并行口径裁决落档 | ✅ | 保留（无代码改动） |
| IT-19 | 外部评审 7 组问题落地 | ✅ | 保留（修掉虚假绿勾） |
| IT-20 | pandoc 公式原生渲染导出 | ✅ | 保留 |
| IT-21 | Q2 方案非最优自我发现 | ✅ | 保留 |
| IT-22 | Q4 单口径重排 + 中继反例 | ✅ | 保留 |

**两次实质回退的根因，已固化为迭代流程的两道硬门（`PLAN.md §6.5b`）**

| 回退 | 根因 | 新增硬门 |
|---|---|---|
| RB-01 | 异常偏小的数值（水平能耗占比 1.3–1.9%）被当成结论，实为单位量纲错误 | **G-A 异常值门**：与量级直觉偏离 >3 倍的数值须先做量纲两路互验 |
| RB-02 | 未核算代价即实现优化（Q3 中继合并） | **G-B 代价核算门**：优化提案在写实现代码前必须给出"收益 vs 代价"定量核算 |

> **G-B 的价值已被实测证明**：IT-13 花约 150 行代码 + 一轮回退才得出"合并净负"；
> IT-14 先核算，**零实现代码**即得出同类结论（Q2 合并代价 ≥ 收益）。

**账本统计**：N_raised **44** / N_open **0**、正负反馈比 **20:1**、回退 **2** 次、
A 类假设 **3** 条、不可判定项 **0** 个（P-22 已消除）、λ = 0.08（复核三条件均未触发回滚）。

---

## 六、需要你注意的三件事

1. **论文 PDF 已生成**，位置为 `提交/论文/论文.pdf`；源稿为 `paper/main.md`，打印版 HTML 为 `paper/export/main.html`。

2. **论文 §2.2 的 3 条运行前提（C-1/C-2/C-3）是我做的裁决**，尤其 **C-2「K 个任务组并行执行」**
   ——若应为串行，Q4 的资源需求会显著下降。

3. **论文 §7.5 的 7 条题面歧义表**是我替你做的裁决，其中 **AMB-3**（模板要"悬停海拔"而题面
   约束是"离地高度"）与 **AMB-4**（期望送达时间的约束强度）影响最大。若你的理解不同，
   Q2/Q3 的结果会变。

---

## 七、目录结构

```
.
├── run_all.sh                 一键复现
├── README.md                  本文件
├── PLAN.md                    自我迭代方案（六阶段 + 迭代引擎 + G-A/G-B 硬门）
├── 山区洪涝灾害下无人机运输与通信协同优化.docx   题面
├── 结果提交模板.xlsx           提交模板
├── 数据/                       附件数据（DEM / 地理 / 基础参数）
├── code/                       求解与工具（18 个模块）
├── spec/                       口径契约与治理（9 份）
├── tests/                      验证套件（8 个）
├── reports/                    阶段报告（11 份）
├── out/                        结果输出（6 张提交表 + 分析表）
└── paper/                      论文与图表
    ├── main.md
    ├── figs/                   fig1..fig5.png
    └── export/                 main.html + *.docx
```
