# Profile tooling

`contribution_trace.py` replaces the generic contribution snake with a first-party SVG renderer.
It queries the GitHub GraphQL contribution calendar, validates a complete one-year response, and
emits deterministic dark and light activity traces.

Local live render:

```powershell
$env:GITHUB_TOKEN = gh auth token
python tools/contribution_trace.py --output-dir .profile-preview/contribution-trace
Remove-Item Env:GITHUB_TOKEN
```

Deterministic replay from a retained response:

```powershell
python tools/contribution_trace.py `
  --input path/to/contributions.json `
  --generated-on 2026-08-10 `
  --output-dir .profile-preview/contribution-trace
```

The SVG contains daily aggregate counts already exposed by GitHub's contribution calendar. It does
not include repository identities, source code, credentials, or a third-party rendering runtime.
The scheduled workflow uses its repository-scoped `GITHUB_TOKEN`, so its aggregate can exclude
contributions from private repositories that token cannot read. A broader local token may render a
higher total; the snapshot digest makes that source-visibility difference explicit instead of
silently treating the two calendars as identical.
