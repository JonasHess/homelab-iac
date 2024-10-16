#!/bin/bash
set -e  # Exit on first error
set -o pipefail  # Fail if any command in a pipe fails
set -u  # Treat unset variables as an error
cd "$(dirname "$0")" # Change to the script directory

# Script to test if all apps render correctly using Helm templates

echo "Running Helm template tests..."

# Function to display help message
show_help() {
    echo "Usage: $0 [-a <appname>]"
    echo
    echo "Options:"
    echo "  -a <appname>  Test a specific app only"
    echo "  -h            Show this help message"
}

# Parse command-line arguments
while getopts "ha:" opt; do
    case ${opt} in
        h )
            show_help
            exit 0
            ;;
        a )
            specific_app=$OPTARG
            ;;
        \? )
            show_help
            exit 1
            ;;
    esac
done

# Function to render and compare templates
test_app() {
    local app=$1
    echo "Testing app: $app"

    # Create directories if they don't exist
    mkdir -p ./unittest/expected
    mkdir -p ./unittest/actual

    # Render the template
    helm template values.yaml . --set apps.${app}.enabled=true > ./unittest/actual/${app}.yaml

    # Compare the actual result with the expected result
    if ! diff -u ./unittest/expected/${app}.yaml ./unittest/actual/${app}.yaml > /dev/null; then
        echo "Error: Template for app ${app} does not match the expected result."
        echo "Differences:"
        diff -u ./unittest/expected/${app}.yaml ./unittest/actual/${app}.yaml
        return 1
    else
        echo "App ${app} rendered correctly."
        return 0
    fi
}

# Read all apps from values.yaml using yq
echo "Reading apps from values.yaml..."
apps=$(yq e '.apps | keys | .[]' values.yaml | xargs)
echo "Apps variable content: '$apps'"

# If a specific app is provided, test only that app
if [ -n "${specific_app:-}" ]; then
    apps=$specific_app
    echo "Testing specific app: $apps"
fi

# Fail if no apps are found
if [ -z "$apps" ]; then
    echo "Error: No apps found to test."
    exit 1
fi

# List the apps found
echo "Apps found: $apps"

# Initialize error count
error_count=0

# Test each app
echo "Running tests..."
for app in $apps; do
    test_app $app || error_count=$((error_count + 1))
done

# Show summary
if [ $error_count -eq 0 ]; then
    echo "All apps rendered correctly."
else
    echo "$error_count app(s) failed to render correctly."
fi

echo "Helm template tests completed."
exit $error_count