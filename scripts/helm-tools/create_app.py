#!/usr/bin/env python3

import os
import subprocess
import shutil
import yaml
import re
from pathlib import Path

def find_repo_root(start_dir):
    """Find the repository root by looking for key directories."""
    current_dir = os.path.abspath(start_dir)

    # Look for up to 5 levels up
    for _ in range(5):
        # Check if this looks like the repo root (has apps/ and base-chart/ directories)
        if (os.path.isdir(os.path.join(current_dir, 'apps')) and
                os.path.isdir(os.path.join(current_dir, 'base-chart'))):
            return current_dir

        # Move up one directory
        parent_dir = os.path.dirname(current_dir)
        if parent_dir == current_dir:  # We've reached the root of the filesystem
            break
        current_dir = parent_dir

    # If we couldn't find it, ask the user
    print("Could not automatically detect repository root directory.")
    repo_root = input("Please enter the path to the repository root: ").strip()
    if os.path.isdir(os.path.join(repo_root, 'apps')):
        return os.path.abspath(repo_root)
    else:
        raise ValueError("Invalid repository root directory. The directory should contain an 'apps' subdirectory.")

def create_directory_if_not_exists(directory):
    """Create directory if it doesn't exist."""
    os.makedirs(directory, exist_ok=True)

def get_generic_chart_version(repo_root):
    """Get the version of the generic chart from Chart.yaml."""
    generic_chart_path = os.path.join(repo_root, 'apps', 'generic', 'Chart.yaml')
    try:
        with open(generic_chart_path, 'r') as f:
            chart_data = yaml.safe_load(f)
            return chart_data.get('version', '0.1.8')  # Default to 0.1.8 if not found
    except Exception as e:
        print(f"Warning: Could not get generic chart version: {e}")
        return '0.1.8'  # Default version

def create_chart_yaml(app_name, app_description, output_dir, generic_version):
    """Create Chart.yaml file for the app."""
    chart_content = {
        'apiVersion': 'v2',
        'appVersion': '1.0.0',
        'dependencies': [
            {
                'name': 'generic',
                'repository': 'file://../generic',
                'version': generic_version
            }
        ],
        'description': f'Helm chart for {app_name}',
        'name': app_name,
        'type': 'application',
        'version': '0.1.0'
    }

    with open(os.path.join(output_dir, 'Chart.yaml'), 'w') as f:
        yaml.dump(chart_content, f, sort_keys=False)

def create_values_yaml(app_config, output_dir):
    """Create values.yaml file for the app."""
    with open(os.path.join(output_dir, 'values.yaml'), 'w') as f:
        yaml.dump(app_config, f, sort_keys=False)

def create_templates_directory(output_dir):
    """Create templates directory for the app."""
    templates_dir = os.path.join(output_dir, 'templates')
    create_directory_if_not_exists(templates_dir)
    return templates_dir

def create_configmap_template(app_name, app_config, templates_dir):
    """Create a basic configmap template for the app."""
    configmap_file = os.path.join(templates_dir, f'{app_name}-configmap.yaml')

    # Create basic configmap template
    configmap_content = f"""apiVersion: v1
kind: ConfigMap
metadata:
  name: {app_name}-config
  namespace: {{{{ $.Release.Namespace }}}}
data:
  # Add your app configuration here
  APP_NAME: "{app_name}"
"""

    # Add configurable options based on app type
    if 'ports' in app_config['generic']['deployment']:
        for port_config in app_config['generic']['deployment']['ports']:
            port = port_config['containerPort']
            if port in [80, 8080, 3000, 8000]:
                configmap_content += f"  PORT: \"{port}\"\n"
                break

    # Add URL if ingress is configured
    if 'ingress' in app_config['generic'] and 'https' in app_config['generic']['ingress']:
        subdomain = app_config['generic']['ingress']['https'][0].get('subdomain', app_name)
        configmap_content += f"  BASE_URL: \"https://{subdomain}.{{{{ .Values.global.domain }}}}\"\n"

    with open(configmap_file, 'w') as f:
        f.write(configmap_content)

def update_dependencies(app_dir):
    """Run helm dependency update in the app directory."""
    try:
        subprocess.run(['helm', 'dependency', 'update'], cwd=app_dir, check=True)
        print(f"Successfully updated dependencies in {app_dir}")
    except subprocess.CalledProcessError as e:
        print(f"Error updating dependencies: {e}")

def update_base_chart_values(app_name, app_display_name, app_description, app_group, base_chart_values_path):
    """Add the app entry to the base-chart values.yaml."""
    with open(base_chart_values_path, 'r') as f:
        values = yaml.safe_load(f)

    if 'apps' not in values:
        values['apps'] = {}

    # Add the new app entry
    values['apps'][app_name] = {
        'enabled': False,
        'homer': {
            'enabled': True,
            'group': app_group,
            'logo': f"https://raw.githubusercontent.com/walkxcode/dashboard-icons/main/svg/{app_name}.svg",
            'subtitle': app_description,
            'displayName': app_display_name
        },
        'argocd': {
            'targetRevision': None,
            'namespace': "argocd",
            'project': "default",
            'syncWave': "15"  # Default sync wave
        }
    }

    with open(base_chart_values_path, 'w') as f:
        yaml.dump(values, f, sort_keys=False)

def get_input_with_default(prompt, default=""):
    """Get input from user with a default value if they press enter."""
    user_input = input(prompt).strip()
    return user_input if user_input else default

def prompt_for_app_details():
    """Prompt user for app details."""
    app_name = get_input_with_default("Enter app name (e.g., nextcloud): ")

    # Validate app name
    if not app_name or not re.match(r'^[a-z0-9]([-a-z0-9]*[a-z0-9])?$', app_name):
        print("Invalid app name. App name must consist of lowercase alphanumeric characters or '-'.")
        return prompt_for_app_details()

    app_display_name = get_input_with_default(f"Enter display name [{app_name.title()}]: ", app_name.title())
    app_description = get_input_with_default("Enter app description: ", f"{app_display_name} Application")

    # Gather deployment details
    default_image = f"ghcr.io/{app_name}/{app_name}:latest"
    image = get_input_with_default(f"Enter container image [{default_image}]: ", default_image)

    # Get ports for the app
    default_port = "80"
    port = get_input_with_default(f"Enter main container port [{default_port}]: ", default_port)

    ports = []
    if port and port.isdigit():
        ports.append({"containerPort": int(port)})
    else:
        # Default to port 80 if no valid port provided
        ports.append({"containerPort": 80})
        port = "80"

    # Always set up ingress with middleware
    subdomain = get_input_with_default(f"Enter subdomain [{app_name}]: ", app_name)
    middlewares = ["traefik-forward-auth"]  # Always add this middleware

    # Get additional ports
    while True:
        additional_port = get_input_with_default("Enter additional port (or leave empty to finish): ")
        if not additional_port:
            break
        if additional_port.isdigit():
            ports.append({"containerPort": int(additional_port)})

    # Always add a default PVC
    pvc_mounts = {
        "data": {
            "mountPath": f"/app/data",
            "hostPath": None  # Use ~ in YAML
        }
    }

    # Always add configmap and external secrets
    env_from = {
        "configMapRef": f"{app_name}-config",
        "secretRef": f"{app_name}-secret"
    }

    # Always add an example external secret
    external_secrets = {
        f"{app_name}-secret": [
            {"API_KEY": f"/{app_name}/API_KEY"},
            {"DB_PASSWORD": f"/{app_name}/DB_PASSWORD"}
        ]
    }

    # Select app group for Homer
    groups = [
        "infrastructure",
        "monitoring",
        "productivity",
        "smartHome",
        "media",
        "starrs",
        "downloads",
        "ai"
    ]

    print("\nAvailable groups for Homer:")
    for i, group in enumerate(groups):
        print(f"{i+1}. {group}")

    default_group_index = 2  # productivity (index 2) as default
    group_choice = get_input_with_default(f"Select group number [1-{len(groups)}] [{default_group_index+1}]: ", str(default_group_index+1))
    try:
        group_index = int(group_choice) - 1
        if 0 <= group_index < len(groups):
            app_group = groups[group_index]
        else:
            app_group = groups[default_group_index]  # Default to productivity
    except ValueError:
        app_group = groups[default_group_index]  # Default to productivity

    # Create app configuration
    app_config = {
        'generic': {
            'deployment': {
                'image': image,
                'ports': ports,
                'pvcMounts': pvc_mounts,
                'envFrom': env_from
            },
            'service': {
                'ports': []
            },
            'externalSecrets': external_secrets
        }
    }

    # Add service ports based on container ports
    for p in ports:
        port_num = p["containerPort"]
        port_name = "http" if port_num in [80, 8080, 8000, 8081, 8888, 8088, 3000] else f"port-{port_num}"
        app_config['generic']['service']['ports'].append({
            'name': port_name,
            'port': port_num
        })

    # Always add ingress with middleware
    app_config['generic']['ingress'] = {
        'https': [
            {
                'subdomain': subdomain,
                'port': int(port),
                'middlewares': middlewares
            }
        ]
    }

    return app_name, app_display_name, app_description, app_group, app_config

def main():
    print("=== Helm Chart App Creator ===")

    # Determine the base directory (repository root)
    script_dir = os.path.dirname(os.path.abspath(__file__))
    repo_root = find_repo_root(script_dir)
    print(f"Repository root directory: {repo_root}")

    # Get the generic chart version
    generic_version = get_generic_chart_version(repo_root)
    print(f"Using generic chart version: {generic_version}")

    # Ensure the apps directory exists
    apps_dir = os.path.join(repo_root, 'apps')
    create_directory_if_not_exists(apps_dir)

    # Get app details from user
    app_name, app_display_name, app_description, app_group, app_config = prompt_for_app_details()

    # Create app directory
    app_dir = os.path.join(apps_dir, app_name)
    if os.path.exists(app_dir):
        overwrite = get_input_with_default(f"App directory {app_dir} already exists. Overwrite? (y/n) [n]: ", "n")
        if overwrite.lower() != 'y':
            print("Operation cancelled.")
            return
        shutil.rmtree(app_dir)

    create_directory_if_not_exists(app_dir)

    # Create app files
    create_chart_yaml(app_name, app_description, app_dir, generic_version)
    create_values_yaml(app_config, app_dir)
    templates_dir = create_templates_directory(app_dir)

    # Always create configmap template
    create_configmap_template(app_name, app_config, templates_dir)
    print(f"Created configmap template at {templates_dir}/{app_name}-configmap.yaml")

    # Update dependencies
    update_dependencies(app_dir)

    # Update base-chart values.yaml
    base_chart_values_path = os.path.join(repo_root, 'base-chart', 'values.yaml')
    if os.path.exists(base_chart_values_path):
        update_base_chart_values(app_name, app_display_name, app_description, app_group, base_chart_values_path)
        print(f"Updated {base_chart_values_path} with new app entry")
    else:
        print(f"Warning: Could not find {base_chart_values_path}")

    print(f"\nSuccessfully created app '{app_name}' in {app_dir}")
    print(f"Next steps:")
    print(f"1. Review and customize the generated files")
    print(f"2. Update app configuration in base-chart/values.yaml if needed")
    print(f"3. Customize the configmap template if needed")
    print(f"4. Create any additional template files in {app_dir}/templates")

if __name__ == "__main__":
    main()