PYTHON ?= python3

.PHONY: data demo test lint

data:
	$(PYTHON) scripts/generate_data.py

demo:
	$(PYTHON) -m bizgoal demo

test:
	$(PYTHON) -m unittest discover -s tests -v

lint:
	$(PYTHON) -m compileall -q bizgoal tests scripts
