include ../tools/python.mk
LOAD ?= nominal
DURATION ?= 10
WORKERS ?= 1

.PHONY: validate build run simulate test
validate build:
	"$(PYTHON)" tools/workshop.py $@ --load "$(LOAD)"
run simulate:
	"$(PYTHON)" tools/workshop.py $@ --load "$(LOAD)" --duration "$(DURATION)" --workers "$(WORKERS)"
test:
	"$(PYTHON)" -m unittest discover -s tests -v
