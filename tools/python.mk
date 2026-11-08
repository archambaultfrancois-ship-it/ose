# Share one compatible PATH interpreter across Make and the application commands.
_ose_python_root := $(abspath $(dir $(lastword $(MAKEFILE_LIST)))/..)
ifneq ($(origin PYTHON),command line)
PYTHON := $(shell sh "$(_ose_python_root)/tools/find-python.sh")
endif
_ose_python_supported := $(shell "$(PYTHON)" -c 'import sys; print(int(sys.version_info >= (3, 10)))' 2>/dev/null)
ifneq ($(_ose_python_supported),1)
$(error Python 3.10+ is required. Install python3 or a compatible python3.x command in PATH)
endif
export PYTHON
_ose_python_deps := $(_ose_python_root)/build/python-deps
export PYTHONPATH := $(_ose_python_root)/ose/src:$(_ose_python_deps)$(if $(PYTHONPATH),:$(PYTHONPATH))
