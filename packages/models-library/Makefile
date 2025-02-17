#
# Targets for DEVELOPMENT of models Library
#
include ../../scripts/common.Makefile
include ../../scripts/common-package.Makefile

.PHONY: requirements
requirements: ## compiles pip requirements (.in -> .txt)
	@$(MAKE_C) requirements reqs


.PHONY: install-dev install-prod install-ci
install-dev install-prod install-ci: _check_venv_active ## install app in development/production or CI mode
	# installing in $(subst install-,,$@) mode
	pip-sync requirements/$(subst install-,,$@).txt


.PHONY: tests tests-ci
tests: ## runs unit tests
	# running unit tests
	@pytest \
		--asyncio-mode=auto \
		--color=yes \
		--cov-config=../../.coveragerc \
		--cov-report=term-missing \
		--cov=models_library \
		--durations=10 \
		--exitfirst \
		--failed-first \
		--pdb \
		-vv \
		$(CURDIR)/tests

tests-ci: ## runs unit tests [ci-mode]
	# running unit tests
	@pytest \
		--asyncio-mode=auto \
		--color=yes \
		--cov-append \
		--cov-config=../../.coveragerc \
		--cov-report=term-missing \
		--cov-report=xml \
		--cov=models_library \
		--durations=10 \
		--log-date-format="%Y-%m-%d %H:%M:%S" \
    --log-format="%(asctime)s %(levelname)s %(message)s" \
    --verbose \
    -m "not heavy_load" \
		$(CURDIR)/tests

.PHONY: project-jsonschema.ignore.json
project-jsonschema.ignore.json: ## creates project-v0.0.1.json for DEV purposes
	python3 -c "from models_library.projects import Project; print(Project.schema_json(indent=2))" > $@

.PHONY: service-jsonschema.ignore.json
node-meta-jsonschema.ignore.json: ## creates node-meta-v0.0.1.json for DEV purposes
	python3 -c "from models_library.services import ServiceDockerData as cls; print(cls.schema_json(indent=2))" > $@

DOCKER_API_VERSION ?= 1.41
.PHONY: docker_rest_api.py
docker_rest_api.py: ## auto-generates pydantic models for Docker REST API models
	# auto-generates $@ from $<
	@$(SCRIPTS_DIR)/openapi-pydantic-models-generator.bash \
			--url https://docs.docker.com/engine/api/v$(DOCKER_API_VERSION).yaml \
			--output $@

	# formats
	@black $@
	# copy output to src/models_library/generated_models...
	@mkdir --parents src/models_library/generated_models
	@mv $@ src/models_library/generated_models/$@
	@touch src/models_library/generated_models/__init__.py
	# done
