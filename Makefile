.PHONY: test demo ordering hazards benchmark package-check

test:
	python3 -m pytest -q

demo:
	PYTHONPATH=. python3 examples/payments.py

ordering:
	PYTHONPATH=. python3 examples/ordering.py

hazards:
	PYTHONPATH=. python3 -m replaycheck hazards examples/orders.jsonl

benchmark:
	PYTHONPATH=. python3 benchmarks/replaycheck_bench.py

package-check:
	python3 -m pip wheel . --no-build-isolation --no-deps --wheel-dir /tmp/replaycheck-wheel
