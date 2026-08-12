# TradeSieve

> 把制裁名单、两用物项和敏感交易检查，放进国际物流公司的日常业务流程。

[![Status: 0.1 alpha prototype](https://img.shields.io/badge/status-0.1%20alpha%20prototype-6f42c1)](#项目进度)
[![Runtime: Docker Compose](https://img.shields.io/badge/runtime-Docker%20Compose-2496ed)](docs/getting-started/docker-reference.md)
[![Decision: Human clearance](https://img.shields.io/badge/decision-human%20clearance%20only-b7791f)](docs/decisions/0002-human-clearance-only.md)
[![Interfaces: REST CLI MCP](https://img.shields.io/badge/interfaces-REST%20%7C%20CLI%20%7C%20MCP-0969da)](#业务团队怎样使用)

TradeSieve 是一个与 CRM、订单和订舱系统解耦的合规审查服务。它接收待审查的交易方、货物和业务动作，使用有版本的官方来源和规则返回保守的业务建议，并保留证据引用。现有系统只负责提交数据和执行拦截，不需要自己维护制裁名单。

当前版本适合业务演示、流程评审、合成数据试接入和技术选型。它不是法律意见，也不会自动宣布交易“合法”“无风险”或“可以放行”；真实交易必须由获得授权的人员决定。

## 交付快速入口

| 你是谁 | 建议先看 | 可以马上做什么 |
| --- | --- | --- |
| 业务负责人、演示人员 | [图文演示 PDF](https://github.com/fyaic/TradeSieve/releases/download/v0.1.0-alpha.5/tradesieve-alpha-local-demo-guide-zh.pdf) · [业务交付包](docs/delivery/business-delivery-pack.md) | 看懂效果、讲解五个固定场景、确认后续试点流程 |
| CRM / OMS 团队 | [API 接入说明](docs/getting-started/official-screening-cli.md) · [OpenAPI](api/openapi/tradesieve.v1.json) | 用认证 REST 提交一笔合成订单并处理拦截结果 |
| 没有 CRM 的业务或技术人员 | [完整演示与接入指南](docs/demo/tradesieve-alpha-local-demo-guide.md) | 通过网页、REST 或 CLI 使用同一个审查服务 |
| Codex / Agent 使用者 | [MCP 与 Agent 接入](docs/getting-started/mcp-agent.md) · [真实 Agent 验收记录](docs/evidence/codex-agent-mcp-demo-2026-08-11.md) | 通过本地只读 MCP 工具审查，不获得放行能力 |
| 部署、开发和安全团队 | [技术交接](docs/delivery/technical-handoff.md) · [外部依赖](docs/operations/external-dependencies.md) | 复核测试证据、运行边界、联网来源和生产缺口 |

## 现在能看到什么效果

一次审查会把结果归成容易执行的业务动作：

- `HOLD`：发现制裁名单候选或已支持的技术阈值命中，暂停当前动作并转人工；
- `REQUEST_EVIDENCE`：资料不足或货物属于敏感候选，保持拦截并补充证明；
- `MONITOR`：已实现的检查未发现更强风险，仍然不是自动放行；
- 服务、来源或证据不可用：失败关闭，业务系统继续拦截。

### 1. 中国国际物流 CRM 风格的合成询报价

![TradeSieve 合成 CRM 概览](docs/demo/assets/tradesieve-alpha-local-demo-guide/01-crm-overview.png)

### 2. 名单候选：直接给出 `RED / HOLD`

示例使用公开官方名单名称，不使用真实客户数据。页面同时显示来源版本和证据定位，便于人工复核。

![公开名单候选产生 RED HOLD](docs/demo/assets/tradesieve-alpha-local-demo-guide/02-red-hold-official-match.png)

### 3. 有限范围的两用物项技术阈值比较

在业务已经提供合格 Annex I 候选和经核验技术参数时，当前首批 `3A001` 规则可以逐项比较，并说明为什么拦截。

![3A001 技术阈值命中](docs/demo/assets/tradesieve-alpha-local-demo-guide/03-dual-use-threshold-match.png)

### 4. CHPL 敏感候选：缺资料时请求补证

HS-6 候选只触发增强尽调，不会冒充出口管制归类或禁运结论。

![CHPL Tier 3A 请求补充证据](docs/demo/assets/tradesieve-alpha-local-demo-guide/04-chpl-tier3a-request-evidence.png)

### 5. REST、CLI 和 MCP 调用同一个活跃版本

下面是 2026-08-12 的本地同轮实测摘要。三种接口得到相同的 bundle、风险等级和业务动作；无效 MCP 参数被拒绝。

![API CLI MCP 同源实测](docs/demo/assets/tradesieve-alpha-local-demo-guide/05-api-cli-mcp-parity.png)

可复核的结构化记录见 [接口一致性证据](docs/evidence/interface-parity-2026-08-12.json)。

## 业务团队怎样使用

| 使用方式 | 适用场景 | 当前入口 | 当前效果 |
| --- | --- | --- | --- |
| 模拟 CRM | 演示询报价、补件和拦截流程 | `http://127.0.0.1:8080/demo/crm` | 五条固定合成询报价，不写入真实客户数据 |
| REST API | CRM、OMS、内部表单或低代码流程 | `POST /v1/official-screenings` | Bearer 认证；返回来源、证据、风险和业务动作 |
| CLI | 技术验证、受控批处理、故障排查 | `tradesieve-manage screen-active` | 从标准输入接收 JSON，读取同一活跃来源 |
| MCP | Codex 等受控 Agent | `tradesieve-mcp` / `screen_transaction` | 本地 stdio、只读、单工具；不能清关或修改案件 |

CRM 推荐在客户准入、报价放行、订单确认、订舱、装运和收付款前调用；主体、货物、路线、最终用户或付款信息发生关键变化后应重新审查。调用方必须把 `HOLD`、`REQUEST_EVIDENCE`、错误、超时和不可解析响应都当作继续拦截。

## 5 分钟本地演示

前提：Git、Docker Engine / Docker Desktop 和 Compose；第一次刷新官方来源需要联网。

```bash
git clone git@github.com:fyaic/TradeSieve.git
cd TradeSieve
git checkout v0.1.0-alpha.5
./scripts/start_business_demo.sh
```

脚本会构建隔离的本地参考栈、等待数据库和服务就绪、刷新四份官方来源，并执行一笔合成审查作为启动验证。成功后打开：

```text
http://127.0.0.1:8080/demo/crm
```

演示数据和技术参数均为固定合成 fixture；名单场景只复用公开官方条目。不要把真实客户、付款、证件或运输文件提交到演示栈、Git 或未经批准的 Agent。

停止并删除演示数据库与对象卷：

```bash
docker compose --env-file .env.example -p tradesieve-demo \
  down --volumes --remove-orphans
```

这是破坏性清理命令，只应用于本地演示项目。

更多入口：

```bash
# CLI：使用本地活跃来源
docker compose --env-file .env.example -p tradesieve-demo exec -T app \
  tradesieve-manage screen-active --request - \
  < examples/requests/official-screening.json

# REST：本机 demo token 只用于验收
curl --fail-with-body \
  -H 'Authorization: Bearer local_demo_only_official_screening_token' \
  -H 'Content-Type: application/json' \
  --data-binary @examples/requests/official-screening.json \
  http://127.0.0.1:8080/v1/official-screenings

# MCP：真实 stdio JSON-RPC 握手、工具枚举、拒绝无效请求和正式调用
uv run --locked python scripts/mcp_stdio_probe.py \
  --project-name tradesieve-demo \
  --request examples/requests/official-screening.json
```

## 项目进度

**当前阶段：`0.1.0a5`，业务演示与原型接入准备度 `95/100`。** 这个内部交付检查分数只评价“业务团队能否拿到、看懂、演示并用合成数据接入”，不评价生产运营或法律正确率，也不是独立认证。评分依据和剩余 5 分见 [业务交付包](docs/delivery/business-delivery-pack.md)。

| 业务能力 | 当前状态 | 业务能得到什么 |
| --- | --- | --- |
| 名单审查 | ✅ 可演示、可试接 | EU FSF、OFAC SDN 和 Consolidated 的明确候选会触发暂停和人工复核 |
| 两用物项与敏感货物 | 🧪 有限范围可用 | Annex I 条目候选、50 个 CHPL HS-6 候选和首批 `3A001` 技术阈值可产生补件或拦截建议 |
| 多种接入方式 | ✅ 可演示、可试接 | 模拟 CRM、认证 REST、CLI 和本地只读 MCP 使用同一个审查版本 |
| 官方来源更新 | ✅ 有受控刷新流程 | 四份正式来源经过验证后一起生效；缺一份或过期时停止服务，不带病运行 |
| 审查证据 | ✅ 当前切片可追溯 | 结果带来源版本、定位和哈希，便于技术人员复核 |
| 历史经验池 | 🧱 基础已建 | 已有快照、受理和审计基础；任意表格导入、人工标签、案件检索仍待完成 |
| 人工作业与完整案件 | ⏳ 未完成 | 当前可以拦截，但还没有可供生产合规团队使用的完整复核工作台 |
| 全球和俄罗斯专项覆盖 | ⏳ 未完成 | 尚不能处理所有权/控制穿透、OFAC 50 Percent Rule、完整 Annex I、俄罗斯货物/路线和最终用途法律效果 |
| 生产运行 | ⛔ 尚不可用 | 正式身份、租户隔离、案件、监控、备份恢复、签名通知和专业规则验收仍是上线前置条件 |

详细工程证据、测试门禁、数据量和 migration 结果不再堆在首页，统一见 [技术交接](docs/delivery/technical-handoff.md)。

## 两个知识池

TradeSieve 刻意把“当下使用的权威来源”和“企业自己的历史经验”分开：

| 知识池 | 作用 | 当前状态 |
| --- | --- | --- |
| 官方来源池 | 保存监管机构发布、版本和新鲜度，供实时审查使用 | 四源活跃 bundle 已实现；CHPL 是带核验日期的内置指导快照 |
| 内部经验池 | 保存历史交互、人工标记、内部表格、证据和决定 | 数据与审计基础已存在，完整导入、检索和案件闭环未完成 |

内部调研表格可以成为有来源、可复核的线索，但不能因为被录入就冒充监管机构清单，也不能自动产生放行结论。工作簿评估见 [调研表格评估](docs/research/user-workbook-assessment.md)。

## 外部服务和数据依赖

- 本地运行只需要 Docker、PostgreSQL 和本地持久卷；审查时不会把客户订单上传给监管机构；
- 刷新官方来源时需要访问 EU Data、EU Webgate、Publications Office、OFAC SLS 及其受限 AWS GovCloud 下载跳转；
- BIS CHPL 当前是仓库内的已核验版本，不是实时联网连接器；
- 没有商业名单订阅或第三方合规 API；这也意味着当前覆盖度不能等同于商业产品；
- 详细域名、数据流、认证、失效行为和生产替代方案见 [外部服务与出站依赖](docs/operations/external-dependencies.md)。

## 安全与范围边界

TradeSieve 当前是确定性决策支持原型，不是法律意见、自动清关器、ECCN/HS 分类器或生产案件系统。尤其不要把下列情况解释成“无风险”：

- 名称没有精确候选；
- HS-6 没有落入 CHPL；
- 首批 `3A001` 规则没有命中；
- 返回 `GREEN_CANDIDATE / MONITOR`；
- 某个国家、项目、所有权关系、最终用途或路线尚未实现。

所有结果都保持 `automatic_clearance=false`。完整发布边界见 [原型发布边界](docs/product/prototype-release-boundary.md)，安全披露见 [SECURITY.md](SECURITY.md)。

## 开发和仓库治理

```bash
uv sync --locked --extra dev
./scripts/check.sh
./scripts/test_postgres.sh
./scripts/test_compose.sh
```

仓库采用保护分支、Issue / PR、Conventional Commits、锁定依赖、自动检查、版本化迁移和可复现构建。贡献与发布入口：

- [开发环境](docs/getting-started/development.md)
- [贡献指南](CONTRIBUTING.md)
- [发布安装与校验](docs/getting-started/prototype-release.md)
- [文档索引](docs/README.md)
- [变更记录](CHANGELOG.md)
- [路线图](docs/roadmap/discovery-plan.md)

核心目录：

```text
api/          OpenAPI 和 JSON Schema
docs/         需求、产品、架构、交付、证据与研究
examples/     合成请求、响应、事件和 Agent 配置
migrations/   PostgreSQL 迁移
scripts/      检查、演示、探针和发布脚本
src/          应用、领域、接口与基础设施代码
tests/        单元、架构、契约和集成测试
```
