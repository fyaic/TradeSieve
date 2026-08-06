# Original request

| Field | Value |
| --- | --- |
| Recorded | 2026-08-06 |
| Status | Source record; do not rewrite in place |
| Source | User-provided description, internal-chat screenshot, and follow-up request |
| Data handling | Personal names and binary attachments intentionally excluded |

## Business background — verbatim user input

> 背景：中国国内有一家国际物流公司涉及到俄罗斯的业务，近期俄罗斯被欧洲制裁导致公司连带进入了制裁名单，非常多的业务，银行收款已经交易受到严重影响。
>
> 针对图中的需求：审核受制裁国家，两用物项，敏感货物的审核机制，公司法务以前做的只有国内工商网，法院，同行黑名单的统计，针对海外制裁的，这部分没有做过整理。这个历史的数据，能否请你们协助大数据收集，整理，然后植入到我们现在的业务系统，在CRM系统或者是订单系统做拦截和提醒。
>
> 当然我们如果要做的话，肯定是与现有的CRM系统保持解耦，保持独立边界。

## Internal discussion excerpt

The supplied screenshot asked the team to use technology to establish a compliance and export-control system, described the issue as increasingly important, and characterized the existing controls as materially weak. The operational request was to collect and organize historical overseas-sanctions data, then integrate screening, blocking, and reminders into CRM or order workflows.

Personal names visible in the screenshot are not reproduced because they are unnecessary to the requirement.

## Interface and repository follow-up — verbatim user input

> 同意，最好是ai友好的，比如支持cli/mcp形态接入agent。当然底层的api接口传统方式也需要保留。你在本地projects目录创建一个目录，名字你帮我构思，使用我的github权限进行git项目管理，按照一流github仓库进行打理，先把我们最原始需求记录好，然后在社区或者一些学术界，工业界进行相关的调研整理成后续可以参考的文档。便于后续的系统设计。

## Requirements preserved from the source

1. Screen sanctions regimes and affected jurisdictions without reducing them to a country-only blacklist.
2. Screen dual-use items and sensitive goods.
3. Collect, normalize, version, and backfill relevant historic data.
4. Provide blocks and warnings inside existing CRM/order workflows.
5. Maintain an independent system boundary and avoid embedding legal logic in CRM.
6. Preserve a conventional API for deterministic business-system integration.
7. Provide a composable CLI and an MCP server for AI-agent access.
8. Manage the work as a high-quality GitHub project with durable requirements and research records.

## Safety requirement added by the project

The system may automatically hold, request evidence, or escalate. It must not automatically declare a party, product, route, payment, or transaction lawful or sanctions-free. Final release is a recorded human decision.

## Provenance note

The original DOCX and screenshot were read for context but are not copied into this repository. This avoids committing internal chat identities, local cache paths, document metadata, and binary material that is not required to design the system.
