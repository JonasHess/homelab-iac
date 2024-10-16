#!/bin/bash
set -e  # Exit on first error
set -o pipefail  # Fail if any command in a pipe fails
set -u  # Treat unset variables as an error
cd "$(dirname "$0")" # Change to the script directory

# Script to test if all apps render correctly using Helm templates

echo "Running Helm template tests..."

# Function to display help message
show_help() {
    echo "Usage: $0 [-a <appname>] [-f]"
    echo
    echo "Options:"
    echo "  -a <appname>  Test a specific app only"
    echo "  -f            Fix failing tests by replacing expected files with actual files"
    echo "  -h            Show this help message"
}

# Function to print error messages in red
print_error() {
    echo -e "\033[31m✗ $1\033[0m"
}

# Function to print success messages in green
print_success() {
    echo -e "\033[32m✔ $1\033[0m"
}

# Parse command-line arguments
fix_tests=false
while getopts "hfa:" opt; do
    case ${opt} in
        h )
            show_help
            exit 0
            ;;
        a )
            specific_app=$OPTARG
            ;;
        f )
            fix_tests=true
            ;;
        \? )
            show_help
            exit 1
            ;;
    esac
done

# Function to render and compare templates
test_app() {
  echo "------------------------------"
    local app=$1
    echo "${app}"

    # Create directories if they don't exist
    mkdir -p ./unittest/expected
    mkdir -p ./unittest/actual

    # Render the template
    helm template values.yaml . --set apps.${app}.enabled=true > ./unittest/actual/${app}.yaml

    # Compare the actual result with the expected result
    if ! diff -u ./unittest/expected/${app}.yaml ./unittest/actual/${app}.yaml > /dev/null; then
        if [ "$fix_tests" = true ]; then
            cp ./unittest/actual/${app}.yaml ./unittest/expected/${app}.yaml
            print_success "Fixed template for app ${app} by replacing the expected result."
            return 0
        else
            print_error "Template for app ${app} does not match the expected result."
#            echo "Differences:"
#            diff -u ./unittest/expected/${app}.yaml ./unittest/actual/${app}.yaml
            return 1
        fi
    else
        print_success "Successful test for app ${app}."
        rm -f "./unittest/actual/${app}.yaml"
        return 0
    fi
}

# Initialize apps variable
apps=""

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
    print_error "No apps found to test."
    exit 1
fi

# List the apps found
echo "Apps found: $apps"

# Initialize counters
error_count=0
success_count=0

# Test each app
echo "Running tests..."
for app in $apps; do
    if test_app $app; then
        success_count=$((success_count + 1))
    else
        error_count=$((error_count + 1))
    fi
done

# Show summary
if [ $error_count -eq 0 ]; then
    print_success "Summary: Successful tests: $success_count, Failed tests: $error_count"
else
    print_error "Summary: Successful tests: $success_count, Failed tests: $error_count"
fi

# Check if no app is enabled
if [ $error_count -eq ${#apps[@]} ]; then
    print_success "No app is enabled. The result is empty as expected."
else
    # Show summary
    if [ $error_count -eq 0 ]; then
        print_success "All apps rendered correctly."
    else
        print_error "$error_count app(s) failed to render correctly."
    fi
fi

echo "Helm template tests completed."
exit $error_count