.PHONY: test demo ordering hazards

test:
	python3 -m pytest -q

demo:
	PYTHONPATH=. python3 examples/payments.py

ordering:
	PYTHONPATH=. python3 examples/ordering.py

hazards:
	PYTHONPATH=. python3 -m replaycheck hazards examples/orders.jsonl
