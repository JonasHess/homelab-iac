#!/bin/bash

# Stop at any error
set -e

# Set colors for output
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[0;33m'
NC='\033[0m' # No Color

# Store original directory
ORIGINAL_DIR="$(pwd)"

# First, increment the patch version of the generic chart
echo -e "${YELLOW}Incrementing patch version of the generic chart...${NC}"
cd ./generic || { echo -e "${RED}Generic chart directory not found!${NC}"; exit 1; }

# Read current version from Chart.yaml
current_version=$(grep -E '^version:' Chart.yaml | awk '{print $2}')
echo -e "${YELLOW}Current version: ${current_version}${NC}"

# Split version into parts
IFS='.' read -ra version_parts <<< "$current_version"
major=${version_parts[0]}
minor=${version_parts[1]}
patch=${version_parts[2]}

# Increment patch version
new_patch=$((patch + 1))
new_version="${major}.${minor}.${new_patch}"
echo -e "${YELLOW}New version: ${new_version}${NC}"

# Update version in Chart.yaml (compatible with both macOS and Linux)
sed -i.bak "s/^version: ${current_version}/version: ${new_version}/" Chart.yaml && rm -f Chart.yaml.bak
echo -e "${GREEN}✓ Updated generic chart version to ${new_version}${NC}"

# Build the generic chart
echo -e "${YELLOW}Building the generic chart...${NC}"
helm package .
echo -e "${GREEN}✓ Successfully built generic chart${NC}"

# Clean up old generic chart packages
echo -e "${YELLOW}Cleaning up old generic chart packages...${NC}"
old_packages=$(find . -name "generic-*.tgz" -not -name "generic-${new_version}.tgz" | wc -l | tr -d ' ')
if [ "$old_packages" -gt 0 ]; then
    find . -name "generic-*.tgz" -not -name "generic-${new_version}.tgz" -delete
    echo -e "${GREEN}✓ Removed ${old_packages} old generic chart package(s)${NC}"
else
    echo -e "${GREEN}✓ No old generic chart packages to clean up${NC}"
fi

# Return to the original directory
cd "$ORIGINAL_DIR"

echo -e "\n${YELLOW}Starting Helm dependency update for all charts...${NC}"

# Initialize counters
success_count=0
error_count=0

# Get list of chart directories
chart_dirs=$(find . -name "Chart.yaml" -not -path "./generic/*" -exec dirname {} \; | sort)

# Process each chart directory
for dir in $chart_dirs
do
    # Skip the generic app
    if [[ "$dir" == "./generic" ]]; then
        echo -e "\n${YELLOW}Skipping dependency update for ${dir}${NC}"
        continue
    fi

    echo -e "\n${YELLOW}Processing ${dir}...${NC}"

    # Go to chart directory - using absolute path
    cd "$ORIGINAL_DIR/$dir"

    # Update the dependency version in Chart.yaml (compatible with both macOS and Linux)
    echo -e "${YELLOW}Updating generic dependency version to ${new_version}...${NC}"
    if grep -q "^  version: ${current_version}" Chart.yaml; then
        sed -i.bak "s/^  version: ${current_version}/  version: ${new_version}/" Chart.yaml && rm -f Chart.yaml.bak
    fi

    # Run helm dependency update
    if helm dependency update; then
        echo -e "${GREEN}✓ Successfully updated dependencies for ${dir}${NC}"
        success_count=$((success_count + 1))
    else
        echo -e "${RED}✗ Failed to update dependencies for ${dir}${NC}"
        error_count=$((error_count + 1))
    fi

    # Return to original directory - using absolute path
    cd "$ORIGINAL_DIR"
done

# Print summary
echo -e "\n${YELLOW}Dependency update complete!${NC}"
echo -e "${GREEN}Successful updates: ${success_count}${NC}"
echo -e "${RED}Failed updates: ${error_count}${NC}"
echo -e "${GREEN}Generic chart version bumped from ${current_version} to ${new_version}${NC}"

exit 0