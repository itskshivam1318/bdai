# The app lives in app/. This proxies every target there, so a fresh clone runs
# `make setup && make dev` from the repo root without knowing the layout.
# The real Makefile is app/Makefile.

.DEFAULT_GOAL := help

.PHONY: help
help:
	@$(MAKE) -C app help

# Guard: without this, the match-anything rule below makes `make` try to remake
# this Makefile through the proxy.
Makefile: ;

%:
	@$(MAKE) -C app $@
