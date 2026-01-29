# Requirements: External Application Configuration Files

## Background

The `smarthome4` application has a large configuration block (`appConfig`) embedded in the environment values file (`homelab-environments/hess.pm/values.yaml`). This configuration spans approximately 2,700 lines and contains smart home automation rules, Home Assistant resource definitions, device mappings, and scene configurations.

The goal is to extract this configuration into a standalone file that is:
- Committed to Git separately from the main values.yaml
- Easier to maintain with clear version history
- Able to trigger ArgoCD sync independently when changed

## Current Architecture

### Repository Structure

```
homelab-iac/                          # Infrastructure as Code repository
├── base-chart/                       # Central chart that creates ArgoCD Applications
│   ├── templates/
│   │   └── application.yaml          # Template that generates ArgoCD Application per app
│   └── values.yaml                   # Default app definitions
├── bootstrap-chart/                  # Initial ArgoCD setup
│   ├── templates/
│   │   └── application.yaml          # Creates ArgoCD Application for base-chart
│   └── values.yaml                   # Environment repo reference (parameterized)
└── apps/
    └── smarthome4/                   # The smarthome4 Helm chart
        ├── templates/
        │   └── smarthome4-app-config.yaml  # ConfigMap template using appConfig
        └── values.yaml

homelab-environments/                 # Environment-specific configuration repository
├── hess.pm/
│   └── values.yaml                   # Environment values (contains large appConfig)
└── home-server.dev/
    └── values.yaml                   # Different environment values
```

### Current Data Flow

```
1. bootstrap-chart is deployed with environment-specific parameters
   ├── Specifies: environment repo URL
   ├── Specifies: environment values path (e.g., "hess.pm/values.yaml")
   └── Creates: ArgoCD Application for base-chart (multi-source)
                ├── Source 1: environments repo (ref: values)
                └── Source 2: base-chart with valueFiles → $values/<path>

2. base-chart renders with merged values
   ├── Iterates over apps.* in values
   ├── Creates: ArgoCD Application per enabled app
   └── Passes: helm values including appConfig to each app

3. smarthome4 app receives appConfig
   ├── Renders: ConfigMap with appConfig content as smarthome-config.yaml
   ├── Mounts: ConfigMap as volume at /config
   └── Application reads: /config/smarthome-config.yaml as YAML
```

### How appConfig is Consumed

The `appConfig` value flows through the system as follows:

1. Defined as a multi-line YAML string in environment values.yaml
2. Passed through ArgoCD → Helm → ConfigMap template
3. Rendered into a ConfigMap as file content (`smarthome-config.yaml`)
4. Volume-mounted into the container at `/config/smarthome-config.yaml`
5. Read by the Spring application as a standard YAML configuration file

The application expects valid YAML content - no Helm templating occurs within the appConfig content itself.

## Requirements

### R1: Standalone Configuration File

The configuration must be extractable to a separate file within the environment directory.

- File location: Same directory as the environment's values.yaml
- Example: `homelab-environments/hess.pm/smarthome4-config.yaml`
- File format: Any format (YAML, JSON, TOML, INI, XML, plain text, etc.)
- The file content is treated as opaque - no parsing or transformation by the deployment pipeline

### R2: Independent ArgoCD Sync

Changes to the external configuration file must trigger ArgoCD synchronization independently.

- Modifying the config file should cause ArgoCD to detect drift
- The smarthome4 application should redeploy with the updated configuration
- Changes to values.yaml should not be required to trigger sync

### R3: Multi-Environment Support

The solution must support multiple environments with different configurations.

- Each environment may have its own smarthome4 configuration
- Environments are directories within an environments repository
- Example: `hess.pm/smarthome4-config.yaml`, `other-env/smarthome4-config.yaml`

### R4: Different Environment Repositories

Environments may exist in completely separate Git repositories.

- Environment A: `https://github.com/user/homelab-environments.git` → `hess.pm/`
- Environment B: `https://github.com/user/other-environments.git` → `prod/`
- The solution must not hardcode repository URLs

### R5: External Configuration Per App Type

The external configuration mechanism applies to specific app types that require large config files.

- If an app is disabled, no configuration file is required
- If an app is enabled and uses external config, the config file must exist
- Apps that do not use external config are unaffected by this mechanism

### R6: Graceful Handling of Missing Files

When an external config file is specified but does not exist, the failure mode must be clear.

- ArgoCD should fail to sync with an understandable error
- The error should indicate which file is missing
- This is acceptable as it represents a configuration error

## Constraints

### C1: Information Gap in base-chart

The `base-chart/templates/application.yaml` template does not have access to:

- The environment repository URL
- The environment repository target revision
- The base path within the environment repository (e.g., "hess.pm")

This information is only known by bootstrap-chart, not passed down to base-chart.

### C2: ArgoCD Application Source Structure

ArgoCD Applications use either:

- `source:` (singular) - current implementation, single repository
- `sources:` (plural) - multi-source, required for referencing files from multiple repositories

Switching between these structures requires conditional logic in the template.

### C3: Helm valueFiles Limitations

Helm's `valueFiles` in ArgoCD can reference files from other sources using `$ref/path` syntax, but:

- Requires the source to be defined with a `ref` name
- The file path must be known at template rendering time
- Cannot dynamically discover files

### C4: No Helm Templating in Config Content

The configuration file content is not processed by Helm templating when consumed by the application.

- The file is mounted as-is into the container
- Helm variables (`.Values.*`) cannot be used within the config content
- The config must be self-contained

### C5: File Format Agnostic

The solution must handle arbitrary file formats, not just YAML.

- Helm `valueFiles` cannot be used (requires valid YAML)
- File content cannot be merged or transformed
- Content must pass through as opaque data to ConfigMap

## Implementation

The chosen approach uses a **standalone Helm chart in the environments repository** that is deployed via a **child ArgoCD Application** created by the smarthome4 chart itself.

### Architecture

```
1. base-chart creates ArgoCD Application for smarthome4
   └── smarthome4 chart renders:
       ├── Deployment (mounts ConfigMap by configurable name)
       ├── ConfigMap (env vars)
       ├── ExternalSecret
       └── ArgoCD Application → points to homelab-environments/hess.pm/smarthome4-config
                                 └── Creates ConfigMap with smarthome-config.yaml

2. Two ArgoCD Applications manage smarthome4:
   ├── argocd-app-smarthome4 (from base-chart) → deploys the app
   └── smarthome4-config (from smarthome4 chart) → deploys the config
```

### Key Design Decisions

- **Separate Helm chart** (`homelab-environments/hess.pm/smarthome4-config/`): Contains `Chart.yaml`, `templates/configmap.yaml`, and `values.yaml` with the full app configuration. This gives the config its own sync lifecycle.
- **Child ArgoCD Application** (`apps/smarthome4/templates/smarthome4-config-application.yaml`): The smarthome4 chart creates an ArgoCD Application that points to the config chart in the environments repo. The repo URL, revision, and path are passed via `externalConfig` values.
- **Configurable ConfigMap name**: The deployment references `.Values.externalConfig.configMapName` instead of a hardcoded name, ensuring the config chart and deployment agree on the ConfigMap name.
- **No changes to base-chart**: The base-chart does not need to know about external config. The smarthome4 chart handles it internally.

### Requirements Satisfaction

| Requirement | How It's Met |
|---|---|
| R1: Standalone config | Config lives in its own Helm chart with dedicated `values.yaml` |
| R2: Independent sync | Separate ArgoCD Application syncs independently on config changes |
| R3: Multi-environment | Each environment directory has its own `smarthome4-config/` chart |
| R4: Different repos | `externalConfig.repoURL` is configurable per environment |
| R5: Per app type | Only apps with `externalConfig` values create child Applications |
| R6: Graceful failure | ArgoCD fails with clear error if chart path doesn't exist |

## Edge Cases

### E1: App Disabled

When `apps.smarthome4.enabled: false`:

- No ArgoCD Application is created for smarthome4
- No external configuration file is required
- No validation of config file existence occurs

### E2: App Enabled With External Config

When smarthome4 is enabled and external config file is specified:

- Multi-source ArgoCD Application must be created
- Config file must exist in the environment repository
- Changes to either chart or config file trigger sync

### E3: Environment Without smarthome4

When an environment does not use smarthome4:

- App can be omitted from values.yaml entirely
- Or app can be explicitly disabled
- No impact on other environments or apps
