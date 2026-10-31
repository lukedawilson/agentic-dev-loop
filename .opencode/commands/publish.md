---
description: Cut and publish an agentic-dev-loop release - bump version, update changelog, commit, tag, push; the v* tag push triggers release.yml (tests → PyPI → GitHub Release). Usage: /publish [--major|--minor|--patch|<version>] [--description "text"]
---

MANDATE: if the release includes any change that requires consumer pipeline.yaml or agent-prompt edits (verdict vocabulary, step caps, banned commands, qa prompt sections), the CHANGELOG entry being cut MUST carry a `### Consumer adoption` subsection naming exactly what consumer repos must change. publish.py refuses a release whose entry references a consumer-adoption plan without that subsection (registry: CONSUMER_ADOPTION_PLANS in tools/publish.py). When a new plan adds consumer obligations, add its slug to that registry.

!`.venv/bin/python tools/publish.py $ARGUMENTS --dry-run`

!`.venv/bin/python tools/publish.py $ARGUMENTS`
