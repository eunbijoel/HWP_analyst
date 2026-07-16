# Org-form Evidence Fill fixtures

Real files for manual/automated verification (not mocks).

| File | Role |
|------|------|
| `target_org_form.hwpx` | Empty form: exact + synonym labels; biz/corp blank; `KEEP_SENTINEL` |
| `ref_org.hwpx` | Evidence for org/rep/addr/phone/email; no biz/corp |
| `ref_org.xlsx` | Excel evidence (synonym headers); no biz/corp |

Run: `python3 scripts/verify_org_evidence_fill.py`
