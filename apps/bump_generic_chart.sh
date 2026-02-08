#!/bin/bash
#
# bump_generic_chart.sh - Bump the generic chart version and propagate it to all dependent app charts.
#
# WHEN TO RUN THIS SCRIPT:
#   Run this script ONLY after making changes to the apps/generic/ chart (templates, helpers,
#   values.schema.json, default values, etc.). The generic chart is a shared base dependency
#   used by most application charts in this repository.
#
#   Because Helm resolves dependencies by version, every app's Chart.yaml pins a specific
#   generic chart version. When you change the generic chart, a new version must be published
#   and every dependent Chart.yaml must be updated to reference it. This script automates that
#   entire process.
#
# WHEN YOU DO NOT NEED THIS SCRIPT:
#   - Editing only an app's own values.yaml or templates/ — ArgoCD picks those up automatically.
#   - Adding a brand-new app that already references the current generic chart version.
#   - Changing anything outside apps/generic/ (e.g., base-chart, bootstrap-chart, scripts).
#
# WHAT IT DOES (in order):
#   1. Increments the patch version of apps/generic/Chart.yaml (e.g., 0.1.17 -> 0.1.18).
#   2. Packages the generic chart into a .tgz and removes stale packages.
#   3. Finds every app chart that declares "name: generic" as a dependency.
#   4. Updates each app's Chart.yaml to reference the new generic version.
#   5. Runs "helm dependency update" in each app directory to regenerate the charts/ lock file.
#   6. Prints a summary of successes and failures.
#
# PREREQUISITES:
#   - helm CLI must be installed and on PATH.
#   - Must be run from the apps/ directory (cd apps && ./bump_generic_chart.sh).
#

# Stop at any error
set -e

# Set colors for output
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[0;33m'
NC='\033[0m' # No Color

# Store the directory the script was invoked from so we can return to it after
# entering subdirectories. This must be the apps/ directory.
ORIGINAL_DIR="$(pwd)"

# ──────────────────────────────────────────────────────────────────────────────
# PHASE 1: Bump the generic chart version
#
# Helm identifies chart versions by the "version:" field in Chart.yaml.
# A version bump is required so that dependent charts pick up the latest
# generic chart during "helm dependency update".
# ──────────────────────────────────────────────────────────────────────────────
echo -e "${YELLOW}Incrementing patch version of the generic chart...${NC}"
cd ./generic || { echo -e "${RED}Generic chart directory not found!${NC}"; exit 1; }

# Parse the current semver version (MAJOR.MINOR.PATCH) from the generic Chart.yaml
current_version=$(grep -E '^version:' Chart.yaml | awk '{print $2}')
echo -e "${YELLOW}Current version: ${current_version}${NC}"

IFS='.' read -ra version_parts <<< "$current_version"
major=${version_parts[0]}
minor=${version_parts[1]}
patch=${version_parts[2]}

# Automatically increment only the patch component (non-breaking change assumption)
new_patch=$((patch + 1))
new_version="${major}.${minor}.${new_patch}"
echo -e "${YELLOW}New version: ${new_version}${NC}"

# Write the new version back into the generic chart's Chart.yaml.
# The sed -i.bak / rm pattern ensures compatibility with both macOS (BSD sed) and Linux (GNU sed).
sed -i.bak "s/^version: ${current_version}/version: ${new_version}/" Chart.yaml && rm -f Chart.yaml.bak
echo -e "${GREEN}✓ Updated generic chart version to ${new_version}${NC}"

# ──────────────────────────────────────────────────────────────────────────────
# PHASE 2: Package the generic chart
#
# "helm package" creates a generic-<version>.tgz archive. App charts reference
# this archive via their "file://../generic" dependency. Helm needs a matching
# .tgz to resolve the dependency during "helm dependency update".
# ──────────────────────────────────────────────────────────────────────────────
echo -e "${YELLOW}Building the generic chart...${NC}"
helm package .
echo -e "${GREEN}✓ Successfully built generic chart${NC}"

# Remove .tgz files from previous versions to keep the directory clean
echo -e "${YELLOW}Cleaning up old generic chart packages...${NC}"
old_packages=$(find . -name "generic-*.tgz" -not -name "generic-${new_version}.tgz" | wc -l | tr -d ' ')
if [ "$old_packages" -gt 0 ]; then
    find . -name "generic-*.tgz" -not -name "generic-${new_version}.tgz" -delete
    echo -e "${GREEN}✓ Removed ${old_packages} old generic chart package(s)${NC}"
else
    echo -e "${GREEN}✓ No old generic chart packages to clean up${NC}"
fi

cd "$ORIGINAL_DIR"

# ──────────────────────────────────────────────────────────────────────────────
# PHASE 3: Propagate the new version to all dependent app charts
#
# Each app chart (e.g., apps/plex/Chart.yaml) pins a specific generic chart
# version in its dependencies section. This loop:
#   a) Updates that pinned version to the new one.
#   b) Runs "helm dependency update" so Helm downloads/links the new .tgz
#      and regenerates Chart.lock.
#
# Charts that do NOT depend on generic (e.g., custom-template apps) are skipped.
# ──────────────────────────────────────────────────────────────────────────────
echo -e "\n${YELLOW}Starting Helm dependency update for all charts...${NC}"

success_count=0
error_count=0

# Find all Chart.yaml files (excluding generic itself) to discover app charts
chart_dirs=$(find . -name "Chart.yaml" -not -path "./generic/*" -exec dirname {} \; | sort)

for dir in $chart_dirs
do
    # Double-guard: skip generic even if found by a different path
    if [[ "$dir" == "./generic" ]]; then
        echo -e "\n${YELLOW}Skipping dependency update for ${dir}${NC}"
        continue
    fi

    # Only process charts that actually list "name: generic" as a dependency
    if ! grep -q "name: generic" "$ORIGINAL_DIR/$dir/Chart.yaml"; then
        echo -e "\n${YELLOW}Skipping ${dir} (no generic dependency)${NC}"
        continue
    fi

    echo -e "\n${YELLOW}Processing ${dir}...${NC}"

    cd "$ORIGINAL_DIR/$dir"

    # Update the dependency version reference in this app's Chart.yaml.
    # The version line is indented (it's inside the dependencies list), so we
    # match leading whitespace and preserve it during replacement.
    echo -e "${YELLOW}Updating generic dependency version to ${new_version}...${NC}"
    if grep -qE "^[[:space:]]+version: ${current_version}" Chart.yaml; then
        sed -i.bak -E "s/^([[:space:]]+)version: ${current_version}/\1version: ${new_version}/" Chart.yaml && rm -f Chart.yaml.bak
    fi

    # Resolve and download the updated dependency, regenerating Chart.lock
    if helm dependency update; then
        echo -e "${GREEN}✓ Successfully updated dependencies for ${dir}${NC}"
        success_count=$((success_count + 1))
    else
        echo -e "${RED}✗ Failed to update dependencies for ${dir}${NC}"
        error_count=$((error_count + 1))
    fi

    cd "$ORIGINAL_DIR"
done

# ──────────────────────────────────────────────────────────────────────────────
# Summary
# ──────────────────────────────────────────────────────────────────────────────
echo -e "\n${YELLOW}Dependency update complete!${NC}"
echo -e "${GREEN}Successful updates: ${success_count}${NC}"
echo -e "${RED}Failed updates: ${error_count}${NC}"
echo -e "${GREEN}Generic chart version bumped from ${current_version} to ${new_version}${NC}"

exit 0