# Build the required SDL services with the same Python as their consumers.
.PHONY: sdl-services
sdl-services:
	+$(MAKE) -C "$(SDL_ROOT)" services PYTHON="$(PYTHON)"
