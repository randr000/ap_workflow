.PHONY: install test scenarios benchmark app api seed invoices clean

install:        ## Install the package (editable) with test dependencies
	pip install -e .

test:           ## Run the unit + integration test suite
	pytest

scenarios:      ## Run the 10 demo scenarios against the 20-invoice dataset
	python -m demo.scenarios

benchmark:      ## Run the 100-invoice synthetic benchmark
	python -m demo.benchmark

app:            ## Launch the Streamlit demo UI
	streamlit run streamlit_app.py

api:            ## Launch the FastAPI service (docs at http://localhost:8000/docs)
	uvicorn app.api.routes:app --reload

seed:           ## Reset the configured database and seed master data
	python -m app.db.seed

invoices:       ## Regenerate data/invoices/*.txt from demo/dataset.py
	python -m demo.dataset

clean:          ## Remove local database and caches
	rm -f ap_demo.db
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
	rm -rf .pytest_cache
