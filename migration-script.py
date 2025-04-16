#!/usr/bin/env python3
import os
import shutil
import yaml
import json
from pathlib import Path

# Define paths
BASE_DIR = Path("base-chart")
OUTPUT_DIR = Path("new-world")
TEMPLATES_DIR = BASE_DIR / "templates"
ASSETS_DIR = BASE_DIR / "assets"

# Ensure output directory exists
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

        # Add global values if relevant for this app
        for key, value in values.items():
            if key != "apps":
                app_values[key] = value

        # Add app-specific values
        if app_name != "generic" and app_name in apps:
            app_values["app"] = apps[app_name]

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

def main():
    # Create chart directories and structure
    process_values_yaml()

    # Copy templates
    copy_templates()

    # Copy assets
    copy_assets()

    print(f"Successfully split base chart into individual charts in {OUTPUT_DIR}")

if __name__ == "__main__":
    main()