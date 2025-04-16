#!/usr/bin/env python3
import os
import shutil
import yaml
import json
from pathlib import Path

# Define paths
BASE_DIR = Path("base-chart")
OUTPUT_DIR = Path("charts")
TEMPLATES_DIR = BASE_DIR / "templates"
ASSETS_DIR = BASE_DIR / "assets"

# Clean output directory if it exists
if OUTPUT_DIR.exists():
    shutil.rmtree(OUTPUT_DIR)

# Create output directory
OUTPUT_DIR.mkdir(exist_ok=True)

def create_chart_yaml(app_name, version="0.1.0", description=None):
    """Create a Chart.yaml file for the app"""
    description = description or f"Helm chart for {app_name}"

    chart_data = {
        "apiVersion": "v2",
        "name": app_name,
        "description": description,
        "type": "application",
        "version": version,
        "appVersion": "1.0.0"
    }

    # Add dependency on generic chart for all charts except generic itself
    if app_name != "generic":
        chart_data["dependencies"] = [{
            "name": "generic",
            "version": version,
            "repository": "file://../generic"
        }]

    return chart_data

def process_values_yaml():
    """Extract app-specific values from the main values.yaml file"""
    with open(BASE_DIR / "values.yaml", "r") as f:
        values = yaml.safe_load(f)

    # Get the list of apps and create a set of all app names
    apps = values.get("apps", {})
    app_names = set(apps.keys())

    # Add generic to the list of apps
    app_names.add("generic")

    # Define global fields - those that should be moved under 'global'
    global_fields = [
        "domain", "cloudflare", "letsencrypt", "traefik_forward_auth"
    ]

    # Create a values file for each app
    for app_name in app_names:
        app_dir = OUTPUT_DIR / app_name
        app_dir.mkdir(exist_ok=True)

        # Create Chart.yaml
        chart_yaml = create_chart_yaml(app_name)
        with open(app_dir / "Chart.yaml", "w") as f:
            yaml.dump(chart_yaml, f, default_flow_style=False)

        # Create values.yaml
        app_values = {}
        global_values = {}

        # Separate global values from app-specific values
        for key, value in values.items():
            if key != "apps":
                if key in global_fields:
                    global_values[key] = value

        # Add global section if there are global values
        if global_values:
            app_values["global"] = global_values

        # Add app-specific values directly to values
        if app_name != "generic" and app_name in apps:
            app_specific_values = apps[app_name]

            # If app has configuration, add it directly to values
            if isinstance(app_specific_values, dict):
                for k, v in app_specific_values.items():
                    if k != "enabled":  # Skip the enabled flag
                        app_values[k] = v

        with open(app_dir / "values.yaml", "w") as f:
            yaml.dump(app_values, f, default_flow_style=False)

def copy_templates():
    """Copy app-specific templates to each app chart"""
    # Create generic chart templates first
    generic_dir = OUTPUT_DIR / "generic" / "templates"
    generic_dir.mkdir(exist_ok=True, parents=True)

    # Copy _generic directory contents to generic chart
    generic_source = TEMPLATES_DIR / "_generic"
    for item in generic_source.glob("*"):
        if item.is_file():
            shutil.copy2(item, generic_dir / item.name)

    # Process each app directory in templates
    for template_dir in TEMPLATES_DIR.iterdir():
        if not template_dir.is_dir() or template_dir.name == "_generic":
            continue

        app_name = template_dir.name
        app_templates_dir = OUTPUT_DIR / app_name / "templates"
        app_templates_dir.mkdir(exist_ok=True, parents=True)

        # Copy app-specific templates
        for item in template_dir.glob("*"):
            if item.is_file():
                shutil.copy2(item, app_templates_dir / item.name)

def copy_assets():
    """Copy app-specific assets to each app chart"""
    # Skip the static-page folder as requested

    # Process each app directory in assets
    for asset_dir in ASSETS_DIR.iterdir():
        if not asset_dir.is_dir() or asset_dir.name == "static-page":
            continue

        app_name = asset_dir.name
        app_assets_dir = OUTPUT_DIR / app_name / "assets"

        # Copy the entire directory structure
        if asset_dir.exists():
            if app_assets_dir.exists():
                shutil.rmtree(app_assets_dir)
            shutil.copytree(asset_dir, app_assets_dir)

def fix_asset_references():
    """Fix references to asset paths in YAML files"""
    # Look for template files that might reference assets
    for app_dir in OUTPUT_DIR.glob("*"):
        if not app_dir.is_dir():
            continue

        templates_dir = app_dir / "templates"
        if not templates_dir.exists():
            continue

        # Scan all yaml files in the templates directory
        for yaml_file in templates_dir.glob("**/*.yaml"):
            with open(yaml_file, "r") as f:
                content = f.read()

            # Replace asset paths
            updated_content = content
            # Fix references to assets/[app_name]/... to just assets/...
            updated_content = updated_content.replace(f"assets/{app_dir.name}/", "assets/")

            # Fix references like "assets/prometheus/dashboards/*.json"
            for other_app in OUTPUT_DIR.glob("*"):
                if not other_app.is_dir() or other_app.name == app_dir.name:
                    continue

                # Replace patterns like assets/prometheus/dashboards/ with assets/dashboards/
                if f"assets/{other_app.name}/" in updated_content:
                    updated_content = updated_content.replace(f"assets/{other_app.name}/", "assets/")

            # Write back if changed
            if updated_content != content:
                with open(yaml_file, "w") as f:
                    f.write(updated_content)
                print(f"Updated asset references in {yaml_file}")

def clean_template_files():
    """Remove app conditionals from template files"""
    for app_dir in OUTPUT_DIR.glob("*"):
        if not app_dir.is_dir():
            continue

        app_name = app_dir.name
        templates_dir = app_dir / "templates"
        if not templates_dir.exists():
            continue

        # Process each yaml file
        for yaml_file in templates_dir.glob("**/*.yaml"):
            with open(yaml_file, "r") as f:
                content = f.read()

            # Remove the opening conditional for this app
            # Match patterns like {{- if .Values.apps.appname.enabled -}} with variable whitespace
            updated_content = content

            # Handle various whitespace patterns in the conditional
            patterns = [
                f"{{{{- if .Values.apps.{app_name}.enabled -}}}}",
                f"{{{{- if  .Values.apps.{app_name}.enabled  -}}}}",
                f"{{{{- if .Values.apps.{app_name}.enabled -}}}}\n",
                f"{{{{-if .Values.apps.{app_name}.enabled-}}}}",
                f"{{{{- if .Values.apps.{app_name}.enabled }}}}",
                f"{{{{- if .Values.apps.{app_name}.enabled}}}}"
            ]

            for pattern in patterns:
                if pattern in updated_content:
                    updated_content = updated_content.replace(pattern, "")
                    # Also try to remove ending conditional
                    updated_content = updated_content.rstrip()
                    if updated_content.endswith("{{- end -}}"):
                        updated_content = updated_content[:-10]
                    elif updated_content.endswith("{{- end }}"):
                        updated_content = updated_content[:-10]
                    elif updated_content.endswith("{{-end}}"):
                        updated_content = updated_content[:-8]
                    elif updated_content.endswith("{{end}}"):
                        updated_content = updated_content[:-7]

                    # Cleanup any trailing newlines but ensure there's one at the end
                    updated_content = updated_content.rstrip() + "\n"
                    break

            # Write back if changed
            if updated_content != content:
                with open(yaml_file, "w") as f:
                    f.write(updated_content)
                print(f"Cleaned conditionals in {yaml_file}")

def main():
    # Create chart directories and structure
    process_values_yaml()

    # Copy templates
    copy_templates()

    # Copy assets
    copy_assets()

    # Fix asset references in template files
    fix_asset_references()

    # Clean app conditionals from template files
    clean_template_files()

    print(f"Successfully split base chart into individual charts in {OUTPUT_DIR}")

if __name__ == "__main__":
    main()