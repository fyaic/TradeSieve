# Assessment of the user-provided Russia sanctions workbook

**Artifact reviewed:** `对俄制裁名单-调研补充版.xlsx`  
**Review date:** 2026-08-11  
**Handling:** read-only analysis; the original workbook was not modified or committed.

## Finding

The workbook is useful as an attributed research register and target field map. It is not a complete or authoritative sanctions database that should be imported directly into the active official-source pool.

The ten worksheets mainly contain:

- official and industry source links;
- suggested list, entity, vessel, goods, country and route fields;
- EU/US summary structures;
- OFAC and BIS mapping notes;
- a 50-code Common High Priority List tier inventory;
- machine-integration field suggestions and update metadata.

Multiple rows explicitly identify themselves as structural placeholders rather than entity records awaiting synchronization. Consequently, a row’s presence cannot be treated as a current designation, control or transaction restriction.

## What the project reuses

| Workbook material | Prototype use | Provenance treatment |
| --- | --- | --- |
| Official-source URLs | Cross-check source discovery and research backlog | Revalidated against the regulator page before use |
| Entity/list fields such as UID, program and list type | Compare with the existing EU FSF and OFAC projections | Runtime facts continue to come from retrieved official bytes |
| HS/CN/ECCN, last-sync and confidence fields | Inform the canonical goods/source metadata design | Candidate fields remain distinct from verified classifications |
| BIS CHPL tier inventory | Exact 50-code HS-6 candidate fixture | Bound to the official BIS guidance URL, publication date, access date and deterministic content hash |
| Case/chat/industry observations | Future internal experience pool | Must retain author/source/time/reviewer status and cannot masquerade as official data |

The workbook’s CHPL codes agree with the BIS inventory reviewed on 2026-08-11. TradeSieve therefore includes the 50 codes as a versioned guidance fixture and exposes `CANDIDATE`, tier and missing-evidence fields. A hit produces at most enhanced-due-diligence signaling; it does not infer an ECCN/Annex I code, licence requirement or prohibition.

## What is not imported

- placeholder person, entity or vessel rows;
- summary prose as executable legal rules;
- source URLs without retrieval, integrity and freshness controls;
- confidence values without a defined calculation and reviewer ownership;
- a workbook “no hit” as evidence of absence;
- any unsupported Russia goods, route, financial, service or circumvention conclusion.

## Recommended internal-pool record

When general historical/import support is implemented, each workbook or chat observation should be stored as an attributed assertion with at least:

```text
assertion_id, source_document_id, source_locator, recorded_at,
subject_or_goods_ref, assertion_type, asserted_value,
fact_class, confidence_method, reviewer_status, valid_from, valid_to,
supersedes, evidence_hash, notes
```

The importer should quarantine unknown schemas, preserve the original file privately, hash the exact bytes, avoid executable spreadsheet content, and require a reviewer before an internal assertion affects a hold. Official source evidence must always remain separately queryable and take precedence for claims about current list membership.

## Decision for the prototype

Use the workbook to improve field coverage, source backlog and the internal experience-pool design. Use official regulator publications for current executable source facts. This preserves the leadership objective—current, workflow-integrated controls—without overstating what a manually maintained spreadsheet can prove.
