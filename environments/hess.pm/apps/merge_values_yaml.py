#!/usr/bin/env python3
import os
import yaml
import glob
import re

# Script to merge all values.yaml files from app directories into one large values.yaml file

def get_app_directories():
    """Get all application directories within the current folder (apps)"""
    return glob.glob("**/values.yaml", recursive=True)

def process_app_name(app_path):
    """Process the app name from the path, removing any leading dots"""
    # Extract app name from path: 'appname/values.yaml' -> 'appname'
    app_name = os.path.dirname(app_path).split('/')[-1]

    # Remove leading dot if present
    if app_name.startswith('.'):
        app_name = app_name[1:]
        is_enabled = False
    else:
        is_enabled = True

    return app_name, is_enabled

def read_yaml_file(file_path):
    """Read a YAML file and return the parsed content"""
    try:
        with open(file_path, 'r') as f:
            content = yaml.safe_load(f)
            return content if content else {}
    except Exception as e:
        print(f"Warning: Error reading {file_path}: {e}")
        return {}

def merge_values():
    """Merge all values.yaml files into one large values.yaml file"""
    result = {"apps": {}}

    # Get all values.yaml files in the apps directory and subdirectories
    app_paths = get_app_directories()

    for app_path in app_paths:
        app_name, is_enabled = process_app_name(app_path)
        content = read_yaml_file(app_path)

        # Skip non-app files or empty files
        if not content:
            # Create an entry with minimal structure for the app
            result["apps"][app_name] = {
                "enabled": is_enabled,
                "argocd": {
                    "targetRevision": "feature/refactor-apps",
                    "helm": {
                        "values": {}
                    }
                }
            }
            continue

        # Check if app key exists and extract the specific app content
        app_content = {}
        if "apps" in content and app_name in content["apps"]:
            app_content = content["apps"][app_name]

        # Create the new structure with the extracted content
        app_entry = {
            "enabled": is_enabled,
            "argocd": {
                "targetRevision": "feature/refactor-apps",
                "helm": {
                    "values": {}
                }
            }
        }

        # Copy any existing content to the new structure
        if app_content and isinstance(app_content, dict):
            # If generic exists, add it to the helm values
            if "generic" in app_content:
                app_entry["argocd"]["helm"]["values"]["generic"] = app_content["generic"]

            # Copy any other keys at the app level
            for key, value in app_content.items():
                if key != "generic" and key != "enabled":
                    app_entry["argocd"]["helm"]["values"][key] = value

        # Add the processed app to the result
        result["apps"][app_name] = app_entry

    return result

def write_merged_yaml(data):
    """Write the merged data to values.yaml"""
    with open("values.yaml", 'w') as f:
        yaml.dump(data, f, sort_keys=False, default_flow_style=False)
    print(f"Successfully created merged values.yaml")

if __name__ == "__main__":
    merged_data = merge_values()
    write_merged_yaml(merged_data)