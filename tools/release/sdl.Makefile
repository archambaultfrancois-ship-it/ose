# Build services from an installed framework; test/example resources are absent.
PYTHON ?= python3
CARGO ?= cargo
CC ?= cc
RECORDING_DEPS ?= build/recording-deps
.PHONY: all services filter-python filter-native recording-native recording-deps
all: services
services: filter-python recording-native recording-deps
filter-python:
	CARGO="$(CARGO)" CC="$(CC)" $(PYTHON) tools/filter/build_native.py --python
filter-native:
	CARGO="$(CARGO)" CC="$(CC)" $(PYTHON) tools/filter/build_native.py
recording-native:
	CARGO="$(CARGO)" CC="$(CC)" $(PYTHON) tools/recording/build_native.py
recording-deps:
	$(PYTHON) -c 'import sdl_recording' || $(PYTHON) -m pip install --no-index --target "$(RECORDING_DEPS)" -r tools/recording/requirements.txt
