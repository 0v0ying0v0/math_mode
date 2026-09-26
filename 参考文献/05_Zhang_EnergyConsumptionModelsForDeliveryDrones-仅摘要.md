# [5] Energy Consumption Models for Delivery Drones: A Comparison and Assessment

**获取状态：仅摘要（出版社全文付费墙，无合法开放获取全文）**

- 作者：Juan Zhang, James F. Campbell, Donald C. Sweeney II, Andrea C. Hupman（均属 University of Missouri–St. Louis）
- 期刊：Transportation Research Part D: Transport and Environment, 2021, 90: 102668
- DOI: 10.1016/j.trd.2020.102668
- 出版社：Elsevier BV
- 类型：Review（Semantic Scholar 标注 publicationTypes = Review）

## 开放获取核查结论

| 渠道 | 结果 |
|---|---|
| OpenAlex | `is_oa: false`, `oa_status: closed`, `oa_url: null`, `any_repository_has_fulltext: false` |
| Semantic Scholar | `openAccessPdf.status: CLOSED`, url 为空 |
| Unpaywall（经 S2 转述） | 无 OA 位置 |
| Crossref | 仅 Elsevier TDM 授权（`elsevier.com/tdm/userlicense/1.0/`），无 OA license |
| ScienceDirect | HTTP 403（Cloudflare 人机校验），摘要页亦不可读 |
| UMSL 机构库 IRIS | 期刊本文无存档 |
| ResearchGate / 其他 | 无公开合法副本 |

**未使用 sci-hub 类站点。未绕过付费墙。**

## 摘要（全文照录）

> Energy consumption is a critical constraint for drone delivery operations to achieve their full potential of providing fast delivery, reducing cost, and cutting emissions. This paper provides a uniform framework to facilitate understanding different drone energy consumption models and the inter-relationships between key factors and performance measures to facilitate decision making for drone delivery operations. We review, classify and assess drone energy consumption models. We then document the very wide variations in the modeled energy consumption rates resulting from differences in: (1) the scopes and features of the models; (2) the specific designs of the drones; and (3) the details of their assumed operations and uses. The results show that great care must be taken in adopting a particular drone energy consumption model and that more research is needed, especially empirical research, to ensure the selected model accurately reflects delivery drone designs and uses.

来源：Semantic Scholar Graph API（`paperId 2d5c6f0acd429a7f056167947c2f054ddec4ed8d`）与 TRID 记录 01765234，二者一致。

## 关键词 / 主题词

**作者关键词（期刊版）**：未能获取（出版社页面不可达；OpenAlex 的 keywords 字段为算法自动生成，非作者关键词）。
**TRID 索引词（TRT Terms，供参考）**：Alternatives analysis; Delivery vehicles; Drones; Energy consumption; Evaluation and assessment; Mathematical models; Performance measurement
**TRID 主题领域**：Aviation; Energy; Freight Transportation; Vehicles and Equipment
**OpenAlex 主题**：UAV Applications and Optimization; Air Traffic Management and Optimization; Vehicle Routing Optimization Methods

## 章节标题结构

期刊版编号未能直接取得。以下为**同源学位论文**（且作者在文中明确声明"An article based on this chapter has been published as ... (Zhang et al. 2021)"）的章节结构，几乎可直接映射期刊版：

### 学位论文 Chapter 3: Energy Consumption Models of Delivery Drones（pp. 32–80）

| 学位论文章节 | 标题 | 对应期刊版（推断） |
|---|---|---|
| 3.1 | Introduction（含 4 条 "key contributions"） | 1 Introduction |
| 3.2 | Classifications of Drone Energy Consumption Models | 2 Background / Classification |
| 3.2.1 | Key Factors Affecting Drone Energy Consumption（4 大类因素；Figure 3.1、3.2） | 2.x |
| 3.2.2 | Distinguishing Features of Drone Energy Consumption Models（Table 3.1，12 个模型分类矩阵） | 2.x |
| 3.2.3 | Energy Models Using Component Approaches | 2.x |
| 3.2.4 | Energy Models Using Other Approaches | 2.x |
| 3.3 | Theoretical Models for Energy Consumption（Table 3.2 统一符号表） | 3 Key models with unified notation |
| 3.3.1 | Unified Notation | 3.x |
| 3.3.2 | Energy Models Using Integrated Approaches | 3.x |
| 3.3.3 | Energy Models Using Component Approaches | 3.x |
| 3.4 | Analysis and Results（Table 3.3/3.4/3.5；Figure 3.3–3.11） | 4 Analysis and results |
| 3.5 | Discussions and Insights | 5 Insights and implications |
| 3.6 | Conclusions | 6 Concluding remarks |
| Appendix 3.A / 3.B / 3.C | 模型推导附录（3.C 讨论 Zeng & Zhang 的通信类无人机功率模型） | Appendix |

### 该章核心内容（据同源学位论文全文）

- **四类影响因素**：drone design / environment / drone dynamics / delivery operations（Figure 3.1 改编自 Demir et al. 2014 的道路运输框架）
- **五个相互关联量**（Figure 3.2）：payload weight、battery weight、drone (airframe) weight、airspeed、range
- **Table 3.1**：12 个关键能耗模型的分类矩阵，按"水平飞行推力假设"分三派（① 推力=阻力，用升阻比；② 推力=重力，即悬停式；③ 推力=重力+阻力+升力，即分量式）；再按涵盖的飞行段（水平/悬停/垂直）、是否对风/航电/空载返航做修正、模型类型（10 个理论模型、4 个回归模型、5 个含实测）分类
- **Table 3.2**：统一符号表（约 25 个符号，含 air density、airspeed、headwind ratio、lift-to-drag ratio、power transfer efficiency、battery charging efficiency、component index i=1/2/3、specific energy、safety factor、energy per unit distance 等）
- **Table 3.5**：在"公共设置"（common setting）下对比 5 种建模路径：LD（升阻比积分式）、RH（仅悬停）、R2（双分量）、R3（三分量）、LR（回归式）
- **Figure 3.3–3.11**：能耗率/航程 对 载荷/空速 的四组配对图 × 小型机与大型机两类平台
- **核心结论**：12 个模型的能耗率（Epm, J/m）在公共设置下仍相差 **3–5 倍甚至更多**；完整飞行剖面（起降+悬停+爬升）比仅稳态平飞的 Epm 高 **100% 以上**；升阻比与功率传输效率是最关键且最难确定的参数

## 补充获取：同源学位论文（合法开放获取）

- 文件：`05_Zhang_UMSL学位论文-EconomicAndEnvironmentalImpactsOfDroneDelivery.pdf`
- 题名：*Economic and Environmental Impacts of Drone Delivery*，作者 Juan Zhang，University of Missouri–St. Louis，311 页
- 来源：UMSL 机构库 IRIS（`https://irl.umsl.edu/cgi/viewcontent.cgi?article=2078&context=dissertation`）
- 关系：其 **Chapter 3** 即本期刊论文的同源版本，作者在章首明示

> **合规声明**：学位论文为机构库合法开放获取资源，与期刊版属**同源但不同的出版物**。本文以之作为期刊版的**结构参考**，不作为期刊版的替代全文引用。
