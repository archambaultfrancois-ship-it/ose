include ../tools/python.mk
LOAD ?= nominal
DURATION ?= 10
WORKERS ?= 1
ANALYZER ?= c

.PHONY: validate build run simulate test
validate build:
	"$(PYTHON)" tools/workshop.py $@ --load "$(LOAD)" --analyzer "$(ANALYZER)"
run simulate:
	"$(PYTHON)" tools/workshop.py $@ --load "$(LOAD)" --duration "$(DURATION)" --workers "$(WORKERS)" --analyzer "$(ANALYZER)"
test:
	"$(PYTHON)" -m unittest discover -s tests -v
