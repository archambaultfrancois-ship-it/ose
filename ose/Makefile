TEST_LANGUAGES ?= python,c,rust,java,ada
include ../tools/python.mk
SDL_ROOT ?= $(abspath $(CURDIR)/../sdl)
export PYTHONPATH := $(CURDIR)/src:$(_ose_python_deps)
MODEL := examples/acquisition/model.json
DEPLOYMENT := examples/acquisition/deployment.json

.PHONY: validate build demo check test test-unit test-integration test-sdk test-simulated test-distributed
validate:
	"$(PYTHON)" -m ose.cli validate --model $(MODEL) --deployment $(DEPLOYMENT) --sdl-root "$(SDL_ROOT)"
build:
	"$(PYTHON)" -m ose.cli build --model $(MODEL) --deployment $(DEPLOYMENT) --sdl-root "$(SDL_ROOT)"
demo: build
	"$(PYTHON)" -m ose.cli run --duration 2
	"$(PYTHON)" -m ose.cli inspect --last
check:
	@"$(PYTHON)" tests/run_check.py "$(MAKE)"
test: test-unit test-sdk test-integration test-simulated $(if $(findstring java,$(TEST_LANGUAGES)),test-java) $(if $(findstring ada,$(TEST_LANGUAGES)),test-ada) $(if $(and $(findstring java,$(TEST_LANGUAGES)),$(findstring ada,$(TEST_LANGUAGES))),test-mixed)
test-unit:
	"$(PYTHON)" -m unittest discover -s tests/unit -v
.PHONY: bench-cenv
bench-cenv:
	"$(PYTHON)" tests/bench_cenv.py
.PHONY: test-cenv2
test-cenv2: sdl-services
	"$(PYTHON)" -m unittest discover -s tests/unit -p 'test_cenv2.py' -v
	"$(PYTHON)" -m unittest discover -s tests/unit -p 'test_tcp_receive.py' -v
	CARGO_TARGET_DIR=$(CURDIR)/build/client-rust cargo test --offline --manifest-path sdk/client/rust/Cargo.toml
	SDL_ROOT="$(SDL_ROOT)" "$(PYTHON)" -m unittest tests.integration.test_broker tests.integration.test_external_client tests.integration.test_delivery_limits tests.integration.test_distributed tests.integration.test_input_process -v
.PHONY: test-inputs-v2
test-inputs-v2: sdl-services
	"$(PYTHON)" -m unittest discover -s tests/unit -p 'test_activation*.py' -v
	"$(PYTHON)" -m unittest discover -s tests/unit -p 'test_input_ipc.py' -v
	SDL_ROOT="$(SDL_ROOT)" "$(PYTHON)" -m unittest tests.integration.test_input_callbacks tests.integration.test_input_process -v
.PHONY: test-rust-inputs-v2
test-rust-inputs-v2: sdl-services
	CARGO_TARGET_DIR=$(CURDIR)/build/sdk-rust cargo test --offline --manifest-path sdk/rust/Cargo.toml
	SDL_ROOT="$(SDL_ROOT)" "$(PYTHON)" -m unittest tests.integration.test_rust_inputs -v
.PHONY: test-c-inputs-v2
test-c-inputs-v2: sdl-services
	SDL_ROOT="$(SDL_ROOT)" "$(PYTHON)" -m unittest tests.integration.test_c_inputs -v
.PHONY: test-java-inputs-v2
test-java-inputs-v2: sdl-services
	SDL_ROOT="$(SDL_ROOT)" "$(PYTHON)" -m unittest tests.integration.test_java_inputs -v
.PHONY: test-ada-inputs-v2
test-ada-inputs-v2: sdl-services
	SDL_ROOT="$(SDL_ROOT)" "$(PYTHON)" -m unittest tests.managed.test_ada_inputs -v
.PHONY: test-distributed-inputs-v2
test-distributed-inputs-v2: sdl-services
	SDL_ROOT="$(SDL_ROOT)" "$(PYTHON)" -m unittest tests.integration.test_distributed_inputs -v
.PHONY: test-example-inputs-v2
test-example-inputs-v2: sdl-services
	"$(PYTHON)" -m unittest discover -s tests/unit -p 'test_example_models.py' -v
	SDL_ROOT="$(SDL_ROOT)" "$(PYTHON)" -m unittest tests.integration.test_ose tests.integration.test_example_inputs tests.simulation.test_simulation.SimulationTests.test_extracted_project_example_and_scripted_inputs -v
test-sdk:
	CARGO_TARGET_DIR=$(CURDIR)/build/sdk-rust cargo test --offline --manifest-path sdk/rust/Cargo.toml
	CARGO_TARGET_DIR=$(CURDIR)/build/client-rust cargo test --offline --manifest-path sdk/client/rust/Cargo.toml
test-integration:
	SDL_ROOT="$(SDL_ROOT)" "$(PYTHON)" -m unittest discover -s tests/integration -v
.PHONY: test-integration-runtime test-integration-transport
test-integration-runtime:
	SDL_ROOT="$(SDL_ROOT)" "$(PYTHON)" tests/run_integration.py runtime
test-integration-transport:
	SDL_ROOT="$(SDL_ROOT)" "$(PYTHON)" tests/run_integration.py transport
test-distributed:
	SDL_ROOT="$(SDL_ROOT)" "$(PYTHON)" -m unittest discover -s tests/integration -p 'test_distributed.py' -v
.PHONY: test-parameters
test-parameters:
	SDL_ROOT="$(SDL_ROOT)" "$(PYTHON)" -m unittest discover -s tests/unit -p 'test_parameter*.py' -v
	SDL_ROOT="$(SDL_ROOT)" "$(PYTHON)" -m unittest discover -s tests/integration -p 'test_parameter*.py' -v
test-simulated:
	SDL_ROOT="$(SDL_ROOT)" "$(PYTHON)" -m unittest discover -s tests/simulation -v

# Recording and content filtering use the required SDL services.
.PHONY: test-recording
test-recording:
	PYTHONPATH="$(CURDIR)/src:$(SDL_ROOT)/runtime/python:$(SDL_ROOT)/runtime/filter/python:$(SDL_ROOT)/build/filter/release:$(SDL_ROOT)/build/recording-deps" "$(PYTHON)" -c 'import mcap, sdl_filter'
	SDL_ROOT="$(SDL_ROOT)" PYTHONPATH="$(CURDIR)/src:$(SDL_ROOT)/runtime/python:$(SDL_ROOT)/runtime/filter/python:$(SDL_ROOT)/build/filter/release:$(SDL_ROOT)/build/recording-deps" "$(PYTHON)" -m unittest discover -s tests/recording -v

.PHONY: client-native external-types external-native
client-native:
	CARGO_TARGET_DIR=$(CURDIR)/build/client-rust cargo build --offline --manifest-path sdk/client/rust/Cargo.toml
external-types:
	"$(PYTHON)" "$(SDL_ROOT)/sdlc/generator.py" -python -c -rust examples/acquisition/schemas build/generated/shared
external-native: external-types client-native
	CARGO_TARGET_DIR=$(CURDIR)/build/client-example cargo build --offline --manifest-path examples/external/rust/Cargo.toml
	$(CC) -std=c11 -D_POSIX_C_SOURCE=200809L -Wall -Wextra -Werror -O2 -Isdk/client/c -Ibuild/generated/shared/c -I"$(SDL_ROOT)/runtime/c" sdk/client/c/ose_client.c examples/external/client.c build/generated/shared/c/*.c "$(SDL_ROOT)/runtime/c/type_engine.c" "$(SDL_ROOT)/runtime/c/type_registry.c" "$(SDL_ROOT)/runtime/c/type_codec.c" "$(SDL_ROOT)/runtime/c/sdl_wire.c" "$(SDL_ROOT)/runtime/c/sdl_dynamic.c" "$(SDL_ROOT)/runtime/c/type_display.c" build/client-rust/debug/libsdl_ose_client.a -ldl -lm -lpthread -o build/external-c

# Dedicated targets are strict: missing toolchains are failures, never skips.
export TEST_LANGUAGES
.PHONY: test-java test-ada client-java client-ada
test-java:
	SDL_ROOT="$(SDL_ROOT)" "$(PYTHON)" -m unittest discover -s tests/managed -p 'test_java*.py' -v
test-ada:
	SDL_ROOT="$(SDL_ROOT)" "$(PYTHON)" -m unittest discover -s tests/managed -p 'test_ada*.py' -v
client-java:
	"$(PYTHON)" -m ose.modeling.client_build "$(SDL_ROOT)" "$(CURDIR)/build/client-java"
client-ada:
	"$(PYTHON)" -m ose.modeling.ada_client_build "$(SDL_ROOT)" "$(CURDIR)/build/client-ada"

.PHONY: test-mixed
test-mixed:
	SDL_ROOT="$(SDL_ROOT)" "$(PYTHON)" -m unittest discover -s tests/managed -p 'test_mixed.py' -v

include ../tools/sdl-services.mk
build check test-unit test-integration test-integration-runtime test-integration-transport test-distributed test-parameters test-simulated test-recording test-java test-ada test-mixed: sdl-services
