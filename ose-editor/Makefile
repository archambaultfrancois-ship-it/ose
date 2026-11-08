include ../tools/python.mk
SDL_ROOT ?= $(abspath ../sdl)
export PYTHONPATH := $(CURDIR)/src:$(abspath ../ose/src):$(SDL_ROOT)/runtime/python:$(_ose_python_deps)

.PHONY: frontend test test-python test-frontend check wheel serve
frontend:
	cd frontend && npm ci --no-audit --no-fund && npm run build
test-python:
	"$(PYTHON)" -m unittest discover -s tests -v
test-frontend:
	cd frontend && npm test
test: test-python test-frontend
check: test
wheel:
	"$(PYTHON)" -m build
serve:
	"$(PYTHON)" -m ose_editor.cli --root ../vibration-watch

include ../tools/sdl-services.mk
test-python serve: sdl-services
