# TradeSieve

> 面向制裁、出口管制与敏感交易审查的独立合规决策支持服务。

[![Status: 0.1 alpha prototype](https://img.shields.io/badge/status-0.1%20alpha%20prototype-6f42c1)](#项目进度)
[![Runtime: Docker Compose](https://img.shields.io/badge/runtime-Docker%20Compose-2496ed)](docs/getting-started/docker-reference.md)
[![Decision: Human clearance](https://img.shields.io/badge/decision-human%20clearance%20only-b7791f)](docs/decisions/0002-human-clearance-only.md)
[![Interfaces: REST CLI MCP](https://img.shields.io/badge/interfaces-REST%20%7C%20CLI%20%7C%20MCP-0969da)](docs/architecture/agent-interface-principles.md)

TradeSieve 的目标是在 CRM、订单、订舱、运输和支付系统旁边建立一道独立、可审计、可替换的合规控制边界。业务系统仍然是业务数据的主系统；TradeSieve 负责接收一次待审查动作，固定输入和证据版本，执行确定性规则，保存审查轨迹，并在不确定、资料缺失或命中风险时给出 `HOLD` / 人工复核要求。

项目起源于一个真实问题：国际物流企业因俄罗斯相关业务受到连带制裁影响，需要把受制裁主体、受限国家/地区、两用物项、敏感货物、路线和交易风险的审核能力，从零散人工查询升级为可嵌入业务流程的独立服务，同时保持与现有 CRM/OMS 解耦，并为传统 API、CLI 和 AI Agent/MCP 保留同一套契约。

> TradeSieve 是决策支持和证据编排工具，不是法律意见，也不会自动宣布一笔交易“合法”“无制裁风险”或“可以放行”。最终放行必须由获得授权的人作出。

## 它准备解决什么问题

- 在客户准入、询报价、下单、订舱、出运和付款前形成统一审查入口；
- 对交易方、所有权与控制、货物、路线、最终用途、支付和单证进行结构化检查；
- 保存所用数据源、规则、输入、模型和人工决定的精确版本，支持重放与审计；
- 将“确定命中”“可能命中”“资料不足”“服务不可用”明确区分，默认失败关闭；
- 让 CRM/OMS 通过 REST/事件接入，让运营人员通过 CLI 使用，让授权 Agent 通过 MCP 使用；
- 将外部制裁数据、规则引擎和业务系统隔离开，避免把供应商或单一模型锁进核心流程。

## 系统边界

```mermaid
flowchart LR
    B["CRM / OMS / 订舱 / 支付系统"]
    U["运营与合规人员"]
    A["授权 AI Agent"]
    I["接口层：REST / CLI / MCP（已实现切片）/ Webhook（目标）"]
    S["TradeSieve 应用服务"]
    E["确定性审查、证据与案件状态"]
    H["授权人工复核与放行"]
    D[("PostgreSQL：不可变审计与状态")]
    O[("私有对象存储：原始来源证据")]

    B --> I
    U --> I
    A --> I
    I --> S
    S --> E
    E --> H
    S --> D
    S --> O
    H --> D
```

接口层不会拥有另一套业务规则。领域模型和应用契约是唯一事实来源，REST/OpenAPI、JSON Schema、CLI、MCP 和事件适配器共享它们。

### 两个知识池，不混淆权威

| 知识边界 | 当前用途 | 当前实现状态 |
| --- | --- | --- |
| 官方/实时来源池 | 保存监管机构原始发布、规范化投影、版本和新鲜度；每次实时审查只读取一个完整活跃 bundle | EU FSF、EU Annex I、OFAC SDN、OFAC Consolidated 已实现原子激活和失败关闭；BIS CHPL 以明确版本的 50 个 HS-6 指导候选接入 |
| 内部案件/经验池 | 保存历史交互、人工标记、Excel/聊天调研、finding、证据、决定、失效与重筛关系 | 通用快照、受理和审计基础已存在；任意内部资料导入、案件复核和经验检索尚未闭环 |

内部访谈记录和《对俄制裁名单-调研补充版.xlsx》可作为第二个池的有来源、可复核输入，但不会因为被收集到表格中就冒充监管机构清单。两个池可以在未来同一应用服务中联合查询，必须保留不同的来源、权限、保留期和政策效果；历史经验尤其不能自动产生法律放行。

## 项目进度

**当前阶段：`0.1.0a4` 高保真工程原型。EU FSF、EU Annex I、OFAC SDN 和 OFAC Consolidated 已能经过验证后作为一个四源 bundle 原子写入 PostgreSQL，并由 CLI、认证 REST、只读 MCP 和合成 CRM 调用同一个活跃版本审查服务；BIS CHPL 的 50 个 HS-6 候选和 `3A001` 首批来源绑定技术参数规则已接入。完整案件、人工作业、远程 MCP/OAuth、所有权/控制传播和俄罗斯专项货物/路线法律效果仍未完成。**

截至 2026-08-11，进度如下：

| 能力 | 状态 | 当前结果 |
| --- | --- | --- |
| 工程基础与 CI | ✅ 已完成 | 锁定 Python/依赖、架构边界、构建、文档、契约、秘密和依赖审计门禁 |
| Docker 参考部署 | ✅ 已完成 | 非 root、只读容器；PostgreSQL 18.4；app/worker；内部网络；持久卷 |
| 身份、租户与授权 | ✅ 已完成 | deny-by-default、对象级授权、不可变授权审计 |
| 数据源注册与就绪度 | ✅ 已完成 | 版本化来源清单、时效检查、fail-closed readiness |
| 规则包治理 | ✅ 已完成 | 有版本、有引用、可审批/激活/回滚的合成规则包 |
| 原始来源快照 | ✅ 已完成 | 私有不可变对象、解析/验证证据、双人治理、PostgreSQL 持久化 |
| 规范化受理与幂等 | ✅ 合成演示可用 | 严格 canonical intake、授权范围幂等、原子审计/outbox、PostgreSQL 18.4 持久化和 demo-only 运行入口；见 [TS-302](https://github.com/fyaic/TradeSieve/issues/21) / [PR #36](https://github.com/fyaic/TradeSieve/pull/36) |
| 合成物流 CRM 门禁 | ✅ 原型可演示 | 5 条中国货代风格合成询报价；页面调用 PostgreSQL 活跃官方来源。名单场景验证公开 FSF 别名，`850440` 场景显示 BIS Tier 3A 候选，高能量密度二次电芯执行真实 `3A001.e.1` 数字阈值；见 [综合演示与接入指南](docs/demo/tradesieve-alpha-local-demo-guide.md) |
| EU 官方制裁名单 | 🧪 活跃版本可用 | 经 data.europa.eu 发现当前 FSF XML；有界 gzip/identity 下载、安全解析、原始哈希、6,234 主体/31,053 别名/3,007 标识投影、不可变激活、强标识精确匹配和官方别名候选；尚无模糊/音译/所有权匹配 |
| EU 两用物项 Annex I | 🧪 活跃版本 + 首批技术断言 | 从 Publications Office CELLAR 读取 `32025R2003` 官方 Formex 附件并持久化 384 个控制条目；首批手工复核规则覆盖 `3A001.a.5.a`、`.a.14`、`.e.1` 的 ADC/电芯数字阈值，绑定精确条目哈希；不从 HS 或货描自动归类 |
| BIS 高优先级物项 CHPL | ✅ 原型候选提示 | 2026-08-11 复核官方 50 项 HS-6 指导清单并绑定内容哈希；精确候选返回 tier 和待补证据，只触发增强尽调，不声明受控、禁止或放行 |
| OFAC SLS 制裁名单 | 🧪 活跃版本可用 | 固定官方 SDN/Consolidated XML 入口、严格 GovCloud 跳转、安全解析、稳定 UID/哈希差分和来源专用不可变投影；精确强标识/名称候选已进入 CLI、REST、CRM；尚无 50 Percent Rule、所有权/控制传播或 program 法律效果引擎 |
| 官方来源刷新与审查 CLI | ✅ 可运行 | `refresh-official-sources` 原子激活同一 FSF/Annex I/OFAC SDN/OFAC Consolidated bundle；`screen-active` 只使用 48 小时内的完整活跃版本；`screen-official` 保留为逐次联网诊断入口 |
| 持久化来源投影与激活 | ✅ 四源垂直切片完成 | migration `20260810_0007` 在保留 0006 EU 历史证据的同时加入 OFAC 来源专用投影；新激活必须四源完整，重复刷新幂等，缺失/损坏/陈旧时失败关闭 |
| 完整确定性审查与证据 | ⏳ 进行中 | 官方别名精确规范化候选和首批 `3A001` 技术事实比较已接入；完整 Annex I 规则、模糊/音译实体解析、所有权/控制、俄罗斯 `833/2014`、路线/最终用途/catch-all 仍待接入 |
| 案件/发现项/处置状态 | ⏳ 计划中 | 结构化 finding、evidence、hold 和人工决定 |
| Screening REST API | 🧪 官方来源技术预览 | `POST /v1/official-screenings` 使用部署级 Bearer token、1 MiB JSON 上限和活跃来源服务；尚无正式 OIDC/租户授权、幂等案件受理、查询和 webhook |
| Screening CLI 与 MCP | 🧪 可运行切片 | `screen-active` 与本地 stdio `screen_transaction` 调用同一活跃来源服务；MCP 仅一个只读工具，远程 Streamable HTTP/OAuth 与案件工具尚未实现 |
| Webhook 与可观测性 | ⏳ 计划中 | 签名事件、脱敏日志/指标/链路 |

当前分支已通过 2,081 个仓库测试和 100% 语句/分支覆盖率，以及独立 PostgreSQL 18.4、完整 Compose 和联网官方来源门禁。migration `0007` 从空库完成四源激活/幂等/完整回读/不可变约束，也证明 0006 中已有 EU 活跃证据升级后逐条保留、但因缺少 OFAC 会失败关闭。2026-08-11 联网端到端验收解析并持久化 EU FSF 6,234 个主体、Annex I 384 个控制条目、OFAC SDN 19,199 条和 Consolidated 481 条；重复刷新返回 `IDEMPOTENT`。同一活跃 bundle 经 CLI、认证 REST 与 MCP 返回一致结果，公开 SOVCOMFLOT 样本产生带 `RUSSIA-EO14024` program 证据的 `RED/HOLD`，合成 CRM 也绑定相同四源 snapshot，并能识别 CHPL 候选及对有限 `3A001` 产品族执行来源哈希绑定的技术阈值比较。这些证据证明当前代码边界和来源可达性，不代表全球名单覆盖、法律正确率或生产可用性。

详细范围见 [MVP 定义](docs/product/mvp-scope.md)、[Phase 1 计划](docs/delivery/phase-1-plan.md) 和 [敏捷 backlog](docs/delivery/phase-1-backlog.md)。

## 现在可以怎样使用

当前主路径是“四份正式来源刷新并原子激活 → CLI/REST/MCP/CRM 读取同一活跃 bundle → 识别 CHPL HS-6 敏感候选 → 对显式合格 Annex I 候选执行来源绑定技术断言 → 返回保守业务动作和可追溯证据”。合成 CRM 只提供测试交易，名单和 Annex I 证据来自真实活跃官方版本；CHPL 是截至标明核验日期的内置指导快照，不会冒充动态法律清单。该垂直切片仍是原型：不持久化任意 REST/MCP 请求、结果或案件，也没有正式 OIDC、模糊/音译实体解析、OFAC 50 Percent Rule、完整 Annex I/俄罗斯专项货物控制或远程 MCP/OAuth。

### 1. 启动参考环境

前提：Docker Desktop 或兼容的 Docker Engine/Compose，以及本私有仓库的访问权限。

```bash
git clone git@github.com:fyaic/TradeSieve.git
cd TradeSieve
cp .env.example .env
docker compose up -d --build --wait
docker compose ps
```

确认进程存活与业务依赖就绪：

```bash
curl --fail http://127.0.0.1:8080/health/live
curl --fail http://127.0.0.1:8080/health/ready
```

`/health/live` 只表示 HTTP 进程可响应；`/health/ready` 还会核验数据库迁移、来源清单、私有快照证据和已激活规则包。HTTP `200` 仅表示服务具备运行条件，绝不表示任何交易已获放行。

### 2. 刷新并激活真实官方来源

参考栈启动后，执行一次运维刷新：

```bash
docker compose run --rm --no-deps app \
  python -m tradesieve.manage refresh-official-sources
```

命令会发现并下载当前 EU FSF、固定审核版本 `32025R2003` Annex I、OFAC SDN 和 OFAC Consolidated，验证原始字节和投影完整性，再把四者作为一个 bundle 原子激活。第一次成功返回 `APPLIED`；同一四源版本再次刷新返回 `IDEMPOTENT`。任一下载、格式、哈希、计数、数据库约束或提交失败都会退出 `2`，旧活跃版本不被半更新覆盖。

### 3. 使用活跃版本执行审查

准备一个本地 JSON 文件；也可以使用 `--request -` 从有界标准输入读取，避免把主体标识写进 shell 历史：

```json
{
  "schema_version": "1.0.0",
  "party_identifiers": [
    {"type": "regnumber", "value": "待审查登记号", "country": "RU"}
  ],
  "party_names": [
    {"name": "待审查客户、承运人或最终用户名称"}
  ],
  "goods": {
    "hs_code": "854231",
    "annex_i_code": "3A001",
    "classification_verified": true,
    "technical_specification_available": true,
    "product_family": "ELECTROCHEMICAL_CELL",
    "technical_facts": [
      {"fact_id": "is_battery", "unit": "BOOLEAN", "boolean_value": false, "evidence_ref": "datasheet-1", "verified": true},
      {"fact_id": "cell_type", "unit": "CELL_TYPE", "text_value": "SECONDARY", "evidence_ref": "datasheet-1", "verified": true},
      {"fact_id": "energy_density_wh_per_kg", "unit": "WH_PER_KG", "numeric_value": "380", "evidence_ref": "datasheet-1", "verified": true},
      {"fact_id": "measurement_temperature_celsius", "unit": "CELSIUS", "numeric_value": "20", "evidence_ref": "datasheet-1", "verified": true}
    ]
  }
}
```

保存为 `screening.json` 后执行：

```bash
docker compose exec -T app \
  tradesieve-manage screen-active --request - < screening.json
```

该命令只读取 PostgreSQL 中完整、未损坏且不超过 48 小时的活跃 bundle，并在一个结果中返回：

1. bundle ID、激活时间及内容哈希；
2. EU FSF 版本、原始文件哈希、强标识/名称候选状态和原生定位；
3. OFAC SDN/Consolidated 的独立版本、program、强标识/名称候选和原生定位；
4. Annex I CELEX、控制号命中、条目哈希、原生定位和缺失事实；
5. CHPL 的 HS-6 精确候选状态、tier、指导来源哈希和待补事实；
6. 已支持分支的技术规则 ID/版本、来源条目哈希、逐项单位化比较和证据引用；
7. `HOLD` / `REQUEST_EVIDENCE` / `MONITOR`，且 `automatic_clearance` 永远为 `false`。

名称候选不是精确身份结论：EU 或 OFAC 任一候选都会返回 `RED/HOLD` 等待授权人员复核；当前尚不支持模糊相似、音译、词序变体、所有权/控制传播或 OFAC 50 Percent Rule。`hs_code` 只用于 CHPL 精确候选提示；输入的 `annex_i_code` 必须来自合格的归类过程，TradeSieve 不会从 HS/CN/TARIC 或货描自动推导正式控制号。结构化技术事实只对已支持的规则分支作 `MATCHED/NOT_MATCHED/INCOMPLETE` 比较；它既不是完整归类也不是放行。即使所有已实现检查均无候选，结果也只是 `GREEN_CANDIDATE/MONITOR`。活跃来源不存在、陈旧、损坏，或活跃条文哈希与已批准规则不一致时失败关闭。完整说明见 [官方来源使用指南](docs/getting-started/official-screening-cli.md)。

### 4. 从 CRM/OMS 调用认证 REST 技术预览

demo 环境的明文 token 仅用于本机验收；服务配置只保存其 SHA-256。生产模式拒绝该默认摘要，必须换成独立生成的部署凭据。

```bash
curl --fail-with-body \
  -H 'Authorization: Bearer local_demo_only_official_screening_token' \
  -H 'Content-Type: application/json' \
  --data-binary @screening.json \
  http://127.0.0.1:8080/v1/official-screenings
```

未认证请求在业务正文解析前返回 `401`；非 JSON、无 `Content-Length`、超过 1 MiB 或契约无效的请求被拒绝；没有新鲜活跃来源时返回安全的 `503`，调用方必须保持业务拦截。这个 endpoint 是官方来源垂直切片，不等同于仍在设计中的完整 `POST /v1/screenings` 案件/幂等接口。

### 5. 从 Codex 或其他 Agent 通过 MCP 调用

参考栈提供本地 stdio MCP server，当前只暴露一个只读
`screen_transaction` 工具。先做真实协议冒烟：

```bash
uv run --locked python scripts/mcp_stdio_probe.py \
  --project-name tradesieve-demo \
  --request examples/requests/official-screening.json
```

该脚本直接完成 MCP 握手、工具枚举、无效请求拒绝和活跃 bundle 调用，不经过
REST。Codex 的项目级配置示例位于
[`examples/codex/config.toml`](examples/codex/config.toml)。完整接入、已验证的
Codex 子 Agent 案例和安全边界见 [MCP 与 Codex Agent 接入](docs/getting-started/mcp-agent.md)。
stdio 进程继承启动者的服务身份，当前没有独立最终用户认证；禁止把它直接暴露为
公网 MCP。远程 Streamable HTTP、OAuth/OIDC、租户授权和案件工具仍属后续范围。

### 6. 打开合成物流 CRM 演示

参考栈就绪后，在浏览器打开：

```text
http://127.0.0.1:8080/demo/crm
```

选择一条固定合成询报价并点击“发起合规审查”。按钮调用真实活跃官方来源服务；若尚未运行刷新或来源陈旧，页面保持拦截并显示服务不可用。页面会展示：

- 使用公开 EU FSF 企业别名的合成交易产生官方名称候选；
- `3A001` 控制号命中当前 Annex I，并明确指出合格归类复核和技术规格缺口；
- HS `850440` 命中 BIS CHPL Tier 3A 后只产生增强尽调候选和补证要求；
- 高能量密度二次电芯以经审核的合成技术参数触发 `3A001.e.1` 严格阈值比较；
- 其他合成交易在缺少 Annex I 归类时请求补充事实；
- 同一 bundle 的 FSF/Annex I/OFAC SDN/OFAC Consolidated snapshot ID、内容哈希和原生证据定位。

页面和接口只在 demo 模式注册。交易、金额、路线、单证和技术参数是固定合成 fixture；首条记录的名单名称来自公开官方制裁文件，只用于可重复验收，不对应真实客户关系。审查证据不是预置结果；当前覆盖 FSF 精确规范化名称/强标识、显式 Annex I 控制号和首批 `3A001` 来源绑定技术断言。完整说明见 [合成 CRM 使用指南](docs/getting-started/demo-crm.md)。

### 7. 使用当前运维 CLI

```bash
# 总体就绪度与有限检查项
docker compose run --rm --no-deps app \
  python -m tradesieve.manage inspect

# 数据源注册表的安全投影
docker compose run --rm --no-deps app \
  python -m tradesieve.manage list-sources

# 已验证来源快照的安全元数据投影
docker compose run --rm --no-deps app \
  python -m tradesieve.manage list-source-snapshots

# 当前激活的合成规则包
docker compose run --rm --no-deps app \
  python -m tradesieve.manage list-rules
```

这些命令只返回经过裁剪的运维安全字段，不导出原始来源字节、客户数据、凭据或私有规则文本。

### 8. 演示一次可重放的合成受理

以下入口只使用仓库内置合成交易，不读取文件、标准输入或真实业务数据。先提交一个固定合成请求：

```bash
docker compose run --rm --no-deps app \
  tradesieve-manage submit-demo-screening \
  --idempotency-key synthetic-order-001 \
  --fixture baseline
```

首次返回 `APPLIED` 和一组稳定的 opaque intake/screening/outbox ID；原命令重试返回 `REPLAY`，且收据引用保持不变。用同一个 key 提交语义变化的合成版本，可观察失败关闭的幂等冲突（退出码 `4`）：

```bash
docker compose run --rm --no-deps app \
  tradesieve-manage submit-demo-screening \
  --idempotency-key synthetic-order-001 \
  --fixture changed
```

这是用于架构评估、CI 和接入方理解受理语义的 operations demo，不是接收任意输入的正式 screening CLI。它只证明“请求被安全、可审计地受理”，不代表已经执行制裁/两用物项判断，更不代表可以放行。

### 9. 停止并清理 demo

```bash
docker compose down --volumes --remove-orphans
```

该命令会删除 demo PostgreSQL 和原始对象命名卷。不要把试点或生产证据放入这套参考栈。

## 外部用户和系统如何接入

### 今天

| 使用者 | 现在可做什么 | 现在不能做什么 |
| --- | --- | --- |
| 架构/安全评估者 | 启动 Compose；刷新/检查不可变官方 bundle；验证认证 REST 和 CRM 失败关闭 | 不能把技术预览当作生产服务或法律清关工具 |
| 开发者 | 运行完整测试、官方连接器和 PostgreSQL 18.4 投影门禁；审查哈希、定位和 Annex I 证据 | 不应把精确名称候选或控制号存在误解为完整实体解析/技术归类 |
| CRM/OMS 团队 | 调用 `POST /v1/official-screenings` 验证同步拦截；保存 bundle/evidence 引用并执行业务动作 | 当前没有完整 `POST /v1/screenings` 幂等案件、状态查询或 webhook |
| AI/Agent 团队 | 通过本地 stdio MCP 调用唯一只读 `screen_transaction`，验证与 CLI/REST 相同的活跃 bundle 和结构化结果 | 当前不能把 stdio 当作带 OAuth/租户授权的远程生产端点，也不能通过 Agent 放行交易 |
| 合规人员 | 审查产品范围、证据模型、规则治理和人工放行边界 | 尚没有完整案件复核工作台 |

当前 HTTP 操作清单只实现：

```text
GET /health/live
GET /health/ready
POST /v1/official-screenings
```

demo 模式另注册隐藏于 OpenAPI 的 `/demo/crm` 及固定 fixture API；其中 `/screen-official` 调用同一个持久化官方来源审查服务。`POST /v1/official-screenings` 已进入版本化 [OpenAPI](api/openapi/tradesieve.v1.json) 和共享 JSON Schema，使用部署级 Bearer token，但尚未实现租户/OIDC/对象级授权和请求/案件持久化。

仓库中的 [OpenAPI](api/openapi/tradesieve.v1.json)、[JSON Schema](api/schemas/tradesieve.contracts.v1.json) 和 `examples/` 同时包含已实现的官方来源技术预览以及仍处于设计状态的完整案件 API；每个接口的实现状态应按文档和扩展字段判断。

### Phase 1 完成后的目标用法

CRM/OMS 的目标接入流程是：

1. 在客户准入、报价、下单或订舱前构造 canonical request；
2. 使用已验证的服务身份、`Idempotency-Key` 和 correlation ID 调用 REST；
3. 将返回的 `HOLD` / `REVIEW_REQUIRED` 作为真正的业务拦截，不把网络或依赖失败解释为通过；
4. 保存 TradeSieve 的 opaque screening/case ID，不复制其内部审计状态；
5. 通过签名 webhook 或查询接口接收复核、过期和重筛结果。

CLI 和 MCP 调用相同的应用服务和契约：CLI 面向运营、CI 与诊断；当前 MCP 面向本地可信 Agent 宿主，只读工具返回结构化证据并保留人工确认门，不提供绕过授权或直接“自动放行”的能力。远程 MCP 的身份、授权和审计边界尚未实现。

目标体验见 [MVP 用户旅程](docs/getting-started/mvp-user-experience.md)。在对应实现和发布门禁通过前，其中的 screening 命令和接口均视为设计目标。

## 本地开发与验证

项目使用 CPython 3.13.14 和 `uv` 0.12.1。非容器门禁：

```bash
./scripts/check.sh
```

它会执行锁文件检查、格式化检查、Ruff、strict mypy、单元/架构测试与 100% 覆盖率、构建、文档/OpenAPI 校验、秘密扫描和依赖审计。

额外的真实运行证据：

```bash
./scripts/test_source_snapshot_postgres.sh  # PostgreSQL 18.4 持久化/并发/约束
./scripts/test_screening_submission_postgres.sh  # 受理幂等/事务/竞态/破坏修复
./scripts/test_official_source_postgres.sh  # 官方投影/激活/幂等/不可变约束
./scripts/test_official_screening_live.sh   # 四源联网刷新 + CLI/REST/CRM 同 bundle
./scripts/test_compose.sh                   # 完整参考部署与零残留验收
uv run --locked python scripts/mcp_stdio_probe.py  # MCP 握手/清单/无效请求/真实审查
uv run python scripts/ofac_sls_live_probe.py  # OFAC SDN/non-SDN 联网解析与重放证据
```

独立 OFAC 探针只验证连接器与重放边界，不写数据库；四源联网门禁会在隔离的临时 PostgreSQL/Compose 项目中执行两次正式刷新、CLI/REST/CRM 审查和零残留清理。详见
[OFAC SLS 连接器证明](docs/research/ofac-sls-connector-proof.md)。

详见 [开发环境](docs/getting-started/development.md) 和 [Docker 参考部署](docs/getting-started/docker-reference.md)。测试与示例只能使用合成数据，禁止提交客户、货运、支付、身份、凭据或生产证据。

## 仓库结构

| 路径 | 用途 |
| --- | --- |
| `docs/requirements/` | 原始需求、问题和范围 |
| `docs/product/` | 服务定义、角色旅程、详细需求和 MVP 边界 |
| `docs/architecture/` | 服务、领域、集成、安全和实现设计 |
| `docs/decisions/` | 架构决策记录 |
| `docs/delivery/` | 敏捷开发方式、Phase 1 计划和 backlog |
| `docs/research/` | 法规数据源、开源、学术和工业界调研 |
| `api/openapi/` | 生成的版本化 HTTP 设计契约 |
| `api/schemas/` | 所有适配器共享的 JSON Schema 注册表 |
| `examples/` | 合成请求、响应和事件 |
| `research/sources.yaml` | 机器可读的调研来源注册表 |
| `src/tradesieve/` | Python 领域、应用、端口和适配器实现 |
| `tests/` | 单元、契约和架构边界测试 |
| `compose.yaml`, `Dockerfile` | demo-only 参考运行环境 |
| `scripts/` | 仓库、数据库、Compose 和契约门禁 |

完整入口见 [文档索引](docs/README.md)。

## 核心原则

- **业务系统与合规控制解耦：** TradeSieve 不接管 CRM/OMS 的主数据职责。
- **证据优先于分数：** 结果必须说明来源、规则、事实、不确定性和下一步动作。
- **自动化只能收紧：** 自动化可以阻断或升级，不能自行作出最终法律放行。
- **一次建模，多种接口：** REST、CLI、MCP 和事件共享领域模型，避免接口漂移。
- **可重放、可追溯：** 原始来源、解析结果、规则、输入和决定都按版本留痕。
- **私有和最小披露：** 生产数据默认不离开批准的部署边界；公开/运维投影只返回必要字段。
- **AI 是助手，不是裁决者：** AI 可用于提取、归一化、比较和草拟，确定性控制与人工复核拥有处置权。

外部网络与服务边界见 [外部服务与出站依赖](docs/operations/external-dependencies.md)：
普通 REST/CLI/MCP/CRM 审查不联网，只有来源刷新和显式诊断访问列明的官方网站，
不会上传客户交易数据。

## 明确不做的事

- 仅凭国家、HS 编码或模糊名称匹配就宣布交易可以放行；
- 替代海关归类、出口管制律师、监管机构、银行或商业数据供应商；
- 默认把生产客户或交易数据发送给第三方 AI 服务；
- 无来源、无版本、无审批地抓取并覆盖制裁或出口管制规则；
- 在 Phase 1 宣称全球法规、历史或名单覆盖完整。

## 治理

本项目位于私有 `fyaic` GitHub 组织，采用敏捷增量、独立审查和证据门禁。参见 [CONTRIBUTING.md](CONTRIBUTING.md)、[SECURITY.md](SECURITY.md)、[GOVERNANCE.md](GOVERNANCE.md) 与 [敏捷开发方式](docs/delivery/agile-operating-model.md)。
