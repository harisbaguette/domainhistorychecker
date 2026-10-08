.PHONY: check check-skills

check-skills:
	python3 scripts/check_skills.py

check: check-skills
	uv run pytest
