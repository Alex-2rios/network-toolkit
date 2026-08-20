.DEFAULT_GOAL := help
.PHONY: help install test lint format demo clean

help:
	@echo "install  install the package in editable mode with the dev extras"
	@echo "test     run the test suite"
	@echo "lint     run ruff"
	@echo "format   let ruff fix what it can"
	@echo "demo     run a few commands so you can see the output"
	@echo "clean    remove build and cache directories"

install:
	pip install -e ".[dev]" ruff

test:
	pytest

lint:
	ruff check .

format:
	ruff check --fix .

demo:
	python -m nettool subnet 172.16.32.0/20
	python -m nettool vlsm 192.168.1.0/24 sales:50 it:25 servers:12 wan:2
	python -m nettool arp

clean:
	rm -rf .pytest_cache build dist *.egg-info
	find . -name __pycache__ -type d -exec rm -rf {} +
