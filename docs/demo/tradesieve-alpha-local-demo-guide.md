# TradeSieve Alpha 本地演示与接入指南

**验证日期：** 2026-08-12
**适用版本：** `0.1.0-alpha.6` 及当前 `main`
**对象：** 业务负责人、合规/法务、CRM/OMS 集成开发者、CLI/Agent 使用者
**性质：** 可运行的技术原型与接入说明，不是法律意见、自动法律判断或生产放行系统。

## 1. 先看结论

TradeSieve 当前已经能演示一条真实运行链路：

1. 受控任务获取并校验 EU FSF、EU Annex I、OFAC SDN、OFAC Consolidated 四个官方来源；
2. 四源作为一个不可变 bundle 原子激活，业务请求不会混用不同刷新批次；
3. 合成 CRM、认证 REST API、CLI 和只读 MCP 读取同一个活跃 bundle；
4. 返回官方证据定位、来源/规则版本、缺失事实及 `HOLD`、`REQUEST_EVIDENCE` 或 `MONITOR`；
5. 来源不存在、过期、损坏或服务不可用时失败关闭，业务系统不得隐式通过；
6. 自动化只能收紧控制，只有获得授权的人可以作有期限、有证据、有理由的最终放行决定。

本项目的核心产品约束是：重点不是维护一张静态名单，而是让**有来源、有版本、可判断是否仍然有效的事实**进入报价、订单、订舱或付款节点，并与日常系统持续互动。本文使用“当前能力”“接入方法”和“后续边界”等准确措辞，不扩大法律解释、组织流程或生产部署承诺。

## 2. 演示中什么是真的，什么是模拟的

| 内容 | 属性 | 说明 |
| --- | --- | --- |
| 中国国际物流询报价、客户、路线、金额、负责人、单证 | 合成 | 5 条仓库自带 fixture，结构接近中国货代销售和订舱流程，不对应真实交易 |
| 首条 CRM 记录的名单名称 | 公开验收样本 | 使用 EU FSF 中公开企业别名，目的是稳定证明名单候选链路，不表示存在客户关系 |
| EU FSF、EU Annex I、OFAC SDN、OFAC Consolidated | 运行时获取的官方数据 | 原始字节、哈希、投影和激活记录进入本地 PostgreSQL；页面结果不是预置 JSON |
| BIS Common High Priority List（CHPL）50 个 HS-6 | 版本化指导性 fixture | 2026-08-11 与 BIS 官方发布核对；命中仅触发增强尽调候选，不是分类、禁止或许可结论 |
| 二次电芯 380 Wh/kg、20°C 等参数 | 合成技术事实 | 用于证明来源绑定的 `3A001.e.1` 数字阈值比较，不代表真实产品规格或正式归类 |
| 本地运行结果 | 真实执行结果 | 本文记录的是 2026-08-11 在隔离 Compose 环境中实际刷新、调用和截图的结果 |

不得将截图、响应、fixture 或本指南用于客户尽调、银行沟通、许可证申请或法律意见。

## 3. 本次本地验证结果

本次演示使用独立 Compose project `tradesieve-walkthrough`，宿主端口 `18080`。四源刷新成功并返回 `APPLIED`：

| 来源 | 本次安全计数 |
| --- | ---: |
| EU FSF | 6,234 个主体、31,053 个别名、3,007 个标识符 |
| EU Annex I | 384 个控制条目 |
| OFAC SDN | 19,199 个条目、24,576 个别名、53,473 个标识符 |
| OFAC Consolidated | 481 个条目、1,186 个别名、2,632 个标识符 |

活跃 bundle：`official-bundle-29ecef0561b3...`。健康检查的数据库、迁移、必需来源覆盖、来源快照证据和规则覆盖均为 `OK`。同一输入经认证 REST 和 `screen-active` CLI 调用，均返回相同 bundle、`RED` 和 `HOLD`。

计数和哈希只能证明本次本地演示读取的版本；它们不会自动证明任何具体交易可做，也不应被长期复制为“最新名单”。

## 4. 本地启动与演示

前提：Docker Desktop 或兼容的 Docker Engine/Compose，且本机能访问列明的官方发布站点。

### 4.1 在默认端口启动

```bash
git clone git@github.com:fyaic/TradeSieve.git
cd TradeSieve
git checkout v0.1.0-alpha.6
./scripts/start_business_demo.sh
```

打开：

```text
http://127.0.0.1:8080/demo/crm
```

如需像本文一样使用隔离名称与端口：

```bash
TRADESIEVE_DEMO_PROJECT=tradesieve-walkthrough \
TRADESIEVE_HOST_PORT=18080 \
./scripts/start_business_demo.sh
```

打开 `http://127.0.0.1:18080/demo/crm`。

### 4.2 操作顺序

1. 选择左侧一条询报价；
2. 核对运输、货物、金额、单证和“发送给 TradeSieve 的边界”；
3. 点击“发起合规审查”；
4. 查看信号灯和业务动作；
5. 查看官方来源候选、Annex I/技术比较、CHPL 候选及缺失事实；
6. 核对 `source_bundle_id`、snapshot ID 和证据定位；
7. CRM 只保存必要引用和业务动作，不复制内部名单、规则、相似度特征或复核笔记。

以下图片均为无损原图；在 GitHub 中点击图片可查看完整分辨率。

[![CRM 总览：5 条合成中国国际物流询报价与独立 TradeSieve 门禁](assets/tradesieve-alpha-local-demo-guide/01-crm-overview.png)](assets/tradesieve-alpha-local-demo-guide/01-crm-overview.png)

## 5. 五个合成场景说明

| 报价 | 主要输入 | 2026-08-11 实际表现 | 正确业务理解 |
| --- | --- | --- | --- |
| `HZ-260810-0047` 上海→莫斯科 | 公开 EU FSF 企业别名；`3A001` 候选；技术规格缺失 | `RED/HOLD`；1 个 EU 名称候选；`TECHNICAL_REVIEW_REQUIRED` | 暂停并交人工核实身份、归类和规格；不是“确定受制裁”法律结论 |
| `HZ-260810-0039` 深圳→法兰克福 | `3A001` 候选；合成二次电芯参数 380 Wh/kg、20°C | `RED/HOLD`；`3A001.e.1` 为 `MATCHED` | 证明有限规则分支的数值比较，不能代替完整技术归类 |
| `HZ-260809-0186` 宁波→杰贝阿里、后续未知 | HS-6 `850440`；最终用户/用途/路线不完整 | `YELLOW/REQUEST_EVIDENCE`；CHPL `TIER_3_A` | 补充制造商型号、技术规格、最终用户、最终用途和路线 |
| `HZ-260810-0052` 青岛→鹿特丹 | HS-6 `400921`；未给 Annex I 归类 | `YELLOW/REQUEST_EVIDENCE`；CHPL 无候选 | CHPL 无候选也不是绿灯，仍需合格归类 |
| `HZ-260808-0124` 上海→伊斯坦布尔 | 精密传感器；技术参数和正式归类缺失 | `YELLOW/REQUEST_EVIDENCE` | 先补归类和技术事实；当前引擎不判断完整路线/最终用途法律效果 |

### 5.1 制裁名单候选触发业务拦截

[![公开 EU FSF 别名候选、证据定位与 RED/HOLD](assets/tradesieve-alpha-local-demo-guide/02-red-hold-official-match.png)](assets/tradesieve-alpha-local-demo-guide/02-red-hold-official-match.png)

页面展示的是**官方别名候选**和原生 XML 定位，不是“系统已认定交易违法”。当前名称能力只做 Unicode 规范化后的精确候选；模糊匹配、音译、词序变化、股权/控制传播和 OFAC 50 Percent Rule 尚未实现，所以候选要人工复核，无候选也不能自动放行。

### 5.2 两用物项有限技术规则

[![来源绑定的 3A001.e.1 二次电芯参数比较](assets/tradesieve-alpha-local-demo-guide/03-dual-use-threshold-match.png)](assets/tradesieve-alpha-local-demo-guide/03-dual-use-threshold-match.png)

该场景将经标记为“已审核”的合成结构化参数传给有限规则包，显示 20°C 与 380 Wh/kg 的比较和规则版本。它证明系统可以产生可追溯、可重放的确定性结果；它不证明 TradeSieve 能从货描或 HS 自动推导 `3A001`，也不覆盖 Annex I 的全部条目和注释。

### 5.3 CHPL 敏感货物候选转为补件动作

[![HS-6 850440 命中 CHPL Tier 3A 并列出补件项](assets/tradesieve-alpha-local-demo-guide/04-chpl-tier3a-request-evidence.png)](assets/tradesieve-alpha-local-demo-guide/04-chpl-tier3a-request-evidence.png)

### 5.4 REST、CLI 与 MCP 接口一致性

2026-08-12 使用同一个合成请求完成一轮本地复核：认证 REST、`screen-active`
CLI 和 `screen_transaction` MCP 工具都返回同一个活跃 bundle、`RED/HOLD` 和
`automatic_clearance=false`；MCP 无效输入被拒绝。

[![REST、CLI、MCP 同源实测摘要](assets/tradesieve-alpha-local-demo-guide/05-api-cli-mcp-parity.png)](assets/tradesieve-alpha-local-demo-guide/05-api-cli-mcp-parity.png)

结构化记录见 [接口一致性证据](../evidence/interface-parity-2026-08-12.json)。

Excel 中的 CHPL 分层字段在这里被用于候选提示，但只有经过官方页面核对、带日期和哈希的 50 项 fixture 可执行。表内占位主体、船舶、概述文字和未同步记录没有被当作官方事实导入。

## 6. 有 CRM/OMS：使用 REST 作为同步门禁

### 6.1 推荐边界

```text
CRM/OMS（客户、报价、订单、路线、单证）
    -> 规范字段 + proposed action
TradeSieve（来源、规则、证据、业务动作）
    -> HOLD / REQUEST_EVIDENCE / MONITOR + 版本与证据引用
CRM/OMS（执行拦截、补件、人工复核入口）
```

CRM 不内置制裁名单和法律规则。建议在报价放行、订单确认、订舱、装运、收付款等关键节点调用；关键字段变化后重新审查。当前 Alpha 的正式可调用垂直切片是：

```text
POST /v1/official-screenings
Authorization: Bearer <deployment-token>
Content-Type: application/json
```

最小映射：

| CRM 字段 | Alpha 请求字段 | 备注 |
| --- | --- | --- |
| 客户/供应商/收货人/最终用户名称 | `party_names[].name` | 当前只作精确规范化候选 |
| 登记号、IMO、税号、BIC 等 | `party_identifiers[]` | 仅有限、明确的标识类型可作强标识候选 |
| HS/CN/TARIC | `goods.hs_code` | 当前只取 HS-6 与 CHPL 候选比较 |
| 经合格人员给出的 Annex I 候选 | `goods.annex_i_code` | 不从 HS 或自由文本自动推导 |
| 技术规格事实 | `goods.technical_facts[]` | 必须有单位、证据引用和 verified 状态 |
| 报价/订单唯一号 | `X-Correlation-ID` 或调用方日志 | 当前技术预览尚无完整租户/案件持久化 |

调用示例：

```bash
curl --fail-with-body \
  -H 'Authorization: Bearer local_demo_only_official_screening_token' \
  -H 'Content-Type: application/json' \
  -H 'X-Correlation-ID: crm-quote-001' \
  --data-binary @examples/requests/official-screening.json \
  http://127.0.0.1:8080/v1/official-screenings
```

调用方必须保守处理：

- `RED/HOLD`：停止当前 proposed action 并进入授权人工复核；
- `YELLOW/REQUEST_EVIDENCE`：保持拦截，按 `missing_facts` 补件；
- `GREEN_CANDIDATE/MONITOR`：仅表示已实现检查未产生更强动作，不是放行指令；
- `401/403/409/422/424/429/503`、超时、空响应或无法解析：保持拦截并告警；
- 保存必要的 bundle/snapshot/result 引用；不要仅保存“命中/未命中”文本。

默认 demo token 只能本机验收。生产模式拒绝该摘要；真正接入前还需要独立服务身份、OIDC/租户/对象级授权、幂等、案件、审计、速率限制和签名 webhook。

## 7. 没有 CRM：直接使用 REST

没有 CRM 不影响调用同一个引擎。可以从脚本、Postman、内部表单或低代码工作流生成符合 OpenAPI/JSON Schema 的 JSON，然后调用上面的 REST 路由。

当前 API 不保存请求和案件，因此使用方必须在受控内部环境记录：

- 谁在什么时间提交了哪个业务对象；
- 输入文件或规范化请求的受控哈希；
- `source_bundle_id`、来源 snapshot、规则版本和结果哈希；
- 返回的业务动作、待补事实和后续人工记录；
- 凭据、客户信息和证据文件不得进入 Git、shell 历史或未经批准的外部模型。

如只需要单机演示，直接使用本指南的合成 JSON；不要用真实客户数据替换 fixture 后再截屏或提交仓库。

## 8. CLI：人工、批处理和 Agent 的共同入口

### 8.1 读取已激活的本地官方 bundle

```bash
docker compose run --rm -T --no-deps app \
  tradesieve-manage screen-active --request - \
  < examples/requests/official-screening.json
```

`screen-active` 使用 PostgreSQL 中最近激活且不超过 48 小时的完整 bundle。退出码 `0` 只表示命令成功执行，**不表示交易获准**；来源、数据库、解析或完整性失败返回 `2`，输入 JSON/契约错误返回 `3`。

### 8.2 联网诊断

```bash
uv run tradesieve-manage screen-official --request - \
  < examples/requests/official-screening.json
```

`screen-official` 每次重新获取官方发布，用于诊断，不代替受控刷新与原子激活。日常业务门禁应使用 `screen-active` 或 REST 技术预览。

### 8.3 Agent 通过 MCP 接入

Alpha 已发布本地 stdio `tradesieve-mcp`，当前只暴露一个只读
`screen_transaction` 工具。它直接复用 `screen-active` 和 REST 的同一应用服务，
不以 REST 结果冒充 MCP。先运行协议探针：

```bash
uv run --locked python scripts/mcp_stdio_probe.py \
  --project-name tradesieve-walkthrough \
  --request examples/requests/official-screening.json
```

探针会完成 MCP 初始化、列出唯一工具、验证只读/幂等注解、拒绝一个无效请求，
再对当前活跃 bundle 执行正式调用。本次实际结果为 `RED/HOLD`，
`automatic_clearance=false`，且绑定与 CLI/REST 相同的
`official-bundle-29ecef0561b3...`。独立 Codex 子 Agent 已通过这条 stdio 通道完成
同一验收，记录见 [Codex Agent MCP 演示](../evidence/codex-agent-mcp-demo-2026-08-11.md)。

Agent 必须做到：

1. JSON 从 stdin/受控临时文件输入，避免把敏感标识写进命令历史；
2. 解析 `signal`、`business_action`、证据和版本，不把自然语言总结当成权威结果；
3. `HOLD`、`REQUEST_EVIDENCE`、错误和超时都保持业务拦截；
4. Agent 可以解释、整理补件和请求人工复核，不能自动产生 `CLEARED` 或释放业务；
5. 保存 Agent 身份、命令/API 版本、参数哈希、来源/规则版本和结果哈希；
6. 不把生产客户、货物、付款、凭据或调查材料发送给未经批准的模型/工具。

可给 Agent 的工具说明应是：

```text
工具：TradeSieve MCP screen_transaction
输入：符合 tradesieve.contracts.v1 JSON Schema 的 JSON
输出：结构化筛查证据、source_bundle_id、signal、business_action
副作用：无业务放行副作用
错误策略：任何执行/来源/解析错误均返回“保持拦截并转人工”
禁止：把 GREEN_CANDIDATE/MONITOR 改写为 CLEARED 或“安全可做”
```

完整 Codex 配置和信任边界见 [MCP 与 Codex Agent 接入](../getting-started/mcp-agent.md)。
当前 stdio 进程继承启动者的服务身份，没有单独的最终用户认证；只能用于本地可信
Agent 宿主。远程 Streamable HTTP、OAuth/OIDC、租户/工具权限和案件工具尚未实现，
不得把本地 stdio 包装成公网生产端点。

## 9. 两个池子的现状

用户提出的“双池”理解是正确的，但当前成熟度不同：

### 9.1 当前有效信息池：已实现技术纵切

- 保存官方原始对象、内容哈希、投影、发布时间/有效时间和访问时间；
- EU FSF、EU Annex I、OFAC SDN、OFAC Consolidated 四源一次性激活；
- 完整性/新鲜度检查失败则拒绝提供业务判断；
- CLI、REST、MCP 和 CRM 读取同一活跃 bundle；
- CHPL 是独立标记的指导性版本 fixture，不伪装成制裁名单。

### 9.2 内部历史经验池：设计已记录，尚未形成可执行闭环

Excel、历史聊天、同行提示和案件经验未来应保存为带来源、时间、作者/责任人、有效期、复核状态、证据哈希和 supersedes 关系的“有归属断言”。它们必须与官方事实分层，未经复核不能直接触发正式名单结论。

建议最小字段：

```text
assertion_id, source_document_id, source_locator, recorded_at,
subject_or_goods_ref, assertion_type, asserted_value, fact_class,
confidence_method, reviewer_status, valid_from, valid_to,
supersedes, evidence_hash, notes
```

当前 Alpha 没有对外声称已完成 Excel 自动导入、历史案件库、群聊 Agent 常驻采集或内部断言复核工作流。这是清楚的产品边界，不影响当前官方来源纵切的演示价值。

## 10. 外部服务与出站依赖

普通 CRM、REST、CLI 和 MCP 审查只读取部署内 PostgreSQL，不访问外网，也不把
客户订单数据上传到官方网站或第三方服务。只有 `refresh-official-sources` 和显式
联网诊断访问以下官方发布基础设施：

- `data.europa.eu` 与 `webgate.ec.europa.eu`：EU FSF 发现与 XML；
- `publications.europa.eu`：固定 CELEX `32025R2003` Annex I Formex；
- `sanctionslistservice.ofac.treas.gov` 及其受限 AWS GovCloud 签名下载地址：
  OFAC SDN/Consolidated XML。

BIS CHPL 在当前版本中是有访问日期和内容哈希的内置快照，不会运行时自动联网。
项目不依赖商业名单 API、托管 LLM 或向量数据库。任一动态源刷新失败都保留旧活跃
指针；任一业务审查遇到缺失、损坏或超过 48 小时的 bundle 都失败关闭。完整主机、
数据流、许可、构建依赖和生产替代方案见
[外部服务与出站依赖](../operations/external-dependencies.md)。

## 11. 当前边界与不可作出的承诺

当前未实现：

- 名称模糊/音译/词序匹配和完整实体解析；
- 股权、所有权/控制传播及 OFAC 50 Percent Rule；
- 俄罗斯 Regulation 833/2014 全部货物附件、路线、最终用途、catch-all、金融和服务限制；
- Annex I 全量可执行技术规则及自动正式归类；
- 许可证、例外、法域适用性或“交易合法/违法”的自动结论；
- 生产级租户/OIDC/对象级权限、案件持久化、证据提交、人工决定、签名 webhook；
- 远程 MCP Streamable HTTP、OAuth/OIDC 和案件/复核工具；
- 企业级 SLA、持续监控、数据许可评估和正式运维责任分配。

因此：

- 名单候选不是身份认定；
- CHPL 命中不是受控分类，未命中不是安全证明；
- `MATCHED/NOT_MATCHED` 只描述被执行的有限技术规则分支；
- `MONITOR` 不是释放指令；
- TradeSieve 是独立的决策支持控制面，不取代法务、合规、出口管制分类人员或获得授权的最终决定人。

## 12. 演示结束与清理

默认项目：

```bash
docker compose --env-file .env.example -p tradesieve-demo \
  down --volumes --remove-orphans
```

本文隔离项目：

```bash
docker compose --env-file .env.example -p tradesieve-walkthrough \
  down --volumes --remove-orphans
```

该操作会删除 demo PostgreSQL 和原始对象命名卷。不要把试点或生产证据放在此参考栈。

## 12. 进一步阅读

- [原始需求](../requirements/original-request.md)
- [用户提供的俄罗斯制裁 Excel 评估](../research/user-workbook-assessment.md)
- [合成 CRM 说明](../getting-started/demo-crm.md)
- [四源刷新、CLI 与 REST 说明](../getting-started/official-screening-cli.md)
- [原型发布与分发](../getting-started/prototype-release.md)
- [集成设计](../architecture/integration-design.md)
- [Agent 接口原则](../architecture/agent-interface-principles.md)
- [版本化 OpenAPI](../../api/openapi/tradesieve.v1.json)
- [共享 JSON Schema](../../api/schemas/tradesieve.contracts.v1.json)
