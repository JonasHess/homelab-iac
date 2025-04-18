import yaml
import os
import sys
from pathlib import Path

def split_values_yaml(source_file, target_dir):
    """
    Split a large values.yaml file into smaller app-specific files.

    Args:
        source_file (str): Path to the source values.yaml file
        target_dir (str): Base directory where app files will be created
    """
    # Load the source YAML file
    with open(source_file, 'r') as f:
        data = yaml.safe_load(f)

    # Check if 'apps' section exists
    if 'apps' not in data:
        print("Error: No 'apps' section found in the values.yaml file")
        sys.exit(1)

    # Process each app
    apps_processed = 0
    target_base = Path(target_dir)

    for app_name, app_config in data['apps'].items():
        if not isinstance(app_config, dict):
            print(f"Warning: Skipping '{app_name}' as it's not a dictionary")
            continue

        # Create a copy of the app configuration
        app_data = app_config.copy()

        # Remove the 'enabled' field if it exists
        if 'enabled' in app_data:
            del app_data['enabled']

        # Create the target directory if it doesn't exist
        app_dir = target_base / app_name
        os.makedirs(app_dir, exist_ok=True)

        # Write the app-specific values.yaml file
        output_file = app_dir / 'values.yaml'
        with open(output_file, 'w') as f:
            yaml.dump(app_data, f, default_flow_style=False, sort_keys=False)

        print(f"Created {output_file}")
        apps_processed += 1

    print(f"\nDone! Processed {apps_processed} apps from values.yaml")

if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage: python split_values.py <source_values.yaml> <target_directory>")
        sys.exit(1)

    source_file = sys.argv[1]
    target_dir = sys.argv[2]

    if not os.path.isfile(source_file):
        print(f"Error: Source file '{source_file}' not found")
        sys.exit(1)

    split_values_yaml(source_file, target_dir)