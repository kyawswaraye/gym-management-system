.PHONY: check-python check-node db-up db-down db-logs migrate backend-install backend-check backend-test backend-test-pg web-install web-check web-test mobile-install mobile-check mobile-test mobile-android mobile-android-release mobile-ios e2e openapi-export openapi-generate openapi-check check validate-production production-images

ifneq ($(wildcard .env),)
include .env
export
endif

check-python:
	@bash scripts/check-python.sh

check-node:
	@bash scripts/check-node.sh

db-up:
	docker compose up -d postgres

db-down:
	docker compose down

db-logs:
	docker compose logs -f postgres

migrate: check-python
	cd backend && \
		DATABASE_ROLE_MODE=MIGRATION \
		MIGRATION_DB_USER=$(or $(MIGRATION_DB_USER),gym_migration) \
		MIGRATION_DB_PASSWORD=$(or $(MIGRATION_DB_PASSWORD),$(POSTGRES_MIGRATION_PASSWORD)) \
		DB_NAME=$(or $(DB_NAME),$(POSTGRES_DB),gym_platform) \
		DB_HOST=$(or $(DB_HOST),$(POSTGRES_HOST),localhost) \
		DB_PORT=$(or $(DB_PORT),$(POSTGRES_PORT),5432) \
		.venv/bin/python manage.py migrate

backend-install: check-python
	python3 -m venv backend/.venv
	backend/.venv/bin/pip install --upgrade pip
	backend/.venv/bin/pip install -r backend/requirements/development.txt

backend-check: check-python
	cd backend && .venv/bin/ruff check .
	cd backend && .venv/bin/ruff format --check .
	cd backend && .venv/bin/mypy apps config
	cd backend && DATABASE_ROLE_MODE=MIGRATION .venv/bin/python manage.py makemigrations --check --dry-run

backend-test: check-python
	cd backend && .venv/bin/pytest -m "not postgresql"

backend-test-pg: check-python
	cd backend && \
		RUN_POSTGRESQL_TESTS=1 \
		TEST_DB_USER=$(or $(TEST_DB_USER),gym_admin) \
		TEST_DB_PASSWORD=$(or $(TEST_DB_PASSWORD),$(POSTGRES_ADMIN_PASSWORD)) \
		BFF_SHARED_SECRET=$(or $(BFF_SHARED_SECRET),test-bff-shared-secret-32-chars-min) \
		.venv/bin/pytest -m postgresql

web-install: check-node
	npm --prefix web ci

web-check: check-node
	npm --prefix web run format:check
	npm --prefix web run lint
	npm --prefix web run typecheck
	npm --prefix web run build

web-test: check-node
	npm --prefix web test -- --run

mobile-install: check-node
	npm --prefix mobile ci

mobile-check: check-node
	npm --prefix mobile run format:check
	npm --prefix mobile run lint
	npm --prefix mobile run typecheck

mobile-test: check-node
	npm --prefix mobile test -- --runInBand
	npm --prefix mobile run test:e2e

mobile-android: check-node
	cd mobile/android && ./gradlew assembleDebug

mobile-android-release: check-node
	cd mobile && npm run android:release

mobile-ios: check-node
	cd mobile && bundle install
	cd mobile/ios && bundle exec pod install
	cd mobile/ios && xcodebuild \
		-workspace GymMember.xcworkspace \
		-scheme GymMember \
		-configuration Debug \
		-sdk iphonesimulator \
		-destination 'generic/platform=iOS Simulator' \
		ARCHS=arm64 \
		ONLY_ACTIVE_ARCH=YES \
		CODE_SIGNING_ALLOWED=NO \
		build

e2e: check-node check-python
	bash scripts/e2e.sh

openapi-export: check-python
	cd backend && .venv/bin/python manage.py spectacular \
		--file ../contracts/openapi.v1.json \
		--format openapi-json \
		--validate \
		--fail-on-warn

openapi-generate: openapi-export check-node
	npm --prefix contracts/generator ci
	npm --prefix contracts/generator run generate

openapi-check: openapi-generate
	git diff --exit-code -- contracts/openapi.v1.json web/src/types/api.generated.ts mobile/src/types/api.generated.ts

check: backend-check backend-test web-check web-test mobile-check mobile-test openapi-check

validate-production:
	bash scripts/validate-production-artifacts.sh

production-images:
	docker build -f backend/Dockerfile -t gym-api:local backend
	docker build -f web/Dockerfile -t gym-web:local web
	docker build -f infra/postgres/Dockerfile -t gym-postgres:local infra/postgres
