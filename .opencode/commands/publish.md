---
description: Cut and publish an agentic-dev-loop release - bump version, update changelog, commit, tag, push; the v* tag push triggers release.yml (tests -> PyPI -> GitHub Release). Usage: /publish [--major|--minor|--patch|<version>] [--description "text"]
---

MANDATE: if the release includes any change that requires consumer
pipeline.yaml or agent-prompt edits (verdict vocabulary, step caps, banned
commands, qa prompt sections), the CHANGELOG entry being cut MUST carry a
`### Consumer adoption` subsection naming exactly what consumer repos must
change. publish.py refuses a release whose entry references a
consumer-adoption plan without that subsection (registry:
CONSUMER_ADOPTION_PLANS in tools/publish.py). When a new plan adds consumer
obligations, add its slug to that registry.

!`.venv/bin/python tools/publish.py $ARGUMENTS --dry-run`

The dry run output above shows the planned version, the rendered release
notes publish.py would write, and any uncommitted files; it also applies the
consumer-adoption gate. Do NOT cut the release yet. Run the flow below:

1. If the dry run reported uncommitted files, ask through the `question`
   tool: header "Unstaged files", question "Add these uncommitted files to
   the release commit?" (list the files in the question), options yes and
   no. If the answer is no, the final publish command gets --no-add and the
   uncommitted files stay out of the release commit.

2. Ask through the `question` tool whether the user is happy with the
   release notes shown by the dry run: header "Release notes", question
   "Happy with these release notes?", options "Yes, cut the release" and
   "No, I want changes". If they want changes, ask what the notes should
   say, then re-run `.venv/bin/python tools/publish.py $ARGUMENTS
   --dry-run --description "<their text>"` and repeat this step until they
   are happy.

3. Only when the user has confirmed the notes, run the real publish
   yourself: `.venv/bin/python tools/publish.py $ARGUMENTS --yes` (append
   --no-add if they declined in step 1) and report the result.

If any question cannot be answered, or the dry run reported an error, stop
and report why. Never run the real publish without the user's confirmation.
publish.py's stdin prompt cannot work here (the session has no stdin), which
is why the question tool is the confirmation path.
