#!/bin/bash

# Stop at any error
set -e

# Move to script dir
cd "$(dirname "$0")"

# Script to run helm dependency update on each chart directory
# Place this script in the root folder of your helm charts repository

# Set colors for output
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[0;33m'
NC='\033[0m' # No Color

# First, build the generic chart
echo -e "${YELLOW}Building the generic chart first...${NC}"
cd ./generic || { echo -e "${RED}Generic chart directory not found!${NC}"; exit 1; }

if helm package .; then
    echo -e "${GREEN}✓ Successfully built generic chart${NC}"
    # Return to the original directory
    cd - > /dev/null
else
    echo -e "${RED}✗ Failed to build generic chart${NC}"
    cd - > /dev/null
    exit 1
fi

echo -e "\n${YELLOW}Starting Helm dependency update for all charts...${NC}"

# Get all directories that contain a Chart.yaml file
chart_dirs=$(find . -name "Chart.yaml" -exec dirname {} \; | sort)

# Initialize counters
success_count=0
error_count=0

# Process each chart directory
for dir in $chart_dirs; do
    # Skip the generic app
    if [[ "$dir" == "./generic" ]]; then
        echo -e "\n${YELLOW}Skipping dependency update for ${dir}${NC}"
        continue
    fi
    
    echo -e "\n${YELLOW}Processing ${dir}...${NC}"
    
    # Change to the chart directory
    cd "$dir" || continue
    
    # Run helm dependency update
    if helm dependency update; then
        echo -e "${GREEN}✓ Successfully updated dependencies for ${dir}${NC}"
        ((success_count++))
    else
        echo -e "${RED}✗ Failed to update dependencies for ${dir}${NC}"
        ((error_count++))
    fi
    
    # Return to the original directory
    cd - > /dev/null
done

# Print summary
echo -e "\n${YELLOW}Dependency update complete!${NC}"
echo -e "${GREEN}Successful updates: ${success_count}${NC}"
echo -e "${RED}Failed updates: ${error_count}${NC}"

exit 0