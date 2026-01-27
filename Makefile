# =============================================================================
# Alpha NextGen - Development Workflow Automation
# =============================================================================
# Usage:
#   make help          - Show all available commands
#   make setup         - First-time setup
#   make test          - Run all tests
#   make branch name=feature/va/my-feature  - Create new branch
# =============================================================================

.PHONY: help setup test lint format check branch commit clean validate-config phase1-check verify

# Default target
help:
	@echo "=============================================="
	@echo "Alpha NextGen - Available Commands"
	@echo "=============================================="
	@echo ""
	@echo "Setup:"
	@echo "  make setup          - First-time development setup"
	@echo "  make verify         - Verify setup is working"
	@echo ""
	@echo "Development:"
	@echo "  make branch name=X  - Create feature branch (from develop)"
	@echo "  make test           - Run all tests"
	@echo "  make test-critical  - Run critical tests only"
	@echo "  make lint           - Run linting (black, isort)"
	@echo "  make format         - Auto-format code"
	@echo "  make check          - Run all checks (lint + test)"
	@echo ""
	@echo "Validation:"
	@echo "  make validate-config - Validate config.py against specs"
	@echo "  make phase1-check    - Run all Phase 1 validations"
	@echo "  make docs-parity     - Check code-to-spec synchronization"
	@echo ""
	@echo "Git:"
	@echo "  make status         - Show git status and branch"
	@echo "  make pr             - Create PR to develop"
	@echo ""
	@echo "Cleanup:"
	@echo "  make clean          - Remove Python cache files"
	@echo "=============================================="

# =============================================================================
# Setup
# =============================================================================

setup:
	@echo "Setting up development environment..."
	python3.11 -m venv venv
	@echo "Activating venv and installing dependencies..."
	. venv/bin/activate && pip install -r requirements.lock
	@echo "Installing pre-commit hooks..."
	. venv/bin/activate && pip install pre-commit && pre-commit install
	@echo ""
	@echo "=============================================="
	@echo "Setup complete! Activate venv with:"
	@echo "  source venv/bin/activate"
	@echo "=============================================="

# =============================================================================
# Development
# =============================================================================

test:
	pytest tests/ -v

test-critical:
	pytest tests/test_architecture_boundaries.py tests/test_target_weight_contract.py tests/test_smoke_integration.py -v

test-coverage:
	pytest tests/ --cov=engines --cov=portfolio --cov=models --cov-report=term-missing

lint:
	@echo "Running black check..."
	black --check engines/ portfolio/ models/ utils/ 2>/dev/null || true
	@echo "Running isort check..."
	isort --check engines/ portfolio/ models/ utils/ 2>/dev/null || true

format:
	@echo "Formatting with black..."
	black engines/ portfolio/ models/ utils/ 2>/dev/null || true
	@echo "Sorting imports with isort..."
	isort engines/ portfolio/ models/ utils/ 2>/dev/null || true

check: lint test
	@echo "All checks passed!"

# =============================================================================
# Validation
# =============================================================================

validate-config:
	@echo "Validating config.py against specs..."
	python scripts/validate_config.py

docs-parity:
	@echo "Checking code-to-spec synchronization..."
	python scripts/check_spec_parity.py

phase1-check: lint test-critical validate-config
	@echo ""
	@echo "=============================================="
	@echo "Phase 1 validation complete!"
	@echo "=============================================="

verify:
	@echo "Verifying development setup..."
	@echo ""
	@echo "1. Python version:"
	@python --version
	@echo ""
	@echo "2. Running critical tests..."
	@pytest tests/test_architecture_boundaries.py tests/test_target_weight_contract.py tests/test_smoke_integration.py -v --tb=short
	@echo ""
	@echo "=============================================="
	@echo "Setup verified! You're ready to develop."
	@echo "=============================================="

# =============================================================================
# Git Workflow
# =============================================================================

# Check we're not on a protected branch
_check-branch:
	@branch=$$(git branch --show-current); \
	if [ "$$branch" = "main" ] || [ "$$branch" = "develop" ]; then \
		echo ""; \
		echo "========================================"; \
		echo "ERROR: You're on protected branch: $$branch"; \
		echo "========================================"; \
		echo ""; \
		echo "Create a feature branch first:"; \
		echo "  make branch name=feature/<initials>/<description>"; \
		echo ""; \
		exit 1; \
	fi

branch:
ifndef name
	@echo "ERROR: Please specify branch name"
	@echo "Usage: make branch name=feature/va/my-feature"
	@exit 1
endif
	git checkout develop
	git pull origin develop
	git checkout -b $(name)
	@echo ""
	@echo "Created branch: $(name)"
	@echo "You can now make changes and commit."

status:
	@echo "Current branch: $$(git branch --show-current)"
	@echo ""
	@git status --short

pr: _check-branch
	@echo "Creating PR to develop..."
	gh pr create --base develop

# =============================================================================
# Cleanup
# =============================================================================

clean:
	find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete 2>/dev/null || true
	find . -type f -name "*.pyo" -delete 2>/dev/null || true
	find . -type d -name ".pytest_cache" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name ".mypy_cache" -exec rm -rf {} + 2>/dev/null || true
	@echo "Cleaned up cache files."
