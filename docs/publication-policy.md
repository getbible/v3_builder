# Publication policy

Extraction and publication are different permissions. GetBibleSWORD may inspect a
module installed by an operator; Builder decides whether that module may enter a
published getBible artifact.

`conf/PublicationPolicy.json` is therefore default-deny. The initial allow-list is
the 117-module catalog that Builder v3 already published when this policy was
introduced. Adding a module to `CrosswireModulesMap.json` is insufficient: a
maintainer must separately review its distribution terms and add it to
`approved_modules`.

Review should record, in the pull request or linked issue:

1. the exact SWORD module and version;
2. the upstream source and distribution license;
3. whether redistribution and transformed JSON publication are permitted;
4. attribution, notice, geography, or access requirements;
5. the reviewer and date;
6. any expiry or re-review condition.

A future policy schema may carry these facts directly. Schema v1 keeps the
enforcement minimal and unambiguous: every build target must be named explicitly,
and an unknown module aborts before download or publication.
