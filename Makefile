# Repository entry points; each project keeps its own build and dependencies.
.DEFAULT_GOAL := all
SDL_ROOT ?= $(abspath $(CURDIR)/sdl)
include tools/python.mk

.PHONY: setup
setup:
	"$(PYTHON)" tools/python_env.py

.PHONY: python-info
python-info:
	@"$(PYTHON)" -c 'import sys; print(sys.executable); print("Python " + sys.version.split()[0])'

.PHONY: all check test check-sdl check-ose test-sdl test-ose check-ws test-ws frontend-ws check-editor test-editor frontend-editor clean help release test-release
all:
	+$(MAKE) -C sdl all
check: check-sdl check-ose
test: test-sdl test-ose
check-sdl:
	+$(MAKE) -C sdl check
check-ose:
	+$(MAKE) -C ose check SDL_ROOT="$(SDL_ROOT)"
test-sdl:
	+$(MAKE) -C sdl test
test-ose:
	+$(MAKE) -C ose test SDL_ROOT="$(SDL_ROOT)"
check-ws test-ws:
	+$(MAKE) -C ose-ws test SDL_ROOT="$(SDL_ROOT)"
frontend-ws:
	+$(MAKE) -C ose-ws frontend
check-editor test-editor:
	+$(MAKE) -C ose-editor test SDL_ROOT="$(SDL_ROOT)"
frontend-editor:
	+$(MAKE) -C ose-editor frontend
release:
	"$(PYTHON)" tools/release/build.py $(RELEASE_OPTIONS)
test-release:
	"$(PYTHON)" -m unittest discover -s tools/release/tests -v
clean:
	+$(MAKE) -C sdl clean
help:
	@echo 'make setup       Install checkout dependencies and build the required SDL services (Python 3.10+)'
	@echo 'make python-info Show the detected Python interpreter and version'
	@echo 'make check       Run SDL and OSE checks'
	@echo 'make check-sdl   Run SDL backend checks'
	@echo 'make check-ose   Run OSE checks'
	@echo 'make test        Run both projects with verbose output'
	@echo 'make test-ws     Run the optional OSE Web Console tests'
	@echo 'make frontend-ws Build the bundled OSE Web Console frontend'
	@echo 'make test-editor Run the OSE Model Editor tests'
	@echo 'make frontend-editor Build the bundled model editor frontend'
	@echo 'make release     Build the offline OSE/SDL/web/editor release for this platform'
	@echo 'make test-release Test the generic release installer'
	@echo 'Other targets are forwarded to the SDL project (for example test-java).'

# Retain the SDL commands previously invoked from the repository root.
%:
	+$(MAKE) -C sdl $@
