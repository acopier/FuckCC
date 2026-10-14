PYTHON ?= python3
export PYTHONPATH := $(CURDIR)/src

.PHONY: help test tidy run list install-local uninstall clean

help:
	@echo "targets: test | run ARGS=... | list | install-local | uninstall | clean"

test:
	$(PYTHON) -m unittest discover -s tests -v

# Run the CLI from the working tree, e.g. `make run ARGS="versions PHSP"`
run:
	$(PYTHON) -m fuckcc $(ARGS)

list:
	$(PYTHON) -m fuckcc list

install-local:
	scripts/brew-install-local.sh

uninstall:
	brew uninstall --zap --cask fuckcc

tidy:
	find . -name '__pycache__' -type d -prune -exec rm -rf {} +
	find . -name '*.py[co]' -delete

clean: tidy
	rm -rf build dist *.egg-info
