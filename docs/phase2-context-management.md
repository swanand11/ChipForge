# Phase 2 Context Management: Repository Assessment

## Scope finding

The supplied Phase 2 prompt describes a medical monitoring application with a local Ollama model, patient-state storage, wearable ingestion, clinical rules, and RAG. Those components are not present in this repository. The checked-in project is ChipForge, a Layer 1 mock EDA raw artifact generator. Its pipeline creates synthetic EDA-style reports from `DesignTruth` and validates their invariants.

This assessment intentionally does not add a medical context manager to ChipForge. Doing so would require inventing the backend, authorization model, patient data model, clinical workflow, and safety controls that the prompt explicitly asks us to reuse.

## Inspected current design

- `README.md` describes the generator entry points and output artifacts.
- `chipforge/pipeline.py` orchestrates generation, validation, and summary output.
- `chipforge/generator.py` creates synthetic design runs and report data.
- `chipforge/models.py` defines design profiles and run data structures.
- `chipforge/validator.py` checks mathematical and cross-report invariants.
- `chipforge/calibration.py` and `docs/data_dictionary.md` cover generator calibration and output fields.
- `scripts/run_pipeline.py` and `chipforge/__main__.py` expose the pipeline.
- Existing tests are `tests/test_generator.py` and `tests/test_upload_raw_to_s3.py`.

Repository-wide searches for `ollama`, `llm`, `rag`, `patient`, `wearable`, `embedding`, `vector`, and prompt/context terms found no corresponding application implementation. There is no relevant authentication/role system, conversation history store, rule engine, safety guardrail, Ollama configuration, Docker configuration, or model-context/token configuration to trace. Phase 1 local Ollama functionality therefore cannot be verified in this checkout.

## Existing context-management limitations

There is no LLM request path and no prompt construction in this project. Consequently, it has no context layers, retrieval pipeline, conversation retention policy, token counter, watermark compaction, context manifest, or state freshness/invalidation mechanism. The requested clinical data flow cannot be traced because its components do not exist here.

## Proposed architecture for the intended application

When the intended medical application repository is available, the smallest viable implementation should be integrated into its existing request service and Ollama abstraction:

1. Preserve authentication, authorization, request validation, deterministic rule evaluation, and escalation precedence in their existing application code.
2. Build a centralized context assembler that reconstructs versioned safety instructions for each request and separately accepts current authorized patient state, request-scoped evidence, and retained conversation turns.
3. Reuse the existing patient store and RAG/vector implementation. Authorize patient-scoped retrieval before retrieved text reaches the assembler; keep general clinical knowledge retrieval separate.
4. Attach an application-owned manifest containing request/session reference, role, policy and state versions, summary version/source range, evidence references and versions, relevant wearable/rule versions, token estimates, output reserve, and compaction metadata. Avoid copying clinical source content into this manifest.
5. Apply high/low watermark compaction only to eligible older conversation history, then remove redundant/low-relevance evidence where safe. Recount after each bounded operation. Refresh stale state from its authoritative store rather than compacting it.
6. Validate that the final prompt plus template overhead and output reserve fits the configured model context. Return a controlled fallback if required state or safety context cannot fit. Keep existing output validation and deterministic escalation authoritative after model generation.

## Context-budget policy proposal

Use model-specific context settings sourced from the existing Ollama configuration rather than assuming a universal window. Suggested starting values from the request are an 8192-token configured window, a 0.80 high watermark, a 0.60 low watermark, and a 1024-token output reserve, but these must be checked against the selected model and actual deployment settings before adoption. Reject invalid thresholds and configurations where the output reserve consumes the effective budget.

Count the fully rendered messages, including system/instruction content, active state, retrieved text and metadata, conversation, and chat-template overhead. Prefer a compatible tokenizer. If unavailable, document and calibrate a conservative estimator against the selected model. The final acceptance limit is the effective model context minus output reserve and required overhead. Compaction is bounded and may stop above the low watermark when further reductions would remove required information.

## Assumptions and prerequisites

- This checkout is not the medical AI application referenced by the prompt.
- No clinical behavior, model settings, authorization rules, or persistence semantics can safely be inferred from ChipForge.
- The intended application source tree or repository should be supplied before implementation. Once available, inspect its actual code and implement against its existing interfaces rather than introducing replacement infrastructure.

## Verification status

This document is based on source and documentation inspection. No context-management implementation or tests were added because the required application components are absent. Existing project tests were not run; this assessment made no code changes that require them.
