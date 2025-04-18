import yaml
import os
import sys
from pathlib import Path
import re

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

        # Write the app-specific values.yaml file (without path modifications)
        output_file = app_dir / 'values.yaml'
        with open(output_file, 'w') as f:
            yaml.dump(app_data, f, default_flow_style=False, sort_keys=False)

        # Parse, modify, and rewrite the file line by line
        process_yaml_file(output_file)

        print(f"Created {output_file}")
        apps_processed += 1

    print(f"\nDone! Processed {apps_processed} apps from values.yaml")

def process_yaml_file(file_path):
    """
    Process a YAML file line by line to handle both hostPath and persistentVolumeClaims values.
    """
    with open(file_path, 'r') as f:
        lines = f.readlines()

    # Track indentation levels and current section
    in_pvc_section = False
    pvc_indent = 0
    modified_lines = []

    for line in lines:
        if not line.strip():  # Keep empty lines as is
            modified_lines.append(line)
            continue

        # Check if we're entering a persistentVolumeClaims section
        pvc_match = re.match(r'(\s*)persistentVolumeClaims:\s*$', line)
        if pvc_match:
            in_pvc_section = True
            pvc_indent = len(pvc_match.group(1))
            modified_lines.append(line)
            continue

        # If we're in a PVC section and this line has more indentation, it's a PVC entry
        if in_pvc_section:
            current_indent = len(re.match(r'(\s*)', line).group(1))

            # If current indentation is less or equal to PVC section's indentation,
            # we've exited the PVC section
            if current_indent <= pvc_indent:
                in_pvc_section = False

            # If we're still in PVC section and this is a value entry, modify it
            elif ':' in line and not line.strip().endswith(':'):
                key_val_match = re.match(r'(\s*)([^:]+): (.+)$', line)
                if key_val_match:
                    indent = key_val_match.group(1)
                    key = key_val_match.group(2)
                    value = key_val_match.group(3).strip()
                    # Replace value with ~ and add comment
                    modified_lines.append(f"{indent}{key}: ~ # {value}\n")
                    continue

        # Handle hostPath entries regardless of section
        hostpath_match = re.match(r'(\s*)hostPath: (.+)$', line)
        if hostpath_match:
            indent = hostpath_match.group(1)
            value = hostpath_match.group(2).strip()
            modified_lines.append(f"{indent}hostPath: ~ # {value}\n")
            continue

        # Keep other lines as is
        modified_lines.append(line)

    # Write the modified content back to the file
    with open(file_path, 'w') as f:
        f.writelines(modified_lines)

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