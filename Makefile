# Convenience targets. Each one is a thin wrapper; the README shows the raw commands.
PYTHON_VENV := python/.venv
PY := $(PYTHON_VENV)/bin/python
GENERATED_DIR := fixtures/generated
# Page counts for the scaling ladder (override: make fixtures-long LADDER="1 10 50").
LADDER ?= 1 10 100 500
LADDER_KIND ?= mixed
LADDER_SEED ?= 1

.PHONY: setup pdfium fixture fixtures-long test test-python test-rust bench bench-scaling clean

setup: $(PY)

$(PY):
	python3 -m venv $(PYTHON_VENV)
	$(PY) -m pip install --quiet -e "python[dev]"

pdfium:
	scripts/fetch-pdfium.sh

fixture: setup
	$(PY) scripts/generate_pdfs.py --fixture

# Size ladder for the scaling benchmark, gitignored. Names are zero-padded so
# directory order is page order.
fixtures-long: setup
	mkdir -p $(GENERATED_DIR)
	for pages in $(LADDER); do \
	  $(PY) scripts/generate_pdfs.py --kind $(LADDER_KIND) --seed $(LADDER_SEED) --pages $$pages \
	    --out $(GENERATED_DIR)/$(LADDER_KIND)-$$(printf '%04d' $$pages)p.pdf || exit 1; \
	done

test: test-python test-rust

test-python: setup
	cd python && .venv/bin/python -m pytest -q

test-rust: pdfium
	PDFIUM_DYNAMIC_LIB_PATH=$${PDFIUM_DYNAMIC_LIB_PATH:-$(CURDIR)/.pdfium/lib} cargo test --manifest-path rust/Cargo.toml

bench: setup pdfium
	scripts/benchmark.sh

# Benchmarks exactly the LADDER rungs, not whatever else is in the directory.
bench-scaling: fixtures-long pdfium
	scripts/benchmark.sh --scaling $(foreach pages,$(LADDER),$(GENERATED_DIR)/$(LADDER_KIND)-$(shell printf '%04d' $(pages))p.pdf)

clean:
	rm -rf $(PYTHON_VENV) .pdfium rust/target $(GENERATED_DIR)
