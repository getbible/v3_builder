# Builder v3 contributor guide

Read `README.md`, `docs/getbiblesword-pipeline.md`, and
`docs/publication-policy.md` before changing extraction or publication behavior.

Non-negotiable invariants:

- Treat `getbiblesword` as a subprocess and the NDJSON file as an untrusted input.
- Validate a complete successful footer and exact stream hash before conversion.
- Decode base64 as authoritative and retain unknown v1 data.
- Never replace raw entry bytes with rendered or stripped projections.
- Keep publication default-deny; a module map edit is not publication approval.
- Do not create symlinks while installing ZIPs or reassembling artifacts.
- Preserve existing API fields unless a separately reviewed API version changes.
- Keep builds deterministic and offline after module/release downloads.

Run `python -m pytest tests/ -v` for unit changes. Native integration changes also
require the manual integration workflow with the pinned release and real test
modules. GetBibleSWORD `0.1.x` is an engineering preview; do not promote this branch
to production without satisfying the gates in the pipeline document.
