# repair

Failure diagnosis, deterministic repair, failure learning, and long-tail repair queue generation.

Typical flow inside this category:

- `build_failure_casebook.py`
- `build_failure_diagnosis.py`
- `auto_fix_failures.py`
- `build_llm_repair_cases.py`
- `build_llm_patch_requests.py`
- `call_openai_llm_patch_api.py`
- `evaluate_llm_patch_results.py`
- `mine_llm_patch_rule_candidates.py`

This directory holds the canonical implementation files for the repair group.
