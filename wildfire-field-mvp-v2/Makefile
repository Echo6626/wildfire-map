.PHONY: run test

run:
	uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

test:
	python -m unittest discover -s tests -v
