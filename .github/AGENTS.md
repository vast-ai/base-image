# CI/CD Workflow Conventions

This repository builds Docker images for ML/AI tools and pushes them to DockerHub.
All build workflows follow a strict 4-job pipeline pattern. **Do not deviate from these conventions.**

## Repository Layout

```
.github/
  actions/
    check-dockerhub-release/   # Polls DockerHub for new upstream version tags
    check-github-release/      # Polls GitHub releases for new upstream versions
    maximize-build-space/      # Frees disk on runner (required for large image builds)
  workflows/
    build-*.yml                # One per image (comfyui, ollama, sglang, vllm, etc.)
    notify-slack.yml           # Reusable workflow called by every build workflow
external/
  <name>/                      # Dockerfile + assets for external-image builds (ollama, vllm, sglang)
derivatives/
  pytorch/derivatives/<name>/  # Dockerfile + assets for pytorch-derived builds (comfyui, ostris)
```

## Required Secrets

All build workflows require:
- `DOCKERHUB_USERNAME` - DockerHub login
- `DOCKERHUB_TOKEN` - DockerHub access token
- `DOCKERHUB_NAMESPACE` - DockerHub org/user namespace (used as image prefix)

Optional:
- `SLACK_WEBHOOK_URL` - Slack incoming webhook for build notifications

## Standard Workflow Inputs

Every `workflow_dispatch` must define these inputs:

| Input | Description | Required | Default |
|---|---|---|---|
| `*_VERSION` or `*_REF` | Upstream version/ref | false | (auto-detect) |
| `DOCKERHUB_REPO` | Target repo name under namespace | true | image-specific |
| `MULTI_ARCH` | Build for linux/arm64 too | false | `false` (boolean) |
| `CUSTOM_IMAGE_TAG` | Override auto-derived tag | false | (empty) |

## Standard Environment Variables

```yaml
env:
  DEFAULT_DOCKERHUB_REPO: "<image-name>"
  DEFAULT_MULTI_ARCH: "false"
  RELEASE_AGE_THRESHOLD: 43200  # 12 hours, in seconds
```

---

## The 4-Job Pipeline

Every build workflow **must** define exactly these jobs in this order:

### 1. `preflight`

Decides whether the build should run and resolves version info.

**Outputs** (at minimum):
- `should-run` - `"true"` or `"false"`
- A version/ref output (e.g., `ollama-version`, `vllm-version`, `comfyui-ref`)

**Steps pattern:**
1. Checkout
2. Check for required secrets
3. Check release (via `check-dockerhub-release` or `check-github-release` action)
4. *(optional)* Build matrix from variant tags (for multi-CUDA builds)
5. Determine if build should run (sets `should-run` output)

### 2. `build`

Builds and pushes the Docker image(s). **Must use a matrix strategy and record the full image reference as an artifact.**

**Always use a matrix strategy**, even for images with a single variant. This keeps every workflow structurally identical, makes `collect-tags` work uniformly, and makes it trivial to add variants later without restructuring.

```yaml
build:
  needs: preflight
  if: needs.preflight.outputs.should-run == 'true'
  runs-on: ubuntu-latest
  environment: ${{ github.event_name == 'workflow_dispatch' && 'production' || '' }}
  permissions:
    contents: read

  strategy:
    fail-fast: false
    matrix:
      base_image:                       # Always define a matrix, even with one entry
        - upstream/image                # Single-variant example
        # - upstream/image-variant-b    # Add more entries to build additional variants
```

**FULL_IMAGE derivation** (must account for all overridable inputs):

The `Derive image tag` step must set `FULL_IMAGE` and `MATRIX_ID` as env vars.
`MATRIX_ID` is **always** derived from the matrix value via md5, never a static string.

```yaml
    - name: Derive image tag
      id: meta
      run: |
        DOCKERHUB_REPO="${{ inputs.DOCKERHUB_REPO || env.DEFAULT_DOCKERHUB_REPO }}"

        IMAGE_TAG="${{ inputs.CUSTOM_IMAGE_TAG }}"
        if [[ -z "$IMAGE_TAG" ]]; then
          IMAGE_TAG="<auto-derived-tag>"  # version, version-cuda-X.Y, etc.
        fi

        FULL_IMAGE="${{ secrets.DOCKERHUB_NAMESPACE }}/${DOCKERHUB_REPO}:${IMAGE_TAG}"

        # Create a safe filename from the matrix value
        MATRIX_ID=$(echo "${{ matrix.base_image }}" | md5sum | cut -c1-8)

        echo "FULL_IMAGE=$FULL_IMAGE" >> $GITHUB_ENV
        echo "IMAGE_TAG=$IMAGE_TAG" >> $GITHUB_ENV
        echo "MATRIX_ID=$MATRIX_ID" >> $GITHUB_ENV
```

**Critical steps after "Build and push":**

```yaml
    - name: Record built tag
      run: |
        mkdir -p built-tags
        echo "${{ env.FULL_IMAGE }}" > built-tags/tag-${{ env.MATRIX_ID }}.txt

    - name: Upload built tag
      uses: actions/upload-artifact@v4
      with:
        name: built-tag-${{ env.MATRIX_ID }}
        path: built-tags/
        retention-days: 1
```

The artifact name **must** match the glob pattern `built-tag-*` so `collect-tags` can find it.

### 3. `collect-tags`

Downloads all tag artifacts and aggregates them into a JSON array.
This job is **identical across all workflows** — copy it verbatim:

```yaml
collect-tags:
  needs: [preflight, build]
  if: always() && needs.preflight.outputs.should-run == 'true'
  runs-on: ubuntu-latest
  outputs:
    all-tags: ${{ steps.collect.outputs.tags }}
  steps:
    - name: Download all tag artifacts
      uses: actions/download-artifact@v4
      with:
        pattern: built-tag-*
        path: all-tags/
        merge-multiple: true

    - name: Aggregate tags
      id: collect
      run: |
        if [[ -d all-tags ]] && [[ "$(ls -A all-tags 2>/dev/null)" ]]; then
          TAGS=$(cat all-tags/*.txt | jq -R -s -c 'split("\n") | map(select(length > 0))')
          echo "Found tags: $TAGS"
        else
          echo "::warning::No tags found - build may have failed"
          TAGS="[]"
        fi

        echo "tags=$TAGS" >> $GITHUB_OUTPUT
```

### 4. `notify`

Calls the reusable `notify-slack.yml` workflow. Also **identical across all workflows** except for `image-name` and `image-ref`:

```yaml
notify:
  needs: [preflight, build, collect-tags]
  if: always() && needs.preflight.outputs.should-run == 'true'
  uses: ./.github/workflows/notify-slack.yml
  with:
    build-result: ${{ needs.build.result }}
    image-name: "<Human-Readable Name>"
    image-ref: ${{ needs.preflight.outputs.<version-output> }}
    image-tags: ${{ needs.collect-tags.outputs.all-tags }}
    trigger: ${{ github.event_name }}
    run-url: ${{ github.server_url }}/${{ github.repository }}/actions/runs/${{ github.run_id }}
  secrets:
    SLACK_WEBHOOK_URL: ${{ secrets.SLACK_WEBHOOK_URL }}
```

**`image-tags` must always come from `collect-tags.outputs.all-tags`**, never from a hardcoded format string.
This ensures the Slack notification shows the full `namespace/repo:tag` references, which remain correct even when `DOCKERHUB_NAMESPACE`, `DOCKERHUB_REPO`, or `CUSTOM_IMAGE_TAG` are overridden.

---

## Common Build Step Pattern

All build jobs share this setup sequence:

```yaml
strategy:
  fail-fast: false
  matrix:
    base_image:
      - upstream/image

steps:
  - name: Checkout
    uses: actions/checkout@v4

  - name: Maximize build space
    uses: ./.github/actions/maximize-build-space

  - name: Set up QEMU
    uses: docker/setup-qemu-action@v3

  - name: Set up Docker Buildx
    uses: docker/setup-buildx-action@v3

  - name: Docker Hub login
    uses: docker/login-action@v3
    with:
      username: ${{ secrets.DOCKERHUB_USERNAME }}
      password: ${{ secrets.DOCKERHUB_TOKEN }}

  - name: Derive image tag
    # ... (see FULL_IMAGE derivation above)

  - name: Build and push
    run: |
      # Resolve MULTI_ARCH from input or default
      if [[ "${{ github.event_name }}" == "workflow_dispatch" ]]; then
        MULTI_ARCH="${{ inputs.MULTI_ARCH }}"
      else
        MULTI_ARCH="${{ env.DEFAULT_MULTI_ARCH }}"
      fi

      platforms="linux/amd64"
      [[ "$MULTI_ARCH" == "true" ]] && platforms+=",linux/arm64"

      cd <dockerfile-directory>
      docker buildx build \
        --platform "$platforms" \
        --build-arg <BASE_ARG>=... \
        -t ${{ env.FULL_IMAGE }} \
        . --push

  - name: Record built tag
    # ... (see above)

  - name: Upload built tag
    # ... (see above)
```

## Reusable Actions

### `check-dockerhub-release`
Polls DockerHub for upstream version tags. Use for images whose upstream publishes to DockerHub (ollama, vllm, sglang).

Key outputs: `new-release`, `resolved-version`, `variant-tags` (JSON array of all tags for that version).

### `check-github-release`
Polls GitHub releases API. Use for images whose upstream publishes GitHub releases (ComfyUI).

Key outputs: `new-release`, `resolved-ref`, `release-tag`, `published-at`.

### `maximize-build-space`
Frees ~30GB on the GitHub runner. **Always include this** — ML Docker images are large.

## Checklist for New Workflows

- [ ] File named `.github/workflows/build-<name>.yml`
- [ ] Header comment lists required and optional secrets
- [ ] Schedule cron set to `'0 0,12 * * *'` (every 12 hours)
- [ ] `workflow_dispatch` inputs include VERSION/REF, DOCKERHUB_REPO, MULTI_ARCH, CUSTOM_IMAGE_TAG
- [ ] `env` block sets DEFAULT_DOCKERHUB_REPO, DEFAULT_MULTI_ARCH, RELEASE_AGE_THRESHOLD
- [ ] 4 jobs defined: `preflight` -> `build` -> `collect-tags` -> `notify`
- [ ] `build` job uses `strategy.matrix` (even for single-variant images)
- [ ] `build` job derives `MATRIX_ID` from matrix value via `md5sum | cut -c1-8`
- [ ] `build` job derives `FULL_IMAGE` from `DOCKERHUB_NAMESPACE/DOCKERHUB_REPO:IMAGE_TAG`
- [ ] `build` job records `FULL_IMAGE` to `built-tags/tag-$MATRIX_ID.txt` and uploads as `built-tag-$MATRIX_ID`
- [ ] `collect-tags` job is copied verbatim from existing workflow
- [ ] `notify` job passes `image-tags: ${{ needs.collect-tags.outputs.all-tags }}`
- [ ] `notify` job `needs` includes `collect-tags`

## Tag Format

When a tag includes a **commit hash** rather than a proper version number, append a hyphenated ISO 8601 date (`YYYY-MM-DD`) to disambiguate builds. Tags with proper version numbers do not need a date.

```bash
DATE=$(date -u +%Y-%m-%d)
# Produces: 2026-02-02
# NOT:      20260202
```

Example tags:
- `lllyasviel-a1b2c3d-2026-02-02-cuda-12.9` (commit hash — date required)
- `v1.2.3-cuda-12.8` (version number — no date needed)
