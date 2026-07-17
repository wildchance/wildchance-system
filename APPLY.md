# Manual update — gold edge system (branch claude/asian-gold-cbdr-delay-ggo59l, 14 commits vs origin/main)
...13 = COMMAND_ARCHITECTURE.md · 14 = STRATOPS foundation (objective + exposure cap + sorter) -> GET /gold/objective, /gold/stratops
Apply:  git checkout -b claude/asian-gold-cbdr-delay-ggo59l origin/main && git am patches/*.patch && git push -u origin claude/asian-gold-cbdr-delay-ggo59l
Docs:   GOLD_BLUEPRINT.md (playbook) · COMMAND_ARCHITECTURE.md (roadmap)  |  Verify: python3 -m pytest tests/ -q
