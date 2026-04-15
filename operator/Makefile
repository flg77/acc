# ACC Operator Makefile
# Targets follow operator-sdk conventions.

# Tool versions
ENVTEST_K8S_VERSION ?= 1.31.0
CONTROLLER_TOOLS_VERSION ?= v0.16.1
OPERATOR_SDK_VERSION ?= v1.36.0

# Image settings
IMG ?= quay.io/redhat-ai-dev/acc-operator:$(VERSION)
VERSION ?= 0.1.0
BUNDLE_IMG ?= quay.io/redhat-ai-dev/acc-operator-bundle:$(VERSION)

# Platforms
PLATFORMS ?= linux/arm64,linux/amd64

# Directories
LOCALBIN ?= $(shell pwd)/bin
$(LOCALBIN):
	mkdir -p $(LOCALBIN)

## Tool binaries
CONTROLLER_GEN ?= $(LOCALBIN)/controller-gen
ENVTEST ?= $(LOCALBIN)/setup-envtest
OPERATOR_SDK ?= $(LOCALBIN)/operator-sdk

# Go settings
GOFLAGS ?=
export GOFLAGS

.PHONY: all
all: build

##@ General

.PHONY: help
help: ## Display this help.
	@awk 'BEGIN {FS = ":.*##"; printf "\nUsage:\n  make \033[36m<target>\033[0m\n"} /^[a-zA-Z_0-9-]+:.*?##/ { printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2 } /^##@/ { printf "\n\033[1m%s\033[0m\n", substr($$0, 5) } ' $(MAKEFILE_LIST)

##@ Development

.PHONY: manifests
manifests: controller-gen ## Generate WebhookConfiguration, ClusterRole and CRD objects.
	$(CONTROLLER_GEN) rbac:roleName=manager-role crd webhook paths="./..." output:crd:artifacts:config=config/crd/bases output:rbac:artifacts:config=config/rbac

.PHONY: generate
generate: controller-gen ## Generate code (DeepCopy, etc.)
	$(CONTROLLER_GEN) object:headerFile="hack/boilerplate.go.txt" paths="./..."

.PHONY: fmt
fmt: ## Run go fmt.
	go fmt ./...

.PHONY: vet
vet: ## Run go vet.
	go vet ./...

.PHONY: test
test: manifests generate fmt vet envtest ## Run tests.
	KUBEBUILDER_ASSETS="$(shell $(ENVTEST) use $(ENVTEST_K8S_VERSION) --bin-path $(LOCALBIN) -p path)" go test ./... -coverprofile cover.out

.PHONY: test-unit
test-unit: ## Run unit tests only (no envtest).
	go test ./test/unit/... -v

##@ Build

.PHONY: build
build: manifests generate fmt vet ## Build operator binary.
	go build -o bin/manager cmd/main.go

.PHONY: run
run: manifests generate fmt vet ## Run the operator locally against the configured Kubernetes cluster.
	go run ./cmd/main.go

.PHONY: docker-build
docker-build: ## Build container image.
	docker build -t ${IMG} .

.PHONY: docker-push
docker-push: ## Push container image.
	docker push ${IMG}

.PHONY: build-installer
build-installer: manifests generate kustomize ## Generate a consolidated install YAML.
	mkdir -p dist
	cd config/manager && $(KUSTOMIZE) edit set image controller=${IMG}
	$(KUSTOMIZE) build config/default > dist/install.yaml

##@ Deployment

.PHONY: install
install: manifests kustomize ## Install CRDs into cluster.
	$(KUSTOMIZE) build config/crd | kubectl apply -f -

.PHONY: uninstall
uninstall: manifests kustomize ## Uninstall CRDs.
	$(KUSTOMIZE) build config/crd | kubectl delete --ignore-not-found=true -f -

.PHONY: deploy
deploy: manifests kustomize ## Deploy operator to cluster.
	cd config/manager && $(KUSTOMIZE) edit set image controller=${IMG}
	$(KUSTOMIZE) build config/default | kubectl apply -f -

.PHONY: undeploy
undeploy: kustomize ## Undeploy operator.
	$(KUSTOMIZE) build config/default | kubectl delete --ignore-not-found=true -f -

##@ Bundle (OLM)

.PHONY: bundle
bundle: manifests kustomize operator-sdk ## Generate bundle manifests and metadata.
	$(OPERATOR_SDK) generate kustomize manifests -q
	cd config/manager && $(KUSTOMIZE) edit set image controller=$(IMG)
	$(KUSTOMIZE) build config/manifests | $(OPERATOR_SDK) generate bundle \
		-q --overwrite --version $(VERSION) $(BUNDLE_METADATA_OPTS)
	$(OPERATOR_SDK) bundle validate ./bundle

.PHONY: bundle-build
bundle-build: ## Build bundle image.
	docker build -f bundle.Dockerfile -t $(BUNDLE_IMG) .

.PHONY: bundle-push
bundle-push: ## Push bundle image.
	docker push $(BUNDLE_IMG)

##@ Dependencies

.PHONY: controller-gen
controller-gen: $(CONTROLLER_GEN) ## Download controller-gen.
$(CONTROLLER_GEN): $(LOCALBIN)
	$(call go-install-tool,$(CONTROLLER_GEN),sigs.k8s.io/controller-tools/cmd/controller-gen,$(CONTROLLER_TOOLS_VERSION))

.PHONY: envtest
envtest: $(ENVTEST) ## Download setup-envtest.
$(ENVTEST): $(LOCALBIN)
	$(call go-install-tool,$(ENVTEST),sigs.k8s.io/controller-runtime/tools/setup-envtest,latest)

.PHONY: operator-sdk
operator-sdk: $(OPERATOR_SDK) ## Download operator-sdk.
$(OPERATOR_SDK): $(LOCALBIN)
	@{ \
	set -e; \
	mkdir -p $(LOCALBIN); \
	curl -sSLo $(OPERATOR_SDK) https://github.com/operator-framework/operator-sdk/releases/download/$(OPERATOR_SDK_VERSION)/operator-sdk_linux_amd64; \
	chmod +x $(OPERATOR_SDK); \
	}

define go-install-tool
@[ -f "$(1)" ] || { \
	set -e; \
	package=$(2)@$(3); \
	echo "Downloading $${package}"; \
	GOBIN=$(LOCALBIN) go install $${package}; \
}
endef
